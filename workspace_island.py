#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import logging
import subprocess

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

class WorkspaceIsland(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("island")
        self.add_css_class("workspace-island")
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)

        # Store workspace buttons to update them
        self.workspace_buttons = {}
        self.active_workspace_id = None
        self.ordered_workspace_ids = [] # To keep track of order for scrolling

        # Event controller for scroll events
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL) # Pass flags to constructor
        scroll_controller.connect("scroll", self.on_scroll)
        self.add_controller(scroll_controller)

        # Initial placeholder
        self.update_workspaces([], None)

    def update_workspaces(self, workspaces_data, active_id):
        self.active_workspace_id = active_id
        # Filter out special workspaces (IDs < 0) if not already done by service
        # and sort by ID to ensure consistent ascending order
        valid_workspaces_data = sorted([ws for ws in workspaces_data if ws.get('id', -1) >= 0], key=lambda x: x['id'])
        current_ws_ids = {ws_data['id'] for ws_data in valid_workspaces_data}
        
        # Update ordered list for scrolling - ensure it's always in ascending order
        self.ordered_workspace_ids = [ws_data['id'] for ws_data in valid_workspaces_data]
        logger.debug(f"Updated workspace order: {self.ordered_workspace_ids}")

        # Remove old buttons that no longer exist
        for ws_id in list(self.workspace_buttons.keys()):
            if ws_id not in current_ws_ids:
                button_to_remove = self.workspace_buttons.pop(ws_id)
                self.remove(button_to_remove)
        
        # Clear the box and re-add buttons in correct order
        # This ensures the visual order matches the logical order
        for child in list(self):
            self.remove(child)
        
        # Add/Update buttons in ascending order
        for ws_data in valid_workspaces_data:
            ws_id = ws_data['id']
            ws_name = str(ws_data.get('name', ws_id)) # Use name, fallback to ID

            if ws_id not in self.workspace_buttons:
                button = Gtk.Button(label=ws_name)
                button.add_css_class("workspace-button")
                button.ws_id = ws_id 
                button.connect("clicked", self.on_workspace_button_clicked)
                self.workspace_buttons[ws_id] = button
            else:
                button = self.workspace_buttons[ws_id]
                if button.get_label() != ws_name:
                    button.set_label(ws_name)

            # Add button to the box in order
            self.append(button)

            # Update style for active/inactive
            if ws_id == self.active_workspace_id:
                button.add_css_class("active-workspace")
                button.remove_css_class("inactive-workspace")
            else:
                button.add_css_class("inactive-workspace")
                button.remove_css_class("active-workspace")

    def on_workspace_button_clicked(self, button):
        ws_id_to_switch = button.ws_id
        logger.info(f"Workspace button {ws_id_to_switch} clicked.")
        try:
            subprocess.run(["hyprctl", "dispatch", "workspace", str(ws_id_to_switch)], check=True)
        except FileNotFoundError:
            logger.error("hyprctl command not found. Please ensure it is in your PATH.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error switching workspace using hyprctl: {e}")

    def on_scroll(self, controller, dx, dy):
        if not self.ordered_workspace_ids or self.active_workspace_id is None:
            return True # Event handled, do nothing

        try:
            current_active_index = self.ordered_workspace_ids.index(self.active_workspace_id)
        except ValueError:
            logger.warning(f"Active workspace ID {self.active_workspace_id} not found in ordered list.")
            return True

        target_ws_id = None

        if dy < 0:  # Scroll up
            if current_active_index > 0:
                target_ws_id = self.ordered_workspace_ids[current_active_index - 1]
            else:
                logger.debug("Already on the first workspace, scroll up does nothing.")
                return True # Do nothing
        elif dy > 0:  # Scroll down
            if current_active_index == len(self.ordered_workspace_ids) - 1:
                logger.info("On the last workspace, creating new one by dispatching 'workspace +1'")
                try:
                    subprocess.run(["hyprctl", "dispatch", "workspace", "+1"], check=True)
                except FileNotFoundError:
                    logger.error("hyprctl command not found. Please ensure it is in your PATH.")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Error creating new workspace via scroll: {e}")
                return True # Action dispatched, service will update UI
            else:
                target_ws_id = self.ordered_workspace_ids[current_active_index + 1]
        
        if target_ws_id is not None:
            logger.info(f"Scrolling to workspace {target_ws_id}")
            try:
                subprocess.run(["hyprctl", "dispatch", "workspace", str(target_ws_id)], check=True)
            except FileNotFoundError:
                logger.error("hyprctl command not found. Please ensure it is in your PATH.")
            except subprocess.CalledProcessError as e:
                logger.error(f"Error switching workspace via scroll: {e}")
        
        return True # Event handled