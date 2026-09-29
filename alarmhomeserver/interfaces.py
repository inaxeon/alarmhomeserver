"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
interfaces.py:
ABC/Protocol definitions for PanelTransport and Panel
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

import abc
import datetime
from typing import Callable, Optional, Protocol

from alarmhomeserver.models.alarm import (
    Device,
    DeviceAttribute,
    DeviceState,
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

# i.e. XMPP transport or HTTP transport
class PanelTransport(Protocol):
    ip_address: Optional[str]

    def send_command(
        self, body: str, context_handle: str, timeout_seconds: float, cancel_event: Optional[object] = None
    ) -> Optional[str]:
        ...

    def get_mac_address(self) -> Optional[str]:
        ...

    def stop(self) -> None:
        ...

    def set_message_received(self, callback: Callable[[str, bool], None]) -> None:
        ...

# i.e. ML or CTC
class Panel(abc.ABC):
    backend_config: PanelBackendConfig
    identifier: str
    version: Optional[str]
    mode: PanelMode
    type: PanelType
    ip_address: Optional[str]
    is_ready: bool
    devices: list[Device]
    device_states: list[DeviceState]
    users: list[PanelUser]
    last_mode_change_user: int
    max_device_name_length: int
    max_signal_strength: int

    @abc.abstractmethod
    def get_history(self) -> list[HistoryEntry]:
        ...

    @abc.abstractmethod
    def set_mode(self, mode: PanelMode, user: int) -> tuple[bool, bool]:
        """Returns (success, door_open)."""

    @abc.abstractmethod
    def set_user(self, index: int, name: str, pin: str, latch: bool) -> bool:
        ...

    @abc.abstractmethod
    def add_user(self, name: str, pin: str, latch: bool) -> int:
        ...

    @abc.abstractmethod
    def delete_user(self, index: int) -> bool:
        ...

    @abc.abstractmethod
    def start_walk_test(self, notifier: Callable[[WalkNotification], None], context: object) -> bool:
        ...

    @abc.abstractmethod
    def stop_walk_test(self) -> bool:
        ...

    @abc.abstractmethod
    def start_learn(self, notifier: Callable[[LearnedDevice], None], context: object) -> bool:
        ...

    @abc.abstractmethod
    def stop_learn(self) -> bool:
        ...

    @abc.abstractmethod
    def add_learned_device(self, new_device: Optional[LearnedDevice], name: str) -> bool:
        ...

    @abc.abstractmethod
    def refresh_devices(self) -> bool:
        ...

    @abc.abstractmethod
    def delete_device(self, index: int) -> bool:
        ...

    @abc.abstractmethod
    def set_device(
        self, index: int, name: str, attribute: Optional[DeviceAttribute], bypass: Optional[bool], chime: Optional[bool]
    ) -> bool:
        ...

    @abc.abstractmethod
    def request_media(self, device: int, flash: bool) -> Optional[str]:
        ...

    @abc.abstractmethod
    def set_power_switch(self, index: int, on: bool) -> Optional[str]:
        ...

    @abc.abstractmethod
    def get_system_config(self) -> Optional[SystemConfiguration]:
        ...

    @abc.abstractmethod
    def set_away_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        ...

    @abc.abstractmethod
    def set_home_arm_config(self, entry_delay: int, exit_delay: int, entry_delay_sound: int, exit_delay_sound: int) -> bool:
        ...

    @abc.abstractmethod
    def set_general_config(self, door_contact_sound: int, supervision: int, siren_length: int) -> bool:
        ...

    @abc.abstractmethod
    def send_siren_configuration(self, setting: SirenSetting, value: int) -> bool:
        ...

    @abc.abstractmethod
    def clear_device_open(self, index: int) -> bool:
        ...

