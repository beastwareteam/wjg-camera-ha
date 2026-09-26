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
        self.calls: list[tuple[str, float]] = []   # (Richtung, Sekunden)
        self.motion_detected = False
        self.is_recording = False
        self.on_run = None

    async def async_ptz_run(self, direction: str, seconds: float) -> bool:
        self.calls.append((direction, round(seconds, 3)))
        if self.on_run:
            self.on_run()
        return True

    def async_update_listeners(self) -> None:
        return None

    def dirs(self) -> list[str]:
        return [d for d, _ in self.calls]


T = patrol_module.PATROL_CLICK_TRAVEL_SECS
M = patrol_module.PATROL_STOP_MARGIN_SECS
LAG = patrol_module.PATROL_STOP_LAG_SECS


def _move(clicks: float) -> float:
    """Wartezeit einer Fahrt über `clicks` Klicks (siehe PatrolController._move)."""
    return round(clicks * T - LAG, 3)


def _home(clicks: float) -> float:
    return round(clicks * T + M, 3)


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
    assert parse_patrol_stations("0, 4, 8") == [(0, None), (4, None), (8, None)]
    assert parse_patrol_stations("0/3, 4 / 2") == [(0, 3), (4, 2)]
    # gemischt = fast immer ein deutsches Komma in einer ","-Liste → Fehler
    assert parse_patrol_stations("0/0.8, 3.5/1.2") == [(0, 0.8), (3.5, 1.2)]
    # Mit ";" als Trenner darf das deutsche Komma stehen
    assert parse_patrol_stations("0/0,8; 3,5/1,2") == [(0, 0.8), (3.5, 1.2)]
    for bad in ("", "a,b", "-1", "99", "1/2/3", "1/99", "1/x", "nan", "0/0,8, 3,5/1,2", "0/3, 4"):
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
async def test_round_aligns_then_one_timed_move_per_station():
    coord = _FakeCoordinator()
    ctl = _patrol(coord)
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("left", 20),              # ausrichten (unbekannt → volle Fahrzeit)
        ("right", _move(2)),       # → Station 2
        ("right", _move(1)),       # → Station 3
    ]
    assert ctl.station == 3


@pytest.mark.asyncio
async def test_fractional_clicks():
    """Bruchteile: 0.5 Klick = eine kurze Fahrt statt eines ganzen Klicks."""
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0/0.5, 1.5/1.25")
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("left", 20), ("up", 10),
        ("down", _move(0.5)),
        ("right", _move(1.5)), ("down", _move(0.75)),
    ]


@pytest.mark.asyncio
async def test_return_path_moves_back_and_realigns_at_zero():
    """„0, 2, 3, 2, 0“: Rückweg fährt nach links; die abschließende 0 ist der
    Start der nächsten Runde und fährt nur so lange zum Anschlag wie nötig."""
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2, 3, 2, 0")
    assert ctl.stations == [(0, None), (2, None), (3, None), (2, None)]
    await ctl._round()  # pylint: disable=protected-access
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("left", 20),
        ("right", _move(2)), ("right", _move(1)), ("left", _move(1)),
        ("left", _home(2)),                  # Runde 2: aus 2 Klicks → kurz
        ("right", _move(2)), ("right", _move(1)), ("left", _move(1)),
    ]


@pytest.mark.asyncio
async def test_tilt_aligns_at_top_and_moves_down():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0/2, 3/1")
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls == [
        ("left", 20), ("up", 10),
        ("down", _move(2)),
        ("right", _move(3)), ("up", _move(1)),
    ]
    coord.calls.clear()
    await ctl._round()  # pylint: disable=protected-access
    assert coord.calls[:2] == [("left", _home(3)), ("up", _home(1))]


@pytest.mark.asyncio
async def test_without_tilt_the_tilt_is_never_touched():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2")
    await ctl._round()  # pylint: disable=protected-access
    assert not {"up", "down"} & set(coord.dirs())


@pytest.mark.asyncio
async def test_shuffle_changes_order_but_always_realigns_first():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, stations="0, 2, 4, 6, 8", shuffle=True)
    ctl._rng.seed(1)  # pylint: disable=protected-access
    orders = []
    for _ in range(4):
        coord.calls.clear()
        at_stop = ctl._pan == 0  # pylint: disable=protected-access
        visited = []
        original = ctl._goto_station  # pylint: disable=protected-access

        async def _spy(index, seq, _orig=original, _visited=visited):
            _visited.append(index)
            return await _orig(index, seq)

        ctl._goto_station = _spy  # type: ignore[method-assign]
        await ctl._round()  # pylint: disable=protected-access
        ctl._goto_station = original  # type: ignore[method-assign]
        # Ausrichten vor jeder Runde (entfällt nur, wenn die Kamera nach der
        # letzten Station ohnehin am Anschlag steht)
        assert at_stop or coord.calls[0][0] == "left"
        assert sorted(visited) == [1, 2, 3, 4, 5]
        orders.append(tuple(visited))
    assert len(set(orders)) > 1


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
    runs = {"n": 0}

    def _on_run():
        runs["n"] += 1
        if runs["n"] == 2:               # während der ersten Stationsfahrt
            ctl.note_manual_ptz()

    coord.on_run = _on_run
    await ctl._round()  # pylint: disable=protected-access
    assert coord.dirs() == ["left", "right"]
    assert ctl.station is None


@pytest.mark.asyncio
async def test_outside_window_goes_to_rest_station_once():
    coord = _FakeCoordinator()
    ctl = _patrol(coord, rest=2, now=datetime.time(12, 0))  # Station 2 = 2 Klicks
    ctl.set_enabled(True)
    await asyncio.sleep(0.05)
    await ctl.async_stop()
    assert coord.calls == [("left", 20), ("right", _move(2))]
    assert ctl.status == "aus"


@pytest.mark.asyncio
async def test_test_station_moves_there_and_pauses_patrol():
    coord = _FakeCoordinator()
    ctl = _patrol(coord)
    result = await ctl.async_test_station("3.5/1.2")
    assert result["angefahren"] is True and result["station"] == "3.5/1.2"
    assert coord.calls == [("left", 20), ("right", _move(3.5)), ("up", 10), ("down", _move(1.2))]
    assert ctl._manual_until > 0  # pylint: disable=protected-access
    with pytest.raises(ValueError):
        await ctl.async_test_station("1, 2")


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
