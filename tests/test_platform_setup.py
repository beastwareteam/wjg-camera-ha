import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.wjg_camera import DOMAIN
from custom_components.wjg_camera.binary_sensor import WJGMotionSensor, async_setup_entry as setup_binary_sensor
from custom_components.wjg_camera.button import WJGPTZButton, async_setup_entry as setup_button
from custom_components.wjg_camera.camera import WJGCamera, async_setup_entry as setup_camera
from custom_components.wjg_camera.sensor import WJGFileListSensor, async_setup_entry as setup_sensor
from custom_components.wjg_camera.switch import WJGRecordingSwitch, async_setup_entry as setup_switch
from tests_helpers import as_any as _as_any, make_add_entities_callback as _make_add_entities_callback


class DummyEntry:
    def __init__(self, entry_id="platform-entry"):
        self.entry_id = entry_id


class DummyHass:
    def __init__(self, coordinator, entry_id="platform-entry"):
        self.data = {DOMAIN: {entry_id: coordinator}}


class DummyCoordinator:
    host = "192.168.1.60"
    http_port = 80
    protocol = "rtsp"
    data = {"available": True}
    is_recording = False
    motion_detected = False
    last_motion_time = 0.0
    rtsp_url = "rtsp://192.168.1.60:554/stream"
    snapshot_url = "http://192.168.1.60/snap.jpg"
    tamper_detected = False
    signal_loss = False
    imaging = {
        "Brightness": 50,
        "Contrast": 50,
        "Saturation": 50,
        "Sharpness": 7,
        "WdrMode": "OFF",
        "WdrLevel": 50,
        "IrCutFilter": "AUTO",
        "ExposureMode": "AUTO",
        "ExposurePriority": "LowNoise",
        "WhiteBalance": "AUTO",
        "CrGain": 50,
        "CbGain": 50,
    }
    ptz_speed = 4
    ptz_presets: list = []
    active_stream = "000"
    firmware_version = "V100.ONVIF"
    serial_number = "NDUB10241204QMPM"
    mac_address = "44:e6:4a:d2:c0:84"
    camera_time = None

    def async_add_listener(self, update_callback):
        _ = update_callback
        def remove_listener():
            return None

        return remove_listener


@pytest.mark.asyncio
async def test_camera_platform_setup_adds_camera_entity():
    added = []
    coordinator = DummyCoordinator()
    hass = DummyHass(coordinator)
    entry = DummyEntry()

    await setup_camera(_as_any(hass), _as_any(entry), _make_add_entities_callback(added))

    assert len(added) == 1
    assert isinstance(added[0], WJGCamera)


@pytest.mark.asyncio
async def test_switch_platform_setup_adds_recording_switch():
    added = []
    coordinator = DummyCoordinator()
    hass = DummyHass(coordinator)
    entry = DummyEntry()

    await setup_switch(_as_any(hass), _as_any(entry), _make_add_entities_callback(added))

    assert len(added) == 3
    assert any(isinstance(e, WJGRecordingSwitch) for e in added)


@pytest.mark.asyncio
async def test_binary_sensor_platform_setup_adds_motion_sensor():
    added = []
    coordinator = DummyCoordinator()
    hass = DummyHass(coordinator)
    entry = DummyEntry()

    await setup_binary_sensor(_as_any(hass), _as_any(entry), _make_add_entities_callback(added))

    assert len(added) == 3
    assert any(isinstance(e, WJGMotionSensor) for e in added)


@pytest.mark.asyncio
async def test_button_platform_setup_adds_ptz_buttons():
    added = []
    coordinator = DummyCoordinator()
    hass = DummyHass(coordinator)
    entry = DummyEntry()

    await setup_button(_as_any(hass), _as_any(entry), _make_add_entities_callback(added))

    assert len(added) == 20
    assert any(isinstance(e, WJGPTZButton) for e in added)


@pytest.mark.asyncio
async def test_sensor_platform_setup_adds_file_list_sensor():
    added = []
    coordinator = DummyCoordinator()
    hass = DummyHass(coordinator)
    entry = DummyEntry()

    await setup_sensor(_as_any(hass), _as_any(entry), _make_add_entities_callback(added))

    assert len(added) == 6
    assert any(isinstance(e, WJGFileListSensor) for e in added)
