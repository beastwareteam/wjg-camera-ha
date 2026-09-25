"""Tests für Netzlast-Steuerung (Motion-Optionen, v2.2.40) und PTZ-Speed-Verhalten.

Hintergrund: Kanal 2 (RTSP-Bildvergleich) und die Auto-Aufnahme erzeugten eine
dauerhafte Netzwerkflut (behoben in v2.2.40). Ab v2.2.42 steuert die
Geschwindigkeitsstufe die HALTEDAUER eines einzelnen ContinuousMove (Kamera
fährt bis Stop, live verifiziert): 1 Tap = 1 Move, Halt ∝ Speed (Stufe 1 → kurz
0.2 s, Stufe 8 → lang 1.5 s).
"""
import asyncio
import contextlib
import io
import logging
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
async def test_async_setup_does_not_start_onvif_event_loop_when_option_disabled(monkeypatch, caplog):
    """Kanal 1 abschaltbar: Die XM-3820 liefert dort nur ismotion=false, jedes
    PullMessages belegt aber ~1 s ihren seriellen SOAP-Server (PTZ-Latenz).
    Kanal 2 und UDP laufen unabhängig davon weiter."""
    caplog.set_level(logging.INFO)
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_onvif_events": False,
    }))
    try:
        await _setup_with_mocks(coordinator, monkeypatch)

        assert _get_private_attr(coordinator, "_event_task") is None
        assert _get_private_attr(coordinator, "_rtsp_motion_task") is not None
        assert _get_private_attr(coordinator, "_udp_monitor_task") is not None
        # Startlog nennt nur die tatsächlich laufenden Kanäle
        started = [r.getMessage() for r in caplog.records if "Motion-Detection (" in r.getMessage()]
        assert started and "ONVIF-Ereignisse +" not in started[-1]
        assert "per Option aus: ONVIF-Ereignisse" in started[-1]
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


def _make_png_with_patch(base: int, patch: int, patch_w: int, patch_h: int) -> bytes:
    """80x60-Graubild (verlustfrei) mit Muster, damit es wie ein echtes Frame
    > 200 Byte groß ist; links oben ist ein Rechteck um (patch - base) heller."""
    delta = patch - base
    img = Image.new("L", (80, 60))
    img.putdata([
        base + ((x * 7 + y * 13) % 40) + (delta if x < patch_w and y < patch_h else 0)
        for y in range(60) for x in range(80)
    ])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_process_rtsp_motion_frame_pixel_threshold_is_configurable():
    """Grauwert-Differenz 20 auf 10 % des Bildes: mit Standard (Pixel-Schwelle
    30) keine Bewegung, mit Option 15 (+ Auslöseschwelle 3 %) Bewegung."""
    before = _make_png_with_patch(100, 100, 24, 20)
    after = _make_png_with_patch(100, 120, 24, 20)   # 480/4800 = 10 %, Diff 20

    default = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    default._trigger_motion_recording = AsyncMock()
    default._process_rtsp_motion_frame(after, before)
    await asyncio.sleep(0)
    default._trigger_motion_recording.assert_not_called()

    tuned = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_pixel_threshold": 15, "motion_rtsp_trigger_percent": 3.0,
    }))
    tuned._trigger_motion_recording = AsyncMock()
    tuned._process_rtsp_motion_frame(after, before)
    await asyncio.sleep(0)
    tuned._trigger_motion_recording.assert_called_once()


@pytest.mark.asyncio
async def test_process_rtsp_motion_frame_trigger_percent_is_configurable():
    """Starke Änderung auf 4 % des Bildes: mit Standard (6 %) keine Bewegung,
    mit Auslöseschwelle 3 % Bewegung."""
    before = _make_png_with_patch(50, 50, 16, 12)
    after = _make_png_with_patch(50, 200, 16, 12)    # 192/4800 = 4 %, Diff 150

    default = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    default._trigger_motion_recording = AsyncMock()
    default._process_rtsp_motion_frame(after, before)
    await asyncio.sleep(0)
    default._trigger_motion_recording.assert_not_called()

    tuned = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA), options={
        "motion_rtsp_trigger_percent": 3.0,
    }))
    tuned._trigger_motion_recording = AsyncMock()
    tuned._process_rtsp_motion_frame(after, before)
    await asyncio.sleep(0)
    tuned._trigger_motion_recording.assert_called_once()


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


def test_ptz_move_duration_scales_monotonically_with_speed():
    """Haltedauer: Stufe 1 = 0 s (Stop direkt nach der Move-Antwort, live
    "genau richtig"), danach streng steigend und progressiv (jeder Schritt
    mindestens so groß wie der vorige) — benachbarte Stufen spürbar verschieden."""
    table = xm_soap_module.PTZ_MOVE_DURATIONS_DEFAULT
    assert len(table) == 8
    assert table[0] == 0.0
    assert all(b > a for a, b in zip(table, table[1:]))
    steps = [round(b - a, 6) for a, b in zip(table, table[1:])]
    assert all(b >= a for a, b in zip(steps, steps[1:]))


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_hold_counts_from_move_ack(monkeypatch):
    """Die Haltedauer zählt ab der Move-ANTWORT: Solange die Antwort aussteht,
    wartet der Move nur in der Warteschlange der Kamera (live v2.2.58) — ein
    Abziehen dieser Zeit ließ Stufe 2/4 manchmal wie Stufe 1 wirken."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.0,) + (0.3,) * 7)
    client = XMSoapClient(host="192.168.1.61")
    slept: list[float] = []
    stops = {"n": 0}
    real_sleep = asyncio.sleep

    async def _record_sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    async def _slow_move(**_kwargs):
        time.sleep(0.1)  # simulierte Wartezeit in der Kamera-Warteschlange
        return True

    async def _fake_stop(token=None):
        _ = token
        stops["n"] += 1
        return True

    client.ptz_continuous_move = _slow_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]
    monkeypatch.setattr(xm_soap_module.asyncio, "sleep", _record_sleep)

    assert await client.ptz_command("right", speed=2 / 8) is True
    assert slept == [pytest.approx(0.3)]  # volle Haltedauer, nichts abgezogen
    assert stops["n"] == 1

    slept.clear()
    assert await client.ptz_command("right", speed=1 / 8) is True
    assert slept == []  # Stufe 1: Stop direkt nach der Move-Antwort


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_waits_for_move_ack_before_stop(monkeypatch):
    """Stop erst NACH der Move-Antwort — ein früher Stop brachte live keinen
    kürzeren Klick, erzeugte aber Ruckeln (v2.2.56)."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.05,) * 8)
    client = XMSoapClient(host="192.168.1.61")
    events: list[str] = []

    async def _slow_move(**_kwargs):
        await asyncio.sleep(0.2)  # Kamera antwortet erst nach 0,2 s
        events.append("move_ack")
        return True

    async def _fake_stop(token=None):
        _ = token
        events.append("stop")
        return True

    client.ptz_continuous_move = _slow_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1 / 8) is True
    assert events == ["move_ack", "stop"]


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_wrong_token_returns_false(monkeypatch):
    """Scheitert der Move (falscher Token), False zurückgeben, damit der
    Coordinator den nächsten Token probiert."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.01,) * 8)
    client = XMSoapClient(host="192.168.1.61")

    async def _slow_failing_move(**_kwargs):
        await asyncio.sleep(0.1)
        return False

    async def _fake_stop(token=None):
        _ = token
        return True

    client.ptz_continuous_move = _slow_failing_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1 / 8) is False


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_failed_move_still_sends_stop(monkeypatch):
    """Auch wenn die Move-Antwort als Fehler zurückkommt (HTTP-/Parse-Fehler
    → None), wird vorsorglich gestoppt — die Kamera kann den Move trotzdem
    angenommen haben."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.01,) * 8)
    client = XMSoapClient(host="192.168.1.61")
    events: list[str] = []

    async def _slow_failing_move(**_kwargs):
        await asyncio.sleep(0.1)
        events.append("move_fail")
        return False

    async def _fake_stop(token=None):
        _ = token
        events.append("stop")
        return True

    client.ptz_continuous_move = _slow_failing_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1 / 8) is False
    assert events == ["move_fail", "stop"]


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_failed_move_stop_is_retried(monkeypatch):
    """Vorsorglicher Stop nach Move-Fehler wird bei Fehlschlag 1× wiederholt."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.01,) * 8)
    client = XMSoapClient(host="192.168.1.61")
    stops = {"n": 0}

    async def _failing_move(**_kwargs):
        return False

    async def _failing_stop(token=None):
        _ = token
        stops["n"] += 1
        return False

    client.ptz_continuous_move = _failing_move  # type: ignore[method-assign]
    client.ptz_stop = _failing_stop  # type: ignore[method-assign]

    assert await client.ptz_command("left", speed=1 / 8) is False
    assert stops["n"] == 2


@pytest.mark.asyncio
async def test_xmsoap_ptz_click_cancelled_still_stops(monkeypatch):
    """Wird der Klick abgebrochen, während die Move-Antwort aussteht, wird
    der Move verworfen und trotzdem ein Stop gesendet."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (5.0,) * 8)
    client = XMSoapClient(host="192.168.1.61")
    events: list[str] = []

    async def _hanging_move(**_kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.append("move_cancelled")
            raise
        return True

    async def _fake_stop(token=None):
        _ = token
        events.append("stop")
        return True

    client.ptz_continuous_move = _hanging_move  # type: ignore[method-assign]
    client.ptz_stop = _fake_stop  # type: ignore[method-assign]

    task = asyncio.ensure_future(client.ptz_command("left", speed=1 / 8))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["move_cancelled", "stop"]


@pytest.mark.asyncio
async def test_ptz_command_suppresses_motion_triggers():
    """Während und kurz nach einem PTZ-Befehl lösen Bewegungs-Kanäle keine
    Aufnahme aus (die Kamera bewegt sich selbst)."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    assert coordinator._ptz_motion_suppressed() is False  # pylint: disable=protected-access

    seen: list[bool] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        seen.append(coordinator._ptz_motion_suppressed())  # pylint: disable=protected-access
        if "ContinuousMove" in body:
            return "<tptz:ContinuousMoveResponse/>"
        if "tptz:Stop" in body:
            return "<tptz:StopResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("left", speed=1) is True
    assert seen and all(seen)  # während des Befehls unterdrückt
    assert coordinator._ptz_motion_suppressed() is True  # Nachlauf-Fenster  # pylint: disable=protected-access

    # Nachlauf abgelaufen → Bewegung zählt wieder
    _set_private_attr(coordinator, "_ptz_quiet_until", time.time() - 1)
    assert coordinator._ptz_motion_suppressed() is False  # pylint: disable=protected-access


def test_ptz_motion_quiet_is_nesting_safe():
    """Überlappende PTZ-Befehle: Das Nachlauf-Fenster beginnt erst, wenn der
    LETZTE Befehl endet — vorher bleibt die Unterdrückung unbegrenzt aktiv."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    quiet = coordinator._ptz_motion_quiet  # pylint: disable=protected-access
    with quiet():
        with quiet():
            pass
        # innerer Befehl fertig, äußerer läuft noch → weiterhin unbegrenzt
        assert _get_private_attr(coordinator, "_ptz_quiet_until") == float("inf")
    assert _get_private_attr(coordinator, "_ptz_quiet_until") < float("inf")
    assert coordinator._ptz_motion_suppressed() is True  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_ptz_home_and_preset_suppress_motion(monkeypatch):
    """Home- und Preset-Fahrten bewegen die Kamera ebenfalls → gleiche
    Bewegungs-Unterdrückung wie beim Richtungs-Klick."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    seen: list[bool] = []

    class _Soap:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def ptz_goto_home(self, **_kwargs):
            seen.append(coordinator._ptz_motion_suppressed())  # pylint: disable=protected-access
            return True

        async def ptz_goto_preset(self, **_kwargs):
            seen.append(coordinator._ptz_motion_suppressed())  # pylint: disable=protected-access
            return True

    monkeypatch.setattr(coordinator, "_soap", _Soap)
    assert await coordinator.async_ptz_home() is True
    assert await coordinator.async_ptz_goto_preset("1") is True
    assert seen == [True, True]
    assert coordinator._ptz_motion_suppressed() is True  # pylint: disable=protected-access


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
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.01,) * 7 + (1.5,))
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
    # Haltedauer 1,5 s ab Move-Antwort + Puffer für die Zeit bis zur Antwort
    expected = 1.5 + coordinator_module.PTZ_FALLBACK_TIMEOUT_MARGIN_SECS
    assert timed and f"<tptz:Timeout>PT{expected:.2f}S</tptz:Timeout>" in timed[-1]


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_fallback_ptz_hold_counts_from_move_ack(monkeypatch):
    """Fallback: Haltedauer ab der Antwort des ERFOLGREICHEN ContinuousMove —
    fehlgeschlagene Varianten/Tokens verkürzen den Klick nicht."""
    monkeypatch.setattr(xm_soap_module, "PTZ_MOVE_DURATIONS", (0.3,) * 8)
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _record_sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        if "ContinuousMove" in body:
            if "ProfileToken>bad<" in body or "<tptz:ContinuousMove>" in body:
                # fehlgeschlagener Token bzw. v20-Variante, langsam → darf
                # NICHT von der Haltedauer abgezogen werden
                time.sleep(0.2)
                return ""
            time.sleep(0.1)  # Antwortzeit des erfolgreichen (v10-)Moves
            return "<tptz10:ContinuousMoveResponse/>"
        if "tptz:Stop" in body:
            return "<tptz:StopResponse/>"
        return ""

    async def _no_legacy(*_args, **_kwargs):
        return ""

    async def _tokens():
        return ["bad", "000"]

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_soap_legacy_for", _no_legacy)
    _set_private_attr(coordinator, "_async_candidate_ptz_profile_tokens", _tokens)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", _record_sleep)

    assert await coordinator.async_ptz_command("left", speed=4) is True
    assert _get_private_attr(coordinator, "_onvif_profile_tokens")["000"] == "000"
    # Haltedauer ab der Move-Antwort — weder Fehlversuche noch die Antwortzeit
    # werden abgezogen
    assert slept == [pytest.approx(0.3)]


class _PtzTestSoap:
    """Fake-XMSoapClient für async_ptz_test: zeichnet Aufrufe auf."""

    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def ptz_relative_move(self, **kwargs):
        self.calls.append(("relative", kwargs))
        return True

    async def ptz_continuous_move(self, **kwargs):
        self.calls.append(("continuous", kwargs))
        return True

    async def ptz_stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        return True


@pytest.mark.asyncio
async def test_ptz_test_relative_sends_single_relative_move_without_stop(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    calls: list = []
    monkeypatch.setattr(coordinator, "_soap", lambda: _PtzTestSoap(calls))

    result = await coordinator.async_ptz_test("left", "relative", 0.05)

    assert [c[0] for c in calls] == ["relative"]
    assert calls[0][1]["pan"] == pytest.approx(-0.05)
    assert calls[0][1]["tilt"] == pytest.approx(0.0)
    assert result["akzeptiert"] is True and result["methode"] == "relative"


@pytest.mark.asyncio
async def test_ptz_test_timeout_sends_timeout_and_late_safety_stop(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    calls: list = []
    slept: list[float] = []

    async def _record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(coordinator, "_soap", lambda: _PtzTestSoap(calls))
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", _record_sleep)

    result = await coordinator.async_ptz_test("up", "timeout", 0.3)

    assert [c[0] for c in calls] == ["continuous", "stop"]
    assert calls[0][1]["timeout"] == pytest.approx(0.3)
    assert calls[0][1]["tilt"] == pytest.approx(1.0)
    # Sicherheits-Stop erst deutlich nach dem erwarteten Selbst-Stopp
    assert slept and slept[0] >= 0.3 + coordinator_module.PTZ_TEST_SAFETY_STOP_SECS - 0.1
    assert result["akzeptiert"] is True


def test_xmsoap_continuous_move_timeout_element():
    """<Timeout> nur, wenn angefordert — der Standard-Klick bleibt unverändert."""
    client = XMSoapClient(host="192.168.1.61")
    bodies: list[str] = []

    async def _fake_post(_endpoint, body, auth=True):
        _ = auth
        bodies.append(body)
        return object()

    client._post = _fake_post  # type: ignore[method-assign]  # pylint: disable=protected-access
    asyncio.run(client.ptz_continuous_move(pan=1.0))
    asyncio.run(client.ptz_continuous_move(pan=1.0, timeout=0.25))
    assert "Timeout" not in bodies[0]
    assert "<tptz:Timeout>PT0.25S</tptz:Timeout>" in bodies[1]


class _TokenPtzTestSoap(_PtzTestSoap):
    """Nur Token "002" funktioniert (wie bei Kameras mit anderem PTZ-Profil)."""

    async def ptz_relative_move(self, **kwargs):
        self.calls.append(("relative", kwargs))
        return kwargs.get("token") == "002"


@pytest.mark.asyncio
async def test_ptz_test_tries_candidate_tokens_and_remembers(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    calls: list = []
    monkeypatch.setattr(coordinator, "_soap", lambda: _TokenPtzTestSoap(calls))
    _set_private_attr(coordinator, "_preferred_onvif_profile_token", "")

    result = await coordinator.async_ptz_test("right", "relative", 0.05)

    assert [c[1]["token"] for c in calls] == ["000", "001", "002"]
    assert result["akzeptiert"] is True and result["token"] == "002"
    assert _get_private_attr(coordinator, "_preferred_onvif_profile_token") == "002"


@pytest.mark.asyncio
async def test_ptz_test_cancelled_during_wait_still_stops(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    calls: list = []
    monkeypatch.setattr(coordinator, "_soap", lambda: _PtzTestSoap(calls))

    task = asyncio.ensure_future(coordinator.async_ptz_test("left", "timeout", 2.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [c[0] for c in calls] == ["continuous", "stop"]


def test_ptz_test_schema_limits_relative_value():
    from custom_components.wjg_camera import (  # pylint: disable=import-outside-toplevel
        _SVC_SCHEMA_PTZ_TEST,
    )
    import voluptuous as vol  # pylint: disable=import-outside-toplevel

    base = {"entity_id": "camera.x", "direction": "left"}
    assert _SVC_SCHEMA_PTZ_TEST({**base, "method": "relative", "value": 1.0})
    assert _SVC_SCHEMA_PTZ_TEST({**base, "method": "timeout", "value": 3.0})
    with pytest.raises(vol.Invalid):
        _SVC_SCHEMA_PTZ_TEST({**base, "method": "relative", "value": 2.0})


def test_get_coordinator_without_fallback_ignores_foreign_entity(monkeypatch):
    """ptz_test nutzt allow_fallback=False: Eine Entity, die zu keinem
    WJG-Config-Entry gehört, darf nicht auf eine beliebige WJG-Kamera fallen."""
    import custom_components.wjg_camera as integration  # pylint: disable=import-outside-toplevel
    from homeassistant.helpers import entity_registry as er  # pylint: disable=import-outside-toplevel

    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))

    class _Registry:
        def async_get(self, _entity_id):
            return None  # fremde Kamera: nicht in der WJG-Registry

    hass = DummyHass()
    hass.data = {integration.DOMAIN: {"entry1": coordinator}}
    monkeypatch.setattr(er, "async_get", lambda _hass: _Registry())

    get = integration._get_coordinator  # pylint: disable=protected-access
    assert get(hass, "camera.fremd") is coordinator  # alter Fallback bleibt für Zoom/Motion
    assert get(hass, "camera.fremd", allow_fallback=False) is None


@pytest.mark.asyncio
async def test_event_loop_pauses_and_cancels_pull_during_ptz():
    """Die XM-Kamera arbeitet SOAP seriell ab: Während PTZ darf kein
    PullMessages laufen. Ein laufendes wird abgebrochen, danach geht es mit
    derselben Subscription weiter (kein Neuaufbau)."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    _set_private_attr(coordinator, "_session", object())
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events_Sub_0")
    pulls = {"started": 0, "cancelled": 0}

    async def _hanging_pull():
        pulls["started"] += 1
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pulls["cancelled"] += 1
            raise
        return True

    coordinator.async_onvif_pull_messages_once = _hanging_pull  # type: ignore[method-assign]
    loop_task = asyncio.ensure_future(
        _get_private_attr(coordinator, "_async_onvif_event_loop")()
    )
    try:
        await asyncio.sleep(0.05)
        assert pulls == {"started": 1, "cancelled": 0}

        with coordinator._ptz_motion_quiet():  # pylint: disable=protected-access
            await asyncio.sleep(0.05)
            # laufendes PullMessages abgebrochen, kein neues während PTZ
            assert pulls == {"started": 1, "cancelled": 1}

        await asyncio.sleep(0.05)
        assert pulls["started"] == 2  # nach PTZ weiter
        assert _get_private_attr(coordinator, "_event_pullpoint_path") == "/onvif/Events_Sub_0"
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


@pytest.mark.asyncio
async def test_options_update_reloads_entry():
    """Geänderte Optionen (z. B. Kanal 2 aus) wirken sofort per Reload."""
    import custom_components.wjg_camera as integration  # pylint: disable=import-outside-toplevel

    reloaded: list[str] = []

    class _Entries:
        async def async_reload(self, entry_id):
            reloaded.append(entry_id)

    hass = DummyHass()
    hass.config_entries = _Entries()
    entry = DummyEntry(dict(ONVIF_DATA))
    entry.entry_id = "e1"
    await integration._async_options_updated(hass, entry)  # pylint: disable=protected-access
    assert reloaded == ["e1"]


def test_translations_cover_all_option_fields():
    """HA erwartet "options" auf oberster Ebene (lag früher in "config" →
    Formular zeigte Rohnamen). Jedes Optionsfeld braucht eine Beschriftung."""
    import json  # pylint: disable=import-outside-toplevel
    import pathlib  # pylint: disable=import-outside-toplevel
    import re  # pylint: disable=import-outside-toplevel

    base = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "wjg_camera"
    flow_src = (base / "config_flow.py").read_text(encoding="utf-8")
    init_src = (base / "__init__.py").read_text(encoding="utf-8")
    options_part = flow_src[flow_src.index("class WJGOptionsFlow"):]
    const_names = set(re.findall(r"vol\.(?:Optional|Required)\(\s*(CONF_[A-Z_]+)", options_part))
    keys = {
        re.search(rf'^{name}: Final = "([^"]+)"', init_src, re.M).group(1)  # type: ignore[union-attr]
        for name in const_names
    }
    assert keys, "keine Optionsfelder gefunden"
    for name in ("strings.json", "translations/de.json", "translations/en.json"):
        data = json.loads((base / name).read_text(encoding="utf-8"))
        assert "options" in data and "options" not in data["config"], name
        labels = data["options"]["step"]["init"]["data"]
        assert keys <= set(labels), f"{name}: fehlend {sorted(keys - set(labels))}"


@pytest.mark.asyncio
async def test_event_loop_cancels_subscription_creation_during_ptz():
    """Auch das Anlegen der Subscription wird beim PTZ-Start abgebrochen;
    während PTZ folgt kein PullMessages, danach neuer Versuch."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    _set_private_attr(coordinator, "_session", object())
    _set_private_attr(coordinator, "_event_pullpoint_path", "")
    calls = {"create": 0, "create_cancelled": 0, "pull": 0}

    async def _hanging_create():
        calls["create"] += 1
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            calls["create_cancelled"] += 1
            raise
        return True

    async def _pull():
        calls["pull"] += 1
        await asyncio.sleep(10)
        return True

    coordinator.async_onvif_create_pullpoint = _hanging_create  # type: ignore[method-assign]
    coordinator.async_onvif_pull_messages_once = _pull  # type: ignore[method-assign]
    loop_task = asyncio.ensure_future(
        _get_private_attr(coordinator, "_async_onvif_event_loop")()
    )
    try:
        await asyncio.sleep(0.05)
        with coordinator._ptz_motion_quiet():  # pylint: disable=protected-access
            await asyncio.sleep(0.05)
            assert calls == {"create": 1, "create_cancelled": 1, "pull": 0}
        await asyncio.sleep(0.05)
        assert calls["create"] == 2 and calls["pull"] == 0
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


@pytest.mark.asyncio
async def test_event_loop_pauses_between_pulls(monkeypatch):
    """Zwischen zwei PullMessages bleibt die Kamera EVENT_PULL_PAUSE_SECS frei,
    damit PTZ-Befehle nicht in ihrer Warteschlange warten."""
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    _set_private_attr(coordinator, "_session", object())
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events_Sub_0")
    slept: list[float] = []
    pulls = {"n": 0}
    real_sleep = asyncio.sleep

    async def _pull():
        pulls["n"] += 1
        return True

    async def _record_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 2:
            raise asyncio.CancelledError
        await real_sleep(0)

    coordinator.async_onvif_pull_messages_once = _pull  # type: ignore[method-assign]
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", _record_sleep)
    with pytest.raises(asyncio.CancelledError):
        await _get_private_attr(coordinator, "_async_onvif_event_loop")()
    assert pulls["n"] == 2
    assert slept == [coordinator_module.EVENT_PULL_PAUSE_SECS] * 2
