# Ethel

A compact, strictly offline AI teacher.

A student signs in, says where they study and what year they are in, and Ethel
builds their course from that institution's own published programme structure.
She installs only the material that programme needs — from a library already on
the machine, never from the internet — places the student with a short adaptive
test, then teaches: in pieces, with a question after each one, repairing the
specific wrong idea behind a wrong answer rather than reading out the answer.

Ask her something outside the installed material and she says **"I don't know"**.
That is the point of the whole design, and it is enforced in four places, not
promised in a prompt.

---

## Run it

```bash
run.bat
```

Or, from any shell:

```bash
python -m ethel
```

Then open <http://127.0.0.1:8770/>. Needs Python 3.10+ and nothing else — no
pip install, no packages, no build step. The web UI is three files served by the
Python standard library.

---

## How big is it?

**Ethel is 1 MB.** The bundle that ships is 0.2 MB compressed:

```bash
run.bat --package          # dist/Ethel-0.1.0.zip - the tutor and her courses
```

That bundle teaches on its own. Everything that makes an install look large is
an optional add-on belonging to the machine, not to the courseware:

| | Size | Without it |
|---|---|---|
| **Ethel + course packs** | **1 MB** | — |
| Language model (Ollama + llama3.2:1b) | 1414 MB | Extractive mode: she quotes the pack instead of rephrasing |
| Piper voices, two English | 121 MB | Read-aloud disabled, and says why |
| MMS Bemba voice | 139 MB | No Bemba speech |
| Bemba recogniser | 1214 MB | Microphone hidden |
| torch + transformers (for the two above) | 914 MB | — |

So a site with ten machines copies a 0.2 MB folder ten times and fetches the
heavy parts once. `REQUIREMENTS.json` inside the bundle lists each optional
component, what it buys, what happens without it, and the exact commands.

`package.py` uses an **allowlist, never a denylist** — only paths matching an
explicit rule ship — and refuses to build if a staged file contains a student
PIN hash, an email address, a MAC address or an absolute user path. That scan is
tested: planting a fake student record in a shipped file stops the build.

### One easy 2.6 GB

Ollama installs CUDA and ROCm runtimes into `lib/ollama/` regardless of your
hardware. On a machine with no NVIDIA or AMD GPU they are pure dead weight:

```
cuda_v12   1099.9 MB      rocm_v7_1   927.1 MB      cuda_v13   629.2 MB
```

Deleting those three folders took Ollama's install from **2810.6 MB to 154.3 MB**
with no loss of function — verified by generating afterwards. Reversible by
reinstalling Ollama, which costs a 1.5 GB download, so it is worth doing
deliberately rather than by accident.

---

Other entry points:

```bash
run.bat --doctor      # what this machine can and cannot do
run.bat --validate    # check every course pack in the library
run.bat --selftest    # end-to-end test of every promise in this README
run.bat --modelcheck  # is the local model working, honest, and fast enough?
run.bat --listencheck bem   # can Ethel hear this language, and how well?
run.bat --package     # build the distributable bundle
```

`--selftest` currently reports **43 passed, 0 failed** on this machine.

---

## How "downloads without touching the internet" works

The tutor cannot reach the internet. `ethel/airlock.py` patches the socket layer
at start-up so that any outbound connection to a non-loopback address raises and
is recorded. Loopback stays open for two local things: the tutor's own web UI,
and an optional Ollama model server. The System check page shows the counter —
if anything in the stack ever tries to phone home, you can see exactly what.

Course material arrives the only way it can without a network: physically.

```
content/library/          <- the offline "content hub", populated by copying a
   cbu-ee220/pack.json       signed pack bundle off a USB stick, SD card or LAN share
   unza-lpr2920/pack.json
   unza-agric-soils/pack.json
```

The student's shelf starts empty. On enrolment the tutor resolves the courses
their programme and year require, matches them against the packs physically
present, verifies each pack's SHA-256 against its own manifest, copies the
matches into `data/installed/<student>/`, and then **names every required course
it has no pack for**. That last step is the honest one: a UNZA LLB Year 2 student
is told, by course code, that Ethel can teach Law of Contract and cannot yet
teach the other five.

---

## Reference material: what an administrator can add

Hand-writing pack JSON does not scale, so an administrator can upload documents
through the **Administration** tab. The role is granted from the machine itself:

```bash
python tools/adminctl.py grant 2021458392
```

Deliberately a command line, not a screen. Every scheme for bootstrapping the
first administrator through a web page is either a default password somebody
forgets to change or a first-run window somebody misses; a person with a
keyboard on the machine is the honest gate.

**Uploads are reference material, not lessons.** A student can ask questions and
get answers grounded in them, cited as *"Land Management II — uploaded
reference: Proctor test"*. They never become lessons. Ethel does not write
teaching material, and a pack still has to be authored by a person who decided
how to teach something. That boundary is what keeps the four gates meaningful:
adding a textbook makes her able to answer more questions truthfully, not to
invent a lesson about them.

Readable formats are `.txt`, `.md`, `.html` and `.docx` — all extractable with
the standard library, so the zero-dependency promise survives. PDF is refused
with an instruction rather than an error, because it is the format everyone
tries first:

```
.pdf files cannot be read. PDF needs a library the core deliberately does not
have. Save or print it to .docx, .html or .txt and upload that.
```

Documents are split at headings, because a heading is the closest thing a
document has to a citation anchor — a student told "Chapter 3, Soil Acidity" can
find it; one told "page 41 of the upload" often cannot.

Uploads carry `reviewed_by: null` and show as **not reviewed**, exactly as an
unsigned pack does. Nobody has vouched for them.

---

## Not knowing, on purpose

Four gates stand between a question and an answer:

1. **Retrieval gate** — a passage must clear both a BM25 score floor and a
   term-coverage floor. Nothing clears it, nothing gets answered.
2. **Instruction** — the model gets only those passages, is told to answer from
   them alone and cite `[S1]`, and to emit `INSUFFICIENT_CONTEXT` if it cannot.
3. **Citation gate** — the reply must cite a real passage. Invented citation
   numbers are stripped; a reply left with none is discarded.
4. **Grounding gate** — enough of the reply's content words must actually occur
   in the passages. This catches a small model sliding from the pack into its
   own half-memory of a textbook.

Failing gates 2–4 falls back to **extractive mode**: the pack's own words,
quoted, with the source named. Failing gate 1 produces a refusal that says what
was searched, so the student knows the limit is the installed material and not
their question.

Marking is arithmetic and lexical, never done by the model. A model that grades
its own students eventually marks a wrong answer right to be agreeable.

---

## Teaching, not answering

Each lesson is compiled into an explicit plan of moves, shaped by the placement
result:

```
hook → segment 0 → check 0 → segment 1 → check 1 → worked example
     → practice ×n → apply → recap
```

* A **scaffolded** student gets micro-recaps and every check.
* A **fast** student who already tested strong gets the hook and some checks dropped.
* An **example-first** student gets the worked example moved ahead of the explanation.

A wrong answer does not reveal the answer. The engine looks up the pack's
diagnosis for **that specific wrong option**, delivers the repair, and re-asks
the same idea with the options rotated. Fail the mastery gate at the end and it
injects more practice instead of marking the lesson complete. Every answered
item is put on a spaced-repetition schedule.

---

## Socratic mode

Ordinary lesson mode presents a segment and then checks it. A student can read a
paragraph, nod along, and discover at the question that they followed none of
it. Socratic mode inverts the order: it takes a segment apart into single
sentences and, for each one, asks the student what they think **before** showing
them the sentence.

```
Ethel: We are looking at The three quantities. What do you already think you know?
You:   i think its about how electricity moves
Ethel: Good - hold on to that, and let us see how the material puts it.

Ethel: What do you expect comes next, and why?
You:   current is how much charge flows past a point each second, in amperes
Ethel: That is it. Here is how the material puts it:
       >> Current (I, measured in amperes, A) is the rate at which charge flows...

Ethel: Before I show you: what would you predict here?
You:   no idea
Ethel: No matter. Here is what it says, then I will ask you about it:
```

Three decisions carry it:

* **The opening question is never marked.** It activates what the student
  already believes, and there is no sentence it corresponds to. Scoring a good
  answer against whichever line happens to come first marks real understanding
  as wrong — which is exactly what the first build did.
* **Lead-ins are shown but not scored.** "Two elements do the work." is context,
  not substance; asking a student to have said *"three, quantities, describe"*
  is noise dressed as feedback. Sentences carrying fewer than six content words
  are glued to the sentence they introduce.
* **"No idea" is *skipped*, not wrong.** An honest admission deserves a gentler
  reply than a wrong guess, and the summary distinguishes them.

**No model is involved, and that is the point.** Lines come from the pack, probes
are fixed forms, and judging is lexical and stemmed — the same arithmetic that
marks everything else here. It cannot flatter a wrong answer to be agreeable, it
works with nothing installed, and it returns in milliseconds.

### The spoken loop

That last property is what makes voice usable. Measured on a 2-core i3:

```
Ethel speaks the probe   0.6s   (Piper, warm)
student answers          -
recognition returns      3.1s   (faster-whisper base.en, warm)
judgement                instant
```

**About four seconds of machine time per turn.** Put a language model in the
middle and the same turn costs 10–30 s, which is not a conversation. Keeping the
model out is not a limitation worked around; it is the design.

Voice mode needs an English recogniser (`models/asr/en`, 141 MB) and a voice.
Bemba measures ~20 s per leg and Lozi has no voice at all, so both fall back to
text and the button says why. Turn-taking is a fixed six-second window — proper
silence detection is real work and is not here yet.

---

## Curriculum: what is real and what is not

Everything in `content/catalog/` was read off the institutions' own websites on
**2026-08-26**, and each programme carries its source URL and a `coverage` flag.

| Institution | Programme | Coverage |
|---|---|---|
| UNZA | Bachelor of Laws (LLB) | **partial** — Years 2–4 with real course codes (LPR2920, LPU2940 …); Year 1 is in the School of Humanities and is not published |
| UNZA | BEng Civil and Environmental Engineering | **full** — Years 1–5 plus electives (titles; UNZA publishes no codes) |
| UNZA | BEng Electrical and Electronic Engineering | **full** — Years 1–5 with the EMP and ET tracks |
| UNZA | Bachelor of Agricultural Sciences | **none** — programme, duration and majors confirmed; no course list published |
| UNZA | BCS Computer Science / Software Engineering / Networking and Information Security | **none** — programmes and entry requirements confirmed; no course list published |
| CBU | BEng Electrical Engineering | **partial** — Years 2–5 with real course codes (EE 220, MA 310 …); Year 1 foundation not published |
| CBU | BSc Computer Science | **none** — confirmed, ACM-aligned, since 1996; no course list published |
| Kwame Nkrumah | Bachelor of Business Administration | **none** — confirmed; the university's own page reads "Details Coming Soon" |

All eight requested fields — law, business administration, agriculture, civil
engineering, computer science, software engineering, cybersecurity, electrical
engineering — are covered by at least one real programme. **A student picks
exactly one.** Switching later archives the old enrolment with its progress
rather than merging it.

Where coverage is `none`, the tutor says so to the student's face and offers the
programme's packs directly instead of inventing a syllabus. Import the real
syllabus with `tools/packtool.py` when you have it.

---

## Course packs

Three ship, all authored for this build and **none yet reviewed by a subject
teacher** — the tutor labels them as such in the sidebar:

| Pack | Course | Content |
|---|---|---|
| `cbu-ee220` | CBU **EE 220** Electrical & Electronics Principles I | 3 lessons — Ohm's law and power, series/parallel and the potential divider, Kirchhoff's laws. 12 practice items, 12 diagnostic items, 9 misconception repairs. |
| `unza-lpr2920` | UNZA **LPR2920** Law of Contract | 2 lessons — offer vs invitation to treat, acceptance and communication. Carries a jurisdiction note: the cases are the English authorities applied in Zambia, and the Zambian position must be confirmed with a lecturer. |
| `unza-agric-soils` | UNZA BAgSc (foundation, unmapped) | 1 lesson — soil acidity and nutrient availability. Explicitly not mapped to a published course code. |

Authoring:

```bash
python tools/packtool.py new my-pack --code "EE 320" --title "..." \
       --institution cbu --programme cbu-beng-electrical --year 3
python tools/packtool.py check      # validate, and report what a pack is missing
python tools/packtool.py sign       # stamp SHA-256 before distribution
```

The validator does not just check JSON. It warns when a lesson has no practice
items ("it will read as a lecture"), when a segment has no check-for-
understanding, and when a pack has no reviewer.

---

## The local model

Ethel is designed to be **useful with no model at all** — extractive mode, quoting
the pack — and she falls back to that automatically whenever the model is absent,
slow, or fails a gate. Adding a model changes how things are *said*, never what
is true.

```bash
winget install --id Ollama.Ollama -e
ollama pull llama3.2:1b
```

Ethel finds Ollama on `127.0.0.1:11434` — the one non-Ethel loopback address the
airlock permits. To move a model to a machine with no internet, pull it once
somewhere connected and copy `%USERPROFILE%\.ollama\models` across.

**A small model is the right choice here, not a compromise.** The model never
supplies a fact — it rewords passages the retrieval gate has already returned
from a course pack. It is held to the same four gates whatever its size, and if a
1B model drifts, the grounding gate discards its answer and quotes the pack
instead. What a bigger model buys is nicer phrasing; what it costs on a small
machine is a student who stops asking questions because answers take a minute.

### Auto-tuning

On start-up `ethel/hardware.py` measures RAM and cores and picks the context
window, thread count, answer-length cap and timeout to match, sorting the machine
into one of three tiers:

| Tier | Trigger | Context | Answer cap | Timeout | Model budget |
|---|---|---|---|---|---|
| constrained | < 6 GB RAM or ≤ 2 cores | 2048 | 320 tok | 240 s | ~1.6 GB |
| standard | < 12 GB or ≤ 4 cores | 4096 | 500 tok | 150 s | ~4.5 GB |
| roomy | anything larger | 8192 | 700 tok | 120 s | ~12 GB |

Note the timeout goes *up* as the machine gets weaker: a slow answer that arrives
still beats a timeout that drops back to quoting.

The System check page shows the tier, the measured hardware and every value that
was chosen, so nothing is hidden. Anything you write explicitly into
`data/config.json` wins over auto-tuning; set `llm.autotune` to `false` to turn it
off entirely, or `llm.backend` to `"none"` to force extractive mode.

### How the grounding gate was calibrated

`run.bat --modelcheck` tries to make the model fail and reports whether the gates
caught it. Running it against `llama3.2:1b` changed the design twice, and both
changes are worth knowing about:

**The blunt word-overlap ratio is a weak signal and its bar was far too high.**
Measured on real answers: a *correct, faithful* paraphrase of a passage overlaps
its source about 34% of the time, because good writing reaches for its own
connective vocabulary. An answer invented from nothing scored 18%. The original
bar of 45% was discarding correct teaching, so it now sits at 25% — between the
two — and is treated as a backstop rather than a quality measure.

**"Long words are specific" was wrong.** The sharp gate originally counted any
word of nine letters or more as a checkable claim. That flagged a perfect answer
for saying *mathematically*, *conductor* and *calculate* — ordinary vocabulary,
not assertions about the world. It now counts only the two things that actually
get fabricated: **anything containing a digit** (years, course codes, quantities)
and **proper nouns mid-sentence** (case names, statutes, people). On the same
answers, that separates cleanly: the paraphrase has no unsupported specifics at
all, while a hallucinated answer about "Hyde v Wrench in 1893" fails outright and
the tutor can name the exact terms it could not vouch for.

Both are locked down by regression tests in `tools/selftest.py`.

### What a 1B model is actually like

Honest observations from `--modelcheck` on `llama3.2:1b`:

* It **does** produce good, correctly-cited answers from the passages.
* It **does not** reliably say `INSUFFICIENT_CONTEXT` when asked something the
  passages do not cover — it invented a product-liability judgment for
  "Hyde v Wrench". Gate 4 caught it every time and Ethel quoted the pack instead.
  This is the design working: the model is not trusted to police itself.
* It occasionally tacks on a stray sentence like "The student followed the
  instructions." Cosmetic.
* It copies a sample sentence in the prompt verbatim if you give it one, which is
  why the citation format is now described rather than demonstrated.

---

## Voice

Male and female voices run through Piper, an offline neural TTS. Voice files live
in `models/piper/` and ship with the project:

| Language | Female | Male |
|---|---|---|
| English | `en_GB-jenny_dioco-medium.onnx` | `en_GB-alan-medium.onnx` |
| Bemba, Lozi | none — see below | none |

Setting it up is one script, and needs the internet **once**:

```bash
setup-voice.bat
```

That builds `.venv/` inside the project from `requirements-voice.txt`, fetches
any missing voice files, and verifies the result. Ethel finds `.venv` on her own
afterwards — **no configuration**. Delete `.venv` and the rest of the app runs
exactly as before; she simply stays silent and says so on the System check page.

This is the only part of Ethel that installs anything. The core — enrolment,
placement, teaching, retrieval, the four gates — is standard library only, and
stays that way: a machine with no network is the worst possible place to
discover a wheel is missing. Piper is the one thing not worth reimplementing.

If you already have Piper elsewhere, either shape works instead:

```json
{"speech": {"piper_exe": "C:\\piper\\piper.exe"}}
{"speech": {"piper_cmd": ["C:\\some\\.venv\\Scripts\\piper.exe"]}}
```

Ethel checks, in order: `piper_cmd`, `piper_exe`, her own `.venv`, a bundled
`piper/` folder, then `PATH`. If none has it, the System check page lists **every
path it tried**, so the fix is never a guess.

### Voices stay loaded

`ethel/voice_worker.py` runs under the voice venv and holds a voice in memory;
the server spawns one per voice and talks to it over a pipe. Measured on a
2-core i3-5010U, through the HTTP API:

```
speak 1 :  7.68s -> 3.9s audio  (1.99x realtime)   <- includes the model load
speak 2 :  0.61s -> 3.8s audio  (0.16x realtime)
speak 3 :  0.65s -> 3.8s audio  (0.17x realtime)
```

The earlier implementation shelled out per sentence and took **17–20 s every
time**, because a 63 MB model was read from disk on every call. Holding it open
makes warm requests roughly **30x faster** and takes voice from slower than
realtime to six times faster than realtime.

Resident models cost RAM, so how many stay loaded follows the hardware tier —
**1** on a constrained machine, 2 on standard, 3 on roomy — and idle voices are
evicted after 5 minutes to give the memory back. On a one-voice machine,
switching voices therefore costs a single reload:

```
after switching to Alan :  6.92s   <- one reload
next call               :  0.85s
```

`speech.max_resident_voices` and `speech.worker_idle_s` override the defaults.
The System check page shows which voices are currently resident, how many
requests each has served, and how long they have been idle.

If the worker cannot run — no venv, or only a standalone Piper binary — the old
one-shot path still works. It is just slow, and the System check page reports
`one-shot` instead of `resident` rather than leaving you to wonder.

### Switching voice and pace

Students choose from the voices actually installed, next to "Read this to me".
Both settings persist to their profile.

| Pace | Speed | For |
|---|---|---|
| Brisk | 0.9x | Revision, when the material is already known |
| Normal | 1.0x | The voice's natural pace |
| Measured | 1.2x | A first pass through new ideas |
| Slow | 1.45x | Difficult passages, or a second language |

Pace is Piper's `length_scale`, so it stretches phonemes without changing pitch.
That matters more here than it would in a consumer product: many students are
working in their second or third language, and a slower reading of a definition
is a real accommodation rather than a gimmick.

The picker hides itself when there is nothing to choose between — a dropdown
with one option is clutter. Voice gender is not recorded in Piper's metadata, so
it comes from a table in `speech.py`; unknown voices are labelled `unknown`
rather than guessed, and `speech.voice_gender` in `data/config.json` extends it.

### Bemba and Lozi

The two languages are **not** in the same position, and Ethel says so rather
than treating them alike:

| | Interface | Lesson text | Voice out (TTS) | Voice in (ASR) |
|---|---|---|---|---|
| Bemba | 60% translated | translator hand-off | `facebook/mms-tts-bem` — installed, **slow** | `zambezivoice/xls-r-300m-bem`, WER 0.32 |
| Lozi | 54% translated | translator hand-off | **nothing exists** — recordings only | `zambezivoice/xls-r-300m-loz`, accuracy unpublished |

### The Bemba voice is installed, and it is slow

`facebook/mms-tts-bem` (139 MB) is in `models/mms/bem/` and works. It needs
torch + transformers in the voice venv, which takes the venv from 156 MB to
**909 MB** — that is why it is opt-in.

Measured through the HTTP API on a 2-core i3 with 3.9 GB of RAM, alongside
Ollama:

```
cold start : 101s   (75s of it loading the model)
warm       :  18-21s for 5.5s of audio   (~3.4x realtime)
```

For comparison, the Piper English voice does 3.8s of audio in 0.61s — **roughly
thirty times faster**. MMS is a heavier architecture and this machine is already
short of memory, so it swaps. It is usable as a "Read this to me" button if the
student is patient, and it is not usable for anything conversational.

Two things to be clear about before relying on it:

* **Nobody has checked how it sounds.** I cannot evaluate Bemba pronunciation,
  and MMS voices are trained largely on read religious texts, which gives a
  particular register. A Bemba speaker should listen before this goes near a
  classroom.
* **There is still no Bemba lesson text.** The voice has nothing of its own to
  read until a translator fills in a pack. Until then it will read English words
  with a Bemba voice, which is why the interface disables it for untranslated
  material rather than letting it.

On better hardware, or with recorded audio instead, both problems go away.

### Speaking to Ethel

Zambezi Voice recognition is wired up: a student can say their question instead
of typing it. The microphone appears **only** when a model for their current
language is actually installed — offering to listen in a language Ethel cannot
hear would be the same dishonesty as offering a voice she lacks.

```bash
python tools/get_voice.py --asr bem    # 1203 MB
```

The Bemba model **is installed** (1213.7 MB). The others are not.

| Language | Model | Size | Published accuracy |
|---|---|---|---|
| Bemba | `zambezivoice/xls-r-300m-bem` | 1203 MB | **WER 0.32** — usable |
| Lozi | `zambezivoice/xls-r-300m-loz` | 1203 MB | not published |
| Chinyanja | `zambezivoice/xls-r-300m-nya` | 1203 MB | not published |
| Chitonga | `zambezivoice/xls-r-300m-toi` | 1203 MB | not published |

`ethel/asr_worker.py` holds a model resident the same way voices are held.

**Voices and recognisers share one memory budget** (`ethel/residency.py`, 1000 MB
on a constrained machine). Loading a 1.2 GB recogniser evicts every resident
voice, and asking for a voice again evicts the recogniser. On this hardware you
genuinely cannot have both, and swapping is far worse than reloading — a browser
demo of the microphone deadlocked this machine at 60 MB free RAM before the
budget existed. The System check page shows what is held and what was evicted for
what.

**Measured on this machine** (`run.bat --listencheck bem`), speaking a phrase
with the MMS voice and hearing it back:

```
said  : Mwapoleni mukwai natotela
heard : mwapoleni mukwai naatotela
cold  : 64.4s   (42.8s of it loading the model)
warm  : 20.6s for 6.0s of audio   (~3.4x realtime)
```

The transcription is essentially right — one duplicated letter in one word.
Word-level WER scores that as 0.33 because a word either matches or does not,
which flatters nothing and understates how close it was. **But 20 seconds to
hear six seconds of speech is too slow to talk to.** On this hardware speech
input is a demonstration, not a feature a student would use.

Two caveats on that test. It is *synthetic* speech: one speaker, no room, no
microphone, no background noise — far easier than a real student. And the model
ships a 5-gram language model that needs `pyctcdecode` and `kenlm`; where those
are missing the worker falls back to plain greedy decoding, which is what
produced the numbers above. Accuracy with the LM would be better. The worker
reports which decoder it used (`wav2vec2` vs `wav2vec2+lm`) rather than hiding
the difference.

A transcript is never treated as certainly right. Ethel puts it in the box for
the student to correct and says how often the model is wrong: *"Heard: … — check
it, about 32% of words come back wrong."* At WER 0.32 that is the honest framing;
presenting it as dictation would be a lie about a model that misses a word in
three.

The browser records **16 kHz mono WAV in JavaScript** rather than using
`MediaRecorder`, whose webm/opus output would need ffmpeg on the machine to
decode. Sixteen kilohertz mono is exactly what these models want, so encoding it
in the page costs nothing and removes a dependency from an offline install.

### Zambezi Voice

[Zambezi Voice](https://huggingface.co/zambezivoice) is a real Zambian language
project — Bemba, Lozi, Tonga and Nyanja, Apache 2.0 — and it is worth knowing
about. But **all twenty of its models are speech *recognition***: Whisper and
wav2vec2/XLS-R fine-tunes, `pipeline_tag: automatic-speech-recognition`. None of
them synthesise, so none can read a lesson aloud.

Where it would matter is the other direction: letting a student *speak* Bemba or
Lozi to Ethel. That is a genuinely valuable feature and a different one from
anything built here. Quality varies sharply — the Bemba model reports WER 0.32,
the Lozi Whisper one reports WER 83.1, which is too high to build on. The
findings are recorded in `LANGUAGE_TTS_REALITY` in `ethel/speech.py` so nobody
has to rediscover them.

I checked MMS directly and searched the Hub on 2026-08-27, and Zambezi Voice on
2026-08-28: there is no Lozi text-to-speech model in Piper, in Meta's MMS, in
Zambezi Voice, or anywhere else on Hugging Face. `get_voice.py
loz` prints that instead of a 404, because "does not exist" and "you typed it
wrong" are different problems for whoever is setting up a classroom.

**Text comes before voice.** A voice reads text, and a Bemba voice reading
English words with Bemba phonetics is exactly the confident nonsense the four
gates exist to prevent. So translations live in the pack, next to the material
they translate, with the same provenance the pack itself carries:

```bash
python tools/packtool.py translate-export unza-lpr2920 bem   # fill-in file
python tools/packtool.py translate-import translate-bem.json # fold it back
python tools/packtool.py translate-status --gaps             # what is missing
```

The translator never edits `pack.json`. They get a flat list of English strings
with an empty box beside each, and three rules that matter:

* **Blank is safe.** An untranslated string shows in English with a visible
  "not translated yet" mark. A guess is not safe, because a student cannot tell
  a guess from a fact.
* **Multiple-choice answers are never translated** - the answer is an index, so
  translating options cannot change which one is correct. The importer rejects
  a translated option list whose length differs from the English.
* **Nothing is anonymous.** `provenance.translator` is required; import refuses
  without it. Until a second person fills in `reviewed_by`, the translation is
  marked unreviewed wherever it appears.

Retrieval is per-language: passages carry their language and a Bemba question is
never answered out of English passages that happen to share a proper noun.
Untranslated passages are skipped in the translated index rather than quietly
falling back.

**Recorded audio beats synthesis in every language**, and for Lozi it is the
only option. A pack can ship a recording per segment:

```jsonc
"translations": {"loz": {"lessons": {"l1": {
    "audio": {"hook": "audio/loz/l1-hook.wav",
              "segments": {"s1": "audio/loz/l1-s1.wav"}}}}}}
```

`/api/speak` plays the recording when one exists and only synthesises otherwise.
When no voice can read the current language, the picker says so and the "Read
this to me" button is disabled rather than reading Bemba in English.

**Bemba and Lozi have no Piper voice.** Rather than read Bemba text with an
English voice — which produces confident-sounding nonsense, exactly the fakery
this project exists to avoid — the tutor reports the voice as unavailable and
stays silent in that language. Two offline routes exist for supplying one: Meta's
MMS-TTS release covers a large number of African languages and can be converted
to run locally, or a pack can ship human-recorded audio per lesson. Neither is
wired up; the System check page says so.

---

## Languages

The interface ships in English (complete), Ichibemba and Silozi (draft). Both
draft locales are deliberately small: only strings a native speaker would confirm
on sight are filled in, and every missing key falls back to English rather than
being machine-translated. `ethel/locales/bem.json` and `loz.json` carry
instructions for a translator to complete them.

**Lesson content is a different problem and is not solved here.** A 4B model
asked to produce Bemba technical prose invents words. Bemba and Lozi lesson text
has to be written or checked by a person; the pack format has per-language fields
ready for it.

---

## Honest limitations

* The three course packs are a demonstration of the format, authored for this
  build. Real deployment needs real syllabi and a subject teacher's review.
* Five of the eight fields have no published course structure online. The tutor
  handles this correctly — by saying so — but the syllabi still have to be
  obtained from the institutions.
* The PIN is a local convenience lock so a shared machine doesn't mix up two
  students' progress. It is PBKDF2-hashed, but anyone with the disk can reset it.
  It is not authentication and is not described as such anywhere in the UI.
* Retrieval is BM25 only. It is strong on the exact terminology students search
  for and needs no embedding model, but it will miss a question phrased with no
  overlapping vocabulary — in which case the tutor refuses rather than guesses,
  which is the safe failure.
* **Speed on very weak hardware is the real limitation.** On a 2015 i3 with 3.9 GB
  of RAM, a grounded answer took 27–44 seconds with almost no free memory. That is
  usable but slow enough that a student notices. Closing other programs helps most;
  `ollama pull gemma3:1b` and setting `llm.model` helps next. Extractive mode is
  instant and always available as the floor.
* The UI deliberately uses a system font stack. A machine with no internet
  cannot fetch a webfont, so anything referencing Google Fonts would silently
  fall back anyway. Google Sans is named first for the rare machine that has it;
  Roboto and Segoe carry everyone else.
* Turn-taking in spoken Socratic mode is a fixed six-second window, not silence
  detection. Proper turn-taking is real work and is not here yet.

---

## What it looks like

The interface follows Gemini: a tinted rail on the left instead of a top bar,
flat surfaces separated by tint rather than shadow, generously rounded geometry,
and one gradient — blue through violet to rose — reserved for identity. The
wordmark uses it, the greeting uses it, the bar that fills as you work uses it,
and so does the small dot in front of anything Ethel says. Nothing else does.

Dark mode follows the operating system. There is no toggle, which is one fewer
preference to store per student on a shared machine.

It expects a keyboard. `1`–`9` pick a multiple-choice answer and `Enter` sends
it; `/` jumps to the question box; `Esc` leaves Socratic mode. The question box
grows as you type, `Shift+Enter` breaks a line, and a half-typed question
survives a trip to a lesson and back. Reference material can be dragged straight
onto the source it belongs to.

One piece of it is an apology for the hardware. A grounded answer takes about
forty-five seconds on the 2015 i3 this was built on, so instead of showing three
dots for three quarters of a minute, Ethel says what she is doing at seven
seconds, at sixteen, and at thirty-two — the last one naming the machine rather
than letting the student assume they broke something.

---

## Layout

```
Ethel/
  run.bat  serve.py            launchers
  tutor/
    airlock.py                 the offline guarantee, enforced
    server.py                  stdlib HTTP server + JSON API
    hardware.py                measures the machine, sizes the model to it
    llm.py  speech.py  i18n.py optional model, voice, interface languages
    core/
      catalog.py               institutions and programmes
      library.py               the offline content hub: resolve, verify, install
      pack.py                  the course pack format
      retrieval.py             BM25 + the coverage gate
      grounding.py             grounded answering and the refusal
      grading.py               deterministic marking
      placement.py             adaptive diagnostic + learner profile
      pedagogy.py              the teaching engine
      profile.py               student records, mastery, spaced repetition
    static/                    the whole UI, three files
    locales/                   en, bem, loz
  content/
    catalog/                   institutions.json, programmes.json
    library/                   course packs, signed
  data/                        students, installed packs, config (created at runtime)
  tools/
    packtool.py                author, validate and sign packs
    selftest.py                end-to-end test
    modelcheck.py              does the local model obey the gates, and how fast
    listencheck.py             does speech input work, and how accurately
    get_voice.py               fetch voices and recognition models (the one online tool)
```
