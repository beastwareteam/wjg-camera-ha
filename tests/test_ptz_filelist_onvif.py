import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, AsyncMock
from tests_helpers import make_coordinator as _make_coordinator, set_private_attr as _set_private_attr

class DummyEntry:
    def __init__(self, data):
        self.data = data
        self.entry_id = "dummy"


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)

@pytest.mark.asyncio
async def test_async_ptz_command_success():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "xm_sdk"})
    coordinator = _make_coordinator(DummyHass(), entry)
    xm_client = MagicMock()
    xm_client.ptz_command = MagicMock(return_value=True)
    _set_private_attr(coordinator, "_xm", xm_client)
    result = await coordinator.async_ptz_command("up")
    assert result is True

@pytest.mark.asyncio
async def test_async_get_file_list():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "xm_sdk"})
    coordinator = _make_coordinator(DummyHass(), entry)
    xm_client = MagicMock()
    xm_client.get_file_list = MagicMock(return_value=[{"name": "file1"}, {"name": "file2"}])
    _set_private_attr(coordinator, "_xm", xm_client)
    files = await coordinator.async_get_file_list()
    assert isinstance(files, list)
    assert len(files) == 2

@pytest.mark.asyncio
async def test_async_onvif_ptz():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)
    class DummyMedia:
        async def GetProfiles(self):
            class Profile: token = "t1"
            return [Profile()]
        def create_type(self, _):
            class Req:
                ProfileToken = "t1"
                Velocity = {}
            return Req()
    class DummyPTZ:
        def create_type(self, _):
            class Req:
                ProfileToken = "t1"
                Velocity = {}
            return Req()
        ContinuousMove = AsyncMock(return_value=None)
        Stop = AsyncMock(return_value=None)
    class DummyONVIF:
        def create_media_service(self): return DummyMedia()
        def create_ptz_service(self): return DummyPTZ()
    _set_private_attr(coordinator, "_onvif", DummyONVIF())
    assert await coordinator.async_onvif_ptz("up") is True
    assert await coordinator.async_onvif_ptz("left") is True
    assert await coordinator.async_onvif_ptz("right") is True
    assert await coordinator.async_onvif_ptz("zoom_in") is True
    assert await coordinator.async_onvif_ptz("zoom_out") is True
    assert await coordinator.async_onvif_ptz("stop") is True


@pytest.mark.asyncio
async def test_async_onvif_ptz_with_sync_profile_call_path():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyMedia:
        def GetProfiles(self):
            class Profile:
                token = "sync-token"

            return [Profile()]

    class DummyPTZ:
        def create_type(self, _):
            class Req:
                ProfileToken = "sync-token"
                Velocity = {}

            return Req()

        async def ContinuousMove(self, req):
            _ = req
            return None

        async def Stop(self, req):
            _ = req
            return None

    class DummyONVIF:
        def create_media_service(self):
            return DummyMedia()

        def create_ptz_service(self):
            return DummyPTZ()

    _set_private_attr(coordinator, "_onvif", DummyONVIF())

    assert await coordinator.async_onvif_ptz("down") is True

@pytest.mark.asyncio
async def test_async_onvif_stream_url():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)
    class DummyMedia:
        async def GetProfiles(self):
            class Profile: token = "t1"
            return [Profile()]
        def create_type(self, _):
            class Req:
                ProfileToken = "t1"
                StreamSetup = {}
            return Req()
        GetStreamUri = AsyncMock(return_value=type("Uri", (), {"Uri": "rtsp://dummy/stream"})())
    class DummyONVIF:
        def create_media_service(self): return DummyMedia()
    _set_private_attr(coordinator, "_onvif", DummyONVIF())
    url = await coordinator.async_onvif_stream_url()
    assert url == "rtsp://dummy/stream"


@pytest.mark.asyncio
async def test_async_onvif_ptz_returns_false_without_onvif_client():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)
    _set_private_attr(coordinator, "_onvif", None)

    assert await coordinator.async_onvif_ptz("up") is False


@pytest.mark.asyncio
async def test_async_onvif_ptz_invalid_command_returns_false():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyMedia:
        async def GetProfiles(self):
            class Profile:
                token = "t1"

            return [Profile()]

    class DummyPTZ:
        def create_type(self, _):
            class Req:
                ProfileToken = "t1"
                Velocity = {}

            return Req()

        ContinuousMove = AsyncMock(return_value=None)
        Stop = AsyncMock(return_value=None)

    class DummyONVIF:
        def create_media_service(self):
            return DummyMedia()

        def create_ptz_service(self):
            return DummyPTZ()

    _set_private_attr(coordinator, "_onvif", DummyONVIF())

    assert await coordinator.async_onvif_ptz("nonsense") is False


@pytest.mark.asyncio
async def test_async_onvif_ptz_handles_exception_returns_false():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyMedia:
        async def GetProfiles(self):
            class Profile:
                token = "t1"

            return [Profile()]

    class DummyPTZ:
        def create_type(self, _):
            class Req:
                ProfileToken = "t1"
                Velocity = {}

            return Req()

        ContinuousMove = AsyncMock(side_effect=RuntimeError("boom"))
        Stop = AsyncMock(return_value=None)

    class DummyONVIF:
        def create_media_service(self):
            return DummyMedia()

        def create_ptz_service(self):
            return DummyPTZ()

    _set_private_attr(coordinator, "_onvif", DummyONVIF())

    assert await coordinator.async_onvif_ptz("up") is False


@pytest.mark.asyncio
async def test_async_onvif_stream_url_returns_none_without_onvif_client():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)
    _set_private_attr(coordinator, "_onvif", None)

    assert await coordinator.async_onvif_stream_url() is None


@pytest.mark.asyncio
async def test_async_onvif_stream_url_handles_exception_returns_none():
    entry = DummyEntry({"host": "1.2.3.4", "rtsp_port": 554, "port": 80, "protocol": "rtsp"})
    coordinator = _make_coordinator(MagicMock(), entry)

    class DummyMedia:
        async def GetProfiles(self):
            raise RuntimeError("bad profiles")

    class DummyONVIF:
        def create_media_service(self):
            return DummyMedia()

    _set_private_attr(coordinator, "_onvif", DummyONVIF())

    assert await coordinator.async_onvif_stream_url() is None
