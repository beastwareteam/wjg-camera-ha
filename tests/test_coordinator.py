import sys
import os
import types
import re
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
    def __init__(self, data, options=None):
        self.data = data
        self.entry_id = "dummy"
        self.options = options or {}


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


@pytest.mark.asyncio
async def test_onvif_soap_omits_header_when_auth_disabled():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    captured: dict[str, object] = {}

    class DummyResponse:
        async def text(self):
            return "<ok/>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def post(self, _url, data=None, headers=None, auth=None):
            _ = headers
            captured["data"] = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
            captured["auth"] = auth
            return DummyResponse()

    _set_private_attr(coordinator, "_session", DummySession())

    response = await coordinator._onvif_soap("/onvif/PTZ", "<tptz:ContinuousMove/>", use_auth=False)
    payload = str(captured["data"])

    assert response == "<ok/>"
    assert "<s:Header>" not in payload
    assert "wsse:Security" not in payload
    assert captured["auth"] is not None
    assert getattr(captured["auth"], "login", None) == "admin"


@pytest.mark.asyncio
async def test_onvif_soap_retries_content_type_and_caches_successful_variant():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    seen_content_types: list[str] = []

    class DummyResponse:
        def __init__(self, text: str):
            self._text = text

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def post(self, _url, data=None, headers=None, auth=None):
            _ = data
            _ = auth
            content_type = str((headers or {}).get("Content-Type", ""))
            seen_content_types.append(content_type)
            if content_type == "application/soap+xml; charset=utf-8":
                return DummyResponse("An error was discovered processing the &lt;wsse:Security&gt; header")
            return DummyResponse("<ok/>")

    _set_private_attr(coordinator, "_session", DummySession())
    _set_private_attr(coordinator, "_onvif_content_type", "application/soap+xml; charset=utf-8")

    response = await coordinator._onvif_soap("/onvif/PTZ", "<tptz:ContinuousMove/>", use_auth=False)

    assert response == "<ok/>"
    assert seen_content_types[:2] == [
        "application/soap+xml; charset=utf-8",
        "text/xml; charset=utf-8",
    ]
    assert coordinator.onvif_content_type == "text/xml; charset=utf-8"

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


def test_coordinator_applies_onvif_option_overrides():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        },
        options={
            "onvif_device_path": "/onvif/Device",
            "onvif_media_path": "http://192.168.178.49:8899/onvif/media_service",
            "onvif_profile_token": "002",
            "onvif_video_source_token": "VideoSource_9",
            "onvif_signal_item_keys": "totalarm, videoloss",
            "onvif_signal_topic_keywords": "tot, videoloss",
        },
    )

    coordinator = _make_coordinator(DummyHass(), entry)
    service_paths = _get_private_attr(coordinator, "_onvif_service_paths")
    event_rules = _get_private_attr(coordinator, "_onvif_event_rules")

    assert service_paths[coordinator_module.ONVIF_SERVICE_DEVICE] == "/onvif/Device"
    assert service_paths[coordinator_module.ONVIF_SERVICE_MEDIA] == "/onvif/media_service"
    assert _get_private_attr(coordinator, "_preferred_onvif_profile_token") == "002"
    assert _get_private_attr(coordinator, "_preferred_onvif_video_source_token") == "VideoSource_9"
    assert "totalarm" in event_rules["signal_loss"]["item_keys"]
    assert "tot" in event_rules["signal_loss"]["topic_keywords"]


@pytest.mark.asyncio
async def test_bootstrap_onvif_service_paths_reads_xaddrs_from_getservices():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = body
        _ = use_auth
        _ = timeout_seconds
        if service_path != "/onvif/device_service":
            return ""
        return (
            "<tds:GetServicesResponse>"
            "<tds:Service><tds:XAddr>http://192.168.178.49:8899/onvif/device_service</tds:XAddr></tds:Service>"
            "<tds:Service><tds:XAddr>http://192.168.178.49:8899/onvif/Media</tds:XAddr></tds:Service>"
            "<tds:Service><tds:XAddr>http://192.168.178.49:8899/onvif/Media2</tds:XAddr></tds:Service>"
            "<tds:Service><tds:XAddr>http://192.168.178.49:8899/onvif/PTZ</tds:XAddr></tds:Service>"
            "<tds:Service><tds:XAddr>http://192.168.178.49:8899/onvif/Imaging</tds:XAddr></tds:Service>"
            "</tds:GetServicesResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    await _call_private_async(coordinator, "_async_bootstrap_onvif_service_paths")

    service_paths = _get_private_attr(coordinator, "_onvif_service_paths")
    assert service_paths[coordinator_module.ONVIF_SERVICE_DEVICE] == "/onvif/device_service"
    assert service_paths[coordinator_module.ONVIF_SERVICE_MEDIA] == "/onvif/Media"
    assert service_paths[coordinator_module.ONVIF_SERVICE_PTZ] == "/onvif/PTZ"
    assert service_paths[coordinator_module.ONVIF_SERVICE_IMAGING] == "/onvif/Imaging"


@pytest.mark.asyncio
async def test_preferred_onvif_tokens_override_runtime_resolution():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        },
        options={
            "onvif_profile_token": "002",
            "onvif_video_source_token": "VideoSource_9",
        },
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    assert await _call_private_async(coordinator, "_async_active_onvif_profile_token") == "002"
    assert await _call_private_async(coordinator, "_async_onvif_video_source_token") == "VideoSource_9"


@pytest.mark.asyncio
async def test_onvif_tokens_load_via_direct_soap_without_python_client():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap_for(service_key, body, use_auth=True, timeout_seconds=5):
        _ = service_key
        _ = use_auth
        _ = timeout_seconds
        if "GetProfiles" in body:
            return (
                "<trt:GetProfilesResponse>"
                "<trt:Profiles token=\"PROFILE_MAIN\">"
                "<tt:VideoSourceConfiguration><tt:SourceToken>VS_001</tt:SourceToken></tt:VideoSourceConfiguration>"
                "</trt:Profiles>"
                "<trt:Profiles token=\"PROFILE_SUB\"/>"
                "</trt:GetProfilesResponse>"
            )
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_active_stream", "001")

    assert await _call_private_async(coordinator, "_async_active_onvif_profile_token") == "PROFILE_SUB"
    assert await _call_private_async(coordinator, "_async_onvif_video_source_token") == "VS_001"


@pytest.mark.asyncio
async def test_onvif_stream_url_loads_via_direct_soap_without_python_client():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap_for(service_key, body, use_auth=True, timeout_seconds=5):
        _ = service_key
        _ = use_auth
        _ = timeout_seconds
        if "GetProfiles" in body:
            return "<trt:GetProfilesResponse><trt:Profiles token=\"PROFILE_MAIN\"/></trt:GetProfilesResponse>"
        if "GetStreamUri" in body:
            return (
                "<trt:GetStreamUriResponse>"
                "<trt:MediaUri><tt:Uri>rtsp://192.168.178.49/live</tt:Uri></trt:MediaUri>"
                "</trt:GetStreamUriResponse>"
            )
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)

    assert await coordinator.async_onvif_stream_url() == "rtsp://admin:@192.168.178.49:554/live"


@pytest.mark.asyncio
async def test_onvif_soap_for_retries_without_auth_on_security_token_fault():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "secret",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    calls: list[tuple[str, bool]] = []

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = body
        _ = timeout_seconds
        calls.append((service_path, use_auth))
        if use_auth:
            return "The security token could not be authenticated or authorized"
        return "<tptz:ContinuousMoveResponse/>"

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)
    _set_private_attr(coordinator, "_onvif_wsse_enabled", True)
    response = await coordinator._onvif_soap_for(
        coordinator_module.ONVIF_SERVICE_PTZ,
        "<tptz:ContinuousMove/>",
    )

    assert response == "<tptz:ContinuousMoveResponse/>"
    assert calls[0] == ("/onvif/PTZ", True)
    assert calls[1] == ("/onvif/PTZ", False)


@pytest.mark.asyncio
async def test_onvif_soap_for_retries_next_service_path_on_soap_fault():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    seen_paths: list[str] = []

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = body
        _ = use_auth
        _ = timeout_seconds
        seen_paths.append(service_path)
        if service_path == "/onvif/PTZ":
            return "<s:Fault><s:Reason><s:Text>ptz fault</s:Text></s:Reason></s:Fault>"
        return "<tptz:ContinuousMoveResponse/>"

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    response = await coordinator._onvif_soap_for(
        coordinator_module.ONVIF_SERVICE_PTZ,
        "<tptz:ContinuousMove/>",
        use_auth=False,
    )

    assert response == "<tptz:ContinuousMoveResponse/>"
    assert seen_paths[:2] == ["/onvif/PTZ", "/onvif/ptz_service"]


@pytest.mark.asyncio
async def test_async_ptz_command_succeeds_with_auth_fault_retry():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "secret",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = service_path
        _ = timeout_seconds
        if "GetProfiles" in body:
            return "<trt:GetProfilesResponse><trt:Profiles token=\"000\"/></trt:GetProfilesResponse>"
        if use_auth:
            return "The security token could not be authenticated or authorized"
        if "ContinuousMove" in body:
            return "<tptz:ContinuousMoveResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)
    _set_private_attr(coordinator, "_onvif_wsse_enabled", True)

    assert await coordinator.async_ptz_command("right") is True


@pytest.mark.asyncio
async def test_async_ptz_command_succeeds_with_wsse_security_header_fault_retry():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "secret",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = service_path
        _ = timeout_seconds
        if "GetProfiles" in body:
            return "<trt:GetProfilesResponse><trt:Profiles token=\"000\"/></trt:GetProfilesResponse>"
        if use_auth:
            return "An error was discovered processing the &lt;wsse:Security&gt; header"
        if "ContinuousMove" in body:
            return "<tptz:ContinuousMoveResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)
    _set_private_attr(coordinator, "_onvif_wsse_enabled", True)

    assert await coordinator.async_ptz_command("right") is True


@pytest.mark.asyncio
async def test_async_ptz_command_falls_back_to_ptz_v10_namespace():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    seen_bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        seen_bodies.append(body)
        if "tptz10:ContinuousMove" in body:
            return "<tptz10:ContinuousMoveResponse/>"
        return "<s:Fault><s:Reason><s:Text>PTZ v20 unsupported</s:Text></s:Reason></s:Fault>"

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("right") is True
    assert any("tptz:ContinuousMove" in body for body in seen_bodies)
    assert any("tptz10:ContinuousMove" in body for body in seen_bodies)


@pytest.mark.asyncio
async def test_async_ptz_command_falls_back_to_soap11_legacy_request():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    legacy_calls: list[tuple[str, str]] = []

    async def _fake_soap_for(_service_key, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return "<s:Fault><s:Reason><s:Text>SOAP 1.2 unsupported</s:Text></s:Reason></s:Fault>"

    async def _fake_legacy_for(_service_key, body, soap_action, timeout_seconds=5):
        _ = timeout_seconds
        legacy_calls.append((body, soap_action))
        return "<tptz:ContinuousMoveResponse/>"

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_soap_legacy_for", _fake_legacy_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("right") is True
    assert legacy_calls
    assert any("ContinuousMove" in body for body, _soap_action in legacy_calls)
    assert any("http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove" == soap_action for _body, soap_action in legacy_calls)


@pytest.mark.asyncio
async def test_onvif_wsse_is_disabled_after_security_header_fault():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "secret",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    calls: list[bool] = []

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = service_path
        _ = timeout_seconds
        calls.append(bool(use_auth))
        if "GetProfiles" in body:
            return "<trt:GetProfilesResponse><trt:Profiles token=\"000\"/></trt:GetProfilesResponse>"
        if use_auth:
            return "An error was discovered processing the &lt;wsse:Security&gt; header"
        return "<tptz:ContinuousMoveResponse/>"

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)
    _set_private_attr(coordinator, "_onvif_wsse_enabled", True)

    assert await coordinator.async_ptz_command("right") is True
    calls.clear()
    assert await coordinator.async_ptz_command("right") is True
    assert calls
    assert all(call is False for call in calls)


def test_onvif_wsse_is_disabled_by_default():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "secret",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    assert _get_private_attr(coordinator, "_onvif_wsse_enabled") is False


@pytest.mark.asyncio
async def test_async_ptz_command_falls_back_to_alternate_profile_token():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    attempted_tokens: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        match = re.search(r"<tptz:ProfileToken>([^<]+)</tptz:ProfileToken>", body)
        token = match.group(1) if match else ""
        attempted_tokens.append(token)
        if token == "GOOD":
            return "<tptz:ContinuousMoveResponse/>"
        return "<s:Fault><s:Reason><s:Text>invalid profile token</s:Text></s:Reason></s:Fault>"

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "BAD", "001": "GOOD"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("right") is True
    assert attempted_tokens[0] == "BAD"
    assert "GOOD" in attempted_tokens
    assert _get_private_attr(coordinator, "_onvif_profile_tokens")["000"] == "GOOD"


@pytest.mark.asyncio
async def test_async_ptz_command_falls_back_to_relative_move_when_continuous_move_fails():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    seen_bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        seen_bodies.append(body)
        if "ContinuousMove" in body:
            return "<s:Fault><s:Reason><s:Text>ActionNotSupported</s:Text></s:Reason></s:Fault>"
        if "RelativeMove" in body:
            return "<tptz:RelativeMoveResponse/>"
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("right") is True
    assert any("ContinuousMove" in body for body in seen_bodies)
    assert any("RelativeMove" in body for body in seen_bodies)


@pytest.mark.asyncio
async def test_async_ptz_command_falls_back_to_continuous_move_with_timeout():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    seen_bodies: list[str] = []

    async def _fake_soap_for(_service_key, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        seen_bodies.append(body)
        if "ContinuousMove" in body and "Timeout" not in body:
            return "<s:Fault><s:Reason><s:Text>ActionNotSupported</s:Text></s:Reason></s:Fault>"
        if "ContinuousMove" in body and "Timeout" in body:
            return "<tptz:ContinuousMoveResponse/>"
        if "RelativeMove" in body:
            return "<s:Fault><s:Reason><s:Text>ShouldNotReachRelativeMove</s:Text></s:Reason></s:Fault>"
        return ""

    _set_private_attr(coordinator, "_onvif", None)
    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap_for)
    _set_private_attr(coordinator, "_onvif_profile_tokens", {"000": "000"})
    _set_private_attr(coordinator, "_active_stream", "000")

    assert await coordinator.async_ptz_command("right") is True
    assert any("ContinuousMove" in body and "Timeout" not in body for body in seen_bodies)
    assert any("ContinuousMove" in body and "Timeout" in body for body in seen_bodies)
    assert not any("RelativeMove" in body for body in seen_bodies)


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


@pytest.mark.asyncio
async def test_async_resolve_rtsp_path_onvif_uses_stream_uri_when_video_available():
    entry = DummyEntry(
        {
            "host": "192.168.1.57",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "rtsp_path": "/custom-stream",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_onvif_stream_url():
        return "rtsp://admin:@192.168.1.57:554/from-onvif"

    _set_private_attr(coordinator, "async_onvif_stream_url", _fake_onvif_stream_url)
    _set_private_attr(
        coordinator,
        "_rtsp_url_has_video",
        lambda url, timeout=4.0: url.endswith("/from-onvif"),
    )

    await coordinator.async_resolve_rtsp_path()

    assert coordinator.rtsp_url == "rtsp://admin:@192.168.1.57:554/from-onvif"


@pytest.mark.asyncio
async def test_async_resolve_rtsp_path_onvif_falls_back_when_stream_uri_has_no_video():
    entry = DummyEntry(
        {
            "host": "192.168.1.58",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "rtsp_path": "/custom-stream",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    checked_urls: list[str] = []

    async def _fake_onvif_stream_url():
        return "rtsp://admin:@192.168.1.58:554/from-onvif"

    def _fake_has_video(url: str, timeout: float = 4.0) -> bool:
        _ = timeout
        checked_urls.append(url)
        return url == "rtsp://192.168.1.58:554/stream0"

    _set_private_attr(coordinator, "async_onvif_stream_url", _fake_onvif_stream_url)
    _set_private_attr(coordinator, "_rtsp_url_has_video", _fake_has_video)

    await coordinator.async_resolve_rtsp_path()

    assert checked_urls[0] == "rtsp://admin:@192.168.1.58:554/from-onvif"
    assert coordinator.rtsp_url == "rtsp://192.168.1.58:554/stream0"


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
async def test_async_setup_onvif_failure_keeps_client_none(monkeypatch):
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
    coordinator = _make_coordinator(DummyHass(), entry)

    class NoHttpSession:
        def get(self, _url, **_kwargs):
            raise AssertionError("HTTP probe must be skipped for ONVIF protocol")

    monkeypatch.setattr("aiohttp.ClientSession", NoHttpSession)

    async def _fake_refresh():
        return None

    coordinator.async_refresh = _fake_refresh

    await coordinator.async_setup()

    assert _get_private_attr(coordinator, "_onvif") is None


@pytest.mark.asyncio
async def test_async_setup_onvif_skips_http_probe(monkeypatch):
    fake_onvif_module = types.SimpleNamespace(
        ONVIFCamera=lambda host, port, username, password: object()
    )
    monkeypatch.setitem(sys.modules, "onvif", fake_onvif_module)

    entry = DummyEntry(
        {
            "host": "192.168.1.88",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    http_get_called = {"value": False}

    class NoHttpSession:
        def get(self, _url, **_kwargs):
            http_get_called["value"] = True
            raise RuntimeError("not expected")

    monkeypatch.setattr("aiohttp.ClientSession", NoHttpSession)

    async def _fake_refresh():
        return None

    async def _fake_bootstrap():
        return None

    coordinator.async_refresh = _fake_refresh
    _set_private_attr(coordinator, "_async_bootstrap_onvif_service_paths", _fake_bootstrap)

    await coordinator.async_setup()

    assert http_get_called["value"] is False


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
async def test_async_fetch_device_info_falls_back_to_alternate_onvif_device_path():
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

    calls: list[str] = []

    async def _fake_soap(service_path, body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        calls.append(service_path)
        if service_path == "/onvif/device_service":
            return "404 Not Found"
        if "GetDeviceInformation" in body:
            return (
                "<tds:GetDeviceInformationResponse>"
                "<tds:FirmwareVersion>1.2.3</tds:FirmwareVersion>"
                "<tds:SerialNumber>ABC123</tds:SerialNumber>"
                "</tds:GetDeviceInformationResponse>"
            )
        return (
            "<tds:GetNetworkInterfacesResponse>"
            "<tt:Info><tt:HwAddress>AA:BB:CC:DD:EE:FF</tt:HwAddress></tt:Info>"
            "</tds:GetNetworkInterfacesResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap", _fake_soap)

    await coordinator.async_fetch_device_info()

    assert calls[:2] == ["/onvif/device_service", "/onvif/Device"]
    assert coordinator.firmware_version == "1.2.3"
    assert coordinator.serial_number == "ABC123"
    assert coordinator.mac_address == "AA:BB:CC:DD:EE:FF"
    assert _get_private_attr(coordinator, "_onvif_service_paths")[coordinator_module.ONVIF_SERVICE_DEVICE] == "/onvif/Device"


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

    seen_use_auth: list[bool] = []

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        seen_use_auth.append(bool(use_auth))
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
    assert seen_use_auth == [False]


@pytest.mark.asyncio
async def test_async_fetch_imaging_settings_limits_value_parsing_to_imaging_block():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return (
            "<timg:GetImagingSettingsResponse>"
            "<tt:Contrast>17316620</tt:Contrast>"
            "<tt:ImagingSettings>"
            "<tt:Brightness>50</tt:Brightness>"
            "<tt:ColorSaturation>50</tt:ColorSaturation>"
            "<tt:Contrast>50</tt:Contrast>"
            "<tt:Sharpness>5</tt:Sharpness>"
            "<tt:IrCutFilter>AUTO</tt:IrCutFilter>"
            "</tt:ImagingSettings>"
            "</timg:GetImagingSettingsResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap)
    _set_private_attr(coordinator, "_preferred_onvif_video_source_token", "000")

    assert await coordinator.async_fetch_imaging_settings() is True
    assert coordinator.imaging["brightness"] == 50.0
    assert coordinator.imaging["contrast"] == 50.0
    assert coordinator.imaging["sharpness"] == 5.0


@pytest.mark.asyncio
async def test_async_fetch_imaging_settings_clamps_vendor_outlier_values():
    entry = DummyEntry(
        {
            "host": "192.168.178.49",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    async def _fake_soap(_service_path, _body, use_auth=True, timeout_seconds=5):
        _ = use_auth
        _ = timeout_seconds
        return (
            "<timg:GetImagingSettingsResponse>"
            "<timg:ImagingSettings>"
            "<tt:Brightness>-5</tt:Brightness>"
            "<tt:ColorSaturation>150</tt:ColorSaturation>"
            "<tt:Contrast>17316620</tt:Contrast>"
            "<tt:Sharpness>99</tt:Sharpness>"
            "<tt:IrCutFilter>AUTO</tt:IrCutFilter>"
            "</timg:ImagingSettings>"
            "</timg:GetImagingSettingsResponse>"
        )

    _set_private_attr(coordinator, "_onvif_soap_for", _fake_soap)
    _set_private_attr(coordinator, "_preferred_onvif_video_source_token", "000")

    assert await coordinator.async_fetch_imaging_settings() is True
    assert coordinator.imaging["brightness"] == 0.0
    assert coordinator.imaging["saturation"] == 100.0
    assert coordinator.imaging["contrast"] == 100.0
    assert coordinator.imaging["sharpness"] == 15.0


@pytest.mark.asyncio
async def test_apply_onvif_event_supports_custom_signal_rule():
    entry = DummyEntry(
        {
            "host": "192.168.1.94",
            "rtsp_port": 554,
            "port": 80,
            "username": "admin",
            "password": "",
            "protocol": "onvif",
            "onvif_port": 8899,
        }
    )
    coordinator = _make_coordinator(DummyHass(), entry)

    rules = dict(_get_private_attr(coordinator, "_onvif_event_rules"))
    rules["signal_loss"] = {
        "item_keys": ("videoloss", "signal", "totalarm"),
        "topic_keywords": ("videoloss", "signal", "tot"),
        "state_attr": "_signal_loss",
        "topic_only_true": True,
    }
    _set_private_attr(coordinator, "_onvif_event_rules", rules)

    changed = _get_private_attr(coordinator, "_apply_onvif_event")(
        "tns1:Device/TotAlarm",
        {"totalarm": "true"},
    )

    assert changed is True
    assert coordinator.signal_loss is True


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
