"""Interface language.

Three locales ship: `en` (complete), `bem` (Ichibemba) and `loz` (Silozi).

An honest note about what this can and cannot do. The tutor's *interface* is
translated. Course material is taught in whatever language the pack was written
in, and packs carry optional per-language fields so a translated lesson can sit
alongside the English one. What the tutor will **not** do is let a small local
model translate teaching material on the fly: a 4B model asked to produce Bemba
technical prose invents words, and inventing words is exactly the failure this
whole system is built to avoid. Bemba and Lozi lesson text has to be written or
checked by a person.

Every string in `bem` and `loz` is marked as draft pending native-speaker
review, and any string missing from a locale falls back to English rather than
being machine-translated.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from . import config

LANGUAGES = [
    {"code": "en", "name": "English", "endonym": "English"},
    {"code": "bem", "name": "Bemba", "endonym": "Ichibemba"},
    {"code": "loz", "name": "Lozi", "endonym": "Silozi"},
]


@lru_cache(maxsize=8)
def _locale(code: str) -> dict[str, Any]:
    path = config.LOCALES_DIR / f"{code}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except ValueError:
        return {}


def bundle(code: str) -> dict[str, Any]:
    """Strings for one language, with English filling every gap."""
    en = _locale("en")
    strings = dict(en.get("strings", {}))
    loc = _locale(code) if code != "en" else en
    meta = loc.get("_meta", {"status": "complete"})
    translated = loc.get("strings", {})
    strings.update({k: v for k, v in translated.items() if v})
    return {
        "code": code if loc else "en",
        "meta": meta,
        "coverage": round(len(translated) / max(1, len(en.get("strings", {}))), 2),
        "strings": strings,
    }


def t(code: str, key: str, default: str = "") -> str:
    return bundle(code)["strings"].get(key, default or key)
