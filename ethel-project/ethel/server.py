"""Local HTTP server and JSON API.

Standard library only, bound to loopback. The student uses a browser, which is
the one rich UI toolkit guaranteed to be on a Windows machine already, but nothing
here leaves the machine - the airlock in `ethel.airlock` enforces that even if a
dependency later tries.
"""

from __future__ import annotations

import http.cookies
import json
import mimetypes
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import PRODUCT_NAME, __version__, airlock, config, hardware, i18n, listen, llm, residency, speech
from .core import catalog as catalog_mod
from .core import grounding, library, pedagogy, placement, socratic, sources
from .core import profile as profile_mod

CFG = config.load()
BACKEND = llm.get_backend(CFG)

_sessions: dict[str, str] = {}
_shelves: dict[str, library.Shelf] = {}
_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def shelf_for(student_id: str, refresh: bool = False,
              language: str = "en") -> library.Shelf:
    """Cached per student, and rebuilt when they change language.

    The index holds the student's language alongside English, so switching
    language is a rebuild rather than a filter.
    """
    language = language or "en"
    with _lock:
        cached = _shelves.get(student_id)
        if refresh or cached is None or cached.language != language:
            _shelves[student_id] = library.Shelf(student_id, language)
        return _shelves[student_id]


# --- request context -------------------------------------------------------

class Ctx:
    def __init__(self, handler: "Handler", body: dict[str, Any],
                 query: dict[str, list[str]]) -> None:
        self.handler = handler
        self.body = body
        self.query = query
        self.set_cookie: str | None = None

    def q(self, name: str, default: str = "") -> str:
        return (self.query.get(name) or [default])[0]

    @property
    def student_id(self) -> str:
        token = self.handler.session_token()
        sid = _sessions.get(token or "")
        if not sid:
            raise ApiError("Not signed in.", 401)
        return sid

    def record(self) -> dict[str, Any]:
        return profile_mod.load(self.student_id)

    def language(self) -> str:
        return str((self.record().get("preferences") or {}).get("language") or "en")

    def shelf(self, refresh: bool = False) -> library.Shelf:
        return shelf_for(self.student_id, refresh, self.language())


# --- endpoints -------------------------------------------------------------

def bootstrap(ctx: Ctx) -> dict[str, Any]:
    token = ctx.handler.session_token()
    return {
        "product": PRODUCT_NAME,
        "version": __version__,
        "offline": {
            "airlock": airlock.is_engaged(),
            "blocked_attempts": len(airlock.violations()),
        },
        "languages": i18n.LANGUAGES,
        "students": profile_mod.list_students(),
        "signed_in": _sessions.get(token or ""),
        "catalog_retrieved_on": catalog_mod.load().retrieved_on,
    }


def strings(ctx: Ctx) -> dict[str, Any]:
    return i18n.bundle(ctx.q("lang", "en"))


def login(ctx: Ctx) -> dict[str, Any]:
    mode = ctx.body.get("mode", "existing")
    pin = str(ctx.body.get("pin") or "")
    if mode == "new":
        sid = profile_mod.normalise_id(str(ctx.body.get("id") or ""))
        name = str(ctx.body.get("name") or "").strip()
        if not name:
            raise ApiError("Please give your name.")
        if len(pin) < 4:
            raise ApiError("Your PIN needs at least four digits.")
        try:
            record = profile_mod.create(sid, name, pin)
        except ValueError:
            # Picking an ID somebody already used is an ordinary mistake, not a
            # server fault. It was surfacing as a 500 with the exception class
            # in the message, which tells the student nothing useful.
            raise ApiError(
                "Somebody on this machine is already using that student ID. "
                "Use \"Continue\" with your PIN if it is yours, or pick another.",
                409) from None
    else:
        sid = profile_mod.normalise_id(str(ctx.body.get("id") or ""))
        if not profile_mod.exists(sid):
            raise ApiError("No student with that ID on this machine.", 404)
        record = profile_mod.load(sid)
        if not profile_mod.check_pin(record, pin):
            raise ApiError("That PIN doesn't match.", 403)

    token = secrets.token_urlsafe(24)
    _sessions[token] = record["id"]
    ctx.set_cookie = token
    return {"student": _me(record)}


def logout(ctx: Ctx) -> dict[str, Any]:
    token = ctx.handler.session_token()
    _sessions.pop(token or "", None)
    ctx.set_cookie = ""
    return {"ok": True}


def _me(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record.get("name"),
        "role": record.get("role", "student"),
        "enrolment": record.get("enrolment"),
        "preferences": record.get("preferences", {}),
        "placement_done": bool(record.get("placement", {}).get("completed")),
        "placement_summary": record.get("placement", {}).get("summary"),
        "installed_packs": [p.id for p in library.installed_packs(record["id"])],
        "active_lesson": record.get("active_lesson"),
    }


def me(ctx: Ctx) -> dict[str, Any]:
    return {"student": _me(ctx.record())}


def catalog_view(ctx: Ctx) -> dict[str, Any]:
    cat = catalog_mod.load()
    iid = ctx.q("institution")
    if not iid:
        return {"institutions": cat.institutions, "retrieved_on": cat.retrieved_on}
    fid = ctx.q("field")
    if not fid:
        return {"fields": cat.fields_for(iid)}
    progs = cat.programmes_for(iid, fid)
    return {"programmes": [
        {
            "id": p["id"], "name": p["name"], "coverage": p["coverage"],
            "duration_years": p.get("duration_years"),
            "source_url": p.get("source_url"),
            "notes": p.get("notes", []),
            "tracks": p.get("tracks", []),
            "majors": p.get("majors", []),
            "entry_requirements": p.get("entry_requirements"),
            "years_published": sorted(
                int(y) for y, v in (p.get("years") or {}).items() if v
            ),
        }
        for p in progs
    ]}


def enrol(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    cat = catalog_mod.load()
    pid = str(ctx.body.get("programme") or "")
    prog = cat.programme(pid)
    if not prog:
        raise ApiError("I don't have that programme in the catalogue.", 404)
    try:
        year = int(ctx.body.get("year"))
    except (TypeError, ValueError):
        raise ApiError("Which year of study are you in?") from None
    if prog.get("duration_years") and not (1 <= year <= prog["duration_years"]):
        raise ApiError(
            f"{prog['name']} runs {prog['duration_years']} years, so year {year} isn't valid."
        )
    track = ctx.body.get("track") or None
    profile_mod.enrol(record, prog["institution"], prog["field"], pid, year, track)
    profile_mod.save(record)
    plan = library.plan_for(pid, year, track)
    return {"student": _me(record), "install_plan": plan}


def install_plan(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    enr = record.get("enrolment")
    if not enr:
        raise ApiError("You haven't chosen a programme yet.")
    return library.plan_for(enr["programme"], enr["year"], enr.get("track"))


def install(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    pack_ids = ctx.body.get("pack_ids") or []
    if not isinstance(pack_ids, list):
        raise ApiError("pack_ids must be a list.")
    result = library.install(record["id"], [str(p) for p in pack_ids])
    profile_mod.note(record, "install", f"{len(result['installed'])} packs")
    profile_mod.save(record)
    shelf = ctx.shelf(refresh=True)
    result["passages_indexed"] = len(shelf.index)
    result["lessons_available"] = len(shelf.all_lessons())
    return result


def library_view(ctx: Ctx) -> dict[str, Any]:
    entries = library.scan_library()
    return {
        "library_path": str(config.LIBRARY_DIR),
        "packs": [
            {
                "id": e.pack.id, "code": e.pack.code, "title": e.pack.title,
                "programme": e.pack.programme, "year": e.pack.year,
                "lessons": len(e.pack.lessons), "integrity": e.integrity,
                "source": e.pack.data.get("source", {}),
                "languages": e.pack.data.get("languages", ["en"]),
            }
            for e in entries
        ],
    }


# --- placement -------------------------------------------------------------

def placement_start(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    pool = ctx.shelf().diagnostic_pool()
    if not pool:
        raise ApiError(
            "None of your installed course packs carry a diagnostic test, so I can't "
            "place you. I'll start you at the beginning instead.", 409,
        )
    placement.start(record, pool, CFG["pedagogy"]["placement_items"])
    item = placement.next_item(record, pool)
    profile_mod.save(record)
    return {"item": item}


def placement_next(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    item = placement.next_item(record, ctx.shelf().diagnostic_pool())
    profile_mod.save(record)
    return {"item": item}


def placement_answer(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    pool = ctx.shelf().diagnostic_pool()
    item_id = str(ctx.body.get("item_id") or "")
    try:
        result = placement.answer(record, pool, item_id, ctx.body.get("response"))
    except KeyError:
        raise ApiError("That question isn't in this test.", 404) from None
    nxt = None if result["done"] else placement.next_item(record, pool)
    profile_mod.save(record)
    return {"result": result, "item": nxt}


def placement_prefs(ctx: Ctx) -> dict[str, Any]:
    langs = ["en"]
    for pack in ctx.shelf().packs:
        langs += pack.data.get("languages", [])
    return {"questions": placement.preference_questions(sorted(set(langs)))}


def placement_finish(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    prefs = ctx.body.get("preferences") or {}
    summary = placement.finish(record, {k: str(v) for k, v in prefs.items()},
                               ctx.shelf().diagnostic_pool())
    profile_mod.save(record)
    return {"summary": summary, "student": _me(record)}


# --- study -----------------------------------------------------------------

def study(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    shelf = ctx.shelf()
    return {
        "lessons": pedagogy.study_plan(record, shelf, CFG),
        "due_reviews": len(profile_mod.due_reviews(record)),
        "courses": [
            {"pack_id": p.id, "code": p.code, "title": p.title,
             "lessons": len(p.lessons),
             "reviewed_by": p.data.get("source", {}).get("reviewed_by"),
             # Provenance travels with the course. A student should be able to
             # see where their material came from and what it has not been
             # checked against.
             "syllabus_reference": p.data.get("source", {}).get("syllabus_reference"),
             "jurisdiction_note": p.data.get("source", {}).get("jurisdiction_note"),
             "field_note": p.data.get("source", {}).get("field_note")}
            for p in shelf.packs
        ],
    }


def lesson_start(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    shelf = ctx.shelf()
    try:
        pedagogy.start(record, shelf, str(ctx.body.get("pack_id")),
                       str(ctx.body.get("lesson_id")), CFG,
                       restart=bool(ctx.body.get("restart")))
    except KeyError:
        raise ApiError("That lesson isn't installed on this machine.", 404) from None
    step = pedagogy.render(record, shelf, BACKEND, CFG)
    profile_mod.save(record)
    return {"step": step}


def lesson_current(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    return {"step": pedagogy.render(record, ctx.shelf(), BACKEND, CFG)}


def lesson_advance(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    step = pedagogy.advance(record, ctx.shelf(), BACKEND, CFG,
                            ctx.body.get("response"))
    profile_mod.save(record)
    return {"step": step}


def ask(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    question = str(ctx.body.get("question") or "").strip()
    if not question:
        raise ApiError("Ask me something.")
    shelf = ctx.shelf()
    # Packs or uploaded reference material - either gives Ethel something true
    # to answer from. Gating on packs alone left a student whose course has only
    # uploaded notes unable to ask anything at all.
    if not shelf.packs and not shelf.sources:
        raise ApiError(
            "You have no course packs installed and no reference material has "
            "been uploaded for your programme yet.", 409)
    result = grounding.answer_question(question, shelf, BACKEND, CFG)
    profile_mod.note(record, "ask", f"{result['mode']}: {question[:80]}")
    profile_mod.save(record)
    return result


def doctor(ctx: Ctx) -> dict[str, Any]:
    ok_backend = BACKEND.available()
    model_present = ok_backend and getattr(BACKEND, "model_present", lambda: False)()
    lib = library.scan_library()
    return {
        "product": PRODUCT_NAME,
        "version": __version__,
        "paths": {
            "root": str(config.ROOT),
            "library": str(config.LIBRARY_DIR),
            "students": str(config.STUDENTS_DIR),
            "installed": str(config.INSTALLED_DIR),
        },
        "offline": {
            "airlock_engaged": airlock.is_engaged(),
            "blocked_attempts": airlock.violations(),
            "explain": "Any entry above is code that tried to reach the internet and was stopped.",
        },
        "model": {
            "backend": BACKEND.name,
            "configured_model": getattr(BACKEND, "model", None),
            "server_reachable": ok_backend,
            "model_installed": model_present,
            "installed_models": getattr(BACKEND, "installed_models", lambda: [])(),
            "mode": ("grounded generation" if model_present
                     else "extractive - the tutor quotes the course pack rather than rephrasing it"),
        },
        "hardware": hardware.describe(CFG["llm"], CFG["retrieval"]),
        "speech": speech.status(CFG),
        "listening": listen.status(CFG),
        "residency": residency.status(),
        "library": {
            "packs": len(lib),
            "unsigned": sum(1 for e in lib if e.integrity == "unsigned"),
            "mismatched": sum(1 for e in lib if e.integrity == "mismatch"),
        },
        "catalog": {
            "retrieved_on": catalog_mod.load().retrieved_on,
            "institutions": len(catalog_mod.load().institutions),
            "programmes": len(catalog_mod.load().programmes),
        },
        "locales": {
            code["code"]: i18n.bundle(code["code"])["coverage"] for code in i18n.LANGUAGES
        },
    }


def socratic_start(ctx: Ctx) -> dict[str, Any]:
    """Begin a line-by-line session over one segment of a lesson."""
    record = ctx.record()
    shelf = ctx.shelf()
    pack_id = str(ctx.body.get("pack_id") or "")
    lesson_id = str(ctx.body.get("lesson_id") or "")
    pack, lesson = shelf.lesson(pack_id, lesson_id)
    if lesson is None:
        raise ApiError("That lesson isn't installed on this machine.", 404)
    session = socratic.build(lesson, ctx.body.get("segment_id") or None)
    if not session["steps"]:
        raise ApiError(
            "There isn't enough explanatory text in that segment to work "
            "through line by line.", 409)
    session["pack_id"] = pack_id
    session["course"] = pack.code or pack.title
    record["socratic"] = session
    profile_mod.save(record)
    return {"step": socratic.step_view(session),
            "lesson_title": session["lesson_title"],
            "course": session["course"],
            "total": len(session["steps"])}


def socratic_current(ctx: Ctx) -> dict[str, Any]:
    session = ctx.record().get("socratic")
    if not session:
        raise ApiError("No Socratic session in progress.", 409)
    return {"step": socratic.step_view(session),
            "lesson_title": session.get("lesson_title"),
            "course": session.get("course"),
            "total": len(session["steps"])}


def socratic_answer(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    session = record.get("socratic")
    if not session:
        raise ApiError("No Socratic session in progress.", 409)
    out = socratic.answer(session, str(ctx.body.get("text") or ""))
    if out.get("done") or out.get("next") is None:
        out["summary"] = socratic.summary(session)
        out["done"] = True
    profile_mod.save(record)
    return out


def _require_admin(ctx: Ctx) -> dict[str, Any]:
    record = ctx.record()
    if not profile_mod.is_admin(record):
        raise ApiError(
            "That is an administrator's area. Ask whoever set up this machine "
            "to grant your account the admin role.", 403)
    return record


def admin_sources(ctx: Ctx) -> dict[str, Any]:
    _require_admin(ctx)
    cat = catalog_mod.load()
    return {
        "sources": sources.list_sources(),
        "summary": sources.summary(),
        "programmes": [{"id": p["id"], "name": p["name"],
                        "institution": p["institution"]}
                       for p in cat.programmes],
        "explain": (
            "Uploaded documents are reference material. A student can ask "
            "questions and get answers grounded in them, cited to the document "
            "and heading. They never become lessons - Ethel does not write "
            "teaching material, and a pack still has to be authored by a person."
        ),
    }


def admin_source_create(ctx: Ctx) -> dict[str, Any]:
    record = _require_admin(ctx)
    title = str(ctx.body.get("title") or "").strip()
    if not title:
        raise ApiError("Give the source a title, so a citation can name it.")
    programme = str(ctx.body.get("programme") or "")
    if programme and not catalog_mod.load().programme(programme):
        raise ApiError(f"No programme called {programme!r} in the catalogue.", 400)
    year = ctx.body.get("year")
    rec = sources.create(
        title=title,
        programme=programme,
        uploaded_by=record["id"],
        course_code=str(ctx.body.get("course_code") or "").strip(),
        year=int(year) if year else None,
        note=str(ctx.body.get("note") or "").strip(),
    )
    return {"source": rec}


def admin_source_delete(ctx: Ctx) -> dict[str, Any]:
    _require_admin(ctx)
    sid = str(ctx.body.get("id") or "")
    if ctx.body.get("file"):
        sources.remove_file(sid, str(ctx.body["file"]))
        _forget_shelves()
        return {"source": sources.load(sid)}
    if not sources.delete(sid):
        raise ApiError(f"No source called {sid!r}.", 404)
    _forget_shelves()
    return {"deleted": sid}


def _forget_shelves() -> None:
    """Uploads change what every student can be asked about; rebuild indexes."""
    with _lock:
        _shelves.clear()


def voices(ctx: Ctx) -> dict[str, Any]:
    """What this machine can actually speak with, and what is selected."""
    record = ctx.record()
    prefs = record.get("preferences", {})
    lang = str(prefs.get("language") or "en")
    gender = str(prefs.get("voice") or "female")
    cat = speech.catalogue(CFG)
    chosen = speech.resolve_voice(CFG, prefs.get("voice_id"), lang, gender)
    return {
        "available": cat,
        "paces": [{"id": k, **v} for k, v in speech.PACES.items()],
        "engine_ready": bool(cat) and speech.status(CFG)["engine_found"],
        "mode": speech.status(CFG)["mode"],
        "current": {
            "voice_id": chosen["id"] if chosen else None,
            "pace": prefs.get("voice_pace") or speech.DEFAULT_PACE,
            "enabled": gender != "off",
            "language": lang,
        },
        # What each language can and cannot do, so the interface can be honest
        # rather than offering a Lozi voice that does not exist.
        "languages": [
            {
                "code": code,
                "name": next((x["name"] for x in i18n.LANGUAGES if x["code"] == code), code),
                # bundle() reports a 0-1 ratio; the API speaks in percent so
                # the interface never has to guess which it was handed.
                "interface_percent": round(100 * i18n.bundle(code)["coverage"]),
                "has_voice": any(v["language_family"] == code for v in cat),
                **speech.LANGUAGE_TTS_REALITY.get(code, {}),
            }
            for code in [x["code"] for x in i18n.LANGUAGES]
        ],
        # How much of the student's own course material exists in their language.
        "content": ctx.shelf().translation_report(),
        # Languages this machine can *hear*. Speech in and speech out are
        # separate capabilities with different models, and for Lozi they differ
        # completely: recognition exists, synthesis does not.
        "listening": [m["language"] for m in listen.installed(CFG)],
    }


def set_voice(ctx: Ctx) -> dict[str, Any]:
    """Change voice or pace. Both are optional; unknown values are refused."""
    record = ctx.record()
    prefs = record.setdefault("preferences", {})
    body = ctx.body

    if "voice_id" in body:
        vid = body.get("voice_id") or None
        if vid is not None:
            if not any(v["id"] == vid for v in speech.catalogue(CFG)):
                raise ApiError(f"There is no voice called {vid!r} on this machine.", 400)
            prefs["voice_id"] = vid
            # Keep the gender answer consistent with an explicit pick, so the
            # fallback path agrees with the choice the student just made.
            picked = next(v for v in speech.catalogue(CFG) if v["id"] == vid)
            if picked["gender"] in ("male", "female"):
                prefs["voice"] = picked["gender"]
        else:
            prefs.pop("voice_id", None)

    if "pace" in body:
        pace = str(body.get("pace") or speech.DEFAULT_PACE)
        if pace not in speech.PACES:
            raise ApiError(f"Unknown pace {pace!r}.", 400)
        prefs["voice_pace"] = pace

    if "enabled" in body:
        prefs["voice"] = prefs.get("voice", "female") if body.get("enabled") else "off"

    if "language" in body:
        code = str(body.get("language") or "en")
        if code not in [x["code"] for x in i18n.LANGUAGES]:
            raise ApiError(f"Ethel has no interface for {code!r}.", 400)
        prefs["language"] = code
        # A saved voice from another language would now be wrong; let it be
        # re-resolved from the language rather than reading Bemba in English.
        if prefs.get("voice_id"):
            picked = speech.resolve_voice(CFG, prefs["voice_id"], code,
                                          prefs.get("voice", "female"))
            if not picked or picked["id"] != prefs["voice_id"]:
                prefs.pop("voice_id", None)

    profile_mod.save(record)
    return voices(ctx)


ROUTES: dict[tuple[str, str], Callable[[Ctx], dict[str, Any]]] = {
    ("GET", "/api/bootstrap"): bootstrap,
    ("GET", "/api/strings"): strings,
    ("POST", "/api/login"): login,
    ("POST", "/api/logout"): logout,
    ("GET", "/api/me"): me,
    ("GET", "/api/catalog"): catalog_view,
    ("POST", "/api/enrol"): enrol,
    ("GET", "/api/install-plan"): install_plan,
    ("POST", "/api/install"): install,
    ("GET", "/api/library"): library_view,
    ("POST", "/api/placement/start"): placement_start,
    ("GET", "/api/placement/next"): placement_next,
    ("POST", "/api/placement/answer"): placement_answer,
    ("GET", "/api/placement/preferences"): placement_prefs,
    ("POST", "/api/placement/finish"): placement_finish,
    ("GET", "/api/study"): study,
    ("POST", "/api/lesson/start"): lesson_start,
    ("GET", "/api/lesson/current"): lesson_current,
    ("POST", "/api/lesson/advance"): lesson_advance,
    ("POST", "/api/ask"): ask,
    ("POST", "/api/socratic/start"): socratic_start,
    ("GET", "/api/socratic/current"): socratic_current,
    ("POST", "/api/socratic/answer"): socratic_answer,
    ("GET", "/api/admin/sources"): admin_sources,
    ("POST", "/api/admin/sources/create"): admin_source_create,
    ("POST", "/api/admin/sources/delete"): admin_source_delete,
    ("GET", "/api/voices"): voices,
    ("POST", "/api/voice"): set_voice,
    ("GET", "/api/doctor"): doctor,
}


class Handler(BaseHTTPRequestHandler):
    server_version = f"Ethel/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        return

    def session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw)
        except http.cookies.CookieError:
            return None
        morsel = jar.get("ethel_session")
        return morsel.value if morsel else None

    # -- helpers
    def _send(self, status: int, body: bytes, content_type: str,
              cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob: data:; connect-src 'self'",
        )
        if cookie is not None:
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"ethel_session={cookie}; Path=/; HttpOnly; SameSite=Strict",
                )
            else:
                self.send_header(
                    "Set-Cookie",
                    "ethel_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
                )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any],
              cookie: str | None = None) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8", cookie)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (config.STATIC_DIR / rel).resolve()
        try:
            target.relative_to(config.STATIC_DIR.resolve())
        except ValueError:
            self._json(403, {"error": "Forbidden"})
            return
        if not target.exists() or not target.is_file():
            self._json(404, {"error": "Not found"})
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- verbs
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._dispatch("GET", parsed.path, parse_qs(parsed.query), {})
        elif parsed.path in ("/", "/index.html"):
            self._static("index.html")
        else:
            self._static(parsed.path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        # /api/listen carries raw audio, not JSON, so it has to be handled
        # before the body is parsed - otherwise every upload is rejected as
        # malformed JSON, which is what happened the first time.
        if parsed.path == "/api/listen":
            self._listen(length)
            return
        # Uploads carry file bytes, not JSON, so they are handled before the
        # body is parsed - same reason as /api/listen.
        if parsed.path == "/api/admin/upload":
            self._upload(length)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json(400, {"error": "Body must be JSON."})
            return
        if parsed.path == "/api/speak":
            self._speak(body)
            return
        self._dispatch("POST", parsed.path, parse_qs(parsed.query), body)

    def _listen(self, length: int) -> None:
        """Raw WAV in, transcript out. The student is speaking, not typing."""
        ctx = Ctx(self, {}, {})
        try:
            record = ctx.record()
        except ApiError as exc:
            self._drain(length)
            self._json(exc.status, {"error": exc.message})
            return
        if length <= 0 or length > 20 * 1024 * 1024:
            self._drain(length)
            self._json(400, {"error": "Send between 1 byte and 20 MB of WAV audio."})
            return
        wav = self.rfile.read(length)
        lang = str(self.headers.get("X-Ethel-Language")
                   or (record.get("preferences") or {}).get("language") or "en")
        try:
            out = listen.transcribe(wav, CFG, lang)
        except listen.ListeningUnavailable as exc:
            self._json(503, {"error": str(exc)})
            return
        self._json(200, out)

    def _upload(self, length: int) -> None:
        """One file into one source. Filename and target ride in headers.

        Multipart parsing is a surprising amount of code and a classic source of
        path-traversal bugs, so the browser sends one file per request with the
        name in a header instead. `sources.safe_name` is the boundary that stops
        a crafted filename from writing outside the source's own folder.
        """
        ctx = Ctx(self, {}, {})
        try:
            record = ctx.record()
            if not profile_mod.is_admin(record):
                raise ApiError("Administrators only.", 403)
        except ApiError as exc:
            self._drain(length)
            self._json(exc.status, {"error": exc.message})
            return

        source_id = self.headers.get("X-Ethel-Source") or ""
        filename = self.headers.get("X-Ethel-Filename") or ""
        if not source_id or not filename:
            self._drain(length)
            self._json(400, {"error": "Missing X-Ethel-Source or X-Ethel-Filename."})
            return
        if length <= 0 or length > sources.MAX_FILE_BYTES:
            self._drain(length)
            self._json(400, {
                "error": f"Send between 1 byte and "
                         f"{sources.MAX_FILE_BYTES // 1024 // 1024} MB."})
            return

        data = self.rfile.read(length)
        try:
            entry = sources.add_file(source_id, filename, data)
        except ValueError as exc:
            # Unsupported format, too large, unknown source - all of these are
            # things the person uploading can act on, so say which.
            self._json(400, {"error": str(exc)})
            return
        _forget_shelves()
        self._json(200, {"file": entry, "source": sources.load(source_id)})

    def _drain(self, length: int) -> None:
        """Read and discard an unwanted body so the connection stays usable."""
        left = length
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                return
            left -= len(chunk)

    def _speak(self, body: dict[str, Any]) -> None:
        ctx = Ctx(self, body, {})
        try:
            record = ctx.record()
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        prefs = record.get("preferences", {})
        gender = str(body.get("voice") or prefs.get("voice") or "female")
        if gender == "off":
            self._json(409, {"error": "Voice is switched off in your preferences."})
            return
        lang = str(body.get("language") or prefs.get("language") or "en")
        # An explicit choice in the request wins, then the student's saved one,
        # then whatever matches their language and gender answer.
        voice_id = body.get("voice_id") or prefs.get("voice_id") or None
        pace = str(body.get("pace") or prefs.get("voice_pace") or speech.DEFAULT_PACE)

        # A human recording always beats synthesis. For Lozi it is the only
        # thing that exists, and even in English a teacher reading their own
        # lesson is better than a model reading it.
        pack_id = body.get("pack_id")
        lesson_id = body.get("lesson_id")
        audio_key = body.get("audio_key")
        if pack_id and lesson_id and audio_key:
            try:
                pack = ctx.shelf().pack(str(pack_id))
            except ApiError:
                pack = None
            if pack:
                recorded = pack.audio_path(str(lesson_id), lang, str(audio_key))
                if recorded:
                    mime = ("audio/wav" if recorded.suffix.lower() == ".wav"
                            else "audio/ogg" if recorded.suffix.lower() in (".ogg", ".opus")
                            else "audio/mpeg")
                    self._send(200, recorded.read_bytes(), mime)
                    return

        try:
            wav = speech.speak(str(body.get("text") or ""), CFG, lang, gender,
                               voice_id=voice_id, pace=pace)
        except speech.VoiceUnavailable as exc:
            self._json(503, {"error": str(exc)})
            return
        self._send(200, wav, "audio/wav")

    def _dispatch(self, verb: str, path: str, query: dict[str, list[str]],
                  body: dict[str, Any]) -> None:
        fn = ROUTES.get((verb, path.rstrip("/") or "/"))
        if fn is None:
            self._json(404, {"error": f"No route for {verb} {path}"})
            return
        ctx = Ctx(self, body, query)
        try:
            payload = fn(ctx)
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        else:
            self._json(200, payload, cookie=ctx.set_cookie)


def serve(host: str | None = None, port: int | None = None) -> None:
    config.ensure_dirs()
    host = host or CFG["host"]
    port = int(port or CFG["port"])
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"{PRODUCT_NAME} {__version__}")
    print(f"  offline airlock : {'engaged' if airlock.is_engaged() else 'NOT ENGAGED'}")
    print(f"  content library : {config.LIBRARY_DIR}")
    print(f"  model backend   : {BACKEND.name}"
          f"{' (reachable)' if BACKEND.available() else ' (not running - extractive mode)'}")
    sp = speech.status(CFG)
    voice_line = sp["mode"]
    if sp["mode"] == "resident":
        voice_line += f", up to {sp['max_resident']} voice(s) held in memory"
    print(f"  voice           : {voice_line}")
    print(f"\n  Open  http://{host}:{port}/  in a browser.  Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        # Resident voice processes are children of this one; leaving them alive
        # would hold ~60 MB each for nothing.
        speech.shutdown()
        listen.shutdown()
        httpd.server_close()
