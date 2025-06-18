import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import os
import socket
import json
import threading
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BatteryIsland(Gtk.Box):
    # --- CONFIGURATION ---
    # Icons for 0-9%, 10-19%, ..., 90-99%
    BATTERY_ICONS = ["󰁺", "󰁻", "󰁼", "󰁽", "󰁾", "󰁿", "󰂀", "󰂁", "󰂂", "󰁹"]
    BATTERY_ICON_FULL = "󰁹"
    BATTERY_ICON_CHARGING = "󰂄"
    ICON_FONT_FAMILY = "Symbols Nerd Font" # Ensure this font is installed

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.add_css_class("island")
        self.add_css_class("battery-island")
        
        self.battery_label = Gtk.Label()
        self.battery_label.add_css_class("battery-label")
        self.append(self.battery_label)
        
        self.socket_path = "/tmp/combined_service.sock"
        self.service_socket = None
        self.connect_to_service()
    
    def connect_to_service(self):
        def connect():
            try:
                self.service_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.service_socket.connect(self.socket_path)
                threading.Thread(target=self.listen_for_updates, daemon=True).start()
                logger.info("Connected to combined_service for battery updates")
            except Exception:
                logger.warning("Failed to connect to combined_service. Retrying.")
                self.service_socket = None
                GLib.timeout_add_seconds(5, self.retry_connection)
        threading.Thread(target=connect, daemon=True).start()

    def retry_connection(self):
        if not self.service_socket:
            self.connect_to_service()
        return False

    def listen_for_updates(self):
        try:
            buffer = ""
            while True:
                data = self.service_socket.recv(1024).decode('utf-8')
                if not data: break
                buffer += data
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        service_data = json.loads(line)
                        logger.debug(f"Received from service: {service_data}")
                        GLib.idle_add(self.update_battery_display, service_data)
        except (socket.error, json.JSONDecodeError, BrokenPipeError):
            logger.warning("Battery service connection lost.")
        finally:
            self.service_socket = None
            GLib.idle_add(self.retry_connection)
    
    def _get_battery_icon(self, percentage, is_charging):
        if is_charging:
            return self.BATTERY_ICON_CHARGING
        if percentage >= 100:
            return self.BATTERY_ICON_FULL
        index = max(0, min(int(percentage / 10), len(self.BATTERY_ICONS) - 1))
        return self.BATTERY_ICONS[index]

    def _format_time(self, seconds):
        if seconds is None or seconds <= 0:
            return ""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    def update_battery_display(self, data):
        percentage = data.get("battery_percentage")
        is_charging = data.get("is_charging", False)
        time_remaining = data.get("battery_time_remaining")
        
        if percentage is not None:
            icon = self._get_battery_icon(percentage, is_charging)
            time_str = self._format_time(time_remaining)
            
            text = f"{percentage}%"
            if time_str:
                action = "charging" if is_charging else "remaining"
                text += f" ({time_str} {action})"
            
            if percentage <= 15 and not is_charging: color = "#f38ba8" # Red
            elif percentage <= 30 and not is_charging: color = "#fab387" # Orange
            elif is_charging: color = "#a6e3a1" # Green
            else: color = "#cdd6f4" # Default
            
            markup = (f'<span font_family="{self.ICON_FONT_FAMILY}" color="{color}">{icon}</span>'
                      f'<span color="{color}"> {text}</span>')
            self.battery_label.set_markup(markup)
        else:
            markup = f'<span font_family="{self.ICON_FONT_FAMILY}">󰚥</span> AC Power'
            self.battery_label.set_markup(markup)
        
        return GLib.SOURCE_REMOVE