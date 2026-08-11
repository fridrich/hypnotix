import time
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GdkPixbuf
import gettext
_ = gettext.gettext

PIXELS_PER_MINUTE = 4

class EPGGuideWidget(Gtk.Box):
    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.main_window = main_window
        self.start_window = 0

        # Setup custom CSS
        provider = Gtk.CssProvider()
        css = b"""
        .epg-program-block {
            background-color: @theme_bg_color;
            border: 1px solid @borders;
            border-radius: 0px;
            padding: 4px;
        }
        .epg-program-block:hover {
            background-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
        }
        .epg-now-playing {
            background-color: alpha(@theme_selected_bg_color, 0.3);
        }
        .epg-time-line {
            background-color: #ff0000;
        }
        .epg-time-header {
            font-weight: bold;
            padding-left: 4px;
        }
        """
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Time Header (Right Panel Top)
        self.time_header_scroll = Gtk.ScrolledWindow()
        self.time_header_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.time_header_scroll.set_size_request(-1, 30)
        self.time_header_layout = Gtk.Layout()
        self.time_header_scroll.add(self.time_header_layout)
        self.pack_start(self.time_header_scroll, False, False, 0)

        # Timeline Grid (Right Panel Bottom)
        self.timeline_scroll = Gtk.ScrolledWindow()
        self.timeline_scroll.set_policy(Gtk.PolicyType.ALWAYS, Gtk.PolicyType.AUTOMATIC)
        self.timeline_layout = Gtk.Layout()
        self.timeline_scroll.add(self.timeline_layout)

        # Synchronize vertical scrolling with main window's sidebar
        self.timeline_scroll.set_vadjustment(self.main_window.sidebar.get_vadjustment())

        # Synchronize horizontal scrolling of time header with timeline grid
        self.time_header_scroll.set_hadjustment(self.timeline_scroll.get_hadjustment())

        self.pack_start(self.timeline_scroll, True, True, 0)

        # Date Label (Left Panel Top)
        self.date_label = Gtk.Label()
        self.date_label.set_xalign(0.5)
        self.date_label.set_valign(Gtk.Align.CENTER)
        self.date_label.set_size_request(-1, 30)
        self.date_label.set_use_markup(True)
        # Prevent it from showing initially until we verify visibility
        self.date_label.set_no_show_all(True)

        # Inject the date label above the sidebar in the main window
        # The sidebar is inside a Gtk.Box which is inside channels_box.
        # We need to insert the date label into a new VBox that wraps the sidebar,
        # but SAFELY without destroying the layout.
        sidebar_parent = self.main_window.sidebar.get_parent()
        sidebar_parent.remove(self.main_window.sidebar)

        self.sidebar_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar_vbox.pack_start(self.date_label, False, False, 0)
        self.sidebar_vbox.pack_start(self.main_window.sidebar, True, True, 0)

        # Re-insert into the exact same spot in the horizontal box
        sidebar_parent.pack_start(self.sidebar_vbox, False, True, 0)
        sidebar_parent.reorder_child(self.sidebar_vbox, 0)
        self.sidebar_vbox.show()

        # Connect to scroll event to update the date dynamically
        hadj = self.timeline_scroll.get_hadjustment()
        hadj.connect("value-changed", self.on_scroll_changed)

        # Sync Date Label visibility with Guide Page visibility
        self.main_window.channel_stack.connect("notify::visible-child-name", self.on_visibility_changed)
        self.main_window.sidebar.connect("notify::visible", self.on_visibility_changed)

    def on_visibility_changed(self, widget, param):
        is_guide = self.main_window.channel_stack.get_visible_child_name() == "guide_page"
        if is_guide and self.main_window.sidebar.get_visible():
            self.date_label.show()
        else:
            self.date_label.hide()

    def on_scroll_changed(self, adj):
        if not self.start_window:
            return
        scroll_x = adj.get_value()
        current_visible_time = self.start_window + (scroll_x / PIXELS_PER_MINUTE) * 60
        date_str = time.strftime("%A, %d %b", time.localtime(current_visible_time))
        self.date_label.set_markup(f"<b>{date_str}</b>")

    def render_guide(self, channels, epg_manager):
        # Clear existing children
        for child in self.timeline_layout.get_children():
            self.timeline_layout.remove(child)
        for child in self.time_header_layout.get_children():
            self.time_header_layout.remove(child)

        current_time = int(time.time())
        self.start_window = current_time - 3600
        end_window = current_time + (12 * 3600)

        total_width = int(((end_window - self.start_window) / 60) * PIXELS_PER_MINUTE)

        # Render Time Header
        # Find the next 30-minute boundary (1800 seconds)
        start_bound = (self.start_window // 1800) * 1800
        if start_bound < self.start_window:
            start_bound += 1800

        for t in range(start_bound, end_window, 1800):
            x_pos = int(((t - self.start_window) / 60) * PIXELS_PER_MINUTE)
            time_str = time.strftime("%H:%M", time.localtime(t))
            lbl = Gtk.Label(label=time_str)
            lbl.get_style_context().add_class("epg-time-header")
            self.time_header_layout.put(lbl, x_pos, 5)

        self.time_header_layout.set_size(total_width, 30)

        channel_widgets = self.main_window.channels_listbox.get_children()

        y_pos = 0
        for row_index, (channel, widget) in enumerate(zip(channels, channel_widgets)):
            min_height, row_height = widget.get_preferred_height()

            # Right Panel (Programs)
            programmes = epg_manager.programmes.get(channel.xmltv_id, []) if epg_manager and getattr(channel, "xmltv_id", None) else []
            has_programmes = False

            for prog in programmes:
                if prog["stop"] > self.start_window and prog["start"] < end_window:
                    has_programmes = True
                    prog_start = max(prog["start"], self.start_window)
                    prog_stop = min(prog["stop"], end_window)

                    x_pos = int((prog_start - self.start_window) / 60 * PIXELS_PER_MINUTE)
                    width = int((prog_stop - prog_start) / 60 * PIXELS_PER_MINUTE)

                    btn = Gtk.EventBox()
                    btn.get_style_context().add_class("epg-program-block")

                    # Highlight Now Playing
                    if prog["start"] <= current_time < prog["stop"]:
                        btn.get_style_context().add_class("epg-now-playing")

                    btn.set_size_request(width, row_height)
                    tooltip_text = prog.get("desc", "") or prog.get("title", "")
                    btn.set_tooltip_text(tooltip_text)

                    prog_lbl = Gtk.Label(label=prog.get("title", ""))
                    prog_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    prog_lbl.set_xalign(0)
                    prog_lbl.set_margin_start(4)
                    prog_lbl.set_margin_end(4)
                    btn.add(prog_lbl)

                    self.timeline_layout.put(btn, x_pos, y_pos)

            y_pos += row_height

        total_height = y_pos

        # Time Indicator (Red line)
        if current_time >= self.start_window and current_time <= end_window:
            time_x = int((current_time - self.start_window) / 60 * PIXELS_PER_MINUTE)
            time_line = Gtk.EventBox()
            time_line.get_style_context().add_class("epg-time-line")
            time_line.set_size_request(2, total_height)
            self.timeline_layout.put(time_line, time_x, 0)

        self.timeline_layout.set_size(total_width, total_height)

        # Trigger an initial scroll update to populate the Date label
        self.on_scroll_changed(self.timeline_scroll.get_hadjustment())
        self.show_all()
        # Enforce visibility rules just in case
        self.on_visibility_changed(None, None)
