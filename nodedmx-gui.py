import dearpygui.dearpygui as dpg

import model
import fixtures
import gui_elements

import re
from copy import copy
import math
import time
import pickle
from threading import RLock
import numpy as np
import os
import mido
from collections import defaultdict
import json
from cProfile import Profile
from pstats import SortKey, Stats
import argparse
import subprocess
import sys
import logging


logger = logging.getLogger(__name__)

TOP_LEFT = (0, 18)
SCREEN_WIDTH = 1940
SCREEN_HEIGHT = 1150
PROJECT_EXTENSION = "ndmx"
NODE_EXTENSION = "ndmxc"
AXIS_MARGIN = 0.025
HUMAN_DELAY = 0.125

def norm_distance(p1, p2, x_limit, y_limit):
    np1 = p1[0]/x_limit[1], p1[1]/y_limit[1],
    np2 = p2[0]/x_limit[1], p2[1]/y_limit[1],
    return math.sqrt((np2[0] - np1[0]) ** 2 + (np2[1] - np1[1]) ** 2)

def inside(p1, rect):
    x = rect[0] <= p1[0] <= rect[1]
    y = rect[2] <= p1[1] <= rect[3]
    return x and y

def valid(*objs):
    return all([
        obj is not None and not getattr(obj, "deleted", False)
        for obj in objs
    ])


def get_node_editor_tag(clip):
    return f"{clip.id}.gui.node_window.node_editor"

def get_output_configuration_window_tag(track):
    return f"{track.id}.gui.output_configuration_window"

def get_io_matrix_window_tag(clip):
    return f"{clip.id}.gui.io_matrix_window"

def get_automation_window_tag(input_channel, is_id=False):
    return f"{input_channel if is_id else input_channel.id}.gui.automation_window"

def get_properties_window_tag(obj):
    return f"{obj.id}.gui.properties_window"

def get_plot_tag(input_channel):
    return f"{input_channel.id}.plot"

def get_node_tag(clip, obj):
    return f"{get_node_editor_tag(clip)}.{obj.id}.node"

def get_node_window_tag(clip):
    return f"{clip.id}.gui.node_window"

def get_node_attribute_tag(clip, channel):
    return f"{clip.id}.{channel.id}.node_attribute"

def get_output_node_value_tag(clip, output_channel):
    return f"{clip.id}.{output_channel.id}.output.value"

class Gui:

    def __init__(self):
        self.state = model.ProgramState()
        self.gui_state = {
            "lock": RLock(),
            "node_positions": {},
            "io_types": {
                "inputs": {0: model.IO_INPUTS[0].__class__},
                "outputs": {},
            },
            "io_args": {
                "inputs": {},
                "outputs": {},
            },
            "track_last_active_clip": {},
            "point_tags": [],

            "active_track": None,
            "active_clip": None,
            "active_clip_slot": None,
            "tags": {
                "hide_on_clip_selection": [],
                "node_window": [],
            },
            "copy_buffer": [],
        }

        self.last_command_count = None

        self.mouse_x, self.mouse_y = 0, 0
        self.mouse_drag_x, self.mouse_drag_y = 0, 0
        self.mouse_click_x, self.mouse_click_y = 0, 0
        self.mouse_clickr_x, self.mouse_clickr_y = 0, 0
        self.node_editor_window_is_focused = False

        self._active_output_channel = None
        self._active_input_channel = None
        self._inspecter_x = list(range(500))

        self._properties_buffer = defaultdict(dict)

        self._last_add_function_node = None
        self._custom_node_to_save = None

        self._tap_tempo_buffer = [0, 0, 0, 0, 0, 0]
        self._quantize_amount = None

        self.ctrl = False
        self.shift = False

    
    def run(self):
        self.initialize()
        self.main_loop()

    def main_loop(self):
        logging.debug("Starting main loop")
        try:
            while dpg.is_dearpygui_running():
                with self.gui_state["lock"]:
                    self.update_state_from_gui()
                self.state.update()
                with self.gui_state["lock"]:
                    self.update_gui_from_state()
                dpg.render_dearpygui_frame()
            
            dpg.destroy_context()
        except:
            print("\n\n\n")
            print(model.UUID_DATABASE)
            print([dpg.get_item_alias(item) for item in dpg.get_all_items()])
            raise

    def initialize(self):
        logging.debug("Initializing")
        dpg.create_context()

        self.gui_state["active_track"] = self.state.tracks[0]
        self.last_command_count = self.state.command_count

        #### Create Clip Window ####
        self.clip_window = gui_elements.ClipWindow(self.state, self.gui_state)

        #### Mouse/Key Handlers ####
        logging.debug("Installing mouse/key handlers")
        with dpg.handler_registry():
            dpg.add_mouse_move_handler(callback=self.mouse_move_callback)
            dpg.add_mouse_click_handler(callback=self.mouse_click_callback)
            dpg.add_mouse_double_click_handler(callback=self.mouse_double_click_callback)
            dpg.add_key_press_handler(callback=self.key_press_callback)
            dpg.add_key_down_handler(callback=self.key_down_callback)
            dpg.add_key_release_handler(callback=self.key_release_callback)

        # Create Viewport
        logging.debug("Creating Viewport")
        dpg.create_viewport(title=f"NodeDMX [{self.state.project_name}] *", width=SCREEN_WIDTH, height=SCREEN_HEIGHT, x_pos=50, y_pos=0)

        # File Dialogs
        def save_callback(sender, app_data):
            self.state.project_filepath = app_data["file_path_name"]
            if not self.state.project_filepath.endswith(f".{PROJECT_EXTENSION}"):
                self.state.project_filepath += f".{PROJECT_EXTENSION}"
            self.save()

        def restore_callback(sender, app_data):
            self.open_project(app_data["file_path_name"])

        def save_custom_node(sender, app_data):
            if self._custom_node_to_save is None:
                return

            file_path_name = app_data["file_path_name"]
            if not file_path_name.endswith(f".{NODE_EXTENSION}"):
                file_path_name += f".{NODE_EXTENSION}"

            with open(file_path_name, "w") as f:
                f.write(f"n_inputs:{self._custom_node_to_save.parameters[0].value}\n")
                f.write(f"n_outputs:{self._custom_node_to_save.parameters[1].value}\n")
                f.write(self._custom_node_to_save.parameters[2].value.replace("[NEWLINE]", "\n"))

        def load_custom_node(sender, app_data):
            file_path_name = app_data["file_path_name"]
            n_inputs = None
            n_outputs = None
            code = ""
            with open(file_path_name, "r") as f:
                for line in f:
                    if line.startswith("n_inputs"):
                        n_inputs = line.split(":")[-1]
                    elif line.startswith("n_outputs"):
                        n_outputs = line.split(":")[-1]
                    else:
                        code += line

            if any(thing is None for thing in [n_inputs, n_outputs, code]):
                self.log("Failed to parse custom node")
                return

            self.add_custom_function_node(None, None, ("create", ("custom", f"{n_inputs},{n_outputs},{code}", self.gui_state["active_clip"]), False))

        def load_custom_fixture(sender, app_data):
            file_path_name = app_data["file_path_name"]
            loaded_fixtures = fixtures.parse_fixture(file_path_name)
            for fixture in loaded_fixtures:
                self.add_fixture(None, None, (self.gui_state["active_track"], fixture))

        with dpg.window(tag="rename_node.popup", label="Rename Node", no_title_bar=True, modal=True, show=False, height=10, pos=(SCREEN_WIDTH/2, SCREEN_HEIGHT/2)):
            def set_name_property(sender, app_data, user_data):
                node_editor_tag = get_node_editor_tag(self.gui_state["active_clip"])
                items = dpg.get_selected_nodes(node_editor_tag)
                if items and app_data:
                    item = items[0]
                    alias = dpg.get_item_alias(item)
                    node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                    obj = self.state.get_obj(node_id)
                    obj.name = app_data
                    dpg.configure_item(get_node_tag(self.gui_state["active_clip"], obj), label=obj.name)
                    dpg.set_value(f"{obj.id}.name", obj.name)
                dpg.configure_item("rename_node.popup", show=False)

            dpg.add_input_text(tag="rename_node.text", on_enter=True, callback=set_name_property)

        with dpg.viewport_menu_bar():
            dpg.add_file_dialog(
                directory_selector=True, 
                show=False, 
                callback=save_callback, 
                tag="save_file_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True,
                default_filename=self.state.project_name,
            )

            dpg.add_file_dialog(
                directory_selector=False, 
                show=False, 
                callback=restore_callback, 
                tag="open_file_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True
            )

            dpg.add_file_dialog(
                directory_selector=True, 
                show=False, 
                callback=save_custom_node, 
                tag="save_custom_node_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True,
            )

            dpg.add_file_dialog(
                directory_selector=False, 
                show=False, 
                callback=load_custom_node, 
                tag="open_custom_node_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True
            )

            dpg.add_file_dialog(
                directory_selector=False, 
                show=False, 
                callback=load_custom_fixture, 
                tag="open_fixture_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True
            )

            for tag in ["open_file_dialog", "save_file_dialog"]:
                dpg.add_file_extension(f".{PROJECT_EXTENSION}", color=[255, 255, 0, 255], parent=tag)

            for tag in ["open_custom_node_dialog", "save_custom_node_dialog"]:
                dpg.add_file_extension(f".{NODE_EXTENSION}", color=[0, 255, 255, 255], parent=tag)

            dpg.add_file_extension(f".fixture", color=[0, 255, 255, 255], parent="open_fixture_dialog")

            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open", callback=self.open_menu_callback)
                dpg.add_menu_item(label="Save", callback=self.save_menu_callback)
                dpg.add_menu_item(label="Save As", callback=self.save_as_menu_callback)

            with dpg.menu(label="View"):
                def show_io_window():
                    dpg.configure_item("io.gui.window", show=True); 
                    dpg.focus_item("io.gui.window")                    
                dpg.add_menu_item(label="I/O", callback=show_io_window)

                def show_inspector():
                    dpg.configure_item("inspector.gui.window", show=False)
                    dpg.configure_item("inspector.gui.window", show=True)
                    dpg.focus_item("inspector.gui.window")
                dpg.add_menu_item(label="Inspector", callback=show_inspector)

            # Transport 
            transport_start_x = 800
            dpg.add_button(label="Reset", callback=self.reset_time, pos=(transport_start_x-100, 0))

            transport_start_x = 800
            dpg.add_button(label="Tap Tempo", callback=self.tap_tempo, pos=(transport_start_x, 0))

            def update_tempo(sender, app_data):
                self.state.tempo = app_data
            #dpg.add_text("Tempo:", pos=(transport_start_x + 90,0))
            dpg.add_input_float(label="Tempo", default_value=self.state.tempo, pos=(transport_start_x + 75, 0), on_enter=True, callback=update_tempo, width=45, tag="tempo", step=0)

            def toggle_play(sender):
                self.state.toggle_play()
            dpg.add_button(label="[Play]", callback=toggle_play, pos=(transport_start_x + 165, 0), tag="play_button")

            def mode_change():
                self.state.mode = "edit" if self.state.mode == "performance" else "performance"
                dpg.configure_item("mode_button", label="Edit Mode" if self.state.mode == "edit" else "Performance Mode")
                dpg.set_item_pos("mode_button", (transport_start_x+1000+50, 0) if self.state.mode == "edit" else (transport_start_x+1000, 0))
            dpg.add_button(label="Edit Mode", callback=mode_change, pos=(transport_start_x+1000+50, 0), tag="mode_button")

            # Global Variables
            with dpg.value_registry():
                dpg.add_string_value(default_value="", tag="io_matrix.source_filter_text")
                dpg.add_string_value(default_value="", tag="io_matrix.destination_filter_text")
                dpg.add_string_value(default_value="", tag="last_midi_message")

            # Themes
            with dpg.theme(tag="bg_line.theme"):
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, (255, 255, 255, 30), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1, category=dpg.mvThemeCat_Plots)

            with dpg.theme(tag="automation_line.theme"):
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvPlotCol_Line, (0, 200, 255), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 3, category=dpg.mvThemeCat_Plots)

        ################
        #### Restore ###
        ################

        logging.debug("Restoring program state.")
        # Need to create this after the node_editor_windows
        for track in self.state.tracks:                           
            self.create_track_output_configuration_window(track)

        self.create_inspector_window()
        self.create_io_window()

        logging.debug("Restoring GUI state.")
        self.restore_gui_state()

        dpg.setup_dearpygui()
        dpg.show_viewport()

    def open_menu_callback(self):
        dpg.configure_item("open_file_dialog", show=True)

    def save_menu_callback(self):
        if self.state.project_filepath is None:
            dpg.configure_item("save_file_dialog", show=True)
        self.save()

    def save_as_menu_callback(self):
        dpg.configure_item("save_file_dialog", show=True)

    def reset_time(self):
        self.state.play_time_start = time.time() - HUMAN_DELAY
        self.state.beats_since_start = time.time()

    def tap_tempo(self):
        self._tap_tempo_buffer.insert(0, time.time())
        self._tap_tempo_buffer.pop()
        dts = []
        for i in range(len(self._tap_tempo_buffer)-1):
            dt = abs(self._tap_tempo_buffer[i] - self._tap_tempo_buffer[i+1])
            if dt < 2:
                dts.append(dt)
        t = sum(dts)/len(dts)
        if t == 0:
            return
        self.state.tempo = round(60.0/t, 2)
        dpg.set_value("tempo", self.state.tempo)

    def toggle_node_editor_fullscreen(self):
        if self.state.mode != "edit":
            return
        
        if not valid(self.gui_state["active_clip"]):
            return

        window_tag = get_node_window_tag(self.gui_state["active_clip"])
        cur_pos = tuple(dpg.get_item_pos(window_tag))
        if cur_pos == TOP_LEFT:
            dpg.configure_item(window_tag, pos=self._old_node_editor_pos)
            dpg.configure_item(window_tag, height=self._old_node_editor_height)
            dpg.configure_item(window_tag, width=self._old_node_editor_width)
        else:
            self._old_node_editor_pos = dpg.get_item_pos(window_tag)
            self._old_node_editor_height = dpg.get_item_height(window_tag)
            self._old_node_editor_width = dpg.get_item_width(window_tag)
            dpg.configure_item(window_tag, pos=TOP_LEFT)
            dpg.configure_item(window_tag, height=SCREEN_HEIGHT)
            dpg.configure_item(window_tag, width=SCREEN_WIDTH)
    
    def update_input_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.ext_set(app_data)

    def update_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.set(app_data)

    def update_channel_attr(self, sender, app_data, user_data):
        channel, attr = user_data
        setattr(channel, attr, app_data)

    def update_parameter_buffer_callback(self, sender, app_data, user_data):
        parameter, parameter_index = user_data
        if app_data is not None:
            self._properties_buffer["parameters"][parameter] = (parameter_index, app_data)

    def update_attr_buffer_callback(self, sender, app_data, user_data):
        attr_name, tag = user_data
        if app_data:
            self._properties_buffer["attrs"][attr_name] = (app_data, tag)

    def save_properties_callback(self, sender, app_data, user_data):
        obj = user_data[0]
        # Parameters
        for parameter, (parameter_index, value) in self._properties_buffer.get("parameters", {}).items():
            if isinstance(obj, model.FunctionCustomNode) and parameter_index in [0, 1]:
                clip = user_data[1]
                self.update_custom_node_attributes(None, value, (clip, obj, parameter_index))
            else:
                self.update_parameter(None, value, (obj, parameter_index))
            
        # Attributes
        for attribute_name, (value, tag) in self._properties_buffer.get("attrs", {}).items():
            setattr(obj, attribute_name, value)
            dpg.configure_item(tag, label=value)

        dpg.configure_item(get_properties_window_tag(obj), show=False)

    def create_properties_window(self, clip, obj):
        window_tag = get_properties_window_tag(obj)
        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH/3,SCREEN_HEIGHT/3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    dpg.add_text(default_value="Type")
                    dpg.add_text(default_value=obj.nice_title)

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(default_value=obj.name, callback=self.update_attr_buffer_callback, user_data=("name", get_node_tag(clip, obj)), tag=f"{obj.id}.name")

                if isinstance(obj, model.Parameterized):
                    for parameter_index, parameter in enumerate(obj.parameters):
                        with dpg.table_row():
                            dpg.add_text(default_value=parameter.name)
                            if parameter.dtype == "bool":
                                dpg.add_checkbox(
                                    source=f"{parameter.id}.value",
                                    callback=self.update_parameter_buffer_callback, 
                                    user_data=(parameter, parameter_index),
                                    default_value=parameter.value,
                                )
                            else:                                
                                dpg.add_input_text(
                                    source=f"{parameter.id}.value",
                                    callback=self.update_parameter_buffer_callback, 
                                    user_data=(parameter, parameter_index),
                                    default_value=parameter.value if parameter.value is not None else "",
                                )

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=self.save_properties_callback, user_data=(obj,))
                        
                        def cancel_properties():
                            for parameter in obj.parameters:
                                dpg.set_value(f"{parameter.id}.value", parameter.value)
                            dpg.configure_item(window_tag, show=False)
                            dpg.set_value(f"{obj.id}.name", obj.name)
                        dpg.add_button(label="Cancel", callback=cancel_properties, user_data=obj)

    def create_custom_node_properties_window(self, clip, node):
        window_tag = get_properties_window_tag(node)
        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH/3,SCREEN_HEIGHT/3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(default_value=node.name, callback=self.update_attr_buffer_callback, user_data=("name", get_node_tag(clip, node)), tag=f"{node.id}.name")

                # Inputs
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[0].name)
                    dpg.add_input_text(
                        source=f"{node.parameters[0].id}.value",
                        callback=self.update_parameter_buffer_callback, 
                        user_data=(node.parameters[0], 0), 
                        default_value=node.parameters[0].value if node.parameters[0].value is not None else "",
                        decimal=True,
                    )

                # Outputs
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[1].name)
                    dpg.add_input_text(
                        source=f"{node.parameters[1].id}.value",
                        callback=self.update_parameter_buffer_callback, 
                        user_data=(node.parameters[1], 1), 
                        default_value=node.parameters[1].value if node.parameters[1].value is not None else "",
                        decimal=True,
                    )

                # Code                        
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[2].name)
                    with dpg.group():
                        default_value = node.parameters[2].value.replace("[NEWLINE]", "\n") if node.parameters[2].value is not None else ""
                        dpg.add_input_text(
                            tag=f"{node.parameters[2].id}.value",
                            callback=self.update_parameter_buffer_callback, 
                            user_data=(node.parameters[2], 2), 
                            default_value=default_value,
                            multiline=True,
                            tab_input=True,
                            width=300,
                            height=400
                        )

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=self.save_properties_callback, user_data=(node, clip))
                        
                        def cancel_properties(sender, app_data, user_data):
                            node = user_data
                            for parameter in node.parameters:
                                dpg.set_value(f"{parameter.id}.value", parameter.value)
                            dpg.configure_item(window_tag, show=False)
                            dpg.set_value(f"{node.id}.name", node.name)
                        dpg.add_button(label="Cancel", callback=cancel_properties, user_data=node)

    def add_node_popup_menu(self, node_tag, clip, obj):
        def show_properties_window(sender, app_data, user_data):
            self._properties_buffer.clear()
            dpg.configure_item(get_properties_window_tag(user_data), show=True)

        def save_custom_node(sender, app_data, user_data):
            self._custom_node_to_save = user_data
            dpg.configure_item("save_custom_node_dialog", show=True)

        def create_and_show_connect_to_window(sender, app_data, user_data):
            clip, src = user_data
            try:
                dpg.delete_item("connect_to_window")
            except:
                pass

            def connect_node_and_hide_window(sender, app_data, user_data):
                self.connect_nodes(*user_data)
                dpg.configure_item("connect_to_window", show=False)

            def toggle_node_connection(sender, app_data, user_data):
                clip, src, dst_channel = user_data
                if isinstance(src, model.FunctionNode):
                    if app_data: 
                        for channel in src.outputs:
                            if any(link.src_channel == channel and valid(link) for link in clip.node_collection.links):
                                continue
                            src_channel = channel
                            break
                        else:
                            return
                    else:
                        for channel in src.outputs:
                            if ((link.src_channel == channel and link.dst_channel == dst_channel) and valid(link) 
                                for link in clip.node_collection.links):
                                src_channel = channel
                                break
                        else:
                            return
                else:
                    src_channel = src

                if app_data:
                    self.add_link_callback(None, None, ("create", clip, src_channel, dst_channel))
                else:
                    link_key = f"{get_node_attribute_tag(clip, src_channel)}:{get_node_attribute_tag(clip, dst_channel)}.gui.link"
                    self.delete_link_callback(None, link_key, (None, clip))

            with dpg.window(tag="connect_to_window", no_title_bar=True, max_size=(200, 400), pos=(self.mouse_x, self.mouse_y)):
                    with dpg.menu(label="Search", tag="connect_to_window_search_menu"):
                        def join(str1, str2):
                            return f"{str1}.{str2}"

                        def get_all_dsts(search_terms=""):
                            def matching(name, toks):
                                return all(tok.lower() in name.lower() for tok in toks) or not toks
                            dsts = []
                            for channel in self.get_all_valid_dst_channels(self.gui_state["active_clip"]):
                                if matching(join("Output", channel.name), search_terms.split()):
                                    dsts.append(("Output", channel))
                            return dsts

                        def update_list(sender, app_data):
                            i = 0
                            while True:
                                tag = f"connect_to_window_search_menu.list.{i}"
                                if dpg.does_item_exist(tag):
                                    dpg.delete_item(tag)
                                else:
                                    break
                                i += 1
                            for i, (name, dst_channel) in enumerate(get_all_dsts(app_data)):
                                dpg.add_selectable(
                                    label=join(name, dst_channel.name), 
                                    parent="connect_to_window_search_menu", 
                                    tag=f"connect_to_window_search_menu.list.{i}",
                                    callback=toggle_node_connection,
                                    user_data=(clip, src, dst_channel),
                                    default_value=clip.node_collection.link_exists(src, dst_channel),
                                    disable_popup_close=True,
                                )

                        dpg.add_input_text(tag="connect_to_window_search_text", callback=update_list)

                    clip_output_channels = self.get_all_valid_track_output_channels(self.gui_state["active_clip"])
                    with dpg.menu(label="Clip Outputs"):
                        with dpg.menu(label="All (Starting at)"):
                            for i, output_channel in enumerate(clip_output_channels):
                                dpg.add_menu_item(label=output_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, clip_output_channels[i::]))

                        for i, output_channel in enumerate(clip_output_channels):
                            if valid(output_channel):
                                dpg.add_menu_item(label=output_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, [output_channel]))

                    for dst_node in clip.node_collection.nodes:
                        if valid(dst_node) and dst_node != src:
                            with dpg.menu(label=dst_node.name):
                                with dpg.menu(label="All (Starting at)"):
                                    for i, dst_channel in enumerate(dst_node.inputs):
                                        dpg.add_menu_item(label=dst_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, dst_node.inputs[i::]))
    
                                for dst_channel in dst_node.inputs:
                                    dpg.add_menu_item(label=dst_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, [dst_channel]))



        with dpg.popup(parent=node_tag, tag=f"{node_tag}.popup", mousebutton=1):
            dpg.add_menu_item(label="Properties", callback=show_properties_window, user_data=obj)
            if isinstance(obj, (model.FunctionNode, model.ClipInputChannel)):
                dpg.add_menu_item(label="Connect To ...", callback=create_and_show_connect_to_window, user_data=(clip, obj))
            if isinstance(obj, model.FunctionCustomNode):
                dpg.add_menu_item(label="Save", callback=save_custom_node, user_data=obj)
            if isinstance(obj, model.MidiInput):
                dpg.add_menu_item(label="Update Map MIDI", callback=self.update_midi_map_node, user_data=obj)
                dpg.add_menu_item(label="Learn Map MIDI", callback=self.learn_midi_map_node, user_data=(obj, "input"))

                def unmap_midi(sender, app_data, user_data):
                    obj = user_data
                    success = self.state.execute_wrapper(f"unmap_midi {obj.id}")
                    if success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                        dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                dpg.add_menu_item(label="Unmap MIDI", callback=unmap_midi, user_data=obj)
            dpg.add_menu_item(label="Map MIDI Output", callback=self.learn_midi_map_node, user_data=(obj, "output"))
            if isinstance(obj, (model.FunctionNode, model.ClipInputChannel)):
                dpg.add_menu_item(label="Delete", callback=self.delete_selected_nodes_callback)

    def update_midi_map_node(self, sender, app_data, user_data):
        self.state.execute_wrapper(f"midi_map {user_data.id}")

    def learn_midi_map_node(self, sender, app_data, user_data):
        obj, inout = user_data
        try:
            dpg.delete_item("midi_map_window")
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item("midi_map_window")

        def save(sender, app_data, user_data):
            obj = user_data
            if model.LAST_MIDI_MESSAGE is not None:
                device_name, message = model.LAST_MIDI_MESSAGE
                note_control = message.control if message.is_cc() else message.note
                if inout == "input":
                    self.update_parameter_by_name(obj, "device", device_name)
                    self.update_parameter_by_name(obj, "id", f"{message.channel}/{note_control}")
                    success = self.state.execute_wrapper(f"midi_map {obj.id}")
                    if success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                        dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                        dpg.delete_item("midi_map_window")
                else: #output
                    input_midi_device_name = device_name
                    while input_midi_device_name:
                        for i, output_device in model.MIDI_OUTPUT_DEVICES.items():
                            if output_device.device_name.startswith(input_midi_device_name):
                                output_device.map_channel(message.channel, note_control, obj.outputs[0])
                                print(f"Mapping {(message.channel, note_control)} to {output_device.device_name}")
                                dpg.delete_item("midi_map_window")
                                return
                        input_midi_device_name = input_midi_device_name[:-2]
                    logging.warning(f"Failed to find corresponding output MIDI device for {input_midi_device_name}")

        dpg.set_value("last_midi_message", "")

        with dpg.window(tag="midi_map_window", modal=True, width=300, height=300):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            dpg.add_button(label="Save", callback=save, user_data=obj)

    def update_parameter(self, sender, app_data, user_data):
        if app_data is not None:
            obj, parameter_index = user_data
            success, _ = self.state.execute_wrapper(f"update_parameter {obj.id} {parameter_index} {app_data}")
            if not success:
                raise RuntimeError("Failed to update parameter")
            dpg.set_value(f"{obj.parameters[parameter_index].id}.value", obj.parameters[parameter_index].value)
            return success

    def update_parameter_by_name(self, obj, parameter_name, value):
        obj.get_parameter(parameter_name).value = value

    def connect_nodes(self, clip, src, dst_channels):
        src_channels = []
        if isinstance(src, model.Channel):
            src_channels.append(src)
        if isinstance(src, model.FunctionNode):
            src_channels.extend(src.outputs)

        for src_channel in src_channels:
            if any(link.src_channel == src_channel and valid(link) for link in clip.node_collection.links):
                continue
            for dst_channel in dst_channels:
                if any(link.dst_channel == dst_channel and valid(link) for link in clip.node_collection.links):
                    continue
                self.add_link_callback(None, None, ("create", clip, src_channel, dst_channel))
                break

    def toggle_automation_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "manual" if input_channel.mode == "automation" else "automation"
    
    def enable_recording_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "armed"

    def create_automation_window(self, clip, input_channel, action):
        parent = get_automation_window_tag(input_channel)
        with dpg.window(
            tag=parent,
            label=f"Automation Window",
            width=1120,
            height=520,
            pos=(799, 18),
            show=False,
            no_move=True,
            no_title_bar=True,

        ) as window:
            self.gui_state["tags"]["hide_on_clip_selection"].append(parent)

            automation = input_channel.active_automation

            series_tag = f"{input_channel.id}.series"
            plot_tag = get_plot_tag(input_channel)
            playhead_tag = f"{input_channel.id}.gui.playhead"
            ext_value_tag = f"{input_channel.id}.gui.ext_value"
            menu_tag = f"{input_channel.id}.menu"
            tab_bar_tag = f"{input_channel.id}.tab_bar"

            def select_preset(sender, app_data, user_data):
                input_channel, automation = user_data
                self.state.execute_wrapper(f"set_active_automation {input_channel.id} {automation.id}")
                self.reset_automation_plot(input_channel)

            with dpg.menu_bar(tag=menu_tag):

                dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_enable_button", label="Disable" if input_channel.mode == "automation" else "Enable", callback=self.toggle_automation_mode, user_data=input_channel)
                dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_record_button", label="Record", callback=self.enable_recording_mode, user_data=input_channel)


                def default_time(sender, app_data, user_data):
                    user_data.speed = 0
                dpg.add_menu_item(
                    label="1",
                    callback=default_time,
                    user_data=input_channel,
                )

                def double_time(sender, app_data, user_data):
                    user_data.speed += 1
                dpg.add_menu_item(
                    label="x2",
                    callback=double_time,
                    user_data=input_channel,
                )

                def half_time(sender, app_data, user_data):
                    user_data.speed -= 1
                dpg.add_menu_item(
                    label="/2",
                    callback=half_time,
                    user_data=input_channel,
                )

                def update_automation_length(sender, app_data, user_data):
                    if app_data:
                        input_channel = user_data
                        input_channel.active_automation.set_length(app_data)
                        self.reset_automation_plot(input_channel)

                def update_preset_name(sender, app_data, user_data):
                    input_channel = user_data
                    automation = input_channel.active_automation
                    if automation is None:
                        return
                    automation.name = app_data
                    tab_bar_tag = f"{input_channel.id}.tab_bar"
                    dpg.configure_item(f"{tab_bar_tag}.{automation.id}.button", label=app_data)

                prop_x_start = 600
                dpg.add_text("Preset:", pos=(prop_x_start-200, 0))
                dpg.add_input_text(tag=f"{parent}.preset_name", label="", default_value="", pos=(prop_x_start-150, 0), on_enter=True, callback=update_preset_name, user_data=input_channel, width=100)
                
                dpg.add_text("Beats:", pos=(prop_x_start+200, 0))
                dpg.add_input_text(tag=f"{parent}.beats", label="", default_value=input_channel.active_automation.length, pos=(prop_x_start+230, 0), on_enter=True, callback=update_automation_length, user_data=input_channel, width=50)

            def delete_preset(sender, app_data, user_data):
                input_channel, automation = user_data
                
                def get_valid_automations(input_channel):
                    return [a for a in input_channel.automations if not a.deleted]

                if len(get_valid_automations(input_channel)) <= 1:
                    return

                success = self.state.execute_wrapper(f"delete {automation.id}")
                if success:
                    tab_bar_tag = f"{input_channel.id}.tab_bar"
                    tags_to_delete = [
                        f"{tab_bar_tag}.{automation.id}.button",
                        f"{tab_bar_tag}.{automation.id}.button.x", 
                    ]
                    for tag in tags_to_delete:
                        dpg.delete_item(tag)

                if input_channel.active_automation == automation:
                    input_channel.set_active_automation(get_valid_automations(input_channel)[0])
                    self.reset_automation_plot(input_channel)

            def add_preset(sender, app_data, user_data):
                input_channel = user_data
                success, automation = self.state.execute_wrapper(f"add_automation {input_channel.id}")
                tab_bar_tag = f"{input_channel.id}.tab_bar"
                if success:
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button", parent=tab_bar_tag, label=automation.name, callback=select_preset, user_data=(input_channel, automation))
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button.x", parent=tab_bar_tag, label="X", callback=delete_preset, user_data=(input_channel, automation))
                    self.reset_automation_plot(input_channel)

            tab_bar_tag = f"{input_channel.id}.tab_bar"
            with dpg.tab_bar(tag=tab_bar_tag):
                for automation_i, automation in enumerate(input_channel.automations):
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button", label=automation.name, callback=select_preset, user_data=(input_channel, automation))
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button.x", label="X", callback=delete_preset, user_data=(input_channel, automation))
                dpg.add_tab_button(label="+", callback=add_preset, user_data=input_channel, trailing=True)

            with dpg.plot(label=input_channel.active_automation.name, height=-1, width=-1, tag=plot_tag, query=True, callback=self.print_callback, anti_aliased=True, no_menus=True):
                min_value = input_channel.get_parameter("min").value
                max_value = input_channel.get_parameter("max").value
                x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.add_plot_axis(dpg.mvXAxis, label="x", tag=x_axis_limits_tag, no_gridlines=True)
                dpg.set_axis_limits(dpg.last_item(), -AXIS_MARGIN, input_channel.active_automation.length+AXIS_MARGIN)

                dpg.add_plot_axis(dpg.mvYAxis, label="y", tag=y_axis_limits_tag, no_gridlines=True)
                dpg.set_axis_limits(dpg.last_item(), -min_value, max_value*1.01)

                dpg.add_line_series(
                    [],
                    [],
                    tag=series_tag,
                    parent=dpg.last_item(),
                )

                self.reset_automation_plot(input_channel)

                dpg.add_drag_line(
                    label="Playhead",
                    tag=playhead_tag,
                    color=[255, 255, 0, 255],
                    vertical=True,
                    default_value=0,
                )
                dpg.add_line_series(
                    parent=y_axis_limits_tag,
                    label="Ext Value",
                    tag=ext_value_tag,
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=dpg.get_axis_limits(y_axis_limits_tag),
                )
                with dpg.popup(plot_tag, mousebutton=1):
                    dpg.add_menu_item(label="Duplicate", callback=self.double_automation)
                    with dpg.menu(label="Set Quantize"):
                        dpg.add_menu_item(label="Off", callback=self.set_quantize, user_data=None)
                        dpg.add_menu_item(label="1 bar", callback=self.set_quantize, user_data=4)
                        dpg.add_menu_item(label="1/2", callback=self.set_quantize, user_data=2)
                        dpg.add_menu_item(label="1/4", callback=self.set_quantize, user_data=1)
                        dpg.add_menu_item(label="1/8", callback=self.set_quantize, user_data=0.5)
                        dpg.add_menu_item(label="1/16", callback=self.set_quantize, user_data=0.25)
                    with dpg.menu(label="Interpolation Mode"):
                        dpg.add_menu_item(label="Linear", callback=self.set_interpolation, user_data="linear")
                        dpg.add_menu_item(label="Nearest", callback=self.set_interpolation, user_data="nearest")
                        dpg.add_menu_item(label="Nearest Up", callback=self.set_interpolation, user_data="nearest-up")
                        dpg.add_menu_item(label="Zero", callback=self.set_interpolation, user_data="zero")
                        dpg.add_menu_item(label="S-Linear", callback=self.set_interpolation, user_data="slinear")
                        dpg.add_menu_item(label="Quadratic", callback=self.set_interpolation, user_data="quadratic")
                        dpg.add_menu_item(label="Cubic", callback=self.set_interpolation, user_data="cubic")
                        dpg.add_menu_item(label="Previous", callback=self.set_interpolation, user_data="previous")
                        dpg.add_menu_item(label="Next", callback=self.set_interpolation, user_data="next")

            dpg.bind_item_theme(ext_value_tag, "bg_line.theme")
            dpg.bind_item_theme(series_tag, "automation_line.theme")

    def set_quantize(self, sender, app_data, user_data):
        self._quantize_amount = user_data
        self.reset_automation_plot(self._active_input_channel)

    def set_interpolation(self, sender, app_data, user_data):
        if valid(self._active_input_channel.active_automation):
            self._active_input_channel.active_automation.set_interpolation(user_data)
        self.reset_automation_plot(self._active_input_channel)

    def double_automation(self):
       if self._active_input_channel is None:
            return
        
       automation = self._active_input_channel.active_automation
       if automation is None:
            return

       success = self.state.execute_wrapper(f"double_automation {automation.id}")
       if success:
        self.reset_automation_plot(self._active_input_channel)

    def reset_automation_plot(self, input_channel):
        window_tag = get_automation_window_tag(input_channel)
        automation = input_channel.active_automation
        series_tag = f"{input_channel.id}.series"
        plot_tag = get_plot_tag(input_channel)
        x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
        y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
        

        dpg.configure_item(plot_tag, label=input_channel.active_automation.name)
        dpg.set_axis_limits(x_axis_limits_tag, -AXIS_MARGIN, input_channel.active_automation.length+AXIS_MARGIN)

        dpg.set_value(f"{window_tag}.beats", value=automation.length)
        dpg.set_value(f"{window_tag}.preset_name", value=automation.name)

        # Delete existing points
        for item in self.gui_state["point_tags"]:
            dpg.delete_item(item)

        # Add new points
        point_tags = []
        for i, x in enumerate(automation.values_x):
            if x is None:
                continue
            y = automation.values_y[i]
            point_tag = f"{input_channel.id}.{automation.id}.series.{i}"
            dpg.add_drag_point(
                color=[0, 255, 255, 255],
                default_value=[x, y],
                callback=self.update_automation_point_callback,
                parent=plot_tag,
                tag=point_tag,
                user_data=input_channel,
                thickness=10,
            )
            point_tags.append(point_tag)
        self.gui_state["point_tags"] = point_tags

        # Add quantization bars
        y_limits = dpg.get_axis_limits(y_axis_limits_tag)
        if self._quantize_amount is not None:
            i = 0
            while True:
                tag = f"gui.quantization_series.{i}"
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
                else:
                    break
                i += 1

            n_bars = int(input_channel.active_automation.length / self._quantize_amount)
            for i in range(n_bars+1):
                tag = f"gui.quantization_series.{i}"
                value = i * self._quantize_amount
                dpg.add_line_series(
                    x=[value, value],
                    y=y_limits,
                    tag=tag,
                    parent=y_axis_limits_tag,
                )
                dpg.bind_item_theme(tag, "bg_line.theme")

    def create_inspector_window(self):
        with dpg.window(
            label=f"Inspector",
            width=750,
            height=600,
            pos=(810, 0),
            show=False,
            tag="inspector.gui.window"
        ) as window:
            with dpg.plot(label="Inspector", height=-1, width=-1):
                dpg.add_plot_axis(dpg.mvXAxis, label="x")
                dpg.set_axis_limits(dpg.last_item(), 0, len(self._inspecter_x))

                dpg.add_plot_axis(dpg.mvYAxis, label="y")
                dpg.add_line_series(
                    [],
                    [],
                    tag="inspector.series",
                    parent=dpg.last_item(),
                )

    def create_io_window(self):
        with dpg.window(
            label=f"I/O",
            width=750,
            height=300,
            pos=(1180, 0),
            tag="io.gui.window"
        ) as window:
            output_table_tag = f"io.outputs.table"
            input_table_tag = f"io.inputs.table"

            def set_io_type(sender, app_data, user_data):
                index, io_type, input_output, *args = user_data
                table_tag = f"io.{input_output}.table"
                self.gui_state["io_types"][input_output][int(index)] = io_type
                dpg.configure_item(f"{table_tag}.{index}.type", label=io_type.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io_type.arg_template if not args else args[0])

            def create_io(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    _, index, input_output = user_data
                    io_type = self.gui_state["io_types"][input_output][int(index)]
                    success, io = self.state.execute(f"create_io {index} {input_output} {io_type.type} {app_data}")
                    
                    if not success:
                        raise RuntimeError("Failed to create IO")

                    self.gui_state["io_args"][input_output][index] = app_data
                else: # restore
                    _, index, io = user_data

                table_tag = f"io.{input_output}.table"
                dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io.arg_string)

            def connect(sender, app_data, user_data):
                _, index, input_output = user_data
                table_tag = f"io.{input_output}.table"
                create_io(sender, dpg.get_value(f"{table_tag}.{index}.arg"), user_data)

            def hide_midi_menu_and_set_io_type(sender, app_data, user_data):
                dpg.configure_item("midi_devices_window", show=False)
                set_io_type(sender, app_data, user_data)

            def create_and_show_midi_menu(sender, app_data, user_data):
                try:
                    dpg.delete_item("midi_devices_window")
                except:
                    pass

                i, in_out = user_data
                devices = mido.get_input_names() if in_out == "inputs" else mido.get_output_names() 
                with dpg.window(tag="midi_devices_window", no_title_bar=True, max_size=(200, 400), pos=(self.mouse_x, self.mouse_y)):
                    for device_name in devices:
                        dpg.add_menu_item(label=device_name, callback=hide_midi_menu_and_set_io_type, user_data=(i, model.MidiInputDevice if in_out == "inputs" else model.MidiOutputDevice, in_out, device_name))

            with dpg.table(header_row=True, tag=input_table_tag):
                type_column_tag = f"{input_table_tag}.column.type"
                arg_column_tag = f"{input_table_tag}.column.arg"
                connected_column_tag = f"{input_table_tag}.column.connected"
                dpg.add_table_column(label="Input Type", tag=type_column_tag)
                dpg.add_table_column(label="Input", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connect", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        input_type = model.IO_INPUTS[i]
                        type_tag = f"{input_table_tag}.{i}.type"
                        dpg.add_button(label="Select Input Type" if input_type is None else input_type.nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_input_type in model.ALL_INPUT_TYPES:
                                if io_input_type.type == "midi_input":
                                    dpg.add_menu_item(label=io_input_type.nice_title, callback=create_and_show_midi_menu, user_data=(i, "inputs"))
                                else:
                                    dpg.add_menu_item(label=io_input_type.nice_title, callback=set_io_type, user_data=(i, io_input_type, "inputs"))
                        
                        arg_tag = f"{input_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=create_io, user_data=("create", i, "inputs"))

                        connected_tag = f"{input_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "inputs"))

            with dpg.table(header_row=True, tag=output_table_tag):
                type_column_tag = f"{output_table_tag}.column.type"
                arg_column_tag = f"{output_table_tag}.column.arg"
                connected_column_tag = f"{output_table_tag}.column.connected"
                dpg.add_table_column(label="Output Type", tag=type_column_tag)
                dpg.add_table_column(label="Output", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connect", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        type_tag = f"{output_table_tag}.{i}.type"
                        dpg.add_button(label="Select Output Type" if model.IO_OUTPUTS[i] is None else model.IO_OUTPUTS[i].nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_output_type in model.ALL_OUTPUT_TYPES:
                                if io_output_type.type == "midi_output":
                                    dpg.add_menu_item(label=io_output_type.nice_title, callback=create_and_show_midi_menu, user_data=(i, "outputs"))
                                else:
                                    dpg.add_menu_item(label=io_output_type.nice_title, callback=set_io_type, user_data=(i, io_output_type, "outputs"))

                        arg_tag = f"{output_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=create_io, user_data=("create", i, "outputs"))
                        
                        connected_tag = f"{output_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "outputs"))

        ###############
        ### Restore ###
        ###############

    def create_track_output_configuration_window(self, track, show=False):
        window_tag = get_output_configuration_window_tag(track)
        with dpg.window(
            tag=window_tag,
            label=f"Output Configuration",
            width=400,
            height=SCREEN_HEIGHT * 5/6,
            pos=(799,60),
            show=show,
        ) as window:
            output_table_tag = f"{window_tag}.output_table"

            with dpg.group(horizontal=True):
                def set_track_title_button_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        track.name = app_data
                        dpg.set_value(user_data, track.name)
                track_title_tag = f"{track.id}.gui.button"
                dpg.add_input_text(tag=f"{track.id}.name", default_value=track.name, user_data=track_title_tag, callback=set_track_title_button_text, width=75)

                dpg.add_button(
                    label="Add Output",
                    callback=self.add_track_output,
                    user_data=("create", track)
                )
                dpg.add_button(label="Add Fixture")    
                with dpg.popup(dpg.last_item(), mousebutton=0):
                    for fixture in fixtures.FIXTURES:
                        dpg.add_menu_item(label=fixture.name, callback=self.add_fixture, user_data=(track, fixture))

                    def open_fixture_dialog():
                        dpg.configure_item("open_fixture_dialog", show=True)
                    dpg.add_menu_item(label="Custom", callback=open_fixture_dialog)

            with dpg.table(header_row=True, tag=output_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="DMX Ch.", tag=f"{output_table_tag}.column.dmx_address")
                dpg.add_table_column(label="Name", tag=f"{output_table_tag}.column.name")
                dpg.add_table_column(tag=f"{output_table_tag}.column.delete", width=10)

        ###############
        ### Restore ###
        ###############
        for output_index, output_channel in enumerate(track.outputs):
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                self.add_track_output_group(sender=None, app_data=None, user_data=("restore", track, output_channel))
            else:
                self.add_track_output(sender=None, app_data=None, user_data=("restore", track, output_channel))

    def add_track_output(self, sender, app_data, user_data):  
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            address = user_data[2] if len(user_data) == 3   else 1
            success, output_channel = self.state.execute(f"create_output {track.id} {address}")
            if not success:
                return
        else: # restore
            output_channel = user_data[2]

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(tag=f"{output_channel.id}.dmx_address", width=75, default_value=output_channel.dmx_address, callback=self.update_channel_attr, user_data=(output_channel, "dmx_address"))
            dpg.add_input_text(tag=f"{output_channel.id}.name", default_value=output_channel.name, callback=self.update_channel_attr, user_data=(output_channel, "name"), width=150)
            dpg.add_button(label="X", callback=self._delete_track_output, user_data=(track, output_channel))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_node(clip, output_channel)

    def add_track_output_group(self, sender, app_data, user_data):
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            starting_address = user_data[2]
            channel_names = user_data[3]
            success, output_channel_group = self.state.execute(f"create_output_group {track.id} {starting_address} {','.join(channel_names)}")
            if not success:
                return
        else: # restore
            output_channel_group = user_data[2]

        def update_channel_group_address(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_starting_address(app_data)

        def update_channel_group_name(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_name(app_data)
            dpg.configure_item(get_node_tag(self.gui_state["active_clip"], output_channel_group), label=app_data)

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel_group.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(tag=f"{output_channel_group.id}.dmx_address", width=75, default_value=output_channel_group.dmx_address, callback=update_channel_group_address, user_data=output_channel_group)
            dpg.add_input_text(tag=f"{output_channel_group.id}.name", default_value=output_channel_group.name, callback=update_channel_group_name, user_data=output_channel_group, width=150)
            dpg.add_button(label="X", callback=self._delete_track_output_group, user_data=(track, output_channel_group))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_group_node(clip, output_channel_group)

    def add_fixture(self, sender, app_data, user_data):
        track = user_data[0]
        fixture = user_data[1]
        starting_address = fixture.address

        for output_channel in track.outputs:
            starting_address = max(starting_address, output_channel.dmx_address + 1)

        self.add_track_output_group(None, None, ("create", track, starting_address, fixture.channels))

    ###

    def _delete_track_output(self, _, __, user_data):
        with self.gui_state["lock"]:
            track, output_channel = user_data
            # Delete the entire window, since we will remake it later.
            parent = get_output_configuration_window_tag(track)
            dpg.delete_item(parent)

            success = self.state.execute_wrapper(f"delete {output_channel.id}")
            if success:
                # Delete each Node from each clip's node editor
                for clip_i, clip in enumerate(track.clips):
                    if clip is None:
                        continue
                    self._delete_node_gui(get_node_tag(clip, output_channel), output_channel.id)

                # Remake the window
                self.create_track_output_configuration_window(track, show=True)
            else:
                RuntimeError(f"Failed to delete: {output_channel.id}")

    def _delete_track_output_group(self, _, __, user_data):
        with self.gui_state["lock"]:
            track, output_channel_group = user_data
            # Delete the entire window, since we will remake it later.
            parent = get_output_configuration_window_tag(track)
            dpg.delete_item(parent)

            success = self.state.execute_wrapper(f"delete {output_channel_group.id}")
            if success:
                # Delete each Node from each clip's node editor
                for clip_i, clip in enumerate(track.clips):
                    if clip is None:
                        continue
                    self._delete_node_gui(get_node_tag(clip, output_channel_group), output_channel_group.id)

                # Remake the window
                self.create_track_output_configuration_window(track, show=True)
            else:
                RuntimeError(f"Failed to delete: {output_channel_group.id}")

    def copy_selected(self):
        window_tag_alias = dpg.get_item_alias(dpg.get_active_window())
        if window_tag_alias is None:
            return

        new_copy_buffer = []
        if window_tag_alias.endswith("node_window"):
            node_editor_tag = get_node_editor_tag(self.gui_state["active_clip"])
            for item in dpg.get_selected_nodes(node_editor_tag):                    
                alias = dpg.get_item_alias(item)
                item_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                obj = self.state.get_obj(item_id)
                if isinstance(obj, (model.DmxOutputGroup, model.DmxOutput)):
                    continue
                new_copy_buffer.append(obj)
            for item in dpg.get_selected_links(node_editor_tag):
                alias = dpg.get_item_alias(item)
                link_key = alias.replace(".gui.link", "")
                new_copy_buffer.append(link_key)

        elif window_tag_alias == "clip.gui.window":
            if self.gui_state["active_clip_slot"] is not None:
                clip = self.state.tracks[self.gui_state["active_clip_slot"][0]].clips[self.gui_state["active_clip_slot"][0]]
                if clip is not None:
                    new_copy_buffer.append(clip)

        if new_copy_buffer:
            self.gui_state["copy_buffer"] = new_copy_buffer

    def paste_selected(self):
        window_tag_alias = dpg.get_item_alias(dpg.get_active_window())
        if window_tag_alias is None:
            return

        if window_tag_alias.endswith("node_window"):
            # First add any nodes
            duplicate_map = {}
            link_ids = []
            for obj in self.gui_state["copy_buffer"]:
                if isinstance(obj, str):
                    link_ids.append(obj)
                elif isinstance(obj, model.ClipInputChannel):
                    success, new_input_channel = self.state.execute_wrapper(f"duplicate_node {self.gui_state['active_clip'].id} {obj.id}")
                    if success:
                        self.add_input_node(sender=None, app_data=None, user_data=("restore", (self.gui_state["active_clip"], new_input_channel), False))
                        gui_elements.copy_node_position(self.gui_state["active_clip"], obj, self.gui_state["active_clip"], new_input_channel)
                        duplicate_map[obj.id] = new_input_channel
                    else:
                        raise RuntimeError(f"Failed to duplicate {obj.id}")
                elif isinstance(obj, model.FunctionNode):
                    success, new_node = self.state.execute_wrapper(f"duplicate_node {self.gui_state['active_clip'].id} {obj.id}")
                    if success:
                        if isinstance(obj, model.FunctionCustomNode):
                            self.add_custom_function_node(sender=None, app_data=None, user_data=("restore", (self.gui_state["active_clip"], new_node), False))
                        else:
                            self.add_function_node(sender=None, app_data=None, user_data=("restore", (self.gui_state["active_clip"], new_node), False))
                        gui_elements.copy_node_position(self.gui_state["active_clip"], obj, self.gui_state["active_clip"], new_node)
                        duplicate_map[obj.id] = new_node
                        for i, input_channel in enumerate(obj.inputs):
                            duplicate_map[input_channel.id] = new_node.inputs[i]
                        for i, output_channel in enumerate(obj.outputs):
                            duplicate_map[output_channel.id] = new_node.outputs[i]                         
                    else:
                       raise RuntimeError("Failed to duplicate_node")
                else:
                        raise RuntimeError(f"Failed to duplicate {obj.id}")
            
            # Then replace old ids with new ids in selected links
            new_link_ids = []
            for link_id in link_ids:   
                new_link_id = link_id
                for old_id, new_obj in duplicate_map.items():
                    new_link_id = new_link_id.replace(old_id, new_obj.id)
                new_link_ids.append(new_link_id)

            # Create new links
            for link_id in new_link_ids:
                src_tag, dst_tag = link_id.split(":")
                self.add_link_callback(sender=None, app_data=(src_tag, dst_tag), user_data=("create", self.gui_state["active_clip"]))

        elif window_tag_alias == self.clip_window.alias:
            if self.gui_state["active_clip_slot"] is not None:
                self.clip_window.paste_clip(self.gui_state["active_clip_slot"][0], self.gui_state["active_clip_slot"][1])


    def get_all_valid_clip_input_channels(self):
        src_channels = []
        for track in self.state.tracks:
            for clip in track.clips:
                if clip is None:
                    continue
                for input_channel in clip.inputs:
                    if input_channel.deleted:
                        continue
                    src_channels.append(input_channel)
        return src_channels

    def get_all_valid_track_output_channels(self, clip):
        output_channels = []
        for output_channel in clip.outputs:
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                output_channels.extend(output_channel.outputs)
            else:   
                output_channels.append(output_channel)
        return output_channels

    def get_all_valid_node_src_channels(self, clip):
        src_channels = []
        for input_channel in clip.inputs:
            if input_channel.deleted:
                continue
            src_channels.append(input_channel)
        for node in clip.node_collection.nodes:
            if node.deleted:
                continue
            for output_channel in node.outputs:
                if output_channel.deleted:
                    continue
                src_channels.append(output_channel)
        return src_channels

    def get_all_valid_dst_channels(self, clip):
        if clip is None:
            return []
        dst_channels = []
        for output_channel in clip.outputs:
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                dst_channels.extend(output_channel.outputs)
            else:   
                dst_channels.append(output_channel)
        for node in clip.node_collection.nodes:
            if node.deleted:
                continue
            for input_channel in node.inputs:
                if input_channel.deleted:
                    continue
                dst_channels.append(input_channel)
        return dst_channels

    def update_state_from_gui(self):
        pass

    def update_gui_from_state(self):
        if self.state.command_count != self.last_command_count:
            dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}] *")

        self.last_command_count = self.state.command_count

        dpg.configure_item("play_button", label="[Pause]" if self.state.playing else "[Play]")

        if valid(self.gui_state["active_clip"]):
            # This is only setting the GUI value, so we only need to update the active clip.
            for dst_channel in self.get_all_valid_dst_channels(self.gui_state["active_clip"]):
                if hasattr(dst_channel, "dmx_address"):
                    tag = get_output_node_value_tag(self.gui_state["active_clip"], dst_channel)
                else:
                    tag = f"{dst_channel.id}.value"
                dpg.set_value(tag, dst_channel.get())

            # This is only setting the GUI value, so we only need to update the active clip.
            for src_channel in self.get_all_valid_node_src_channels(self.gui_state["active_clip"]):
                tag = f"{src_channel.id}.value"
                dpg.set_value(tag, src_channel.get())


        # Update automation points
        if valid(self._active_input_channel) and valid(self._active_input_channel.active_automation):
            automation = self._active_input_channel.active_automation
            values = sorted(
                zip(automation.values_x, automation.values_y), 
                key=lambda t: t[0] if t[0] is not None else 0
            )
            xs = np.arange(0, self._active_input_channel.active_automation.length, 0.01)
            ys = self._active_input_channel.active_automation.f(xs)
            dpg.configure_item(
               f"{self._active_input_channel.id}.series",
                x=xs,
                y=ys,
            )

        # Update Inspector
        if valid(self._active_output_channel):
            dpg.configure_item(
                    "inspector.series",
                    x=self._inspecter_x,
                    y=self._active_output_channel.history[-1 - len(self._inspecter_x):-1],
                )

        # Set the play heads to the correct position
        if valid(self.gui_state["active_clip"], self._active_input_channel):
            if valid(self._active_input_channel.active_automation):
                playhead_tag = f"{self._active_input_channel.id}.gui.playhead"
                ext_value_tag = f"{self._active_input_channel.id}.gui.ext_value"
                playhead_color = {
                    "armed": [255, 100, 100, 255],
                    "recording": [255, 0, 0, 255],
                    "automation": [255, 255, 0, 255],
                    "manual": [200, 200, 200, 255],
                }
                dpg.configure_item(playhead_tag, color=playhead_color[self._active_input_channel.mode])
                dpg.configure_item(f"{self._active_input_channel.id}.gui.automation_enable_button", label="Disable" if self._active_input_channel.mode == "automation" else "Enable")
                dpg.set_value(
                    playhead_tag, 
                    self._active_input_channel.last_beat % self._active_input_channel.active_automation.length if self._active_input_channel.mode in ["automation", "armed", "recording"] else 0
                )

                x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                dpg.configure_item(
                    ext_value_tag, 
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=[self._active_input_channel.ext_get(), self._active_input_channel.ext_get()]
                )

        if model.LAST_MIDI_MESSAGE is not None:
            device_name, message = model.LAST_MIDI_MESSAGE
            channel = message.channel
            note_control = message.control if message.is_cc() else message.note
            dpg.set_value("last_midi_message", "" if model.LAST_MIDI_MESSAGE is None else f"{device_name}: {channel}/{note_control}")

    def mouse_move_callback(self, sender, app_data, user_data):
        cur_x, cur_y = app_data
        self.mouse_x = cur_x
        self.mouse_y = cur_y

        # Relative to window
        cur_x, cur_y = dpg.get_mouse_pos()
        self.mouse_drag_x = cur_x - self.mouse_click_x
        self.mouse_drag_y = cur_y - self.mouse_click_y

    def mouse_click_callback(self, sender, app_data, user_data):
        # TODO: separate click by relative and non-relative positions
        # Automation window wants relative
        # Node Editor wants non relative
        self.mouse_click_x, self.mouse_click_y = self.mouse_x, self.mouse_y

        if app_data == 0:
            if self.gui_state["active_clip"] is not None:
                tag = get_node_window_tag(self.gui_state["active_clip"])
                window_x, window_y = dpg.get_item_pos(tag)
                window_x2, window_y2 = window_x + dpg.get_item_width(tag), window_y + dpg.get_item_height(tag)
                self.node_editor_window_is_focused = inside((self.mouse_x, self.mouse_y), (window_x, window_x2, window_y+10, window_y2))
        elif app_data == 1:
            self.mouse_clickr_x, self.mouse_clickr_y = self.mouse_x, self.mouse_y

            # Right clicking things should disable focus
            self.node_editor_window_is_focused = False
            
            # Show popup menu
            # TODO: Interfering with node properties
            if self.gui_state["active_clip"] is not None and self.ctrl:
                tag = get_node_window_tag(self.gui_state["active_clip"])
                window_x, window_y = dpg.get_item_pos(tag)
                window_x2, window_y2 = window_x + dpg.get_item_width(tag), window_y + dpg.get_item_height(tag)
                if inside((self.mouse_x, self.mouse_y), (window_x, window_x2, window_y+10, window_y2)):
                    popup_menu_tag = get_node_window_tag(self.gui_state["active_clip"]) + ".popup_menu"
                    dpg.configure_item(popup_menu_tag, pos=(self.mouse_x, self.mouse_y))
                    dpg.configure_item(popup_menu_tag, show=True)
                    dpg.focus_item(popup_menu_tag)

    def mouse_double_click_callback(self, sender, app_data, user_data):
        window_tag = dpg.get_item_alias(dpg.get_item_parent(dpg.get_active_window()))
        mouse_pos = dpg.get_mouse_pos()

        if app_data == 0:
            if window_tag is not None and window_tag.endswith("automation_window"):
                plot_mouse_pos = dpg.get_plot_mouse_pos()
                automation = self._active_input_channel.active_automation
                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                    y_axis_limits_tag = f"{self._active_input_channel.id}.plot.y_axis_limits"
                    if norm_distance((x,y), plot_mouse_pos, dpg.get_axis_limits(x_axis_limits_tag), dpg.get_axis_limits(y_axis_limits_tag)) <= 0.015:
                        if self.state.execute_wrapper(f"remove_automation_point {self._active_input_channel.id} {i}"):
                            point_tag = f"{self._active_input_channel.id}.series.{i}"
                            dpg.delete_item(point_tag)
                        return

                point = self._quantize_point(*plot_mouse_pos, self._active_input_channel.dtype, automation.length, quantize_x=False)
                success = self.state.execute_wrapper(
                    f"add_automation_point {automation.id} {point[0]},{point[1]}"
                )
                if success:
                    self.reset_automation_plot(self._active_input_channel)

    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)
        #print(key_n)
        if key == " ":
            self.state.toggle_play()
        elif key_n in [8, 46] and self.node_editor_window_is_focused and self.ctrl:
            self.delete_selected_nodes_callback()
        elif key_n in [120]:
            if self._active_input_channel is not None:
                self.enable_recording_mode(None, None, self._active_input_channel)
        elif key_n in [9]:
            self.toggle_node_editor_fullscreen()
        elif key in ["C"]:
            if self.ctrl:
                self.copy_selected()
        elif key in ["O"]:
            if self.ctrl:
                self.open_menu_callback()
        elif key in ["I"]:
            if self.ctrl and self.shift:
                if self.gui_state["active_clip"]:
                    self.add_input_node(None, None,( "create", (self.gui_state["active_clip"], "int"), False))
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self.gui_state["active_clip"]:
                    self.add_input_node(None, None, ("create", (self.gui_state["active_clip"], "bool"), False))
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self.gui_state["active_clip"]:
                    self.add_input_node(None, None, ("create", (self.gui_state["active_clip"], "float"), False))
        elif key in ["T"]:
            if self.state.mode == "performance":
                self.tap_tempo()
        elif key in ["V"]:
            if self.ctrl:
                    self.paste_selected()
        elif key in ["R"]:
            if self.ctrl:
                if valid(self._active_clip):
                    node_editor_tag = get_node_editor_tag(self.gui_state["active_clip"])
                    items = dpg.get_selected_nodes(node_editor_tag)
                if items:
                    dpg.set_value("rename_node.text", "")
                    dpg.configure_item("rename_node.popup", show=True)
                    dpg.focus_item("rename_node.text")
            elif self.state.mode == "performance":
                self.reset_time()
        elif key in ["N"]:
            if self.ctrl:
                for track_i, track in enumerate(self.state.tracks):
                    if track == self.gui_state["active_track"]:
                        for clip_i, clip in enumerate(track.clips):
                            if clip is None:
                                self.clip_window.create_new_clip_callback(None, None, ("create", track_i, clip_i))
                                return
        elif key in ["S"]:
            if self.shift and self.ctrl:
                self.save_as_menu_callback()
            elif self.ctrl:
                self.save_menu_callback()

        elif key_n in [187]:
            if self.ctrl and self.shift:
                if self._last_add_function_node is not None:
                    self.add_function_node(*self._last_add_function_node)

    def key_down_callback(self, sender, app_data, user_data):
        keys = app_data
        if not isinstance(app_data, list):
            keys = [app_data]

        if 17 in keys:
            self.ctrl = True
        if 16 in keys:
            self.shift = True

    def key_release_callback(self, sender, app_data, user_data):
        if not isinstance(app_data, int):
            return
        key_n = app_data
        key = chr(key_n)
        if key_n == 17:
            self.ctrl = False
        if key_n == 16:
            self.shift = False


    def print_callback(self, sender, app_data, user_data):
        print(sender)
        print(app_data)
        print(user_data)

    def update_automation_point_callback(self, sender, app_data, user_data):
        """Callback when a draggable point it moved."""
        input_channel = user_data
        automation = input_channel.active_automation
        tag = dpg.get_item_alias(sender)
        point_id, point_index = tag.split(".series.")
        point_index = int(point_index)

        x, y, *_ = dpg.get_value(sender)
        max_x_i = automation.values_x.index(max(automation.values_x, key=lambda x: x or 0))
        original_x = automation.values_x[point_index]
        if point_index in [0, max_x_i]:
            dpg.set_value(sender, (original_x, y))
            x = original_x

        quantize_x = True
        delta_x = x - original_x
        if self._quantize_amount is not None and (abs(delta_x) < self._quantize_amount/3):
            x = original_x
            quantize_x = False

        x, y = self._quantize_point(x, y, input_channel.dtype, automation.length, quantize_x=quantize_x)
        dpg.set_value(sender, (x, y))

        success = self.state.execute_wrapper(f"update_automation_point {automation.id} {point_index} {x},{y}")
        if not success:
            raise RuntimeError("Failed to update automation point")

    def _quantize_point(self, x, y, dtype, length, quantize_x=True):
        x2 = x
        y2 = y
        if dtype == "bool":
            y2 = int(y > 0.5)
        elif dtype == "int":
            y2 = int(y)

        if self._quantize_amount is not None and quantize_x:
            x2 /= self._quantize_amount
            x2 = round(x2)
            x2 *= self._quantize_amount
            x2 = min(length - 0.0001, max(0, x2))

        return x2, y2

    def save(self):
        node_positions = {}
        for track_i, track in enumerate(self.state.tracks):
            for clip in track.clips:
                if clip is None:
                    continue
                for input_channel in clip.inputs:
                    if not valid(input_channel):
                        continue
                    tag = get_node_tag(clip, input_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)
                for output_channel in clip.outputs:
                    if not valid(output_channel):
                        continue
                    tag = get_node_tag(clip, output_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)
                for node in clip.node_collection.nodes:
                    if not valid(node):
                        continue
                    tag = get_node_tag(clip, node)
                    node_positions[tag] = dpg.get_item_pos(tag)

        io_type_data = {
            "inputs": {},
            "outputs": {},
        }

        io_arg_data = {
            "inputs": {},
            "outputs": {},
        }
        for inout in ["inputs", "outputs"]:
            for index, io_args in self.gui_state["io_args"][inout].items():
                io_arg_data[inout][index] = io_args


        gui_data = {
            "node_positions": node_positions,
            "io_types": io_type_data,
            "io_args": io_arg_data,
            "point_tags": self.gui_state["point_tags"],
            "track_last_active_clip": self.gui_state["point_tags"],
        }

        if self.state.project_filepath is not None:
            self.state.project_name = os.path.basename(self.state.project_filepath).replace(f".{PROJECT_EXTENSION}", "")
            data = {
                "state": self.state.serialize(),
                "gui": gui_data
            }
            with open(self.state.project_filepath, "w") as f:
                f.write(json.dumps(data, indent=4, sort_keys=False))

            dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}]")

    def restore_gui_state(self):
        for tag, pos in self.gui_state["node_positions"].items():
            dpg.set_item_pos(tag, pos)

        for i, args in self.gui_state["io_args"]["inputs"].items():
            dpg.set_value(f"io.inputs.table.{i}.arg", args)
        for i, args in self.gui_state["io_args"]["outputs"].items():
            dpg.set_value(f"io.outputs.table.{i}.arg", args)

    def deserialize(self, data):
        self.state.deserialize(data["state"])
        self.gui_state = data["gui"]

    def open_project(self, filepath):
        new_cmd = ["python"] + sys.argv + ["--project", filepath]
        subprocess.Popen(new_cmd)
        dpg.stop_dearpygui()


if __name__ == "__main__":
    logging.basicConfig(filename="log.txt",
                        filemode='w',
                        format='[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
                        level=logging.DEBUG)

    parser = argparse.ArgumentParser(description="NodeDMX [BETA]")
    parser.add_argument("--project", 
                        default=None,
                        dest="project_filepath",
                        help="Project file path.")

    args = parser.parse_args()

    gui = Gui()

    if args.project_filepath:
        logging.debug("Opening %s", args.project_filepath)
        with open(args.project_filepath, 'r') as f:
            data = json.load(f)
            gui.deserialize(data)

    gui.run()
    logging.info("Exiting.")

