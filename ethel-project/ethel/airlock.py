"""Network airlock.

"Completely offline" is a claim that should be enforced by the code, not by a
promise in a README. Once `engage()` runs, every outbound socket connection to
anything other than loopback raises `NetworkBlocked` and is recorded.

Loopback stays open because two legitimate local services live there: the
tutor's own HTTP UI on 127.0.0.1, and (optionally) an Ollama model server on
127.0.0.1:11434. Neither leaves the machine.

The list of blocked attempts is surfaced in the app's Doctor page, so a
deployment can be audited: if the counter is not zero, something in the stack
tried to phone home and you can see exactly what.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}

_lock = threading.Lock()
_violations: list[dict[str, Any]] = []
_engaged = False

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo


class NetworkBlocked(RuntimeError):
    """Raised when code attempts to reach a non-loopback address."""


def _host_of(address: Any) -> str | None:
    if isinstance(address, (tuple, list)) and address:
        return str(address[0])
    return None


def _is_loopback(host: str | None) -> bool:
    if host is None:
        # AF_UNIX and friends never leave the machine.
        return True
    host = host.strip("[]")
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _record(kind: str, host: str | None) -> None:
    with _lock:
        _violations.append({"kind": kind, "host": host})
        del _violations[:-50]


def engage() -> None:
    """Install the airlock. Idempotent."""
    global _engaged
    if _engaged:
        return

    def connect(self, address):  # type: ignore[no-untyped-def]
        host = _host_of(address)
        if not _is_loopback(host):
            _record("connect", host)
            raise NetworkBlocked(
                f"Ethel is offline by design; refused to connect to {host!r}."
            )
        return _orig_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        host = _host_of(address)
        if not _is_loopback(host):
            _record("connect_ex", host)
            return 1
        return _orig_connect_ex(self, address)

    def getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _is_loopback(host if host is None else str(host)):
            _record("dns", str(host))
            raise NetworkBlocked(
                f"Ethel is offline by design; refused to resolve {host!r}."
            )
        return _orig_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    _engaged = True


def is_engaged() -> bool:
    return _engaged


def violations() -> list[dict[str, Any]]:
    with _lock:
        return list(_violations)
