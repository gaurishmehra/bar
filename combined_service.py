#!/usr/bin/env python3
# FILE: combined_service.py
from __future__ import annotations  # Enables modern, string-based type hints
import os
import sys
import time
import json
import socket
import threading
import signal
import logging
import glob
import ctypes
import ctypes.util
import errno
import select
import fcntl
from typing import Any, Optional

# --- GObject and Cvc/WirePlumber Setup ---
try:
    import gi
    gi.require_version("GObject", "2.0")
    gi.require_version("GLib", "2.0")
    gi.require_version("Cvc", "1.0")
    from gi.repository import GObject, GLib, Cvc
    CVC_AVAILABLE = True
    logging.info("gi and Cvc libraries found, WirePlumber monitoring will be enabled.")
except (ImportError, ValueError) as e:
    CVC_AVAILABLE = False
    GObject, GLib, Cvc = None, None, None
    logging.warning(f"Could not import GObject/Cvc libraries: {e}. Audio monitoring will be disabled.")

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Inotify Setup (unchanged) ---
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_ATTRIB = 0x00000004
libc_path = ctypes.util.find_library('c')
if not libc_path:
    logging.error("libc not found, inotify will not work.")
    libc = None
else:
    libc = ctypes.CDLL(libc_path, use_errno=True)

if libc:
    class InotifyEvent(ctypes.Structure):
        _fields_ = [('wd', ctypes.c_int), ('mask', ctypes.c_uint32), ('cookie', ctypes.c_uint32), ('len', ctypes.c_uint32)]
    EVENT_HEADER_SIZE = ctypes.sizeof(InotifyEvent)
else:
    InotifyEvent, EVENT_HEADER_SIZE = None, 16


# ==============================================================================
# == CVC / WIREPLUMBER INTEGRATION CLASSES                                   ===
# ==============================================================================

if CVC_AVAILABLE:
    class AudioStream(GObject.Object):
        __gsignals__ = { 'changed': (GObject.SignalFlags.RUN_FIRST, None, ()) }

        # FIXED: Use Any type for dynamic types to avoid Pylance errors
        def __init__(self, stream: Any, control: Any, **kwargs):
            super().__init__(**kwargs)
            self._stream = stream
            self._control = control
            self._stream.connect("notify::is-muted", self._on_prop_changed)
            self._stream.connect("notify::volume", self._on_prop_changed)

        def _on_prop_changed(self, _obj, _pspec): self.emit("changed")
        @GObject.Property(type=str, flags=GObject.ParamFlags.READABLE)
        def name(self) -> str: return self._stream.get_name()
        @GObject.Property(type=float, flags=GObject.ParamFlags.READABLE)
        def volume(self) -> float:
            vol_max_norm = self._control.get_vol_max_norm()
            if vol_max_norm == 0: return 0.0
            return (self._stream.get_volume() / vol_max_norm) * 100
        @GObject.Property(type=bool, default=False, flags=GObject.ParamFlags.READABLE)
        def muted(self) -> bool: return self._stream.get_is_muted()

    class WirePlumberAudioService(GObject.Object):
        __gsignals__ = {
            'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
            'speaker-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
            'microphone-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        }
        INVALID_STREAM_ID = (2**32) - 1

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            # FIXED: Use Any type for dynamic types to avoid Pylance errors
            self._control: Any = Cvc.MixerControl(name="CombinedServiceMonitor")
            self._streams: dict[int, AudioStream] = {}
            self._speaker: Optional[AudioStream] = None
            self._microphone: Optional[AudioStream] = None
            self._speaker_handler_id: Optional[int] = None
            self._mic_handler_id: Optional[int] = None

            self._control.connect("default-sink-changed", self._on_default_sink_changed)
            self._control.connect("default-source-changed", self._on_default_source_changed)
            self._control.connect("stream-added", self._on_stream_added)
            self._control.connect("stream-removed", self._on_stream_removed)
            
            logger.info("Opening Cvc.MixerControl to connect to WirePlumber...")
            self._control.open()
            logger.info("Cvc.MixerControl opened successfully.")

        @GObject.Property(type=AudioStream, flags=GObject.ParamFlags.READABLE)
        def speaker(self) -> Optional[AudioStream]: return self._speaker
        @GObject.Property(type=AudioStream, flags=GObject.ParamFlags.READABLE)
        def microphone(self) -> Optional[AudioStream]: return self._microphone
        
        def _lookup_any_stream(self, stream_id: int):
            return (self._control.lookup_stream_id(stream_id) or 
                    self._control.lookup_output_id(stream_id) or 
                    self._control.lookup_input_id(stream_id))

        def _on_stream_added(self, _, stream_id: int):
            stream = self._lookup_any_stream(stream_id)
            if stream: self._streams[stream_id] = AudioStream(stream=stream, control=self._control)

        def _on_stream_removed(self, _, stream_id: int): self._streams.pop(stream_id, None)

        def _on_default_sink_changed(self, _, stream_id: int):
            if self._speaker and self._speaker_handler_id:
                try: self._speaker.disconnect(self._speaker_handler_id)
                except TypeError: pass
            
            if stream_id == self.INVALID_STREAM_ID:
                self._speaker = None
                logger.info("Default sink has been unset (no device).")
            else:
                stream_obj = self._lookup_any_stream(stream_id)
                if stream_obj:
                    self._speaker = AudioStream(stream=stream_obj, control=self._control)
                    self._speaker_handler_id = self._speaker.connect("changed", lambda _: self.emit("changed"))
                    logger.info(f"Default sink changed to: {self._speaker.name}")
                else:
                    self._speaker = None
                    logger.warning(f"Default sink changed to ID {stream_id}, but stream could not be found.")
            self.emit("speaker-changed"); self.emit("changed")

        def _on_default_source_changed(self, _, stream_id: int):
            if self._microphone and self._mic_handler_id:
                try: self._microphone.disconnect(self._mic_handler_id)
                except TypeError: pass
            if stream_id == self.INVALID_STREAM_ID:
                self._microphone = None
                logger.info("Default source has been unset (no device).")
            else:
                stream_obj = self._lookup_any_stream(stream_id)
                if stream_obj:
                    self._microphone = AudioStream(stream=stream_obj, control=self._control)
                    self._mic_handler_id = self._microphone.connect("changed", lambda _: self.emit("changed"))
                    logger.info(f"Default source changed to: {self._microphone.name}")
                else:
                    self._microphone = None
                    logger.warning(f"Default source changed to ID {stream_id}, but stream could not be found.")
            self.emit("microphone-changed"); self.emit("changed")
            
        def cleanup(self):
            logger.info("Closing Cvc.MixerControl...")
            if self._control: self._control.close()
            logger.info("Cvc.MixerControl closed.")


class CombinedService:
    def __init__(self):
        self.socket_path = "/tmp/combined_service.sock"
        self.current_data = {
            "battery_percentage": None, "is_charging": False, "battery_time_remaining": None,
            "backlight_percentage": None, "volume_percentage": None,
            "speaker_muted": None, "mic_muted": None
        }
        self.clients, self.running, self.data_lock = [], True, threading.Lock()
        self.audio_service, self.glib_thread, self.main_loop = None, None, None
        self.inotify_fd = -1
        self.battery_files = self._find_and_debug_battery_files()
        self.backlight_brightness_file, self.backlight_max_brightness_file = self._find_backlight_files()
        self.last_battery_update = 0  # Track when we last updated battery time
        
        if os.path.exists(self.socket_path):
            try: os.unlink(self.socket_path)
            except OSError: pass
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server_socket.bind(self.socket_path); self.server_socket.listen(5)
        except Exception as e:
            logger.error(f"Failed to bind/listen on socket: {e}"); self.running = False; return
        signal.signal(signal.SIGTERM, self.signal_handler); signal.signal(signal.SIGINT, self.signal_handler)
        self.init_audio_monitoring()
        self.update_file_data_and_notify()
        self.setup_inotify_watches()
        logger.info(f"Combined service started, socket: {self.socket_path}")

    def signal_handler(self, signum, frame):
        logger.info("Shutting down combined service (signal received)...")
        self.running = False
        if self.main_loop and self.main_loop.is_running(): self.main_loop.quit()

    def init_audio_monitoring(self):
        global CVC_AVAILABLE
        if not CVC_AVAILABLE:
            logger.warning("Cvc library not available. Audio status will be placeholders.")
            with self.data_lock: self.current_data.update({"volume_percentage": 70, "speaker_muted": False, "mic_muted": False})
            return
        try:
            logger.info("Initializing WirePlumber/Cvc audio monitoring...")
            self.main_loop = GLib.MainLoop()
            self.glib_thread = threading.Thread(target=self.main_loop.run, daemon=True)
            self.audio_service = WirePlumberAudioService()
            self.audio_service.connect("changed", self._on_audio_state_changed)
            self.glib_thread.start()
            logger.info("GObject/GLib main loop thread started for audio events.")
            GLib.idle_add(self._on_audio_state_changed, self)
        except Exception as e:
            logger.error(f"Failed to initialize Cvc audio monitoring: {e}", exc_info=True)
            if self.main_loop and self.main_loop.is_running(): self.main_loop.quit()
            CVC_AVAILABLE, self.audio_service = False, None
            self.init_audio_monitoring()

    def _on_audio_state_changed(self, *args):
        data_changed = False
        if not self.audio_service: return GLib.SOURCE_REMOVE
        with self.data_lock:
            speaker = self.audio_service.speaker
            new_volume = int(speaker.volume) if speaker else self.current_data['volume_percentage']
            new_speaker_muted = speaker.muted if speaker else self.current_data['speaker_muted']
            if self.current_data['volume_percentage'] != new_volume: self.current_data['volume_percentage'], data_changed = new_volume, True
            if self.current_data['speaker_muted'] != new_speaker_muted: self.current_data['speaker_muted'], data_changed = new_speaker_muted, True

            mic = self.audio_service.microphone
            new_mic_muted = mic.muted if mic else self.current_data['mic_muted']
            if self.current_data['mic_muted'] != new_mic_muted: self.current_data['mic_muted'], data_changed = new_mic_muted, True
        if data_changed: self.notify_clients()
        return GLib.SOURCE_REMOVE

    def _find_and_debug_battery_files(self):
        """Find battery files and print detailed debug info about what exists"""
        paths = glob.glob('/sys/class/power_supply/BAT*')
        if not paths: 
            logger.error("No battery found at /sys/class/power_supply/BAT*")
            return {}
            
        base_path = paths[0]
        logger.info(f"=== BATTERY DEBUG INFO ===")
        logger.info(f"Battery path: {base_path}")
        
        # List all files in the battery directory
        try:
            all_files = os.listdir(base_path)
            logger.info(f"All files in {base_path}: {sorted(all_files)}")
        except OSError as e:
            logger.error(f"Could not list files in {base_path}: {e}")
            return {}
        
        files = {}
        file_names = [
            'capacity', 'status', 'time_to_empty', 'time_to_full',
            'power_now', 'energy_now', 'energy_full', 'energy_full_design',
            'charge_now', 'charge_full', 'current_now'
        ]
        
        for name in file_names:
            file_path = os.path.join(base_path, name)
            exists = os.path.exists(file_path)
            files[name] = file_path if exists else None
            
            if exists:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read().strip()
                    logger.info(f"✓ {name}: {content}")
                except Exception as e:
                    logger.info(f"✗ {name}: exists but unreadable - {e}")
            else:
                logger.info(f"✗ {name}: does not exist")
        
        logger.info("=== END BATTERY DEBUG ===")
        return files

    def _find_backlight_files(self):
        paths = glob.glob('/sys/class/backlight/*'); return (None, None) if not paths else (os.path.join(paths[0], 'actual_brightness'), os.path.join(paths[0], 'max_brightness'))

    def _parse_battery_status(self, status_str):
        """Parse battery status string and return charging state"""
        status_clean = status_str.strip().lower()
        
        # Log the exact status for debugging
        logger.info(f"Raw battery status: '{status_str}' -> cleaned: '{status_clean}'")
        
        # Define charging states - be very explicit
        charging_states = ['charging']
        discharging_states = ['discharging', 'not charging']
        full_states = ['full', 'unknown']
        
        is_charging = False
        is_discharging = False
        
        for state in charging_states:
            if state in status_clean:
                is_charging = True
                break
                
        for state in discharging_states:
            if state in status_clean:
                is_discharging = True
                break
        
        # Log the decision process
        logger.info(f"Status analysis: charging={is_charging}, discharging={is_discharging}")
        
        # Return True only if explicitly charging, False for everything else
        return is_charging and not is_discharging

    def _calculate_time_from_files(self, files, is_charging, status_str):
        """Try multiple methods to calculate battery time"""
        
        # Method 1: Direct time files
        time_file_key = 'time_to_full' if is_charging else 'time_to_empty' if 'discharging' in status_str else None
        if time_file_key and files.get(time_file_key):
            try:
                with open(files[time_file_key], 'r') as f:
                    time_seconds = int(f.read().strip())
                    if time_seconds > 0:
                        logger.debug(f"Got time from {time_file_key}: {time_seconds}s")
                        return time_seconds
            except Exception as e:
                logger.debug(f"Failed to read {time_file_key}: {e}")
        
        # Method 2: power_now + energy files
        if files.get('power_now') and files.get('energy_now'):
            try:
                with open(files['power_now'], 'r') as f:
                    power_now = int(f.read().strip())
                with open(files['energy_now'], 'r') as f:
                    energy_now = int(f.read().strip())
                
                if power_now > 0:
                    if is_charging and files.get('energy_full'):
                        with open(files['energy_full'], 'r') as f:
                            energy_full = int(f.read().strip())
                        time_seconds = int(((energy_full - energy_now) / power_now) * 3600)
                        logger.debug(f"Calculated charging time from power/energy: {time_seconds}s")
                        return time_seconds if time_seconds > 0 else None
                    elif not is_charging:
                        time_seconds = int((energy_now / power_now) * 3600)
                        logger.debug(f"Calculated discharge time from power/energy: {time_seconds}s")
                        return time_seconds if time_seconds > 0 else None
            except Exception as e:
                logger.debug(f"Failed power/energy calculation: {e}")
        
        # Method 3: current_now + charge files (older systems)
        if files.get('current_now') and files.get('charge_now'):
            try:
                with open(files['current_now'], 'r') as f:
                    current_now = int(f.read().strip())
                with open(files['charge_now'], 'r') as f:
                    charge_now = int(f.read().strip())
                
                if current_now > 0:
                    if is_charging and files.get('charge_full'):
                        with open(files['charge_full'], 'r') as f:
                            charge_full = int(f.read().strip())
                        time_seconds = int(((charge_full - charge_now) / current_now) * 3600)
                        logger.debug(f"Calculated charging time from current/charge: {time_seconds}s")
                        return time_seconds if time_seconds > 0 else None
                    elif not is_charging:
                        time_seconds = int((charge_now / current_now) * 3600)
                        logger.debug(f"Calculated discharge time from current/charge: {time_seconds}s")
                        return time_seconds if time_seconds > 0 else None
            except Exception as e:
                logger.debug(f"Failed current/charge calculation: {e}")
        
        logger.debug("Could not calculate battery time using any method")
        return None

    def setup_inotify_watches(self):
        if not libc or not InotifyEvent: return
        self.inotify_fd = libc.inotify_init1(os.O_NONBLOCK)
        if self.inotify_fd == -1: return
        files_to_watch = [f for f in [
            self.battery_files.get('capacity'), self.battery_files.get('status'),
            self.backlight_brightness_file, self.battery_files.get('time_to_empty'), 
            self.battery_files.get('time_to_full'), self.battery_files.get('power_now'),
            self.battery_files.get('energy_now')
        ] if f and os.path.exists(f)]
        for f_path in files_to_watch: libc.inotify_add_watch(self.inotify_fd, f_path.encode('utf-8'), IN_MODIFY | IN_CLOSE_WRITE | IN_ATTRIB)

    def get_battery_info_internal(self):
        if not self.battery_files.get('capacity') or not self.battery_files.get('status'):
            return None, False, None
            
        try:
            with open(self.battery_files['capacity'], 'r') as f: 
                capacity = int(f.read().strip())
            with open(self.battery_files['status'], 'r') as f: 
                status_str = f.read().strip()
                
            # Use the improved status parsing
            is_charging = self._parse_battery_status(status_str)
            
            logger.info(f"Battery: {capacity}%, status: '{status_str}', charging: {is_charging}")
            
            # Calculate time remaining
            time_remaining = None
            if is_charging or 'discharging' in status_str.lower():
                time_remaining = self._calculate_time_from_files(self.battery_files, is_charging, status_str.lower())
            
            logger.info(f"Final battery data: {capacity}%, charging: {is_charging}, time: {time_remaining}s")
            return capacity, is_charging, time_remaining
            
        except Exception as e:
            logger.error(f"Error reading battery info: {e}")
            return None, False, None

    def get_backlight_percentage_internal(self):
        try:
            if not all([self.backlight_brightness_file, self.backlight_max_brightness_file]): return None
            with open(self.backlight_brightness_file, 'r') as f: current = int(f.read().strip())
            with open(self.backlight_max_brightness_file, 'r') as f: max_val = int(f.read().strip())
            return int((current / max_val) * 100) if max_val > 0 else 0
        except (IOError, ValueError): return None

    def update_file_data_and_notify(self, force_battery_update=False):
        current_time = time.time()
        
        # Always update backlight
        backlight_percent = self.get_backlight_percentage_internal()
        
        # Update battery info if forced or if 30 seconds have passed
        should_update_battery = force_battery_update or (current_time - self.last_battery_update) >= 30
        
        if should_update_battery:
            bat_percent, is_charging, time_rem = self.get_battery_info_internal()
            self.last_battery_update = current_time
        else:
            # Keep existing battery values
            with self.data_lock:
                bat_percent = self.current_data["battery_percentage"]
                is_charging = self.current_data["is_charging"]
                time_rem = self.current_data["battery_time_remaining"]
        
        data_changed = False
        
        with self.data_lock:
            if self.current_data["battery_percentage"] != bat_percent: self.current_data["battery_percentage"], data_changed = bat_percent, True
            if self.current_data["is_charging"] != is_charging: self.current_data["is_charging"], data_changed = is_charging, True
            if self.current_data["battery_time_remaining"] != time_rem: self.current_data["battery_time_remaining"], data_changed = time_rem, True
            if self.current_data["backlight_percentage"] != backlight_percent: self.current_data["backlight_percentage"], data_changed = backlight_percent, True
        
        if data_changed: 
            if should_update_battery:
                logger.info(f"Sending update: {bat_percent}%, charging: {is_charging}, time: {time_rem}s")
            self.notify_clients()

    def inotify_event_loop(self):
        if self.inotify_fd == -1:
            # Fallback mode - check every 30 seconds for battery updates
            while self.running: 
                time.sleep(30)
                if self.running:
                    self.update_file_data_and_notify(force_battery_update=True)
            return
            
        while self.running:
            try:
                rlist, _, _ = select.select([self.inotify_fd], [], [], 30.0)  # 30 second timeout
                if not self.running: break
                
                if self.inotify_fd in rlist: 
                    os.read(self.inotify_fd, 4096)
                    self.update_file_data_and_notify(force_battery_update=True)
                else:
                    # Timeout - periodic battery update
                    self.update_file_data_and_notify(force_battery_update=True)
                    
            except OSError as e:
                if e.errno != errno.EINTR: logger.error(f"Inotify loop error: {e}")

    def notify_clients(self):
        with self.data_lock: data_to_send, clients_to_notify = self.current_data.copy(), list(self.clients)
        message = json.dumps(data_to_send) + "\n"; disconnected_clients = []
        for client_socket in clients_to_notify:
            try: client_socket.sendall(message.encode('utf-8'))
            except socket.error: disconnected_clients.append(client_socket)
        if disconnected_clients:
            with self.data_lock:
                for client in disconnected_clients:
                    if client in self.clients: self.clients.remove(client); client.close()

    def handle_client(self, client_socket):
        with self.data_lock: initial_message = json.dumps(self.current_data) + "\n"
        try:
            client_socket.sendall(initial_message.encode('utf-8'))
            while self.running and client_socket.fileno() != -1:
                if not client_socket.recv(1024): break
        finally:
            with self.data_lock:
                if client_socket in self.clients: self.clients.remove(client_socket)
            try: client_socket.close()
            except: pass

    def accept_clients(self):
        while self.running:
            try:
                self.server_socket.settimeout(1.0); client_socket, _ = self.server_socket.accept()
                with self.data_lock: self.clients.append(client_socket)
                threading.Thread(target=self.handle_client, args=(client_socket,), daemon=True).start()
            except socket.timeout: continue
            except Exception as e:
                if self.running: logger.error(f"Error accepting client: {e}")

    def run(self):
        if not self.running: self.cleanup(); return
        inotify_thread = threading.Thread(target=self.inotify_event_loop, daemon=True); inotify_thread.start()
        accept_thread = threading.Thread(target=self.accept_clients, daemon=True); accept_thread.start()
        try:
            while self.running: time.sleep(0.5)
        finally:
            self.running = False; self.cleanup()
            if self.glib_thread: self.glib_thread.join(timeout=1.0)
            inotify_thread.join(timeout=1.0); accept_thread.join(timeout=1.0)

    def cleanup(self):
        logger.info("Cleaning up resources...")
        if self.main_loop and self.main_loop.is_running(): self.main_loop.quit()
        if self.audio_service: self.audio_service.cleanup()
        if self.inotify_fd != -1:
            try: os.close(self.inotify_fd)
            except OSError: pass
        with self.data_lock:
            for client in list(self.clients):
                try: client.close()
                except: pass
            self.clients.clear()
        try: self.server_socket.close()
        except: pass
        if os.path.exists(self.socket_path):
            try: os.unlink(self.socket_path)
            except OSError: pass
        logger.info("Service has shut down.")

def main():
    lock_path = "/tmp/combined_service.lock"; lock_file = open(lock_path, 'w')
    try: fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError): logger.warning("Another instance is running. Exiting."); sys.exit(1)
    
    service = CombinedService()
    if service.running:
        service.run()
    
    fcntl.flock(lock_file, fcntl.LOCK_UN); lock_file.close()
    if os.path.exists(lock_path):
        os.remove(lock_path)

if __name__ == "__main__":
    main()