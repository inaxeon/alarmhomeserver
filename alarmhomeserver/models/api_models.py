"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
api_models.py:
REST API models for Web UI
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
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from alarmhomeserver.models.alarm import (
    Device,
    DeviceAttribute,
    DeviceState,
    DeviceType,
    PanelMode,
    PanelType,
    SirenSetting,
)
from alarmhomeserver.models.history_descriptions import HistoryEventType


def _by_name_enum(enum_cls):
    # Builds a friendly name for an enum value
    def _parse(value):
        if isinstance(value, enum_cls):
            return value
        if isinstance(value, str):
            try:
                return enum_cls[value]
            except KeyError:
                pass
        return enum_cls(value)

    return Annotated[enum_cls, BeforeValidator(_parse), PlainSerializer(lambda v: v.name, return_type=str)]


class ApiModel(BaseModel):
    # Convert Python snake_case to camelCase
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AlarmSummaryDto(ApiModel):
    identifier: str
    friendly_name: Optional[str] = None
    version: Optional[str] = None
    ip_address: Optional[str] = None
    mode: _by_name_enum(PanelMode)
    type: PanelType
    last_mode_change_user: int
    max_device_name_length: int
    # Whether this panel talks over the HTTP polling transport (CTC_Polling), where a live query
    # can take a while - the frontend uses this to avoid auto-loading slow panes.
    is_polling_alarm: bool
    # Whether the Mode screen's periodic poll should also auto-refresh the open-devices list.
    should_auto_refresh_open_dcs: bool
    can_view_media: bool
    can_control_devices: bool
    can_view_history: bool


class SavedMediaDto(ApiModel):
    file_name: str
    taken_at: Optional[datetime.datetime] = None


class DiscoveredPanelDto(ApiModel):
    mac_address: str
    hw_version: Optional[str] = None
    sw_version: Optional[str] = None
    ip_address: Optional[str] = None
    webs_port: Optional[str] = None
    last_seen: datetime.datetime


class AlarmUserDto(ApiModel):
    index: int
    name: Optional[str] = None
    pin: Optional[str] = None
    pin_masked: bool = False
    latch: bool = False


class DeviceDto(ApiModel):
    index: int
    name: Optional[str] = None
    type: _by_name_enum(DeviceType)
    attribute: _by_name_enum(DeviceAttribute)
    radio_identifier: Optional[str] = None
    bypass: bool = False

    @staticmethod
    def from_device(d: Device) -> "DeviceDto":
        return DeviceDto(
            index=d.index,
            name=d.name,
            type=d.type,
            attribute=d.attribute,
            radio_identifier=d.radio_identifier,
            bypass=d.bypass,
        )


class DeviceStateDto(ApiModel):
    device_index: Optional[int] = None
    status: int
    status_last_changed: Optional[datetime.datetime] = None
    power_on: bool = False

    @staticmethod
    def from_state(s: DeviceState) -> "DeviceStateDto":
        return DeviceStateDto(
            device_index=s.device.index if s.device else None,
            status=int(s.status),
            status_last_changed=s.status_last_changed,
            power_on=s.power_on,
        )


class DevicesDto(ApiModel):
    devices: list[DeviceDto]
    states: list[DeviceStateDto]


class OpenDeviceDto(ApiModel):
    index: int
    name: Optional[str] = None
    status_last_changed: Optional[datetime.datetime] = None


class HistoryEntryDto(ApiModel):
    type: _by_name_enum(HistoryEventType)
    description: str
    preposition: Optional[str] = None
    user: Optional[str] = None
    device: Optional[str] = None
    when_occurred: Optional[datetime.datetime] = None
    alarm_clock_when_queried: Optional[datetime.datetime] = None


class SystemConfigDto(ApiModel):
    away_arm_entry_delay: Optional[int] = None
    away_arm_exit_delay: Optional[int] = None
    away_arm_entry_delay_sound: Optional[int] = None
    away_arm_exit_delay_sound: Optional[int] = None
    home_arm_entry_delay: Optional[int] = None
    home_arm_exit_delay: Optional[int] = None
    home_arm_entry_delay_sound: Optional[int] = None
    home_arm_exit_delay_sound: Optional[int] = None
    door_contact_sound: Optional[int] = None
    supervision: Optional[int] = None
    siren_length: Optional[int] = None


class LearnedDeviceDto(ApiModel):
    radio_identifier: Optional[str] = None
    type: _by_name_enum(DeviceType)


class WalkTestSignalDto(ApiModel):
    device_index: Optional[int] = None
    device_name: Optional[str] = None
    rssi: int
    max_rssi: int
    strength: int


class SetModeRequest(ApiModel):
    mode: _by_name_enum(PanelMode)
    user: int = 0


class SetModeResponse(ApiModel):
    door_open: bool = False


class SetUserRequest(ApiModel):
    name: str
    pin: str
    latch: bool = False


class RequestMediaRequest(ApiModel):
    flash: bool = False


class SetPowerSwitchRequest(ApiModel):
    on: bool


class AddLearnedDeviceRequest(ApiModel):
    name: str


class SetDeviceRequest(ApiModel):
    name: str
    attribute: Optional[_by_name_enum(DeviceAttribute)] = None
    bypass: Optional[bool] = None


class SetAwayArmConfigRequest(ApiModel):
    entry_delay: int
    exit_delay: int
    entry_delay_sound: int
    exit_delay_sound: int


class SetHomeArmConfigRequest(ApiModel):
    entry_delay: int
    exit_delay: int
    entry_delay_sound: int
    exit_delay_sound: int


class SetGeneralConfigRequest(ApiModel):
    door_contact_sound: int
    supervision: int
    siren_length: int


class SirenCommandRequest(ApiModel):
    setting: SirenSetting
    value: int
