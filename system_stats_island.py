# FILE: system_stats_island.py (or your main UI file)
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import os
import socket
import json
import threading
import logging
import subprocess

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SystemStatsIsland(Gtk.Box):
    
    # --- CONFIGURATION (ULTRA-SIMPLE) ---
    ICON_FONT_FAMILY = "Symbols Nerd Font"

    BACKLIGHT_ICONS = ["󰃞 ", "󰃟 ", "󰃠 "]  # Low, Medium, High
    # ADDED: Icons for volume state
    VOLUME_ICONS = {"muted": "󰝟 ", "low": "󰕿 ", "medium": "󰖀 ", "high": "󰕾 "}
    MIC_ICONS = {"muted": " ", "on": " "}

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        self.add_css_class("island")
        self.add_css_class("system-stats-island")
        
        # --- Backlight Label ---
        self.backlight_label = Gtk.Label()
        self.append(self.backlight_label)
        
        backlight_scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        backlight_scroll.connect("scroll", self._on_backlight_scroll)
        self.backlight_label.add_controller(backlight_scroll)

        backlight_click = Gtk.GestureClick.new()
        backlight_click.connect("pressed", self._on_backlight_click)
        self.backlight_label.add_controller(backlight_click)

        # --- Volume Label (NOW DYNAMIC) ---
        self.volume_label = Gtk.Label()
        # REMOVED: Static icon setting is gone. It will be set by update_display.
        self.append(self.volume_label)
        
        volume_scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        volume_scroll.connect("scroll", self._on_volume_scroll)
        self.volume_label.add_controller(volume_scroll)
        
        volume_click = Gtk.GestureClick.new()
        volume_click.connect("pressed", self._on_volume_click)
        self.volume_label.add_controller(volume_click)

        # --- Mic Label ---
        self.mic_label = Gtk.Label()
        self.append(self.mic_label)
        
        mic_click = Gtk.GestureClick.new()
        mic_click.connect("pressed", self._on_mic_click)
        self.mic_label.add_controller(mic_click)
        
        # --- Service Connection ---
        self.socket_path = "/tmp/combined_service.sock"
        self.service_socket = None
        self.connect_to_service()
    
    # --- Event Handlers (One-shot commands) ---
    def _on_backlight_click(self, *args): subprocess.Popen(['hyprlock'])
    def _on_backlight_scroll(self, _, dx, dy): subprocess.run(['brightnessctl', 'set', '5%+' if dy < 0 else '5%-'])
    def _on_volume_scroll(self, _, dx, dy): subprocess.run(['pamixer', '-i' if dy < 0 else '-d', '5'])
    # CHANGED: Click now toggles mute, providing a standard function with visual feedback.
    def _on_volume_click(self, *args): subprocess.run(['pamixer', '--toggle-mute'])
    def _on_mic_click(self, *args): subprocess.run(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SOURCE@', 'toggle'])

    # --- Service Connection and Data Handling ---
    def connect_to_service(self):
        def connect():
            try:
                self.service_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.service_socket.connect(self.socket_path)
                threading.Thread(target=self.listen_for_updates, daemon=True).start()
                logger.info("Connected to combined_service")
            except Exception:
                logger.warning("Failed to connect to combined_service. Retrying in 5s.")
                self.start_service() # Try to start it if connection fails
                GLib.timeout_add_seconds(5, self.retry_connection)
        threading.Thread(target=connect, daemon=True).start()

    def start_service(self):
        # Assumes combined_service.py is in the same directory
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_service.py")
        if os.path.exists(script_path):
            try:
                # Ensure the service script is executable or called via python
                subprocess.Popen(['python3', script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info(f"Attempted to start {script_path}")
            except Exception as e:
                logger.error(f"Failed to start service: {e}")

    def retry_connection(self):
        if not self.service_socket:
            self.connect_to_service()
        return False # Do not repeat timer

    def listen_for_updates(self):
        try:
            buffer = ""
            while True:
                data = self.service_socket.recv(1024).decode('utf-8')
                if not data: break # Connection closed
                buffer += data
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        service_data = json.loads(line)
                        GLib.idle_add(self.update_display, service_data)
        except (socket.error, json.JSONDecodeError, BrokenPipeError):
            logger.warning("Service connection lost.")
        finally:
            self.service_socket = None
            GLib.idle_add(self.retry_connection)
    
    def update_display(self, data):
        # Update Backlight
        if (p := data.get("backlight_percentage")) is not None:
            icon = self.BACKLIGHT_ICONS[0] if p <= 33 else self.BACKLIGHT_ICONS[1] if p <= 66 else self.BACKLIGHT_ICONS[2]
            markup = f'<span font_family="{self.ICON_FONT_FAMILY}">{icon}</span> {p}%'
            self.backlight_label.set_markup(markup)

        # ADDED: Update Volume icon and percentage
        if (vol := data.get("volume_percentage")) is not None:
            is_muted = data.get("speaker_muted", False)
            if is_muted or vol == 0:
                icon = self.VOLUME_ICONS["muted"]
            elif vol <= 33:
                icon = self.VOLUME_ICONS["low"]
            elif vol <= 66:
                icon = self.VOLUME_ICONS["medium"]
            else:
                icon = self.VOLUME_ICONS["high"]
            
            markup = f'<span font_family="{self.ICON_FONT_FAMILY}">{icon}</span> {vol}%'
            self.volume_label.set_markup(markup)

        # Update Mic Status
        if (muted := data.get("mic_muted")) is not None:
            if muted:
                icon, text, color = self.MIC_ICONS["muted"], "Muted", "#f38ba8"
            else:
                icon, text, color = self.MIC_ICONS["on"], "On", "#a6e3a1"
            markup = (f'<span font_family="{self.ICON_FONT_FAMILY}" color="{color}">{icon}</span>'
                      f'<span color="{color}"> {text}</span>')
            self.mic_label.set_markup(markup)
        
        return GLib.SOURCE_REMOVE