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

import dataclasses
import datetime
import enum
from typing import Any, Optional


def _unknown_member(cls, value):
    pseudo = int.__new__(cls, value)
    pseudo._name_ = f"Unknown_{value}"
    pseudo._value_ = value
    return pseudo


class PanelMode(enum.IntEnum):
    # Real states
    Disarm = 0
    AwayArm = 1
    HomeArm = 2

    # States which only exist in this service
    AwayArmInProgress = 3
    HomeArmInProgress = 4

    @classmethod
    def _missing_(cls, value):
        return _unknown_member(cls, value)

PANEL_MODE_DESCRIPTIONS = {
    PanelMode.Disarm: "Disarm",
    PanelMode.AwayArm: "Away Arm",
    PanelMode.HomeArm: "Home Arm",
    PanelMode.AwayArmInProgress: "Away Arm Setting",
    PanelMode.HomeArmInProgress: "Home Arm Setting",
}


class PanelType(enum.Enum):
    Unknown = "Unknown"
    ML_yaukoz = "ML_yaukoz"
    CTC_XMPP = "CTC_XMPP"
    CTC_Polling = "CTC_Polling"


class DeviceType(enum.IntEnum):
    Unknown = 0
    KeyFob = 2
    DoorContact = 4
    PIR = 9
    Keypad = 15
    PowerSwitch = 24
    PirCamera = 27
    Siren = 46

    @classmethod
    def _missing_(cls, value):
        return _unknown_member(cls, value)


class DeviceAttribute(enum.IntEnum):
    Unset = 0
    Perimeter = 1
    Interior = 2
    HomeDelay = 5
    PerimeterFollower = 6
    InteriorFollower = 7
    Entry1 = 8
    InteriorWithDelay = 9
    TwentyFourHour = 10
    TriggerScene = 17
    SilentBurglar = 18
    Entry2 = 19
    BurglarOutdoor = 21

    @classmethod
    def _missing_(cls, value):
        return _unknown_member(cls, value)


class DeviceFlags(enum.IntFlag):
    Bypass = 0x02
    Chime = 0x80


class DeviceStatus(enum.IntFlag):
    NONE = 0
    Open = 0x10  # DoorContact devices only
    On = 0x20  # PowerSwitch devices only


class SirenSetting(enum.Enum):
    ComfortLed = "ComfortLed"
    TamperDetection = "TamperDetection"
    EntryExitConfirm = "EntryExitConfirm"


@dataclasses.dataclass
class Device:
    type: DeviceType = DeviceType.Unknown
    flags: DeviceFlags = DeviceFlags(0)
    attribute: DeviceAttribute = DeviceAttribute.Unset
    index: int = 0
    name: Optional[str] = None
    radio_identifier: Optional[str] = None
    bypass: bool = False


@dataclasses.dataclass
class DeviceState:
    device: Optional[Device] = None
    status: DeviceStatus = DeviceStatus.NONE
    status_last_changed: Optional[datetime.datetime] = None
    power_on: bool = False


@dataclasses.dataclass
class PanelUser:
    index: int = 0
    name: Optional[str] = None
    pin: Optional[str] = None
    latch: bool = False


@dataclasses.dataclass
class SystemConfiguration:
    home_arm_exit_delay: int = 0
    away_arm_exit_delay: int = 0
    away_arm_entry_delay: int = 0
    home_arm_entry_delay: int = 0
    away_arm_entry_delay_sound: int = 0
    away_arm_exit_delay_sound: int = 0
    home_arm_entry_delay_sound: int = 0
    home_arm_exit_delay_sound: int = 0
    # Chime sound played when a door/window contact opens.
    door_contact_sound: int = 0
    # Not exposed in the UI - preserved as read so writes to the other sounds carried on the same
    # underlying command don't reset it.
    hub_warning_sound: int = 0
    # Minutes of inactivity before a supervised device is reported missing. 0 disables it.
    supervision: int = 0
    siren_length: int = 0


@dataclasses.dataclass
class LearnedDevice:
    alarm: Any = None
    context: Any = None
    type: DeviceType = DeviceType.Unknown
    radio_identifier: Optional[str] = None


@dataclasses.dataclass
class WalkNotification:
    alarm: Any = None
    device: Optional[Device] = None
    rssi: int = 0
    context: Any = None


@dataclasses.dataclass
class PanelBackendConfig:
    friendly_name: str = "Alarm"
    account_number: Optional[int] = None
    send_emails_to: list[str] = dataclasses.field(default_factory=list)
    send_emails_from_name: str = "Alarm"
    send_emails_from_address: str = "alarm@alarmtest.com"
    media_save_path: Optional[str] = None
    email_media: bool = True


@dataclasses.dataclass
class DiscoveredPanel:
    mac_address: str
    hw_version: Optional[str] = None
    sw_version: Optional[str] = None
    lan_type: Optional[str] = None
    ip_address: Optional[str] = None
    netmask: Optional[str] = None
    gateway: Optional[str] = None
    dns1: Optional[str] = None
    dns2: Optional[str] = None
    webs_port: Optional[str] = None
    # When this panel last replied to a discovery poll.
    last_seen: datetime.datetime = dataclasses.field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))

