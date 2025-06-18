import gi
gi.require_version('Gtk', '4.0')
gi.require_version('AstalTray', '0.1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gdk, GLib, Gio, GObject, AstalTray

class SystrayIsland(Gtk.Box):
    """
    A GTK widget that displays system tray icons, correctly handling both
    Ayatana DBusMenu (via AstalTray's menu-model) and standard SNI methods.
    """
    def __init__(self):
        # We reduce the spacing here to make icons closer together
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2) 
        self.add_css_class("island")
        # Add a specific class for targeted CSS styling
        self.add_css_class("systray-island")
        
        self.item_widgets = {}

        try:
            self.tray = AstalTray.Tray.get_default()
            self.tray.connect("item-added", self._on_item_added)
            self.tray.connect("item-removed", self._on_item_removed)
                
        except GLib.Error as e:
            print(f"Warning: Could not initialize AstalTray. {e}")
            error_label = Gtk.Label(label="Systray not available")
            self.append(error_label)

    def _on_item_added(self, tray, object_path):
        item = tray.get_item(object_path)
        if not item or object_path in self.item_widgets:
            return
        widget = self._create_widget_for_item(item, object_path)
        self.item_widgets[object_path] = widget
        self.append(widget)

    def _on_item_removed(self, tray, object_path):
        if object_path in self.item_widgets:
            widget_to_remove = self.item_widgets.pop(object_path)
            self.remove(widget_to_remove)

    def _create_widget_for_item(self, item, object_path):
        """
        Creates a Gtk.Button for the tray item and configures it to handle
        all click events and menus correctly.
        """
        button = Gtk.Button(has_frame=False)
        # Add a class to the button for styling
        button.add_css_class("systray-button")
        
        # REMOVED pixel_size=22. Size will now be controlled by CSS.
        icon = Gtk.Image()
        button.set_child(icon)
        
        item.bind_property("gicon", icon, "gicon", GObject.BindingFlags.SYNC_CREATE)
        item.bind_property("tooltip-markup", button, "tooltip-markup", GObject.BindingFlags.SYNC_CREATE)
        
        button.tray_item = item
        
        service_name, clean_object_path = None, None
        if object_path and object_path.startswith(':'):
            parts = object_path.split('/', 1)
            if len(parts) >= 2:
                service_name = parts[0]
                clean_object_path = '/' + parts[1]
        button.dbus_service_name = service_name
        button.dbus_clean_object_path = clean_object_path
        
        button.menu_model = item.get_property("menu-model")
        action_group = item.get_action_group()
        if action_group:
            button.insert_action_group("dbusmenu", action_group)
            
        def on_menu_model_changed(*args):
            button.menu_model = item.get_property("menu-model")
        item.connect("notify::menu-model", on_menu_model_changed)

        def on_action_group_changed(*args):
            new_group = item.get_action_group()
            if new_group:
                button.insert_action_group("dbusmenu", new_group)
        item.connect("notify::action-group", on_action_group_changed)

        button.connect("clicked", self._on_left_click)
        
        gesture = Gtk.GestureClick.new()
        gesture.set_button(Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_right_click)
        button.add_controller(gesture)
        
        return button

    def _on_left_click(self, button):
        print(f"Left-click on {button.dbus_service_name}{button.dbus_clean_object_path}")
        button.tray_item.activate(0, 0)

    def _on_right_click(self, gesture, n_press, x, y):
        button = gesture.get_widget()
        print(f"Right-click on {button.dbus_service_name}{button.dbus_clean_object_path}")

        if button.menu_model:
            print("Found menu-model. Creating Gtk.PopoverMenu.")
            popover = Gtk.PopoverMenu.new_from_model(button.menu_model)
            popover.set_parent(button)
            popover.popup()
            return

        print("No menu-model found. Falling back to standard SNI ContextMenu method.")
        self._try_standard_context_menu(button, x, y)
        
    def _try_standard_context_menu(self, button, x, y):
        service_name = button.dbus_service_name
        object_path = button.dbus_clean_object_path
        if not service_name or not object_path:
            return

        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                bus_type=Gio.BusType.SESSION,
                flags=Gio.DBusProxyFlags.NONE,
                info=None,
                name=service_name,
                object_path=object_path,
                interface_name='org.kde.StatusNotifierItem',
                cancellable=None)

            try:
                surface = button.get_native().get_surface()
                origin_x, origin_y = surface.get_surface_transform().transform_point(0, 0)
                final_x, final_y = int(origin_x + x), int(origin_y + y)
            except:
                final_x, final_y = int(x), int(y)

            try:
                print(f"Trying ContextMenu with coords ({final_x}, {final_y})")
                proxy.call_sync('ContextMenu', GLib.Variant('(ii)', (final_x, final_y)),
                                Gio.DBusCallFlags.NONE, 500, None)
                print("ContextMenu call succeeded.")
                return
            except GLib.Error as e:
                print(f"ContextMenu failed: {e}. Trying SecondaryActivate...")

            try:
                proxy.call_sync('SecondaryActivate', GLib.Variant('(ii)', (final_x, final_y)),
                                Gio.DBusCallFlags.NONE, 500, None)
                print("SecondaryActivate call succeeded.")
                return
            except GLib.Error as e:
                print(f"SecondaryActivate also failed: {e}")

        except Exception as e:
            print(f"Failed to create D-Bus proxy for standard context menu: {e}")