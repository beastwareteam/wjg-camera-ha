"""Patrouille ohne Presets (ab v2.2.63, Neigung + Zufall ab v2.2.65).

Die XM-3820 fährt bei GotoPreset/GotoHomePosition über ONVIF immer denselben
festen Punkt an (live bestätigt 25.09.2026), Presets sind über Home Assistant
also unbrauchbar. Einzige verlässliche Bezugspunkte: die Anschläge.

Stationen: "rechts" oder "rechts/runter" in Klicks (Stufe 8) ab dem linken
bzw. oberen Anschlag, z. B. "0/3, 4/3, 8/2". Ohne "/runter" bleibt die
Neigung unverändert.

Ablauf einer Runde:
  1. neu ausrichten: nach links (und, falls Stationen eine Neigung haben,
     nach oben) bis zum Anschlag – aus bekannter Position nur so lange wie nötig
  2. Stationen der Reihe nach (oder zufällig gemischt) per Klicks anfahren,
     dort verweilen; solange Bewegung/Aufnahme läuft, stehen bleiben
  3. nächste Runde beginnt wieder mit dem Ausrichten → keine Drift

Außerhalb des Zeitfensters fährt die Kamera einmal zur Ruhe-Station.
Manuelle PTZ-Klicks pausieren die Patrouille; danach ist die Position
unbekannt und es wird mit voller Fahrzeit neu ausgerichtet.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import random
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


Station = tuple[int, "int | None"]   # (Klicks rechts, Klicks runter | None)


def parse_patrol_stations(value: Any) -> list[Station]:
    """"0/3, 4, 8/2" → [(0, 3), (4, None), (8, 2)]: Klicks ab linkem bzw.
    oberem Anschlag; ohne "/runter" wird die Neigung nicht verändert.
    Wirft ValueError bei ungültigem Wert."""
    items = [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]
    if not items:
        raise ValueError("mindestens eine Station angeben")
    stations: list[Station] = []
    for item in items:
        parts = [p.strip() for p in item.split("/")]
        if len(parts) > 2:
            raise ValueError(f"Station '{item}' nicht im Format rechts/runter")
        pan = int(parts[0])
        tilt = int(parts[1]) if len(parts) == 2 else None
        for clicks in (pan, tilt):
            if clicks is not None and not 0 <= clicks <= PATROL_MAX_CLICKS:
                raise ValueError(
                    f"Klicks je Station müssen zwischen 0 und {PATROL_MAX_CLICKS} liegen"
                )
        stations.append((pan, tilt))
    return stations


def format_patrol_station(station: Station) -> str:
    pan, tilt = station
    return str(pan) if tilt is None else f"{pan}/{tilt}"


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
        stations: list[Station],
        rest_station: int,
        home_secs: float,
        tilt_home_secs: float = 10.0,
        shuffle: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self.start = start
        self.end = end
        self.dwell_secs = float(dwell_secs)
        stations = list(stations) or [(0, None)]
        # Hin- und Rückweg „0, 4, …, 4, 0“: die letzte Station ist die erste der
        # nächsten Runde → nicht doppelt anfahren/verweilen.
        if len(stations) > 1 and stations[0] == stations[-1]:
            stations = stations[:-1]
        self.stations = stations
        self.rest_station = max(1, min(len(self.stations), int(rest_station)))
        self.home_secs = float(home_secs)
        self.tilt_home_secs = float(tilt_home_secs)
        self.shuffle = bool(shuffle)
        self.uses_tilt = any(tilt is not None for _, tilt in self.stations)
        self._task: asyncio.Task[None] | None = None
        self._manual_until = 0.0
        self._manual_seq = 0
        self._pan: int | None = None      # Klicks ab linkem Anschlag; None = unbekannt
        self._tilt: int | None = None     # Klicks ab oberem Anschlag; None = unbekannt
        self._rng = random.Random()
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
                "Patrouille (%s) an: %s–%s, Stationen %s%s, Verweildauer %ds, Ruhe-Station %d",
                self._coordinator.host, self.start.strftime("%H:%M"),
                self.end.strftime("%H:%M"), self._stations_text(),
                " (zufällig)" if self.shuffle else "", int(self.dwell_secs),
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
        self._forget_position()

    def _forget_position(self) -> None:
        self.station = None
        self._pan = None
        self._tilt = None

    def _stations_text(self) -> str:
        return ", ".join(format_patrol_station(st) for st in self.stations)

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "station": self.station,
            "stationen": self._stations_text(),
            "reihenfolge": "zufällig" if self.shuffle else "wie eingetragen",
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
                self._forget_position()
                await asyncio.sleep(PATROL_ERROR_RETRY_SECS)

    def _round_order(self) -> list[int]:
        order = list(range(1, len(self.stations) + 1))
        if self.shuffle and len(order) > 1:
            last = self.station
            self._rng.shuffle(order)
            # nicht dort beginnen, wo die letzte Runde aufgehört hat
            if order[0] == last:
                order.append(order.pop(0))
        return order

    async def _round(self) -> None:
        """Eine Runde: neu ausrichten, dann alle Stationen; bricht bei
        manuellem Eingriff oder Ende des Zeitfensters ab."""
        seq = self._manual_seq
        self._set_status("richtet sich an den Anschlägen aus")
        if not await self._align(seq):
            return
        for index in self._round_order():
            if not self._in_window() or seq != self._manual_seq:
                return
            self._set_status(f"fährt zu Station {index}")
            if not await self._goto_station(index, seq):
                return
            self._set_status(f"überwacht Station {index}")
            if not await self._dwell(seq):
                return

    @staticmethod
    def _run_secs(known: int | None, full: float) -> float:
        """Fahrzeit zum Anschlag: unbekannt → volle Fahrzeit, sonst nur so lange
        wie nötig (kein langes Rattern am Anschlag)."""
        if known is None:
            return full
        return min(full, known * PATROL_CLICK_TRAVEL_SECS + PATROL_STOP_MARGIN_SECS)

    async def _align_pan(self) -> bool:
        secs = self._run_secs(self._pan, self.home_secs)
        self._pan = None
        _LOGGER.info("Patrouille (%s): fahre zum linken Anschlag (%.0fs)", self._coordinator.host, secs)
        if not await self._coordinator.async_ptz_run("left", secs):
            return False
        self._pan = 0
        return True

    async def _align_tilt(self) -> bool:
        secs = self._run_secs(self._tilt, self.tilt_home_secs)
        self._tilt = None
        _LOGGER.info("Patrouille (%s): fahre zum oberen Anschlag (%.0fs)", self._coordinator.host, secs)
        if not await self._coordinator.async_ptz_run("up", secs):
            return False
        self._tilt = 0
        return True

    async def _align(self, seq: int) -> bool:
        """Rundenstart: an den Anschlägen neu ausrichten (Neigung nur, wenn
        Stationen eine Neigung vorgeben)."""
        self.station = None
        if self._pan != 0 and not await self._align_pan():
            return False
        if self.uses_tilt and self._tilt != 0 and not await self._align_tilt():
            return False
        return seq == self._manual_seq

    async def _click(self, positive: str, negative: str, delta: int, seq: int) -> bool:
        direction = positive if delta > 0 else negative
        for _ in range(abs(delta)):
            if seq != self._manual_seq:
                return False
            if not await self._coordinator.async_ptz_command(direction, PATROL_CLICK_LEVEL):
                return False
            await asyncio.sleep(PATROL_CLICK_GAP_SECS)
        return seq == self._manual_seq

    async def _goto_station(self, index: int, seq: int) -> bool:
        """Station `index` (1-basiert) anfahren. Unbekannte Position oder Ziel
        0 → Anschlag (neu ausrichten), sonst die Differenz klicken."""
        coord = self._coordinator
        pan, tilt = self.stations[index - 1]
        self.station = None
        # Schwenken
        if self._pan is None or (pan == 0 and self._pan != 0):
            if not await self._align_pan():
                return False
        current, self._pan = self._pan, None
        if not await self._click("right", "left", pan - current, seq):
            return False
        self._pan = pan
        # Neigen (nur wenn die Station eine Neigung vorgibt)
        if tilt is not None:
            if self._tilt is None or (tilt == 0 and self._tilt != 0):
                if not await self._align_tilt():
                    return False
            current, self._tilt = self._tilt, None
            if not await self._click("down", "up", tilt - current, seq):
                return False
            self._tilt = tilt
        self.station = index
        _LOGGER.info(
            "Patrouille (%s): Station %d erreicht (%s)",
            coord.host, index, format_patrol_station(self.stations[index - 1]),
        )
        coord.async_update_listeners()
        return True

    async def _dwell(self, seq: int) -> bool:
        """Verweilen (bei Zufall 50–150 % der Verweildauer); danach warten,
        solange Bewegung erkannt wird oder eine Aufnahme läuft. False bei
        manuellem Eingriff oder Ende des Fensters."""
        coord = self._coordinator
        dwell = self.dwell_secs * (self._rng.uniform(0.5, 1.5) if self.shuffle else 1.0)
        deadline = time.monotonic() + dwell
        while True:
            if seq != self._manual_seq or not self._in_window():
                return False
            busy = coord.motion_detected or coord.is_recording
            if time.monotonic() >= deadline and not busy:
                return True
            if busy and time.monotonic() >= deadline:
                self._set_status(f"Bewegung – bleibt an Station {self.station}")
            await asyncio.sleep(PATROL_TICK_SECS)
