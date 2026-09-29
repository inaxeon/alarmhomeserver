"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
base_panel.py:
Common panel implementation
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
import logging
from typing import Optional

from alarmhomeserver.interfaces import Panel, PanelTransport
from alarmhomeserver.models.alarm import (
    Device,
    DeviceState,
    DeviceStatus,
    PanelBackendConfig,
    PanelMode,
    PanelType,
    PanelUser,
    SystemConfiguration,
)


class BasePanel(Panel):
    def __init__(self, logger: logging.Logger, transport: PanelTransport, backend_config: PanelBackendConfig):
        self.logger = logger
        self.transport = transport
        self.backend_config = backend_config

        self._mode: PanelMode = PanelMode.Disarm
        self._type: PanelType = PanelType.Unknown
        self._version: Optional[str] = None
        self._users: list[PanelUser] = []
        self._devices: list[Device] = []
        self._device_states: list[DeviceState] = []
        self._config = SystemConfiguration()
        self._last_mode_change_command: Optional[datetime.datetime] = None
        self._last_mode_change_user: int = 0
        self._alarm_data_loaded = False

    @property
    def identifier(self) -> str:
        return self.transport.get_mac_address() or ""

    @property
    def version(self) -> Optional[str]:
        return self._version

    @property
    def ip_address(self) -> Optional[str]:
        return self.transport.ip_address

    @property
    def is_ready(self) -> bool:
        return self._alarm_data_loaded

    @property
    def mode(self) -> PanelMode:
        return self._mode

    @property
    def type(self) -> PanelType:
        return self._type

    @property
    def devices(self) -> list[Device]:
        return self._devices

    @property
    def users(self) -> list[PanelUser]:
        return self._users

    @property
    def device_states(self) -> list[DeviceState]:
        return self._device_states

    @property
    def last_mode_change_user(self) -> int:
        # Only "fresh" (recent) mode changes are attributed - otherwise this is likely not the
        # person who performed the last actual mode change.
        if self._last_mode_change_user == 0 or self._last_mode_change_command is None:
            return 0

        elapsed = (datetime.datetime.utcnow() - self._last_mode_change_command).total_seconds()
        window = max(self._config.home_arm_exit_delay, self._config.away_arm_exit_delay) + 10

        return self._last_mode_change_user if elapsed < window else 0

    @property
    @abc.abstractmethod
    def max_device_name_length(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def max_signal_strength(self) -> int:
        ...

    @abc.abstractmethod
    def initialise(self) -> bool:
        ...

    def clear_device_open(self, index: int) -> bool:
        # NOTE: This only updates the state stored in this service. We can't tell the alarm to override its definition of "open".
        state = next((s for s in self._device_states if s.device and s.device.index == index), None)

        if state is None:
            self.log_warning(f"No device state held for index {index}")
            return False

        state.status &= ~DeviceStatus.Open
        return True

    def close_connection(self) -> None:
        self.transport.stop()

    def log_info(self, message: str) -> None:
        self.logger.info("Alarm '%s': %s", self.identifier, message)

    def log_warning(self, message: str) -> None:
        self.logger.warning("Alarm '%s': %s", self.identifier, message)

    def log_error(self, message: str) -> None:
        self.logger.error("Alarm '%s': %s", self.identifier, message)
