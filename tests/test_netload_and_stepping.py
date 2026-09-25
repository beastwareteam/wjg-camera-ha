"""Tests für Netzlast-Steuerung (Motion-Optionen, v2.2.40) und PTZ-Speed-Verhalten.

Hintergrund: Kanal 2 (RTSP-Bildvergleich) und die Auto-Aufnahme erzeugten eine
dauerhafte Netzwerkflut (behoben in v2.2.40). Ab v2.2.42 steuert die
Geschwindigkeitsstufe die HALTEDAUER eines einzelnen ContinuousMove (Kamera
fährt bis Stop, live verifiziert): 1 Tap = 1 Move, Halt ∝ Speed (Stufe 1 → kurz
0.2 s, Stufe 8 → lang 1.5 s).
"""
import asyncio
import io
import os
import sys
import time
from unittest.mock import AsyncMock

import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import custom_components.wjg_camera.coordinator as coordinator_module
from custom_components.wjg_camera import xm_soap as xm_soap_module
from custom_components.wjg_camera.xm_soap import XMSoapClient
from tests_helpers import (
    call_private_async as _call_private_async,
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
    """Default seit v2.2.51: Kanal 2 AN (ONVIF liefert bei manchen Kamera-
    Firmwares dauerhaft kein echtes Motion-Event), Auto-Aufnahme AN."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))

    assert coordinator.motion_rtsp_diff_enabled is True
    assert coordinator.motion_auto_record_enabled is True
    assert coordinator.motion_rtsp_interval == 2
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
        "motion_rtsp_interval": 0,
    }))

    assert coordinator.motion_rtsp_interval == 1


async def _reachable_true() -> bool:
    """Erreichbarkeits-Gate aus async_setup() umgehen (v2.2.52)."""
    return True


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
    # conftest stellt _tcp_port_reachable auf immer-False (Offline-CI).
    # async_setup() bricht seit v2.2.52 genau darauf mit ConnectionError ab,
    # damit HA das Setup wiederholt — hier die Kamera als erreichbar melden.
    _set_private_attr(coordinator, "_async_any_port_reachable", _reachable_true)
    await coordinator.async_setup()


@pytest.mark.asyncio
async def test_async_setup_starts_rtsp_motion_loop_by_default(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    try:
        await _setup_with_mocks(coordinator, monkeypatch)

        assert _get_private_attr(coordinator, "_rtsp_motion_task") is not None
        assert _get_private_attr(coordinator, "_event_task") is not None
        assert _get_private_attr(coordinator, "_udp_monitor_task") is not None
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_async_setup_does_not_start_rtsp_motion_loop_when_option_disabled(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_diff": False,
    }))
    try:
        await _setup_with_mocks(coordinator, monkeypatch)

        assert _get_private_attr(coordinator, "_rtsp_motion_task") is None
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_async_setup_warns_but_does_not_fail_when_ffmpeg_missing(monkeypatch, caplog):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    try:
        with caplog.at_level("WARNING"):
            await _setup_with_mocks(coordinator, monkeypatch)

        assert "ffmpeg-Binary nicht im PATH gefunden" in caplog.text
    finally:
        await coordinator.async_shutdown()


# ── Kanal 2: kontinuierlicher RTSP-Motion-Stream (statt Einzel-Frame/Intervall) ──

def _make_jpeg(color: tuple) -> bytes:
    img = Image.new("RGB", (80, 60), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_process_rtsp_motion_frame_first_frame_sets_baseline_without_trigger():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    coordinator._trigger_motion_recording = AsyncMock()
    frame = _make_jpeg((10, 10, 10))

    result = coordinator._process_rtsp_motion_frame(frame, None)

    assert result == frame
    coordinator._trigger_motion_recording.assert_not_called()


def test_process_rtsp_motion_frame_no_trigger_below_threshold():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    coordinator._trigger_motion_recording = AsyncMock()
    frame = _make_jpeg((10, 10, 10))

    result = coordinator._process_rtsp_motion_frame(frame, frame)

    assert result == frame
    coordinator._trigger_motion_recording.assert_not_called()


@pytest.mark.asyncio
async def test_process_rtsp_motion_frame_triggers_on_large_diff():
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    coordinator._trigger_motion_recording = AsyncMock()
    frame1 = _make_jpeg((0, 0, 0))
    frame2 = _make_jpeg((255, 255, 255))

    result = coordinator._process_rtsp_motion_frame(frame2, frame1)
    await asyncio.sleep(0)  # geplanten ensure_future-Task laufen lassen

    assert result == frame2
    coordinator._trigger_motion_recording.assert_called_once()
    assert _get_private_attr(coordinator, "_last_motion_time") > 0


class _FakeRtspStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        raise asyncio.CancelledError


class _FakeRtspProc:
    def __init__(self, chunks):
        self.stdout = _FakeRtspStdout(chunks)
        self.returncode = None

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_rtsp_motion_loop_uses_single_persistent_stream_and_triggers_on_diff(monkeypatch):
    """Kanal 2 darf pro Bewegungs-Erkennung nur EINEN ffmpeg-Prozess mit EINER
    RTSP-Verbindung nutzen (kontinuierlicher Stream) statt wie vor v2.2.50 pro
    Check einen neuen Prozess zu starten — das war die urspruengliche
    Netzwerkflut-Ursache aus v2.2.40 bei kurzen Intervallen."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_diff": True,
        "motion_rtsp_interval": 1,
    }))
    _set_private_attr(coordinator, "_session", object())
    _set_private_attr(coordinator, "_last_motion_time", 0.0)

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", _no_sleep)

    frame1 = _make_jpeg((0, 0, 0))
    frame2 = _make_jpeg((255, 255, 255))
    process_calls = {"n": 0}

    async def _fake_create_subprocess_exec(*cmd, **_kwargs):
        process_calls["n"] += 1
        assert any(str(arg).startswith("fps=1.0000") for arg in cmd)
        # beide Frames in einem einzigen Stream-Chunk, danach Stream-Ende
        return _FakeRtspProc(chunks=[frame1 + frame2])

    monkeypatch.setattr(
        coordinator_module.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec
    )

    with pytest.raises(asyncio.CancelledError):
        await _call_private_async(coordinator, "_async_rtsp_motion_loop")

    assert process_calls["n"] == 1  # nur EIN Prozess/EINE Verbindung für beide Frames
    assert coordinator.motion_detected is True


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

    async def _fake_start(reason="manual"):
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
async def test_xmsoap_ptz_command_single_click_velocity_scales_with_speed():
    """1 Tap = genau 1 ContinuousMove + 1 Stop; Velocity = Stufe/8."""
    client = XMSoapClient(host="192.168.1.61")
    moves: list[dict] = []
    stops = {"n": 0}

    async def _fake_move(**kwargs):
        moves.append(kwargs)
        return True

    async def _fake_stop(token=None):
        _ = token
        stops["n"] += 1
        return True

    client.ptz_continuous_move = _fake_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("right", speed=1 / 8) is True
    assert len(moves) == 1 and stops["n"] == 1
    assert moves[0]["pan"] == pytest.approx(0.125)

    moves.clear()
    stops["n"] = 0
    assert await client.ptz_command("left", speed=1.0) is True
    assert len(moves) == 1 and stops["n"] == 1
    assert moves[0]["pan"] == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_xmsoap_ptz_command_retries_failed_stop_once():
    """Greift Stop nicht, wird er 1× wiederholt; Bewegung lief → True
    (kein Token-Retry im Coordinator, sonst Extra-Bewegung)."""
    client = XMSoapClient(host="192.168.1.61")
    stops = {"n": 0}

    async def _fake_move(**_kwargs):
        return True

    async def _fake_stop(token=None):
        _ = token
        stops["n"] += 1
        return False

    client.ptz_continuous_move = _fake_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1.0) is True
    assert stops["n"] == 2


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


def test_ptz_move_duration_scales_monotonically_with_speed(monkeypatch):
    """Klick-Dauer steigt streng monoton: Stufe 1 = kurz, Stufe 8 = lang."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MIN_MOVE_DURATION", 0.25)
    monkeypatch.setattr(xm_soap_module, "PTZ_MAX_MOVE_DURATION", 1.5)
    durations = [
        xm_soap_module.ptz_move_duration_for_speed(level / 8) for level in range(1, 9)
    ]
    assert durations[0] == pytest.approx(0.25)
    assert durations[-1] == pytest.approx(1.5)
    assert all(b > a for a, b in zip(durations, durations[1:]))


# ── PTZ: Coordinator-Fallback (Direct-SOAP) ──────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_ptz_sends_single_click():
    """Schlägt der XMSoapClient-Primärpfad fehl (conftest-OfflineStub), sendet
    der Direct-SOAP-Fallback genau 1 ContinuousMove (Velocity = Stufe/8)."""
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
    assert len(move_bodies) == 1  # 1 Tap = 1 Klick
    assert len(stop_bodies) == 1  # Klick wird gestoppt
    assert 'x="-0.50"' in move_bodies[0]  # Stufe 4 → Velocity 0.5



@pytest.mark.asyncio
async def test_fallback_ptz_retries_failed_stop_once():
    """Direct-SOAP-Fallback: 1 Klick, fehlgeschlagener Stop wird 1× wiederholt."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        bodies.append(body)
        if "ContinuousMove" in body:
            return "<tptz:ContinuousMoveResponse/>"
        return ""  # Stop schlägt fehl

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    await coordinator.async_ptz_command("left", speed=8)

    assert len([b for b in bodies if "ContinuousMove" in b]) == 1
    assert len([b for b in bodies if "tptz:Stop" in b]) == 2


@pytest.mark.asyncio
async def test_fallback_ptz_timeout_covers_click_duration(monkeypatch):
    """Timeout-Variante: <Timeout> muss mindestens die Klick-Dauer abdecken."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MIN_MOVE_DURATION", 0.0)
    monkeypatch.setattr(xm_soap_module, "PTZ_MAX_MOVE_DURATION", 1.5)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        bodies.append(body)
        if "ContinuousMove" in body and "<tptz:Timeout>" in body:
            return "<tptz:ContinuousMoveResponse/>"  # nur Timeout-Variante klappt
        if "tptz:Stop" in body:
            return "<tptz:StopResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("left", speed=8) is True
    timed = [b for b in bodies if "<tptz:Timeout>" in b]
    assert timed and "<tptz:Timeout>PT1.50S</tptz:Timeout>" in timed[-1]


async def _no_sleep(_seconds):
    return None
