import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import custom_components.wjg_camera as integration
from custom_components.wjg_camera.config_flow import (
    WJGCameraConfigFlow,
    WJGOptionsFlow,
    _check_host_reachable,
)
from custom_components.wjg_camera.coordinator import WJGCameraCoordinator
from homeassistant.exceptions import ConfigEntryNotReady
from tests_helpers import as_any as _as_any, private_name as _private_name


class DummyEntry:
    def __init__(self, data, entry_id="dummy-entry", options=None):
        self.data = data
        self.entry_id = entry_id
        self.options = options or {}

    def async_on_unload(self, _func):
        """DataUpdateCoordinator (HA ≥2025) registriert sich am Entry."""
        return None


class DummySocketConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_check_host_reachable_returns_first_open_port(monkeypatch):
    def fake_create_connection(address, timeout=3.0):
        _ = timeout
        host, port = address
        if host == "192.168.1.10" and port == 554:
            return DummySocketConnection()
        raise OSError("closed")

    monkeypatch.setattr("socket.create_connection", fake_create_connection)

    port = _check_host_reachable("192.168.1.10", [80, 554, 34567])

    assert port == 554


def test_check_host_reachable_returns_none_when_all_ports_closed(monkeypatch):
    def fake_create_connection(address, timeout=3.0):
        _ = address
        _ = timeout
        raise OSError("closed")

    monkeypatch.setattr("socket.create_connection", fake_create_connection)

    port = _check_host_reachable("192.168.1.10", [80, 554, 34567])

    assert port is None


@pytest.mark.asyncio
async def test_async_setup_entry_success(monkeypatch):
    created = {}

    class DummyCoordinator:
        def __init__(self, hass, entry):
            created["coordinator"] = self
            self.hass = hass
            self.entry = entry

        async def async_setup(self):
            return None

        async def async_shutdown(self):
            return None

    monkeypatch.setattr(integration, "WJGCameraCoordinator", DummyCoordinator)

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = DummyEntry({"host": "192.168.1.20"})

    result = await integration.async_setup_entry(hass, _as_any(entry))

    assert result is True
    assert integration.DOMAIN in hass.data
    assert hass.data[integration.DOMAIN][entry.entry_id] is created["coordinator"]
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, integration.PLATFORMS
    )


@pytest.mark.asyncio
async def test_async_setup_entry_raises_not_ready(monkeypatch):
    class FailingCoordinator:
        def __init__(self, hass, entry):
            self.hass = hass
            self.entry = entry

        async def async_setup(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(integration, "WJGCameraCoordinator", FailingCoordinator)

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = DummyEntry({"host": "192.168.1.21"})

    with pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(hass, _as_any(entry))

    hass.config_entries.async_forward_entry_setups.assert_not_called()


@pytest.mark.asyncio
async def test_options_flow_uses_retry_from_options_before_entry_data():
    entry = DummyEntry(
        {
            "host": "192.168.1.30",
            "protocol": integration.PROTOCOL_RTSP,
            "rtsp_path": "/stream",
            "snapshot_path": "/snap.jpg",
            "http_retries": 1,
        },
        options={"http_retries": 4},
    )
    flow = WJGOptionsFlow(_as_any(entry))

    result = await flow.async_step_init()
    schema = _as_any(result)["data_schema"].schema
    retry_key = next(key for key in schema if getattr(key, "schema", None) == integration.CONF_HTTP_RETRIES)

    assert retry_key.default() == 4


@pytest.mark.asyncio
async def test_options_flow_uses_runtime_defaults_for_protocol_and_paths_from_options():
    entry = DummyEntry(
        {
            "host": "192.168.1.30",
            "protocol": integration.PROTOCOL_RTSP,
            "rtsp_path": "/legacy-stream",
            "snapshot_path": "/legacy-snapshot.jpg",
        },
        options={
            integration.CONF_PROTOCOL: integration.PROTOCOL_ONVIF,
            integration.CONF_RTSP_PATH: "/streamtype=0",
            integration.CONF_SNAPSHOT_PATH: "/snap-current.jpg",
        },
    )
    flow = WJGOptionsFlow(_as_any(entry))

    result = await flow.async_step_init()
    schema = _as_any(result)["data_schema"].schema
    protocol_key = next(key for key in schema if getattr(key, "schema", None) == integration.CONF_PROTOCOL)
    rtsp_path_key = next(key for key in schema if getattr(key, "schema", None) == integration.CONF_RTSP_PATH)
    snapshot_path_key = next(key for key in schema if getattr(key, "schema", None) == integration.CONF_SNAPSHOT_PATH)

    assert protocol_key.default() == integration.PROTOCOL_ONVIF
    assert rtsp_path_key.default() == "/streamtype=0"
    assert snapshot_path_key.default() == "/snap-current.jpg"


@pytest.mark.asyncio
async def test_options_flow_falls_back_to_default_retry_for_legacy_entry():
    entry = DummyEntry(
        {
            "host": "192.168.1.30",
            "protocol": integration.PROTOCOL_RTSP,
            "rtsp_path": "/stream",
            "snapshot_path": "/snap.jpg",
        }
    )
    flow = WJGOptionsFlow(_as_any(entry))

    result = await flow.async_step_init()
    schema = _as_any(result)["data_schema"].schema
    retry_key = next(key for key in schema if getattr(key, "schema", None) == integration.CONF_HTTP_RETRIES)

    assert retry_key.default() == integration.DEFAULT_HTTP_RETRIES


@pytest.mark.asyncio
async def test_options_flow_create_entry_path():
    entry = DummyEntry(
        {
            "host": "192.168.1.30",
            "protocol": integration.PROTOCOL_RTSP,
            "rtsp_path": "/stream",
            "snapshot_path": "/snap.jpg",
        }
    )
    flow = WJGOptionsFlow(_as_any(entry))

    result = await flow.async_step_init({"rtsp_path": "/new"})

    typed_result = _as_any(result)
    assert typed_result["type"] == "create_entry"
    assert typed_result["data"]["rtsp_path"] == "/new"


@pytest.mark.asyncio
async def test_options_flow_exposes_onvif_override_fields():
    entry = DummyEntry(
        {
            "host": "192.168.1.31",
            "protocol": integration.PROTOCOL_ONVIF,
            "rtsp_path": "/stream",
            "snapshot_path": "/snap.jpg",
        },
        options={
            integration.CONF_ONVIF_DEVICE_PATH: "/onvif/Device",
            integration.CONF_ONVIF_PROFILE_TOKEN: "000",
            integration.CONF_ONVIF_VIDEO_SOURCE_TOKEN: "VideoSource_1",
            integration.CONF_ONVIF_SIGNAL_ITEM_KEYS: ["videoloss", "totalarm"],
        },
    )
    flow = WJGOptionsFlow(_as_any(entry))

    result = await flow.async_step_init()
    schema = _as_any(result)["data_schema"].schema
    device_key = next(
        key for key in schema
        if getattr(key, "schema", None) == integration.CONF_ONVIF_DEVICE_PATH
    )
    signal_key = next(
        key for key in schema
        if getattr(key, "schema", None) == integration.CONF_ONVIF_SIGNAL_ITEM_KEYS
    )
    profile_key = next(
        key for key in schema
        if getattr(key, "schema", None) == integration.CONF_ONVIF_PROFILE_TOKEN
    )
    video_source_key = next(
        key for key in schema
        if getattr(key, "schema", None) == integration.CONF_ONVIF_VIDEO_SOURCE_TOKEN
    )

    assert device_key.default() == "/onvif/Device"
    assert profile_key.default() == "000"
    assert video_source_key.default() == "VideoSource_1"
    assert signal_key.default() == "videoloss, totalarm"


def test_coordinator_prefers_runtime_options_over_entry_data():
    entry = DummyEntry(
        {
            "host": "192.168.1.31",
            "protocol": integration.PROTOCOL_RTSP,
            "rtsp_path": "/legacy-stream",
            "snapshot_path": "/legacy-snapshot.jpg",
        },
        options={
            integration.CONF_PROTOCOL: integration.PROTOCOL_ONVIF,
            integration.CONF_RTSP_PATH: "/stream-from-options",
            integration.CONF_SNAPSHOT_PATH: "/snapshot-from-options.jpg",
            integration.CONF_ONVIF_PORT: 8899,
        },
    )

    coordinator = WJGCameraCoordinator(MagicMock(), _as_any(entry))

    assert coordinator.protocol == integration.PROTOCOL_ONVIF
    assert coordinator.rtsp_path == "/stream-from-options"
    assert coordinator.snapshot_path == "/snapshot-from-options.jpg"
    assert coordinator.onvif_port == 8899


def test_config_flow_is_matching_by_host():
    flow_a = WJGCameraConfigFlow()
    flow_b = WJGCameraConfigFlow()
    flow_c = WJGCameraConfigFlow()

    setattr(flow_a, _private_name("host"), "192.168.1.10")
    setattr(flow_b, _private_name("host"), "192.168.1.10")
    setattr(flow_c, _private_name("host"), "192.168.1.11")

    assert flow_a.is_matching(flow_b) is True
    assert flow_a.is_matching(flow_c) is False
    assert flow_a.is_matching(object()) is False


@pytest.mark.asyncio
async def test_config_flow_user_step_cannot_connect_shows_form():
    class FakeHass:
        def __init__(self, result):
            self._result = result

        async def async_add_executor_job(self, func, *args):
            _ = func
            _ = args
            return self._result

    flow = WJGCameraConfigFlow()
    flow.hass = _as_any(FakeHass(None))
    flow.async_show_form = MagicMock(return_value={"type": "form"})

    user_input = {
        "host": "192.168.1.90",
        "username": "admin",
        "password": "",
        "port": 80,
        "rtsp_port": 554,
        "protocol": integration.PROTOCOL_RTSP,
        "http_retries": 1,
        "rtsp_path": "/cam/realmonitor",
        "snapshot_path": "/webcapture.jpg",
    }

    result = await flow.async_step_user(user_input)

    assert _as_any(result)["type"] == "form"
    show_form_call = flow.async_show_form.call_args.kwargs
    assert show_form_call["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_user_step_success_creates_entry():
    class FakeHass:
        def __init__(self, result):
            self._result = result

        async def async_add_executor_job(self, func, *args):
            _ = func
            _ = args
            return self._result

    flow = WJGCameraConfigFlow()
    flow.hass = _as_any(FakeHass(554))
    flow.async_set_unique_id = AsyncMock(return_value=None)
    setattr(flow, _private_name("abort_if_unique_id_configured"), MagicMock(return_value=None))
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry", "title": "ok"})

    user_input = {
        "host": "192.168.1.91",
        "username": "admin",
        "password": "",
        "port": 80,
        "rtsp_port": 554,
        "protocol": integration.PROTOCOL_RTSP,
        "http_retries": 2,
        "rtsp_path": "/cam/realmonitor",
        "snapshot_path": "/webcapture.jpg",
    }

    result = await flow.async_step_user(user_input)

    assert _as_any(result)["type"] == "create_entry"
    flow.async_set_unique_id.assert_awaited_once_with("wjg_192.168.1.91")
    getattr(flow, _private_name("abort_if_unique_id_configured")).assert_called_once()


def test_config_flow_options_factory_returns_options_flow():
    entry = DummyEntry({"host": "192.168.1.99"})

    flow = WJGCameraConfigFlow.async_get_options_flow(_as_any(entry))

    assert isinstance(flow, WJGOptionsFlow)


def test_coordinator_http_retries_prefers_options_over_data():
    entry = DummyEntry(
        {
            "host": "192.168.1.40",
            "port": 80,
            "rtsp_port": 554,
            "protocol": "rtsp",
            "username": "admin",
            "password": "",
            "http_retries": 1,
        },
        options={"http_retries": 3},
    )

    coordinator = WJGCameraCoordinator(MagicMock(), _as_any(entry))

    assert coordinator.http_retries == 3


def test_coordinator_http_retries_clamps_invalid_values():
    negative_entry = DummyEntry(
        {
            "host": "192.168.1.41",
            "port": 80,
            "rtsp_port": 554,
            "protocol": "rtsp",
            "username": "admin",
            "password": "",
        },
        options={"http_retries": -10},
    )
    high_entry = DummyEntry(
        {
            "host": "192.168.1.42",
            "port": 80,
            "rtsp_port": 554,
            "protocol": "rtsp",
            "username": "admin",
            "password": "",
        },
        options={"http_retries": 99},
    )
    bad_entry = DummyEntry(
        {
            "host": "192.168.1.43",
            "port": 80,
            "rtsp_port": 554,
            "protocol": "rtsp",
            "username": "admin",
            "password": "",
        },
        options={"http_retries": "abc"},
    )

    negative = WJGCameraCoordinator(MagicMock(), _as_any(negative_entry))
    high = WJGCameraCoordinator(MagicMock(), _as_any(high_entry))
    bad = WJGCameraCoordinator(MagicMock(), _as_any(bad_entry))

    assert negative.http_retries == 0
    assert high.http_retries == 5
    assert bad.http_retries == integration.DEFAULT_HTTP_RETRIES