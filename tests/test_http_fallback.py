import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tests_helpers import (
    make_coordinator as _make_coordinator,
    private_name as _private_name,
    set_private_attr as _set_private_attr,
)


class DummyEntry:
    def __init__(self, data=None, options=None):
        self.data = data or {
            "host": "192.168.1.80",
            "rtsp_port": 554,
            "port": 80,
            "protocol": "rtsp",
            "username": "admin",
            "password": "",
        }
        self.options = options or {}
        self.entry_id = "http-fallback-entry"


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


@dataclass
class FakeResponse:
    status: int = 200
    payload_bytes: bytes | None = None
    payload_json: dict | None = None

    async def read(self):
        return self.payload_bytes or b""

    async def json(self):
        return self.payload_json or {}


class FakeRequestContext:
    def __init__(self, response: FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, routes=None, raise_on=None):
        self._routes = routes or {}
        self._raise_on = raise_on or []
        self.calls = []
        self.request_kwargs = []
        self._route_counters = {}

    def get(self, url, **request_kwargs):
        self.calls.append(url)
        self.request_kwargs.append(request_kwargs)
        for marker in self._raise_on:
            if marker in url:
                raise RuntimeError("network error")

        for marker, response in self._routes.items():
            if marker in url:
                if isinstance(response, list):
                    idx = self._route_counters.get(marker, 0)
                    self._route_counters[marker] = idx + 1
                    selected = response[idx] if idx < len(response) else response[-1]
                    return FakeRequestContext(selected)
                return FakeRequestContext(response)

        return FakeRequestContext(FakeResponse(status=404))


@pytest.mark.asyncio
async def test_http_fallback_set_recording_success():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={"/cgi-bin/record": FakeResponse(status=200)}
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_set_recording(True)

    assert ok is True
    assert coordinator.is_recording is True
    assert any("cmd=start" in u for u in session.calls)


@pytest.mark.asyncio
async def test_http_fallback_set_recording_stop_uses_stop_command():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={"/cgi-bin/record": FakeResponse(status=200)}
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_set_recording(False)

    assert ok is True
    assert coordinator.is_recording is False
    assert any("cmd=stop" in u for u in session.calls)


@pytest.mark.asyncio
async def test_http_fallback_set_recording_non_200_returns_false():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={"/cgi-bin/record": FakeResponse(status=500)}
    ))

    ok = await coordinator.async_set_recording(True)

    assert ok is False
    assert coordinator.is_recording is False


@pytest.mark.asyncio
async def test_http_fallback_set_recording_exception_returns_false():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(raise_on=["/cgi-bin/record"]))

    ok = await coordinator.async_set_recording(True)

    assert ok is False
    assert coordinator.is_recording is False


@pytest.mark.asyncio
async def test_http_fallback_set_recording_retries_and_succeeds():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={
            "/cgi-bin/record": [
                FakeResponse(status=503),
                FakeResponse(status=200, payload_bytes=b"ok"),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_set_recording(True)

    assert ok is True
    assert coordinator.is_recording is True
    assert len([u for u in session.calls if "/cgi-bin/record" in u]) == 2


@pytest.mark.asyncio
async def test_http_fallback_get_file_list_success():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={
            "/cgi-bin/fileman": FakeResponse(
                status=200,
                payload_json={"files": [{"name": "one.h264"}, {"name": "two.h264"}]},
            )
        }
    ))

    files = await coordinator.async_get_file_list()

    assert len(files) == 2
    assert files[0]["name"] == "one.h264"


@pytest.mark.asyncio
async def test_http_fallback_get_file_list_exception_returns_empty():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(raise_on=["/cgi-bin/fileman"]))

    files = await coordinator.async_get_file_list()

    assert files == []


@pytest.mark.asyncio
async def test_http_fallback_get_file_list_non_200_returns_empty():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={"/cgi-bin/fileman": FakeResponse(status=403)}
    ))

    files = await coordinator.async_get_file_list()

    assert files == []


@pytest.mark.asyncio
async def test_http_fallback_get_file_list_retries_and_succeeds():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={
            "/cgi-bin/fileman": [
                FakeResponse(status=503),
                FakeResponse(status=200, payload_json={"files": [{"name": "retry.h264"}]}),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    files = await coordinator.async_get_file_list()

    assert len(files) == 1
    assert files[0]["name"] == "retry.h264"
    assert len([u for u in session.calls if "/cgi-bin/fileman" in u]) == 2


@pytest.mark.asyncio
async def test_http_fallback_snapshot_success():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    session = FakeSession(
        routes={"/webcapture.jpg": FakeResponse(status=200, payload_bytes=b"jpeg-bytes")}
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    data = await coordinator.async_snapshot()

    assert data == b"jpeg-bytes"
    assert session.request_kwargs[0].get("auth") is not None


@pytest.mark.asyncio
async def test_http_fallback_snapshot_non_200_returns_none():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={"/webcapture.jpg": FakeResponse(status=404)}
    ))

    data = await coordinator.async_snapshot()

    assert data is None


@pytest.mark.asyncio
async def test_http_fallback_snapshot_exception_returns_none():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("session"), FakeSession(raise_on=["/webcapture.jpg"]))

    data = await coordinator.async_snapshot()

    assert data is None


@pytest.mark.asyncio
async def test_http_fallback_snapshot_retries_and_succeeds():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    session = FakeSession(
        routes={
            "/webcapture.jpg": [
                FakeResponse(status=502),
                FakeResponse(status=200, payload_bytes=b"retry-jpeg"),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    data = await coordinator.async_snapshot()

    assert data == b"retry-jpeg"
    assert len([u for u in session.calls if "/webcapture.jpg" in u]) == 2


@pytest.mark.asyncio
async def test_http_fallback_snapshot_tries_alternate_snapshot_path():
    entry = DummyEntry(options={"snapshot_path": "/bad.jpg"})
    coordinator = _make_coordinator(DummyHass(), entry)
    session = FakeSession(
        routes={
            "/bad.jpg": FakeResponse(status=404),
            "/webcapture.jpg": FakeResponse(status=200, payload_bytes=b"fallback-jpeg"),
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    data = await coordinator.async_snapshot()

    assert data == b"fallback-jpeg"
    assert any("/bad.jpg" in u for u in session.calls)
    assert any("/webcapture.jpg" in u for u in session.calls)


@pytest.mark.asyncio
async def test_http_fallback_snapshot_retries_without_http_auth_on_401():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    seen_auth: list[bool] = []

    class LocalRequestContext:
        def __init__(self, response: FakeResponse):
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class LocalSession:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url, **request_kwargs):
            self.calls.append(url)
            auth = request_kwargs.get("auth")
            seen_auth.append(auth is not None)
            if auth is not None:
                return LocalRequestContext(FakeResponse(status=401))
            return LocalRequestContext(FakeResponse(status=200, payload_bytes=b"anon-jpeg"))

    _set_private_attr(coordinator, _private_name("session"), LocalSession())

    data = await coordinator.async_snapshot()

    assert data == b"anon-jpeg"
    assert seen_auth[:2] == [True, False]


@pytest.mark.asyncio
async def test_http_fallback_ptz_success():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={"/cgi-bin/ptz": FakeResponse(status=200)}
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_ptz_command("up", speed=3)

    assert ok is True
    assert any("cmd=up" in u and "speed=3" in u for u in session.calls)


@pytest.mark.asyncio
async def test_http_fallback_ptz_non_200_returns_false():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={"/cgi-bin/ptz": FakeResponse(status=401)}
    ))

    ok = await coordinator.async_ptz_command("up")

    assert ok is False


@pytest.mark.asyncio
async def test_http_fallback_ptz_tries_hi3510_after_generic_endpoint_fails():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={
            "/cgi-bin/ptz": FakeResponse(status=404),
            "/web/cgi-bin/hi3510/ptzctrl.cgi": FakeResponse(status=200, payload_bytes=b"ok"),
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_ptz_command("right", speed=4)

    assert ok is True
    assert any("/cgi-bin/ptz" in u for u in session.calls)
    assert any("/web/cgi-bin/hi3510/ptzctrl.cgi" in u and "-act=right" in u for u in session.calls)


@pytest.mark.asyncio
async def test_http_fallback_ptz_exception_returns_false():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(raise_on=["/cgi-bin/ptz"]))

    ok = await coordinator.async_ptz_command("up")

    assert ok is False


@pytest.mark.asyncio
async def test_http_fallback_ptz_retries_and_succeeds():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={
            "/cgi-bin/ptz": [
                FakeResponse(status=429),
                FakeResponse(status=200, payload_bytes=b"ok"),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    ok = await coordinator.async_ptz_command("up", speed=4)

    assert ok is True
    assert len([u for u in session.calls if "/cgi-bin/ptz" in u]) == 2


@pytest.mark.asyncio
async def test_http_retry_option_zero_disables_retry_for_snapshot():
    entry = DummyEntry(options={"http_retries": 0})
    coordinator = _make_coordinator(DummyHass(), entry)
    session = FakeSession(
        routes={
            "/webcapture.jpg": [
                FakeResponse(status=503),
                FakeResponse(status=200, payload_bytes=b"late-success"),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    data = await coordinator.async_snapshot()

    assert data == b"late-success"
    assert len([u for u in session.calls if "/webcapture.jpg" in u]) == 2


@pytest.mark.asyncio
async def test_http_retry_option_two_allows_third_attempt_success():
    entry = DummyEntry(options={"http_retries": 2})
    coordinator = _make_coordinator(DummyHass(), entry)
    _set_private_attr(coordinator, _private_name("xm"), None)
    session = FakeSession(
        routes={
            "/cgi-bin/fileman": [
                FakeResponse(status=503),
                FakeResponse(status=503),
                FakeResponse(status=200, payload_json={"files": [{"name": "third-try.h264"}]}),
            ]
        }
    )
    _set_private_attr(coordinator, _private_name("session"), session)

    files = await coordinator.async_get_file_list()

    assert len(files) == 1
    assert files[0]["name"] == "third-try.h264"
    assert len([u for u in session.calls if "/cgi-bin/fileman" in u]) == 3


@pytest.mark.asyncio
async def test_http_fallback_ptz_invalid_command_returns_false():
    coordinator = _make_coordinator(DummyHass(), DummyEntry())
    _set_private_attr(coordinator, _private_name("xm"), None)
    _set_private_attr(coordinator, _private_name("session"), FakeSession(
        routes={"/cgi-bin/ptz": FakeResponse(status=200)}
    ))

    ok = await coordinator.async_ptz_command("not-a-command")

    assert ok is False