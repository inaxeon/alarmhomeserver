"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
history_descriptions.py:
ML protocol history event types, descriptions and prepositions
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
from typing import Optional

from alarmhomeserver.models.alarm import Device, PanelUser, _unknown_member

class HistoryEventType(enum.IntEnum):
    NoResponse = 1
    StartEntry1 = 2
    StartEntry2 = 3
    Chime = 4
    Burglar = 5
    SmokeAlarm = 6
    MedicalAlarm = 7
    WaterAlarm = 8
    SilentPanicAlarm = 9
    PanicAlarm = 10
    EmergencyAlarm = 11
    FireAlarm = 12
    COAlarm = 13
    BurglarPerimeter = 14
    BurglarInterior = 15
    BurglarOutdoor = 16
    EmergencyQuiet = 17
    HighTempAlarm = 18
    LowTempAlarm = 19
    ApplyScene = 20
    GasAlarm = 21
    HeatAlarm = 22
    TamperAlarm = 23
    Burglar24Hour = 24
    BurglarSilent = 25
    SetUnsetDisarm = 50
    SetUnsetArm = 51
    Disarm = 52
    Arm = 53
    HomeArm = 54
    ArmWithFault = 55
    HomeArmWithFault = 56
    RemoteDisarm = 57
    RemoteArm = 58
    RemoteHomeArm = 59
    RemoteArmWithFault = 60
    RemoteHomeArmWithFault = 61
    PanelACFail = 100
    HubBatteryDead = 101
    PanelBatteryLow = 102
    PanelTamper = 103
    JamDetect = 104
    GSMNoSignal = 105
    NetworkDisconnected = 106
    Tamper = 107
    BatteryLow = 108
    SupervisionFailure = 109
    PanelACOK = 111
    PanelBatteryNormal = 112
    TamperRestore = 113
    NoJam = 114
    GSMSignalOK = 115
    NetworkConnected = 116
    HubTamperRestore = 117
    BatteryNormal = 118
    SupervisionOK = 119
    DoorUnlock = 121
    ACFail = 123
    ACOK = 124,
    PowerOn = 150

    @classmethod
    def _missing_(cls, value):
        return _unknown_member(cls, value)


_HISTORY_DESCRIPTIONS = {
    HistoryEventType.NoResponse: "No Response",
    HistoryEventType.StartEntry1: "Start Entry",
    HistoryEventType.StartEntry2: "Start Entry2",
    HistoryEventType.Chime: "Chime",
    HistoryEventType.Burglar: "BURGLAR",
    HistoryEventType.SmokeAlarm: "SMOKE ALARM",
    HistoryEventType.MedicalAlarm: "Medical Alarm",
    HistoryEventType.WaterAlarm: "Water Alarm",
    HistoryEventType.SilentPanicAlarm: "Silent Panic Alarm",
    HistoryEventType.PanicAlarm: "Panic Alarm",
    HistoryEventType.EmergencyAlarm: "Emergency Alarm",
    HistoryEventType.FireAlarm: "FIRE ALARM",
    HistoryEventType.COAlarm: "CO Alarm",
    HistoryEventType.BurglarPerimeter: "BURGLAR Perimeter",
    HistoryEventType.BurglarInterior: "BURGLAR Interior",
    HistoryEventType.BurglarOutdoor: "BURGLAR Outdoor",
    HistoryEventType.EmergencyQuiet: "BURGLAR Quiet",
    HistoryEventType.HighTempAlarm: "High Temp Alarm",
    HistoryEventType.LowTempAlarm: "Low Temp Alarm",
    HistoryEventType.ApplyScene: "Apply Scene",
    HistoryEventType.GasAlarm: "Gas Alarm",
    HistoryEventType.HeatAlarm: "Heat Alarm",
    HistoryEventType.TamperAlarm: "Tamper Alarm",
    HistoryEventType.Burglar24Hour: "Burglar 24H",
    HistoryEventType.BurglarSilent: "Burglar Silent",
    HistoryEventType.SetUnsetDisarm: "Set/Unset Disarm",
    HistoryEventType.SetUnsetArm: "Set/Unset Arm",
    HistoryEventType.Disarm: "Disarm",
    HistoryEventType.Arm: "Away Arm",
    HistoryEventType.HomeArm: "Home Arm",
    HistoryEventType.ArmWithFault: "Arm with Fault",
    HistoryEventType.HomeArmWithFault: "Home Arm with Fault",
    HistoryEventType.RemoteDisarm: "Disarm",
    HistoryEventType.RemoteArm: "Arm",
    HistoryEventType.RemoteHomeArm: "Home Arm",
    HistoryEventType.RemoteArmWithFault: "Remote Arm with Fault",
    HistoryEventType.RemoteHomeArmWithFault: "Remote Home Arm with Fault",
    HistoryEventType.PanelACFail: "Hub AC Fail",
    HistoryEventType.HubBatteryDead: "Hub Battery Dead",
    HistoryEventType.PanelBatteryLow: "Hub Battery Low",
    HistoryEventType.PanelTamper: "Hub Tamper",
    HistoryEventType.JamDetect: "Jam Detect",
    HistoryEventType.GSMNoSignal: "GSM No Signal",
    HistoryEventType.NetworkDisconnected: "Network Disconnected",
    HistoryEventType.Tamper: "Tamper",
    HistoryEventType.BatteryLow: "Battery Low",
    HistoryEventType.SupervisionFailure: "Supervision Failure",
    HistoryEventType.PanelACOK: "Hub AC OK",
    HistoryEventType.PanelBatteryNormal: "Hub Battery Normal",
    HistoryEventType.TamperRestore: "Tamper Restore",
    HistoryEventType.NoJam: "No Jam",
    HistoryEventType.GSMSignalOK: "GSM Signal OK",
    HistoryEventType.NetworkConnected: "Network Connected",
    HistoryEventType.HubTamperRestore: "Tamper Restore",
    HistoryEventType.BatteryNormal: "Battery Normal",
    HistoryEventType.SupervisionOK: "Supervision OK",
    HistoryEventType.DoorUnlock: "Door Unlock",
    HistoryEventType.ACFail: "AC Fail",
    HistoryEventType.ACOK: "AC OK",
    HistoryEventType.PowerOn: "Power On",
}

# Event types that relate to a device/user and should be joined with a preposition, e.g.
# "Burglar at Front Door".
_HISTORY_PREPOSITIONS = {
    HistoryEventType.StartEntry1: "at",
    HistoryEventType.StartEntry2: "at",
    HistoryEventType.Chime: "at",
    HistoryEventType.Burglar: "at",
    HistoryEventType.SmokeAlarm: "at",
    HistoryEventType.MedicalAlarm: "at",
    HistoryEventType.WaterAlarm: "at",
    HistoryEventType.SilentPanicAlarm: "at",
    HistoryEventType.PanicAlarm: "at",
    HistoryEventType.EmergencyAlarm: "at",
    HistoryEventType.FireAlarm: "at",
    HistoryEventType.COAlarm: "at",
    HistoryEventType.BurglarPerimeter: "at",
    HistoryEventType.BurglarInterior: "at",
    HistoryEventType.BurglarOutdoor: "at",
    HistoryEventType.EmergencyQuiet: "at",
    HistoryEventType.HighTempAlarm: "at",
    HistoryEventType.LowTempAlarm: "at",
    HistoryEventType.Disarm: "by",
    HistoryEventType.Arm: "by",
    HistoryEventType.HomeArm: "by",
    HistoryEventType.ArmWithFault: "by",
    HistoryEventType.HomeArmWithFault: "by",
    HistoryEventType.RemoteDisarm: "by",
    HistoryEventType.RemoteArm: "by",
    HistoryEventType.RemoteHomeArm: "by",
    HistoryEventType.RemoteArmWithFault: "by",
    HistoryEventType.RemoteHomeArmWithFault: "by",
    HistoryEventType.Tamper: "at",
    HistoryEventType.BatteryLow: "at",
    HistoryEventType.TamperRestore: "at",
    HistoryEventType.HubTamperRestore: "at",
    HistoryEventType.DoorUnlock: "at",
}


def history_description(value: HistoryEventType) -> str:
    return _HISTORY_DESCRIPTIONS.get(value, f"Event {value.name if isinstance(value, HistoryEventType) else value}")


def history_preposition(value: HistoryEventType) -> Optional[str]:
    return _HISTORY_PREPOSITIONS.get(value)


@dataclasses.dataclass
class HistoryEntry:
    type: HistoryEventType = HistoryEventType.NoResponse
    when_occurred: Optional[datetime.datetime] = None
    # The panel's own clock at the time it was queried. History timestamps come from that clock,
    # which can be wrong if an NTP update hasn't yet happened.
    alarm_clock_when_queried: Optional[datetime.datetime] = None
    user: Optional[PanelUser] = None
    device: Optional[Device] = None
