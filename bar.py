import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    print("Warning: Gtk4LayerShell not available. Install gtk4-layer-shell.")
    HAS_LAYER_SHELL = False

from gi.repository import Gtk, Gdk, GLib
import os
from window_island import WindowIsland
from time_island import TimeIsland
from battery_island import BatteryIsland
from workspace_island import WorkspaceIsland
from system_stats_island import SystemStatsIsland
from systray_island import SystrayIsland  # Import the new SystrayIsland

class LayerTopBar(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        
        if not HAS_LAYER_SHELL:
            print("Error: gtk4-layer-shell is required for Wayland layer shell support")
            return
        
        # Initialize layer shell for this window
        Gtk4LayerShell.init_for_window(self)
        
        # Set layer shell properties
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
        Gtk4LayerShell.set_namespace(self, "topbar-islands")
        
        # Anchor to top, left, and right (full width top bar)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
        
        # Set exclusive zone (reserves space for the bar)
        Gtk4LayerShell.set_exclusive_zone(self, 35)
        
        # Set margins: 10px for top and bottom, 0px for left and right
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 7)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 0)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT, 0)

        
        
        # Set up CSS styling
        self.setup_css()
        
        # Create the main center box for the top bar (transparent)
        main_box = Gtk.CenterBox()
        main_box.add_css_class("topbar-transparent")
        
        # Left section with workspace island and window island
        left_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left_container.set_halign(Gtk.Align.START)
        left_container.set_hexpand(False)
        left_container.set_margin_start(5)
        
        self.workspace_island = WorkspaceIsland()
        left_container.append(self.workspace_island)
        
        self.window_island = WindowIsland(workspace_island_ref=self.workspace_island)
        left_container.append(self.window_island)
        
        # Center section with time island
        center_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        center_container.set_halign(Gtk.Align.CENTER)
        center_container.set_hexpand(False)
        
        self.time_island = TimeIsland()
        center_container.append(self.time_island)
        
        # Right section with systray, system stats, and battery islands
        right_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right_container.set_halign(Gtk.Align.END)
        right_container.set_hexpand(False)
        right_container.set_margin_end(5)
        
        self.systray_island = SystrayIsland()  # Create SystrayIsland instance
        right_container.append(self.systray_island)  # Add SystrayIsland first
        
        self.system_stats_island = SystemStatsIsland()
        right_container.append(self.system_stats_island)
        
        self.battery_island = BatteryIsland()
        right_container.append(self.battery_island)
        
        # Add all sections to main CenterBox
        main_box.set_start_widget(left_container)
        main_box.set_center_widget(center_container)
        main_box.set_end_widget(right_container)
        
        # Set the main box as the window child
        self.set_child(main_box)
    
    def setup_css(self):
        """Set up CSS styling for the top bar"""
        css_provider = Gtk.CssProvider()
        
        # Get the directory where the script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        css_file_path = os.path.join(script_dir, "style.css")
        
        try:
            # Load CSS from external file
            css_provider.load_from_path(css_file_path)
        except Exception as e:
            print(f"Error loading CSS file: {e}")
            # Fallback to basic inline CSS if file loading fails
            fallback_css = """
            .topbar-transparent {
                background-color: transparent;
                padding: 8px 0px;
            }
            .island {
                background-color: rgba(30, 30, 46, 0.9);
                border-radius: 12px;
                padding: 8px 16px;
                border: 1px solid rgba(49, 50, 68, 0.8);
            }
            window {
                background-color: transparent;
            }
            """
            css_provider.load_from_data(fallback_css.encode())
        
        # Apply CSS to the display
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, 
            css_provider, 
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

class TopBarApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.example.wayland.topbar")
        
    def do_activate(self):
        if not HAS_LAYER_SHELL:
            print("gtk4-layer-shell is required for this application")
            self.quit()
            return
            
        window = LayerTopBar(self)
        window.present()

def main():
    app = TopBarApp()
    app.run()

if __name__ == "__main__":
    main()