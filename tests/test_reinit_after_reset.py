"""Re-Initialisierung nach Reset (Fix v2.2.52).

Deckt die drei Ursachen ab, aus denen die Integration nach einem Reset nur
durch manuelles Neuladen zurueckkam:

1. ``async_setup()`` konnte gar nicht fehlschlagen -> kein ConfigEntryNotReady
   -> kein Wiederholungsversuch durch HA.
2. Die einmaligen Geraeteabfragen liefen nur in ``async_setup()`` -- kam die
   Kamera zurueck, blieb ihr Zustand auf dem Stand des Ausfalls.
3. ``async_reboot()`` raeumte den alten Zustand nicht ab.
"""
import asyncio
import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from custom_components.wjg_camera.coordinator import (
    BOOTSTRAP_MAX_BACKOFF,
    BOOTSTRAP_RETRY_INTERVAL,
)
from tests_helpers import (
    get_private_attr as _get_private_attr,
    make_coordinator as _make_coordinator,
    set_private_attr as _set_private_attr,
)


class DummyEntry:
    def __init__(self, data, options=None):
        self.data = data
        self.entry_id = "dummy"
        self.options = options or {}


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _entry(protocol="onvif", host="192.0.2.10"):
    return DummyEntry(
        {
            "host": host,
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": protocol,
            "onvif_port": 8899,
        }
    )


def _coordinator(protocol="onvif", host="192.0.2.10"):
    return _make_coordinator(DummyHass(), _entry(protocol, host))


def _neutralize_bootstrap_calls(coordinator):
    """Alle Netzwerk-Schritte des Bootstraps durch No-Ops ersetzen."""

    async def _noop():
        return None

    for name in (
        "_async_bootstrap_onvif_service_paths",
        "async_resolve_rtsp_path",
        "async_fetch_device_info",
        "async_fetch_imaging_settings",
        "async_ptz_get_presets",
        "async_fetch_audio_settings",
    ):
        _set_private_attr(coordinator, name, _noop)


# -- Ursache 1: async_setup muss fehlschlagen koennen ----------------------

@pytest.mark.asyncio
async def test_setup_raises_when_no_port_answers():
    """Stumme Kamera -> ConnectionError, damit HA das Setup wiederholt."""
    coordinator = _coordinator()

    async def _unreachable(_port, timeout=2.0):
        return False

    _set_private_attr(coordinator, "_tcp_port_reachable", _unreachable)

    with pytest.raises(ConnectionError):
        await coordinator.async_setup()


@pytest.mark.asyncio
async def test_setup_failure_leaves_no_session_or_tasks():
    """Das Gate steht vor ClientSession und Event-Tasks -- nichts leckt."""
    coordinator = _coordinator()

    async def _unreachable(_port, timeout=2.0):
        return False

    _set_private_attr(coordinator, "_tcp_port_reachable", _unreachable)

    with pytest.raises(ConnectionError):
        await coordinator.async_setup()

    assert _get_private_attr(coordinator, "_session") is None
    assert _get_private_attr(coordinator, "_event_task") is None
    assert _get_private_attr(coordinator, "_rtsp_motion_task") is None
    assert _get_private_attr(coordinator, "_udp_monitor_task") is None


@pytest.mark.asyncio
async def test_any_port_reachable_true_on_first_open_port():
    coordinator = _coordinator()
    probed = []

    async def _only_onvif(port, timeout=2.0):
        probed.append(port)
        return port == 8899

    _set_private_attr(coordinator, "_tcp_port_reachable", _only_onvif)

    assert await coordinator._async_any_port_reachable() is True
    assert 8899 in probed


# -- Ursache 2: Wiederholung der Einmal-Abfragen ---------------------------

@pytest.mark.asyncio
async def test_bootstrap_needs_a_read_value_not_just_absence_of_error():
    """Stumme ONVIF-Kamera wirft nicht -- 'kein Fehler' darf nicht zaehlen."""
    coordinator = _coordinator()
    _neutralize_bootstrap_calls(coordinator)

    assert await coordinator.async_bootstrap_device() is False
    assert _get_private_attr(coordinator, "_bootstrapped") is False


@pytest.mark.asyncio
async def test_bootstrap_marks_done_when_device_info_arrives():
    coordinator = _coordinator()
    _neutralize_bootstrap_calls(coordinator)

    async def _device_info():
        _set_private_attr(coordinator, "_fw_version", "V5.00.R02")

    _set_private_attr(coordinator, "async_fetch_device_info", _device_info)

    assert await coordinator.async_bootstrap_device() is True
    assert _get_private_attr(coordinator, "_bootstrapped") is True


@pytest.mark.asyncio
async def test_update_data_clears_flag_when_camera_disappears():
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_bootstrapped", True)

    async def _unreachable(_port, timeout=2.0):
        return False

    _set_private_attr(coordinator, "_tcp_port_reachable", _unreachable)

    data = await coordinator._async_update_data()

    assert data["available"] is False
    assert _get_private_attr(coordinator, "_bootstrapped") is False


@pytest.mark.asyncio
async def test_update_data_schedules_reinit_when_camera_returns():
    """Der Kern des Fixes: Rueckkehr loest den Bootstrap ohne Neuladen aus."""
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_bootstrapped", False)

    async def _reachable(_port, timeout=2.0):
        return True

    _set_private_attr(coordinator, "_tcp_port_reachable", _reachable)

    bootstrap_runs = {"count": 0}

    async def _fake_bootstrap():
        bootstrap_runs["count"] += 1
        _set_private_attr(coordinator, "_bootstrapped", True)
        return True

    _set_private_attr(coordinator, "async_bootstrap_device", _fake_bootstrap)

    data = await coordinator._async_update_data()
    assert data["available"] is True

    task = _get_private_attr(coordinator, "_bootstrap_task")
    assert task is not None
    await task

    assert bootstrap_runs["count"] == 1
    assert _get_private_attr(coordinator, "_bootstrapped") is True


@pytest.mark.asyncio
async def test_successful_reinit_invalidates_pullpoint_subscription():
    """Nach dem Kamera-Reset ist die alte Subscription tot -- Loop legt neu an."""
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events?sub=alt")

    async def _fake_bootstrap():
        return True

    _set_private_attr(coordinator, "async_bootstrap_device", _fake_bootstrap)

    await coordinator._async_bootstrap_and_notify()

    assert _get_private_attr(coordinator, "_event_pullpoint_path") == ""


@pytest.mark.asyncio
async def test_failed_reinit_keeps_pullpoint_untouched():
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events?sub=alt")

    async def _fake_bootstrap():
        return False

    _set_private_attr(coordinator, "async_bootstrap_device", _fake_bootstrap)

    await coordinator._async_bootstrap_and_notify()

    assert _get_private_attr(coordinator, "_event_pullpoint_path") == "/onvif/Events?sub=alt"


@pytest.mark.asyncio
async def test_schedule_bootstrap_throttles_within_retry_interval():
    """Eine halb antwortende Kamera darf nicht im 10-Sekunden-Takt geprobt werden."""
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_last_bootstrap_attempt", time.monotonic())

    coordinator._schedule_bootstrap()

    assert _get_private_attr(coordinator, "_bootstrap_task") is None


@pytest.mark.asyncio
async def test_schedule_bootstrap_runs_after_retry_interval():
    coordinator = _coordinator()
    _set_private_attr(
        coordinator,
        "_last_bootstrap_attempt",
        time.monotonic() - BOOTSTRAP_RETRY_INTERVAL - 1,
    )

    async def _fake_bootstrap():
        return False

    _set_private_attr(coordinator, "async_bootstrap_device", _fake_bootstrap)

    coordinator._schedule_bootstrap()

    task = _get_private_attr(coordinator, "_bootstrap_task")
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_schedule_bootstrap_does_not_stack_a_second_task():
    """Ein laufender Bootstrap darf nicht ein zweites Mal angestossen werden."""
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_last_bootstrap_attempt", 0.0)

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_bootstrap():
        started.set()
        await release.wait()
        return False

    _set_private_attr(coordinator, "async_bootstrap_device", _slow_bootstrap)

    coordinator._schedule_bootstrap()
    first = _get_private_attr(coordinator, "_bootstrap_task")
    await started.wait()

    _set_private_attr(coordinator, "_last_bootstrap_attempt", 0.0)
    coordinator._schedule_bootstrap()

    assert _get_private_attr(coordinator, "_bootstrap_task") is first
    release.set()
    await first


# -- Ursache 3: Reboot raeumt ab -------------------------------------------

@pytest.mark.asyncio
async def test_reboot_invalidates_device_state():
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_bootstrapped", True)
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events?sub=alt")

    async def _soap_ok(_service, _body, **_kwargs):
        return "<tds:SystemRebootResponse/>"

    _set_private_attr(coordinator, "_onvif_soap_for", _soap_ok)

    _set_private_attr(coordinator, "_bootstrap_backoff", BOOTSTRAP_MAX_BACKOFF)

    assert await coordinator.async_reboot() is True
    assert _get_private_attr(coordinator, "_bootstrapped") is False
    assert _get_private_attr(coordinator, "_event_pullpoint_path") == ""
    assert _get_private_attr(coordinator, "_last_bootstrap_attempt") == 0.0
    # Ein angewachsener Backoff darf den Wiederanlauf nach dem Reset nicht
    # ausbremsen -- der Reboot ist ein bekannter Neuanfang.
    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_RETRY_INTERVAL


@pytest.mark.asyncio
async def test_failed_reboot_keeps_device_state():
    coordinator = _coordinator()
    _set_private_attr(coordinator, "_bootstrapped", True)
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Events?sub=alt")

    async def _soap_fault(_service, _body, **_kwargs):
        return "<s:Fault/>"

    _set_private_attr(coordinator, "_onvif_soap_for", _soap_fault)

    assert await coordinator.async_reboot() is False
    assert _get_private_attr(coordinator, "_bootstrapped") is True
    assert _get_private_attr(coordinator, "_event_pullpoint_path") == "/onvif/Events?sub=alt"


# -- Backoff: erreichbare, aber stumme Kamera nicht dauerprobent -----------

@pytest.mark.asyncio
async def test_backoff_doubles_while_bootstrap_stays_without_proof():
    coordinator = _coordinator()
    _neutralize_bootstrap_calls(coordinator)

    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_RETRY_INTERVAL

    await coordinator.async_bootstrap_device()
    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_RETRY_INTERVAL * 2

    await coordinator.async_bootstrap_device()
    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_RETRY_INTERVAL * 4


@pytest.mark.asyncio
async def test_backoff_is_capped():
    coordinator = _coordinator()
    _neutralize_bootstrap_calls(coordinator)
    _set_private_attr(coordinator, "_bootstrap_backoff", BOOTSTRAP_MAX_BACKOFF)

    await coordinator.async_bootstrap_device()

    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_MAX_BACKOFF


@pytest.mark.asyncio
async def test_backoff_resets_after_successful_bootstrap():
    coordinator = _coordinator()
    _neutralize_bootstrap_calls(coordinator)
    _set_private_attr(coordinator, "_bootstrap_backoff", BOOTSTRAP_MAX_BACKOFF)

    async def _device_info():
        _set_private_attr(coordinator, "_serial_number", "SN-4711")

    _set_private_attr(coordinator, "async_fetch_device_info", _device_info)

    assert await coordinator.async_bootstrap_device() is True
    assert _get_private_attr(coordinator, "_bootstrap_backoff") == BOOTSTRAP_RETRY_INTERVAL


@pytest.mark.asyncio
async def test_http_only_protocol_needs_no_reinit():
    """http_only/xm_sdk halten keinen ONVIF-Zustand -- nichts nachzuholen."""
    coordinator = _coordinator(protocol="http_only")
    _neutralize_bootstrap_calls(coordinator)

    assert await coordinator.async_bootstrap_device() is True
