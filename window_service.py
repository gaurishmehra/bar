#!/usr/bin/env python3
"""
Window Service - A lightweight background service to monitor active window changes
Uses native libraries and IPC for efficient communication with the bar
"""

import os
import sys
import time
import json
import socket
import threading
from pathlib import Path
import signal
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    # Try to import Wayland/X11 libraries
    HAS_SUBPROCESS = True  # This seems to be a misnomer, was not used for subprocess before.
except ImportError:
    HAS_SUBPROCESS = False  # Keeping for consistency, though not directly used for subprocess.

class WindowService:
    def __init__(self):
        self.socket_path = "/tmp/window_service.sock"
        # Updated data structure
        self.current_display_state_data = {
            "active_window": {"title": "Hyprland, ArchLinux", "class": "", "pid": 0},
            "workspaces": [],
            "active_workspace_id": None
        }
        self.clients = []
        self.running = True
        
        # Remove existing socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
            
        # Create socket server
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        logger.info(f"Window and Workspace service started, socket: {self.socket_path}")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Shutting down window and workspace service...")
        self.running = False
        self.cleanup()
        sys.exit(0)
    
    def cleanup(self):
        """Clean up resources"""
        try:
            self.server_socket.close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
    
    def _fetch_hyprland_data(self, ipc_command):
        """Helper function to send a command to Hyprland IPC and get JSON response."""
        instance_signature = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
        if not instance_signature:
            return None

        potential_paths = []
        xdg_runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
        if xdg_runtime_dir:
            potential_paths.append(
                Path(xdg_runtime_dir) / "hypr" / instance_signature / ".socket.sock"
            )
        potential_paths.append(
            Path("/tmp/hypr") / instance_signature / ".socket.sock"
        )

        for socket_path_obj in potential_paths:
            socket_path_str = str(socket_path_obj)
            if not socket_path_obj.exists() or not socket_path_obj.is_socket():
                logger.debug(f"Socket not found or not a socket file at {socket_path_str}. Trying next path.")
                continue

            hypr_socket = None
            try:
                hypr_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                hypr_socket.connect(socket_path_str)
                logger.debug(f"Successfully connected to Hyprland IPC socket: {socket_path_str} for command {ipc_command}")
                
                hypr_socket.sendall(ipc_command.encode('utf-8'))
                
                response_data = b""
                hypr_socket.settimeout(0.5) 
                try:
                    while True:
                        chunk = hypr_socket.recv(8192)
                        if not chunk:
                            break
                        response_data += chunk
                        if len(response_data) > 131072:
                            logger.error(f"Hyprland IPC response too large for command {ipc_command}.")
                            return None
                except socket.timeout:
                    logger.debug(f"Timeout receiving data from Hyprland IPC socket: {socket_path_str} for {ipc_command}. Data so far: {len(response_data)} bytes")
                
                if response_data:
                    try:
                        decoded_data = response_data.decode('utf-8')
                        return json.loads(decoded_data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode JSON from Hyprland IPC for {ipc_command}: {e}. Response: {response_data[:200]}")
                        return None
                else:
                    logger.warning(f"No data received from Hyprland IPC for {ipc_command} at {socket_path_str}")
                    return None
                
            except (socket.error, FileNotFoundError) as e:
                logger.error(f"Error with Hyprland IPC socket {socket_path_str} for {ipc_command}: {e}")
            finally:
                if hypr_socket:
                    try:
                        hypr_socket.close()
                    except socket.error as e:
                        logger.error(f"Error closing Hyprland IPC socket {socket_path_str}: {e}")
            
        logger.error(f"Failed to get Hyprland data for {ipc_command} after trying all potential IPC socket paths.")
        return None

    def get_hyprland_active_window_props(self):
        """Get active window properties from Hyprland."""
        data = self._fetch_hyprland_data("j/activewindow")
        if data and isinstance(data, dict):
            return {
                "title": data.get("title", "Untitled"),
                "class": data.get("class", ""),
                "pid": data.get("pid", 0)
            }
        return {"title": "Hyprland, ArchLinux", "class": "", "pid": 0}

    def get_hyprland_all_workspaces_list(self):
        """Get list of all non-special workspaces from Hyprland, sorted by ID."""
        data = self._fetch_hyprland_data("j/workspaces")
        if data and isinstance(data, list):
            # Filter out special workspaces (ID < 0) and sort by ID
            filtered_workspaces = []
            for ws in data:
                ws_id = ws.get("id", 0)
                if ws_id >= 0:  # Only include non-special workspaces
                    filtered_workspaces.append({
                        "id": ws_id,
                        "name": ws.get("name"),
                        "windows": ws.get("windows", 0)
                        # Add other fields if needed, e.g., "monitor"
                    })
            
            # Sort by ID to ensure ascending order
            filtered_workspaces.sort(key=lambda ws: ws["id"])
            logger.debug(f"Workspaces after sorting: {[ws['id'] for ws in filtered_workspaces]}")
            return filtered_workspaces
        return []

    def get_hyprland_active_workspace_id_only(self):
        """Get the ID of the active workspace from Hyprland."""
        data = self._fetch_hyprland_data("j/activeworkspace")
        if data and isinstance(data, dict):
            return data.get("id")
        return None

    def get_current_display_state_from_hyprland(self):
        """Gets combined window and workspace info from Hyprland."""
        active_window_props = self.get_hyprland_active_window_props()
        all_workspaces_list = self.get_hyprland_all_workspaces_list()
        active_ws_id = self.get_hyprland_active_workspace_id_only()
        
        # Already sorted in get_hyprland_all_workspaces_list()
        return {
            "active_window": active_window_props,
            "workspaces": all_workspaces_list,
            "active_workspace_id": active_ws_id
        }

    def get_x11_window(self):
        """Placeholder for X11 window fetching, returns basic structure."""
        logger.info("X11 window fetching not fully implemented, returning default.")
        return {"title": "Desktop (X11)", "class": "", "pid": 0}

    def get_current_display_state(self):
        """Get current display state (window and workspaces)."""
        try:
            if self.check_hyprland():
                return self.get_current_display_state_from_hyprland()
            elif self.check_x11():
                x11_window_info = self.get_x11_window()
                return {
                    "active_window": x11_window_info,
                    "workspaces": [],
                    "active_workspace_id": None
                }
            return {
                "active_window": {"title": "Hyprland, ArchLinux", "class": "", "pid": 0},
                "workspaces": [],
                "active_workspace_id": None
            }
        except Exception as e:
            logger.error(f"Error getting display state: {e}")
            return {
                "active_window": {"title": "Hyprland, ArchLinux", "class": "", "pid": 0},
                "workspaces": [],
                "active_workspace_id": None
            }

    def check_hyprland(self):
        """Check if Hyprland is running by checking for its instance signature."""
        return os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') is not None
    
    def check_x11(self):
        """Check if X11 is available"""
        return os.environ.get('DISPLAY') is not None

    def monitor_display_state(self):
        """Monitor window and workspace changes."""
        while self.running:
            try:
                new_state_data = self.get_current_display_state()
                
                if new_state_data != self.current_display_state_data:
                    self.current_display_state_data = new_state_data
                    self.notify_clients()
                    logger.debug(f"Display state changed: Win: {new_state_data['active_window']['title']}, WS_ID: {new_state_data['active_workspace_id']}")
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error in display state monitoring: {e}")
                time.sleep(1)
    
    def notify_clients(self):
        """Notify all connected clients of display state changes."""
        message = json.dumps(self.current_display_state_data) + "\n"
        disconnected_clients = []
        
        for client in self.clients:
            try:
                client.send(message.encode())
            except Exception:
                disconnected_clients.append(client)
        
        for client in disconnected_clients:
            self.clients.remove(client)
            try:
                client.close()
            except Exception:
                pass
    
    def handle_client(self, client_socket):
        """Handle a client connection."""
        try:
            message = json.dumps(self.current_display_state_data) + "\n"
            client_socket.send(message.encode())
            
            while self.running:
                time.sleep(1)
                
        except Exception as e:
            logger.debug(f"Client disconnected: {e}")
        finally:
            if client_socket in self.clients:
                self.clients.remove(client_socket)
            try:
                client_socket.close()
            except Exception:
                pass
    
    def accept_clients(self):
        """Accept client connections in a separate thread"""
        while self.running:
            try:
                client_socket, _ = self.server_socket.accept()
                self.clients.append(client_socket)
                
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket,),
                    daemon=True
                )
                client_thread.start()
                
            except Exception as e:
                if self.running:
                    logger.error(f"Error accepting client: {e}")
    
    def run(self):
        """Run the window and workspace service."""
        try:
            monitor_thread = threading.Thread(target=self.monitor_display_state, daemon=True)
            monitor_thread.start()
            
            self.accept_clients()
            
        except KeyboardInterrupt:
            logger.info("Service interrupted by user")
        except Exception as e:
            logger.error(f"Service error: {e}")
        finally:
            self.cleanup()

def main():
    """Main function"""
    socket_file = "/tmp/window_service.sock"
    if os.path.exists(socket_file):
        try:
            test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_socket.connect(socket_file)
            test_socket.close()
            print("Window and Workspace service is already running.")
            sys.exit(0)
        except Exception:
            logger.warning(f"Removing stale socket file: {socket_file}")
            os.unlink(socket_file)
    
    service = WindowService()
    service.run()

if __name__ == "__main__":
    main()