"""
TODO:
    Remove get_*_tag()
"""
import dearpygui.dearpygui as dpg
import logging

logger = logging.getLogger(__name__)

def register_handler(add_item_handler_func, tag, function, user_data):
    handler_registry_tag = f"{tag}.item_handler_registry"
    if not dpg.does_item_exist(handler_registry_tag):
        dpg.add_item_handler_registry(tag=handler_registry_tag)
    add_item_handler_func(parent=handler_registry_tag, callback=function, user_data=user_data)
    dpg.bind_item_handler_registry(tag, handler_registry_tag)


def add_passive_button(group_tag, text_tag, text, single_click_callback=None, double_click_callback=None, user_data=None, double_click=False):
    dpg.add_text(parent=group_tag, default_value=text, tag=text_tag)
    dpg.add_text(parent=group_tag, default_value=" "*1000, tag=f"{text_tag}.filler")
    if single_click_callback is not None:
        register_handler(dpg.add_item_clicked_handler, group_tag, single_click_callback, user_data)
    if double_click_callback is not None:
        register_handler(dpg.add_item_double_clicked_handler, group_tag, double_click_callback, user_data)


def copy_node_position(self, from_clip, from_obj, to_clip, to_obj):
    from_pos = dpg.get_item_pos(get_node_tag(from_clip, from_obj))
    dpg.set_item_pos(get_node_tag(to_clip, to_obj), from_pos)

# TODO; Remove copied vars
TOP_LEFT = (0, 18)
SCREEN_WIDTH = 1940
SCREEN_HEIGHT = 1150
PROJECT_EXTENSION = "ndmx"
NODE_EXTENSION = "ndmxc"
AXIS_MARGIN = 0.025
HUMAN_DELAY = 0.125

def valid(*objs):
    return all([
        obj is not None and not getattr(obj, "deleted", False)
        for obj in objs
    ])

def get_group_tag(track_i, clip_i):
    return f"track[{track_i}].clip[{clip_i}].gui.table_group"

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

class Window:

    def __init__(self, state, gui_state, **kwargs):
        self.window = dpg.window(**kwargs)
        self.alias = dpg.get_item_alias(self.window)
        self.state = state
        self.gui_state = gui_state

    def init(self, state):
        raise NotImplementedError


class ClipWindow(Window):
    def __init__(self, state, gui_state):
        super().__init__(state, gui_state, tag="clip.gui.window", pos=(0,18), width=800, height=520, no_move=True, no_title_bar=True, no_resize=True)

        logging.debug("Creating Clip Window")
        with self.window:
            table_tag = f"clip_window.table"
            with dpg.table(header_row=False, tag=table_tag,
                   borders_innerH=True, borders_outerH=True, borders_innerV=True,
                   borders_outerV=True, policy=dpg.mvTable_SizingStretchProp, resizable=True):

                for track_i in range(len(self.state.tracks)):
                    dpg.add_table_column()

                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):
                        with dpg.table_cell():
                            with dpg.group(horizontal=True) as group_tag:
                                # When user clicks on the track title, bring up the output configuration window.
                                def select_track(sender, app_data, user_data):
                                    if self.gui_state["active_track"] == user_data:
                                        return

                                    self.save_last_active_clip()

                                    # Unset activate clip
                                    self.gui_state["active_clip"] = None
                                    for tag in self.gui_state["tags"]["hide_on_clip_selection"]:
                                        dpg.configure_item(tag, show=False)

                                    self.gui_state["active_track"] = user_data
                                    last_active_clip_id = self.gui_state["track_last_active_clip"].get(self.gui_state["active_track"].id)
                                    if last_active_clip_id is not None:
                                        self.gui_state["active_clip"] = self.state.get_obj(last_active_clip_id)
                                        self.select_clip_callback(None, None, (self.gui_state["active_track"], self.gui_state["active_clip"]))

                                    self.update_clip_status()

                                def show_track_output_configuration_window(sender, app_data, user_data):
                                    # Hide all track config windows
                                    for track in self.state.tracks:
                                        dpg.configure_item(get_output_configuration_window_tag(track), show=False)
                                    dpg.configure_item(get_output_configuration_window_tag(user_data), show=True)
                                    dpg.focus_item(get_output_configuration_window_tag(user_data))
                                    
                                text_tag = f"{track.id}.gui.button"
                                add_passive_button(group_tag, text_tag, track.name, single_click_callback=select_track, user_data=track)

                                # Menu for track
                                for tag in [text_tag, text_tag+".filler"]:
                                    with dpg.popup(tag, mousebutton=1):
                                        dpg.add_menu_item(label="Properties", callback=show_track_output_configuration_window, user_data=track)

                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    # Row
                    with dpg.table_row(height=10):
                        for track_i, track in enumerate(self.state.tracks):
                            # Col
                            clip = track.clips[clip_i]
                            with dpg.table_cell() as cell_tag:
                                group_tag = get_group_tag(track_i, clip_i)
                                with dpg.group(tag=group_tag, horizontal=True, horizontal_spacing=5):
                                    # Always add elements for an empty clip, if the clip is not empty, then we will update it after.
                                    text_tag = f"{track.id}.{clip_i}.gui.text"
                                    add_passive_button(
                                        group_tag, 
                                        text_tag, 
                                        "", 
                                        single_click_callback=self.select_clip_slot_callback, 
                                        double_click_callback=self.create_new_clip_callback, 
                                        user_data=("create", track_i, clip_i)
                                    )
                                    # Menu for empty clip
                                    with dpg.popup(text_tag+".filler", mousebutton=1):
                                        dpg.add_menu_item(label="New Clip", callback=self.create_new_clip_callback, user_data=("create", track_i, clip_i))
                                        dpg.add_menu_item(label="Paste", callback=self.paste_clip_callback, user_data=(track_i, clip_i))

                                    if clip is not None:
                                        self.populate_clip_slot(track_i, clip_i)

                self.update_clip_status()

    #### GUI Actions ####

    def populate_clip_slot(self, track_i, clip_i):
        group_tag = get_group_tag(track_i, clip_i)
        track = self.state.tracks[int(track_i)]
        clip = track.clips[int(clip_i)]

        for slot, child_tags in dpg.get_item_children(group_tag).items():
            for child_tag in child_tags:
                dpg.delete_item(child_tag)

        self._add_clip_elements(track, clip, group_tag, track_i, clip_i)
        self.save_last_active_clip()
        self.gui_state["active_track"] = track
        self.gui_state["active_clip"] = clip
        
        with self.gui_state["lock"]:
            node_editor_window = NodeEditorWindow(clip)
            self.node_editor_windows[node_editor_window.tag] = node_editor_window

        self.update_clip_status()

    def _add_clip_elements(self, track, clip, group_tag, track_i, clip_i):
        dpg.add_button(arrow=True, direction=dpg.mvDir_Right, tag=f"{clip.id}.gui.play_button", callback=self.play_clip_callback, user_data=(track,clip), parent=group_tag)                        
        
        text_tag = f"{clip.id}.name"
        add_passive_button(group_tag, text_tag, clip.name, self.select_clip_callback, user_data=(track, clip))

        def copy_clip_callback(sender, app_data, user_data):
            self.gui_state["copy_buffer"] = [user_data]

        for tag in [text_tag, text_tag+".filler"]:
            with dpg.popup(tag, mousebutton=1):
                dpg.add_menu_item(label="Copy", callback=copy_clip_callback, user_data=clip)
                dpg.add_menu_item(label="Paste", callback=self.paste_clip_callback, user_data=(track_i, clip_i))


    #### Actions ####

    def save_last_active_clip(self):
        """Saves the currently active clip for the currently active track."""
        if valid(self.gui_state["active_track"]) and valid(self.gui_state["active_clip"]):
            self.gui_state["track_last_active_clip"][self.gui_state["active_track"].id] = self.gui_state["active_clip"].id

    def update_clip_status(self):
        """Updates the visible status of each clip."""
        for track_i, track in enumerate(self.state.tracks):
            for clip_i, clip in enumerate(track.clips):

                # In edit mode the active clip should always play.
                if clip is not None:
                    if self.state.mode == "edit":
                        if self.gui_state["active_clip"] == clip:
                            if not clip.playing:
                                clip.start()
                        else:
                            clip.stop()

                active = 155 if self.gui_state["active_clip_slot"] == (track_i, clip_i) else 0
                if clip is not None and clip == self.gui_state["active_clip"]:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 155, 155, 100 + active])                    
                elif clip is not None and clip.playing:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 255, 10, 200 + active])
                elif clip is not None and not clip.playing:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 50, 100, 100 + active])
                else:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[50, 50, 50, 100 + active])                    

            if self.gui_state["active_track"] == track:
                dpg.highlight_table_column("clip_window.table", track_i, color=[100, 100, 100, 255])
            else:
                dpg.highlight_table_column("clip_window.table", track_i, color=[0, 0, 0, 0])

   
    def paste_clip(self, track_i, clip_i):
        # TODO: Prevent copy/pasting clips across different tracks (outputs wont match)
        if not self.gui_state["copy_buffer"]:
            return

        obj = self.gui_state["copy_buffer"][0]
        if not isinstance(obj, model.Clip):
            return

        clip = obj
        clip_id = clip.id
        success, new_clip = self.state.execute(f"duplicate_clip {track_i} {clip_i} {clip_id} ")
        if success:
            self.populate_clip_slot(track_i, clip_i)
        else:
            raise RuntimeError(f"Failed to duplicate clip {clip_id}")

        for i, old_channel in enumerate(clip.inputs):
            self.copy_node_position(clip, old_channel, new_clip, new_clip.inputs[i])

        for i, old_channel in enumerate(clip.outputs):
            self.copy_node_position(clip, old_channel, new_clip, new_clip.outputs[i])

        for i, old_node in enumerate(clip.node_collection.nodes):
            self.copy_node_position(clip, old_node, new_clip, new_clip.node_collection.nodes[i])

        self.save_last_active_clip()
        self.gui_state["active_track"] = self.state.tracks[track_i]
        self.gui_state["active_clip"] = new_clip
        self.update_clip_status()

    #### Callbacks ####

    def select_clip_callback(self, sender, app_data, user_data):
        """Called when a clip is selected."""
        track, clip = user_data

        self.save_last_active_clip()

        self.gui_state["active_track"] = track
        self.gui_state["active_clip"] = clip
        self.update_clip_status()

        for tag in self.gui_state["tags"]["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(clip), show=True)

    def play_clip_callback(self, sender, app_data, user_data):
        """Called when the play button for a clip is selected."""
        track, clip = user_data
        if self.state.execute(f"toggle_clip {track.id} {clip.id}"):
            self.update_clip_status()

    def paste_clip_callback(self, sender, app_data, user_data):
        """Called to paste a clip."""
        self.paste_clip(*user_data)
        self.update_clip_status()

    def select_clip_slot_callback(self, sender, app_data, user_data):
        track_i = int(user_data[1])
        clip_i = int(user_data[2])
        self.gui_state["active_clip_slot"] = (track_i, clip_i)
        self.gui_state["active_track"] = self.state.tracks[track_i]
        self.update_clip_status()

    def create_new_clip_callback(self, sender, app_data, user_data):
        action, track_i, clip_i = user_data
        track = self.state.tracks[int(track_i)]

        if action == "create":
            success, clip = self.state.execute(f"new_clip {track.id},{clip_i}")
            if not success:
                raise RuntimeError("Failed to create clip")
        else: # restore
            clip = self.state.tracks[int(track_i)].clips[int(clip_i)]
        
        self.populate_clip_slot(track_i, clip_i)


class NodeEditorWindow(Window):

    def __init__(self, clip):
        self.alias = f"{clip.id}.gui.node_window"

        super().__init__(
            tag=self.alias,
            label=f"Node Window | {clip.name}",
            width=SCREEN_WIDTH * 9.9 / 10,
            height=570,
            pos=(0, 537),
            no_title_bar=True,
            no_move=True,

        )

        logging.debug("Creating Node Editor Window  (%s)", clip.id)
        self.gui_state["tags"]["hide_on_clip_selection"].append(self.alias)

        with self.window as window:
            # Node Editor
            node_editor_tag = get_node_editor_tag(clip)
            dpg.add_node_editor(
                callback=self.add_link_callback,
                delink_callback=self.delete_link_callback,
                tag=node_editor_tag,
                user_data=("create", clip),
                minimap=True,
                minimap_location=dpg.mvNodeMiniMap_Location_BottomRight
            )

            with dpg.menu_bar() as menu_tag:
                self.add_node_menu(menu_tag, clip)

                dpg.add_menu_item(label="[Ctrl+Del]", callback=self.delete_selected_nodes_callback)

                # TODO: Add functionality
                def show_presets_window():
                    pass
                dpg.add_menu_item(label="Presets", callback=show_presets_window, user_data=clip)

                dpg.add_text(default_value="Clip Name:")

                def set_clip_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        clip.name = app_data
                dpg.add_input_text(source=f"{clip.id}.name", width=75, callback=set_clip_text)

        ###############
        ### Restore ###
        ###############

        # Popup window for adding node elements
        popup_window_tag = get_node_window_tag(clip) + ".popup_menu"
        with dpg.window(tag=popup_window_tag, show=False, no_title_bar=True):
            self.add_node_menu(popup_window_tag, clip)

        for input_index, input_channel in enumerate(clip.inputs):
            if input_channel.deleted:
                continue
            self.add_input_node(sender=None, app_data=None, user_data=("restore", (clip, input_channel), False))

        for output_index, output_channel in enumerate(clip.outputs):
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                self.add_output_group_node(clip, output_channel)
            else:
                self.add_output_node(clip, output_channel)

        for node_index, node in enumerate(clip.node_collection.nodes):
            if node.deleted:
                continue
            if isinstance(node, model.FunctionCustomNode):
                self.add_custom_function_node(sender=None, app_data=None, user_data=("restore", (clip, node), False))
            else:
                self.add_function_node(sender=None, app_data=None, user_data=("restore", (clip, node), False))

        for link_index, link in enumerate(clip.node_collection.links):
            if link.deleted:
                continue
            logger.debug(link.src_channel)
            logger.debug(link.dst_channel)
            self.add_link_callback(sender=None, app_data=None, user_data=("restore", clip, link.src_channel, link.dst_channel))

    #### GUI Actions ####

    def add_node_menu(self, parent, clip):
        right_click_menu = "popup_menu" in dpg.get_item_alias(parent)

        with dpg.menu(parent=parent, label="Inputs"):
            dpg.add_menu_item(label="Bool", callback=self.add_input_node, user_data=("create", (clip, "bool"), right_click_menu))
            dpg.add_menu_item(label="Integer", callback=self.add_input_node, user_data=("create", (clip, "int"), right_click_menu))
            dpg.add_menu_item(label="Float", callback=self.add_input_node, user_data=("create", (clip, "float"), right_click_menu))
            dpg.add_menu_item(label="Osc Integer", callback=self.add_input_node, user_data=("create", (clip, "osc_input_int"), right_click_menu))
            dpg.add_menu_item(label="Osc Float", callback=self.add_input_node, user_data=("create", (clip, "osc_input_float"), right_click_menu))
            dpg.add_menu_item(label="MIDI", callback=self.add_input_node, user_data=("create", (clip, "midi"), right_click_menu))

        with dpg.menu(parent=parent, label="Functions"):
            with dpg.menu(label="Aggregator"):
                for i in range(2, 33):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("aggregator", i, clip), right_click_menu), callback=self.add_function_node
                    )
            dpg.add_menu_item(
                label="Binary Operator", user_data=("create", ("binary_operator", None, clip), right_click_menu), callback=self.add_function_node
            )
            dpg.add_menu_item(
                label="Buffer", user_data=("create", ("buffer", None, clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="Changing", user_data=("create", ("changing", None, clip), right_click_menu), callback=self.add_function_node
            )   
            with dpg.menu(label="Demux"):
                for i in range(2, 33):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("demux", i, clip), right_click_menu), callback=self.add_function_node
                    )
            with dpg.menu(label="Last Changed"):
                for i in range(2, 33):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("last_changed", i, clip), right_click_menu), callback=self.add_function_node
                    )  
            with dpg.menu(label="Multiplexer"):
                for i in range(2, 33):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("multiplexer", i, clip), right_click_menu), callback=self.add_function_node
                    )
            dpg.add_menu_item(
                label="Passthrough", user_data=("create", ("passthrough", None, clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="Random", user_data=("create", ("random", None, clip), right_click_menu), callback=self.add_function_node
            )           
            dpg.add_menu_item(
                label="Sequencer", user_data=("create", ("sequencer", None, clip), right_click_menu), callback=self.add_function_node
            )  
            dpg.add_menu_item(
                label="Scale", user_data=("create", ("scale", None, clip), right_click_menu), callback=self.add_function_node
            )     
            dpg.add_menu_item(
                label="Sample", user_data=("create", ("sample", None, clip), right_click_menu), callback=self.add_function_node
            ) 
            with dpg.menu(label="Separator"):
                for i in range(2, 13):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("separator", i, clip), right_click_menu), callback=self.add_function_node
                    )
            with dpg.menu(label="Time"):
                dpg.add_menu_item(
                    label="Beat", user_data=("create", ("time_beat", None, clip), right_click_menu), callback=self.add_function_node
                ) 
                dpg.add_menu_item(
                    label="Second", user_data=("create", ("time_s", None, clip), right_click_menu), callback=self.add_function_node
                ) 
            dpg.add_menu_item(
                label="ToggleOnChange", user_data=("create", ("toggle_on_change", None, clip), right_click_menu), callback=self.add_function_node
            )
        with dpg.menu(parent=parent,label="Custom"):
            dpg.add_menu_item(
                label="New Custom Node", user_data=("create", ("custom", None, clip), right_click_menu), callback=self.add_custom_function_node
            ) 
            dpg.add_menu_item(
                label="Load Custom Node", user_data=(clip, right_click_menu), callback=self.load_custom_node_callback
            ) 

    #### Actions ####

    def _delete_link(self, link_tag, link_key, clip):
        src_node_attribute_tag, dst_node_attribute_tag = link_key.split(":")
        src_id = src_node_attribute_tag.replace(".node_attribute", "").split(".", 1)[-1]
        dst_id = dst_node_attribute_tag.replace(".node_attribute", "").split(".", 1)[-1]
        success = self.state.execute(f"delete_link {clip.id} {src_id} {dst_id}")
        if success:              
            dpg.delete_item(link_tag)
        else:
            raise RuntimeError(f"Failed to delete: {link_key}")

    def _delete_node_gui(self, node_tag, obj_id):
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        obj = self.state.get_obj(obj_id)
        channels_to_delete = []
        if isinstance(obj, model.Channel):
            # Input Nodes (also need to delete automation window)
            channels_to_delete = [obj]
            automation_window_tag = get_automation_window_tag(obj_id, is_id=True)
            if automation_window_tag in all_aliases:
                dpg.delete_item(automation_window_tag)

        # Function Nodes have their own inputs/outputs that we need to delete
        # corresponding links.
        if isinstance(obj, model.FunctionNode):
            channels_to_delete.extend(obj.inputs)
            channels_to_delete.extend(obj.outputs)

        self.delete_associated_links(channels_to_delete)
        
        # Finally, delete the node from the Node Editor
        dpg.delete_item(node_tag)

    def delete_associated_links(self, channels):
        # Delete any links attached to this node
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        ids = [channel.id for channel in channels]
        link_tags = [alias for alias in all_aliases if alias.endswith(".gui.link")]
        for id_ in ids:
            for link_tag in link_tags:
                if id_ in link_tag:
                    self._delete_link(link_tag, link_tag.replace(".gui.link", ""), self.gui_state["active_clip"])

    #### Callbacks ####
    
    def add_link_callback(self, sender, app_data, user_data):
        action, clip = user_data[0:2]

        if action == "create":
            if app_data is not None:
                src_tag, dst_tag = app_data
                src_tag = (dpg.get_item_alias(src_tag) or src_tag).replace(".node_attribute", "")
                dst_tag = (dpg.get_item_alias(dst_tag) or dst_tag).replace(".node_attribute", "")
                src_channel_id = src_tag.split(".", 1)[-1]
                dst_channel_id = dst_tag.split(".", 1)[-1]
                src_channel = self.state.get_obj(src_channel_id)
                dst_channel = self.state.get_obj(dst_channel_id)
            else:
                src_channel, dst_channel = user_data[2:4]
            
            success = self.state.execute(f"create_link {clip.id} {src_channel.id} {dst_channel.id}")
            if not success:
                raise RuntimeError("Failed to create link")
        else: # restore
            src_channel, dst_channel = user_data[2:4]

        src_node_attribute_tag = get_node_attribute_tag(clip, src_channel)
        dst_node_attribute_tag = get_node_attribute_tag(clip, dst_channel)
        link_tag = f"{src_node_attribute_tag}:{dst_node_attribute_tag}.gui.link"
        dpg.add_node_link(src_node_attribute_tag, dst_node_attribute_tag, parent=get_node_editor_tag(clip), tag=link_tag)

    def delete_link_callback(self, sender, app_data, user_data):
        alias = dpg.get_item_alias(app_data) or app_data
        clip = user_data[1]
        self._delete_link(alias, alias.replace(".gui.link", ""), clip)

    def delete_selected_nodes_callback(self):
        node_editor_tag = get_node_editor_tag(self.gui_state["active_clip"])

        for item in dpg.get_selected_nodes(node_editor_tag):                    
            alias = dpg.get_item_alias(item)
            node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
            # Deleting outputs from the Node Editor GUI is not allowed.
            if "DmxOutput" in node_id:
                continue
            success = self.state.execute(f"delete {node_id}")
            if success:
                self._delete_node_gui(alias, node_id)
            else:
                RuntimeError(f"Failed to delete: {node_id}")

        for item in dpg.get_selected_links(node_editor_tag):
            alias = dpg.get_item_alias(item)
            link_key = alias.replace(".gui.link", "")
            self._delete_link(alias, link_key, self.gui_state["active_clip"])

    @gui_lock_callback
    def add_input_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
        if action == "create":
            clip, dtype = args
            success, input_channel = self.state.execute(f"create_input {clip.id} {dtype}")
            if not success:  
                raise RuntimeError("Failed to create input")
            success = self.state.execute(f"add_automation {input_channel.id}")
        else: # restore
            clip, input_channel = args

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y
        with dpg.node(label=input_channel.name, tag=node_tag, parent=node_editor_tag, pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0)):

            self.add_node_popup_menu(node_tag, clip, input_channel)

            parameters = getattr(input_channel, "parameters", [])
            
            # Special Min/Max Parameters
            def update_min_max_value(sender, app_data, user_data):
                clip, input_channel, parameter_index, min_max = user_data
                self.update_parameter(None, app_data, (input_channel, parameter_index))

                value = model.cast[input_channel.dtype](app_data)
                kwarg = {f"{min_max}_value": value}
                dpg.configure_item(f"{input_channel.id}.value", **kwarg)

                plot_tag = get_plot_tag(input_channel)
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.set_axis_limits(y_axis_limits_tag, input_channel.get_parameter("min").value, input_channel.get_parameter("max").value)
                self.reset_automation_plot(input_channel)

            for parameter_index, name in enumerate(["min", "max"]):
                parameter = input_channel.get_parameter(name)
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name, 
                        tag=f"{parameter.id}.value",
                        callback=update_min_max_value, 
                        user_data=(clip, input_channel, parameter_index, name), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                        decimal=True,
                    )

            for parameter_index, parameter in enumerate(parameters):
                if parameter.name in ["min", "max"]:
                    continue
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name, 
                        tag=f"{parameter.id}.value",
                        callback=self.update_parameter, 
                        user_data=(input_channel, parameter_index), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                    )

            with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel), attribute_type=dpg.mvNode_Attr_Output):
                # Input Knob
                add_func = dpg.add_drag_float if input_channel.dtype == "float" else dpg.add_drag_int
                add_func(
                    label="out", 
                    min_value=input_channel.get_parameter("min").value,
                    max_value=input_channel.get_parameter("max").value, 
                    tag=f"{input_channel.id}.value", 
                    width=75, 
                    callback=self.update_input_channel_value, 
                    user_data=input_channel
                )

            # Create Automation Editor
            self.create_automation_window(
                clip,
                input_channel,
                action
            )

            # When user clicks on the node, bring up the automation window.
            def input_selected_callback(sender, app_data, user_data):
                # Show right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    clip, input_channel = user_data
                    self._active_input_channel = input_channel
                    for other_input_channel in clip.inputs:
                        if other_input_channel.deleted:
                            continue
                        dpg.configure_item(get_automation_window_tag(other_input_channel), show=False)
                    dpg.configure_item(get_automation_window_tag(self._active_input_channel), show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=input_selected_callback, user_data=(clip, input_channel))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

    @gui_lock_callback
    def add_function_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
        if action == "create":
            node_type = args[0]
            node_args = args[1]
            clip = args[2]
            success, node = self.state.execute(f"create_node {clip.id} {node_type} {node_args or ''}")
            if not success:
                return
            self._last_add_function_node = (sender, app_data, user_data)
        else: # restore
            clip, node = args

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        parent = get_node_editor_tag(clip)
        parameters = node.parameters
        input_channels = node.inputs
        output_channels = node.outputs

        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y

        node_tag = get_node_tag(clip, node)
        with dpg.node(parent=get_node_editor_tag(clip), tag=node_tag, label=node.name, pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0)):

            self.add_node_popup_menu(node_tag, clip, node)

            for parameter_index, parameter in enumerate(parameters):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    if parameter.dtype == "bool":
                        dpg.add_checkbox(
                            label=parameter.name, 
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter, 
                            user_data=(node, parameter_index), 
                            default_value=parameter.value
                        )
                    else:
                        dpg.add_input_text(
                            label=parameter.name, 
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter, 
                            user_data=(node, parameter_index), 
                            width=70,
                            default_value=parameter.value if parameter.value is not None else "",
                            on_enter=True,
                        )


            for input_index, input_channel in enumerate(input_channels):
                with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel)):
                    kwargs = {}
                    if input_channel.dtype == "any":
                        add_func = dpg.add_input_text
                    elif input_channel.size == 1:
                        add_func = dpg.add_input_float if input_channel.dtype == "float" else dpg.add_input_int
                    else:
                        add_func = dpg.add_drag_floatx 
                        kwargs["size"] = input_channel.size

                    add_func(
                        label=input_channel.name, 
                        tag=f"{input_channel.id}.value", 
                        width=90, 
                        on_enter=True,
                        default_value=input_channel.get(),
                        callback=self.update_input_channel_value_callback,
                        user_data=input_channel,
                        **kwargs
                    )

            for output_index, output_channel in enumerate(output_channels):
                with dpg.node_attribute(tag=get_node_attribute_tag(clip, output_channel), attribute_type=dpg.mvNode_Attr_Output):
                    if output_channel.dtype == "any":
                        dpg.add_input_text(tag=f"{output_channel.id}.value", readonly=True, width=100)
                    elif output_channel.size == 1:
                        add_func = dpg.add_input_float if output_channel.dtype == "float" else dpg.add_input_int
                        add_func(label=output_channel.name, tag=f"{output_channel.id}.value", width=90, step=0, readonly=True)
                    else:
                        add_func = dpg.add_drag_floatx 
                        add_func(label=output_channel.name, tag=f"{output_channel.id}.value", width=90, size=output_channel.size)



            def node_selcted_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=node_selcted_callback, user_data=(clip, node))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

        self.create_properties_window(clip, node)

    @gui_lock_callback
    def add_custom_function_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "create":
            node_type = args[0]
            node_args = args[1]
            clip = args[2]
            success, node = self.state.execute(f"create_node {clip.id} {node_type} {node_args}")
            if not success:
                return
        else: # restore
            clip, node = args

        dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        parent = get_node_editor_tag(clip)
        parameters = node.parameters
        input_channels = node.inputs
        output_channels = node.outputs

        node_tag = get_node_tag(clip, node)
        with dpg.node(parent=get_node_editor_tag(clip), tag=node_tag, label=node.name):

            self.add_node_popup_menu(node_tag, clip, node)

            # Parameter 0 = n_inputs
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    label=node.parameters[0].name, 
                    tag=f"{node.parameters[0].id}.value",
                    callback=self.update_custom_node_attributes, 
                    user_data=(clip, node, 0), 
                    width=70,
                    default_value=node.parameters[0].value if node.parameters[0].value is not None else "0",
                    on_enter=True,
                    decimal=True,
                )

            # Parameter 1 = n_outputs
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    label=node.parameters[1].name, 
                    tag=f"{node.parameters[1].id}.value",
                    callback=self.update_custom_node_attributes, 
                    user_data=(clip, node, 1), 
                    width=70,
                    default_value=node.parameters[1].value if node.parameters[1].value is not None else "0",
                    on_enter=True,
                    decimal=True,
                )

            for input_index, input_channel in enumerate(input_channels):
                self.add_custom_node_input_attribute(clip, node, input_channel)

            for output_index, output_channel in enumerate(output_channels):
                self.add_custom_node_output_attribute(clip, node, output_channel)

            def node_selcted_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=node_selcted_callback, user_data=(clip, node))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

        self.create_custom_node_properties_window(clip, node)

    def add_custom_node_input_attribute(self, clip, node, channel):
        with dpg.node_attribute(parent=get_node_tag(clip, node), tag=get_node_attribute_tag(clip, channel)):
            dpg.add_input_text(
                label=channel.name, 
                tag=f"{channel.id}.value", 
                width=90, 
                on_enter=True,
                default_value=channel.get(),
                callback=self.update_input_channel_value_callback,
                user_data=channel
            )

    def add_custom_node_output_attribute(self, clip, node, channel):
        with dpg.node_attribute(parent=get_node_tag(clip, node), tag=get_node_attribute_tag(clip, channel), attribute_type=dpg.mvNode_Attr_Output):
            dpg.add_input_text(label=channel.name, tag=f"{channel.id}.value", width=90)

    def update_custom_node_attributes(self, sender, app_data, user_data):
        with self.gui_state["lock"]:
            n = int(app_data)
            clip, node, parameter_index = user_data
            success, results = self.state.execute(f"update_parameter {node.id} {parameter_index} {n}")
            if success:
                delta, channels = results
                for channel in channels:
                    if delta > 0:
                        if parameter_index == 0:
                            self.add_custom_node_input_attribute(clip, node, channel)
                        else:
                            self.add_custom_node_output_attribute(clip, node, channel)
                    elif delta < 0:
                        self.delete_associated_links([channel])
                        dpg.delete_item(get_node_attribute_tag(clip, channel))

    def update_input_channel_value_callback(self, sender, app_data, user_data):
        # If an input isn't connected to a node, the user can set it 
        if app_data is not None:
            input_channel = user_data
            success = self.state.execute(f"update_channel_value {input_channel.id} {app_data}")
            if not success:
                raise RuntimeError(f"Failed to update channel value {input_channel.id}")

    def load_custom_node_callback(self, sender, app_data, user_data):
        clip = user_data
        dpg.configure_item("open_custom_node_dialog", show=True)

    def add_output_node(self, clip, output_channel):
        # This is the id used when adding links.
        attr_tag = get_node_attribute_tag(clip, output_channel)

        if dpg.does_item_exist(attr_tag):
            return

        node_tag = get_node_tag(clip, output_channel)
        with dpg.node(label="Output", tag=node_tag, parent=get_node_editor_tag(clip)):

            self.add_node_popup_menu(node_tag, clip, output_channel)

            with dpg.node_attribute(tag=attr_tag):
                dpg.add_input_int(label="In", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="Ch.", source=f"{output_channel.id}.dmx_address", width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text(source=f"{output_channel.id}.name", default_value=output_channel.name)

            # When user clicks on the output node it will populate the inspector.
            def output_selected_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    self._active_output_channel = user_data

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=output_selected_callback, user_data=output_channel)
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)


    def add_output_group_node(self, clip, output_channel_group):
        # This is the id used when adding links.
        node_tag = get_node_tag(clip, output_channel_group)
        if dpg.does_item_exist(node_tag):
            return

        with dpg.node(label=output_channel_group.name, tag=node_tag, parent=get_node_editor_tag(clip)):

            self.add_node_popup_menu(node_tag, clip, output_channel_group)

            for i, output_channel in enumerate(output_channel_group.outputs):
                attr_tag = get_node_attribute_tag(clip, output_channel)
                with dpg.node_attribute(tag=attr_tag):
                    dpg.add_input_int(label=output_channel.name.split(".")[-1] + f" [{output_channel.dmx_address}]", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

                # When user clicks on the output node it will populate the inspector.
                def output_selected_callback(sender, app_data, user_data):
                    # Right click menu
                    if app_data[0] == 1:
                        dpg.configure_item(f"{node_tag}.popup", show=True)
                    else:
                        self._active_output_channel_group = user_data

                #handler_registry_tag = f"{node_tag}.item_handler_registry"
                #with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                #    dpg.add_item_clicked_handler(callback=output_selected_callback, user_data=output_channel)
                #dpg.bind_item_handler_registry(attr_tag, handler_registry_tag)