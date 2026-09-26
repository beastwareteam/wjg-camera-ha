"""Tests für die Patrouille über den linken Anschlag (patrol.py)."""
from __future__ import annotations

import asyncio
import datetime

import pytest

from custom_components.wjg_camera import patrol as patrol_module
from custom_components.wjg_camera.patrol import (
    PatrolController,
    in_patrol_window,
    parse_patrol_stations,
    parse_patrol_time,
)
from tests_helpers import make_coordinator as _make_coordinator


class DummyEntry:
    def __init__(self, data, options=None):
        self.data = data
        self.entry_id = "patrol-entry"
        self.options = options or {}


class DummyHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


ONVIF_DATA = {
    "host": "192.168.1.60",
    "rtsp_port": 554,
    "port": 80,
    "username": "admin",
    "password": "",
    "protocol": "onvif",
    "onvif_port": 8899,
}


class _FakeCoordinator:
    host = "192.168.178.49"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.run_secs: list[float] = []
        self.motion_detected = False
        self.is_recording = False
        self.on_command = None

    async def async_ptz_run(self, direction: str, seconds: float) -> bool:
        self.calls.append(("run", direction))
        self.run_secs.append(seconds)
        return True

    async def async_ptz_command(self, direction: str, speed: int) -> bool:
        self.calls.append((direction, speed))
        if self.on_command:
            self.on_command()
        return True

    def async_update_listeners(self) -> None:
        return None


def _patrol(coord, stations="0, 2, 3", dwell=0.0, rest=1, now=datetime.time(23, 0), shuffle=False):
    ctl = PatrolController(
        coord,
        start=datetime.time(22, 0),
        end=datetime.time(6, 0),
        dwell_secs=dwell,
        stations=parse_patrol_stations(stations),
        rest_station=rest,
        home_secs=20,
        tilt_home_secs=10,
        shuffle=shuffle,
    )
    ctl.now = lambda: now
    return ctl


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(patrol_module, "PATROL_TICK_SECS", 0.001)
    monkeypatch.setattr(patrol_module, "PATROL_CLICK_GAP_SECS", 0.0)


def test_parse_and_window():
    assert parse_patrol_time("22:00") == datetime.time(22, 0)
    assert parse_patrol_time("06:30:00") == datetime.time(6, 30)
    assert parse_patrol_stations("0, 4;8") == [(0, None), (4, None), (8, None)]
    assert parse_patrol_stations("0/3, 4 / 2") == [(0, 3), (4, 2)]
    for bad in ("", "a,b", "-1", "99", "1/2/3", "1/99", "1/x"):
        with pytest.raises(ValueError):
            parse_patrol_stations(bad)
    for bad in ("22", "25:00", "abc"):
        with pytest.raises(ValueError):
            parse_patrol_time(bad)
    start, end = datetime.time(22, 0), datetime.time(6, 0)
    assert in_patrol_window(datetime.time(23, 0), start, end)
    assert in_patrol_window(datetime.time(5, 59), start, end)
    assert not in_patrol_window(datetime.time(12, 0), start, end)
    assert in_patrol_window(datetime.time(12, 0), start, start)  # gleich = immer


@pytest.mark.asyncio
async def test_round_starts_at_end_stop_and_clicks_between_stations():
    coord = _FakeCoordinator()
    ctl = _patrol(coord)
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("run", "left"),                 # Station 1 = Anschlag
        ("right", 8), ("right", 8),      # → Station 2 (2 Klicks)
        ("right", 8),                    # → Station 3 (1 weiterer Klick)
    ]
    assert ctl.station == 3


@pytest.mark.asyncio
async def test_return_path_clicks_back_and_realigns_at_zero():
    """„0, 2, 3, 2, 0“: Rückweg klickt nach links; die abschließende 0 ist der
    Start der nächsten Runde (kein doppeltes Verweilen) und fährt nur so lange
    zum Anschlag wie nötig."""
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2, 3, 2, 0")
    assert ctl.stations == [(0, None), (2, None), (3, None), (2, None)]
    await ctl._round()  # pylint: disable=protected-access
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("run", "left"),                  # Runde 1, Position unbekannt
        ("right", 8), ("right", 8),       # → 2
        ("right", 8),                     # → 3
        ("left", 8),                      # ← 2 (Rückweg)
        ("run", "left"),                  # Runde 2 beginnt: ← 0 am Anschlag
        ("right", 8), ("right", 8),
        ("right", 8),
        ("left", 8),
    ]
    assert coord.run_secs[0] == 20        # unbekannt → volle Fahrzeit
    assert coord.run_secs[1] == pytest.approx(  # aus 2 Klicks → kurz
        2 * patrol_module.PATROL_CLICK_TRAVEL_SECS + patrol_module.PATROL_STOP_MARGIN_SECS
    )


@pytest.mark.asyncio
async def test_tilt_aligns_at_top_and_clicks_down():
    """„rechts/runter“: Rundenstart richtet links UND oben aus, dann Klicks
    nach rechts und nach unten; Neigungswechsel klickt hoch/runter."""
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0/2, 3/1")
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("run", "left"), ("run", "up"),          # ausrichten (unbekannt → voll)
        ("down", 8), ("down", 8),                # Station 1: 0/2
        ("right", 8), ("right", 8), ("right", 8),
        ("up", 8),                               # Station 2: 3/1
    ]
    assert coord.run_secs == [20, 10]
    await ctl._round()  # pylint: disable=protected-access
    # Runde 2: aus bekannter Position nur kurz zu den Anschlägen
    assert coord.run_secs[2:] == [
        pytest.approx(3 * patrol_module.PATROL_CLICK_TRAVEL_SECS + patrol_module.PATROL_STOP_MARGIN_SECS),
        pytest.approx(1 * patrol_module.PATROL_CLICK_TRAVEL_SECS + patrol_module.PATROL_STOP_MARGIN_SECS),
    ]


@pytest.mark.asyncio
async def test_without_tilt_the_tilt_is_never_touched():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2")
    await ctl._round()  # pylint: disable=protected-access
    assert all(call[0] not in ("up", "down") and call[1] != "up" for call in coord.calls)


@pytest.mark.asyncio
async def test_shuffle_changes_order_but_always_realigns_first():
    """Zufall: jede Runde andere Reihenfolge, aber immer zuerst an den
    Anschlägen ausrichten; alle Stationen werden besucht."""
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2, 4, 6, 8", shuffle=True)
    ctl._rng.seed(1)  # pylint: disable=protected-access
    orders = []
    for _ in range(4):
        visited = []
        coord.calls.clear()
        original = ctl._goto_station  # pylint: disable=protected-access

        async def _spy(index, seq, _orig=original, _visited=visited):
            _visited.append(index)
            return await _orig(index, seq)

        ctl._goto_station = _spy  # type: ignore[method-assign]
        at_stop = ctl._pan == 0  # pylint: disable=protected-access
        await ctl._round()  # pylint: disable=protected-access
        ctl._goto_station = original  # type: ignore[method-assign]
        # Ausrichten vor jeder Runde (entfällt nur, wenn die Kamera nach der
        # letzten Station ohnehin am Anschlag steht)
        assert at_stop or coord.calls[0] == ("run", "left")
        assert sorted(visited) == [1, 2, 3, 4, 5]
        orders.append(tuple(visited))
    assert len(set(orders)) > 1                   # nicht jedes Mal gleich


@pytest.mark.asyncio
async def test_dwell_waits_while_motion():
    coord = _FakeCoordinator()
    coord.motion_detected = True
    ctl = _patrol(coord)
    task = asyncio.create_task(ctl._dwell(ctl._manual_seq))  # pylint: disable=protected-access
    await asyncio.sleep(0.05)
    assert not task.done()               # Bewegung → bleibt stehen
    coord.motion_detected = False
    assert await asyncio.wait_for(task, 1) is True


@pytest.mark.asyncio
async def test_manual_ptz_aborts_round_and_forgets_position():
    coord = _FakeCoordinator()
    ctl = _patrol(coord)
    coord.on_command = ctl.note_manual_ptz   # erster Klick = „manueller“ Eingriff
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [("run", "left"), ("right", 8)]
    assert ctl.station is None


@pytest.mark.asyncio
async def test_outside_window_goes_to_rest_station_once():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, rest=2, now=datetime.time(12, 0))  # Station 2 = 2 Klicks
    ctl.set_enabled(True)
    await asyncio.sleep(0.05)
    await ctl.async_stop()
    assert coord.calls == [("run", "left"), ("right", 8), ("right", 8)]
    assert ctl.status == "aus"


@pytest.mark.asyncio
async def test_ptz_run_moves_full_speed_and_stops(monkeypatch):
    coordinator = _make_coordinator(DummyHass(), DummyEntry(dict(ONVIF_DATA)))
    log: list[tuple] = []

    class _Soap:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def ptz_continuous_move(self, **kwargs):
            log.append(("move", kwargs["pan"], kwargs["tilt"]))
            return True

        async def ptz_stop(self, **_kwargs):
            log.append(("stop",))
            return True

    monkeypatch.setattr(coordinator, "_soap", _Soap)
    assert await coordinator.async_ptz_run("left", 0.01) is True
    assert log == [("move", -1.0, 0.0), ("stop",)]

    # Abbruch mitten in der Fahrt → trotzdem Stop
    log.clear()
    task = asyncio.create_task(coordinator.async_ptz_run("left", 10))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == [("move", -1.0, 0.0), ("stop",)]


def test_invalid_patrol_options_fall_back_to_defaults():
    entry = DummyEntry(
        dict(ONVIF_DATA),
        {"patrol_start": "abc", "patrol_stations": "x", "patrol_dwell": 45},
    )
    coordinator = _make_coordinator(DummyHass(), entry)
    assert coordinator.patrol.start == datetime.time(22, 0)
    assert coordinator.patrol.stations == [(0, None), (4, None), (8, None)]
    assert coordinator.patrol.dwell_secs == 45
