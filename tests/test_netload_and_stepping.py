"""Tests für Netzlast-Steuerung (Motion-Optionen, v2.2.40) und PTZ-Puls-Verhalten.

Hintergrund: Kanal 2 (RTSP-Bildvergleich) und die Auto-Aufnahme erzeugten eine
dauerhafte Netzwerkflut (behoben in v2.2.40). In v2.2.41 wurde das
N-Pulse-Stepping durch 1 Puls mit proportionaler Dauer ersetzt: 1 Tap = 1 Puls,
Dauer ∝ Speed (Stufe 1 → kurz, Stufe 8 → lang).
"""
import os
import sys
import time
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.wjg_camera.xm_soap import XMSoapClient
from tests_helpers import (
    get_private_attr as _get_private_attr,
    make_coordinator as _make_coordinator,
    set_private_attr as _set_private_attr,
)


class DummyEntry:
    def __init__(self, data, options=None):
        self.data = data
        self.entry_id = "netload-entry"
        self.options = options or {}


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


ONVIF_DATA = {
    "host": "192.168.1.60",
    "rtsp_port": 554,
    "port": 80,
    "username": "admin",
    "password": "",
    "protocol": "onvif",
    "onvif_port": 8899,
}


# ── Motion-Optionen ───────────────────────────────────────────────────────────

def test_motion_options_defaults():
    """Default: Kanal 2 AUS (Netzlast), Auto-Aufnahme AN (Funktionserhalt)."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))

    assert coordinator.motion_rtsp_diff_enabled is False
    assert coordinator.motion_auto_record_enabled is True
    assert coordinator.motion_rtsp_interval == 30
    assert coordinator.motion_record_cooldown == 30


def test_motion_options_read_from_entry_options():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_diff": True,
        "motion_rtsp_interval": 60,
        "motion_auto_record": False,
        "motion_record_cooldown": 120,
    }))

    assert coordinator.motion_rtsp_diff_enabled is True
    assert coordinator.motion_rtsp_interval == 60
    assert coordinator.motion_auto_record_enabled is False
    assert coordinator.motion_record_cooldown == 120


def test_motion_rtsp_interval_clamped_to_minimum():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_interval": 1,
    }))

    assert coordinator.motion_rtsp_interval == 10


async def _setup_with_mocks(coordinator, monkeypatch):
    class NoopSession:
        async def close(self):
            return None

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: NoopSession())
    coordinator.async_resolve_rtsp_path = AsyncMock()
    coordinator.async_fetch_device_info = AsyncMock()
    coordinator.async_fetch_imaging_settings = AsyncMock()
    coordinator.async_ptz_get_presets = AsyncMock()
    coordinator.async_fetch_audio_settings = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    _set_private_attr(coordinator, "_async_bootstrap_onvif_service_paths", AsyncMock())
    await coordinator.async_setup()


@pytest.mark.asyncio
async def test_async_setup_does_not_start_rtsp_motion_loop_by_default(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    try:
        await _setup_with_mocks(coordinator, monkeypatch)

        assert _get_private_attr(coordinator, "_rtsp_motion_task") is None
        assert _get_private_attr(coordinator, "_event_task") is not None
        assert _get_private_attr(coordinator, "_udp_monitor_task") is not None
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_async_setup_starts_rtsp_motion_loop_when_option_enabled(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_diff": True,
    }))
    try:
        await _setup_with_mocks(coordinator, monkeypatch)

        assert _get_private_attr(coordinator, "_rtsp_motion_task") is not None
    finally:
        await coordinator.async_shutdown()


# ── Auto-Aufnahme: Option + Cooldown ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_motion_recording_disabled_by_option():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_auto_record": False,
    }))
    coordinator.async_start_local_recording = AsyncMock()

    await _get_private_attr(coordinator, "_trigger_motion_recording")()

    coordinator.async_start_local_recording.assert_not_awaited()


@pytest.mark.asyncio
async def test_motion_recording_cooldown_limits_restarts():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    starts = {"n": 0}

    async def _fake_start():
        starts["n"] += 1
        return "/media/camera/x.mkv"

    coordinator.async_start_local_recording = _fake_start
    trigger = _get_private_attr(coordinator, "_trigger_motion_recording")

    await trigger()
    await trigger()  # innerhalb des Cooldowns → kein zweiter Start
    assert starts["n"] == 1

    # Cooldown abgelaufen → erneuter Start erlaubt
    _set_private_attr(
        coordinator, "_last_record_trigger",
        time.time() - coordinator.motion_record_cooldown - 1,
    )
    await trigger()
    assert starts["n"] == 2

    stop_task = _get_private_attr(coordinator, "_recording_stop_task")
    if stop_task:
        stop_task.cancel()


# ── PTZ: xm_soap (Primärpfad) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_xmsoap_ptz_command_sends_single_pulse():
    """1 Tap = immer genau 1 ContinuousMove, unabhängig von der Speed-Stufe."""
    client = XMSoapClient(host="192.168.1.61")
    moves = {"n": 0}

    async def _fake_move(**_kwargs):
        moves["n"] += 1
        return True

    async def _fake_stop(token=None):
        _ = token
        return True

    client.ptz_continuous_move = _fake_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    # Sowohl bei Speed 3/8 als auch 1.0 genau 1 ContinuousMove
    assert await client.ptz_command("right", speed=3 / 8) is True
    assert moves["n"] == 1

    moves["n"] = 0
    assert await client.ptz_command("right", speed=1.0) is True
    assert moves["n"] == 1


@pytest.mark.asyncio
async def test_xmsoap_ptz_command_returns_true_on_success():
    """Erfolgreiches ContinuousMove → ptz_command gibt True zurück."""
    client = XMSoapClient(host="192.168.1.61")

    async def _fake_move(**_):
        return True

    async def _fake_stop(**_):
        return True

    client.ptz_continuous_move = _fake_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1.0) is True


@pytest.mark.asyncio
async def test_xmsoap_ptz_command_fails_without_any_movement():
    client = XMSoapClient(host="192.168.1.61")

    async def _fake_move(**_kwargs):
        return False

    client.ptz_continuous_move = _fake_move  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1.0) is False


# ── PTZ: Coordinator-Fallback (Direct-SOAP) ──────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_ptz_sends_single_pulse():
    """Schlägt der XMSoapClient-Primärpfad fehl (conftest-OfflineStub), sendet
    der Direct-SOAP-Fallback genau 1 ContinuousMove (proportionale Dauer)."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        bodies.append(body)
        if "ContinuousMove" in body:
            return "<tptz:ContinuousMoveResponse/>"
        if "tptz:Stop" in body:
            return "<tptz:StopResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("left", speed=4) is True

    move_bodies = [b for b in bodies if "ContinuousMove" in b]
    stop_bodies = [b for b in bodies if "tptz:Stop" in b]
    assert len(move_bodies) == 1  # 1 Tap = 1 Puls
    assert len(stop_bodies) == 1  # Puls wird gestoppt
