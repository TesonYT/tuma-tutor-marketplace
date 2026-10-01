"""Authoring tool for course packs.

    python tools/packtool.py new    <pack-id> --code "EE 220" --title "..." \
                                    --institution cbu --programme cbu-beng-electrical --year 2
    python tools/packtool.py sign   [pack-id ...]     # stamp SHA-256 integrity blocks
    python tools/packtool.py check  [pack-id ...]     # validate structure and report warnings
    python tools/packtool.py list                     # what is in the library

`sign` is what makes a pack distributable: it writes a digest over the pack's
canonical content, and every box that later loads the pack recomputes that
digest and refuses to install anything that does not match. Sign a pack after
you finish editing it and before you copy it onto a USB stick.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ethel import config  # noqa: E402
from ethel.core.library import pack_digest  # noqa: E402
from ethel.core.pack import PackError, load_pack, validate  # noqa: E402

TEMPLATE = {
    "schema": "ethel.pack/1",
    "id": "",
    "course": {"code": "", "title": "", "institution": "", "programme": "", "year": 1},
    "source": {
        "authored_by": "",
        "authored_on": "",
        "licence": "CC BY-SA 4.0",
        "reviewed_by": None,
        "syllabus_reference": "Where did this course's structure come from? Cite it, "
                              "or say plainly that it is not published.",
    },
    "languages": ["en"],
    "lessons": [
        {
            "id": "l1",
            "title": "",
            "skill": "",
            "prerequisites": [],
            "estimated_minutes": 25,
            "hook": "One concrete situation the student recognises, that this lesson resolves.",
            "segments": [
                {
                    "id": "s1",
                    "heading": "",
                    "body": "",
                    "recap": "",
                    "check": {
                        "id": "l1s1c",
                        "type": "mcq",
                        "skill": "",
                        "difficulty": 2,
                        "prompt": "",
                        "options": ["", "", "", ""],
                        "answer": 0,
                        "misconception": {"1": "m1"},
                        "hint": "",
                        "explanation": "",
                    },
                }
            ],
            "worked_example": {"prompt": "", "steps": [""]},
            "practice": [
                {
                    "id": "l1p1",
                    "type": "numeric",
                    "skill": "",
                    "difficulty": 3,
                    "prompt": "",
                    "answer": 0,
                    "hint": "",
                    "explanation": "",
                }
            ],
            "apply": {
                "id": "l1a1",
                "type": "short",
                "skill": "",
                "difficulty": 4,
                "prompt": "",
                "answer": ["", ""],
                "min_match": 2,
                "explanation": "",
            },
            "recap": [""],
            "misconceptions": [
                {
                    "id": "m1",
                    "signal": "What the student does or says when they hold this wrong idea.",
                    "repair": "Address the wrong idea directly. Do not simply restate the "
                              "correct rule - explain why the wrong one feels right and where "
                              "it breaks.",
                }
            ],
        }
    ],
    "diagnostic": [
        {"id": "d1", "skill": "", "difficulty": 2, "type": "mcq",
         "prompt": "", "options": ["", "", "", ""], "answer": 0}
    ],
}


def _dirs(names: list[str]) -> list[Path]:
    if names:
        return [config.LIBRARY_DIR / n for n in names]
    return sorted(p for p in config.LIBRARY_DIR.iterdir() if p.is_dir())


def cmd_new(args: argparse.Namespace) -> int:
    target = config.LIBRARY_DIR / args.pack_id
    if target.exists():
        print(f"{target} already exists.")
        return 1
    target.mkdir(parents=True)
    data = json.loads(json.dumps(TEMPLATE))
    data["id"] = args.pack_id
    data["course"].update({
        "code": args.code or "",
        "title": args.title or args.pack_id,
        "institution": args.institution or "",
        "programme": args.programme or "",
        "year": args.year or 1,
    })
    (target / "pack.json").write_text(json.dumps(data, indent=2), "utf-8")
    print(f"Created {target / 'pack.json'}")
    print("Fill it in, then run:  python tools/packtool.py sign", args.pack_id)
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    failed = 0
    for directory in _dirs(args.pack_ids):
        manifest = directory / "pack.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text("utf-8"))
        digest = pack_digest(data)
        data["integrity"] = {"algorithm": "sha256", "sha256": digest}
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        try:
            validate(data, label=data.get("id", directory.name))
            print(f"signed  {data.get('id', directory.name)}  {digest[:16]}...")
        except PackError as exc:
            failed += 1
            print(f"SIGNED BUT INVALID  {directory.name}: {exc}")
    return 1 if failed else 0


def cmd_check(args: argparse.Namespace) -> int:
    bad = 0
    for directory in _dirs(args.pack_ids):
        if not (directory / "pack.json").exists():
            continue
        try:
            pack = load_pack(directory)
        except PackError as exc:
            bad += 1
            print(f"FAIL  {directory.name}: {exc}")
            continue
        warnings = validate(pack.data, label=pack.id)
        declared = pack.data.get("integrity", {}).get("sha256")
        actual = pack_digest(pack.data)
        seal = ("unsigned" if not declared
                else "ok" if declared == actual else "MISMATCH")
        if seal == "MISMATCH":
            bad += 1
        print(f"{'FAIL' if seal == 'MISMATCH' else 'OK  '}  {pack.id}: "
              f"{len(pack.lessons)} lessons, {len(pack.passages())} passages, "
              f"{sum(len(l.get('practice', [])) for l in pack.lessons)} practice items, "
              f"{len(pack.diagnostic_items())} diagnostic items, seal={seal}")
        for w in warnings:
            print(f"      warning: {w}")
    return 1 if bad else 0


def cmd_translate_export(args: argparse.Namespace) -> int:
    """Write a fill-in file for a translator.

    The file is deliberately plain: every English string paired with an empty
    box to put the translation in, in the order a student meets them. A
    translator should not have to read the pack schema, and should never be
    asked to edit `pack.json` by hand - that is how correct answers get broken.
    """
    directory = _dirs([args.pack_id])[0]
    pack = load_pack(directory)
    lang = args.language
    existing = (pack.data.get("translations") or {}).get(lang) or {}
    prev_lessons = existing.get("lessons") or {}

    units: list[dict[str, Any]] = []

    def unit(path: str, english: str, note: str = "") -> None:
        if not english:
            return
        units.append({
            "id": path,
            "english": english,
            lang: _dig(prev_lessons, path) or "",
            **({"note": note} if note else {}),
        })

    for lesson in pack.lessons:
        lid = lesson["id"]
        unit(f"{lid}.title", lesson.get("title", ""))
        unit(f"{lid}.hook", lesson.get("hook", ""),
             "The opening hook. Keep it concrete and local if you can.")
        for seg in lesson.get("segments", []):
            sid = seg.get("id")
            unit(f"{lid}.{sid}.heading", seg.get("heading", ""))
            unit(f"{lid}.{sid}.body", seg.get("body", ""))
            # The micro-recap a scaffolded student sees after this segment. Like
            # `apply`, it was missed here at first: the lesson uses it, so
            # leaving it out of the export meant it could never be translated.
            unit(f"{lid}.{sid}.recap", seg.get("recap", ""),
                 "The one-line recap shown after this segment to students who "
                 "need extra scaffolding.")
            check = seg.get("check")
            if check:
                unit(f"{lid}.item.{check['id']}.prompt", check.get("prompt", ""))
                for i, opt in enumerate(check.get("options", []) or []):
                    unit(f"{lid}.item.{check['id']}.option.{i}", opt,
                         "Keep the options in this exact order - the right answer "
                         "is stored as a position, not as text.")
        we = lesson.get("worked_example") or {}
        unit(f"{lid}.worked_example.prompt", we.get("prompt", ""))
        for i, step in enumerate(we.get("steps", []) or []):
            unit(f"{lid}.worked_example.step.{i}", step)
        for item in lesson.get("practice", []):
            unit(f"{lid}.item.{item['id']}.prompt", item.get("prompt", ""))
            for i, opt in enumerate(item.get("options", []) or []):
                unit(f"{lid}.item.{item['id']}.option.{i}", opt,
                     "Keep this order.")
            if item.get("type") == "short":
                unit(f"{lid}.item.{item['id']}.answer",
                     ", ".join(item.get("answer", []) or []),
                     "Comma-separated words the student's answer must contain, "
                     "in this language. Leave blank and the question stays in English.")
            unit(f"{lid}.item.{item['id']}.hint", item.get("hint", ""))
        # The transfer task at the end of the lesson. It lives beside `practice`
        # rather than inside it, and was missed here at first - the completeness
        # metric counted it while the export never offered it, so a pack could
        # never reach 100%.
        apply_item = lesson.get("apply")
        if apply_item:
            unit(f"{lid}.item.{apply_item['id']}.prompt", apply_item.get("prompt", ""))
            for i, opt in enumerate(apply_item.get("options", []) or []):
                unit(f"{lid}.item.{apply_item['id']}.option.{i}", opt, "Keep this order.")
            if apply_item.get("type") == "short":
                unit(f"{lid}.item.{apply_item['id']}.answer",
                     ", ".join(apply_item.get("answer", []) or []),
                     "Comma-separated words the answer must contain, in this "
                     "language. Leave blank and the question stays in English.")
            unit(f"{lid}.item.{apply_item['id']}.hint", apply_item.get("hint", ""))

        for i, line in enumerate(lesson.get("recap", []) or []):
            unit(f"{lid}.recap.{i}", line)
        for m in lesson.get("misconceptions", []):
            unit(f"{lid}.misconception.{m['id']}.signal", m.get("signal", ""))
            unit(f"{lid}.misconception.{m['id']}.repair", m.get("repair", ""))

    out = {
        "_instructions": [
            f"Translate this course into {lang}. Put your translation in the "
            f"\"{lang}\" field beside each English line, then send this file back.",
            "Leave a line blank if you are unsure. Blank is safe: Ethel shows the "
            "English and tells the student it is not translated yet. A guess is "
            "not safe, because the student cannot tell a guess from a fact.",
            "Do not change the \"id\" or \"english\" fields.",
            "Where options are numbered, keep them in the given order.",
            "Technical terms: if the English term is what students actually use "
            "in class, keep the English term. Note it in provenance.notes.",
        ],
        "pack": pack.id,
        "course": f"{pack.code} {pack.title}".strip(),
        "language": lang,
        "provenance": {
            "translator": existing.get("provenance", {}).get("translator") or "",
            "translated_on": "",
            "reviewed_by": existing.get("provenance", {}).get("reviewed_by") or "",
            "reviewed_on": "",
            "method": "human",
            "notes": "",
        },
        "units": units,
    }
    dest = Path(args.out) if args.out else directory / f"translate-{lang}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), "utf-8")
    filled = sum(1 for u in units if u.get(lang))
    print(f"wrote {dest}")
    print(f"  {len(units)} strings to translate, {filled} already done")
    return 0


def _dig(lessons: dict[str, Any], path: str) -> str:
    """Read an already-translated string back out, so re-exports keep work."""
    parts = path.split(".")
    lid, rest = parts[0], parts[1:]
    node = lessons.get(lid) or {}
    try:
        if rest[0] == "title":
            return node.get("title", "")
        if rest[0] == "hook":
            return node.get("hook", "")
        if rest[0] == "recap":
            return (node.get("recap") or [])[int(rest[1])]
        if rest[0] == "worked_example":
            we = node.get("worked_example") or {}
            return we.get("prompt", "") if rest[1] == "prompt" else (we.get("steps") or [])[int(rest[2])]
        if rest[0] == "item":
            it = (node.get("items") or {}).get(rest[1]) or {}
            if rest[2] == "prompt":
                return it.get("prompt", "")
            if rest[2] == "hint":
                return it.get("hint", "")
            if rest[2] == "answer":
                return ", ".join(it.get("answer") or [])
            if rest[2] == "option":
                return (it.get("options") or [])[int(rest[3])]
        if rest[0] == "misconception":
            m = (node.get("misconceptions") or {}).get(rest[1]) or {}
            return m.get(rest[2], "")
        seg = (node.get("segments") or {}).get(rest[0]) or {}
        return seg.get(rest[1], "")
    except (IndexError, KeyError, ValueError):
        return ""


def cmd_translate_glossary(args: argparse.Namespace) -> int:
    """List the domain vocabulary a translator has to settle before starting.

    The failure mode this prevents is inconsistency: three lessons where
    "offer" is rendered three different ways, so a student cannot tell whether
    they are reading about the same concept. Deciding the terms once, up front,
    is standard translation practice and costs an hour.

    It also forces the decision that matters most in Zambian higher education:
    which words stay in English. Law and engineering are examined in English -
    a student who learns "offeror" only as a Bemba coinage will not recognise it
    in the exam paper. Translating the prose around a term of art helps; replacing
    the term of art does not.
    """
    import collections
    import re

    directory = _dirs([args.pack_id])[0]
    pack = load_pack(directory)

    prose: list[str] = []
    for lesson in pack.lessons:
        prose += [lesson.get("title", ""), lesson.get("hook", "")]
        for seg in lesson.get("segments", []):
            prose += [seg.get("heading", ""), seg.get("body", "")]
        prose += lesson.get("recap", []) or []
    text = " ".join(prose)

    stop = set("""a an the and or but if then than that this these those with without
        from into onto over under between across through during before after while
        when where which what who whom whose how why because so such as at by for in
        of on to up out off is are was were be been being do does did have has had
        will would can could should may might must not no nor only just also very
        more most much many some any each every both either neither other another
        same own here there now then once again still yet even ever never always
        you your yours they them their it its he she his her we us our i me my
        one two three four five ten first second third next last new old good bad
        long short high low large small great little""".split())

    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text)]
    freq = collections.Counter(w for w in words if w not in stop)
    terms = [{"term": w, "occurrences": c, args.language: "",
              "keep_english": None}
             for w, c in freq.most_common(args.top)]

    out = {
        "_instructions": [
            f"Decide each term ONCE, before translating {pack.id}.",
            f"Set keep_english to true for terms of art the student will meet in "
            f"English in lectures and exams - then leave the \"{args.language}\" "
            f"field blank.",
            f"Set keep_english to false and fill in \"{args.language}\" for ordinary "
            f"words that should be translated.",
            "If you are unsure, keep_english true is the safer default: a student "
            "who knows the English term can follow the lecture, and one who only "
            "knows an invented word cannot.",
            "Hand this back with the translation file; it becomes the pack's "
            "provenance.notes so the next translator is consistent with you.",
        ],
        "pack": pack.id,
        "course": f"{pack.code} {pack.title}".strip(),
        "language": args.language,
        "terms": terms,
    }
    dest = Path(args.out) if args.out else directory / f"glossary-{args.language}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), "utf-8")
    print(f"wrote {dest}")
    print(f"  {len(terms)} terms to decide, from {len(prose)} passages")
    print("  Decide these before translating, or the same concept will appear")
    print("  under three different names across three lessons.")
    return 0


def cmd_translate_import(args: argparse.Namespace) -> int:
    """Fold a translator's file back into the pack.

    Refuses rather than half-applies: if the file does not match the pack it is
    for, nothing is written. A pack that is silently half-translated is worse
    than one that is obviously untranslated.
    """
    incoming = json.loads(Path(args.file).read_text("utf-8"))
    lang = incoming["language"]
    directory = _dirs([incoming["pack"]])[0]
    pack = load_pack(directory)
    if incoming["pack"] != pack.id:
        print(f"FAIL  file is for pack {incoming['pack']!r}, not {pack.id!r}")
        return 1

    prov = incoming.get("provenance") or {}
    if not prov.get("translator"):
        print("FAIL  provenance.translator is empty. Ethel records who wrote every "
              "translation; an anonymous one cannot be reviewed later.")
        return 1

    lessons: dict[str, Any] = {}
    applied = skipped = 0
    problems: list[str] = []

    for u in incoming.get("units", []):
        value = (u.get(lang) or "").strip()
        if not value:
            skipped += 1
            continue
        try:
            _place(lessons, u["id"], value, pack)
            applied += 1
        except ValueError as exc:
            problems.append(f"{u['id']}: {exc}")

    if problems:
        print(f"FAIL  {len(problems)} problem(s); nothing written:")
        for p in problems[:20]:
            print(f"      {p}")
        return 1

    translations = pack.data.setdefault("translations", {})
    block = translations.setdefault(lang, {})
    block["lessons"] = lessons
    block["provenance"] = {
        "translator": prov.get("translator"),
        "translated_on": prov.get("translated_on") or _today(),
        "reviewed_by": prov.get("reviewed_by") or None,
        "reviewed_on": prov.get("reviewed_on") or None,
        "method": prov.get("method") or "human",
        "notes": prov.get("notes") or "",
    }
    langs = pack.data.setdefault("languages", ["en"])
    if lang not in langs:
        langs.append(lang)
    # The digest covers content, so a translation invalidates the old seal.
    pack.data.pop("integrity", None)
    (directory / "pack.json").write_text(
        json.dumps(pack.data, indent=2, ensure_ascii=False), "utf-8")

    status = pack.translation_status(lang)
    print(f"imported {applied} strings into {pack.id} [{lang}] "
          f"({skipped} left blank)")
    print(f"  completeness: {status['percent']}%  "
          f"({status['units_done']}/{status['units_total']} units)")
    print(f"  translator: {prov.get('translator')}  "
          f"reviewed_by: {prov.get('reviewed_by') or 'NOBODY YET'}")
    if not prov.get("reviewed_by"):
        print("  NOTE: until a second person reviews it, Ethel shows this "
              "translation with an 'unreviewed' mark.")
    print("  pack seal cleared - run 'packtool sign' when you are done.")
    return 0


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _place(lessons: dict[str, Any], path: str, value: str, pack: Any) -> None:
    """Route one translated string to its home, validating as it goes."""
    parts = path.split(".")
    lid, rest = parts[0], parts[1:]
    if pack.lesson(lid) is None:
        raise ValueError(f"no lesson {lid!r} in this pack")
    node = lessons.setdefault(lid, {})

    if rest[0] in ("title", "hook"):
        node[rest[0]] = value
    elif rest[0] == "recap":
        node.setdefault("recap", []).append(value)
    elif rest[0] == "worked_example":
        we = node.setdefault("worked_example", {})
        if rest[1] == "prompt":
            we["prompt"] = value
        else:
            we.setdefault("steps", []).append(value)
    elif rest[0] == "item":
        it = node.setdefault("items", {}).setdefault(rest[1], {})
        if rest[2] == "prompt":
            it["prompt"] = value
        elif rest[2] == "hint":
            it["hint"] = value
        elif rest[2] == "answer":
            it["answer"] = [t.strip() for t in value.split(",") if t.strip()]
        elif rest[2] == "option":
            opts = it.setdefault("options", [])
            idx = int(rest[3])
            while len(opts) <= idx:
                opts.append("")
            opts[idx] = value
    elif rest[0] == "misconception":
        m = node.setdefault("misconceptions", {}).setdefault(rest[1], {})
        m[rest[2]] = value
    else:
        seg = node.setdefault("segments", {}).setdefault(rest[0], {})
        seg[rest[1]] = value


def cmd_translate_status(args: argparse.Namespace) -> int:
    for directory in _dirs(args.pack_ids):
        if not (directory / "pack.json").exists():
            continue
        pack = load_pack(directory)
        langs = [l for l in pack.languages if l != "en"]
        if not langs:
            print(f"{pack.id:22} English only")
            continue
        for lang in langs:
            s = pack.translation_status(lang)
            mark = "reviewed" if s["reviewed"] else "UNREVIEWED"
            print(f"{pack.id:22} {lang}  {s['percent']:3}%  "
                  f"({s['units_done']}/{s['units_total']})  {mark}  "
                  f"translator={s['translator'] or '?'}")
            if args.gaps and s["gaps"]:
                for g in s["gaps"]:
                    print(f"    missing: {g}")
                if s["gaps_total"] > len(s["gaps"]):
                    print(f"    ... and {s['gaps_total'] - len(s['gaps'])} more")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    for directory in _dirs([]):
        try:
            pack = load_pack(directory)
        except PackError:
            print(f"?  {directory.name}  (not a valid pack)")
            continue
        print(f"{pack.id:24}  {pack.code or '-':10}  {pack.title[:48]:48}  "
              f"{pack.programme or '-'} y{pack.year}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="packtool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="scaffold a new pack")
    p.add_argument("pack_id")
    p.add_argument("--code", default="")
    p.add_argument("--title", default="")
    p.add_argument("--institution", default="")
    p.add_argument("--programme", default="")
    p.add_argument("--year", type=int, default=1)
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("sign", help="stamp integrity digests")
    p.add_argument("pack_ids", nargs="*")
    p.set_defaults(fn=cmd_sign)

    p = sub.add_parser("check", help="validate packs")
    p.add_argument("pack_ids", nargs="*")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("list", help="list the library")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("translate-export",
                       help="write a fill-in file for a translator")
    p.add_argument("pack_id")
    p.add_argument("language", help="language code, e.g. bem or loz")
    p.add_argument("--out", default="", help="where to write it")
    p.set_defaults(fn=cmd_translate_export)

    p = sub.add_parser("translate-glossary",
                       help="domain terms a translator must settle first")
    p.add_argument("pack_id")
    p.add_argument("language")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_translate_glossary)

    p = sub.add_parser("translate-import",
                       help="fold a translator's file back into the pack")
    p.add_argument("file")
    p.set_defaults(fn=cmd_translate_import)

    p = sub.add_parser("translate-status",
                       help="how complete each translation is, and what is missing")
    p.add_argument("pack_ids", nargs="*")
    p.add_argument("--gaps", action="store_true", help="list every missing string")
    p.set_defaults(fn=cmd_translate_status)

    args = parser.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
