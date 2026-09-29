"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
ml_panel.py:

Implementation for "ML" protocol alarms. I don't know if this is the official
name but alarms using this protocol always have the letters "ML" stamped on the
PCB. "ML" also appears in the identifier for the firmware i.e. ML_yaukoz.

ML Alarms use the same outer XMPP protocol as CTC alarms but the inner protocol
is utterly different.

ML type alarms are substantially different to CTC alarms. Where the CTC models
are Linux based running on massive SoCs armed with oodles of flash and RAM;
ML alarms run on small ARM SoCs i.e. Kinetis K60/K64 with 512KB-1MB of flash.
They're probably running the alarm application as a monolith combined with
some kind of RTOS.

This project (for now) focuses on one model: The original Yale badged MZ-1
which is based on the K60 MCU and can be identifed by checking for:

1) SKU "EF-IPBOX"
2) "V6" sticker on the rear
3) "V1.1" sticker on the side
4) "MAC Address" sticker on the side (important!)
5) MCU inside labelled "ML_yaukoz 0.0.1.23A" (it will be if the above are true)

If it has a "Serial Number" sticker on the side rather than "MAC Address"
then it is an MZ-1-K64 which runs a firmware I have nicknamed "big yaukoz"
because it only runs from a larger K64 MCU. I presume the larger part is
selected because it includes an SSL library as it will only communicate with
the server via XMPP-TLS, with strict certificate checking scuppering any
possibility of compatibility with this service.

Newer "ML_yamga" alarms (SR-HUB, IA-HUB) also use the exact same protocol and
employ the same strict SSL certificate enforcement thus it is not possible to
use them with this project or snoop the protcol directly. It was found that all
exchanges with the server are spat out in plaintext from an RS-232 debug header
on the PCB.

The Yale SR-HUB also identifies its self as "MZ-1". The hardware appears to be
very similar to the MZ-1-K64 just squashed into a smaller form factor.

The Yale "Sync" hub (IA-HUB) identifies its self as "MR-1" instead of "MZ-1".
The hardware *also* looks very similar to the MZ-1 albiet squashed into an
-even smaller- form factor. Current firmware (1.0.3.4V) no longer churns out
plaintext XMPP comms from the RS-232 debug port.

Code readout protection is enabled on all hubs except the K60 MZ-1.
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

import datetime
import enum
import logging
import random
import string
import threading
from typing import Callable, Optional

from alarmhomeserver.interfaces import PanelTransport
from alarmhomeserver.models.alarm import (
    Device,
    DeviceAttribute,
    DeviceFlags,
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
from alarmhomeserver.models.history_descriptions import HistoryEntry, HistoryEventType
from alarmhomeserver.panels.base_panel import BasePanel

PROTOCOL_TIMEOUT_SECONDS = 20.0  # Extra long delay because request media can take ages.
HISTORY_DATE_FORMAT = "%Y/%m/%d_%H:%M:%S"
MAX_USERS = 10


class CommandFailureReason(enum.Enum):
    NONE = "None"
    BUSY = "Busy"
    TIMEOUT = "Timeout"
    ERROR = "Error"

    @staticmethod
    def parse(value: str) -> Optional["CommandFailureReason"]:
        for member in CommandFailureReason:
            if member.value.lower() == value.lower():
                return member
        return None


class MlPanel(BasePanel):
    def __init__(
        self,
        logger: logging.Logger,
        transport: PanelTransport,
        backend_config: PanelBackendConfig,
        first_message: Optional[str],
    ):
        super().__init__(logger, transport, backend_config)
        self._first_message = first_message
        self._random = random.Random()
        self._sequence_number = bytes(5)
        self._initialised_event = threading.Event()
        self._wait_devices_event: Optional[threading.Event] = None
        self._user_slots: list[PanelUser] = []

        self._special_state_context: object = None
        self._walk_test_notifier: Optional[Callable[[WalkNotification], None]] = None
        self._learned_device_notifier: Optional[Callable[[LearnedDevice], None]] = None
        self._last_learned_device: Optional[LearnedDevice] = None
        self._mode_change_timer: Optional[threading.Timer] = None

        transport.set_message_received(self._on_message_received)

    @property
    def max_device_name_length(self) -> int:
        return 26

    @property
    def max_signal_strength(self) -> int:
        return 9

    def initialise(self) -> bool:
        self._devices = []
        self._users = []
        self._device_states = []
        self._sequence_number = bytes(5)
        self._alarm_data_loaded = False
        self._config = SystemConfiguration()
        self._last_mode_change_user = 0

        if self._first_message:
            self._handle_alarm_message(self._first_message)

        threading.Thread(target=self._load_panel_data_safe, daemon=True).start()
        self._initialised_event.wait(10)

        return self._alarm_data_loaded

    def _load_panel_data_safe(self) -> None:
        try:
            self._load_panel_data()
        except Exception:
            self.logger.exception("Failure loading alarm data")

    def get_history(self) -> list[HistoryEntry]:
        results: list[HistoryEntry] = []

        # Also get the time the alarm is set to internally, in case it's wrong (usually is for a
        # bit after power-on).
        _, dtz_result, _ = self._send_command("DTZ?", None)
        alarm_time_now = None
        if dtz_result:
            dtz_parts = dtz_result[0].split(",")
            alarm_time_now = datetime.datetime.strptime(dtz_parts[0], "%Y/%m/%d_%H:%M:%S")

        _, hst_result, _ = self._send_command("HST?", None)
        for line in hst_result or []:
            parts = line.split(",")
            user_index = int(parts[5])
            device_index = int(parts[4])

            results.append(
                HistoryEntry(
                    type=HistoryEventType(int(parts[6])),
                    alarm_clock_when_queried=alarm_time_now,
                    when_occurred=datetime.datetime.strptime(parts[2], HISTORY_DATE_FORMAT),
                    user=next((u for u in self._users if u.index == user_index), None) if user_index > 0 else None,
                    device=next((d for d in self._devices if d.index == device_index), None) if device_index > 0 else None,
                )
            )

        return results

    def set_mode(self, mode: PanelMode, user: int) -> tuple[bool, bool]:
        if mode in (PanelMode.AwayArmInProgress, PanelMode.HomeArmInProgress):
            self.log_error(f"Not in correct mode to change to: {mode}")
            return False, False

        # MODA doesn't accept a user parameter, so we track it separately for CidService.
        self._last_mode_change_user = user
        self._last_mode_change_command = datetime.datetime.utcnow()

        reason, _, status_text = self._send_command("MODA", f"1,{int(mode)}")

        if reason != CommandFailureReason.NONE:
            self.log_error(f"SetMode({mode}) failed")
            return False, False

        # "DC open" is the only non-OK status seen in practice - the mode still applies, but
        # flags that a door/window contact is open.
        door_open = (status_text or "").strip().lower() == "dc open"

        if status_text and not door_open:
            self.log_warning(f"Panel reported unrecognised status '{status_text}' while setting mode to {mode}")

        self.log_info(f"Set mode to: {mode}")
        return True, door_open

    def set_user(self, index: int, name: str, pin: str, latch: bool) -> bool:
        existing = next((u for u in self._users if u.index == index), None)
        if existing is None:
            self.log_error(f"No user at index {index} to edit")
            return False

        if not self._write_user(existing, name, pin, latch):
            return False

        self.log_info(f"Edited user {index} ('{name}')")
        return True

    def add_user(self, name: str, pin: str, latch: bool) -> int:
        free = next((u for u in self._user_slots if not (u.name or "").strip() and not (u.pin or "").strip()), None)
        if free is None:
            self.log_error("There are no free user slots")
            return -1

        slot_index = free.index
        if not self._write_user(free, name, pin, latch):
            return -1

        self.log_info(f"Added user '{name}' at slot {slot_index}")
        return slot_index

    def delete_user(self, index: int) -> bool:
        existing = next((u for u in self._users if u.index == index), None)
        if existing is None:
            self.log_error(f"No user at index {index} to delete")
            return False

        reason, _, _ = self._send_command("USRD", f"1,{existing.index}")
        if reason != CommandFailureReason.NONE:
            self.log_error(f"Failed to delete user {index}")
            return False

        reason, users, _ = self._send_command("USR?", None)
        if reason == CommandFailureReason.NONE and users:
            self._handle_users(users)

        self.log_info(f"Deleted user {index} ('{existing.name}')")
        return True

    def _write_user(self, slot: PanelUser, name: str, pin: str, latch: bool) -> bool:
        if _contains_delimiter(name) or _contains_delimiter(pin):
            self.log_error("User name and PIN cannot contain ',', ';' or ':'")
            return False

        reason, _, _ = self._send_command("USRS", f"1,{slot.index},{pin},{name},{1 if latch else 0}")
        if reason != CommandFailureReason.NONE:
            self.log_error(f"Panel rejected update of user {slot.index}. PIN may be in use by another user.")
            return False

        reason, users, _ = self._send_command("USR?", None)
        if reason == CommandFailureReason.NONE and users:
            self._handle_users(users)

        return True

    def add_learned_device(self, new_device: Optional[LearnedDevice], name: str) -> bool:
        new_device = new_device or self._last_learned_device
        if new_device is None:
            self.log_error("No learned device to add")
            return False

        self._wait_devices_event = threading.Event()
        reason, _, _ = self._send_command("DLRA", new_device.radio_identifier)

        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to add device")
            self._wait_devices_event = None
            return False

        # The alarm will now send an updated devices list - wait for it.
        self._wait_devices_event.wait(5)
        self._wait_devices_event = None

        device = next((d for d in self._devices if d.radio_identifier == new_device.radio_identifier), None)
        if device is None:
            self.log_error("Newly added device was not returned by panel")
            return False

        if not self.set_device(device.index, name, None, None, None):
            self.log_warning(f"Device added at index {device.index} but set name failed")

        return True

    def set_device(
        self, index: int, name: str, attribute: Optional[DeviceAttribute], bypass: Optional[bool], chime: Optional[bool]
    ) -> bool:
        device = next((d for d in self._devices if d.index == index), None)
        if device is None:
            self.log_error(f"No device at index {index}")
            return False

        if _contains_delimiter(name):
            self.log_error("Device names cannot contain ',', ';' or ':'")
            return False

        # Wire protocol expects the numeric code (matches how DEW?/DEM parse it back), not the name.
        attribute_str = str(int(attribute)) if attribute is not None else ""

        flags = device.flags
        if bypass is not None:
            flags = (flags | DeviceFlags.Bypass) if bypass else (flags & ~DeviceFlags.Bypass)
        if chime is not None:
            flags = (flags | DeviceFlags.Chime) if chime else (flags & ~DeviceFlags.Chime)

        reason, _, _ = self._send_command("DEED", f"1,{index},,,{name},{int(flags):02X},{attribute_str},,")
        if reason != CommandFailureReason.NONE:
            self.log_error(f"Failed to save the device at index {index}")
            return False

        reason, devices, _ = self._send_command("DEW?", None)
        if reason == CommandFailureReason.NONE and devices:
            self._handle_devices(devices)

        return True

    def delete_device(self, index: int) -> bool:
        device = next((d for d in self._devices if d.index == index), None)
        if device is None:
            self.log_error(f"No device at index {index} to delete")
            return False

        radio_identifier = device.radio_identifier

        reason, _, _ = self._send_command("DEVD", f"{index}")
        if reason != CommandFailureReason.NONE:
            self.log_error(f"Failed to delete the device at index {index}")
            return False

        reason, devices, _ = self._send_command("DEW?", None)
        if reason == CommandFailureReason.NONE and devices:
            self._handle_devices(devices)

        if any(d.radio_identifier == radio_identifier for d in self._devices):
            self.log_error(f"The panel still lists {radio_identifier} after deleting index {index}")
            return False

        self.log_info(f"Deleted the device at index {index} ({radio_identifier})")
        return True

    def start_walk_test(self, notifier: Callable[[WalkNotification], None], context: object) -> bool:
        reason, _, _ = self._send_command("DWKS", "1")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to start walk test")
            return False

        self._special_state_context = context
        self._walk_test_notifier = notifier
        self.log_info("Walk test started")
        return True

    def stop_walk_test(self) -> bool:
        if self._walk_test_notifier is None:
            return True

        reason, _, _ = self._send_command("DWKS", "0")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to stop walk test")
            return False

        self._special_state_context = None
        self._walk_test_notifier = None
        return True

    def start_learn(self, notifier: Callable[[LearnedDevice], None], context: object) -> bool:
        reason, _, _ = self._send_command("DLRS", "1")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to enter learn mode")
            return False

        self._special_state_context = context
        self._learned_device_notifier = notifier
        self.log_info("Entered learn mode")
        return True

    def stop_learn(self) -> bool:
        if self._learned_device_notifier is None:
            return True

        reason, _, _ = self._send_command("DLRS", "0")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to exit learn mode")
            return False

        self._special_state_context = None
        self._learned_device_notifier = None
        self.log_info("Exited learn mode")
        return True

    def request_media(self, device: int, flash: bool) -> Optional[str]:
        dev = next((d for d in self._devices if d.index == device), None)
        if dev is None:
            return f"No device at index {device}"
        if dev.type != DeviceType.PirCamera:
            return f"Device at index {device} is not a camera"

        reason, _, _ = self._send_command("RQMD", f"1,{dev.index},{0 if flash else 1}")
        if reason != CommandFailureReason.NONE:
            message = {
                CommandFailureReason.BUSY: "The camera is busy - try again shortly",
            }.get(reason, "Failed to request media")
            self.log_error(message)
            return message
        return None

    def set_power_switch(self, index: int, on: bool) -> Optional[str]:
        device = next((d for d in self._devices if d.index == index), None)
        if device is None:
            return f"No device at index {index}"
        if device.type != DeviceType.PowerSwitch:
            return f"Device at index {index} is not a power switch"

        reason, _, _ = self._send_command("PSSW", f"1,{index},{1 if on else 0}")
        if reason != CommandFailureReason.NONE:
            message = {
                CommandFailureReason.BUSY: "Power switch is busy",
                CommandFailureReason.TIMEOUT: "Hardware timeout setting power switch state",
            }.get(reason, "Failed to set power switch state")
            self.log_error(message)
            return message

        state = next((s for s in self._device_states if s.device and s.device.index == index), None)
        if state is None:
            state = DeviceState(device=device)
            self._device_states.append(state)
        state.power_on = on

        self.log_info(f"Set Power Switch '{device.name}' to {'On' if on else 'Off'}")
        return None

    def get_system_config(self) -> Optional[SystemConfiguration]:
        """We don't load system config on start-up; this loads it on demand."""
        reason, delays, _ = self._send_command("ATM?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get delays")
            return None
        self._handle_delays(delays)

        reason, sounds, _ = self._send_command("ASN?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get sounds")
            return None
        self._handle_sounds(sounds)

        reason, sup, _ = self._send_command("ARA?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get supervision/siren length")
            return None
        self._handle_supervision_and_siren(sup)

        return self._config

    def set_away_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        if self.get_system_config() is None:
            self.log_error("Failed to read current Away Arm configuration")
            return False

        reason, _, _ = self._send_command("ATMR", f"1,{entry_delay},,,,{exit_delay},,,,,,")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save Away Arm delays")
            return False

        reason, _, _ = self._send_command("ASND", f"1,,{entry_delay_sound},,{exit_delay_sound},,,,")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save Away Arm sounds")
            return False

        self.log_info("Saved Away Arm configuration")
        return True

    def set_home_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        if self.get_system_config() is None:
            self.log_error("Failed to read current Home Arm configuration")
            return False

        reason, _, _ = self._send_command("ATMR", f"1,,{entry_delay},,,,{exit_delay},,,,,")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save the Home Arm delays")
            return False

        reason, _, _ = self._send_command("ASND", f"1,,,{entry_delay_sound},,{exit_delay_sound},,,")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save the Home Arm sounds")
            return False

        self.log_info("Saved Home Arm configuration")
        return True

    def set_general_config(self, door_contact_sound: int, supervision: int, siren_length: int) -> bool:
        if self.get_system_config() is None:
            self.log_error("Failed to read current General configuration")
            return False

        reason, _, _ = self._send_command("ASND", f"1,{door_contact_sound},,,,,,,")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save the chime sound")
            return False

        reason, _, _ = self._send_command("ARAS", f"1,{supervision},,,,{siren_length},")
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to save the supervision/siren length settings")
            return False

        self.log_info("Saved General configuration")
        return True

    def send_siren_configuration(self, setting: SirenSetting, value: int) -> bool:
        code = {
            SirenSetting.ComfortLed: "M",
            SirenSetting.TamperDetection: "T",
            SirenSetting.EntryExitConfirm: "C",
        }.get(setting)
        if code is None:
            raise ValueError(f"Unsupported siren setting: {setting}")

        reason, _, _ = self._send_command("SRTX", f"{code},{value}")
        if reason != CommandFailureReason.NONE:
            self.log_error(f"Failed to send the {setting} message")
            return False

        self.log_info(f"Set siren {setting} to {value}")
        return True

    def refresh_devices(self) -> bool:
        return True

    # Message handling

    def _on_message_received(self, message: str, is_xml: bool) -> None:
        if is_xml:
            raise ValueError("ML Panel should never receive XML payloads")

        # Do not try to communicate with the alarm in this callback else it will lock the stream
        # up - do it on a new thread.
        threading.Thread(target=self._handle_alarm_message_safe, args=(message,), daemon=True).start()

    def _handle_alarm_message_safe(self, message: str) -> None:
        try:
            self._handle_alarm_message(message)
        except Exception:
            self.logger.exception("Failure processing message")

    def _handle_alarm_message(self, message: str) -> None:
        self.log_info(f"Received message: {message}")
        args, cmd = _parse_message_args(message)

        handlers = {
            "PNL": self._handle_panel,
            "VER": lambda a: self._handle_version(a, False),
            "MOE": lambda a: self._handle_mode(a, True),
            "ATM": self._handle_delays,
            "USR": self._handle_users,
            "DEW": self._handle_devices,
            "DES": self._handle_states,
            "DEN": self._handle_device_event,
            "DEM": self._handle_device_edit,
            "DWK": self._handle_walk,
            "DLR": self._handle_learn,
        }
        handler = handlers.get(cmd)
        if handler:
            handler(args)

    def _load_panel_data(self) -> None:
        # We're quite demanding in this service: The real server probably keeps a lot
        # of this in an RDBMS, but we ask for everything on every (re)connect.
        reason, result, _ = self._send_command("VER?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get version")
            return
        self._handle_version(result, True)

        reason, result, _ = self._send_command("ATM?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get delays")
            return
        self._handle_delays(result)

        reason, result, _ = self._send_command("USR?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get users")
            return
        self._handle_users(result)

        reason, result, _ = self._send_command("DEW?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get devices")
            return
        self._handle_devices(result)

        if self._devices:
            reason, result, _ = self._send_command("DES?", None)
            if reason != CommandFailureReason.NONE:
                self.log_error("Failed to get states")
                return
            self._handle_states(result)

        reason, result, _ = self._send_command("MOE?", None)
        if reason != CommandFailureReason.NONE:
            self.log_error("Failed to get mode")
            return
        self._handle_mode(result, False)

        self._alarm_data_loaded = True
        self._initialised_event.set()

    def _handle_users(self, args: list[str]) -> None:
        slots = []
        for line in args:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            slots.append(PanelUser(index=int(parts[0]), pin=parts[4], name=parts[5], latch=parts[6] == "1"))

        self._user_slots = [u for u in slots if u.index <= MAX_USERS]
        self._users = [u for u in slots if (u.name or "").strip()]
        self.log_info(f"{len(self._users)} User(s) loaded")

    def _handle_panel(self, args: list[str]) -> None:
        if not self._alarm_data_loaded:
            self.log_info("Resuming previous session")

    def _handle_version(self, args: list[str], is_reply: bool) -> None:
        if args[0].startswith("MZ-1_G"):
            self._type = PanelType.ML_yaukoz

        if not self._alarm_data_loaded and not is_reply:
            self.log_info("Starting new session")

        self._version = args[0].split(",")[0]
        self.log_info(f"Version: {self._version}")

    def _handle_walk(self, args: list[str]) -> None:
        parts = args[0].split(",")
        if len(parts) < 6:
            return

        device = next((d for d in self._devices if d.index == int(parts[4])), None)
        self.log_info(f"Walk test device notification: '{device and device.name}'")

        if self._walk_test_notifier:
            self._walk_test_notifier(
                WalkNotification(alarm=self, context=self._special_state_context, device=device, rssi=int(parts[5]))
            )

    def _handle_learn(self, args: list[str]) -> None:
        parts = args[0].split(",")
        learned_device = LearnedDevice(
            alarm=self, context=self._special_state_context, radio_identifier=parts[3], type=DeviceType(int(parts[5]))
        )
        self.log_info(f"Learn: {learned_device.type} found ({learned_device.radio_identifier})")

        self._last_learned_device = learned_device
        if self._learned_device_notifier:
            self._learned_device_notifier(learned_device)

    def _handle_mode(self, args: list[str], realtime: bool) -> None:
        parts = args[0].split(",")
        mode = PanelMode(int(parts[1]))

        # We get this immediately after the mode is changed - approximately track the "in
        # progress" arming state too, since there's no message for when it actually finishes.
        if realtime:
            if self._mode_change_timer is not None:
                self._mode_change_timer.cancel()
                self._mode_change_timer = None

            if mode == PanelMode.HomeArm:
                self._mode = PanelMode.HomeArmInProgress
                self._mode_change_timer = threading.Timer(
                    self._config.home_arm_exit_delay, self._finish_mode_change, args=(PanelMode.HomeArm,)
                )
                self._mode_change_timer.daemon = True
                self._mode_change_timer.start()
            elif mode == PanelMode.AwayArm:
                self._mode = PanelMode.AwayArmInProgress
                self._mode_change_timer = threading.Timer(
                    self._config.away_arm_exit_delay, self._finish_mode_change, args=(PanelMode.AwayArm,)
                )
                self._mode_change_timer.daemon = True
                self._mode_change_timer.start()
            else:
                self._mode = mode
        else:
            self._mode = mode

        self.log_info(f"Mode changed to {self._mode}")

    def _finish_mode_change(self, mode: PanelMode) -> None:
        self._mode = mode
        self._mode_change_timer = None
        self.log_info(f"Mode changed to {self._mode}")

    def _handle_delays(self, args: list[str]) -> None:
        parts = args[0].split(",")
        self._config.away_arm_entry_delay = int(parts[1])
        self._config.home_arm_entry_delay = int(parts[2])
        self._config.away_arm_exit_delay = int(parts[5])
        self._config.home_arm_exit_delay = int(parts[6])
        self.log_info("Delays loaded")

    def _handle_sounds(self, args: list[str]) -> None:
        parts = args[0].split(",")
        self._config.door_contact_sound = int(parts[1])
        self._config.away_arm_entry_delay_sound = int(parts[2])
        self._config.home_arm_entry_delay_sound = int(parts[3])
        self._config.away_arm_exit_delay_sound = int(parts[4])
        self._config.home_arm_exit_delay_sound = int(parts[5])
        self._config.hub_warning_sound = int(parts[6])
        self.log_info("Sounds loaded")

    def _handle_supervision_and_siren(self, args: list[str]) -> None:
        parts = args[0].split(",")
        self._config.supervision = int(parts[2])
        self._config.siren_length = int(parts[6])
        self.log_info("Supervision/siren length loaded")

    def _handle_devices(self, args: list[str]) -> None:
        devices = []
        for line in args:
            parts = line.split(",")
            if len(parts) == 2 and parts[0] == "0" and parts[1] == "0":
                self.log_warning("Has no devices")
                self._devices = devices
                if self._wait_devices_event:
                    self._wait_devices_event.set()
                return

            device = Device(
                index=int(parts[0]),
                type=DeviceType(int(parts[6])),
                name=parts[7],
                flags=DeviceFlags(int(parts[8], 16)),
                radio_identifier=parts[4],
                attribute=DeviceAttribute(int(parts[9])),
            )
            device.bypass = bool(device.flags & DeviceFlags.Bypass)
            devices.append(device)

        self._devices = devices
        self.log_info(f"{len(self._devices)} Device(s) loaded")

        if self._wait_devices_event:
            self._wait_devices_event.set()

    def _handle_states(self, args: list[str]) -> None:
        states = []
        for line in args:
            parts = line.split(",")
            if len(parts) == 2 and parts[0] == "0" and parts[1] == "0":
                self.log_warning("Has no states")
                self._device_states = states
                return

            dev = next((d for d in self._devices if d.index == int(parts[0])), None)
            if dev is not None:
                status = DeviceStatus(int(parts[4], 16))
                # Only some device types (e.g. PowerSwitch, DoorContact) report the extra
                # trailing block with a "last changed" timestamp at index 6.
                timestamp = parts[6] if len(parts) > 6 else None
                states.append(
                    DeviceState(
                        device=dev,
                        status=status,
                        status_last_changed=(
                            datetime.datetime.strptime(timestamp, HISTORY_DATE_FORMAT) if timestamp else None
                        ),
                        power_on=dev.type == DeviceType.PowerSwitch and bool(status & DeviceStatus.On),
                    )
                )

        self._device_states = states
        self.log_info(f"{len(self._device_states)} Device state(s) loaded")

    def _handle_device_event(self, args: list[str]) -> None:
        parts = args[0].split(",")
        existing = next((s for s in self._device_states if s.device and s.device.index == int(parts[1])), None)

        if existing is None:
            existing = DeviceState(device=next((d for d in self._devices if d.index == int(parts[1])), None))
            self._device_states.append(existing)

        existing.status = DeviceStatus(int(parts[2], 16))
        if len(parts) >= 5:
            existing.status_last_changed = datetime.datetime.strptime(parts[4], HISTORY_DATE_FORMAT)

        if existing.device and existing.device.type == DeviceType.PowerSwitch:
            existing.power_on = bool(existing.status & DeviceStatus.On)

        self.log_info(f"Realtime update of status for device: '{existing.device and existing.device.name}' to '{existing.status}'")

    def _handle_device_edit(self, args: list[str]) -> None:
        parts = args[0].split(",")
        device = next((d for d in self._devices if d.index == int(parts[1])), None)

        if device is not None:
            device.name = parts[5]
            device.flags = DeviceFlags(int(parts[6], 16))
            device.attribute = DeviceAttribute(int(parts[7]))
            device.bypass = bool(device.flags & DeviceFlags.Bypass)

        self.log_info(f"New device settings received for {device and device.type} '{device and device.name}'")

    def _send_command(self, command: str, parameters: Optional[str]) -> tuple[CommandFailureReason, Optional[list[str]], Optional[str]]:
        full_command = f":;{command}:{parameters or ''}"
        token = self._generate_token()
        response = self.transport.send_command(f"{token}{full_command}", token, PROTOCOL_TIMEOUT_SECONDS)

        if response is None or not response.startswith("OK;"):
            self.log_error(f"Command '{command}' failed. Result: {response}")
            return CommandFailureReason.ERROR, None, None

        message = response[3:]
        prefix = f"{command}:"
        status_text = None

        if message.startswith(prefix):
            status = message[len(prefix) :].rstrip(";")
            rejection_reason = CommandFailureReason.parse(status)
            if rejection_reason is not None:
                if rejection_reason != CommandFailureReason.NONE:
                    return rejection_reason, None, None
            elif status.lower() != "ok":
                status_text = status

        result, _ = _parse_message_args(message)
        return CommandFailureReason.NONE, result, status_text

    def _generate_token(self) -> str:
        digits = "".join(self._random.choice(string.digits) for _ in range(19))
        letters = "".join(self._random.choice(string.ascii_letters) for _ in range(13))
        return digits + letters

def _parse_message_args(message: str) -> tuple[list[str], str]:
    cmd_parsed = message.split("?")[0]
    separator = cmd_parsed + "?"
    parts = [p for p in message.split(separator) if p]
    return [p.lstrip(":").rstrip(";") for p in parts], cmd_parsed


def _contains_delimiter(value: Optional[str]) -> bool:
    return value is not None and any(c in value for c in (",", ";", ":", "\r", "\n"))
