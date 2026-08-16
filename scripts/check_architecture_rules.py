#!/usr/bin/env python3
"""Prueft die in custom_components/wjg_camera/CLAUDE.md unter
"Was NICHT geaendert werden darf" festgehaltenen Architektur-Regeln.

Diese Regeln wurden nach mehrfach wiederkehrenden PTZ-/Motion-Auth-Bugs
festgelegt (IP-basiertes Session-Lockout der XM-Firmware, siehe CLAUDE.md).
Ein Verstoss fuehrt in der Praxis zu HTTP-400-Lockouts der Kamera, die nur
per Stromtrennung behebbar sind -- daher automatisierte Pruefung statt
Verlass auf Review-Disziplin.

Exit-Code 0 = alle Regeln eingehalten, 1 = Verstoss gefunden (Details auf
stdout). Wird von .githooks/pre-push und der CI (.github/workflows/ci.yml)
aufgerufen.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "wjg_camera"
COORDINATOR_FILE = COMPONENT_DIR / "coordinator.py"

PTZ_METHOD_PREFIX = "async_ptz_"


def _fail(messages: list[str], msg: str) -> None:
    messages.append(f"  - {msg}")


def check_xm_soap_filename(messages: list[str]) -> None:
    """Regel 2: xm_soap.py darf nicht in xm-soap.py umbenannt werden
    (Python kann Module mit Bindestrich nicht importieren)."""
    if not (COMPONENT_DIR / "xm_soap.py").is_file():
        _fail(messages, "custom_components/wjg_camera/xm_soap.py fehlt.")
    hyphenated = COMPONENT_DIR / "xm-soap.py"
    if hyphenated.is_file():
        _fail(
            messages,
            f"{hyphenated} existiert -- xm_soap.py darf NICHT in xm-soap.py "
            "umbenannt werden (Python kann Bindestrich-Module nicht importieren).",
        )


def check_bare_xmsoapclient(messages: list[str]) -> None:
    """Regel 3: In coordinator.py darf _XMSoapClient() nie ohne Argumente
    aufgerufen werden -- das verdrahtet PTZ/Motion wieder fest auf die
    hardcodierten Fallback-Konstanten (z.B. 192.168.178.49) statt auf
    host/credentials der jeweiligen Kamera. Immer 'async with self._soap()
    as soap:' verwenden."""
    if not COORDINATOR_FILE.is_file():
        _fail(messages, f"{COORDINATOR_FILE} nicht gefunden.")
        return
    tree = ast.parse(COORDINATOR_FILE.read_text(encoding="utf-8"), filename=str(COORDINATOR_FILE))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_XMSoapClient"
            and not node.args
            and not node.keywords
        ):
            _fail(
                messages,
                f"coordinator.py:{node.lineno}: _XMSoapClient() ohne Argumente "
                "aufgerufen -- nie wieder ohne host/credentials, sonst PTZ/Motion "
                "gehen bei Multi-Device wieder an die falsche Kamera. Stattdessen "
                "'async with self._soap() as soap:' verwenden.",
            )


def check_ptz_methods_use_soap_helper(messages: list[str]) -> None:
    """Regel 1: async_ptz_* (und async_ptz_stop/home/preset) duerfen NICHT auf
    self._onvif_soap_for()/self._session zurueckfallen -- das loeste historisch
    den IP-Lockout der XM-Firmware aus (persistente Session -> HTTP 400 nach
    mehreren Versuchen). Jede PTZ-Methode muss stattdessen ueber
    'async with self._soap() as soap:' eine frische Session je Befehl nutzen."""
    if not COORDINATOR_FILE.is_file():
        return
    source = COORDINATOR_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(COORDINATOR_FILE))
    lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not node.name.startswith(PTZ_METHOD_PREFIX):
            continue
        end_line = getattr(node, "end_lineno", node.lineno)
        body_source = "\n".join(lines[node.lineno - 1 : end_line])

        if "self._soap(" not in body_source:
            _fail(
                messages,
                f"coordinator.py: {node.name}() (Zeile {node.lineno}) nutzt nicht "
                "'self._soap(' -- jede PTZ-Methode muss ueber "
                "'async with self._soap() as soap:' eine frische Session je "
                "Befehl aufbauen (siehe CLAUDE.md: IP-Lockout-Historie).",
            )
        if "_onvif_soap_for(" in body_source and "ONVIF_SERVICE_PTZ" in body_source:
            _fail(
                messages,
                f"coordinator.py: {node.name}() (Zeile {node.lineno}) ruft "
                "_onvif_soap_for(ONVIF_SERVICE_PTZ, ...) auf -- PTZ darf NIE ueber "
                "die persistente Coordinator-Session (self._session) laufen, das "
                "loest den IP-Lockout der Kamera-Firmware aus.",
            )


def main() -> int:
    messages: list[str] = []
    check_xm_soap_filename(messages)
    check_bare_xmsoapclient(messages)
    check_ptz_methods_use_soap_helper(messages)

    if messages:
        print("Architektur-Regel-Verstoss (siehe custom_components/wjg_camera/CLAUDE.md,")
        print('Abschnitt "Was NICHT geaendert werden darf"):\n')
        print("\n".join(messages))
        return 1

    print("Architektur-Regeln OK (xm_soap.py-Name, self._soap()-Pattern, PTZ-Session).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
