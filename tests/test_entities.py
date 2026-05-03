import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.wjg_camera.binary_sensor import WJGMotionSensor
from custom_components.wjg_camera.button import WJGPTZButton
from custom_components.wjg_camera.camera import WJGCamera, _FALLBACK_IMAGE_BYTES
from custom_components.wjg_camera.sensor import WJGFileListSensor
from custom_components.wjg_camera.switch import WJGMicrophoneSwitch, WJGRecordingSwitch
from tests_helpers import as_any as _as_any


class DummyEntry:
    def __init__(self, data=None, entry_id="entity-entry"):
        self.data = data or {}
        self.entry_id = entry_id


class DummyCoordinator:
    def __init__(self):
        self.host = "192.168.1.50"
        self.http_port = 80
        self.protocol = "rtsp"
        self.rtsp_url = "rtsp://192.168.1.50:554/stream"
        self.snapshot_url = "http://192.168.1.50/webcapture.jpg"
        self.data = {"available": True, "snapshot_bytes": b"cached-image"}
        self._listeners = []
        self.last_update_success = True
        self.is_recording = False
        self.motion_detected = True
        self.last_motion_time = 1234567890.0
        self.last_ptz_fault = ""
        self.async_ptz_command = AsyncMock(return_value=True)
        self.async_set_recording = AsyncMock(return_value=True)
        self.microphone_enabled = True
        self.async_set_microphone_enabled = AsyncMock(return_value=True)
        self.async_get_file_list = AsyncMock(return_value=[{"name": "a.h264"}, {"name": "b.h264"}])
        self.async_snapshot = AsyncMock(return_value=b"live-image")

    def async_add_listener(self, update_callback):
        self._listeners.append(update_callback)

        def remove_listener():
            self._listeners.remove(update_callback)

        return remove_listener


def test_camera_entity_exposes_expected_attributes():
    coordinator = DummyCoordinator()
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry()))

    assert entity.available is True
    assert entity.is_recording is False
    assert entity.extra_state_attributes["rtsp_url"] == coordinator.rtsp_url
    assert entity.extra_state_attributes["snapshot_url"] == coordinator.snapshot_url
    assert entity.extra_state_attributes["protocol"] == coordinator.protocol


@pytest.mark.asyncio
async def test_camera_entity_prefers_cached_snapshot():
    coordinator = DummyCoordinator()
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry()))

    image = await entity.async_camera_image()

    assert image == b"cached-image"
    coordinator.async_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_camera_entity_falls_back_to_live_snapshot_without_cache():
    coordinator = DummyCoordinator()
    coordinator.data = {"available": True}
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry()))

    image = await entity.async_camera_image()

    assert image == b"live-image"
    coordinator.async_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_camera_entity_uses_last_successful_image_when_snapshot_temporarily_unavailable():
    coordinator = DummyCoordinator()
    coordinator.data = {"available": True, "snapshot_bytes": b"cached-image"}
    coordinator.async_snapshot = AsyncMock(return_value=None)
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry()))

    first = await entity.async_camera_image()
    coordinator.data = {"available": True}
    second = await entity.async_camera_image()

    assert first == b"cached-image"
    assert second == b"cached-image"


@pytest.mark.asyncio
async def test_camera_entity_returns_static_fallback_image_when_no_snapshot_available():
    coordinator = DummyCoordinator()
    coordinator.data = {"available": True}
    coordinator.async_snapshot = AsyncMock(return_value=None)
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry()))

    image = await entity.async_camera_image()

    assert image == _FALLBACK_IMAGE_BYTES


@pytest.mark.asyncio
async def test_camera_entity_misc_properties_and_noop_controls():
    coordinator = DummyCoordinator()
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry(entry_id="cam-1")))

    device_info = entity.device_info
    stream = await entity.stream_source()
    await entity.async_enable_motion_detection()
    await entity.async_disable_motion_detection()
    await entity.async_turn_on()
    await entity.async_turn_off()

    assert entity.motion_detection_enabled is True
    assert stream == coordinator.rtsp_url
    assert entity.stream_options.get("rtsp_transport") == "tcp"
    assert device_info.get("manufacturer") is not None
    assert device_info.get("model") is not None
    assert device_info.get("configuration_url") is not None


@pytest.mark.asyncio
async def test_ptz_button_calls_coordinator():
    coordinator = DummyCoordinator()
    button = WJGPTZButton(_as_any(coordinator), _as_any(DummyEntry()), "left")

    await button.async_press()

    coordinator.async_ptz_command.assert_awaited_once_with("left")
    assert button.icon == "mdi:arrow-left-bold-circle"


@pytest.mark.asyncio
async def test_ptz_button_handles_failed_command_and_device_info():
    coordinator = DummyCoordinator()
    coordinator.async_ptz_command = AsyncMock(return_value=False)
    coordinator.last_ptz_fault = "ActionNotSupported"
    button = WJGPTZButton(_as_any(coordinator), _as_any(DummyEntry(entry_id="btn-1")), "up")

    with pytest.raises(HomeAssistantError, match="ActionNotSupported"):
        await button.async_press()
    info = button.device_info

    coordinator.async_ptz_command.assert_awaited_once_with("up")
    assert info.get("identifiers") is not None


def test_button_sync_press_raises_not_implemented():
    coordinator = DummyCoordinator()
    button = WJGPTZButton(_as_any(coordinator), _as_any(DummyEntry()), "up")

    with pytest.raises(NotImplementedError):
        button.press()


@pytest.mark.asyncio
async def test_recording_switch_toggles_recording():
    coordinator = DummyCoordinator()
    switch = WJGRecordingSwitch(_as_any(coordinator), _as_any(DummyEntry()))
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    await switch.async_turn_off()

    coordinator.async_set_recording.assert_any_await(True)
    coordinator.async_set_recording.assert_any_await(False)
    assert switch.async_write_ha_state.call_count == 2


@pytest.mark.asyncio
async def test_recording_switch_failure_and_sync_methods_and_properties():
    coordinator = DummyCoordinator()
    coordinator.is_recording = True
    coordinator.async_set_recording = AsyncMock(return_value=False)
    switch = WJGRecordingSwitch(_as_any(coordinator), _as_any(DummyEntry(entry_id="sw-1")))
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    info = switch.device_info

    assert switch.is_on is True
    assert info.get("identifiers") is not None
    coordinator.async_set_recording.assert_awaited_once_with(True)
    with pytest.raises(NotImplementedError):
        switch.turn_on()
    with pytest.raises(NotImplementedError):
        switch.turn_off()


@pytest.mark.asyncio
async def test_microphone_switch_toggles_microphone():
    coordinator = DummyCoordinator()
    switch = WJGMicrophoneSwitch(_as_any(coordinator), _as_any(DummyEntry()))
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()
    await switch.async_turn_on()

    coordinator.async_set_microphone_enabled.assert_any_await(False)
    coordinator.async_set_microphone_enabled.assert_any_await(True)
    assert switch.async_write_ha_state.call_count == 2


def test_microphone_switch_sync_methods_raise_not_implemented():
    coordinator = DummyCoordinator()
    switch = WJGMicrophoneSwitch(_as_any(coordinator), _as_any(DummyEntry(entry_id="mic-1")))

    assert switch.is_on is True
    assert switch.device_info.get("identifiers") is not None
    with pytest.raises(NotImplementedError):
        switch.turn_on()
    with pytest.raises(NotImplementedError):
        switch.turn_off()


def test_motion_sensor_reads_motion_state_and_timestamp():
    coordinator = DummyCoordinator()
    sensor = WJGMotionSensor(_as_any(coordinator), _as_any(DummyEntry()))

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["last_motion"] == 1234567890.0
    assert sensor.device_info.get("identifiers") is not None


@pytest.mark.asyncio
async def test_file_list_sensor_updates_attributes():
    coordinator = DummyCoordinator()
    sensor = WJGFileListSensor(_as_any(coordinator), _as_any(DummyEntry()))

    await sensor.async_update()
    attributes = sensor.extra_state_attributes

    coordinator.async_get_file_list.assert_awaited_once()
    assert sensor.native_value == 2
    assert attributes is not None
    assert attributes["files"][0]["name"] == "a.h264"
    assert sensor.device_info.get("identifiers") is not None


def test_camera_sync_methods_raise_not_implemented():
    coordinator = DummyCoordinator()
    entity = WJGCamera(_as_any(coordinator), _as_any(DummyEntry(entry_id="cam-sync")))

    with pytest.raises(NotImplementedError):
        entity.camera_image()
    with pytest.raises(NotImplementedError):
        entity.enable_motion_detection()
    with pytest.raises(NotImplementedError):
        entity.disable_motion_detection()
    with pytest.raises(NotImplementedError):
        entity.turn_on()
    with pytest.raises(NotImplementedError):
        entity.turn_off()