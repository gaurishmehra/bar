import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import socket
import json
import threading
import logging
import os
import subprocess # Import subprocess module

# Set up logging
logging.basicConfig(level=logging.INFO) # Changed to INFO for better debugging
logger = logging.getLogger(__name__)

class TimeIsland(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("island")
        self.add_css_class("time-island")

        self.time_label = Gtk.Label()
        self.time_label.set_markup('<span font="10">Loading...</span>')
        self.time_label.add_css_class("time-label")
        self.append(self.time_label)

        # --- New code for click handling ---
        # Create a click gesture recognizer
        click_gesture = Gtk.GestureClick.new()
        # Connect the "pressed" signal to our callback function
        click_gesture.connect("pressed", self._on_clicked)
        # Add the gesture controller to this widget
        self.add_controller(click_gesture)
        # --- End of new code ---

        self.socket_path = "/tmp/time_service.sock"
        self.service_socket = None

        self.connect_to_service()

    def _on_clicked(self, gesture, n_press, x, y):
        """
        Callback function executed when the widget is clicked.
        Toggles the Hyprland special workspace.
        """
        command = ['hyprctl', 'dispatch', 'togglespecialworkspace']
        try:
            # Use Popen for non-blocking execution, so it doesn't freeze the GUI
            subprocess.Popen(command)
            logger.info("hyprctl command executed to toggle special workspace.")
        except FileNotFoundError:
            logger.error("'hyprctl' command not found. Is Hyprland running and is hyprctl in your PATH?")
        except Exception as e:
            logger.error(f"Failed to execute hyprctl command: {e}")


    def connect_to_service(self):
        def connect():
            try:
                self.service_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.service_socket.connect(self.socket_path)

                listen_thread = threading.Thread(target=self.listen_for_updates, daemon=True)
                listen_thread.start()
                logger.info("Connected to time service")

            except Exception as e:
                logger.warning(f"Failed to connect to time service: {e}")
                self.service_socket = None
                self.start_service()

        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()

    def start_service(self):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            service_path = os.path.join(script_dir, "time_service.py")

            if os.path.exists(service_path):
                # No need to chmod here, should be done on install
                subprocess.Popen(
                    ['python3', service_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Attempted to start time_service.py")
                # Retry connection after a short delay to give the service time to start
                GLib.timeout_add_seconds(3, self.retry_connection)
            else:
                logger.error(f"time_service.py not found at {service_path}")

        except Exception as e:
            logger.error(f"Failed to start time service: {e}")

    def retry_connection(self):
        if self.service_socket is None:
            logger.info("Retrying connection to time service...")
            self.connect_to_service()
        return GLib.SOURCE_REMOVE # Use GLib.SOURCE_REMOVE to run only once

    def listen_for_updates(self):
        if not self.service_socket:
            return

        try:
            buffer = ""
            while True:
                # Use a larger buffer to be safe
                data = self.service_socket.recv(4096).decode('utf-8')
                if not data:
                    logger.warning("Time service disconnected (recv returned empty).")
                    break

                buffer += data
                # Process all complete JSON objects in the buffer
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            time_info = json.loads(line)
                            # Use GLib.idle_add to schedule the UI update on the main thread
                            GLib.idle_add(self.update_time_display, time_info)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode JSON from time service: {e} - Data: '{line}'")

        except (socket.error, ConnectionResetError) as e:
            logger.warning(f"Time service socket connection lost: {e}")
        except Exception as e:
            logger.error(f"Error listening to time service: {e}", exc_info=True)
        finally:
            if self.service_socket:
                self.service_socket.close()
            self.service_socket = None
            # Schedule a retry on the main thread
            GLib.idle_add(self.retry_connection)

    def update_time_display(self, time_info):
        display_text = time_info.get("full_display", "Error")
        self.time_label.set_markup(f'<span font="10">{GLib.markup_escape_text(display_text)}</span>')
        return GLib.SOURCE_REMOVE # Use GLib.SOURCE_REMOVE to run only once