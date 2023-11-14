from collections import defaultdict
import re
from threading import Lock
import scipy
import time
import operator
import random
import dmxio
import uuid 
import math
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server
import threading 
import mido
import json

# For Custom Fuction Nodes
import colorsys

def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)

MAX_VALUES = {
    "bool": 1,
    "int": 255,
    "float": 255.0,
}

TYPES = ["bool", "int", "float", "array", "any"]

UUID_DATABASE = {}

ID_COUNT = 0

def update_name(name, other_names):
    def toks(obj_name):
        match = re.fullmatch(r"(\D*)(\d*)$", obj_name)
        if match:
            prefix, number = match.groups()
            if not number:
                number = 1
            else:
                number = int(number)
            return prefix, number
        else:
            return None, None

    my_prefix, my_number = toks(name)
    if my_prefix is None:
        return f"{name}-1"

    for other_name in other_names:
        other_prefix, other_number = toks(other_name)
        if other_prefix is None:
            continue
        if my_prefix == other_prefix:
            my_number = max(my_number, other_number)
    return f"{my_prefix}{my_number+1}"

# Outputs cannot be copied.
NOT_COPYABLE = [
    "DmxOutput",
    "DmxOutputGroup",
]

def new_ids(data):
    uuid_pattern = r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
    string_data = json.dumps(data)
    obj_ids = re.findall(rf"(\w+\[{uuid_pattern}\])", string_data)
    if not obj_ids:
        return json.loads(string_data)
    obj_ids = set(obj_ids)
    for old_id in obj_ids:
        match2 = re.match(rf"(\w+)\[{uuid_pattern}\]", old_id)
        if not match2:
            raise Exception("Failed to replace ids")
        class_name = match2.groups()[0]
        if class_name in NOT_COPYABLE:
            continue
        
        new_id = f"{class_name}[{uuid.uuid4()}]"
        string_data = string_data.replace(old_id, new_id)

    return json.loads(string_data)


def clear_database():
    global UUID_DATABASE
    global ID_COUNT
    ID_COUNT = 0
    UUID_DATABASE = {}

class Identifier:
    def __init__(self):
        global UUID_DATABASE
        global ID_COUNT
        self.id = f"{self.__class__.__name__}[{uuid.uuid4()}]"
        ID_COUNT += 1
        UUID_DATABASE[self.id] = self
        self.deleted = False

    def delete(self):
        self.deleted = True

    def serialize(self):
        return {"id": self.id}

    def deserialize(self, data):
        global UUID_DATABASE
        self.id = data["id"]
        UUID_DATABASE[self.id] = self


cast = {
    "bool": int,
    "int": int,
    "float": float,
    "any": lambda x: x
}


class Channel(Identifier):
    def __init__(self, **kwargs):
        super().__init__()
        self.value = kwargs.get("value")
        self.size = kwargs.get("size", 1)

        if self.value is None:
            self.value = 0 if self.size == 1 else [0]*self.size

        self.direction = kwargs.get("direction", "in")
        self.dtype = kwargs.get("dtype", "float")
        self.name = kwargs.get("name", "Channel")

    def get(self):
        return cast[self.dtype](self.value)

    def set(self, value):
        self.value = value

    def serialize(self):
        data = super().serialize()
        data.update({
            "value": self.value,
            "size": self.size,
            "direction": self.direction,
            "dtype": self.dtype,
            "name": self.name,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.direction = data["direction"]
        self.value = data["value"]
        self.dtype = data["dtype"]
        self.name = data["name"]
        self.size = data["size"]


class Parameter(Identifier):
    def __init__(self, name="", value=None, dtype="any"):
        super().__init__()
        self.name = name
        self.value = value
        self.dtype = dtype

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "value": self.value,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        self.value = data["value"]



class Parameterized(Identifier):
    def __init__(self):
        super().__init__()
        self.parameters = []

    def update_parameter(self, index, value):
        if 0 <= index < len(self.parameters):
            return True, None
        else:
            return False, None

    def add_parameter(self, parameter):
        assert self.get_parameter(parameter.name) is None
        n = len(self.parameters)
        self.parameters.append(parameter)
        return n

    def get_parameter(self, parameter_name):
        for parameter in self.parameters:
            if parameter_name == parameter.name:
                return parameter
        return None

    def get_parameter_id(self, parameter_name):
        parameter = self.get_parameter(parameter_name)
        if parameter is not None:
            return parameter.id

    def serialize(self):
        data = super().serialize()
        data.update({"parameters": []})
        for parameter in self.parameters:
            data["parameters"].append(parameter.serialize())
        return data

    def deserialize(self, data):
        super().deserialize(data)
        parameters_data = data["parameters"]
        for i, parameter_data in enumerate(parameters_data):
            self.parameters[i].deserialize(parameter_data)


class ClipInputChannel(Parameterized):
    nice_title = "Input"

    def __init__(self, **kwargs):
        super().__init__()
        self.name = kwargs.get("name", "")
        self.channel = Channel(**kwargs)
        self.ext_channel = Channel(**kwargs)
        self.input_type = kwargs.get("dtype", "float")

        self.mode = "automation"
        self.min_parameter = Parameter("min", 0)
        self.max_parameter = Parameter("max",  MAX_VALUES[self.channel.dtype])
        self.add_parameter(self.min_parameter)
        self.add_parameter(self.max_parameter)


        self.automations = []
        self.active_automation = None
        self.speed = 0
        self.last_beat = 0

    def update(self, clip_beat):
        beat = (clip_beat * (2**self.speed))
        current_beat = beat % self.active_automation.length
        restarted = current_beat < self.last_beat

        if self.mode == "armed":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.channel.set(value)
            if restarted:
                self.mode = "recording"
        elif self.mode == "recording":
            if restarted:
                self.mode = "automation"
            point = (current_beat, self.ext_channel.get())
            self.active_automation.add_point(point, replace_near=True)
            self.channel.set(self.ext_channel.get())
        elif self.mode == "automation":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.channel.set(value)
        else: # manual
            self.channel.set(self.ext_channel.get())

        self.last_beat = current_beat

    def ext_get(self):
        return self.ext_channel.get()

    def ext_set(self, value):
        value = max(self.min_parameter.value, value)
        value = min(self.max_parameter.value, value)
        self.ext_channel.set(value)

    def set(self, value):
        value = max(self.min_parameter.value, value)
        value = min(self.max_parameter.value, value)
        self.channel.set(value)

    def get(self):
        return self.channel.get()

    @property
    def dtype(self):
        return self.channel.dtype 

    @property
    def direction(self):
        return self.channel.direction 

    @property
    def value(self):
        return self.channel.value 

    @property
    def size(self):
        return self.channel.size 

    def set_active_automation(self, automation):
        assert automation in self.automations
        self.active_automation = automation
        return True

    def add_automation(self):
        n = len(self.automations)
        new_automation = ChannelAutomation(
            self.channel.dtype, 
            f"Preset #{n}", 
            min_value=self.get_parameter("min").value, 
            max_value=self.get_parameter("max").value, 
        )
        self.automations.append(new_automation)
        self.set_active_automation(new_automation)
        return new_automation

    def update_parameter(self, index, value):
        if self.parameters[index] in [self.min_parameter, self.max_parameter]:
            self.parameters[index].value = cast[self.channel.dtype](value)
            min_value = self.min_parameter.value
            max_value = self.max_parameter.value
            for automation in self.automations:
                if automation.deleted:
                    continue
                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    automation.update_point(i, (x, clamp(y, min_value, max_value)))
            return True, None
        else:
            return super().update_parameter(index, value)

    def serialize(self):
        data = super().serialize()
        
        data.update({
            "name": self.name,
            "channel": self.channel.serialize(),
            "ext_channel": self.ext_channel.serialize(),
            "input_type": self.input_type,
            "mode": self.mode,
            "active_automation": self.active_automation.id if self.active_automation else None,
            "automations": [automation.serialize() for automation in self.automations if not automation.deleted],
            "speed": self.speed,
        })
        
        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.channel.deserialize(data["channel"])
        self.ext_channel.deserialize(data["ext_channel"])
        self.input_type = data["input_type"]
        self.mode = data["mode"]
        self.speed = data["speed"]

        for automation_data in data["automations"]:
            automation = ChannelAutomation()
            automation.deserialize(automation_data)
            self.automations.append(automation)
        self.set_active_automation(UUID_DATABASE[data["active_automation"]])
        

class DmxOutput(Channel):
    def __init__(self, dmx_address=1, name=""):
        super().__init__(direction="in", dtype="int", name=name or f"DMX CH. {dmx_address}")
        self.dmx_address = dmx_address
        self.history = [0] * 500

    def record(self):
        self.history.pop(0)
        self.history.append(self.value)

    def serialize(self):
        data = super().serialize()
        data.update({
            "dmx_address": self.dmx_address,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.dmx_address = data["dmx_address"]


class DmxOutputGroup(Identifier):

    def __init__(self, channel_names=[], dmx_address=1, name="Group"):
        super().__init__()
        self.name = name
        self.dmx_address = dmx_address
        self.outputs: DmxOutput = []
        self.channel_names = channel_names
        for i, channel_name in enumerate(channel_names):
            output_channel = DmxOutput()
            self.outputs.append(output_channel)
        self.update_starting_address(dmx_address)
        self.update_name(name)

    def record(self):
        for output in self.outputs:
            output.record()

    def update_starting_address(self, address):
        for i, output_channel in enumerate(self.outputs):
            output_channel.dmx_address = i + address

    def update_name(self, name):
        for i, output_channel in enumerate(self.outputs):
            output_channel.name = f"{name}.{self.channel_names[i]}"

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "dmx_address": self.dmx_address,
            "channel_names": self.channel_names,
            "outputs": [],
        })

        for output_channel in self.outputs:
            data["outputs"].append(output_channel.serialize())
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        self.dmx_address = data["dmx_address"]
        self.channel_names = data["channel_names"]
        for i, output_data in enumerate(data["outputs"]):
            self.outputs[i].deserialize(output_data)


class OscInput(ClipInputChannel):
    def __init__(self, **kwargs):
        kwargs.setdefault("name", f"OSC")
        kwargs.setdefault("direction", "out")
        kwargs.setdefault("dtype", "int")
        super().__init__(**kwargs)
        self.endpoint_parameter = Parameter("endpoint", value="/")
        self.add_parameter(self.endpoint_parameter)
        self.input_type = "osc_input_" + self.dtype

    def update_parameter(self, index, value):
        if self.parameters[index] == self.endpoint_parameter:
            if value.startswith("/"):
                self.parameters[index].value = value
                global_osc_server().map_channel(value, self)
            return True, None
        else:
            return super().update_parameter(index, value)


class MidiInput(ClipInputChannel):
    
    def __init__(self, **kwargs):
        kwargs.setdefault("name", "MIDI")
        kwargs.setdefault("direction", "out")
        kwargs.setdefault("dtype", "int")
        super().__init__(**kwargs)
        self.device_parameter = Parameter("device", value="")
        self.id_parameter = Parameter("id", value="/")
        self.add_parameter(self.device_parameter)
        self.add_parameter(self.id_parameter)
        self.input_type = "midi"

    def update_parameter(self, index, value):
        if self.parameters[index] == self.device_parameter:
            if not value:
                return False, None
            self.parameters[index].value = value
            return True, None
        elif self.parameters[index] == self.id_parameter:
            if self.device_parameter.value is None:
                return False, None
            result = value.split("/")
            if not len(result) == 2 and result[0] and result[1]:
                return False, None
            self.parameters[index].value = value
            return True, None
        else:
            return super().update_parameter(index, value)


class ChannelLink(Identifier):
    def __init__(self, src_channel=None, dst_channel=None):
        super().__init__()
        self.src_channel = src_channel
        self.dst_channel = dst_channel

    def update(self):
        self.dst_channel.set(self.src_channel.get())

    def serialize(self):
        data = super().serialize()
        data.update({
            "src_channel": self.src_channel.id,
            "dst_channel": self.dst_channel.id,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.src_channel = UUID_DATABASE[data["src_channel"]]
        self.dst_channel = UUID_DATABASE[data["dst_channel"]]


class FunctionNode(Parameterized):

    def __init__(self, args="", name=""):
        super().__init__()
        self.name = name
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.args = args
        self.type = None

    def transform(self):
        raise NotImplemented

    def outputs(self):
        return self.outputs

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "type": self.type,
            "args": self.args,
            "inputs": [channel.serialize() for channel in self.inputs],
            "outputs": [channel.serialize() for channel in self.outputs],
        })

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.type = data["type"]
        self.args = data["args"]

        # Some nodes create their inputs/outputs dynamically
        # Ensure the size is correct before deerializing.
        self.inputs = [None] * len(data["inputs"])
        self.outputs = [None] * len(data["outputs"])
        for i, input_data in enumerate(data["inputs"]):
            channel = Channel()
            channel.deserialize(input_data)
            self.inputs[i] = channel
        for i, output_data in enumerate(data["outputs"]):
            channel = Channel()
            channel.deserialize(output_data)
            self.outputs[i] = channel


class FunctionCustomNode(FunctionNode):
    nice_title = "Custom"

    def __init__(self, args="", name="Custom"):
        super().__init__(args, name)
        self.name = name
        self.n_in_parameter = Parameter("n_inputs", 0)
        self.n_out_parameter = Parameter("n_outputs", 0)
        self.code_parameter = Parameter("code", "")
        self.add_parameter(self.n_in_parameter)
        self.add_parameter(self.n_out_parameter)
        self.add_parameter(self.code_parameter)
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.type = "custom"
        self._vars = defaultdict(float)

        arg_v = args.split(",", 2)
        if len(arg_v) == 3:
            n_inputs = int(arg_v[0])
            n_outputs = int(arg_v[1])
            code = arg_v[2]
            self.update_parameter(0, n_inputs)
            self.update_parameter(1, n_outputs)
            self.update_parameter(2, code)

    def transform(self):
        code = self.parameters[2].value
        if code is None:
            return

        code = code.replace("[NEWLINE]", "\n")

        i = dict()
        o = dict()
        v = self._vars
        for input_i, input_channel in enumerate(self.inputs):
            i[input_i] = input_channel.get()
        try:
            exec(code)
        except BaseException as e:
            print(F"Error: {e}")
            return

        for output_i, output_channel in enumerate(self.outputs):
            output_channel.set(o.get(output_i, 0))

    def outputs(self):
        return self.outputs

    def update_parameter(self, index, value):
        channels = []
        if self.parameters[index] == self.n_in_parameter:
            n = int(value)
            if n < 0:
                return False, None

            delta = n - len(self.inputs)
            if n < len(self.inputs):
                for i in range(n, len(self.inputs)):
                    channels.append(self.inputs.pop(-1))
            elif n > len(self.inputs):
                for _ in range(delta):
                    i = len(self.inputs)
                    new_channel = Channel(direction="in", dtype="any", name=f"i[{i}]")
                    self.inputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[0].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.n_out_parameter:
            n = int(value)
            if n < 0:
                return False, None
                
            delta = n - len(self.outputs)
            if n < len(self.outputs):
                for i in range(n, len(self.outputs)):
                    channels.append(self.outputs.pop(-1))
            elif n > len(self.outputs):
                for _ in range(delta):
                    i = len(self.outputs)
                    new_channel = Channel(direction="out", dtype="any", name=f"o[{i}]")
                    self.outputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[1].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.code_parameter:
            self.parameters[2].value = value.replace("\n", "[NEWLINE]")
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionBinaryOperator(FunctionNode):
    nice_title = "Binary Operator"

    def __init__(self, args="", name="Operator"):
        super().__init__(args, name)
        self.op_parameter = Parameter("op")
        self.add_parameter(self.op_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"x"), 
            Channel(direction="in", value=0, name=f"y"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"z")
        ]
        self.type = "binary_operator"
        self.f = None

    def transform(self):
        # TODO: Handle division by zero
        if self.f is not None:
            if self.parameters[0].value == "/" and self.inputs[1].get() == 0:
                return
            self.outputs[0].set(self.f(self.inputs[0].get(), self.inputs[1].get()))

    def update_parameter(self, index, value):
        if self.parameters[index] == self.op_parameter and value in ["+", "-", "/", "*"]:
            self.parameters[index].value = value

            # TODO: Add other operators
            self.f = {
                "+": operator.add,
                "-": operator.sub,
                "/": operator.truediv,
                "*": operator.mul,
            }[value]
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionSequencer(FunctionNode):
    nice_title = "Sequencer"

    def __init__(self, args="", name="Sequencer"):
        super().__init__(args, name)
        self.steps_parameter = Parameter("Steps", 4)
        self.step_length_parameter = Parameter("Step Legnth", 1)
        self.add_parameter(self.steps_parameter)
        self.add_parameter(self.step_length_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"beat"), 
            Channel(direction="in", dtype="any", size=4, name=f"seq"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"on")
        ]
        self.type = "sequencer"

    def transform(self):
        beat = self.inputs[0].get()
        seq = self.inputs[1].get()
        steps = self.steps_parameter.value
        step_length = self.step_length_parameter.value * 4

        step_n = int(((beat // step_length) - 1) % steps)

        if step_n <= len(seq):
            self.outputs[0].set(seq[step_n])

    def update_parameter(self, index, value):
        if self.parameters[index] == self.steps_parameter:
            if value.isnumeric():
                self.parameters[index].value = int(value)
            else:
                return False, None
            return True, None
        elif self.parameters[index] == self.step_length_parameter:
            if value.isnumeric():
                value = int(value)
            else:
                if "/" in value:
                    try:
                        numerator, denom = value.split("/")
                        value = float(numerator)/float(denom)
                    except Exception as e:
                        return False, None
                else:
                    return False, None
            self.parameters[index].value = value
            return True, None
        else:
            return super().update_parameter(index, value)

class FunctionScale(FunctionNode):
    nice_title = "Scale"

    def __init__(self, args="", name="Scale"):
        super().__init__(name)
        self.in_min_parameter = Parameter("in.min", 0)
        self.in_max_parameter = Parameter("in.max", 255)
        self.out_min_parameter = Parameter("out.min", 0)
        self.out_max_parameter = Parameter("out.max", 1)
        self.add_parameter(self.in_min_parameter)
        self.add_parameter(self.in_max_parameter)
        self.add_parameter(self.out_min_parameter)
        self.add_parameter(self.out_max_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"x"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"y")
        ]
        self.type = "scale"

    def transform(self):
        in_min = self.in_min_parameter.value
        in_max = self.in_max_parameter.value
        out_min = self.out_min_parameter.value
        out_max = self.out_max_parameter.value
        x = self.inputs[0].get()
        y = (((x - in_min)/(in_max - in_min))*(out_max-out_min)) + out_min
        self.outputs[0].set(y)

    def update_parameter(self, index, value):
        if self.parameters[index] in [self.in_min_parameter, self.in_max_parameter, self.out_min_parameter, self.out_max_parameter]:
            self.parameters[index].value = float(value)
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionDemux(FunctionNode):
    nice_title = "Demux"

    clear_values = {
        "bool": 0,
        "int": 0,
        "float": 0.0,
        "array": [],
    }

    def __init__(self, args="0", name="Demux"):
        super().__init__(args, name)
        self.n = int(args)
        self.parameters = []
        self.inputs = [
            Channel(direction="in", value=0, dtype="int", name=f"sel"),
            Channel(direction="in", dtype="any", name=f"val")
        ]
        for i in range(self.n):
            self.outputs.append(Channel(direction="out", dtype="any", name=f"{i+1}"))

        self.type = "demux"

    def transform(self):
        value = self.inputs[1].get()
        if isinstance(value, list):
            reset_value = [0] * len(value)
        else:
            reset_value = 0

        for output in self.outputs:
            output.set(reset_value)

        selected = int(self.inputs[0].get())
        if selected in range(self.n+1):
            if selected != 0:
                self.outputs[selected-1].set(value)


class FunctionMultiplexer(FunctionNode):
    nice_title = "Multiplexer"

    def __init__(self, args="0", name="Multiplexer"):
        super().__init__(args, name)
        self.n = int(args)
        self.inputs = [
            Channel(direction="in", value=1, dtype="int", name=f"sel")
        ]
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="any", name=f"out"))
        self.type = "multiplexer"

    def transform(self):
        selected = int(self.inputs[0].get())
        if selected in range(1, self.n+1):
            self.outputs[0].set(self.inputs[selected].get())


class FunctionPassthrough(FunctionNode):
    nice_title = "Passthrough"

    def __init__(self, args="", name="Passthrough"):
        super().__init__(args, name)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="any", name=f"out"))
        self.type = "passthrough"

    def transform(self):
        self.outputs[0].set(self.inputs[0].get())


class FunctionTimeSeconds(FunctionNode):
    nice_title = "Seconds"

    def __init__(self, args="", name="Seconds"):
        super().__init__(args, name)
        self.outputs.append(Channel(direction="out", dtype="float", name="s"))
        self.type = "time_s"

    def transform(self):
        global _STATE
        self.outputs[0].set(_STATE.time_since_start_s)


class FunctionTimeBeats(FunctionNode):
    nice_title = "Beats"

    def __init__(self, args="", name="Beats"):
        super().__init__(args, name)
        self.outputs.append(Channel(direction="out", dtype="float", name="beat"))
        self.type = "time_beat"

    def transform(self):
        global _STATE
        self.outputs[0].set(_STATE.time_since_start_beat + 1)




class FunctionChanging(FunctionNode):
    nice_title = "Changing"

    def __init__(self, args="", name="Changing"):
        super().__init__(args, name)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="bool", name=f"out"))
        self.type = "changing"
        self._last_value = None

    def transform(self):
        changing = False
        new_value = self.inputs[0].get()
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            self._last_value = new_value

        self.outputs[0].set(int(changing))


class FunctionToggleOnChange(FunctionNode):
    nice_title = "Toggle On Change"

    def __init__(self, args="", name="ToggleOnChange"):
        super().__init__(args, name)
        self.rising_only_parameter = Parameter("Rising", False, dtype="bool")
        self.add_parameter(self.rising_only_parameter)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="bool", name=f"out"))
        self.type = "toggle_on_change"
        self._last_value = None
        self._toggle_value = 0

    def transform(self):
        changing = False
        rising_only = self.rising_only_parameter.value
        new_value = self.inputs[0].get()
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            if changing and rising_only:
                changing = new_value
            self._last_value = new_value

        if changing:
            self._toggle_value = int(not self._toggle_value)
        self.outputs[0].set(self._toggle_value)
        
    def update_parameter(self, index, value):
        if self.parameters[index] == self.rising_only_parameter:
            self.parameters[index].value = value.lower() == "true"
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionLastChanged(FunctionNode):
    nice_title = "Last Changed"

    def __init__(self, args="0", name="LastChanged"):
        super().__init__(args, name)
        self.n = int(args)
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", value=0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="int", name=f"out{n}"))
        self.type = "last_changed"
        self._last_values = [None]*n
        self._last_changed_index = 0

    def transform(self):
        for i, last_value in enumerate(self._last_values):
            changing = False
            new_value = self.inputs[i].get()
            if isinstance(new_value, (list)):
                changing = tuple(new_value) == last_value
                self._last_values[i] = tuple(new_value)
            else:
                changing = last_value != new_value
                self._last_values[i] = new_value

            if changing:
                self._last_changed_index = i

        self.outputs[0].set(self._last_changed_index)


class FunctionAggregator(FunctionNode):
    nice_title = "Aggregator"

    def __init__(self, args="0", name="Aggregator"):
        super().__init__(args, name)
        self.n = int(args)
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", value=0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="any", size=self.n, name=f"out{self.n}"))
        self.type = "aggregator"

    def transform(self):
        value = [channel.get() for channel in self.inputs]
        self.outputs[0].set(value)


class FunctionSeparator(FunctionNode):
    nice_title = "Separator"

    def __init__(self, args="0", name="Separator"):
        super().__init__(args, name)
        self.n = int(args)
        self.inputs.append(Channel(direction="in", dtype="any", size=self.n, name=f"in{self.n}"))

        for i in range(self.n):
            self.outputs.append(Channel(direction="out", dtype="any", name=f"out{n}"))
        self.type = "separator"

    def transform(self):
        values = self.inputs[0].get()
        for i, value in enumerate(values):
            self.outputs[i].set(value)


class FunctionRandom(FunctionNode):
    nice_title = "Random"

    def __init__(self, args="", name="Random"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, dtype="int", name=f"a"),
            Channel(direction="in", value=1, dtype="int", name=f"b")
        ]
        self.outputs.append(
            Channel(direction="out", value=0, name=f"z")
        )
        self.type = "random"

    def transform(self):
        a = int(self.inputs[0].get())
        b = int(self.inputs[1].get())
        if a > b:
            return
        self.outputs[0].set(random.randint(a, b))


class FunctionSample(FunctionNode):
    nice_title = "Sample"

    def __init__(self, args="", name="Sample"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=1, name=f"rate", dtype="float"),
            Channel(direction="in", value=0, name=f"in", dtype="float")
        ]
        self.outputs.append(
            Channel(direction="out", value=0, name=f"out", dtype="float")
        )
        self.type = "sample"

        self._cycles_held = 0

    def transform(self):
        self._cycles_held += 1 
        s_held = self._cycles_held/60.0

        rate = self.inputs[0].get()
        if rate <= 0:
            return
        if s_held <= rate:
            return
        else:
            self._cycles_held = 0
            self.outputs[0].set(self.inputs[1].get())


class FunctionBuffer(FunctionNode):
    nice_title = "Buffer"

    def __init__(self, args="", name="Buffer"):
        super().__init__(args, name)
        self.n_parameter = Parameter("n", value=60)
        self.add_parameter(self.n_parameter)
        self.inputs = [
            Channel(direction="in", name=f"in", dtype="any")
        ]
        self.outputs.append(
            Channel(direction="out", name=f"out", dtype="any")
        )
        self.type = "buffer"

        self._buffer = []

    def transform(self):
        self._buffer.insert(0, self.inputs[0].get())
        self.outputs[0].set(self._buffer.pop())

    def update_parameter(self, index, value):
        if self.parameters[index] == self.n_parameter:
            try:
                value = int(value)
            except Exception as e:
                print(e)
                return False, None

            container_value = self.inputs[0].get()
            if isinstance(container_value, list):
                reset_value = [0] * len(container_value)
            else:
                reset_value = 0

            self._buffer = [reset_value] * value
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionCanvas1x8(FunctionNode):
    nice_title = "Canvas 1x8"

    def __init__(self, args="", name="Canvas1x8"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, name="start", dtype="int"),
            Channel(direction="in", value=0, name="size", dtype="int"),
            Channel(direction="in", value=0, name="r", dtype="int"),
            Channel(direction="in", value=0, name="g", dtype="int"),
            Channel(direction="in", value=0, name="b", dtype="int"),
            Channel(direction="in", value=0, name="clear", dtype="bool"),
        ]
        self.outputs.extend([
            Channel(direction="out", value=0, name=f"{rgb}{n}", dtype="float")
            for n in range(8)
            for rgb in "rgb"
        ])
        self.type = "canvas1x8"

    def transform(self):
        start = self.inputs[0].get()
        size = self.inputs[1].get()
        r = self.inputs[2].get()
        g = self.inputs[3].get()
        b = self.inputs[4].get()
        clear = self.inputs[5].get()
        color = [r, g, b]        
        if clear:
            for output_channel in self.outputs:
                output_channel.set(0)

        if 0 <= start < 8 and 0 <= start+size <= 8:
            for i in range(start, start+size):
                for j in range(3):
                    self.outputs[(i*3) + (j)].set(color[j])

class FunctionPixelMover1(FunctionNode):
    nice_title = "Pixel Mover"

    def __init__(self, args="", name="PixelMover1"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, name="i1", dtype="int"),
            Channel(direction="in", value=0, name="r1", dtype="int"),
            Channel(direction="in", value=0, name="g1", dtype="int"),
            Channel(direction="in", value=0, name="b1", dtype="int"),
            Channel(direction="in", value=0, name="i2", dtype="int"),
            Channel(direction="in", value=0, name="r2", dtype="int"),
            Channel(direction="in", value=0, name="g2", dtype="int"),
            Channel(direction="in", value=0, name="b2", dtype="int"),
        ]
        self.outputs.extend([
            Channel(direction="out", value=0, name=f"{rgb}{n}", dtype="float")
            for n in range(8)
            for rgb in "rgb"
        ])
        self.type = "pixelmover1"

        self._canvas = [0] * 24

    def transform(self):
        i1 = self.inputs[0].get()
        r1 = self.inputs[1].get()
        g1 = self.inputs[2].get()
        b1 = self.inputs[3].get()
        i2 = self.inputs[4].get()
        r2 = self.inputs[5].get()
        g2 = self.inputs[6].get()
        b2 = self.inputs[7].get()

        for output_channel in self.outputs:
            output_channel.set(0)
        canvas = [0] * 24
        
        if 0 <= i1 <= 7:
            canvas[i1*3 + 0] += r1 
            canvas[i1*3 + 1] += g1 
            canvas[i1*3 + 2] += b1
        if 0 <= i2 <= 7:
            canvas[i2*3 + 0] += r2 
            canvas[i2*3 + 1] += g2 
            canvas[i2*3 + 2] += b2

        for i, value in enumerate(canvas):
            self.outputs[i].set(value)


FUNCTION_TYPES = {
    "custom": FunctionCustomNode,
    "binary_operator": FunctionBinaryOperator,
    "scale": FunctionScale,
    "sequencer": FunctionSequencer,
    "demux": FunctionDemux,
    "multiplexer": FunctionMultiplexer,
    "passthrough": FunctionPassthrough,
    "time_s": FunctionTimeSeconds,
    "time_beat": FunctionTimeBeats,
    "changing": FunctionChanging,
    "toggle_on_change": FunctionToggleOnChange,
    "last_changed": FunctionLastChanged,
    "aggregator": FunctionAggregator,
    "separator": FunctionSeparator,
    "random": FunctionRandom,
    "sample": FunctionSample,
    "buffer": FunctionBuffer,
    "canvas1x8": FunctionCanvas1x8,
    "pixelmover1": FunctionPixelMover1,
}


class NodeCollection:
    """Collection of nodes and their the a set of inputs and outputs"""

    def __init__(self):
        self.nodes: Node = []  # Needs to be a tree
        self.links = []


    def add_node(self, cls, arg):
        n = len(self.nodes)
        node = cls(arg)

        self.nodes.append(node)
        return node

    def add_link(self, src_channel, dst_channel):
        assert src_channel.direction == "out"
        assert dst_channel.direction == "in"
        for link in self.links:
            if link.deleted:
                continue
            if link.dst_channel == dst_channel:
                return False
        link = ChannelLink(src_channel, dst_channel)
        self.links.append(link)
        return True

    def del_link(self, src_channel, dst_channel):
        found = False
        for i, link in enumerate(self.links):
            if link.deleted:
                continue
            if src_channel == link.src_channel and dst_channel == link.dst_channel:
                found = True
                break
        
        if found:
            self.links[i].deleted = True
        
        return found

    def link_exists(self, src_channel, dst_channel):
        for link in self.links:
            if link.deleted:
                continue
            if src_channel == link.src_channel and dst_channel == link.dst_channel:
                return True
        return False

    def update(self):
        for link in self.links:
            if link.deleted:
                continue
            link.update()

        for node in self.nodes:
            if node.deleted:
                continue
            try:
                node.transform()
            except Exception as e:
                print(f"{e} in {node.name}")

    def serialize(self):
        data = {
            "nodes": [node.serialize() for node in self.nodes if not node.deleted],
            "links": [link.serialize() for link in self.links if not link.deleted],
        }
        return data

    def deserialize(self, data):
        for node_data in data["nodes"]:
            cls = FUNCTION_TYPES[node_data["type"]]
            node = cls(args=node_data["args"], name=node_data["name"])
            node.deserialize(node_data)
            self.nodes.append(node)

        for link_data in data["links"]:
            link = ChannelLink()
            link.deserialize(link_data)
            self.links.append(link)
        

class ChannelAutomation(Identifier):
    default_interpolation_type = {
        "bool": "previous",
        "int": "linear",
        "float": "linear",
    }
    TIME_RESOLUTION = 1/60.0
    def __init__(self, dtype="int", name="", min_value=0, max_value=1):

        super().__init__()
        self.dtype = dtype
        self.name = name
        self.length = 4 # beats
        self.values_x = [0, self.length]
        self.values_y = [min_value, max_value]
        self.f = scipy.interpolate.interp1d(self.values_x, self.values_y)
        self.interpolation = self.default_interpolation_type[self.dtype]

    def value(self, beat_time):
        if self.f is None:
            v = 0
        else:
            v = self.f(beat_time % self.length)
        if self.dtype == "bool":
            return int(v > 0.5)
        elif self.dtype == "int":
            return int(v)
        else:
            return float(v)

    def n_points(self):
        return len(self.values_x)

    def add_point(self, p1, replace_near=False):
        if replace_near:
            max_x_index = self.max_x_index()
            for i, x in enumerate(self.values_x):
                if x is None:
                    continue
                if abs(x - p1[0]) >= 0.01:
                    continue

                if i in [0, max_x_index]:
                    self.values_y[i] = p1[1]
                else:
                    self.values_x[i] = None
                    self.values_y[i] = None
                    self.values_x.append(p1[0])
                    self.values_y.append(p1[1])
                break                       
            else:
                self.values_x.append(p1[0])
                self.values_y.append(p1[1])
        else:
            self.values_x.append(p1[0])
            self.values_y.append(p1[1])
        
        self.reinterpolate()

    def remove_point(self, index, force=False):
        if index in [0, self.max_x_index()] and not force:
            return False
        else:
            self.values_x[index] = None
            self.values_y[index] = None
            self.reinterpolate()
            return True

    def max_x_index(self):
        return self.values_x.index(max(self.values_x, key=lambda x: x or 0))

    def update_point(self, index, p1):
        self.values_x[index] = p1[0]
        self.values_y[index] = p1[1]
        self.reinterpolate()

    def set_interpolation(self, kind):
        self.interpolation = kind
        self.reinterpolate()

    def reinterpolate(self):
        values_x = [x for x in self.values_x if x is not None]
        values_y = [y for y in self.values_y if y is not None]
        self.f = scipy.interpolate.interp1d(
            values_x, 
            values_y, 
            kind=self.interpolation, 
            assume_sorted=False
        )

    def set_length(self, new_length):
        new_length = float(new_length)
        self.length = new_length
        if new_length > self.length:
            self.add_point((new_length, self.values_y[self.max_x_index()]))
        else:
            for i, x in enumerate(self.values_x):
                if x is None:
                    continue
                if x > new_length:
                    self.remove_point(i, force=True)
            self.add_point((new_length, self.values_y[self.max_x_index()]))

    def serialize(self):
        data = super().serialize()
        data.update({
            "length": self.length,
            "values_x": self.values_x,
            "values_y": self.values_y,
            "dtype": self.dtype,
            "name": self.name,
            "interpolation": self.interpolation,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.dtype = data["dtype"]
        self.values_x = data["values_x"]
        self.values_y = data["values_y"]
        self.name = data["name"]
        self.set_interpolation(data["interpolation"])
        # The will re-interpolate for us.
        self.set_length(data["length"])


class Clip(Identifier):
    def __init__(self, outputs=[]):
        super().__init__()
        self.name = ""

        self.inputs = []
        self.outputs = outputs
        self.node_collection = NodeCollection()

        # Speed to play clip
        self.speed = 0

        self.time = 0

        self.playing = False

    def create_input(self, input_type):
        n = len(self.inputs)
        if input_type.startswith("osc_input"):
            input_type = input_type.replace("osc_input_", "")
            new_inp = OscInput(dtype=input_type)
        elif input_type == "midi":
            new_inp = MidiInput()
        else:
            new_inp = ClipInputChannel(direction="out", dtype=input_type, name=f"In.{n}")
        self.inputs.append(new_inp)
        return new_inp

    def update(self, beat):
        if self.playing:
            self.time = (beat * (2**self.speed))
            for channel in self.inputs:
                if channel.deleted:
                    continue
                channel.update(self.time)

            self.node_collection.update()

    def start(self):
        self.time = 0
        self.playing = True

    def stop(self):
        self.playing = False

    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.start()

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "speed": self.speed,
            "inputs": [channel.serialize() for channel in self.inputs if not channel.deleted],
            "outputs": [channel.serialize() for channel in self.outputs if not channel.deleted],
            "node_collection": self.node_collection.serialize(),
        })

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.speed = data["speed"]
        self.outputs = [UUID_DATABASE[output_data["id"]] for output_data in data["outputs"]]

        for input_data in data["inputs"]:
            if input_data["input_type"] in cast.keys():
                channel = ClipInputChannel()
                channel.deserialize(input_data)
            elif input_data["input_type"].startswith("osc_input_"):
                channel = OscInput()
                channel.deserialize(input_data)
            elif input_data["input_type"] == "midi":
                channel = MidiInput()
                channel.deserialize(input_data)
            self.inputs.append(channel)

        self.node_collection.deserialize(data["node_collection"])


class Track(Identifier):
    def __init__(self, name="", n_clips=20):
        super().__init__()
        self.name = name
        self.clips = [None] * n_clips
        self.outputs = []

    def update(self, beat):
        for clip in self.clips:
            if clip is not None:
                clip.update(beat)

        for output in self.outputs:
            if output.deleted:
                continue
            output.record()

    def create_output(self, address):
        new_output = DmxOutput(address)
        self.outputs.append(new_output)
        for clip in self.clips:
            if clip is not None:
                clip.outputs = self.outputs
        return new_output

    def create_output_group(self, address, channel_names):
        new_output_group = DmxOutputGroup(channel_names, address)
        self.outputs.append(new_output_group)
        for clip in self.clips:
            if clip is not None:
                clip.outputs = self.outputs
        return new_output_group

    def __delitem__(self, key):
        clips[key] = None

    def __getitem__(self, key):
        return self.clips[key]

    def __setitem__(self, key, value):
        self.clips[key] = value

    def __len__(self):
        return len(self.clips)

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "clips": [clip.serialize() if clip else None for clip in self.clips],
            "outputs": [],
        })

        for output in self.outputs:
            if output.deleted:
                continue
            output_type = "single" if isinstance(output, DmxOutput) else "group"
            data["outputs"].append((output_type, output.serialize()))

        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        n_clips = len(data["clips"])
        self.clips = [None] * n_clips

        for output_type, output_data in data["outputs"]:
            if output_type == "single":
                output = DmxOutput()
            elif output_type == "group":
                output = DmxOutputGroup(output_data["channel_names"])
            output.deserialize(output_data)
            self.outputs.append(output)


        for i, clip_data in enumerate(data["clips"]):
            if clip_data is None:
                continue
            new_clip = Clip()
            new_clip.deserialize(clip_data)
            self.clips[i] = new_clip


class IO:
    type = None
    def __init__(self, arg_string):
        self.arg_string = arg_string

    def update(self, outputs):
        raise NotImplemented


class EthernetDmxOutput(IO):
    nice_title = "Ethernet DMX"
    arg_template = "host:port"
    type = "ethernet_dmx"

    def __init__(self, host_port):
        super().__init__(host_port)
        self.host, self.port = host_port.split(":")
        self.port = int(self.port)
        self.dmx_connection = dmxio.DmxConnection((self.host, self.port))
        self.dmx_frame = [1] * 512

    def update(self, outputs):
        for output_channel in outputs:
            if output_channel.deleted:
                continue

            channels = []
            if isinstance(output_channel, DmxOutputGroup):
                channels.extend(output_channel.outputs)
            else:
                channels = [output_channel]

            for channel in channels:
                self.dmx_frame[channel.dmx_address-1] = min(255, max(0, int(round(channel.get()))))

        try:
            self.dmx_connection.set_channels(1, self.dmx_frame)
            self.dmx_connection.render()
        except Exception as e:
            raise e

    def __str__(self):
        return f"NodeDmxClient({self.host}:{self.port})"


class OscServerInput(IO):
    nice_title = "OSC Server"
    arg_template = "port"
    type = "osc_server"

    def __init__(self):
        super().__init__(arg_string="")
        self.host = "127.0.0.1"
        self.dispatcher = Dispatcher()

    def start(self, port):
        self.server = osc_server.ThreadingOSCUDPServer((self.host, port), self.dispatcher)

        def start_osc_listening_server():
            print("OSCServer started on {}".format(self.server.server_address))
            self.server.serve_forever()
            print("OSC Server Stopped")

        self.arg_string = str(port)
        self.thread = threading.Thread(target=start_osc_listening_server)
        self.thread.daemon = True
        self.thread.start()

    def map_channel(self, endpoint, input_channel):
        def func(endpoint, value):
            input_channel.ext_set(value)
        self.dispatcher.map(endpoint, func)

    def umap(self, endpoint):
        self.dispatcher.umap(endpoint, lambda endpoint, *args: print(f"Unmapped {endpoint} {args}"))

    def update(self, outputs):
        pass

    def __str__(self):
        return f"OscServer"


class MidiInputDevice(IO):
    nice_title = "MIDI (Input)"
    arg_template = "name"
    type = "midi_input"

    def __init__(self, device_name):
        super().__init__(arg_string=device_name)
        self.device_name = device_name
        assert device_name in mido.get_input_names()
        self.port = mido.open_input(device_name, callback=self.callback)
        self.channel_map = defaultdict(lambda: defaultdict(list))

    def map_channel(self, midi_channel, note_control, channel):
        global_unmap_midi(channel)
        self.channel_map[midi_channel][note_control].append(channel)

    def unmap_channel(self, channel):
        for midi_channel, note_controls in self.channel_map.items():
            for note_control, channels in note_controls.items():
                for other_channel in channels:
                    if channel == other_channel:
                        self.channel_map[midi_channel][note_control].remove(channel)
                        break

    def callback(self, message):
        global LAST_MIDI_MESSAGE
        LAST_MIDI_MESSAGE = (self.device_name, message)
        midi_channel = message.channel
        if message.is_cc():
            note_control = message.control
            value = message.value
        else:
            note_control = message.note
            value = 255 if message.velocity > 1 else 0
        if midi_channel in self.channel_map and note_control in self.channel_map[midi_channel]:
            for channel in self.channel_map[midi_channel][note_control]:
                channel.ext_set(value)


class MidiOutputDevice(IO):
    nice_title = "MIDI (Output)"
    arg_template = "name"
    type = "midi_output"

    def __init__(self, device_name):
        super().__init__(arg_string=device_name)
        self.device_name = device_name
        assert device_name in mido.get_output_names()
        self.port = mido.open_output(device_name)
        self.channel_map = {}
        self.port.reset()

    def update(self, _):
        for (midi_channel, note_control), channel in self.channel_map.items():
            value = channel.get()
            value = clamp(int(value), 0, 127)
            self.port.send(mido.Message("note_on", channel=midi_channel, note=note_control, velocity=value))

    def map_channel(self, midi_channel, note_control, channel):
        self.channel_map[(midi_channel, note_control)] = channel


IO_OUTPUTS = [None] * 5
IO_INPUTS = [OscServerInput(), None, None, None, None] 
N_TRACKS = 6

OSC_SERVER_INDEX = 0
def global_osc_server():
    if OSC_SERVER_INDEX is not None:
        return IO_INPUTS[OSC_SERVER_INDEX]

LAST_MIDI_MESSAGE = None
MIDI_INPUT_DEVICES = {}
MIDI_OUTPUT_DEVICES = {}
def global_midi_control(device_name, in_out):
    if in_out == "in":
        print(MIDI_INPUT_DEVICES, device_name, in_out)
        return MIDI_INPUT_DEVICES.get(device_name)
    else:
        return MIDI_OUTPUT_DEVICES.get(device_name)        

def global_unmap_midi(obj):
    for midi_device in MIDI_INPUT_DEVICES.values():
        midi_device.unmap_channel(obj)


class ProgramState(Identifier):
    _attrs_to_dump = [
        "project_name",
        "project_filepath",
        "tempo",
    ]
    
    def __init__(self):
        global _STATE
        _STATE = self
        super().__init__()
        self.mode = "edit"
        self.project_name = "Untitled"
        self.project_filepath = None
        self.tracks = []

        for i in range(N_TRACKS):
            self.tracks.append(Track(f"Track {i}"))

        self.playing = False
        self.tempo = 120.0
        self.play_time_start_s = 0
        self.time_since_start_beat = 0
        self.time_since_start_s = 0
        self.all_track_outputs = []
        self.command_count = 0

    def toggle_play(self):
        if self.playing:
            self.playing = False
        else:
            self.playing = True
            self.play_time_start_s = time.time()

    def update(self):
        global IO_OUTPUTS
        global IO_INPUTS
        global OSC_SERVER_INDEX

        # Update timing
        if self.playing:
            self.time_since_start_s = time.time() - self.play_time_start_s
            self.time_since_start_beat = self.time_since_start_s * (1.0/60.0) * self.tempo

            # Update values
            for track in self.tracks:
                track.update(self.time_since_start_beat)

            # Update DMX outputs
            for io_output in IO_OUTPUTS:
                if io_output is not None:
                    io_output.update(self.all_track_outputs)

    def serialize(self):
        data = {
            "tempo": self.tempo,
            "project_name": self.project_name,
            "project_filepath": self.project_filepath,
            "tracks": []
        }

        for track in self.tracks:
            data["tracks"].append(track.serialize())

        return data

    def deserialize(self, data):
        self.tempo = data["tempo"]
        self.project_name = data["project_name"]
        self.project_filepath = data["project_filepath"]

        for i, track_data in enumerate(data["tracks"]):
            new_track = Track()
            new_track.deserialize(track_data)
            self.tracks[i] = new_track

    def duplicate_obj(self, obj):
        data = obj.serialize()
        new_data = new_ids(data)
        new_obj = obj.__class__()
        new_obj.deserialize(new_data)
        return new_obj

    def execute(self, full_command):
        global IO_OUTPUTS
        global IO_INPUTS
        global OSC_SERVER_INDEX
        global MIDI_INPUT_DEVICES
        global MIDI_OUTPUT_DEVICES
        print(full_command)

        toks = full_command.split()
        cmd = toks[0]
        
        if self.mode == "performance":
            if cmd == "toggle_clip":
                track_id = toks[1]
                clip_id = toks[2]
                track = self.get_obj(track_id)
                clip = self.get_obj(clip_id)
                for other_clip in track.clips:
                    if other_clip is None or clip == other_clip:
                        continue
                    other_clip.stop()
                clip.toggle()
                if clip.playing:
                    self.playing = True
                return True

        self.command_count += 1
        if cmd == "new_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            track[clip_i] = Clip(track.outputs)
            return True, track[clip_i]

        elif cmd == "create_input":
            clip_id = toks[1]
            input_type = toks[2]
            clip = self.get_obj(clip_id)
            new_input_channel = clip.create_input(input_type)
            return True, new_input_channel

        elif cmd == "create_output":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            new_output_channel = track.create_output(address)
            self.all_track_outputs.append(new_output_channel)
            return True, new_output_channel

        elif cmd == "create_output_group":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            channel_names = full_command.split(" ", 3)[-1].split(',')
            new_output_group = track.create_output_group(address, channel_names)
            self.all_track_outputs.append(new_output_group)
            return True, new_output_group

        elif cmd == "create_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]
            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return clip.node_collection.add_link(src_channel, dst_channel)

        elif cmd == "delete_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]

            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return clip.node_collection.del_link(src_channel, dst_channel)

        elif cmd == "create_node":
            # resplit
            toks = full_command.split(" ", 3)
            clip_id = toks[1]
            type_id = toks[2]
            args = toks[3] or None

            clip = self.get_obj(clip_id)

            if type_id == "none":
                node = clip.node_collection.add_node(None, None)
            else:
                node = clip.node_collection.add_node(FUNCTION_TYPES[type_id], args)
            return True, node

        elif cmd == "delete":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            if obj.deleted:
                return False
            obj.deleted = True
            return True

        elif cmd == "set_active_automation":
            input_id = toks[1]
            automation_id = toks[2]
            input_channel = self.get_obj(input_id)
            automation = self.get_obj(automation_id)
            input_channel.set_active_automation(automation)
            return True 

        elif cmd == "add_automation":
            input_id = toks[1]
            input_channel = self.get_obj(input_id)
            return True, input_channel.add_automation()

        elif cmd == "add_automation_point":
            automation_id = toks[1]
            point = toks[2]
            automation = self.get_obj(automation_id)
            automation.add_point([float(x) for x in point.split(",")])
            return True

        elif cmd == "update_automation_point":
            automation_id = toks[1]
            point_index = toks[2]
            point = toks[3]
            automation = self.get_obj(automation_id)
            automation.update_point(
                int(point_index), [float(x) for x in point.split(",")]
            )
            return True

        elif cmd == "update_parameter":
            # resplit
            toks = full_command.split(" ", 3)
            obj_id = toks[1]
            param_i = toks[2]
            if len(toks) <= 3:
                return
            value = toks[3]
            node = self.get_obj(obj_id)
            result = node.update_parameter(int(param_i), value)
            return result

        elif cmd == "update_channel_value":
            input_id = toks[1]
            value = " ".join(toks[2:])
            try:
                value = eval(value)
            except:
                print(f"Failed to evaluate {value}")
                return False
            input_channel = self.get_obj(input_id)
            value = cast[input_channel.dtype](value)
            input_channel.set(value)
            return True

        elif cmd == "remove_automation_point":
            src = toks[1]
            point_index = toks[2]
            input_channel = self.get_obj(src)
            automation = input_channel.active_automation
            return automation.remove_point(int(point_index))

        elif cmd == "create_io":
            index = int(toks[1])
            input_output = toks[2]
            io_type = toks[3]
            args = toks[4::]
            args = " ".join(args)
            IO_LIST = IO_OUTPUTS if input_output == "outputs" else IO_INPUTS
            MIDI_LIST = MIDI_OUTPUT_DEVICES if input_output == "outputs" else MIDI_INPUT_DEVICES
            try:
                if io_type == "ethernet_dmx":
                    IO_LIST[index] = EthernetDmxOutput(args)
                    return True, IO_LIST[index]
                elif io_type == "osc_server":
                    # TODO: Only allow one
                    IO_LIST[index].start(int(args))
                    return True, IO_LIST[index]
                elif io_type == "midi_input":
                    IO_LIST[index] = MidiInputDevice(args)
                    MIDI_LIST[args] = IO_LIST[index]
                    self._map_all_midi_inputs()
                    self._map_all_midi_inputs()
                    return True, IO_LIST[index]
                elif io_type == "midi_output":
                    IO_LIST[index] = MidiOutputDevice(args)
                    MIDI_LIST[args] = IO_LIST[index]
                    return True, IO_LIST[index]
            except Exception as e:
                print(e)
                return False, None

        elif cmd == "duplicate_clip":
            new_track_i = int(toks[1])
            new_clip_i = int(toks[2])
            clip_id = toks[3]

            new_track = self.tracks[int(new_track_i)]
            new_track_ptr = f"*track[{new_track_i}]"
            old_clip = self.get_obj(clip_id)
            new_clip = self.duplicate_obj(old_clip)
            new_track[new_clip_i] = new_clip
            return True, new_clip

        elif cmd == "duplicate_node":
            clip_id = toks[1]
            obj_id = toks[2]
            clip = self.get_obj(clip_id)
            obj = self.get_obj(obj_id)
            new_obj = self.duplicate_obj(obj)
            collection = clip.node_collection.nodes if isinstance(new_obj, FunctionNode) else clip.inputs
            collection.append(new_obj)
            new_obj.name = update_name(new_obj.name, [obj.name for obj in collection])
            return True, new_obj

        elif cmd == "double_automation":
            automation_id = toks[1]
            automation = self.get_obj(automation_id)
            old_length = automation.length
            automation.set_length(old_length * 2)
            for i in range(automation.n_points()):
                x = automation.values_x[i]
                y = automation.values_y[i]
                if x is not None:
                    automation.add_point((x+old_length, y))
            return True

        elif cmd == "midi_map":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            dp = obj.get_parameter("device")
            device_name = obj.get_parameter("device").value
            id_ = obj.get_parameter("id").value
            midi_channel, note_control = id_.split("/")
            global_midi_control(device_name, "in").map_channel(int(midi_channel), int(note_control), obj)
            return True
        elif cmd == "unmap_midi":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            global_unmap_midi(obj)
            return True

        print("Previous command failed")

    def _map_all_midi_inputs(self):
        for track in self.tracks:
            for clip in track.clips:
                if clip is None:
                    continue
                for input_channel in clip.inputs:
                    if input_channel.deleted:
                        continue
                    if isinstance(input_channel, MidiInput):
                        if not input_channel.get_parameter("device").value or input_channel.get_parameter("id").value == "/":
                            continue
                        self.execute(f"midi_map {input_channel.id}")

    def get_obj(self, id_):
        if id_.startswith("*"):
            return self.get_obj_ptr(id_[1::])
        else:
            try:
                return UUID_DATABASE[id_]
            except Exception as e:
                print(UUID_DATABASE)
                raise

    def get_obj_ptr(self, item_key):
        if item_key.startswith("state"):
            return self

        # Track
        match = re.fullmatch(r"track\[(\d+)\]", item_key)
        if match:
            ti = match.groups()[0]
            return self.tracks[int(ti)]

        # Clip
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]", item_key)
        if match:
            ti, ci = match.groups()
            return self.tracks[int(ti)].clips[int(ci)]

        # Output
        match = re.fullmatch(r"track\[(\d+)\]\.out\[(\d+)\]", item_key)
        if match:
            ti, oi = match.groups()
            return self.tracks[int(ti)].outputs[int(oi)]

        # Clip Input
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.in\[(\d+)\]", item_key)
        if match:
            ti, ci, ii = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].inputs[int(ii)]

        # Clip Output
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.out\[(\d+)\]", item_key)
        if match:
            ti, ci, oi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].outputs[int(oi)]

        # Node Input
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\].in\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, ii = match.groups()
            print(self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].__class__.__name__)
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].inputs[int(ii)]

        # Node Output
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\].out\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, oi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].outputs[int(oi)]

        # Automation
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.in\[(\d+)\]\.automation\[(\d+)\]", item_key)
        if match:
            ti, ci, ii, ai = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].inputs[int(ii)].automations[int(ai)]

        # Node
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\]", item_key)
        if match:
            ti, ci, ni = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)]

        # Parameter
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\]\.parameter\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, pi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].parameters[int(pi)]

        raise Exception(f"Failed to find {item_key}")

    def get_ptr_from_clip(self, clip):
        for track_i, track in enumerate(self.tracks):
            for clip_i, other_clip in enumerate(track.clips):
                if clip == other_clip:
                    return f"*track[{track_i}].clip[{clip_i}]"

    def get_clip_from_ptr(self, clip_key):
        """*track[i].clip[j].+ -> Clip"""
        match = re.match(r"track\[(\d+)\]\.clip\[(\d+)\]", clip_key[1::])
        if match:
            track_i = int(match.groups()[0])
            clip_i = int(match.groups()[1])
            return self.tracks[track_i][clip_i]
        raise RuntimeError(f"Failed to find clip for {clip_key}")


IO_TYPES = {
    "ethernet_dmx": EthernetDmxOutput,
    "osc_server": OscServerInput,
    "midi_input": MidiInputDevice,
    "midi_output": MidiOutputDevice,
}

ALL_INPUT_TYPES = [
    OscServerInput,
    MidiInputDevice,
]
ALL_OUTPUT_TYPES = [
    EthernetDmxOutput,
    MidiOutputDevice,
]

_STATE = None