import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import custom_components.wjg_camera as integration
from tests_helpers import as_any as _as_any


class DummyEntry:
    def __init__(self, data=None, entry_id="lifecycle-entry"):
        self.data = data or {"host": "192.168.1.70"}
        self.entry_id = entry_id


@pytest.mark.asyncio
async def test_async_unload_entry_success_removes_coordinator_and_shutdowns():
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()

    hass = MagicMock()
    hass.data = {integration.DOMAIN: {"lifecycle-entry": coordinator}}
    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    entry = DummyEntry(entry_id="lifecycle-entry")

    result = await integration.async_unload_entry(hass, _as_any(entry))

    assert result is True
    coordinator.async_shutdown.assert_awaited_once()
    assert "lifecycle-entry" not in hass.data[integration.DOMAIN]
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, integration.PLATFORMS
    )


@pytest.mark.asyncio
async def test_async_unload_entry_failure_keeps_coordinator():
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()

    hass = MagicMock()
    hass.data = {integration.DOMAIN: {"lifecycle-entry": coordinator}}
    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    entry = DummyEntry(entry_id="lifecycle-entry")

    result = await integration.async_unload_entry(hass, _as_any(entry))

    assert result is False
    coordinator.async_shutdown.assert_not_called()
    assert "lifecycle-entry" in hass.data[integration.DOMAIN]


@pytest.mark.asyncio
async def test_async_reload_entry_calls_unload_then_setup(monkeypatch):
    call_order = []
    seen_entries = []

    async def fake_unload(hass, entry):
        _ = hass
        call_order.append("unload")
        seen_entries.append(entry)
        return True

    async def fake_setup(hass, entry):
        _ = hass
        call_order.append("setup")
        seen_entries.append(entry)
        return True

    monkeypatch.setattr(integration, "async_unload_entry", fake_unload)
    monkeypatch.setattr(integration, "async_setup_entry", fake_setup)

    hass = MagicMock()
    entry = DummyEntry()

    await integration.async_reload_entry(hass, _as_any(entry))

    assert call_order == ["unload", "setup"]
    assert seen_entries == [entry, entry]