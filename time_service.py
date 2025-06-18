#!/usr/bin/env python3
"""
Time Service - A lightweight background service to provide time and date updates.
"""

import os
import sys
import time
import json
import socket
import threading
import signal
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TimeService:
    def __init__(self):
        self.socket_path = "/tmp/time_service.sock"
        self.current_time_info = self.get_formatted_time()
        self.clients = []
        self.running = True
        
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
            
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)
        
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        logger.info(f"Time service started, socket: {self.socket_path}")

    def signal_handler(self, signum, frame):
        logger.info("Shutting down time service...")
        self.running = False
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        try:
            self.server_socket.close()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_ordinal_suffix(self, day):
        if 10 <= day % 100 <= 20:
            return "th"
        return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    def get_formatted_time(self):
        now = datetime.now()
        time_str = now.strftime("%H:%M")
        day_name = now.strftime("%a")
        day = now.day
        suffix = self.get_ordinal_suffix(day)
        month = now.strftime("%b")
        date_str = f"{day}{suffix} {month}"
        return {"time_str": time_str, "day_name": day_name, "date_str": date_str, "full_display": f"{time_str}, {day_name}, {date_str}"}

    def monitor_time(self):
        while self.running:
            try:
                new_time_info = self.get_formatted_time()
                # Time changes every minute, so we always update if the minute is different
                if new_time_info["time_str"] != self.current_time_info["time_str"]:
                    self.current_time_info = new_time_info
                    self.notify_clients()
                    logger.debug(f"Time updated: {self.current_time_info['full_display']}")
                
                # Sleep until the start of the next minute
                now = datetime.now()
                sleep_seconds = 60 - now.second
                time.sleep(sleep_seconds)

            except Exception as e:
                logger.error(f"Error in time monitoring: {e}")
                time.sleep(5) # Wait before retrying in case of error

    def notify_clients(self):
        message = json.dumps(self.current_time_info) + "\n"
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
        try:
            message = json.dumps(self.current_time_info) + "\n"
            client_socket.send(message.encode())
            
            # Keep connection alive, but time service is push-only
            while self.running:
                if client_socket.fileno() == -1: # Check if socket is closed
                    break
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
        try:
            monitor_thread = threading.Thread(target=self.monitor_time, daemon=True)
            monitor_thread.start()
            
            self.accept_clients()
            
        except KeyboardInterrupt:
            logger.info("Time service interrupted by user")
        except Exception as e:
            logger.error(f"Time service error: {e}")
        finally:
            self.cleanup()

def main():
    if os.path.exists("/tmp/time_service.sock"):
        try:
            test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_socket.connect("/tmp/time_service.sock")
            test_socket.close()
            print("Time service is already running.")
            sys.exit(0)
        except Exception:
            os.unlink("/tmp/time_service.sock") # Stale socket
    
    service = TimeService()
    service.run()

if __name__ == "__main__":
    main()
