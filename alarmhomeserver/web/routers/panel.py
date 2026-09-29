"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
panel.py:
REST API for user-facing web frontend
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

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse

from alarmhomeserver.models.alarm import (
    DeviceStatus,
    DeviceType,
    LearnedDevice,
    PanelMode,
    PanelType,
    WalkNotification,
)
from alarmhomeserver.models.history_descriptions import HistoryEventType, history_description, history_preposition
from alarmhomeserver.models.api_models import (
    AddLearnedDeviceRequest,
    AlarmSummaryDto,
    AlarmUserDto,
    DeviceDto,
    DevicesDto,
    DeviceStateDto,
    DiscoveredPanelDto,
    HistoryEntryDto,
    LearnedDeviceDto,
    OpenDeviceDto,
    RequestMediaRequest,
    SetAwayArmConfigRequest,
    SetDeviceRequest,
    SetGeneralConfigRequest,
    SetHomeArmConfigRequest,
    SetModeRequest,
    SetModeResponse,
    SetPowerSwitchRequest,
    SetUserRequest,
    SirenCommandRequest,
    SystemConfigDto,
    WalkTestSignalDto,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/panel", tags=["panel"])


def _panel_manager(request: Request):
    return request.app.state.panel_manager


def _media_service(request: Request):
    return request.app.state.media_service


def _panel_finder(request: Request):
    return request.app.state.panel_finder


def _get_panel_or_404(request: Request, identifier: Optional[str]):
    alarm = _panel_manager(request).get_panel(identifier)
    if alarm is None:
        raise HTTPException(status_code=404)
    return alarm


def _to_summary(alarm) -> AlarmSummaryDto:
    return AlarmSummaryDto(
        identifier=alarm.identifier,
        friendly_name=alarm.backend_config.friendly_name if alarm.backend_config else None,
        version=alarm.version,
        ip_address=alarm.ip_address,
        mode=alarm.mode,
        type=alarm.type,
        last_mode_change_user=alarm.last_mode_change_user,
        max_device_name_length=alarm.max_device_name_length,
        is_polling_alarm=alarm.type == PanelType.CTC_Polling,
        should_auto_refresh_open_dcs=alarm.type not in (PanelType.CTC_Polling, PanelType.CTC_XMPP),
        can_view_media=bool(alarm.backend_config and alarm.backend_config.media_save_path),
        can_control_devices=any(d.type in (DeviceType.PowerSwitch, DeviceType.PirCamera) for d in alarm.devices),
        can_view_history=alarm.type == PanelType.ML_yaukoz,
    )


@router.get("")
def get_default(request: Request):
    return _to_summary(_get_panel_or_404(request, None))


@router.get("/list")
def get_panels(request: Request):
    return [_to_summary(a) for a in _panel_manager(request).get_panels()]


@router.get("/discovered")
def get_discovered_panels(request: Request):
    known = {p.identifier.lower() for p in _panel_manager(request).get_panels() if p.identifier}

    return [
        DiscoveredPanelDto(
            mac_address=p.mac_address,
            hw_version=p.hw_version,
            sw_version=p.sw_version,
            ip_address=p.ip_address,
            webs_port=p.webs_port,
            last_seen=p.last_seen,
        )
        for p in _panel_finder(request).get_discovered_panels()
        if p.mac_address.lower() not in known
    ]


@router.get("/{identifier}")
def get_summary(identifier: str, request: Request):
    return _to_summary(_get_panel_or_404(request, identifier))


@router.get("/{identifier}/history")
def get_history(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    return [
        HistoryEntryDto(
            type=el.type,
            description=history_description(el.type),
            preposition=history_preposition(el.type),
            user=el.user.name if el.user else None,
            device=el.device.name if el.device else None,
            when_occurred=el.when_occurred,
            alarm_clock_when_queried=el.alarm_clock_when_queried,
        )
        for el in alarm.get_history()
    ]


@router.get("/{identifier}/devices")
def get_devices(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    return DevicesDto(
        devices=[DeviceDto.from_device(d) for d in alarm.devices],
        states=[DeviceStateDto.from_state(s) for s in alarm.device_states],
    )


@router.get("/{identifier}/open")
def get_open_devices(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    alarm.refresh_devices()

    return [
        OpenDeviceDto(index=s.device.index, name=s.device.name, status_last_changed=s.status_last_changed)
        for s in alarm.device_states
        if s.device and s.device.type == DeviceType.DoorContact and (s.status & DeviceStatus.Open)
    ]


@router.post("/{identifier}/devices/{index}/markclosed")
def mark_device_closed(identifier: str, index: int, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not any(s.device and s.device.index == index for s in alarm.device_states):
        raise HTTPException(status_code=400, detail=f"No device is reporting a state for index {index}")
    if not alarm.clear_device_open(index):
        raise HTTPException(status_code=500, detail="Failed to clear the open flag")
    return {}


@router.post("/{identifier}/devices/{index}")
def set_device(identifier: str, index: int, body: SetDeviceRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="A name is required")
    if len(body.name) > alarm.max_device_name_length:
        raise HTTPException(status_code=400, detail=f"This alarm accepts device names of up to {alarm.max_device_name_length} characters")
    if not alarm.set_device(index, body.name, body.attribute, body.bypass, None):
        raise HTTPException(status_code=500, detail="Device update failed")
    return {}


@router.delete("/{identifier}/devices/{index}")
def delete_device(identifier: str, index: int, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.delete_device(index):
        raise HTTPException(status_code=500, detail="Failed to delete device")
    return {}


@router.get("/{identifier}/users")
def get_users(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    results = []
    for u in alarm.users:
        masked = bool(u.pin) and all(c == "*" for c in u.pin)
        results.append(AlarmUserDto(index=u.index, name=u.name, pin=None if masked else u.pin, pin_masked=masked, latch=u.latch))
    return results


@router.post("/{identifier}/users/{index}")
def set_user(identifier: str, index: int, body: SetUserRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="A name is required")
    if not body.pin or not body.pin.isdigit():
        raise HTTPException(status_code=400, detail="A numeric PIN is required")
    if not alarm.set_user(index, body.name, body.pin, body.latch):
        raise HTTPException(status_code=500, detail="Update user failed. Pin may already be in use by another user.")
    return {}


@router.post("/{identifier}/users")
def add_user(identifier: str, body: SetUserRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="A name is required")
    if not body.pin or not body.pin.isdigit():
        raise HTTPException(status_code=400, detail="A numeric PIN is required")
    index = alarm.add_user(body.name, body.pin, body.latch)
    if index <= 0:
        raise HTTPException(status_code=500, detail="Failed to add user")
    return {"index": index}


@router.delete("/{identifier}/users/{index}")
def delete_user(identifier: str, index: int, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.delete_user(index):
        raise HTTPException(status_code=500, detail="Failed to delete user")
    return {}


@router.post("/{identifier}/mode")
def set_mode(identifier: str, body: SetModeRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    success, door_open = alarm.set_mode(body.mode, body.user)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to set state")
    return SetModeResponse(door_open=door_open)


@router.post("/{identifier}/media/{device_index}")
def request_media(identifier: str, device_index: int, body: RequestMediaRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    error = alarm.request_media(device_index, body.flash)
    if error is not None:
        raise HTTPException(status_code=500, detail=error)
    return {}


@router.get("/{identifier}/media")
def get_saved_media(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    return _media_service(request).get_saved_media(alarm)


@router.get("/{identifier}/media/{file_name}")
def get_saved_media_file(identifier: str, file_name: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    path = _media_service(request).try_get_saved_media_path(alarm, file_name)
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/{identifier}/media/{file_name}")
def delete_saved_media_file(identifier: str, file_name: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not _media_service(request).delete_saved_media(alarm, file_name):
        raise HTTPException(status_code=404)
    return {}


@router.post("/{identifier}/devices/{index}/power")
def set_power_switch(identifier: str, index: int, body: SetPowerSwitchRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    error = alarm.set_power_switch(index, body.on)
    if error is not None:
        raise HTTPException(status_code=500, detail=error)
    return {}


@router.get("/{identifier}/config")
def get_system_config(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    config = alarm.get_system_config()
    if config is None:
        raise HTTPException(status_code=500, detail="Failed to read system configuration")

    return SystemConfigDto(
        away_arm_entry_delay=config.away_arm_entry_delay,
        away_arm_exit_delay=config.away_arm_exit_delay,
        away_arm_entry_delay_sound=config.away_arm_entry_delay_sound,
        away_arm_exit_delay_sound=config.away_arm_exit_delay_sound,
        home_arm_entry_delay=config.home_arm_entry_delay,
        home_arm_exit_delay=config.home_arm_exit_delay,
        home_arm_entry_delay_sound=config.home_arm_entry_delay_sound,
        home_arm_exit_delay_sound=config.home_arm_exit_delay_sound,
        door_contact_sound=config.door_contact_sound,
        supervision=config.supervision,
        siren_length=config.siren_length,
    )


@router.post("/{identifier}/config/awayarm")
def set_away_arm_config(identifier: str, body: SetAwayArmConfigRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.set_away_arm_config(body.entry_delay, body.exit_delay, body.entry_delay_sound, body.exit_delay_sound):
        raise HTTPException(status_code=500, detail="Failed to save Away Arm configuration")
    return {}


@router.post("/{identifier}/config/homearm")
def set_home_arm_config(identifier: str, body: SetHomeArmConfigRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.set_home_arm_config(body.entry_delay, body.exit_delay, body.entry_delay_sound, body.exit_delay_sound):
        raise HTTPException(status_code=500, detail="Failed to save Home Arm configuration")
    return {}


@router.post("/{identifier}/config/general")
def set_general_config(identifier: str, body: SetGeneralConfigRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.set_general_config(body.door_contact_sound, body.supervision, body.siren_length):
        raise HTTPException(status_code=500, detail="Failed to save General configuration")
    return {}


@router.post("/{identifier}/config/siren")
def set_siren_setting(identifier: str, body: SirenCommandRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.send_siren_configuration(body.setting, body.value):
        raise HTTPException(status_code=500, detail="Failed to save siren setting")
    return {}


@router.get("/{identifier}/learn/stream")
async def learn_stream(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_learned(device: LearnedDevice) -> None:
        dto = LearnedDeviceDto(radio_identifier=device.radio_identifier, type=device.type)
        loop.call_soon_threadsafe(queue.put_nowait, dto)

    if not await run_in_threadpool(alarm.start_learn, on_learned, None):
        raise HTTPException(status_code=500, detail="Failed to enter learn mode")

    async def event_stream():
        yield ": ready\n\n"
        try:
            while True:
                dto = await queue.get()
                yield f"data: {dto.model_dump_json(by_alias=True)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await run_in_threadpool(alarm.stop_learn)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/{identifier}/learn/stop")
def stop_learn(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.stop_learn():
        raise HTTPException(status_code=500, detail="Failed to exit learn mode")
    return {}


@router.post("/{identifier}/learn/add")
def add_learned_device(identifier: str, body: AddLearnedDeviceRequest, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="A name is required")
    if len(body.name) > alarm.max_device_name_length:
        raise HTTPException(status_code=400, detail=f"This alarm accepts device names of up to {alarm.max_device_name_length} characters")
    try:
        if not alarm.add_learned_device(None, body.name):
            raise HTTPException(status_code=500, detail="Failed to add learned device")
        return {}
    finally:
        alarm.stop_learn()


@router.get("/{identifier}/walktest/stream")
async def walk_test_stream(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_signal(signal: WalkNotification) -> None:
        dto = WalkTestSignalDto(
            device_index=signal.device.index if signal.device else None,
            device_name=signal.device.name if signal.device else None,
            rssi=signal.rssi,
            max_rssi=alarm.max_signal_strength,
            strength=max(1, min(10, round(signal.rssi * 10 / max(1, alarm.max_signal_strength)))),
        )
        loop.call_soon_threadsafe(queue.put_nowait, dto)

    if not await run_in_threadpool(alarm.start_walk_test, on_signal, None):
        raise HTTPException(status_code=500, detail="Failed to start walk test")

    async def event_stream():
        yield ": ready\n\n"
        try:
            while True:
                dto = await queue.get()
                yield f"data: {dto.model_dump_json(by_alias=True)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await run_in_threadpool(alarm.stop_walk_test)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/{identifier}/walktest/stop")
def stop_walk_test(identifier: str, request: Request):
    alarm = _get_panel_or_404(request, identifier)
    if not alarm.stop_walk_test():
        raise HTTPException(status_code=500, detail="Failed to stop walk test")
    return {}
