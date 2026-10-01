"""One memory budget, shared by everything that holds a model resident.

Ethel keeps two kinds of model in memory: voices (`speech.py`) and speech
recognition (`listen.py`). Each pool was originally capped on its own - one
voice, one recogniser on a constrained machine - which looks reasonable until
you add the numbers up. A Piper voice is 60 MB, an MMS voice about 145 MB, and a
Zambezi Voice recogniser is 1.2 GB. "One of each" on a 3.9 GB machine that is
also running a language model means swapping, and swapping on this hardware
means a request that never returns.

That is not theoretical: it is what happened the first time speech input was
demonstrated in a browser. Free memory hit 60 MB and the connection died.

So residency is arbitrated in one place instead of two. Before any pool loads a
model it asks here for room, and this module evicts the least recently used
resident models - **across both pools** - until the new one fits. A voice gets
dropped to make space for a recogniser and vice versa, which on a small machine
is the honest behaviour: you cannot have both, and pretending otherwise produces
a hang rather than a slower answer.

Two deliberate choices:

* **A model larger than the whole budget is still allowed to load**, after
  everything else has been evicted. Refusing would mean a 4 GB machine could
  never use a 1.2 GB recogniser at all, when in fact it can - just not alongside
  anything else. `status()` reports it as over budget so the situation is
  visible rather than mysterious.
* **The budget ignores the language model server.** Ollama manages its own
  memory in another process and Ethel cannot see or control it, so the budget is
  set low enough to leave room for it rather than pretending to account for it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import hardware

# How much Ethel will hold in her own processes, per tier. Deliberately modest
# on constrained hardware: Windows wants ~2 GB and Ollama another ~1.3 GB, so
# anything above roughly a gigabyte here starts paging.
_BUDGET_MB = {"constrained": 1000, "standard": 3000, "roomy": 8000}


class _Entry:
    __slots__ = ("kind", "key", "mb", "evict", "last_used")

    def __init__(self, kind: str, key: str, mb: float, evict: Callable[[], None]) -> None:
        self.kind = kind
        self.key = key
        self.mb = mb
        self.evict = evict
        self.last_used = time.time()


_entries: dict[tuple[str, str], _Entry] = {}
_lock = threading.RLock()
_evictions: list[dict[str, Any]] = []


def budget_mb() -> int:
    return _BUDGET_MB[hardware.profile()["tier"]]


def resident_mb() -> float:
    with _lock:
        return round(sum(e.mb for e in _entries.values()), 1)


def make_room(mb: float, for_kind: str, for_key: str) -> list[str]:
    """Evict least-recently-used models until `mb` fits. Returns what went.

    Called before a pool spawns a worker. The caller keeps its own lock for its
    own dictionary; eviction callbacks must therefore not re-enter that lock,
    which is why they are plain `kill` functions rather than pool methods.
    """
    budget = budget_mb()
    dropped: list[str] = []
    with _lock:
        # Never evict the thing we are making room for, if it is somehow already
        # registered - the caller is about to replace it.
        candidates = [e for e in _entries.values()
                      if not (e.kind == for_kind and e.key == for_key)]
        while candidates and resident_mb() + mb > budget:
            oldest = min(candidates, key=lambda e: e.last_used)
            try:
                oldest.evict()
            except Exception:  # noqa: BLE001 - eviction must never block a load
                pass
            _entries.pop((oldest.kind, oldest.key), None)
            candidates.remove(oldest)
            dropped.append(f"{oldest.kind}:{oldest.key}")
            _evictions.append({
                "evicted": f"{oldest.kind}:{oldest.key}",
                "mb": oldest.mb,
                "for": f"{for_kind}:{for_key}",
                "at": time.time(),
            })
        del _evictions[:-20]
    return dropped


def register(kind: str, key: str, mb: float, evict: Callable[[], None]) -> None:
    with _lock:
        _entries[(kind, key)] = _Entry(kind, key, mb, evict)


def touch(kind: str, key: str) -> None:
    with _lock:
        e = _entries.get((kind, key))
        if e is not None:
            e.last_used = time.time()


def release(kind: str, key: str) -> None:
    with _lock:
        _entries.pop((kind, key), None)


def status() -> dict[str, Any]:
    with _lock:
        held = [{"kind": e.kind, "key": e.key, "mb": e.mb,
                 "idle_seconds": round(time.time() - e.last_used)}
                for e in sorted(_entries.values(), key=lambda x: -x.last_used)]
        used = round(sum(e.mb for e in _entries.values()), 1)
        budget = budget_mb()
        return {
            "budget_mb": budget,
            "resident_mb": used,
            "held": held,
            "over_budget": used > budget,
            "recent_evictions": [
                {**e, "ago_seconds": round(time.time() - e["at"])}
                for e in _evictions[-5:]
            ],
            "note": ("Voices and speech recognition share one budget. Loading a "
                     "1.2 GB recogniser on a small machine evicts the voices, "
                     "and loading a voice again evicts it back - that is the "
                     "trade on this hardware, not a fault."),
        }
