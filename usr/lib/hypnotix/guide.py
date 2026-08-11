import time
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GdkPixbuf
import gettext
_ = gettext.gettext

PIXELS_PER_MINUTE = 4

class EPGTimeline(Gtk.DrawingArea):
    def __init__(self, guide_widget):
        super().__init__()
        self.guide = guide_widget
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_leave)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self.on_query_tooltip)

        self.hovered_prog = None

    def get_program_at_pos(self, x, y):
        if not self.guide.channels or not self.guide.epg_manager:
            return None

        # Find which row we are in
        current_y = 0
        target_row = -1
        target_channel = None
        target_row_height = 0

        for i, (channel, widget) in enumerate(zip(self.guide.channels, self.guide.channel_widgets)):
            min_height, row_height = widget.get_preferred_height()
            if current_y <= y < current_y + row_height:
                target_row = i
                target_channel = channel
                target_row_height = row_height
                break
            current_y += row_height

        if target_row == -1 or not target_channel:
            return None

        programmes = self.guide.epg_manager.programmes.get(target_channel.xmltv_id, []) if getattr(target_channel, "xmltv_id", None) else []

        for prog in programmes:
            if prog["stop"] > self.guide.start_window and prog["start"] < self.guide.end_window:
                prog_start = max(prog["start"], self.guide.start_window)
                prog_stop = min(prog["stop"], self.guide.end_window)

                prog_x = int((prog_start - self.guide.start_window) / 60 * PIXELS_PER_MINUTE)
                prog_w = int((prog_stop - prog_start) / 60 * PIXELS_PER_MINUTE)

                if prog_x <= x < prog_x + prog_w:
                    return prog
        return None

    def on_motion(self, widget, event):
        prog = self.get_program_at_pos(event.x, event.y)
        if prog != self.hovered_prog:
            self.hovered_prog = prog
            self.queue_draw() # Trigger redraw to show hover state

    def on_leave(self, widget, event):
        if self.hovered_prog:
            self.hovered_prog = None
            self.queue_draw()

    def on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        prog = self.get_program_at_pos(x, y)
        if prog:
            text = prog.get("desc", "") or prog.get("title", "")
            tooltip.set_text(text)
            return True
        return False

    def on_draw(self, widget, cr):
        if not self.guide.channels:
            return

        # Get theme colors dynamically
        context = self.get_style_context()

        # In modern GTK3 themes get_background_color often returns transparent
        # so we lookup the named theme colors directly.
        found, theme_base = context.lookup_color("theme_base_color")
        if not found:
            found, theme_base = context.lookup_color("theme_bg_color")
            if not found: theme_base = Gdk.RGBA(0.2, 0.2, 0.2, 1.0)

        found, theme_fg = context.lookup_color("theme_fg_color")
        if not found: theme_fg = Gdk.RGBA(0.9, 0.9, 0.9, 1.0)

        found, theme_sel_bg = context.lookup_color("theme_selected_bg_color")
        if not found: theme_sel_bg = Gdk.RGBA(0.2, 0.4, 0.8, 1.0)

        found, theme_sel_fg = context.lookup_color("theme_selected_fg_color")
        if not found: theme_sel_fg = Gdk.RGBA(1.0, 1.0, 1.0, 1.0)

        found, theme_borders = context.lookup_color("borders")
        if not found: theme_borders = Gdk.RGBA(0.3, 0.3, 0.3, 1.0)

        # We need an alpha blended version of sel_bg_color for 'now playing'
        now_playing_bg = Gdk.RGBA(theme_sel_bg.red, theme_sel_bg.green, theme_sel_bg.blue, 0.15)
        hover_bg = Gdk.RGBA(theme_sel_bg.red, theme_sel_bg.green, theme_sel_bg.blue, 0.8)

        clip_extents = cr.clip_extents()
        clip_y1, clip_y2 = clip_extents[1], clip_extents[3]
        clip_x1, clip_x2 = clip_extents[0], clip_extents[2]

        current_time = int(time.time())

        layout = PangoCairo.create_layout(cr)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        font_desc = context.get_font(Gtk.StateFlags.NORMAL)
        layout.set_font_description(font_desc)

        y_pos = 0
        for channel, cwidget in zip(self.guide.channels, self.guide.channel_widgets):
            min_height, row_height = cwidget.get_preferred_height()

            # Only draw rows that intersect with the clip region (visible screen)
            if y_pos + row_height >= clip_y1 and y_pos <= clip_y2:
                programmes = self.guide.epg_manager.programmes.get(channel.xmltv_id, []) if self.guide.epg_manager and getattr(channel, "xmltv_id", None) else []

                for prog in programmes:
                    if prog["stop"] > self.guide.start_window and prog["start"] < self.guide.end_window:
                        prog_start = max(prog["start"], self.guide.start_window)
                        prog_stop = min(prog["stop"], self.guide.end_window)

                        x_pos = int((prog_start - self.guide.start_window) / 60 * PIXELS_PER_MINUTE)
                        width = int((prog_stop - prog_start) / 60 * PIXELS_PER_MINUTE)

                        # Only draw blocks that intersect with the clip region horizontally
                        if x_pos + width >= clip_x1 and x_pos <= clip_x2:
                            is_hovered = (prog == self.hovered_prog)
                            is_now_playing = (prog["start"] <= current_time < prog["stop"])

                            # Draw Background
                            cr.rectangle(x_pos, y_pos, width, row_height)

                            if is_hovered:
                                # Draw base first, then hover on top
                                cr.set_source_rgba(theme_base.red, theme_base.green, theme_base.blue, theme_base.alpha)
                                cr.fill()
                                cr.rectangle(x_pos, y_pos, width, row_height)
                                cr.set_source_rgba(hover_bg.red, hover_bg.green, hover_bg.blue, hover_bg.alpha)
                                cr.fill()
                            elif is_now_playing:
                                # Draw normal background first
                                cr.set_source_rgba(theme_base.red, theme_base.green, theme_base.blue, theme_base.alpha)
                                cr.fill()
                                # Then overlay the transparent highlight
                                cr.rectangle(x_pos, y_pos, width, row_height)
                                cr.set_source_rgba(now_playing_bg.red, now_playing_bg.green, now_playing_bg.blue, now_playing_bg.alpha)
                                cr.fill()
                            else:
                                cr.set_source_rgba(theme_base.red, theme_base.green, theme_base.blue, theme_base.alpha)
                                cr.fill()

                            # Draw Border
                            cr.rectangle(x_pos, y_pos, width, row_height)
                            cr.set_source_rgba(theme_borders.red, theme_borders.green, theme_borders.blue, theme_borders.alpha)
                            cr.set_line_width(1)
                            cr.stroke()

                            # Draw Text
                            if width > 12: # Only draw text if there's reasonable space
                                layout.set_text(prog.get("title", ""))
                                layout.set_width((width - 8) * Pango.SCALE) # 4px padding each side

                                if is_hovered:
                                    cr.set_source_rgba(theme_sel_fg.red, theme_sel_fg.green, theme_sel_fg.blue, theme_sel_fg.alpha)
                                else:
                                    cr.set_source_rgba(theme_fg.red, theme_fg.green, theme_fg.blue, theme_fg.alpha)

                                # Center text vertically
                                ink_rect, logical_rect = layout.get_pixel_extents()
                                text_y = y_pos + (row_height - logical_rect.height) / 2

                                cr.move_to(x_pos + 4, text_y)
                                PangoCairo.show_layout(cr, layout)

            y_pos += row_height

        # Draw Time Indicator (Red line)
        if current_time >= self.guide.start_window and current_time <= self.guide.end_window:
            time_x = int((current_time - self.guide.start_window) / 60 * PIXELS_PER_MINUTE)
            if time_x >= clip_x1 and time_x <= clip_x2:
                cr.set_source_rgba(1.0, 0.0, 0.0, 1.0)
                cr.move_to(time_x, clip_y1)
                cr.line_to(time_x, clip_y2)
                cr.set_line_width(2)
                cr.stroke()


class EPGGuideWidget(Gtk.Box):
    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.main_window = main_window
        self.start_window = 0
        self.end_window = 0
        self.channels = []
        self.channel_widgets = []
        self.epg_manager = None

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

        # Use our custom high-performance drawing area
        self.timeline_area = EPGTimeline(self)
        self.timeline_scroll.add(self.timeline_area)

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

        # Inject the date label natively into the UI's sidebar wrapper
        self.main_window.sidebar_vbox.pack_start(self.date_label, False, False, 0)
        self.main_window.sidebar_vbox.reorder_child(self.date_label, 0)

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
        self.channels = channels
        self.channel_widgets = self.main_window.channels_listbox.get_children()
        self.epg_manager = epg_manager

        for child in self.time_header_layout.get_children():
            self.time_header_layout.remove(child)

        current_time = int(time.time())
        self.start_window = current_time - 3600
        self.end_window = current_time + (12 * 3600)

        total_width = int(((self.end_window - self.start_window) / 60) * PIXELS_PER_MINUTE)

        # Render Time Header (we keep this as Layout with Labels since it's just 24 labels)
        start_bound = (self.start_window // 1800) * 1800
        if start_bound < self.start_window:
            start_bound += 1800

        for t in range(start_bound, self.end_window, 1800):
            x_pos = int(((t - self.start_window) / 60) * PIXELS_PER_MINUTE)
            time_str = time.strftime("%H:%M", time.localtime(t))
            lbl = Gtk.Label(label=f"<b>{time_str}</b>")
            lbl.set_use_markup(True)
            self.time_header_layout.put(lbl, x_pos, 5)

        self.time_header_layout.set_size(total_width, 30)

        total_height = 0
        for widget in self.channel_widgets:
            min_height, row_height = widget.get_preferred_height()
            total_height += row_height

        self.timeline_area.set_size_request(total_width, total_height)
        self.timeline_area.queue_draw()

        # Trigger an initial scroll update to populate the Date label
        self.on_scroll_changed(self.timeline_scroll.get_hadjustment())
        self.show_all()
        self.on_visibility_changed(None, None)
