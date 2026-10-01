"""Look at the machine, then decide how hard to push it.

Ethel is meant to run on whatever hardware is actually in the room, which in
practice ranges from a 2-core laptop with 4 GB to a reasonable desktop. Those
need different settings, and asking a student - or whoever is setting up a
classroom - to hand-tune a context window is not realistic.

So this module measures RAM and cores at start-up and picks the model settings
to match. The chosen numbers are shown on the System check page rather than
hidden, and anything written explicitly into `data/config.json` overrides them.

The judgement encoded here: on a constrained machine, latency ruins teaching
long before quality does. A student waiting forty seconds for a rephrased
paragraph stops asking. So on weak hardware the context window and the answer
length both come down, and the timeout goes *up* - because a slow answer that
arrives still beats a timeout that drops back to quoting.

Standard library only: `ctypes` on Windows, `sysconf` elsewhere.
"""

from __future__ import annotations

import ctypes
import os
import platform
from typing import Any


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_gb() -> tuple[float, float]:
    """(total, available) in GiB. (0, 0) if it cannot be determined."""
    if platform.system() == "Windows":
        try:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                gib = 1024 ** 3
                return (round(status.ullTotalPhys / gib, 1),
                        round(status.ullAvailPhys / gib, 1))
        except (AttributeError, OSError):
            pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
        try:
            avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 1024 ** 3
        except (ValueError, OSError):
            avail = 0.0
        return round(total, 1), round(avail, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0, 0.0


def cpu_count() -> int:
    return os.cpu_count() or 2


def profile() -> dict[str, Any]:
    total, avail = memory_gb()
    cores = cpu_count()

    # Unknown RAM is treated as constrained. Guessing generously on a machine we
    # cannot measure is the failure that produces a hung tutor.
    if total == 0.0:
        tier = "constrained"
    elif total < 6 or cores <= 2:
        tier = "constrained"
    elif total < 12 or cores <= 4:
        tier = "standard"
    else:
        tier = "roomy"

    return {
        "tier": tier,
        "ram_total_gb": total,
        "ram_available_gb": avail,
        "cores": cores,
        "platform": f"{platform.system()} {platform.machine()}",
        "processor": platform.processor() or "unknown",
    }


# Per tier: context window, answer cap, how long to wait, how many threads to
# leave for everything else.
_TIERS: dict[str, dict[str, int]] = {
    "constrained": {"num_ctx": 2048, "max_tokens": 320, "timeout_s": 240, "reserve": 1, "top_k": 3},
    "standard":    {"num_ctx": 4096, "max_tokens": 500, "timeout_s": 150, "reserve": 1, "top_k": 4},
    "roomy":       {"num_ctx": 8192, "max_tokens": 700, "timeout_s": 120, "reserve": 2, "top_k": 5},
}

# What a machine of each tier can actually hold. Used only to warn - Ethel never
# refuses to run a model the operator has chosen, she just says what she expects.
_MODEL_BUDGET_GB = {"constrained": 1.6, "standard": 4.5, "roomy": 12.0}


def suggest_llm() -> dict[str, Any]:
    """Model settings for this machine, merged under any explicit config."""
    p = profile()
    t = _TIERS[p["tier"]]
    threads = max(1, min(8, p["cores"] - t["reserve"]))
    return {
        "num_ctx": t["num_ctx"],
        "num_thread": threads,
        "max_tokens": t["max_tokens"],
        "timeout_s": t["timeout_s"],
    }


def suggest_retrieval() -> dict[str, Any]:
    """How much material to hand the model.

    Retrieval quality is not the constraint here - BM25 ranks the same however
    slow the machine is. What changes is how much context the model can chew
    through before a student gives up waiting, so a weak machine gets fewer,
    better passages rather than more.
    """
    return {"top_k": _TIERS[profile()["tier"]]["top_k"]}


def describe(cfg_llm: dict[str, Any],
             cfg_retrieval: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the System check page needs to explain its own choices."""
    p = profile()
    budget = _MODEL_BUDGET_GB[p["tier"]]
    notes = []
    if p["tier"] == "constrained":
        notes.append(
            f"This machine has {p['ram_total_gb']} GB of RAM and {p['cores']} cores, "
            "so Ethel is running in her smallest configuration. Keep the model at or "
            f"under about {budget} GB on disk - a larger one will swap and answers will "
            "take minutes rather than seconds."
        )
        notes.append(
            "Nothing is lost by this. The model only rewords passages already retrieved "
            "from your course packs; it is never the source of a fact, so a small model "
            "and a large one are held to exactly the same four gates."
        )
    elif p["tier"] == "standard":
        notes.append(
            f"{p['ram_total_gb']} GB and {p['cores']} cores. A model up to about "
            f"{budget} GB will run comfortably."
        )
    else:
        notes.append(
            f"{p['ram_total_gb']} GB and {p['cores']} cores - room for a model up to "
            f"about {budget} GB."
        )
    if p["ram_available_gb"] and p["ram_available_gb"] < 1.5:
        notes.append(
            f"Only {p['ram_available_gb']} GB is free right now. Close other programs "
            "before a lesson, or the model will be paged to disk."
        )
    return {
        **p,
        "model_budget_gb": budget,
        "applied": {
            "num_ctx": cfg_llm.get("num_ctx"),
            "num_thread": cfg_llm.get("num_thread"),
            "max_tokens": cfg_llm.get("max_tokens"),
            "timeout_s": cfg_llm.get("timeout_s"),
            "top_k": (cfg_retrieval or {}).get("top_k"),
        },
        "notes": notes,
    }
