"""
TODO:
    Remove get_*_tag()
"""
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
	def __init__(self):
		super().__init__(tag="clip.gui.window", pos=(0,18), width=800, height=520, no_move=True, no_title_bar=True, no_resize=True)

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
        
        with self.gui_lock:
            self.create_node_editor_window(clip)

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

                active = 155 if self._active_clip_slot == (track_i, clip_i) else 0
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
        success, new_clip = self.state.execute_wrapper(f"duplicate_clip {track_i} {clip_i} {clip_id} ")
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
        if self.state.execute_wrapper(f"toggle_clip {track.id} {clip.id}"):
            self.update_clip_status()

    def paste_clip_callback(self, sender, app_data, user_data):
    	"""Called to paste a clip."""
        self.paste_clip(*user_data)
        self.update_clip_status()

    def select_clip_slot_callback(self, sender, app_data, user_data):
        track_i = int(user_data[1])
        clip_i = int(user_data[2])
        self.gui_state["active_clip"]_slot = (track_i, clip_i)
        self.gui_state["active_track"] = self.state.tracks[track_i]
        self.update_clip_status()

    def create_new_clip_callback(self, sender, app_data, user_data):
        action, track_i, clip_i = user_data
        track = self.state.tracks[int(track_i)]

        if action == "create":
            success, clip = self.state.execute_wrapper(f"new_clip {track.id},{clip_i}")
            if not success:
                raise RuntimeError("Failed to create clip")
        else: # restore
            clip = self.state.tracks[int(track_i)].clips[int(clip_i)]
        
        self.populate_clip_slot(track_i, clip_i)