# Architecture

Decisions, data shapes and the reasoning behind them. `README.md` covers what
the thing does; this covers why it is built this way and where to extend it.

---

## 1. The load-bearing constraint

Everything follows from one rule: **the tutor may only teach from course packs
physically present on this machine, and must say so when a question falls
outside them.**

That rule is not a prompt. Small models argue with prompts. It is enforced
structurally in four independent places, any one of which can stop an answer:

| Gate | Where | What it catches |
|---|---|---|
| Retrieval | `core/retrieval.py` | The question has no support in the installed material at all. |
| Instruction | `core/grounding.py` `SYSTEM` | The model is told the passages are the whole world, and given `INSUFFICIENT_CONTEXT` as an explicitly valued answer. |
| Citation | `core/grounding.py` `_valid_citations` | The model answered without pointing at anything, or invented a passage number. |
| Grounding | `core/retrieval.py` `grounding_ratio` | The model started from the pack and drifted into its own memory. |

Gates 2–4 fall back to **extractive mode** — the pack's own words, quoted —
rather than to an unverified paraphrase. Gate 1 produces a refusal naming what
was searched.

The same principle governs marking: `core/grading.py` is arithmetic and lexical,
never a model call. And it governs the curriculum layer: `content/catalog/` has a
`coverage` flag per programme, and `"none"` surfaces to the student as "this
institution does not publish its course list" rather than as a generated syllabus.

---

## 2. Why no dependencies

`pip install` is a network operation, and a machine with no network is the worst
possible place to discover a wheel is missing. The core runs on the Python
standard library alone:

* **Retrieval** is pure-Python BM25 rather than embeddings. Students search with
  exact terminology — "Kirchhoff", "LPR2920", "invitation to treat" — which is
  precisely BM25's strength. No model to download, nothing to keep in sync, and
  it ranks a three-pack shelf in milliseconds. The cost is recall on a question
  phrased with no overlapping vocabulary; there the tutor refuses, which is the
  safe direction to fail.
* **UI** is a stdlib `ThreadingHTTPServer` plus three static files. The browser
  is the one rich UI toolkit already on every Windows machine.
* **Model and voice are optional and detected at runtime.** The app is fully
  usable with neither.
* **Voices are held open, not respawned.** `ethel/voice_worker.py` is the only
  module that runs under the voice virtualenv rather than the system
  interpreter, because it is the only code that imports `piper`. The server
  spawns one process per voice and speaks to it over a pipe: newline-delimited
  JSON in, a length-prefixed WAV out. Shelling out per sentence reloaded a 63 MB
  model every call and cost 17-20s; holding it open costs 0.4-0.9s. Residency is
  capped by hardware tier and reaped when idle, because on a 4 GB machine memory
  is the scarce resource, and a crashed worker degrades to the one-shot path
  rather than to an error.
* **Socratic mode keeps the model out of the loop on purpose.**
  `core/socratic.py` splits a segment into sentences, asks before telling, and
  judges lexically - the same arithmetic as `core/grading.py`. That is a
  pedagogical choice and a latency one at the same time: judging returns in
  milliseconds, so a spoken turn costs ~0.6s to speak plus ~3.1s to recognise.
  A model in the middle would add 10-30s on this class of hardware and there
  would be no conversation to have. The opener is deliberately unscored, and
  lead-in sentences are shown but never marked.
* **Packs are taught; sources are only quoted.** `core/sources.py` holds
  documents an administrator uploaded. They are indexed and citable, and every
  passage carries `kind="reference"` so the citation says where it came from.
  They never become lessons. A pack encodes a decision about *how to teach*
  something - hook, segments, checks, misconception repairs - and no extraction
  step produces that. Blurring the two would make Ethel the author of teaching
  material, which is the one thing gate 1 exists to prevent.
* **Speech in and speech out are separate capabilities.** `speech.py` speaks,
  `listen.py` hears, and for these languages they are not symmetric: Bemba has
  both, Lozi has recognition only, English has synthesis only (no ASR model is
  installed). Each reports what exists rather than what is wanted.
* **Both share one memory budget** (`ethel/residency.py`). Two independently
  capped pools looked reasonable - one voice, one recogniser - until the numbers
  were added up: 60 MB plus 1.2 GB on a 3.9 GB machine that is also running a
  language model. It deadlocked a browser demo with 60 MB of free RAM. Loading a
  recogniser now evicts resident voices and vice versa, across pools, LRU first.
  A model bigger than the whole budget is still allowed to load once everything
  else is gone - refusing would mean a 4 GB machine could never use a 1.2 GB
  recogniser at all - and it is reported as over budget rather than left to be
  discovered as a hang.
* **The machine is measured, not assumed.** `ethel/hardware.py` reads RAM and
  core count at start-up and sizes the context window, thread count, answer cap
  and timeout to match, then publishes every chosen value on the System check
  page. An explicit value in `data/config.json` always wins. The counter-intuitive
  part is that the *timeout rises* as hardware weakens: a slow answer that
  arrives still beats a timeout that falls back to quoting.

---

## 3. The airlock

`ethel/airlock.py` replaces `socket.socket.connect`, `connect_ex` and
`socket.getaddrinfo` before any other module is imported (`ethel/__main__.py`
engages it on line one). Non-loopback destinations raise `NetworkBlocked` and are
recorded in a ring buffer surfaced on the System check page.

Loopback is allowed because two legitimate local services live there: the tutor's
own UI, and an optional Ollama server. Neither leaves the machine.

This makes "offline" auditable rather than aspirational. A non-zero blocked
counter is a finding: something in the stack tried to reach out, and the page
names the host.

---

## 4. Content distribution

```
authoring machine                    target machine
-----------------                    --------------
packtool new                         content/library/     <- USB / SD / LAN copy
packtool check                            |
packtool sign   --> SHA-256 in manifest   | enrol
                                          v
                                     library.plan_for(programme, year, track)
                                          |  resolve syllabus -> match packs
                                          |  verify SHA-256
                                          v
                                     data/installed/<student>/
```

`library.pack_digest()` hashes the pack's canonical JSON with the `integrity`
block removed, so the digest depends on what the pack *says*, not on how it is
formatted. Sign once at authoring time; every machine that later receives the
pack recomputes and refuses a mismatch.

`plan_for()` returns four lists, and the fourth is the important one:

* `matched` — syllabus courses with a verified pack on this machine
* `missing` — syllabus courses with **no** pack, named individually with a reason
* `also_available` — packs for this programme that were not matched: a different
  year, or (when the institution publishes no syllabus at all) everything
* `syllabus_known` — false when the institution publishes no course list

Without `also_available`, a student on a programme like the UNZA BAgSc would be
offered nothing despite real material sitting on the machine.

---

## 5. The pack format

One directory, one `pack.json`, schema `ethel.pack/1`. Everything the tutor may
ever say about a course lives inside it.

```jsonc
{
  "schema": "ethel.pack/1",
  "id": "cbu-ee220",
  "course":  {"code": "EE 220", "title": "...", "institution": "cbu",
              "programme": "cbu-beng-electrical", "year": 2},
  "source":  {"authored_by": "...", "licence": "...", "reviewed_by": null,
              "syllabus_reference": "where the structure came from, or that it isn't published",
              "jurisdiction_note": "...",   // law packs
              "field_note": "..."},         // agriculture packs
  "integrity": {"algorithm": "sha256", "sha256": "..."},   // written by packtool sign
  "languages": ["en"],
  "lessons": [{
    "id": "l1", "title": "...", "skill": "ohms-law", "prerequisites": [],
    "estimated_minutes": 25,
    "hook": "one concrete situation the student recognises",
    "segments": [{"id": "s1", "heading": "...", "body": "...", "recap": "...",
                  "check": { /* item */ }}],
    "worked_example": {"prompt": "...", "steps": ["..."]},
    "practice": [ /* items */ ],
    "apply":    { /* item, usually type "short" */ },
    "recap":    ["..."],
    "misconceptions": [{"id": "m1", "signal": "what the student does when they hold this wrong idea",
                        "repair":  "address the wrong idea, don't restate the right one"}]
  }],
  "diagnostic": [ /* items, tagged skill + difficulty 1-5 */ ]
}
```

An **item**:

```jsonc
{"id": "...", "skill": "...", "difficulty": 1-5,
 "type": "mcq" | "numeric" | "short",
 "prompt": "...",
 "options": ["..."],                  // mcq
 "answer": 0 | 3.5 | ["term", ...],   // index | number | required terms
 "tolerance": 0.02,                   // numeric, relative
 "min_match": 2,                      // short
 "forbidden": ["..."],                // short: terms that mark it wrong outright
 "hint": "...", "explanation": "...",
 "misconception": {"1": "m2", "order_of_magnitude": "m3"},
 "variant": { /* a genuine second version for the retry */ }}
```

`misconception` maps a *specific wrong answer* to a *specific repair*. That
mapping is what makes the tutor teach rather than mark. `order_of_magnitude` is a
reserved key the numeric grader fires when an answer is out by a factor of ten —
almost always a unit-prefix slip.

**Only teaching prose is indexed for retrieval.** Practice answers, explanations
and hints are excluded, so a student cannot pull the answer key out of the tutor
by asking the exam question back at it. The self-test asserts this.

---

## 6. The learner model

`core/profile.py`. Deliberately simple and readable in the student's JSON file,
so a teacher can see why the tutor made a decision and argue with it.

```
p_new = p + α·(outcome − p),    α = 0.20 + 0.06·difficulty,    p₀ = 0.35
mastered ⟺ p ≥ 0.8 and attempts ≥ 3
```

Spaced repetition is SM-2 without the ceremony: correct multiplies the interval
by an ease that drifts up to 2.8, wrong resets to one day and drops the ease.

The prior of 0.35 is "probably not yet mastered" — the tutor starts by assuming
it needs to teach, not that it needs to test.

---

## 7. Placement

Two parts, in order, because they answer different questions.

**Adaptive diagnostic** (`core/placement.py`) — items drawn from the installed
packs, tagged skill + difficulty. Starts at difficulty 3, steps up on a correct
answer and down on a wrong one, so twelve items locate a student far more
precisely than twelve fixed ones. No explanations are given during the test:
explaining item 3 contaminates item 7 on the same skill. The full breakdown comes
at the end.

**Five preference questions** — language, voice, pace, example-first vs rule-
first, reading support. These set the *shape* of teaching; the diagnostic sets
the *starting point*.

The output is a plain-language statement of how the tutor will now behave
(`_describe_plan`), shown to the student. Skills are bucketed into three bands —
gap (< 0.5), shaky (0.5–0.75), strong (≥ 0.75) — because most students land in
the middle and telling them the test said nothing would be both discouraging and
untrue.

---

## 8. The teaching engine

`core/pedagogy.py` compiles a lesson into an explicit list of **moves** stored on
the student's record, then walks a cursor along it. The plan is data, so it can
be inspected, logged, and modified mid-lesson.

```python
[{"type": "hook"}, {"type": "worked_example"},
 {"type": "segment", "i": 0}, {"type": "check", "i": 0},
 {"type": "micro_recap", "i": 0},          # scaffolded pace only
 {"type": "practice", "i": 0}, ..., {"type": "apply"}, {"type": "recap"}, {"type": "end"}]
```

`build_plan()` shapes it from the learner profile. `advance()` grades the current
move's item, updates mastery, schedules review — and on a wrong answer
**splices** new moves in at the cursor:

```python
plan[cursor+1:cursor+1] = [{"type": "remediate", "body": <the pack's repair>},
                           {"type": "retry",     "item": _variant(item)}]
```

`_variant()` prefers a genuine variant from the pack; failing that it rotates the
MCQ options and remaps the answer and the misconception keys, so the student
cannot pass by remembering a position. It deliberately does **not** ask a model
to generate a new question — a generated question has no verified answer key.

Two circuit breakers stop this from grinding a student down: a second miss on the
same idea stops the drilling and gives the pack's full explanation, and
`remediations` is capped. At the `end` move, failing the mastery gate splices in
extra practice instead of signing the student off.

---

## 9. Request flow

```
browser ──▶ ethel/server.py (stdlib HTTP, loopback only)
              │  session cookie ──▶ data/students/<id>.json
              ├─ /api/catalog     ──▶ core/catalog     ──▶ content/catalog/*.json
              ├─ /api/install     ──▶ core/library     ──▶ content/library ⇒ data/installed
              ├─ /api/placement/* ──▶ core/placement   ──▶ core/grading
              ├─ /api/lesson/*    ──▶ core/pedagogy    ──▶ core/grading, core/grounding
              ├─ /api/ask         ──▶ core/grounding   ──▶ core/retrieval, tutor/llm
              └─ /api/speak       ──▶ tutor/speech     ──▶ piper subprocess
```

`Shelf` (`core/library.py`) is the per-student unit of work: installed packs plus
a BM25 index over their passages, cached in `server._shelves` and rebuilt on
install.

The student record is loaded, mutated and saved on each request — no in-memory
session state beyond the token map, so a crash loses at most one answer.

---

## 9a. The interface

Three files, no build step, no package manager: `ethel/static/index.html`,
`style.css`, `app.js`. Vanilla DOM throughout, because the whole point of the
project is that it can be copied onto a machine and run.

**The look follows Gemini.** A tinted rail on the left rather than a top bar,
flat surfaces separated by tint instead of shadow, large radii (16px cards,
28px composers, fully round buttons), and one gradient — blue through violet to
rose — used only for identity: the wordmark, the greeting, the bar that fills
as a student works, and the dot in front of anything Ethel says.

Three things about it were deliberate choices rather than defaults:

* **One typeface.** The previous design set anything the tutor said in a serif,
  which was good pedagogy but reads as a different product next to a
  single-typeface idiom. `--serif` still exists and is still applied to the same
  elements, so it is one line to put back.
* **System fonts.** Google Sans is named first for the rare machine that has it;
  Roboto and Segoe carry everyone else. A webfont would be a network request,
  and the airlock would refuse it.
* **Dark mode follows the operating system.** There is no in-app toggle: one
  fewer preference to store per student on a shared machine.

### What happens without being asked

The interface does a small amount of work on the student's behalf. None of it
touches teaching; all of it is about keeping hands on the keyboard.

| | |
|---|---|
| `1`–`9` pick a multiple-choice option, `Enter` answers | one `optionList()` serves both the placement test and lessons, which had drifted apart as two copies |
| `/` jumps to the question box, `Esc` leaves Socratic mode | Socratic mode is the one place a student can feel stuck |
| The question box grows with what is typed, `Enter` sends, `Shift+Enter` breaks a line | a textarea does not submit its form on `Enter` the way an input does |
| A half-typed question survives a trip to a lesson and back | `sessionStorage`, so it never outlives the browser or follows one student to the next |
| Finishing a lesson refreshes the mastery bars beside it | matched by lesson id, not row position |
| Files dropped on a source card upload to it | the buttons still work; this is for dragging a folder out of a file manager |
| The rail remembers whether it was collapsed | except on a phone, where it is an overlay and always starts closed |
| A long answer says what it is doing at 7s, 16s and 32s | a grounded answer measured **45 seconds** on the machine this was built on; three dots for that long reads as a hang, and a student who thinks it hung will reload and lose the question |

The last row is the one that matters. The wait is real and the honest thing is
to name the hardware rather than let the student assume they broke something.

### Two notes for whoever touches this next

* The page is served under `style-src 'self'`. Every fixed value lives in the
  stylesheet; only genuinely computed ones — bar widths, the height of the
  growing text box — are written from JavaScript, and those are applied through
  the CSSOM, which the policy permits. Inline `style` attributes in the markup
  are *not* permitted and will silently do nothing.
* `[hidden]` needs `display: none !important`, because several elements toggled
  with the attribute are also flex or grid containers and an author rule with an
  explicit `display` beats the user-agent rule.

---

## 10. Extension points

| To do this | Touch |
|---|---|
| Add an institution or programme | `content/catalog/*.json` — set `coverage` honestly |
| Add a course | `tools/packtool.py new` → fill → `check` → `sign` |
| Swap the model | `data/config.json` `llm.model`, or add a backend class in `ethel/llm.py` |
| Add a voice | Drop a `.onnx` + `.onnx.json` into `models/piper/`; it appears in the picker |
| Change speaking paces | `ethel/speech.py` `PACES` |
| Change how many voices stay resident | `ethel/hardware.py` tier, or `speech.max_resident_voices` |
| Change the shared memory budget | `ethel/residency.py` `_BUDGET_MB` |
| Add a recognition language | `ethel/listen.py` `MODELS`, then `get_voice.py --asr <code>` |
| Translate a pack | `packtool translate-export` / `translate-import` |
| Add an upload format | `core/sources.py` `SUPPORTED` plus an extractor |
| Make someone an administrator | `tools/adminctl.py grant <id>` |
| Tune Socratic strictness | `core/socratic.py` `GRASPED` / `PARTIAL` / `MIN_CONTENT_TERMS` |
| Add a recognition runtime | `ethel/asr_worker.py` `_load` |
| Change how the machine is sized | `ethel/hardware.py` `_TIERS` and `_MODEL_BUDGET_GB` |
| Add embeddings alongside BM25 | `core/retrieval.py` — `Bm25Index.search` is the only caller-facing surface |
| Add a language | `ethel/locales/<code>.json` + `i18n.LANGUAGES`; per-language pack fields already exist |
| Add a voice | `data/config.json` `speech.voices.<lang>.<gender>` |
| Change how strictly it refuses | `data/config.json` `retrieval.min_term_coverage`, `min_answer_grounding` |
| Change the mastery bar | `data/config.json` `pedagogy.mastery_threshold` |
| Restyle anything | `ethel/static/style.css` — colours are all custom properties on `:root` |
| Put the tutor's serif back | `style.css` `--serif` |
| Change a keyboard shortcut | `app.js`, the single `keydown` listener |

---

## 11. What this design does not do

* **No cross-student analytics.** Each record is a single file on one machine. A
  cohort view means an export step that does not exist yet, and any such step
  needs a consent position settled before it is built.
* **No content generation.** The tutor cannot write a lesson, and adding that
  would dismantle gate 1. Course material is authored by people and reviewed by
  subject teachers; `reviewed_by: null` is displayed to students until it is.
* **No free-form Bemba or Lozi teaching.** See `ethel/i18n.py`. The interface is
  translatable today; lesson text needs human translators.
* **No real authentication.** The PIN separates students on a shared machine.
  Anything stronger needs a threat model, and for a supervised shared machine the
  honest answer is that this is enough.
