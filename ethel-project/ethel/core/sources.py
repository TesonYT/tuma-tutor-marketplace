"""Reference material an administrator uploads, and what Ethel may do with it.

A course pack is written by a person who decided how to teach something. A
*source* is different: a syllabus, a set of lecture notes, a chapter someone
scanned into a Word file. It is raw material, not teaching.

Ethel treats the two differently on purpose, and the difference is the point of
this module:

* **A pack can be taught.** It has a hook, segments, checks, misconception
  repairs - somebody decided the pedagogy.
* **A source can only be quoted.** A student can ask a question and get an
  answer grounded in it, cited to the document and the heading it came from. It
  never becomes a lesson, and Ethel never writes one from it.

That boundary is what keeps the four gates intact. Uploading a textbook makes
Ethel able to answer more questions truthfully; it does not make her able to
invent a lesson about them, and it must not start to.

Sources are marked in every citation, so a student can always tell whether they
are reading material a teacher shaped or a document an administrator uploaded.
Nothing here is signed the way a pack is, because nobody has vouched for it -
`reviewed_by` stays null and the interface says so.

Extraction is standard library only:

* `.txt`, `.md`  - read directly
* `.html`, `.htm` - tags stripped with `html.parser`
* `.docx`        - a zip of XML; paragraphs pulled from `word/document.xml`

PDF is deliberately absent. Nothing in the standard library reads it, and the
core of this project installs nothing. The uploader says so plainly rather than
failing on the file type someone will inevitably try first.
"""

from __future__ import annotations

import html.parser
import json
import re
import shutil
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from .. import config
from .retrieval import Passage

SCHEMA = "ethel.source/1"

TEXT_TYPES = {".txt": "text", ".md": "markdown", ".markdown": "markdown"}
HTML_TYPES = {".html": "html", ".htm": "html"}
DOCX_TYPES = {".docx": "word"}
SUPPORTED = {**TEXT_TYPES, **HTML_TYPES, **DOCX_TYPES}

# Formats people will try that this cannot read, with a useful answer for each.
UNSUPPORTED = {
    ".pdf": ("PDF needs a library the core deliberately does not have. "
             "Save or print it to .docx, .html or .txt and upload that."),
    ".doc": ("The old binary .doc format cannot be read here. Open it in Word "
             "and save as .docx."),
    ".pptx": "Export the slides as .docx or .txt first.",
    ".xlsx": "Save the sheet as .csv, then rename to .txt, or paste into a .docx.",
    ".jpg": "An image has no text to extract. Type the content into a .txt file.",
    ".png": "An image has no text to extract. Type the content into a .txt file.",
}

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_SOURCE = 200


def sources_dir() -> Path:
    d = config.ROOT / "content" / "sources"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ extraction

class _HtmlText(html.parser.HTMLParser):
    """Strip tags, keep headings as headings and paragraphs as paragraphs."""

    SKIP = {"script", "style", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._buf: list[str] = []
        self._skip = 0
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._heading = tag
        elif tag in ("p", "div", "li", "br", "tr"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._buf.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.blocks.append(("heading" if self._heading else "text", text))
        self._heading = None

    def close(self) -> None:
        super().close()
        self._flush()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_blocks(data: bytes) -> list[tuple[str, str]]:
    """Paragraphs out of a .docx, keeping Word's own heading styles."""
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        if "word/document.xml" not in z.namelist():
            raise ValueError("not a Word document (no word/document.xml)")
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    out: list[tuple[str, str]] = []
    for para in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in para.iter(f"{_W}t")).strip()
        if not text:
            continue
        style = ""
        pstyle = para.find(f"{_W}pPr/{_W}pStyle")
        if pstyle is not None:
            style = (pstyle.get(f"{_W}val") or "").lower()
        kind = "heading" if style.startswith("heading") or style == "title" else "text"
        out.append((kind, text))
    return out


def _markdown_blocks(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            out.append(("heading", block.lstrip("#").strip()))
        else:
            out.append(("text", " ".join(block.split())))
    return out


def extract(filename: str, data: bytes) -> list[dict[str, Any]]:
    """File bytes -> a list of {heading, text} sections.

    Sections are cut at headings, because a heading is the closest thing a
    document has to a citation anchor. A student told "from Chapter 3, Soil
    Acidity" can go and find it; one told "from page 41 of the upload" often
    cannot.
    """
    ext = Path(filename).suffix.lower()
    if ext in UNSUPPORTED:
        raise ValueError(f"{ext} files cannot be read. {UNSUPPORTED[ext]}")
    if ext not in SUPPORTED:
        raise ValueError(
            f"{ext or 'that file'} is not a format Ethel can read. Supported: "
            + ", ".join(sorted(SUPPORTED)))
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{filename} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB.")

    if ext in DOCX_TYPES:
        blocks = _docx_blocks(data)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", "replace")
        text = unicodedata.normalize("NFC", text)
        if ext in HTML_TYPES:
            parser = _HtmlText()
            parser.feed(text)
            parser.close()
            blocks = parser.blocks
        else:
            blocks = _markdown_blocks(text)

    sections: list[dict[str, Any]] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        body = "\n\n".join(buf).strip()
        if body:
            sections.append({"heading": heading or "(no heading)", "text": body})

    for kind, text in blocks:
        if kind == "heading":
            flush()
            buf = []
            heading = text
        else:
            buf.append(text)
    flush()
    return sections


# ------------------------------------------------------------------ the store

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "source"


def list_sources() -> list[dict[str, Any]]:
    out = []
    for d in sorted(sources_dir().iterdir()):
        meta = d / "source.json"
        if d.is_dir() and meta.exists():
            try:
                out.append(json.loads(meta.read_text("utf-8")))
            except (ValueError, OSError):
                continue
    return out


def load(source_id: str) -> dict[str, Any] | None:
    meta = sources_dir() / source_id / "source.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text("utf-8"))
    except (ValueError, OSError):
        return None


def create(title: str, programme: str, uploaded_by: str,
           course_code: str = "", year: int | None = None,
           note: str = "") -> dict[str, Any]:
    base = _slug(title)
    sid, n = base, 1
    while (sources_dir() / sid).exists():
        n += 1
        sid = f"{base}-{n}"
    (sources_dir() / sid).mkdir(parents=True)
    rec = {
        "schema": SCHEMA,
        "id": sid,
        "title": title.strip() or sid,
        "programme": programme,
        "course_code": course_code,
        "year": year,
        "note": note,
        "uploaded_by": uploaded_by,
        "uploaded_on": time.strftime("%Y-%m-%d"),
        # Nobody has vouched for this the way an author vouches for a pack.
        "reviewed_by": None,
        "files": [],
    }
    save(rec)
    return rec


def save(rec: dict[str, Any]) -> None:
    d = sources_dir() / rec["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False), "utf-8")


def safe_name(filename: str) -> str:
    """A filename that cannot escape the source's own folder.

    Uploads name their own files, so this is the boundary that stops
    `../../data/students/x.json` from being written.
    """
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(". ")
    return name[:120] or "upload"


def add_file(source_id: str, filename: str, data: bytes) -> dict[str, Any]:
    rec = load(source_id)
    if rec is None:
        raise ValueError(f"No source called {source_id!r}.")
    if len(rec["files"]) >= MAX_FILES_PER_SOURCE:
        raise ValueError(f"A source holds at most {MAX_FILES_PER_SOURCE} files.")

    name = safe_name(filename)
    sections = extract(name, data)          # raises with a usable message
    folder = sources_dir() / source_id / "files"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        n += 1
        target = folder / f"{stem}-{n}{suffix}"
    target.write_bytes(data)

    entry = {
        "name": target.name,
        "original_name": filename,
        "bytes": len(data),
        "format": SUPPORTED[Path(name).suffix.lower()],
        "sections": len(sections),
        "words": sum(len(s["text"].split()) for s in sections),
        "added_on": time.strftime("%Y-%m-%d"),
    }
    rec["files"].append(entry)
    save(rec)
    (sources_dir() / source_id / "extracted").mkdir(exist_ok=True)
    (sources_dir() / source_id / "extracted" / f"{target.stem}.json").write_text(
        json.dumps(sections, indent=2, ensure_ascii=False), "utf-8")
    return entry


def remove_file(source_id: str, name: str) -> None:
    rec = load(source_id)
    if rec is None:
        return
    rec["files"] = [f for f in rec["files"] if f["name"] != name]
    save(rec)
    for p in (sources_dir() / source_id / "files" / name,
              sources_dir() / source_id / "extracted" / f"{Path(name).stem}.json"):
        p.unlink(missing_ok=True)


def delete(source_id: str) -> bool:
    d = sources_dir() / source_id
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


# ------------------------------------------------------------------ retrieval

def passages(source_id: str) -> list[Passage]:
    """Sections of a source, as retrievable passages marked as reference."""
    rec = load(source_id)
    if rec is None:
        return []
    out: list[Passage] = []
    folder = sources_dir() / source_id / "extracted"
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*.json")):
        try:
            sections = json.loads(f.read_text("utf-8"))
        except (ValueError, OSError):
            continue
        for i, sec in enumerate(sections):
            text = (sec.get("text") or "").strip()
            if not text:
                continue
            out.append(Passage(
                id=f"src:{source_id}:{f.stem}:{i}",
                pack_id=f"source:{source_id}",
                course_code=rec.get("course_code") or "",
                course_title=rec.get("title") or source_id,
                lesson_id=f.stem,
                lesson_title=f.stem,
                section=sec.get("heading") or "(no heading)",
                text=text,
                language="en",
                kind="reference",
            ))
    return out


def for_programme(programme: str, year: int | None = None) -> list[dict[str, Any]]:
    out = []
    for rec in list_sources():
        if rec.get("programme") and rec["programme"] != programme:
            continue
        if year is not None and rec.get("year") and rec["year"] != year:
            continue
        out.append(rec)
    return out


def summary() -> dict[str, Any]:
    recs = list_sources()
    files = sum(len(r["files"]) for r in recs)
    words = sum(f.get("words", 0) for r in recs for f in r["files"])
    return {
        "sources": len(recs),
        "files": files,
        "words": words,
        "reviewed": sum(1 for r in recs if r.get("reviewed_by")),
        "supported_formats": sorted(SUPPORTED),
        "unsupported": UNSUPPORTED,
        "path": str(sources_dir()),
    }
