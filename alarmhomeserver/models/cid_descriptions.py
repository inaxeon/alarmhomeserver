"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
cid_descriptions.py:
Contact ID event descriptions
It is actually possibly to redirect CID traffic from a yamga alarm to this
service, thus a few extra event codes which the "small yaukoz" firmware doesn't
send are listed in here.
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
import enum
from typing import Optional


class EventType(enum.IntEnum):
    Fire = 1110
    Smoke = 1111
    Heat = 1114
    Panic = 1120
    SilentPanic = 1122
    Burglar = 1130
    BurglarPerimeter = 1131
    BurglarEntry = 1132
    HubPowerLost = 1301
    HubPowerRestored = 3301
    HubBatteryLow = 1302
    HubBatteryGood = 3302
    HubBatteryDead = 1311
    HubBatteryAlive = 3311
    Interference = 1344
    Tamper = 1383
    TamperRestore = 3383
    LowBattery = 1384
    BatteryGood = 3384
    DisarmByRc = 1400
    ArmByRc = 3400
    ArmByPanel = 3401  # MZ-1 only
    DisarmByApp = 1401
    DisarmByKeypad = 1407
    ArmByApp = 1493
    HomeArmGeneral = 3456  # Needed for MZ-1. "Zone" param can be user or device - no way to tell.
    ArmByKeypad = 1491
    HomeArmByRc = 1494
    HomeArmByApp = 1496
    PeriodicTest = 1602  # Ignored


_DESCRIPTIONS = {
    EventType.Fire: "FIRE",
    EventType.Smoke: "SMOKE",
    EventType.Heat: "HEAT",
    EventType.Panic: "PANIC",
    EventType.SilentPanic: "Silent Panic",
    EventType.Burglar: "BURGLAR",
    EventType.BurglarPerimeter: "BURGLAR",
    EventType.BurglarEntry: "BURGLAR",
    EventType.HubPowerLost: "Hub Power Lost",
    EventType.HubPowerRestored: "Hub Power Restored",
    EventType.HubBatteryLow: "Hub Battery Low",
    EventType.HubBatteryGood: "Hub Battery Good",
    EventType.HubBatteryDead: "Hub Battery Missing/Dead",
    EventType.HubBatteryAlive: "Hub Battery Present",
    EventType.Interference: "Interference Detected",
    EventType.Tamper: "Tamper",
    EventType.TamperRestore: "Tamper Restore",
    EventType.LowBattery: "Low Battery",
    EventType.BatteryGood: "Battery Good",
    EventType.DisarmByRc: "Disarm",
    EventType.ArmByRc: "Away Arm",
    EventType.ArmByPanel: "Away Arm",
    EventType.DisarmByApp: "Disarm",
    EventType.DisarmByKeypad: "Disarm",
    EventType.ArmByApp: "Away Arm",
    EventType.HomeArmGeneral: "Home Arm",
    EventType.ArmByKeypad: "Away Arm",
    EventType.HomeArmByRc: "Home Arm",
    EventType.HomeArmByApp: "Home Arm",
}

# i.e. Away Arm [by] Keypad
# i.e. BURGLAR [at] Front door
_PREPOSITIONS: dict[EventType, Optional[str]] = {
    EventType.Fire: "at",
    EventType.Smoke: "at",
    EventType.Heat: "at",
    EventType.Panic: "by",
    EventType.SilentPanic: "by",
    EventType.Burglar: "at",
    EventType.BurglarPerimeter: "at",
    EventType.BurglarEntry: "at",
    EventType.HubPowerLost: None,
    EventType.HubPowerRestored: None,
    EventType.HubBatteryLow: None,
    EventType.HubBatteryGood: None,
    EventType.HubBatteryDead: None,
    EventType.HubBatteryAlive: None,
    EventType.Interference: None,
    EventType.Tamper: "at",
    EventType.TamperRestore: "at",
    EventType.LowBattery: "at",
    EventType.BatteryGood: "at",
    EventType.DisarmByRc: "by",
    EventType.ArmByRc: "by",
    EventType.ArmByPanel: "by",
    EventType.DisarmByApp: "by",
    EventType.DisarmByKeypad: "by",
    EventType.ArmByApp: "by",
    EventType.HomeArmGeneral: "by",
    EventType.ArmByKeypad: "by",
    EventType.HomeArmByRc: "by",
    EventType.HomeArmByApp: "by",
}

# Periodic Tests are sent every 24 hours and get quite annoying so silence them
_IGNORED = {EventType.PeriodicTest}


class EventTypeHelper:
    @staticmethod
    def get_description(value: EventType) -> str:
        return _DESCRIPTIONS.get(value, f"Event {value.name if isinstance(value, EventType) else value}")

    @staticmethod
    def get_preposition(value: EventType) -> Optional[str]:
        return _PREPOSITIONS.get(value)

    @staticmethod
    def is_ignored(value: EventType) -> bool:
        return value in _IGNORED


@dataclasses.dataclass
class CidMessage:
    account_number: str = ""
    event_code: int = 0
    partition: int = 0
    zone: int = 0
