import sys
import os
import types
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock
import custom_components.wjg_camera.coordinator as coordinator_module
from tests_helpers import (
    call_private_async as _call_private_async,
    get_private_attr as _get_private_attr,
    make_coordinator as _make_coordinator,
    private_name as _private_name,
    set_private_attr as _set_private_attr,
)

class DummyEntry:
    def __init__(self, data):
        self.data = data
        self.entry_id = "dummy"


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)

@pytest.mark.asyncio
async def test_is_adb_proxy():
    entry = DummyEntry({
        "host": "127.0.0.1",
        "rtsp_port": 8080,
        "port": 8081,
        "username": "admin",
        "password": "",
        "protocol": "rtsp"
    })
    coordinator = _make_coordinator(MagicMock(), entry)
    assert coordinator.is_adb_proxy() is True

@pytest.mark.asyncio
async def test_async_adb_proxy_check(monkeypatch):
    entry = DummyEntry({
        "host": "127.0.0.1",
        "rtsp_port": 8080,
        "port": 8081,
        "username": "admin",
        "password": "",
        "protocol": "rtsp"
    })
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyResp:
        status = 200
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def get(self, _url):
            return DummyResp()

    monkeypatch.setattr("aiohttp.ClientSession", DummySession)
    assert await coordinator.async_adb_proxy_check() is True


@pytest.mark.asyncio
async def test_update_data_xm_keepalive_success_sets_available():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", None)
    _set_private_attr(coordinator, "_xm", type("XM", (), {"keepalive": lambda self: True})())

    data = await _call_private_async(coordinator, "_async_update_data")

    assert data["available"] is True


@pytest.mark.asyncio
async def test_update_data_xm_keepalive_false_keeps_unavailable_without_snapshot():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", None)
    _set_private_attr(coordinator, "_xm", type("XM", (), {"keepalive": lambda self: False})())

    data = await _call_private_async(coordinator, "_async_update_data")

    assert data["available"] is False


@pytest.mark.asyncio
async def test_update_data_xm_keepalive_exception_triggers_reconnect():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", None)

    def _raise_keepalive():
        raise RuntimeError("xm keepalive failed")

    _set_private_attr(
        coordinator,
        "_xm",
        type("XM", (), {"keepalive": lambda self: _raise_keepalive()})(),
    )
    reconnect_called = {"value": False}

    def _fake_setup_xm():
        reconnect_called["value"] = True

    _set_private_attr(coordinator, "_setup_xm", _fake_setup_xm)

    data = await _call_private_async(coordinator, "_async_update_data")

    assert reconnect_called["value"] is True
    assert data["available"] is False


@pytest.mark.asyncio
async def test_async_shutdown_closes_session_and_disconnects_xm():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class DummySession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class DummyXM:
        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    session = DummySession()
    xm = DummyXM()
    _set_private_attr(coordinator, "_session", session)
    _set_private_attr(coordinator, "_xm", xm)

    await coordinator.async_shutdown()

    assert session.closed is True
    assert xm.disconnected is True
    assert _get_private_attr(coordinator, "_session") is None
    assert _get_private_attr(coordinator, "_xm") is None


@pytest.mark.asyncio
async def test_async_shutdown_is_idempotent():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class DummySession:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    class DummyXM:
        def __init__(self):
            self.disconnect_calls = 0

        def disconnect(self):
            self.disconnect_calls += 1

    session = DummySession()
    xm = DummyXM()
    _set_private_attr(coordinator, "_session", session)
    _set_private_attr(coordinator, "_xm", xm)

    await coordinator.async_shutdown()
    await coordinator.async_shutdown()

    assert session.close_calls == 1
    assert xm.disconnect_calls == 1
    assert _get_private_attr(coordinator, "_session") is None
    assert _get_private_attr(coordinator, "_xm") is None


@pytest.mark.asyncio
async def test_async_prepare_connection_skips_check_when_not_adb_proxy():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    called = {"value": False}

    async def _fake_check():
        called["value"] = True
        return True

    coordinator.async_adb_proxy_check = _fake_check

    await coordinator.async_prepare_connection()

    assert called["value"] is False


@pytest.mark.asyncio
async def test_async_prepare_connection_checks_when_adb_proxy():
    entry = DummyEntry(
        {
            "host": "127.0.0.1",
            "rtsp_port": 8080,
            "port": 8081,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    called = {"value": False}

    async def _fake_check():
        called["value"] = True
        return False

    coordinator.async_adb_proxy_check = _fake_check

    await coordinator.async_prepare_connection()

    assert called["value"] is True


@pytest.mark.asyncio
async def test_async_setup_continues_on_http_error_and_runs_refresh(monkeypatch):
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class RaisingRequest:
        async def __aenter__(self):
            raise RuntimeError("http down")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def get(self, _url, **_kwargs):
            return RaisingRequest()

    monkeypatch.setattr("aiohttp.ClientSession", DummySession)

    refresh_called = {"value": False}

    async def _fake_refresh():
        refresh_called["value"] = True

    coordinator.async_refresh = _fake_refresh

    await coordinator.async_setup()

    assert refresh_called["value"] is True
    assert _get_private_attr(coordinator, "_session") is not None


@pytest.mark.asyncio
async def test_async_setup_xm_protocol_invokes_setup_xm(monkeypatch):
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class OkResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def get(self, _url, **_kwargs):
            return OkResponse()

    monkeypatch.setattr("aiohttp.ClientSession", DummySession)

    setup_called = {"value": False}

    def _fake_setup_xm():
        setup_called["value"] = True

    _set_private_attr(coordinator, "_setup_xm", _fake_setup_xm)

    async def _fake_refresh():
        return None

    coordinator.async_refresh = _fake_refresh

    await coordinator.async_setup()

    assert setup_called["value"] is True


@pytest.mark.asyncio
async def test_async_adb_proxy_check_exception_returns_false(monkeypatch):
    entry = DummyEntry(
        {
            "host": "127.0.0.1",
            "rtsp_port": 8080,
            "port": 8081,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("session failed")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", BrokenSession)

    assert await coordinator.async_adb_proxy_check() is False


def test_rtsp_url_uses_username_and_password():
    entry = DummyEntry(
        {
            "host": "192.168.1.51",
            "rtsp_port": 554,
            "port": 80,
            "username": "user1",
            "password": "secret",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    assert coordinator.rtsp_url == "rtsp://user1:secret@192.168.1.51:554/user=admin&password=&channel=1&stream=1.sdp?real_stream"


def test_rtsp_url_uses_default_admin_when_username_missing():
    entry = DummyEntry(
        {
            "host": "192.168.1.52",
            "rtsp_port": 554,
            "port": 80,
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    assert coordinator.rtsp_url == "rtsp://admin:@192.168.1.52:554/user=admin&password=&channel=1&stream=1.sdp?real_stream"


def test_rtsp_url_supports_explicit_username_password_placeholders():
    entry = DummyEntry(
        {
            "host": "192.168.1.53",
            "rtsp_port": 554,
            "port": 80,
            "username": "user1",
            "password": "secret",
            "protocol": "rtsp",
            "rtsp_path": "/user={username}&password={password}&channel=1&stream=0.sdp?real_stream",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    assert coordinator.rtsp_url == "rtsp://user1:secret@192.168.1.53:554/user=user1&password=secret&channel=1&stream=0.sdp?real_stream"


@pytest.mark.asyncio
async def test_async_resolve_rtsp_path_uses_first_candidate_with_video():
    entry = DummyEntry(
        {
            "host": "192.168.1.54",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
            "rtsp_path": "/user=admin&password=&channel=1&stream=0.sdp?real_stream",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    checked_urls: list[str] = []

    def _fake_has_video(url: str, timeout: float = 4.0) -> bool:
        _ = timeout
        checked_urls.append(url)
        return url.endswith("/stream0")

    _set_private_attr(coordinator, "_rtsp_url_has_video", _fake_has_video)

    await coordinator.async_resolve_rtsp_path()

    assert checked_urls[0].startswith("rtsp://admin")
    assert coordinator.rtsp_url == "rtsp://admin:@192.168.1.54:554/stream0"


@pytest.mark.asyncio
async def test_async_resolve_rtsp_path_keeps_configured_path_when_none_have_video():
    entry = DummyEntry(
        {
            "host": "192.168.1.55",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
            "rtsp_path": "/custom-stream",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    _set_private_attr(
        coordinator,
        "_rtsp_url_has_video",
        lambda url, timeout=4.0: False,
    )

    await coordinator.async_resolve_rtsp_path()

    assert coordinator.rtsp_url == "rtsp://admin:@192.168.1.55:554/custom-stream"


@pytest.mark.asyncio
async def test_async_resolve_rtsp_path_falls_back_to_no_credentials_url():
    entry = DummyEntry(
        {
            "host": "192.168.1.56",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
            "rtsp_path": "/stream0",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    def _fake_has_video(url: str, timeout: float = 4.0) -> bool:
        _ = timeout
        return url == "rtsp://192.168.1.56:554/stream0"

    _set_private_attr(coordinator, "_rtsp_url_has_video", _fake_has_video)

    await coordinator.async_resolve_rtsp_path()

    assert coordinator.rtsp_url == "rtsp://192.168.1.56:554/stream0"


def test_last_motion_time_property_returns_timestamp():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)
    _set_private_attr(coordinator, "_last_motion_time", 123.45)

    assert coordinator.last_motion_time == 123.45


def test_setup_xm_sets_client_on_success(monkeypatch):
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyXMClient:
        def __init__(self, host, port, username, password):
            _ = host
            _ = port
            _ = username
            _ = password

        def connect(self):
            return True

    monkeypatch.setattr(coordinator_module, "XMClient", DummyXMClient)

    getattr(coordinator, _private_name("setup_xm"))()

    assert _get_private_attr(coordinator, "_xm") is not None


def test_setup_xm_keeps_none_on_failure(monkeypatch):
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyXMClient:
        def __init__(self, host, port, username, password):
            _ = host
            _ = port
            _ = username
            _ = password

        def connect(self):
            return False

    monkeypatch.setattr(coordinator_module, "XMClient", DummyXMClient)

    getattr(coordinator, _private_name("setup_xm"))()

    assert _get_private_attr(coordinator, "_xm") is None


def test_init_onvif_failure_keeps_client_none(monkeypatch):
    fake_onvif_module = types.SimpleNamespace(
        ONVIFCamera=lambda host, port, username, password: (_ for _ in ()).throw(RuntimeError("onvif boom"))
    )
    monkeypatch.setitem(sys.modules, "onvif", fake_onvif_module)

    entry = DummyEntry(
        {
            "host": "192.168.1.77",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )

    coordinator = _make_coordinator(MagicMock(), entry)

    assert _get_private_attr(coordinator, "_onvif") is None


@pytest.mark.asyncio
async def test_update_data_snapshot_success_sets_available_and_bytes():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class DummyResp:
        status = 200
        headers = {"Content-Type": "image/jpeg"}

        async def read(self):
            return b"jpeg"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def get(self, _url, **_kwargs):
            return DummyResp()

    _set_private_attr(coordinator, "_session", DummySession())
    _set_private_attr(coordinator, "_xm", None)

    data = await _call_private_async(coordinator, "_async_update_data")

    assert data["available"] is True
    assert data["snapshot_bytes"] == b"jpeg"


@pytest.mark.asyncio
async def test_update_data_snapshot_exception_is_handled():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class BrokenSession:
        def get(self, _url, **_kwargs):
            raise RuntimeError("snapshot failed")

    _set_private_attr(coordinator, "_session", BrokenSession())
    _set_private_attr(coordinator, "_xm", None)

    data = await _call_private_async(coordinator, "_async_update_data")

    assert data["available"] is False


@pytest.mark.asyncio
async def test_async_snapshot_returns_none_for_non_bytes_payload():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", object())

    async def _fake_get_data(url, timeout_seconds, retries=None, as_json=False):
        _ = url
        _ = timeout_seconds
        _ = retries
        _ = as_json
        return {"not": "bytes"}

    setattr(coordinator, _private_name("async_http_get_data"), _fake_get_data)

    assert await coordinator.async_snapshot() is None


@pytest.mark.asyncio
async def test_async_http_get_data_returns_none_without_session():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", None)

    result = await getattr(coordinator, _private_name("async_http_get_data"))(
        "http://example.local/test",
        1,
    )

    assert result is None


@pytest.mark.asyncio
async def test_async_snapshot_returns_none_without_session():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "rtsp",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_session", None)

    assert await coordinator.async_snapshot() is None


@pytest.mark.asyncio
async def test_async_set_recording_uses_xm_success_path_for_start_and_stop():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class DummyXM:
        def start_recording(self, channel):
            _ = channel
            return True

        def stop_recording(self, channel):
            _ = channel
            return True

    _set_private_attr(coordinator, "_xm", DummyXM())

    assert await coordinator.async_set_recording(True) is True
    assert coordinator.is_recording is True
    assert await coordinator.async_set_recording(False) is True
    assert coordinator.is_recording is False


@pytest.mark.asyncio
async def test_async_ptz_command_xm_exception_without_http_returns_false():
    entry = DummyEntry(
        {
            "host": "192.168.1.50",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "xm_sdk",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    class DummyXM:
        def ptz_command(self, code, speed, channel):
            _ = code
            _ = speed
            _ = channel
            raise RuntimeError("ptz error")

    _set_private_attr(coordinator, "_xm", DummyXM())
    _set_private_attr(coordinator, "_session", None)

    assert await coordinator.async_ptz_command("up") is False


@pytest.mark.asyncio
async def test_async_onvif_create_pullpoint_uses_address_path():
    entry = DummyEntry(
        {
            "host": "192.168.1.90",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return (
            "<CreatePullPointSubscriptionResponse>"
            "<SubscriptionReference>"
            "<Address>http://192.168.1.90:8899/onvif/Subscription?Idx=2</Address>"
            "</SubscriptionReference>"
            "</CreatePullPointSubscriptionResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    assert await coordinator.async_onvif_create_pullpoint() is True
    assert _get_private_attr(coordinator, "_event_pullpoint_path") == "/onvif/Subscription"


@pytest.mark.asyncio
async def test_async_onvif_pull_messages_once_updates_motion_tamper_and_signal():
    entry = DummyEntry(
        {
            "host": "192.168.1.91",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_event_pullpoint_path", "/onvif/Subscription")

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return (
            "<tev:PullMessagesResponse>"
            "<wsnt:NotificationMessage>"
            "<wsnt:Topic>tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>"
            "<tt:Message><tt:Data><tt:SimpleItem Name=\"IsMotion\" Value=\"true\"/></tt:Data></tt:Message>"
            "</wsnt:NotificationMessage>"
            "<wsnt:NotificationMessage>"
            "<wsnt:Topic>tns1:RuleEngine/TamperDetector/Tamper</wsnt:Topic>"
            "<tt:Message><tt:Data><tt:SimpleItem Name=\"Tamper\" Value=\"true\"/></tt:Data></tt:Message>"
            "</wsnt:NotificationMessage>"
            "<wsnt:NotificationMessage>"
            "<wsnt:Topic>tns1:VideoSource/VideoLoss</wsnt:Topic>"
            "<tt:Message><tt:Data><tt:SimpleItem Name=\"VideoLoss\" Value=\"true\"/></tt:Data></tt:Message>"
            "</wsnt:NotificationMessage>"
            "</tev:PullMessagesResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)
    listener_calls = {"value": 0}

    def _fake_update_listeners():
        listener_calls["value"] += 1

    coordinator.async_update_listeners = _fake_update_listeners

    assert await coordinator.async_onvif_pull_messages_once() is True
    assert coordinator.motion_detected is True
    assert coordinator.tamper_detected is True
    assert coordinator.signal_loss is True
    assert listener_calls["value"] == 1


@pytest.mark.asyncio
async def test_async_fetch_audio_settings_reads_token_and_level():
    entry = DummyEntry(
        {
            "host": "192.168.1.92",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return (
            "<trt:GetAudioOutputConfigurationsResponse>"
            "<trt:Configurations token=\"AO_000\">"
            "<tt:SendPrimacy>www.onvif.org/ver10/schema/Always</tt:SendPrimacy>"
            "<tt:OutputLevel>0</tt:OutputLevel>"
            "</trt:Configurations>"
            "<trt:AudioOutputConfiguration token=\"AO_000\">"
            "<tt:SendPrimacy>www.onvif.org/ver10/schema/Always</tt:SendPrimacy>"
            "<tt:OutputLevel>0</tt:OutputLevel>"
            "</trt:AudioOutputConfiguration>"
            "</trt:GetAudioOutputConfigurationsResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    assert await coordinator.async_fetch_audio_settings() is True
    assert coordinator.microphone_enabled is False
    assert _get_private_attr(coordinator, "_audio_output_token") == "AO_000"


@pytest.mark.asyncio
async def test_async_set_microphone_enabled_sends_set_audio_output_configuration():
    entry = DummyEntry(
        {
            "host": "192.168.1.93",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, "_audio_output_token", "AO_001")
    _set_private_attr(coordinator, "_audio_output_level", 45)

    captured = {"body": ""}

    async def _fake_soap(_service_path, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        captured["body"] = body
        return "<trt:SetAudioOutputConfigurationResponse/>"

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    assert await coordinator.async_set_microphone_enabled(False) is True
    assert "<tt:OutputLevel>0</tt:OutputLevel>" in captured["body"]
    assert coordinator.microphone_enabled is False
