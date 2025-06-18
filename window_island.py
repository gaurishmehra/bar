import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import socket
import json
import threading
import logging

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

class WindowIsland(Gtk.Box):
    def __init__(self, workspace_island_ref=None):  # Add workspace_island_ref parameter
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("island")
        self.add_css_class("window-island")
        
        # Create window title label
        self.window_label = Gtk.Label()
        self.window_label.set_markup('<span font="10" weight="normal">Connecting...</span>')
        self.window_label.add_css_class("window-label")
        self.window_label.set_ellipsize(3)  # Ellipsize at end
        
        self.append(self.window_label)
        
        # Service connection
        self.socket_path = "/tmp/window_service.sock"
        self.service_socket = None
        self.current_title = "No active window"
        self.current_workspaces_data = []  # Added to store workspace data
        self.current_active_workspace_id = None  # Added to store active workspace ID
        self.workspace_island_ref = workspace_island_ref  # Store the reference
        
        # Start service connection
        self.connect_to_service()
    
    def connect_to_service(self):
        """Connect to the window service"""
        def connect():
            try:
                self.service_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.service_socket.connect(self.socket_path)
                
                # Start listening for updates in a separate thread
                listen_thread = threading.Thread(target=self.listen_for_updates, daemon=True)
                listen_thread.start()
                
                logger.info("Connected to window service")
                
            except Exception as e:
                logger.warning(f"Failed to connect to window service: {e}")
                self.service_socket = None
                # Try to start the service
                self.start_service()
        
        # Run connection in thread to avoid blocking UI
        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()
    
    def start_service(self):
        """Try to start the window service"""
        try:
            import subprocess
            import os
            
            # Get the directory where window_island.py is located
            script_dir = os.path.dirname(os.path.abspath(__file__))
            service_path = os.path.join(script_dir, "window_service.py")
            
            # Make sure the service file is executable
            os.chmod(service_path, 0o755)
            
            subprocess.Popen([
                'python3', service_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Wait a bit and try to connect again
            GLib.timeout_add_seconds(3, self.retry_connection)
            
        except Exception as e:
            logger.error(f"Failed to start window service: {e}")
    
    def retry_connection(self):
        """Retry connection to service"""
        if self.service_socket is None:
            self.connect_to_service()
        return False  # Don't repeat this timeout
    
    def listen_for_updates(self):
        """Listen for window updates from the service"""
        if not self.service_socket:
            return
            
        try:
            buffer = ""
            while True:
                data = self.service_socket.recv(1024).decode()  # Consider larger buffer if needed
                if not data:
                    logger.warning("Window/Workspace service disconnected (no data).")
                    break
                    
                buffer += data
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            display_state_info = json.loads(line)
                            # Update UI in main thread
                            GLib.idle_add(self.update_display, display_state_info)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode JSON from window/workspace service: {e} - Data: '{line}'")
                            
        except socket.error as e:
            logger.warning(f"Window/Workspace service connection lost: {e}")
        except Exception as e:
            logger.error(f"Error listening to window/workspace service: {e}")
        finally:
            self.service_socket = None
            # Try to reconnect after a delay
            GLib.timeout_add_seconds(3, self.retry_connection)
    
    def update_display(self, display_state_info):
        """Update the window title and workspace display with info from service"""
        # Update window title
        window_info = display_state_info.get("active_window", {})
        title = window_info.get("title", "Hyprland, ArchLinux")
        
        # Clean up the title
        if len(title) > 70:
            title = title[:67] + "..."
        
        if self.current_title != title:
            self.window_label.set_markup(f'<span font="10" weight="normal">{title}</span>')
            self.current_title = title

        # Update workspaces
        self.current_workspaces_data = display_state_info.get("workspaces", [])
        self.current_active_workspace_id = display_state_info.get("active_workspace_id")

        # If LayerTopBar is directly listening or WindowIsland has a direct reference to WorkspaceIsland:
        if hasattr(self, 'workspace_island_ref') and self.workspace_island_ref:
            self.workspace_island_ref.update_workspaces(self.current_workspaces_data, self.current_active_workspace_id)

        return False  # Don't repeat this idle call