"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
ctc_panel.py:

Implementation for older "CTC" protocol alarms.

All of the alarms seen using this protocl are based on a mysterious MIPS SoC -
the Davicom DM9168GP for which no public documentation is available. The alarms
run a MontaVista Linux 2.4 kernel. The alarm application runs in userspace.
The DM9168GP has only a supervisory role. The real business of the alarm happens
in a smaller (8051 based?) MCU placed underneath the DM9168GP mezzanine board.

CTC alarms all speak the protocol defined in this class however exchange the
XML "polling" document with the server in two known ways:

HTTP polling transport:

With this method the polling document is periodically posted to an HTTP endpoint
on the server. Status information is collected by the server, and commands are
inserted into the returned document. The server has to wait until next time
the alarm "polls" to collect the result. Which might be quite a long wait
depending on the polling period configured on the alarm (default = 120s).

XMPP transport:

These alarms use the exact same polling document to exchange information and
commands with the server however the phrase "polling" becomes a misnomer as
as the alarm holds a persistent connection to the server enabling instantenous
exchanges of status, commands, and the collection of results.

Three alarms have been seen which speak this protocol:

1) CTC-1735 (HTTP polling transport): Halo hub
2) CTC-1815 (XMPP transport): Yale EF-IPBOX v2
2) CTC-1815 (XMPP transport): Lupus XT1. XMPP code appears to be in the
    firmware but it is not officially used by Lupus and superficial attempts
    to make it work were unsuccessful.
--------------------------------------------------------------------------------
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; If not, see <http://www.gnu.org/licenses/>.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional
from xml.sax.saxutils import escape as xml_escape

from alarmhomeserver.interfaces import PanelTransport
from alarmhomeserver.models.alarm import (
    Device,
    DeviceAttribute,
    DeviceState,
    DeviceStatus,
    DeviceType,
    LearnedDevice,
    PanelBackendConfig,
    PanelMode,
    PanelType,
    PanelUser,
    SirenSetting,
    SystemConfiguration,
    WalkNotification,
)
from alarmhomeserver.models.history_descriptions import HistoryEntry
from alarmhomeserver.panels.base_panel import BasePanel
from alarmhomeserver.transports.http_polling_transport import HttpPollingTransport

PROTOCOL_TIMEOUT_SECONDS = 10.0
USER_SLOTS = 6


class CtcPanel(BasePanel):
    def __init__(
        self,
        logger: logging.Logger,
        transport: PanelTransport,
        backend_config: PanelBackendConfig,
        initial_polling_response: Optional[str] = None,
    ):
        super().__init__(logger, transport, backend_config)
        self._initial_polling_response = initial_polling_response
        self._initialised_event = threading.Event()
        self._xml_command_id = 0
        self._panel_report_id: Optional[str] = None
        self._panel_sensor_mod = 0

        self._special_state_context: object = None
        self._learned_device_notifier: Optional[Callable[[LearnedDevice], None]] = None
        self._last_learned_device: Optional[LearnedDevice] = None
        self._notified_learn_sids: set[str] = set()
        self._learn_stop_event: Optional[threading.Event] = None
        self._learn_thread: Optional[threading.Thread] = None

        self._walk_test_notifier: Optional[Callable[[WalkNotification], None]] = None
        self._walk_test_stop_event: Optional[threading.Event] = None
        self._walk_test_thread: Optional[threading.Thread] = None
        self._walk_test_signals_notified = 0

        self._learn_lock = threading.Lock()
        self._walk_lock = threading.Lock()

        transport.set_message_received(self._on_message_received)

    @property
    def max_device_name_length(self) -> int:
        return 10

    @property
    def max_signal_strength(self) -> int:
        return 6

    def initialise(self) -> bool:
        self._devices = []
        self._users = []
        self._device_states = []
        self._alarm_data_loaded = False
        self._type = (
            PanelType.CTC_Polling if isinstance(self.transport, HttpPollingTransport) else PanelType.CTC_XMPP
        )

        # Sensible defaults - for CTC-1735 this is all the user will ever see when opening the
        # settings page, because we have no way to read settings over the polling interface (can only write).
        self._config = SystemConfiguration(
            home_arm_exit_delay=30,
            away_arm_exit_delay=30,
            away_arm_entry_delay=30,
            home_arm_entry_delay=30,
            away_arm_entry_delay_sound=1,
            away_arm_exit_delay_sound=1,
            home_arm_entry_delay_sound=1,
            home_arm_exit_delay_sound=1,
            door_contact_sound=0,
            hub_warning_sound=0,
            supervision=0,
            siren_length=3,
        )

        threading.Thread(target=self._load_panel_data_safe, daemon=True).start()
        self._initialised_event.wait(180)

        return self._alarm_data_loaded

    def _load_panel_data_safe(self) -> None:
        try:
            self._load_panel_data()
        except Exception:
            self.logger.exception("Failure loading alarm data")

    def get_history(self) -> list[HistoryEntry]:
        self.log_warning("History is not yet supported over the XML protocol")
        return []

    def set_mode(self, mode: PanelMode, user: int) -> tuple[bool, bool]:
        wire_mode = {PanelMode.Disarm: 1, PanelMode.HomeArm: 2, PanelMode.AwayArm: 3}.get(mode, -1)

        if wire_mode == -1:
            self.log_error(f"Not in correct mode to change to: {mode}")
            return False, False

        self._last_mode_change_user = user
        self._last_mode_change_command = _utc_now()

        xml_text, command_id = self._build_command_xml("setMode", f'<mode value="{wire_mode}"/><f_arm value="0"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not response or not response.strip():
            self.log_error(f"SetMode({mode}) failed")
            return False, False

        document = ET.fromstring(response)
        set_mode_command = document.find(".//command[@action='setMode']")

        result_text = None
        if set_mode_command is not None:
            result_node = set_mode_command.find("result")
            result_text = result_node.text if result_node is not None else None
        if result_text != "1":
            root_result = document.find("result")
            result_text = root_result.text if root_result is not None else None

        if result_text != "1":
            self.log_error(f"SetMode({mode}) failed: {response}")
            return False, False

        door_open = False
        message_node = None
        if set_mode_command is not None:
            message_node = set_mode_command.find("message")
        if message_node is None:
            message_node = document.find("message")
        message = message_node.text.strip() if message_node is not None and message_node.text else None

        if message and message.lower() != "ok":
            zone_match = re.search(r"Zone:(\d+)", message)
            triggered_device = None
            if zone_match:
                zone_index = int(zone_match.group(1))
                triggered_device = next((d for d in self._devices if d.index == zone_index), None)

            door_open = triggered_device is not None and triggered_device.type == DeviceType.DoorContact
            self.log_warning(f"Panel reported '{message}' while setting mode to {mode}")

        self.log_info(f"Set mode to: {mode}")
        self._mode = mode

        return True, door_open

    def set_user(self, index: int, name: str, pin: str, latch: bool) -> bool:
        if not any(u.index == index for u in self._users):
            self.log_error(f"No user at index {index} to edit")
            return False

        if not self._write_user(index, name, pin, latch):
            return False

        self.log_info(f"Edited user {index} ('{name}')")
        self._load_users()
        return True

    def _write_user(self, index: int, name: str, pin: str, latch: bool) -> bool:
        inner = (
            f'<index value="{index}"/><name value="{xml_escape(name)}"/>'
            f'<code value="{xml_escape(pin)}"/><latch value="{1 if latch else 0}"/>'
        )
        xml_text, command_id = self._build_command_xml("setUser", inner)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setUser"):
            self.log_error(f"Failed to write user {index}: {response}")
            return False
        return True

    def add_user(self, name: str, pin: str, latch: bool) -> int:
        used = {u.index for u in self._users}
        free_slot = next((i for i in range(1, USER_SLOTS + 1) if i not in used), None)

        if free_slot is None:
            self.log_error(f"All {USER_SLOTS} user slots are occupied")
            return -1

        if not self._write_user(free_slot, name, pin, latch):
            return -1

        self.log_info(f"Added user '{name}' at slot {free_slot}")
        self._load_users()
        return free_slot

    def delete_user(self, index: int) -> bool:
        if index == 1:
            self.log_error("The master user cannot be deleted")
            return False

        existing = next((u for u in self._users if u.index == index), None)
        if existing is None:
            self.log_error(f"No user at index {index} to delete")
            return False

        xml_text, command_id = self._build_command_xml("delUser", f'<index value="{index}"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "delUser"):
            self.log_error(f"Failed to delete user {index}: {response}")
            return False

        self.log_info(f"Deleted user {index} ('{existing.name}')")
        self._load_users()
        return True

    def _load_users(self) -> None:
        xml_text, command_id = self._build_command_xml("getUsers")
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not response or not response.strip():
            self.log_warning("Failed to get users using XML protocol")
            return

        self._parse_users(ET.fromstring(response))

    def add_learned_device(self, new_device: Optional[LearnedDevice], name: str) -> bool:
        new_device = new_device or self._last_learned_device
        if new_device is None:
            self.log_error("No learned device to add")
            return False

        xml_text, command_id = self._build_command_xml(
            "addSensor", f'<sid value="{xml_escape(new_device.radio_identifier or "")}"/>'
        )
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not response or not response.strip():
            self.log_error("Failed to add device")
            return False

        document = ET.fromstring(response)
        add_command = document.find(".//command[@action='addSensor']")
        result_node = add_command.find("result") if add_command is not None else None

        if result_node is None or result_node.text != "1":
            self.log_error(f"Failed to add device: {response}")
            return False

        zone_node = add_command.find("xmldata/zone") if add_command is not None else None
        if zone_node is None:
            zone_node = document.find(".//xmldata/zone")

        zone_value = zone_node.get("value") if zone_node is not None else None
        if zone_value is None or not zone_value.isdigit():
            self.log_error(f"Alarm did not return an index for the added device: {response}")
            return False

        index = int(zone_value)

        # addSensor only returns the assigned index, not the device itself - refresh our copy
        # first so it's actually there for set_device below to find and name.
        self._load_devices()

        if not self.set_device(index, name, None, None, None):
            self.log_warning(f"Device added at index {index} but naming it failed")
        else:
            self.log_info(f"Added device '{name}' at index {index} ({new_device.radio_identifier})")

        return True

    def set_device(
        self, index: int, name: str, attribute: Optional[DeviceAttribute], bypass: Optional[bool], chime: Optional[bool]
    ) -> bool:
        device = next((d for d in self._devices if d.index == index), None)
        if device is None:
            self.log_error(f"No device at index {index}")
            return False

        value = None
        if attribute is not None:
            value = _map_to_xml_device_attribute(attribute)
            if value is None:
                self.log_error(f"{attribute} has no equivalent on this panel")
                return False

        inner = f'<zone value="{index}"/><name value="{xml_escape(name)}"/>'
        if value is not None:
            inner += f'<attr value="{value}"/>'
        if bypass is not None:
            inner += f'<bypass value="{1 if bypass else 0}"/>'
        if chime is not None:
            inner += f'<chime value="{1 if chime else 0}"/>'

        xml_text, command_id = self._build_command_xml("setSensor", inner)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setSensor"):
            self.log_error(f"Failed to save the device at index {index}: {response}")
            return False

        self._load_devices()
        saved = next((d for d in self._devices if d.index == index), None)

        if saved is None or saved.name != name:
            self.log_error(f"Wrote name '{name}' to index {index}, but the panel now reports '{saved and saved.name}'")
            return False

        if attribute is not None and saved.attribute != attribute:
            self.log_error(
                f"Wrote attr={value} to index {index} for {attribute}, but the panel now reports "
                f"{saved.attribute}. It was {device.attribute} before."
            )
            return False

        if bypass is not None:
            saved.bypass = bypass

        self.log_info(f"Set the device at index {index} to '{name}'")
        return True

    def delete_device(self, index: int) -> bool:
        existing = next((d for d in self._devices if d.index == index), None)
        if existing is None:
            self.log_error(f"No device at index {index} to delete")
            return False

        xml_text, command_id = self._build_command_xml("delSensor", f'<zone value="{index}"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "delSensor"):
            self.log_error(f"Failed to delete device at index {index}: {response}")
            return False

        self.log_info(f"Deleted device '{existing.name}' at index {index}")
        self._load_devices()
        return True

    def refresh_devices(self) -> bool:
        # CTC alarms don't tell us when devices are edited, so we have to refresh to see anything current
        self._load_devices()
        return True

    def _load_devices(self) -> None:
        xml_text, command_id = self._build_command_xml("getSensors")
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not response or not response.strip():
            self.log_warning("Failed to get devices using XML protocol")
            return

        self._parse_sensors(ET.fromstring(response))

    def start_walk_test(self, notifier: Callable[[WalkNotification], None], context: object) -> bool:
        xml_text, command_id = self._build_command_xml("setMode", '<mode value="5"/><f_arm value="0"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setMode"):
            self.log_error(f"Failed to start walk test: {response}")
            return False

        self._special_state_context = context
        self._walk_test_notifier = notifier
        self._walk_test_signals_notified = 0
        self._walk_test_stop_event = threading.Event()
        self._walk_test_thread = threading.Thread(
            target=self._poll_walk_test_signals, args=(self._walk_test_stop_event,), daemon=True
        )
        self._walk_test_thread.start()

        self.log_info("Walk test started")
        return True

    def _poll_walk_test_signals(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                xml_text, command_id = self._build_command_xml("getWalkTestSignals")
                response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

                if response and response.strip():
                    document = ET.fromstring(response)
                    signals_command = document.find(".//command[@action='getWalkTestSignals']")
                    result_node = signals_command.find("result") if signals_command is not None else None
                    if result_node is not None and result_node.text == "1":
                        self._process_walk_test_signals(signals_command)
            except Exception as ex:
                self.log_warning(f"Failed to poll for walk test signals: {ex}")

            stop_event.wait(2)

    def _process_walk_test_signals(self, signals_command: ET.Element) -> None:
        # The alarm sends a full list of received signals for the session each time a new one is
        # received. But we just take the latest and bubble it up - let the UI accumulate.
        signals = signals_command.findall("xmldata/signal")

        if len(signals) < self._walk_test_signals_notified:
            self._walk_test_signals_notified = 0

        for i in range(self._walk_test_signals_notified, len(signals)):
            signal = signals[i]
            zone_node = signal.find("zone")
            rssi_node = signal.find("rssi")
            zone = zone_node.get("value") if zone_node is not None else None
            rssi = rssi_node.get("value") if rssi_node is not None else None

            if zone is None or rssi is None or not zone.isdigit() or not rssi.lstrip("-").isdigit():
                continue

            device_index = int(zone)
            device = next((d for d in self._devices if d.index == device_index), None)

            self.log_info(f"Walk test signal received from '{device and device.name}' RSSI: {rssi}")

            if self._walk_test_notifier:
                self._walk_test_notifier(
                    WalkNotification(alarm=self, context=self._special_state_context, device=device, rssi=int(rssi))
                )

        self._walk_test_signals_notified = len(signals)

    def stop_walk_test(self) -> bool:
        with self._walk_lock:
            if self._walk_test_notifier is None:
                return True

            if self._walk_test_stop_event:
                self._walk_test_stop_event.set()
            if self._walk_test_thread:
                self._walk_test_thread.join(timeout=10)
            self._walk_test_thread = None

            xml_text, command_id = self._build_command_xml("setMode", '<mode value="1"/><f_arm value="0"/>')
            response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

            if not self._is_command_successful(response, "setMode"):
                self.log_error(f"Failed to stop walk test: {response}")
                return False

            self._special_state_context = None
            self._walk_test_notifier = None
            self.log_info("Walk test stopped")
            return True

    def start_learn(self, notifier: Callable[[LearnedDevice], None], context: object) -> bool:
        xml_text, command_id = self._build_command_xml("setMode", '<mode value="6"/><f_arm value="0"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setMode"):
            self.log_error(f"Failed to enter learn mode: {response}")
            return False

        self._special_state_context = context
        self._learned_device_notifier = notifier
        self._notified_learn_sids = set()
        self._learn_stop_event = threading.Event()
        self._learn_thread = threading.Thread(target=self._poll_learn_signals, args=(self._learn_stop_event,), daemon=True)
        self._learn_thread.start()

        self.log_info("Entered learn mode")
        return True

    def _process_learn_signals(self, signals_command: ET.Element) -> bool:
        signals = signals_command.findall(".//signal")
        if not signals:
            self.log_info("Learn: panel reported no detected devices")
            return False

        found = False
        for signal in signals:
            sid_node = signal.find("sid")
            type_node = signal.find("type")
            if type_node is None:
                type_node = signal.find("ty")

            sid = sid_node.get("value") if sid_node is not None else None
            type_value = type_node.get("value") if type_node is not None else None

            if not sid:
                self.log_warning("Ignoring malformed learn signal")
                continue

            if sid in self._notified_learn_sids:
                continue
            self._notified_learn_sids.add(sid)

            learned_device = LearnedDevice(
                alarm=self, context=self._special_state_context, radio_identifier=sid, type=_parse_xml_device_type(type_value)
            )
            self.log_info(f"Learn: {learned_device.type} found ({learned_device.radio_identifier})")

            self._last_learned_device = learned_device
            if self._learned_device_notifier:
                self._learned_device_notifier(learned_device)
            found = True

        return found

    def _poll_learn_signals(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                xml_text, command_id = self._build_command_xml("getLearnSignals")
                response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)
                if response:
                    document = ET.fromstring(response)
                    signals_command = document.find(".//command[@action='getLearnSignals']")
                    result_node = signals_command.find("result") if signals_command is not None else None
                    if result_node is not None and result_node.text == "1" and self._process_learn_signals(signals_command):
                        return
            except Exception:
                pass

            stop_event.wait(2)

    def stop_learn(self) -> bool:
        with self._learn_lock:
            if self._learned_device_notifier is None:
                return True

            if self._learn_stop_event:
                self._learn_stop_event.set()
            if self._learn_thread:
                self._learn_thread.join(timeout=10)
            self._learn_thread = None

            xml_text, command_id = self._build_command_xml("setMode", '<mode value="1"/><f_arm value="0"/>')
            response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

            if not self._is_command_successful(response, "setMode"):
                self.log_error(f"Failed to exit learn mode: {response}")
                return False

            self._special_state_context = None
            self._learned_device_notifier = None
            self.log_info("Exited learn mode")
            return True

    # INTERESTING OBSERVATION: The CTC-1815's UI appears to suggest that PIR cameras are supported
    # But stuffed if I could get get one to pair. Left out for now.
    def request_media(self, device: int, flash: bool) -> Optional[str]:
        message = "Requesting media is not supported on this alarm type"
        self.log_warning(message)
        return message

    # No indication that power switches are supported.
    # And I couldn't get one to pair. Also left out.
    def set_power_switch(self, index: int, on: bool) -> Optional[str]:
        message = "Power switches are not supported on this alarm type"
        self.log_warning(message)
        return message

    def get_system_config(self) -> Optional[SystemConfiguration]:
        if self._type == PanelType.CTC_Polling:
            return self._config  # Just return what we have in memory

        self._load_delays()
        return self._config

    def set_away_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        inner = (
            f'<edelay value="{entry_delay // 10}"/><xdelay value="{exit_delay // 10}"/>'
            f'<ebeep value="{1 if entry_delay_sound > 0 else 0}"/><xbeep value="{1 if exit_delay_sound > 0 else 0}"/>'
        )
        xml_text, command_id = self._build_command_xml("setAwayEnExDelay", inner)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setAwayEnExDelay"):
            self.log_error(f"Failed to save Away Arm configuration: {response}")
            return False

        self.log_info("Saved Away Arm configuration")
        self._load_delays()
        return True

    def set_home_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        inner = (
            f'<edelay value="{entry_delay // 10}"/><xdelay value="{exit_delay // 10}"/>'
            f'<ebeep value="{1 if entry_delay_sound > 0 else 0}"/><xbeep value="{1 if exit_delay_sound > 0 else 0}"/>'
        )
        xml_text, command_id = self._build_command_xml("setHomeEnExDelay", inner)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setHomeEnExDelay"):
            self.log_error(f"Failed to save Home Arm configuration: {response}")
            return False

        self.log_info("Saved Home Arm configuration")
        self._load_delays()
        return True

    def set_general_config(self, door_contact_sound: int, supervision: int, siren_length: int) -> bool:
        inner = (
            f'<doorchime value="{1 if door_contact_sound > 0 else 0}"/>'
            f'<supervisor value="{5 if supervision > 0 else 0}"/><alarmlength value="{siren_length}"/>'
        )
        xml_text, command_id = self._build_command_xml("setPanel", inner)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "setPanel"):
            self.log_error(f"Failed to save General configuration: {response}")
            return False

        self.log_info("Saved General configuration")
        self._load_delays()
        return True

    def send_siren_configuration(self, setting: SirenSetting, value: int) -> bool:
        if setting == SirenSetting.ComfortLed:
            rf_code = 50 + value
        elif setting == SirenSetting.TamperDetection:
            rf_code = 20 if value > 0 else 21
        elif setting == SirenSetting.EntryExitConfirm:
            rf_code = 22 if value > 0 else 23
        else:
            raise ValueError(f"Unsupported siren setting: {setting}")

        xml_text, command_id = self._build_command_xml("transmitSignal", f'<rfcode value="{rf_code}"/>')
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not self._is_command_successful(response, "transmitSignal"):
            self.log_error(f"Failed to save {setting}: {response}")
            return False

        self.log_info(f"Set siren {setting} to {value} (rfcode {rf_code})")
        return True

    def _on_message_received(self, message: str, is_xml: bool) -> None:
        # Handles unsolicited messages sent by the alarm.
        if not is_xml:
            raise ValueError("CTC Panel should never receive non-XML payloads")

        try:
            document = ET.fromstring(message)
            self._parse_mode(document)
            self._parse_sensors(document)
            self._parse_users(document)
            self._parse_delays(document)
        except ET.ParseError as ex:
            self.log_warning(f"Ignoring malformed XML message: {ex}")

    def _load_panel_data(self) -> None:
        cleaned = (
            HttpPollingTransport.clean_xml(self._initial_polling_response)
            if self._type == PanelType.CTC_Polling
            else self._initial_polling_response
        )
        document = ET.fromstring(cleaned)

        ver_node = document.find("ver")
        self._version = ver_node.get("value") if ver_node is not None else None

        # Currently configured CID Report account number. Whatever it is, we have to take note of
        # it and return it in all messages back to the alarm, otherwise it will ignore us.
        rptipid_node = document.find("rptipid")
        self._panel_report_id = rptipid_node.get("value") if rptipid_node is not None else None

        version_upper = (self._version or "").upper()
        if "CTC-1735" in version_upper:
            self._type = PanelType.CTC_Polling
        elif "CTC-1815" in version_upper:
            self._type = PanelType.CTC_XMPP
        else:
            self._type = PanelType.Unknown

        mode_command = document.find(".//command[@action='getMode']") or document.find(".//command[@action='getPanel']")
        if mode_command is not None:
            result_node = mode_command.find("result")
            if result_node is not None and result_node.text == "1":
                mode_node = mode_command.find("xmldata/mode")
                mode_value = mode_node.get("value") if mode_node is not None else None
                if mode_value is not None and mode_value.isdigit() and 0 <= int(mode_value) <= 3:
                    self._mode = {0: PanelMode.Disarm, 1: PanelMode.Disarm, 2: PanelMode.HomeArm, 3: PanelMode.AwayArm}[
                        int(mode_value)
                    ]

        commands = ["getSensors", "getUsers"]
        if self._type == PanelType.CTC_XMPP:
            commands += ["getAwayEnExDelay", "getHomeEnExDelay"]

        xml_text, command_id = self._build_batched_commands_xml(*commands)
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)
        cleaned = HttpPollingTransport.clean_xml(response) if self._type == PanelType.CTC_Polling else response

        document = ET.fromstring(cleaned)
        self._parse_sensors(document)
        self._parse_users(document)
        self._parse_delays(document)

        self._alarm_data_loaded = True
        self._initialised_event.set()

        self.log_info(f"Version={self._version} Mode={self._mode}")

    def _parse_mode(self, document: ET.Element) -> None:
        mode_command = document.find(".//command[@action='getMode']") or document.find(".//command[@action='getPanel']")
        if mode_command is None:
            return

        result_node = mode_command.find("result")
        if result_node is None or result_node.text != "1":
            return

        mode_node = mode_command.find("xmldata/mode")
        mode_value = mode_node.get("value") if mode_node is not None else None
        if mode_value is None or not mode_value.isdigit():
            return

        # 4 = entering walk test (transient), 5 = walk test active, 6 = learn/pairing mode; none
        # are real arm states, so leave _mode untouched while any of them are active.
        new_mode = {0: PanelMode.Disarm, 1: PanelMode.Disarm, 2: PanelMode.HomeArm, 3: PanelMode.AwayArm}.get(int(mode_value))
        if new_mode is None:
            return

        if new_mode != self._mode:
            self.log_info(f"Mode changed to {new_mode}")
        self._mode = new_mode

    def _parse_users(self, document: ET.Element) -> None:
        users_command = document.find(".//command[@action='getUsers']")
        if users_command is None:
            return
        result_node = users_command.find("result")
        if result_node is None or result_node.text != "1":
            return

        users = []
        for user in users_command.findall("xmldata/user"):
            index_node, code_node, name_node, latch_node = (user.find(n) for n in ("index", "code", "name", "latch"))
            index_value = index_node.get("value") if index_node is not None else None
            if index_value is None or not index_value.isdigit():
                continue

            index = int(index_value)
            # CTC alarms only expose 6 user slots (1-6) for general users. Slots above 6
            # (e.g. 9=Installer, 10=Master) are system PINs.
            if index > USER_SLOTS:
                continue

            code = code_node.get("value") if code_node is not None else None
            name = name_node.get("value") if name_node is not None else None
            latch = latch_node.get("value") if latch_node is not None else None

            if not (code or "").strip() and not (name or "").strip():
                continue

            users.append(
                PanelUser(
                    index=index,
                    pin=code,
                    name=name if (name or "").strip() else f"User {index}",
                    latch=(latch or "").lower() == "enabled",
                )
            )

        self._users = users
        self.log_info(f"{len(self._users)} User(s) loaded")

    def _parse_sensors(self, document: ET.Element) -> None:
        sensors_command = document.find(".//command[@action='getSensors']")
        if sensors_command is None:
            return
        result_node = sensors_command.find("result")
        if result_node is None or result_node.text != "1":
            return

        devices: list[Device] = []
        device_states: list[DeviceState] = []

        for zone in sensors_command.findall("xmldata/zone"):
            index_node = zone.find("no")
            type_node = zone.find("type")
            attribute_node = zone.find("attr")
            address_node = zone.find("address")
            name_node = zone.find("name")
            status1_node = zone.find("status1")
            bypass_node = zone.find("bypass")

            index_value = index_node.get("value") if index_node is not None else None
            if index_value is None or not index_value.isdigit():
                continue
            index = int(index_value)

            status1 = status1_node.get("value") if status1_node is not None else None
            has_status1 = False
            status_value = 0
            if status1:
                try:
                    status_value = int(status1, 16)
                    has_status1 = True
                except ValueError:
                    pass

            attribute_str = attribute_node.get("value") if attribute_node is not None else None
            bypass_str = bypass_node.get("value") if bypass_node is not None else None

            device = Device(
                index=index,
                type=_parse_xml_device_type(type_node.get("value") if type_node is not None else None),
                attribute=_map_xml_device_attribute(int(attribute_str)) if attribute_str and attribute_str.isdigit() else DeviceAttribute.Unset,
                name=name_node.get("value") if name_node is not None else None,
                radio_identifier=address_node.get("value") if address_node is not None else None,
                bypass=(bypass_str == "1") if bypass_str is not None else (has_status1 and (status_value & 0x04) != 0),
            )
            devices.append(device)

            if has_status1:
                status = DeviceStatus(status_value)
                previous = next((s for s in self._device_states if s.device and s.device.index == index), None)
                was_open = bool(previous and previous.status & DeviceStatus.Open)

                device_states.append(
                    DeviceState(
                        device=device,
                        status=status,
                        status_last_changed=(
                            (previous.status_last_changed if was_open else _utc_now())
                            if status & DeviceStatus.Open
                            else None
                        ),
                    )
                )

        self._devices = devices
        self._device_states = device_states
        self.log_info(f"{len(self._devices)} Device(s) loaded")

    def _parse_delays(self, document: ET.Element) -> None:
        away_command = document.find(".//command[@action='getAwayEnExDelay']")
        home_command = document.find(".//command[@action='getHomeEnExDelay']")

        away_result = away_command.find("result") if away_command is not None else None
        home_result = home_command.find("result") if home_command is not None else None
        if away_result is None or away_result.text != "1" or home_result is None or home_result.text != "1":
            return

        away_data = away_command.find("xmldata")
        home_data = home_command.find("xmldata")

        def attr(node: Optional[ET.Element], name: str) -> Optional[str]:
            if node is None:
                return None
            child = node.find(name)
            return child.get("value") if child is not None else None

        def as_int(value: Optional[str]) -> Optional[int]:
            return int(value) if value is not None and value.lstrip("-").isdigit() else None

        # Firmware reports delays in units of 10 seconds (e.g. "3" means 30 seconds).
        away_exit = as_int(attr(away_data, "xdelay"))
        if away_exit is not None:
            self._config.away_arm_exit_delay = away_exit * 10
        home_exit = as_int(attr(home_data, "xdelay"))
        if home_exit is not None:
            self._config.home_arm_exit_delay = home_exit * 10
        away_entry = as_int(attr(away_data, "edelay"))
        if away_entry is not None:
            self._config.away_arm_entry_delay = away_entry * 10
        home_entry = as_int(attr(home_data, "edelay"))
        if home_entry is not None:
            self._config.home_arm_entry_delay = home_entry * 10

        away_ebeep = as_int(attr(away_data, "ebeep"))
        if away_ebeep is not None:
            self._config.away_arm_entry_delay_sound = away_ebeep
        away_xbeep = as_int(attr(away_data, "xbeep"))
        if away_xbeep is not None:
            self._config.away_arm_exit_delay_sound = away_xbeep
        home_ebeep = as_int(attr(home_data, "ebeep"))
        if home_ebeep is not None:
            self._config.home_arm_entry_delay_sound = home_ebeep
        home_xbeep = as_int(attr(home_data, "xbeep"))
        if home_xbeep is not None:
            self._config.home_arm_exit_delay_sound = home_xbeep

        door_chime = as_int(attr(away_data, "doorchime"))
        if door_chime is not None:
            self._config.door_contact_sound = door_chime
        supervisor = as_int(attr(away_data, "supervisor"))
        if supervisor is not None:
            self._config.supervision = 1440 if supervisor != 0 else 0
        alarm_length = as_int(attr(away_data, "alarm"))
        if alarm_length is not None:
            self._config.siren_length = alarm_length

        self.log_info("Delays loaded")

    def _load_delays(self) -> None:
        if self._type == PanelType.CTC_Polling:
            return  # Not gonna work. getAwayEnExDelay and getHomeEnExDelay don't exist.

        xml_text, command_id = self._build_batched_commands_xml("getAwayEnExDelay", "getHomeEnExDelay")
        response = self.transport.send_command(xml_text, command_id, PROTOCOL_TIMEOUT_SECONDS)

        if not response or not response.strip():
            self.log_warning("Failed to get delays using XML protocol")
            return

        self._parse_delays(ET.fromstring(response))

    def _is_command_successful(self, response: Optional[str], action: str) -> bool:
        if not response or not response.strip():
            return False
        try:
            document = ET.fromstring(response)
        except ET.ParseError:
            return False

        command = document.find(f".//command[@action='{action}']/result")
        if command is not None and command.text == "1":
            return True
        root_result = document.find("result")
        return root_result is not None and root_result.text == "1"

    def _build_command_xml(self, action: str, inner_xml: str = "") -> tuple[str, str]:
        self._xml_command_id += 1
        command_id = str(self._xml_command_id)
        cmd_xml = (
            f'<command id="{command_id}" action="{action}"/>'
            if not inner_xml
            else f'<command id="{command_id}" action="{action}">{inner_xml}</command>'
        )
        return self._build_polling_commands(cmd_xml), command_id

    def _build_batched_commands_xml(self, *commands: str) -> tuple[str, str]:
        self._xml_command_id += 1
        command_id = str(self._xml_command_id)
        commands_xml = "".join(f'<command id="{command_id}" action="{action}"/>' for action in commands)
        return self._build_polling_commands(commands_xml), command_id

    def _build_polling_commands(self, commands: str) -> str:
        return build_polling_xml(self.identifier, self._version, self._panel_report_id, self._panel_sensor_mod, commands)

    @staticmethod
    def build_probe_xml(mac: str) -> str:
        return build_polling_xml(mac, None, None, 0, '<command id="1" action="getVer"/>')


def build_polling_xml(mac: str, version: Optional[str], report_id: Optional[str], sensor_mod: int, commands: str) -> str:
    xml_mac = _format_xml_mac(mac)
    report_id = report_id or ""
    ver = version or ""

    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        f'<polling><mac value="{xml_escape(xml_mac)}"/>'
        f'<sn value="{xml_escape(xml_mac)}"/>'
        f'<ver value="{xml_escape(ver)}"/><rptipid value="{xml_escape(report_id)}"/>'
        f'<sensormod value="{sensor_mod}"/><commands>{commands}</commands></polling>'
    )


def _format_xml_mac(mac: str) -> str:
    return ":".join(mac[i : i + 2] for i in range(0, len(mac), 2)).upper()


def _parse_xml_device_type(value: Optional[str]) -> DeviceType:
    if value is not None and value.isdigit():
        return _map_xml_device_type(int(value))

    lowered = (value or "").strip().lower()
    return {
        "remote controllor": DeviceType.KeyFob,
        "remote controller": DeviceType.KeyFob,
        "pendant": DeviceType.KeyFob,
        "wrist tx": DeviceType.KeyFob,
        "door contact": DeviceType.DoorContact,
        "ir sensor": DeviceType.PIR,
        "remote keypad": DeviceType.Keypad,
        "ir camera": DeviceType.PirCamera,
        "siren": DeviceType.Siren,
    }.get(lowered, DeviceType.Unknown)


def _map_xml_device_type(type_code: int) -> DeviceType:
    return {
        0: DeviceType.KeyFob,
        1: DeviceType.DoorContact,
        3: DeviceType.PIR,
        7: DeviceType.Keypad,
        33: DeviceType.PirCamera,  # Never seen one of these successfully pair to the CTC.
    }.get(type_code, DeviceType.Unknown)


def _map_xml_device_attribute(value: int) -> DeviceAttribute:
    return {
        1: DeviceAttribute.Perimeter, # "Burglar"
        2: DeviceAttribute.Interior,  # "Home Omit"
        3: DeviceAttribute.HomeDelay, # "Home Access"
        4: DeviceAttribute.Entry1,    # "Entry Zone"
    }.get(value, DeviceAttribute.Unset)


def _map_to_xml_device_attribute(attribute: DeviceAttribute) -> Optional[int]:
    return {
        DeviceAttribute.Perimeter: 1,
        DeviceAttribute.Interior: 2,
        DeviceAttribute.HomeDelay: 3,
        DeviceAttribute.Entry1: 4,
    }.get(attribute)


def _utc_now():
    import datetime

    return datetime.datetime.utcnow()
