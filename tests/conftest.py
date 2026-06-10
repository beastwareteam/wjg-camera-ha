"""Gemeinsame Test-Fixtures für die wjg_camera-Testsuite.

WICHTIG: Die Unit-Tests sind für Offline-Umgebungen geschrieben (CI ohne
Kamera). Läuft die Suite in einem Netz mit ECHTER Kamera (192.168.178.x),
würde der XMSoapClient-Primärpfad sonst reale PTZ-Befehle an das Gerät
senden und die Kamera physisch bewegen. Der autouse-Stub trennt die Tests
vollständig vom Netzwerk; die Puls-Wartezeiten werden genullt, damit die
Puls-Stepping-Logik ohne reale Sleeps getestet wird.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import custom_components.wjg_camera.coordinator as coordinator_module
import custom_components.wjg_camera.xm_soap as xm_soap_module


class OfflineXMSoapStub:
    """Simuliert einen nicht erreichbaren XMSoapClient (Offline-CI-Verhalten).

    Alle PTZ-/Event-Aufrufe schlagen fehl, sodass die Tests wie vorgesehen
    die Fallback-Pfade des Coordinators ausüben.
    """

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ptz_command(self, *args, **kwargs):
        return False

    async def ptz_stop(self, *args, **kwargs):
        return False

    async def ptz_goto_home(self, *args, **kwargs):
        return False

    async def ptz_set_home(self, *args, **kwargs):
        return False

    async def ptz_goto_preset(self, *args, **kwargs):
        return False

    async def ptz_set_preset(self, *args, **kwargs):
        return None

    async def ptz_get_presets(self, *args, **kwargs):
        return {}

    async def create_pull_point_subscription(self):
        return None

    async def pull_messages(self, *args, **kwargs):
        return []


async def _tcp_never_reachable(self, port, timeout=2.0):
    """Kein echter TCP-Connect in Unit-Tests (keine Timeouts, kein Netz)."""
    _ = self, port, timeout
    return False


@pytest.fixture(autouse=True)
def _offline_network(monkeypatch):
    """XMSoapClient-Primärpfad offline stellen, Puls-Timing nullen."""
    monkeypatch.setattr(coordinator_module, "_XMSoapClient", OfflineXMSoapStub)
    monkeypatch.setattr(coordinator_module, "_PTZ_PULSE_DURATION", 0.0)
    monkeypatch.setattr(coordinator_module, "_PTZ_PULSE_GAP", 0.0)
    monkeypatch.setattr(xm_soap_module, "PTZ_PULSE_DURATION", 0.0)
    monkeypatch.setattr(xm_soap_module, "PTZ_PULSE_GAP", 0.0)
    monkeypatch.setattr(
        coordinator_module.WJGCameraCoordinator,
        "_tcp_port_reachable",
        _tcp_never_reachable,
    )
