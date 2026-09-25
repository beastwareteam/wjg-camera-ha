"""Patrouille ohne Presets (ab v2.2.63).

Die XM-3820 fährt bei GotoPreset/GotoHomePosition über ONVIF immer denselben
festen Punkt an (live bestätigt 25.09.2026), Presets sind über Home Assistant
also unbrauchbar. Einziger verlässlicher Bezugspunkt: der linke Anschlag.

Ablauf einer Runde:
  1. mit voller Geschwindigkeit nach links bis zum Anschlag fahren
  2. für jede Station: Klicks (Stufe 8) nach rechts bis zur Station,
     dort verweilen; solange Bewegung/Aufnahme läuft, stehen bleiben
  3. nächste Runde beginnt wieder am Anschlag → keine Drift

Außerhalb des Zeitfensters fährt die Kamera einmal zur Ruhe-Station.
Manuelle PTZ-Klicks pausieren die Patrouille; danach startet die Runde neu
am Anschlag, weil die Position dann nicht mehr bekannt ist.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)

PATROL_MANUAL_PAUSE_SECS = 300.0   # Pause nach manuellem PTZ-Klick
PATROL_CLICK_LEVEL = 8             # Stufe der Klicks zwischen den Stationen
PATROL_CLICK_GAP_SECS = 0.5        # Abstand zwischen zwei Klicks
PATROL_TICK_SECS = 1.0             # Prüfintervall beim Warten
PATROL_ERROR_RETRY_SECS = 30.0
PATROL_MAX_CLICKS = 60
# Fahrt zum Anschlag aus bekannter Position: je Klick Stufe 8 ~1,6 s Halten +
# ~0,2 s Stop-Latenz bei gleicher Geschwindigkeit (Velocity 1.0), plus Reserve,
# damit der Anschlag sicher erreicht wird, der Motor aber nicht lange rattert.
PATROL_CLICK_TRAVEL_SECS = 1.8
PATROL_STOP_MARGIN_SECS = 3.0


def parse_patrol_time(value: Any) -> datetime.time:
    """"HH:MM" oder "HH:MM:SS" → time. Wirft ValueError bei ungültigem Wert."""
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Uhrzeit '{value}' nicht im Format HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    return datetime.time(hour, minute, second)


def parse_patrol_stations(value: Any) -> list[int]:
    """"0, 4, 8" → [0, 4, 8] (Klicks ab linkem Anschlag).
    Wirft ValueError bei ungültigem Wert."""
    items = [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]
    if not items:
        raise ValueError("mindestens eine Station angeben")
    stations = [int(p) for p in items]
    if any(s < 0 or s > PATROL_MAX_CLICKS for s in stations):
        raise ValueError(f"Klicks je Station müssen zwischen 0 und {PATROL_MAX_CLICKS} liegen")
    return stations


def in_patrol_window(now: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    """Liegt `now` im Zeitfenster? Über Mitternacht erlaubt; start == end = immer."""
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _local_now() -> datetime.time:
    try:
        from homeassistant.util import dt as dt_util  # pylint: disable=import-outside-toplevel
        return dt_util.now().time()
    except Exception:  # pragma: no cover - nur ohne HA
        return datetime.datetime.now().time()


class PatrolController:
    """Steuert die Patrouille einer Kamera (ein Hintergrund-Task)."""

    def __init__(
        self,
        coordinator: WJGCameraCoordinator,
        *,
        start: datetime.time,
        end: datetime.time,
        dwell_secs: float,
        stations: list[int],
        rest_station: int,
        home_secs: float,
    ) -> None:
        self._coordinator = coordinator
        self.start = start
        self.end = end
        self.dwell_secs = float(dwell_secs)
        stations = list(stations) or [0]
        # Hin- und Rückweg „0, 4, …, 4, 0“: die letzte Station ist die erste der
        # nächsten Runde → nicht doppelt anfahren/verweilen.
        if len(stations) > 1 and stations[0] == stations[-1]:
            stations = stations[:-1]
        self.stations = stations
        self.rest_station = max(1, min(len(self.stations), int(rest_station)))
        self.home_secs = float(home_secs)
        self._task: asyncio.Task[None] | None = None
        self._manual_until = 0.0
        self._manual_seq = 0
        self.status = "aus"
        self.station: int | None = None   # 1-basiert; None = unbekannt
        self.now = _local_now              # in Tests austauschbar

    # ── Steuerung ────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.enabled:
            hass = getattr(self._coordinator, "hass", None)
            create = getattr(hass, "async_create_background_task", None)
            name = f"wjg_camera_patrol_{self._coordinator.host}"
            # Background-Task: blockiert weder Start noch Stopp von Home Assistant
            self._task = create(self._run(), name) if create else asyncio.create_task(self._run())
            _LOGGER.info(
                "Patrouille (%s) an: %s–%s, Stationen %s, Verweildauer %ds, Ruhe-Station %d",
                self._coordinator.host, self.start.strftime("%H:%M"),
                self.end.strftime("%H:%M"), self.stations, int(self.dwell_secs),
                self.rest_station,
            )
        elif not enabled and self._task is not None:
            self._task.cancel()
            self._task = None
            self.status = "aus"
            _LOGGER.info("Patrouille (%s) aus", self._coordinator.host)

    async def async_stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.status = "aus"

    def note_manual_ptz(self) -> None:
        """Manueller PTZ-Klick: Patrouille pausieren, Position gilt als unbekannt."""
        self._manual_until = time.monotonic() + PATROL_MANUAL_PAUSE_SECS
        self._manual_seq += 1
        self.station = None

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "station": self.station,
            "stationen": self.stations,
            "zeitfenster": f"{self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')}",
            "verweildauer_s": int(self.dwell_secs),
            "ruhe_station": self.rest_station,
        }

    # ── Ablauf ───────────────────────────────────────────────────────────────

    def _in_window(self) -> bool:
        return in_patrol_window(self.now(), self.start, self.end)

    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self._coordinator.async_update_listeners()

    async def _run(self) -> None:
        at_rest = False
        while True:
            try:
                if time.monotonic() < self._manual_until:
                    self._set_status("pausiert (manuelle Steuerung)")
                    await asyncio.sleep(PATROL_TICK_SECS)
                    continue
                if not self._in_window():
                    if not at_rest:
                        self._set_status("fährt zur Ruhe-Station")
                        seq = self._manual_seq
                        if await self._goto_station(self.rest_station, seq):
                            at_rest = True
                    self._set_status("außerhalb Zeitfenster")
                    await asyncio.sleep(PATROL_TICK_SECS * 30)
                    continue
                at_rest = False
                await self._round()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Patrouille darf nie sterben
                _LOGGER.exception("Patrouille (%s): Fehler, neuer Versuch", self._coordinator.host)
                self.station = None
                await asyncio.sleep(PATROL_ERROR_RETRY_SECS)

    async def _round(self) -> None:
        """Eine Runde über alle Stationen; bricht bei manuellem Eingriff oder
        Ende des Zeitfensters ab."""
        seq = self._manual_seq
        for index in range(1, len(self.stations) + 1):
            if not self._in_window() or seq != self._manual_seq:
                return
            self._set_status(f"fährt zu Station {index}")
            if not await self._goto_station(index, seq):
                return
            self._set_status(f"überwacht Station {index}")
            if not await self._dwell(seq):
                return

    async def _goto_station(self, index: int, seq: int) -> bool:
        """Station `index` (1-basiert) anfahren. Rundenstart, Stationen mit 0
        Klicks und unbekannte Position → erst zum linken Anschlag (neu
        ausrichten), sonst nur die Differenz klicken (auch nach links)."""
        coord = self._coordinator
        target = self.stations[index - 1]
        known = None if self.station is None else self.stations[self.station - 1]
        if known is None or index == 1 or target == 0:
            # Aus bekannter Position nur so lange fahren wie nötig (kein langes
            # Rattern am Anschlag); unbekannt → volle Fahrzeit.
            secs = self.home_secs if known is None else min(
                self.home_secs, known * PATROL_CLICK_TRAVEL_SECS + PATROL_STOP_MARGIN_SECS
            )
            _LOGGER.info("Patrouille (%s): fahre zum linken Anschlag (%.0fs)", coord.host, secs)
            if not await coord.async_ptz_run("left", secs):
                return False
            current = 0
        else:
            current = known
        self.station = None
        delta = target - current
        direction = "right" if delta > 0 else "left"
        for _ in range(abs(delta)):
            if seq != self._manual_seq:
                return False
            if not await coord.async_ptz_command(direction, PATROL_CLICK_LEVEL):
                return False
            await asyncio.sleep(PATROL_CLICK_GAP_SECS)
        if seq != self._manual_seq:
            return False
        self.station = index
        _LOGGER.info("Patrouille (%s): Station %d erreicht (%d Klicks)", coord.host, index, target)
        coord.async_update_listeners()
        return True

    async def _dwell(self, seq: int) -> bool:
        """Verweilen; danach warten, solange Bewegung erkannt wird oder eine
        Aufnahme läuft. False bei manuellem Eingriff oder Ende des Fensters."""
        coord = self._coordinator
        deadline = time.monotonic() + self.dwell_secs
        while True:
            if seq != self._manual_seq or not self._in_window():
                return False
            busy = coord.motion_detected or coord.is_recording
            if time.monotonic() >= deadline and not busy:
                return True
            if busy and time.monotonic() >= deadline:
                self._set_status(f"Bewegung – bleibt an Station {self.station}")
            await asyncio.sleep(PATROL_TICK_SECS)
