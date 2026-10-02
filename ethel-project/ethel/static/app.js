/* Ethel - front end.
   Vanilla JS on purpose: no build step, no package manager, nothing to fetch.
   The whole app is three files served from the machine itself. */

const S = { me: null, view: null, enrol: {}, plan: null, placement: {}, step: null };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

async function api(path, body) {
  const opts = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch { data = { error: 'Unreadable reply from the tutor.' }; }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5200);
}

function show(view) {
  S.view = view;
  document.querySelectorAll('.view').forEach(v => { v.hidden = v.id !== 'view-' + view; });
  document.querySelectorAll('.navbtn').forEach(b => {
    b.classList.toggle('is-active', b.dataset.view === view);
  });
  // On a phone the rail covers the page, so navigating has to close it.
  // Leaving it open would hide the thing the student just asked for.
  if (window.matchMedia('(max-width: 760px)').matches) document.body.classList.add('rail-tight');
  window.scrollTo(0, 0);
  focusFirstField(view);
}

/* Put the cursor where the student is about to type. Which field that is
   depends on the view, and getting it wrong is worse than doing nothing - so
   this only fires for views with one obvious answer, and never on a touch
   screen, where raising the keyboard hides half the page. */
function focusFirstField(view) {
  if (matchMedia('(hover: none)').matches) return;
  const target = { login: '#loginForm input[name=id]', ask: '#askInput' }[view];
  if (target) setTimeout(() => { const n = $(target); if (n) n.focus(); }, 60);
}

/* ---------------- boot ---------------- */

async function boot() {
  const b = await api('/api/bootstrap');
  const pill = $('#offlinePill');
  if (b.offline.airlock) {
    pill.className = 'pill offline';
    pill.textContent = 'Offline';
    pill.title = 'The network airlock is engaged: this app cannot reach the internet.'
      + (b.offline.blocked_attempts ? ` ${b.offline.blocked_attempts} attempt(s) blocked.` : '');
  } else {
    pill.className = 'pill online';
    pill.textContent = 'Airlock off';
    pill.title = 'The network airlock is NOT engaged. Start the app with run.bat.';
  }

  const list = $('#studentList');
  list.innerHTML = '';
  b.students.forEach(s => {
    const btn = el('button', null, s.name);
    btn.onclick = () => {
      $('#loginForm').elements.id.value = s.id;
      $('#loginForm').elements.pin.focus();
    };
    list.appendChild(btn);
  });

  if (b.signed_in) { await afterLogin(); } else { show('login'); }
}

/* ---------------- login ---------------- */

document.querySelectorAll('.tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('is-active', t === tab));
    $('#nameField').hidden = tab.dataset.tab !== 'new';
  };
});

$('#loginForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const mode = $('.tab.is-active').dataset.tab;
  try {
    await api('/api/login', { mode, id: f.id.value, name: f.name.value, pin: f.pin.value });
    f.pin.value = '';
    await afterLogin();
  } catch (err) { toast(err.message, true); }
};

$('#signout').onclick = async () => {
  await api('/api/logout', {});
  S.me = null;
  $('#nav').hidden = true;
  $('#signout').hidden = true;
  $('#newAsk').hidden = true;
  $('#who').textContent = '';
  $('#avatar').hidden = true;
  $('#askLog').innerHTML = '';
  $('#askGreeting').hidden = false;
  await boot();
};

async function afterLogin() {
  const { student } = await api('/api/me');
  S.me = student;
  $('#who').textContent = student.name;
  paintAvatar(student.name);
  $('#greetLine').textContent = 'Hello, ' + (student.name || '').split(/\s+/)[0];
  $('#newAsk').hidden = false;
  $('#signout').hidden = false;
  $('#nav').hidden = !student.enrolment && student.role !== 'admin';
  $('#navAdmin').hidden = student.role !== 'admin';
  VOICES = null;
  refreshMics();
  route();
}

function route() {
  const m = S.me;
  if (!m.enrolment) return renderEnrol();
  if (!m.installed_packs.length) return renderInstall();
  if (!m.placement_done) return renderPlacement();
  return renderLearn();
}

document.querySelectorAll('.navbtn').forEach(b => {
  b.onclick = () => {
    if (b.dataset.view === 'learn') renderLearn();
    else if (b.dataset.view === 'ask') openAsk();
    else if (b.dataset.view === 'admin') renderAdmin();
    else renderDoctor();
  };
});

/* ---------------- enrolment ---------------- */

async function renderEnrol() {
  show('enrol');
  S.enrol = {};
  ['fieldStep', 'progStep', 'yearStep', 'trackWrap'].forEach(id => { $('#' + id).hidden = true; });
  const { institutions } = await api('/api/catalog');
  const box = $('#instChoices');
  box.innerHTML = '';
  institutions.forEach(i => {
    const b = el('button', 'choice');
    b.appendChild(el('b', null, i.name));
    b.appendChild(el('small', null, `${i.short} - ${i.city}`));
    b.onclick = () => { markActive(box, b); pickInstitution(i.id); };
    box.appendChild(b);
  });
}

function markActive(container, btn) {
  container.querySelectorAll('.choice').forEach(c => c.classList.toggle('is-active', c === btn));
}

async function pickInstitution(id) {
  S.enrol = { institution: id };
  ['progStep', 'yearStep', 'trackWrap'].forEach(i => { $('#' + i).hidden = true; });
  const { fields } = await api('/api/catalog?institution=' + encodeURIComponent(id));
  const box = $('#fieldChoices');
  box.innerHTML = '';
  fields.forEach(f => {
    const b = el('button', 'choice');
    b.appendChild(el('b', null, f.name));
    if (!f.available) {
      b.disabled = true;
      b.appendChild(el('small', null, 'Not catalogued at this institution'));
    }
    b.onclick = () => { markActive(box, b); pickField(f.id); };
    box.appendChild(b);
  });
  $('#fieldStep').hidden = false;
}

async function pickField(field) {
  S.enrol.field = field;
  $('#yearStep').hidden = true;
  const { programmes } = await api(
    `/api/catalog?institution=${encodeURIComponent(S.enrol.institution)}&field=${encodeURIComponent(field)}`);
  const box = $('#progChoices');
  box.innerHTML = '';
  programmes.forEach(p => {
    const b = el('button', 'choice');
    b.appendChild(el('b', null, p.name));
    const badge = el('span', 'badge ' + p.coverage,
      p.coverage === 'full' ? 'Full syllabus'
        : p.coverage === 'partial' ? 'Syllabus partly published' : 'Syllabus not published');
    b.appendChild(badge);
    const bits = [];
    if (p.duration_years) bits.push(p.duration_years + ' years');
    if (p.years_published.length) bits.push('years ' + p.years_published.join(', ') + ' published');
    if (p.notes && p.notes.length) bits.push(p.notes[0]);
    b.appendChild(el('small', null, bits.join(' - ')));
    b.onclick = () => { markActive(box, b); pickProgramme(p); };
    box.appendChild(b);
  });
  $('#progStep').hidden = false;
}

function pickProgramme(p) {
  S.enrol.programme = p.id;
  S.enrol.prog = p;
  S.enrol.year = null;
  S.enrol.track = null;
  const years = p.duration_years || 5;
  const box = $('#yearChoices');
  box.innerHTML = '';
  for (let y = 1; y <= years; y++) {
    const b = el('button', 'choice');
    b.appendChild(el('b', null, 'Year ' + y));
    if (p.years_published.length && !p.years_published.includes(y)) {
      b.appendChild(el('small', null, 'course list not published'));
    }
    b.onclick = () => { markActive(box, b); S.enrol.year = y; $('#enrolBtn').disabled = false; };
    box.appendChild(b);
  }
  const tw = $('#trackWrap');
  if (p.tracks && p.tracks.length) {
    const tb = $('#trackChoices');
    tb.innerHTML = '';
    p.tracks.forEach(t => {
      const b = el('button', 'choice');
      b.appendChild(el('b', null, t.name));
      b.onclick = () => { markActive(tb, b); S.enrol.track = t.id; };
      tb.appendChild(b);
    });
    tw.hidden = false;
  } else { tw.hidden = true; }
  $('#enrolBtn').disabled = true;
  $('#yearStep').hidden = false;
}

$('#enrolBtn').onclick = async () => {
  try {
    const r = await api('/api/enrol', {
      programme: S.enrol.programme, year: S.enrol.year, track: S.enrol.track,
    });
    S.me = r.student;
    $('#nav').hidden = false;
    renderInstall(r.install_plan);
  } catch (err) { toast(err.message, true); }
};

/* ---------------- install ---------------- */

async function renderInstall(plan) {
  show('install');
  S.plan = plan || await api('/api/install-plan');
  const p = S.plan;
  const body = $('#installBody');
  body.innerHTML = '';

  const head = el('div');
  head.appendChild(el('h2', null, `${p.programme.name} - Year ${p.year}`));
  if (!p.syllabus_known) {
    const note = el('p', 'hint');
    note.textContent = `${p.programme.name} does not publish a year-by-year course list, `
      + `so I cannot tell you which courses you should be taking. `
      + `I will not make one up. Here is the material for this programme that is on this machine.`;
    head.appendChild(note);
  }
  body.appendChild(head);

  const installable = [...p.matched, ...(p.syllabus_known ? [] : p.also_available)];

  if (installable.length) {
    body.appendChild(el('h3', null, 'Ready to install'));
    const ul = el('ul', 'packlist');
    installable.forEach(m => {
      const li = el('li');
      li.appendChild(el('code', null, m.code || '-'));
      li.appendChild(el('span', null, m.title));
      const why = el('span', 'why', `${m.lessons} lesson${m.lessons === 1 ? '' : 's'} - signature ${m.integrity}`);
      li.appendChild(why);
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  if (p.missing.length) {
    body.appendChild(el('h3', null, 'On your syllabus, but not on this machine'));
    const ul = el('ul', 'packlist');
    p.missing.forEach(m => {
      const li = el('li', 'miss');
      li.appendChild(el('code', null, m.code || '-'));
      li.appendChild(el('span', null, m.title));
      li.appendChild(el('span', 'why', m.reason));
      ul.appendChild(li);
    });
    body.appendChild(ul);
    const note = el('p', 'hint');
    note.textContent = 'Whoever set up this machine needs to add these course packs. '
      + 'Until then I will tell you honestly that I cannot teach them.';
    body.appendChild(note);
  }

  if (p.programme.source_url) {
    const src = el('p', 'hint');
    src.textContent = 'Syllabus source: ' + p.programme.source_url;
    body.appendChild(src);
  }

  $('#installBtn').disabled = !installable.length;
  $('#installBtn').onclick = async () => {
    try {
      const r = await api('/api/install', { pack_ids: installable.map(m => m.pack_id) });
      if (r.refused.length) toast(`${r.refused.length} pack(s) refused: ${r.refused[0].reason}`, true);
      toast(`Installed ${r.installed.length} course pack(s), ${r.lessons_available} lessons ready.`);
      S.me = (await api('/api/me')).student;
      renderPlacement();
    } catch (err) { toast(err.message, true); }
  };
  $('#skipInstallBtn').onclick = () => renderLearn();
}

/* ---------------- placement ---------------- */

function renderPlacement() {
  show('placement');
  $('#placementIntro').hidden = false;
  $('#placementQuiz').hidden = true;
  $('#placementPrefs').hidden = true;
  $('#placementResult').hidden = true;
}

$('#placementBegin').onclick = async () => {
  try {
    const r = await api('/api/placement/start', {});
    $('#placementIntro').hidden = true;
    $('#placementQuiz').hidden = false;
    showPlacementItem(r.item);
  } catch (err) {
    toast(err.message, true);
    askPreferences();
  }
};

function showPlacementItem(item) {
  const box = $('#placementQuiz');
  box.innerHTML = '';
  if (!item) { askPreferences(); return; }

  const prog = el('div', 'progress');
  prog.appendChild(el('span', null, `Question ${item.asked} of ${item.total}`));
  const bar = el('div', 'bar');
  const fill = el('i');
  fill.style.width = Math.round(100 * (item.asked - 1) / item.total) + '%';
  bar.appendChild(fill);
  prog.appendChild(bar);
  box.appendChild(prog);

  box.appendChild(el('p', 'qprompt', item.prompt));
  let chosen = null;

  const submit = async (response) => {
    try {
      const r = await api('/api/placement/answer', { item_id: item.id, response });
      if (r.item) showPlacementItem(r.item); else askPreferences();
    } catch (err) { toast(err.message, true); }
  };

  if (item.type === 'mcq') {
    box.appendChild(optionList(item.options,
      (i) => { chosen = i; },
      () => { if (chosen !== null) submit(chosen); }));
  } else {
    const inp = el('input');
    inp.placeholder = 'Your answer';
    inp.onkeydown = (e) => { if (e.key === 'Enter') submit(inp.value); };
    box.appendChild(inp);
    setTimeout(() => inp.focus(), 40);
    chosen = () => inp.value;
  }

  const acts = el('div', 'actions');
  const go = el('button', 'primary', 'Answer');
  go.onclick = () => submit(typeof chosen === 'function' ? chosen() : chosen);
  const skip = el('button', 'ghost', "I don't know this one");
  skip.onclick = () => submit(item.type === 'mcq' ? -1 : '');
  acts.appendChild(go);
  acts.appendChild(skip);
  box.appendChild(acts);
}

async function askPreferences() {
  $('#placementQuiz').hidden = true;
  const { questions } = await api('/api/placement/preferences');
  const box = $('#placementPrefs');
  box.hidden = false;
  box.innerHTML = '';
  box.appendChild(el('h1', null, 'How should I teach you?'));
  const answers = {};
  questions.forEach(q => {
    const wrap = el('div', 'prefblock');
    wrap.appendChild(el('h3', null, q.prompt));
    if (q.help) wrap.appendChild(el('p', 'hint', q.help));
    const grid = el('div', 'choices');
    q.options.forEach(o => {
      const b = el('button', 'choice');
      b.appendChild(el('b', null, o.label));
      if (o.available === false) {
        b.appendChild(el('small', null, 'interface only - no lesson text in this language yet'));
      }
      b.onclick = () => { markActive(grid, b); answers[q.id] = o.value; };
      grid.appendChild(b);
    });
    wrap.appendChild(grid);
    box.appendChild(wrap);
  });
  const acts = el('div', 'actions');
  const done = el('button', 'primary', 'Finish');
  done.onclick = async () => {
    try {
      const r = await api('/api/placement/finish', { preferences: answers });
      S.me = r.student;
      showPlacementResult(r.summary);
    } catch (err) { toast(err.message, true); }
  };
  acts.appendChild(done);
  box.appendChild(acts);
}

function showPlacementResult(summary) {
  $('#placementPrefs').hidden = true;
  const box = $('#placementResult');
  box.hidden = false;
  box.innerHTML = '';
  box.appendChild(el('h1', null, "Here's how I'll teach you"));
  if (summary.answered) {
    box.appendChild(el('p', 'lede',
      `You answered ${summary.correct} of ${summary.answered} correctly. `
      + `That is not a grade - it is how I decide where to start.`));
  }
  const ul = el('ul');
  summary.teaching_plan.forEach(line => ul.appendChild(el('li', null, line)));
  box.appendChild(ul);
  const acts = el('div', 'actions');
  const go = el('button', 'primary', 'Start learning');
  go.onclick = () => renderLearn();
  acts.appendChild(go);
  box.appendChild(acts);
}

/* ---------------- learn ---------------- */

async function renderLearn() {
  show('learn');
  const data = await api('/api/study');
  const box = $('#planBody');
  box.innerHTML = '';
  if (!data.lessons.length) {
    box.appendChild(el('p', 'hint', 'No lessons installed yet.'));
  }
  data.lessons.forEach(l => {
    const b = el('button', 'planrow');
    b.dataset.lesson = l.lesson_id;   // so the mastery bars can be refreshed in place
    const top = el('div', 'top');
    top.appendChild(el('span', 'code', l.course_code || l.course_title));
    const statusLabel = l.blocked_by.length ? 'Locked'
      : l.status === 'done' ? 'Mastered'
        : l.status === 'in_progress' ? 'In progress'
          : l.mode === 'review' ? 'Review' : 'Not started';
    top.appendChild(el('span', 'sub', statusLabel));
    b.appendChild(top);
    b.appendChild(el('div', 'title', l.title));
    if (l.blocked_by.length) {
      b.appendChild(el('div', 'sub', 'Needs ' + l.blocked_by.join(', ') + ' first'));
    } else if (l.minutes) {
      b.appendChild(el('div', 'sub', `about ${l.minutes} minutes`));
    }
    const m = el('div', 'mastery');
    const fill = el('i');
    fill.style.width = Math.round(l.mastery * 100) + '%';
    m.appendChild(fill);
    b.appendChild(m);
    if (l.blocked_by.length) {
      b.disabled = true;              // :disabled carries the dimming in CSS
      b.title = 'Master ' + l.blocked_by.join(', ') + ' first.';
    } else {
      b.onclick = () => {
        box.querySelectorAll('.planrow').forEach(r => r.classList.toggle('is-active', r === b));
        startLesson(l.pack_id, l.lesson_id, l.status === 'done');
      };
    }
    box.appendChild(b);
  });

  const meta = $('#courseMeta');
  meta.innerHTML = '';
  data.courses.forEach(c => {
    const line = el('div', 'course');
    line.appendChild(el('b', null, `${c.code || c.title}: ${c.lessons} lessons`));
    line.appendChild(el('div', null,
      c.reviewed_by ? `Reviewed by ${c.reviewed_by}.`
        : 'Not yet reviewed by a subject teacher.'));
    const details = document.createElement('details');
    details.appendChild(el('summary', null, 'Where this material came from'));
    [c.syllabus_reference, c.jurisdiction_note, c.field_note]
      .filter(Boolean)
      .forEach(t => details.appendChild(el('p', null, t)));
    line.appendChild(details);
    meta.appendChild(line);
  });
  if (data.due_reviews) {
    meta.appendChild(el('div', null, `${data.due_reviews} item(s) due for review.`));
  }
}

async function startLesson(pack_id, lesson_id, restart) {
  try {
    const r = await api('/api/lesson/start', { pack_id, lesson_id, restart: !!restart });
    drawStep(r.step);
  } catch (err) { toast(err.message, true); }
}

function drawStep(step) {
  S.step = step;
  $('#lessonEmpty').hidden = true;
  const box = $('#lessonBody');
  box.hidden = false;
  box.innerHTML = '';

  if (step.kind === 'idle') { $('#lessonEmpty').hidden = false; box.hidden = true; return; }

  if (step.progress) {
    const prog = el('div', 'progress');
    prog.appendChild(el('span', null, `${step.progress.course} - ${step.progress.lesson}`));
    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = Math.round(100 * step.progress.step / step.progress.total) + '%';
    bar.appendChild(fill);
    prog.appendChild(bar);
    prog.appendChild(el('span', null, `${step.progress.right}/${step.progress.asked} right`));
    box.appendChild(prog);
  }

  if (step.feedback) {
    const f = el('div', 'feedback ' + (step.feedback.correct ? 'good' : 'bad'));
    f.appendChild(el('b', null, step.feedback.correct ? 'Correct' : 'Not quite'));
    if (step.feedback.message) f.appendChild(el('div', null, step.feedback.message));
    if (step.feedback.explanation) f.appendChild(el('div', null, step.feedback.explanation));
    box.appendChild(f);
  }

  box.appendChild(el('h2', null, step.title || ''));

  if (step.kind === 'say' || step.kind === 'done') {
    box.appendChild(el('div', 'saybody', step.body || ''));
    if (step.untranslated) box.appendChild(untranslatedNote(step));
    const acts = el('div', 'actions');
    if (step.kind === 'say') {
      const next = el('button', 'primary', 'Continue');
      next.onclick = () => advance(null);
      acts.appendChild(next);
      const soc = el('button', 'ghost', 'Work through it line by line');
      soc.title = 'Socratic mode: I ask before I tell, one sentence at a time.';
      soc.onclick = () => startSocratic(step.pack_id, step.lesson_id, step.segment_id);
      acts.appendChild(soc);
      const speak = el('button', 'ghost', 'Read this to me');
      speak.onclick = () => readAloud(step.body, speak, step);
      acts.appendChild(speak);
      loadVoices().then(v => {
        const can = v.available.some(a => a.language_family === v.current.language);
        speak.disabled = !can;
        if (!can) speak.title = 'There is no voice for this language on this machine.';
      });
      acts.appendChild(voicePicker());
    } else {
      const back = el('button', 'primary', 'Back to my plan');
      back.onclick = () => renderLearn();
      acts.appendChild(back);
      const again = el('button', 'ghost', 'Do this lesson again');
      again.onclick = () => startLesson(step.pack_id, step.lesson_id, true);
      acts.appendChild(again);
    }
    box.appendChild(acts);
  }

  if (step.kind === 'ask') {
    const item = step.item;
    box.appendChild(el('p', 'qprompt', item.prompt));
    let chosen = null;
    if (item.type === 'mcq') {
      box.appendChild(optionList(item.options,
        (i) => { chosen = i; },
        () => { if (chosen !== null) advance(chosen); }));
    } else if (item.type === 'numeric') {
      const inp = el('input');
      inp.placeholder = item.unit ? `Your answer in ${item.unit}` : 'Your answer';
      inp.onkeydown = (e) => { if (e.key === 'Enter') advance(inp.value); };
      box.appendChild(inp);
      setTimeout(() => inp.focus(), 40);
      chosen = () => inp.value;
    } else {
      const ta = document.createElement('textarea');
      ta.rows = 4;
      ta.className = 'wide';
      ta.placeholder = item.placeholder || 'Your answer in your own words';
      box.appendChild(ta);
      setTimeout(() => ta.focus(), 40);
      chosen = () => ta.value;
    }
    const acts = el('div', 'actions');
    const go = el('button', 'primary', 'Answer');
    go.onclick = () => advance(typeof chosen === 'function' ? chosen() : chosen);
    acts.appendChild(go);
    box.appendChild(acts);
  }

  if (step.sources && step.sources.length) {
    const s = el('div', 'sources');
    s.textContent = 'From your course material: '
      + step.sources.map(x => `${x.course} - ${x.lesson}`).join('; ');
    box.appendChild(s);
  }
}

async function advance(response) {
  try {
    const r = await api('/api/lesson/advance', { response });
    drawStep(r.step);
    // Finishing a lesson changes the mastery bars in the plan beside it. They
    // used to sit stale until the student navigated away and back, which read
    // as the work not having counted.
    if (r.step && r.step.kind === 'done') refreshPlan();
  } catch (err) { toast(err.message, true); }
}

let audio;
async function readAloud(text, btn, step) {
  btn.disabled = true;
  btn.textContent = 'Reading...';
  try {
    const res = await fetch('/api/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Naming the source lets the server play a human recording instead of
      // synthesising, when the pack ships one for this language.
      body: JSON.stringify(step && step.pack_id
        ? { text, pack_id: step.pack_id, lesson_id: step.lesson_id,
            audio_key: step.audio_key || 'hook' }
        : { text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Voice is not available on this machine.');
    }
    const blob = await res.blob();
    if (audio) audio.pause();
    audio = new Audio(URL.createObjectURL(blob));
    audio.play();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Read this to me';
  }
}

function untranslatedNote(step) {
  /* Ethel shows English when nobody has translated a passage yet. Saying so is
     the whole point - a student must never have to guess whether the words in
     front of them were checked by a person. */
  const n = el('p', 'untranslated');
  n.textContent = step.untranslated_note
    || 'This part has not been translated yet, so it is shown in English.';
  return n;
}

/* ---------------- administration ----------------
   Uploading reference material. One file per request with the name in a header:
   multipart parsing is a lot of code and a classic path-traversal hazard, and
   the server sanitises the name anyway. Folder uploads work because the file
   picker can return a directory tree, which is just many files. */

async function renderAdmin() {
  show('admin');
  let d;
  try { d = await api('/api/admin/sources'); }
  catch (err) { toast(err.message, true); return; }

  $('#adminExplain').textContent = d.explain;

  const sel = $('#sourceProgramme');
  if (!sel.options.length) {
    const any = document.createElement('option');
    any.value = ''; any.textContent = 'Any programme';
    sel.appendChild(any);
    d.programmes.forEach(p => {
      const o = document.createElement('option');
      o.value = p.id; o.textContent = `${p.name} (${p.institution.toUpperCase()})`;
      sel.appendChild(o);
    });
  }

  const list = $('#sourceList');
  list.innerHTML = '';
  const head = el('h3', null,
    `${d.summary.sources} source(s), ${d.summary.files} file(s), ${d.summary.words.toLocaleString()} words`);
  list.appendChild(head);
  if (!d.sources.length) {
    list.appendChild(el('p', 'hint',
      'Nothing uploaded yet. Create a source above, then add files to it. '
      + 'Readable formats: ' + d.summary.supported_formats.join(', ') + '.'));
  }
  d.sources.forEach(src => list.appendChild(sourceCard(src, d.summary)));
}

function sourceCard(src, summary) {
  const card = el('div', 'source');
  card.dataset.id = src.id;          // so a dropped file knows where to go
  const top = el('div', 'top');
  top.appendChild(el('b', null, src.title));
  const badge = el('span', 'badge ' + (src.reviewed_by ? 'full' : 'none'),
    src.reviewed_by ? 'reviewed' : 'not reviewed');
  top.appendChild(badge);
  card.appendChild(top);

  const bits = [src.programme || 'any programme'];
  if (src.course_code) bits.push(src.course_code);
  if (src.year) bits.push('year ' + src.year);
  bits.push('added ' + src.uploaded_on + ' by ' + src.uploaded_by);
  card.appendChild(el('small', null, bits.join(' · ')));

  if (src.files.length) {
    const ul = el('ul', 'files');
    src.files.forEach(f => {
      const li = el('li');
      li.appendChild(el('span', 'fname', f.name));
      li.appendChild(el('span', 'fmeta',
        `${f.format}, ${f.sections} section(s), ${f.words.toLocaleString()} words`));
      const del = el('button', 'linkbtn', 'remove');
      del.onclick = async () => {
        try {
          await api('/api/admin/sources/delete', { id: src.id, file: f.name });
          toast(`Removed ${f.name}`); renderAdmin();
        } catch (err) { toast(err.message, true); }
      };
      li.appendChild(del);
      ul.appendChild(li);
    });
    card.appendChild(ul);
  } else {
    card.appendChild(el('p', 'hint', 'No files yet.'));
  }

  const acts = el('div', 'actions');
  const pick = document.createElement('input');
  pick.type = 'file'; pick.multiple = true; pick.hidden = true;
  pick.accept = (summary.supported_formats || []).join(',');
  pick.onchange = () => uploadTo(src.id, [...pick.files]);
  const addFiles = el('button', 'ghost', 'Add files');
  addFiles.onclick = () => pick.click();

  const pickDir = document.createElement('input');
  pickDir.type = 'file'; pickDir.multiple = true; pickDir.hidden = true;
  pickDir.webkitdirectory = true;
  pickDir.onchange = () => uploadTo(src.id, [...pickDir.files]);
  const addFolder = el('button', 'ghost', 'Add a folder');
  addFolder.onclick = () => pickDir.click();

  const delSrc = el('button', 'linkbtn', 'delete source');
  delSrc.onclick = async () => {
    if (!confirm(`Delete "${src.title}" and all its files?`)) return;
    try {
      await api('/api/admin/sources/delete', { id: src.id });
      toast('Source deleted'); renderAdmin();
    } catch (err) { toast(err.message, true); }
  };

  acts.append(addFiles, addFolder, pick, pickDir, delSrc);
  card.appendChild(acts);
  return card;
}

async function uploadTo(sourceId, files) {
  if (!files.length) return;
  let ok = 0;
  const skipped = [];
  for (const f of files) {
    try {
      const res = await fetch('/api/admin/upload', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Ethel-Source': sourceId,
          // A folder upload gives relative paths; the server keeps only the
          // last component, but send what the browser gave us.
          'X-Ethel-Filename': f.webkitRelativePath || f.name,
        },
        body: f,
      });
      const data = await res.json();
      if (!res.ok) { skipped.push(`${f.name}: ${data.error}`); continue; }
      ok++;
    } catch (err) { skipped.push(`${f.name}: ${err.message}`); }
  }
  toast(skipped.length
    ? `${ok} added, ${skipped.length} skipped. ${skipped[0]}`
    : `${ok} file(s) added`, skipped.length > 0);
  renderAdmin();
}

$('#sourceForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  try {
    await api('/api/admin/sources/create', {
      title: f.title.value,
      programme: f.programme.value,
      year: f.year.value ? Number(f.year.value) : null,
      course_code: f.course_code.value,
    });
    e.target.reset();
    toast('Source created. Add files to it below.');
    renderAdmin();
  } catch (err) { toast(err.message, true); }
};

/* ---------------- speaking to Ethel ----------------
   Records raw PCM and encodes a 16 kHz mono WAV in the browser. The obvious
   alternative, MediaRecorder, produces webm/opus, which the server could only
   decode with ffmpeg - one more thing to install on a machine that is meant to
   need nothing. Sixteen kilohertz mono is exactly what the recognition models
   want, so encoding it here costs nothing and saves a dependency. */

async function recordWav(onLevel) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  node.onaudioprocess = (e) => {
    const buf = e.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(buf));
    if (onLevel) {
      let peak = 0;
      for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i]));
      onLevel(peak);
    }
  };
  src.connect(node);
  node.connect(ctx.destination);

  return {
    async stop() {
      node.disconnect(); src.disconnect();
      stream.getTracks().forEach(t => t.stop());
      const rate = ctx.sampleRate;
      await ctx.close();
      let n = 0; chunks.forEach(c => { n += c.length; });
      const flat = new Float32Array(n);
      let o = 0; chunks.forEach(c => { flat.set(c, o); o += c.length; });
      return encodeWav(resampleTo16k(flat, rate), 16000);
    },
  };
}

function resampleTo16k(samples, rate) {
  if (rate === 16000) return samples;
  const n = Math.round(samples.length * 16000 / rate);
  const out = new Float32Array(n);
  const ratio = (samples.length - 1) / Math.max(1, n - 1);
  for (let i = 0; i < n; i++) {
    const x = i * ratio, lo = Math.floor(x), hi = Math.min(lo + 1, samples.length - 1);
    out[i] = samples[lo] + (samples[hi] - samples[lo]) * (x - lo);
  }
  return out;
}

function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const str = (off, t) => { for (let i = 0; i < t.length; i++) view.setUint8(off + i, t.charCodeAt(i)); };
  str(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

const MIC_ICON = 'M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z';
const STOP_ICON = 'M6 6h12v12H6z';

function micButton(onText) {
  // A round icon button, not a labelled one: it sits inside the question box
  // beside Send, where a word would crowd the field. The state lives in the
  // glyph and the tooltip, so nothing here writes over the icon with text.
  const btn = el('button', 'iconbtn mic');
  btn.type = 'button';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'currentColor');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', MIC_ICON);
  svg.appendChild(path);
  btn.appendChild(svg);
  const setState = (state) => {
    path.setAttribute('d', state === 'recording' ? STOP_ICON : MIC_ICON);
    btn.classList.toggle('rec', state === 'recording');
    btn.setAttribute('aria-label', {
      idle: 'Say your question instead of typing it',
      recording: 'Stop recording',
      busy: 'Working out what you said',
    }[state]);
    btn.title = btn.getAttribute('aria-label');
  };
  setState('idle');
  let rec = null;
  btn.onclick = async () => {
    if (rec) {
      const wav = await rec.stop(); rec = null;
      setState('busy'); btn.disabled = true;
      try {
        const v = await loadVoices();
        const res = await fetch('/api/listen', {
          method: 'POST',
          headers: { 'Content-Type': 'audio/wav', 'X-Ethel-Language': v.current.language },
          body: wav,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Could not transcribe that.');
        onText(data.text);
        // Never present a transcript as though it were certainly right: these
        // models are wrong often enough that the student must be able to see
        // and fix what Ethel thinks they said.
        toast(data.wer
          ? `Heard: "${data.text}" - check it, about ${Math.round(data.wer * 100)}% of words come back wrong`
          : `Heard: "${data.text}" - check it before sending`);
      } catch (err) {
        toast(err.message, true);
      } finally {
        setState('idle'); btn.disabled = false;
      }
      return;
    }
    try {
      rec = await recordWav();
      setState('recording');
    } catch {
      toast('I could not use the microphone. Check the browser has permission.', true);
    }
  };
  btn.hidden = true;              // never offer to listen before we know we can
  btn.refresh = async () => {
    const v = await loadVoices();
    const lang = (v.current || {}).language || 'en';
    const ok = (v.listening || []).includes(lang);
    btn.hidden = !ok;
  };
  btn.refresh();
  MICS.push(btn);
  return btn;
}

/* ---------------- voice switching ----------------
   The catalogue is fetched once and cached: it only changes when someone adds
   a voice file to the machine, which is not something that happens mid-lesson.
   The control hides itself entirely when there is nothing to choose between -
   a dropdown with one option is just clutter. */

let VOICES = null;
const MICS = [];

function refreshMics() { MICS.forEach(m => m.refresh && m.refresh()); }

async function loadVoices() {
  if (VOICES) return VOICES;
  try {
    VOICES = await api('/api/voices');
  } catch {
    // Signed out, or the server is unhappy. Return a shape with every field a
    // caller reads, so a failure degrades to "nothing is available" instead of
    // throwing inside a .then and leaving controls in their default state -
    // which is how the microphone came to show for a language Ethel cannot hear.
    VOICES = { available: [], paces: [], languages: [], listening: [],
               current: { language: 'en', voice_id: null, pace: 'normal' } };
  }
  return VOICES;
}

function voicePicker() {
  const wrap = el('div', 'voicepick');
  wrap.hidden = true;
  loadVoices().then(v => {
    if (!v.engine_ready || !v.available.length) return;
    wrap.hidden = false;

    // Language first: it decides what the other two can even offer.
    if (v.languages && v.languages.length > 1) {
      const lang = document.createElement('select');
      lang.title = 'Language for lessons and the interface';
      v.languages.forEach(L => {
        const o = document.createElement('option');
        o.value = L.code;
        o.textContent = L.name;
        // Say what each language can actually do, rather than offering a
        // Bemba voice that does not exist and letting the student find out.
        o.title = L.has_voice
          ? `${L.name}: interface ${L.interface_percent}%, voice installed`
          : `${L.name}: interface ${L.interface_percent}%, no voice on this machine`;
        if (L.code === v.current.language) o.selected = true;
        lang.appendChild(o);
      });
      lang.onchange = () => saveVoice({ language: lang.value });
      wrap.appendChild(lang);
    }

    // Only voices that can legitimately read the current language. Offering an
    // English voice while the lesson is in Bemba implies it will read Bemba,
    // which is the one thing this project will not do.
    const usable = v.available.filter(a => a.language_family === v.current.language);
    if (!usable.length) {
      const none = el('span', 'novoice',
        'No voice for this language on this machine');
      wrap.appendChild(none);
    }
    if (usable.length > 1) {
      const sel = document.createElement('select');
      sel.title = 'Which voice reads to you';
      usable.forEach(a => {
        const o = document.createElement('option');
        o.value = a.id;
        o.textContent = a.gender === 'unknown' ? a.label : `${a.label} (${a.gender})`;
        if (a.id === v.current.voice_id) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = () => saveVoice({ voice_id: sel.value });
      wrap.appendChild(sel);
    }

    if (!usable.length) return;
    const pace = document.createElement('select');
    pace.title = 'How fast the voice reads';
    v.paces.forEach(p => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.label;
      o.title = p.hint;
      if (p.id === v.current.pace) o.selected = true;
      pace.appendChild(o);
    });
    pace.onchange = () => saveVoice({ pace: pace.value });
    wrap.appendChild(pace);
  });
  return wrap;
}

async function saveVoice(patch) {
  try {
    VOICES = await api('/api/voice', patch);
    if (patch.language) {
      const L = VOICES.languages.find(x => x.code === VOICES.current.language);
      toast(L.has_voice
        ? `Language: ${L.name}`
        : `Language: ${L.name} - no voice on this machine, so reading aloud is off`);
      // Lesson text and the study plan both change with language.
      refreshMics();
      if (S.view === 'learn') await renderLearn();
      return;
    }
    const what = patch.pace
      ? VOICES.paces.find(p => p.id === VOICES.current.pace)
      : VOICES.available.find(a => a.id === VOICES.current.voice_id);
    toast(patch.pace ? `Reading pace: ${what.label}` : `Voice: ${what.label}`);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------------- ask ---------------- */

(function () {
  const mic = micButton(text => {
    $('#askInput').value = text;
    autoGrow($('#askInput'));
    paintSend();                  // a dictated question should light Send too
    $('#askInput').focus();
  });
  $('#askForm').insertBefore(mic, $('#askForm').lastElementChild);
})();

$('#askForm').onsubmit = async (e) => {
  e.preventDefault();
  const q = $('#askInput').value.trim();
  if (!q) return;
  $('#askInput').value = '';
  autoGrow($('#askInput'));
  paintSend();
  saveDraft('');
  $('#askGreeting').hidden = true;
  const log = $('#askLog');
  const entry = el('div', 'qa');
  entry.appendChild(el('div', 'q', q));
  // Three breathing dots rather than a sentence. The sentence used to say
  // "Searching your course material", which covered four separate stages and
  // was only true of the first.
  const pending = el('div', 'spin');
  pending.append(el('i'), el('i'), el('i'));
  entry.appendChild(pending);
  const patience = waitNote(entry);
  // Newest last, and scrolled to, so a conversation reads downwards the way
  // every other chat does.
  log.append(entry);
  entry.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const r = await api('/api/ask', { question: q });
    patience.stop();
    pending.remove();
    if (r.mode === 'refused') entry.className = 'qa refused';
    const label = r.mode === 'grounded' ? 'Answered from your course material'
      : r.mode === 'extractive' ? 'Straight from your course material'
        : 'Not in your course material';
    entry.appendChild(el('div', 'mode', label));
    entry.appendChild(el('div', 'a', r.answer));
    if (r.detail) entry.appendChild(el('div', 'src', r.detail));
    if (r.fallback_reason) entry.appendChild(el('div', 'src', 'Note: ' + r.fallback_reason + '.'));
    if (r.sources && r.sources.length) {
      entry.appendChild(el('div', 'src',
        'Source: ' + r.sources.map(s => `${s.course} - ${s.lesson} / ${s.section}`).join('; ')));
    }
  } catch (err) {
    patience.stop();
    pending.remove();
    entry.appendChild(el('div', 'a', err.message));
  }
};

/* A grounded answer takes about forty-five seconds on the machine this was
   built on: retrieval, then the model, then three checks on what it said. Dots
   alone for three quarters of a minute read as a hang, and a student who
   thinks it has hung will reload and lose the question.

   So say what is happening, and say it in stages. The last line is the
   important one - it names the hardware rather than implying the student did
   something wrong, and it is the truth. */
function waitNote(entry) {
  const note = el('p', 'hint waiting');
  const stages = [
    [7000, 'Reading through your course material.'],
    [16000, 'Checking every sentence against the source before showing it to you.'],
    [32000, 'This machine is slow at this part. It is still working - about half a minute more.'],
  ];
  const timers = stages.map(([at, text]) => setTimeout(() => {
    note.textContent = text;
    if (!note.isConnected) entry.appendChild(note);
  }, at));
  return { stop() { timers.forEach(clearTimeout); note.remove(); } };
}

/* ---------------- doctor ---------------- */

async function renderDoctor() {
  show('doctor');
  const d = await api('/api/doctor');
  const box = $('#doctorBody');
  box.innerHTML = '';

  const section = (title, rows) => {
    const s = el('div', 'section');
    s.appendChild(el('h3', null, title));
    const dl = el('dl', 'kv');
    rows.forEach(([k, v, state]) => {
      dl.appendChild(el('dt', null, k));
      const dd = el('dd');
      if (state) dd.appendChild(el('span', 'dot ' + state));
      dd.appendChild(document.createTextNode(v));
      dl.appendChild(dd);
    });
    s.appendChild(dl);
    box.appendChild(s);
  };

  section('Offline', [
    ['Network airlock', d.offline.airlock_engaged ? 'Engaged - outbound connections are blocked'
      : 'NOT engaged - start with run.bat', d.offline.airlock_engaged ? 'ok' : 'bad'],
    ['Blocked attempts', d.offline.blocked_attempts.length
      ? d.offline.blocked_attempts.map(v => `${v.kind} ${v.host}`).join(', ')
      : 'none - nothing has tried to reach the internet',
      d.offline.blocked_attempts.length ? 'warn' : 'ok'],
  ]);

  section('Model', [
    ['Backend', d.model.backend],
    ['Configured model', d.model.configured_model || '-'],
    ['Model server', d.model.server_reachable ? 'reachable on loopback' : 'not running',
      d.model.server_reachable ? 'ok' : 'warn'],
    ['Model installed', d.model.model_installed ? 'yes' : 'no',
      d.model.model_installed ? 'ok' : 'warn'],
    ['Current mode', d.model.mode],
  ]);

  const h = d.hardware;
  if (h) {
    const a = h.applied;
    section('This machine', [
      ['Tier', h.tier, h.tier === 'constrained' ? 'warn' : 'ok'],
      ['Memory', `${h.ram_total_gb} GB total, ${h.ram_available_gb} GB free now`,
        h.ram_available_gb && h.ram_available_gb < 1.5 ? 'warn' : 'ok'],
      ['Processor cores', String(h.cores)],
      ['Model size budget', `up to about ${h.model_budget_gb} GB on disk`],
      ['Context window', `${a.num_ctx} tokens (chosen for this machine)`],
      ['Threads', `${a.num_thread} of ${h.cores}`],
      ['Passages per answer', String(a.top_k)],
      ['Answer length cap', `${a.max_tokens} tokens`],
      ['Patience', `${a.timeout_s}s before falling back to quoting`],
    ]);
    const notes = document.querySelector('#doctorBody .section:last-child');
    h.notes.forEach(n => {
      const p = el('p', 'hint', n);
      notes.appendChild(p);
    });
  }

  const v = d.speech.voices;
  section('Voice', [
    ['Piper engine', d.speech.engine_found ? d.speech.engine_path : 'not installed',
      d.speech.engine_found ? 'ok' : 'warn'],
    ['Mode', d.speech.mode + ' - ' + d.speech.mode_note,
      d.speech.mode === 'resident' ? 'ok' : d.speech.mode === 'one-shot' ? 'warn' : 'bad'],
    ['Held in memory', d.speech.resident && d.speech.resident.length
      ? d.speech.resident.map(r => `${r.voice} (${r.requests} used, idle ${r.idle_seconds}s)`).join('; ')
      : `none loaded yet — up to ${d.speech.max_resident} at a time`],
    ['Voice files', d.speech.voices_dir],
    ['Installed voices', (d.speech.catalogue || []).length
      ? d.speech.catalogue.map(c => `${c.label} (${c.gender}, ${c.language_code})`).join(', ')
      : 'none', (d.speech.catalogue || []).length ? 'ok' : 'bad'],
    ['English voices', `female ${v.en.female ? 'yes' : 'no'}, male ${v.en.male ? 'yes' : 'no'}`,
      (v.en.female && v.en.male) ? 'ok' : (v.en.female || v.en.male) ? 'warn' : 'bad'],
    ['Bemba voice', v.bem.female || v.bem.male ? 'installed' : 'none installed',
      (v.bem.female || v.bem.male) ? 'ok' : 'warn'],
    ['Lozi voice', v.loz.female || v.loz.male ? 'installed' : 'none installed',
      (v.loz.female || v.loz.male) ? 'ok' : 'warn'],
    ['Note', d.speech.note],
  ]);

  // When Piper is missing, "not installed" alone is a dead end. List every
  // place that was tried so whoever is fixing it knows where to put it.
  if (!d.speech.engine_found && d.speech.searched) {
    const box = document.querySelector('#doctorBody .section:last-child');
    box.appendChild(el('p', 'hint', 'Piper was looked for in these places:'));
    d.speech.searched.forEach(s => {
      const p = el('p', 'hint indent', `${s.where}: ${s.path}`);
      box.appendChild(p);
    });
    box.appendChild(el('p', 'hint',
      'Set speech.piper_exe (a binary) or speech.piper_cmd (an argv list, for the '
      + 'piper-tts Python package in a virtualenv) in data/config.json.'));
  }

  const li = d.listening;
  if (li) {
    section('Speaking to Ethel', [
      ['Runtime', {
        yes: 'ready (torch + transformers)',
        no: 'torch + transformers not usable — see requirements-voice.txt',
        checking: 'checking… (importing torch can take minutes on a slow machine)',
        'no venv': 'no voice venv — run setup-voice.bat',
      }[li.runtime_state] || 'unknown',
        li.runtime_state === 'yes' ? 'ok' : li.runtime_state === 'checking' ? 'warn' : 'bad'],
      ['Models installed', li.installed.length
        ? li.installed.map(m => `${m.language_name} (${m.size_mb} MB)`).join(', ')
        : 'none', li.installed.length ? 'ok' : 'warn'],
      ['Held in memory', li.resident.length
        ? li.resident.map(r => `${r.language} (${r.requests} used)`).join('; ')
        : `none loaded — up to ${li.max_resident} at a time`],
      ['Models folder', li.models_dir],
    ]);
    const box = document.querySelector('#doctorBody .section:last-child');
    box.appendChild(el('p', 'hint', li.note));
    li.known.forEach(m => {
      const have = li.installed.some(x => x.language === m.code);
      const wer = m.wer === null ? 'accuracy not published' : `WER ${m.wer}`;
      const p2 = el('p', 'hint indent',
        `${have ? '✓ ' : ''}${m.language} (${m.code}) — ${wer}, ${m.size_mb} MB`
        + (have ? '' : `  ·  python tools/get_voice.py --asr ${m.code}`));

      box.appendChild(p2);
    });
  }

  const rz = d.residency;
  if (rz) {
    section('Memory held by models', [
      ['Budget', `${rz.resident_mb} MB of ${rz.budget_mb} MB`,
        rz.over_budget ? 'warn' : 'ok'],
      ['Held now', rz.held.length
        ? rz.held.map(h => `${h.kind}:${h.key} (${h.mb} MB, idle ${h.idle_seconds}s)`).join('; ')
        : 'nothing loaded'],
      ['Recent evictions', rz.recent_evictions.length
        ? rz.recent_evictions.map(e => `${e.evicted} dropped for ${e.for}, ${e.ago_seconds}s ago`).join('; ')
        : 'none'],
    ]);
    const box = document.querySelector('#doctorBody .section:last-child');
    box.appendChild(el('p', 'hint', rz.note));
  }

  section('Content library', [
    ['Packs on this machine', String(d.library.packs), d.library.packs ? 'ok' : 'bad'],
    ['Unsigned packs', String(d.library.unsigned), d.library.unsigned ? 'warn' : 'ok'],
    ['Failed signature checks', String(d.library.mismatched), d.library.mismatched ? 'bad' : 'ok'],
    ['Library path', d.paths.library],
  ]);

  section('Curriculum catalogue', [
    ['Retrieved on', d.catalog.retrieved_on],
    ['Institutions', String(d.catalog.institutions)],
    ['Programmes', String(d.catalog.programmes)],
  ]);

  section('Interface languages', Object.entries(d.locales).map(([code, cov]) => [
    code, Math.round(cov * 100) + '% translated'
      + (code === 'en' ? '' : ' - draft, awaiting native-speaker review'),
    cov > 0.9 ? 'ok' : 'warn',
  ]));
}

boot().catch(err => toast(err.message, true));

/* ---------------- Socratic mode ----------------
   Ask before telling, one line at a time, with an optional spoken loop.

   The loop is only fast because no language model is in it: Ethel speaks a
   probe (~0.6s), the student answers, recognition returns (~3s), and the
   judgement is lexical and instant. Putting a model in the middle would add ten
   to thirty seconds a turn on modest hardware, which is not a conversation. */

const SOC = { on: false, voice: false, busy: false, rec: null };

async function startSocratic(packId, lessonId, segmentId) {
  try {
    const d = await api('/api/socratic/start',
      { pack_id: packId, lesson_id: lessonId, segment_id: segmentId || null });
    SOC.on = true;
    // The Socratic card lives inside the Learn view, so the view has to be the
    // visible one - otherwise the panel unhides behind whatever is on screen.
    show('learn');
    SOC.busy = false;
    $('#socForm').hidden = false;
    $('#socNote').textContent = '';
    $('#socraticCard').hidden = false;
    $('#lessonCard').hidden = true;
    drawSocratic(d.step, d.total, null);
    if (SOC.voice) speakProbe(d.step);
  } catch (err) { toast(err.message, true); }
}

function drawSocratic(step, total, result) {
  if (!step) return;
  $('#socHead').textContent = step.heading || '';
  const done = step.index < 0 ? 0 : step.index;
  $('#socBar').style.width = total ? `${Math.round(100 * done / total)}%` : '0%';
  $('#socCount').textContent = step.opener ? 'to begin' : `${done + 1} of ${total}`;
  $('#socProbe').textContent = step.probe;

  const reveal = $('#socReveal');
  if (result && result.line) {
    reveal.hidden = false;
    const fb = $('#socFeedback');
    fb.className = 'feedback ' + (result.verdict === 'grasped' ? 'good'
      : result.verdict === 'missed' ? 'bad' : '');
    fb.innerHTML = '';
    fb.appendChild(el('b', null, {
      grasped: 'You had it', partial: 'Partly',
      missed: 'Not yet', skipped: 'Fair enough', opener: '',
    }[result.verdict] || ''));
    fb.appendChild(el('div', null, result.response));
    $('#socLine').textContent = result.line;
  } else if (result && result.verdict === 'opener') {
    reveal.hidden = false;
    const fb = $('#socFeedback');
    fb.className = 'feedback';
    fb.innerHTML = '';
    fb.appendChild(el('div', null, result.response));
    $('#socLine').textContent = '';
  } else {
    reveal.hidden = true;
  }
  $('#socInput').value = '';
  $('#socInput').focus();
}

async function answerSocratic(text) {
  if (SOC.busy) return;
  SOC.busy = true;
  try {
    const r = await api('/api/socratic/answer', { text });
    if (r.done) {
      finishSocratic(r);
      return;
    }
    drawSocratic(r.next, r.next.total, r);
    if (SOC.voice) await speakThen(r.spoken || r.response, r.next);
  } catch (err) {
    toast(err.message, true);
  } finally { SOC.busy = false; }
}

function finishSocratic(r) {
  const s = r.summary || {};
  const c = s.counts || {};
  $('#socProbe').textContent = 'Finished.';
  $('#socReveal').hidden = false;
  $('#socFeedback').className = 'feedback good';
  $('#socFeedback').textContent =
    `${s.lines} lines: ${c.grasped || 0} in your own words, ${c.partial || 0} partly, `
    + `${c.missed || 0} missed, ${c.skipped || 0} skipped.`;
  $('#socLine').textContent = (s.revisit || []).join('\n\n');
  $('#socNote').textContent = s.note || '';
  $('#socForm').hidden = true;
  $('#socBar').style.width = '100%';
}

/* --- the spoken loop --- */

async function speakProbe(step) {
  if (!step) return;
  await speakThen(step.probe, step);
}

async function speakThen(text, step) {
  try {
    const res = await fetch('/api/speak', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) { SOC.voice = false; paintVoiceBtn();
      toast('No voice on this machine, so voice mode is off.', true); return; }
    const blob = await res.blob();
    await new Promise(done => {
      const a = new Audio(URL.createObjectURL(blob));
      a.onended = done; a.onerror = done; a.play().catch(done);
    });
    // Only after Ethel has finished speaking does listening start, or the
    // microphone records her own voice.
    if (SOC.voice && step && !step.opener) await listenForAnswer();
    else if (SOC.voice && step) await listenForAnswer();
  } catch { /* speaking is optional; the text is on screen regardless */ }
}

async function listenForAnswer() {
  const v = await loadVoices();
  if (!(v.listening || []).includes(v.current.language)) return;
  const btn = $('#socVoice');
  btn.textContent = 'Listening…'; btn.classList.add('rec');
  try {
    const rec = await recordWav();
    // A fixed window keeps this simple and predictable. Proper turn-taking
    // wants silence detection, which is a real piece of work and not here yet.
    await new Promise(r => setTimeout(r, 6000));
    const wav = await rec.stop();
    btn.textContent = 'Thinking…';
    const res = await fetch('/api/listen', {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav', 'X-Ethel-Language': v.current.language },
      body: wav,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not hear that.');
    $('#socInput').value = data.text;
    paintVoiceBtn();
    await answerSocratic(data.text);
  } catch (err) {
    toast(err.message, true);
    paintVoiceBtn();
  }
}

function paintVoiceBtn() {
  const b = $('#socVoice');
  b.classList.remove('rec');
  b.textContent = 'Voice mode: ' + (SOC.voice ? 'on' : 'off');
}

$('#socForm').onsubmit = (e) => {
  e.preventDefault();
  answerSocratic($('#socInput').value);
};

$('#socVoice').onclick = async () => {
  const v = await loadVoices();
  const canHear = (v.listening || []).includes(v.current.language);
  const canSpeak = v.available.some(a => a.language_family === v.current.language);
  if (!SOC.voice && !(canHear && canSpeak)) {
    toast(canSpeak
      ? 'No recognition model for this language, so Ethel cannot hear you.'
      : 'No voice for this language on this machine.', true);
    return;
  }
  SOC.voice = !SOC.voice;
  paintVoiceBtn();
  if (SOC.voice) {
    const cur = await api('/api/socratic/current');
    speakProbe(cur.step);
  }
};

$('#socSpeak').onclick = async () => {
  const cur = await api('/api/socratic/current');
  if (cur.step) speakThen(cur.step.probe, null);
};

$('#socQuit').onclick = () => {
  SOC.on = false; SOC.voice = false;
  $('#socraticCard').hidden = true;
  $('#lessonCard').hidden = false;
  $('#socForm').hidden = false;
  renderLearn();
};


/* ================= the shell, and the small automatic things =================

   Nothing below teaches anything. It is the layer that keeps a student's hands
   on the keyboard and stops the interface asking for clicks it could have
   worked out for itself. */

/* --- multiple choice, once, with number keys ---
   Both the placement test and the lesson had their own copy of this. They have
   drifted apart before. One version now, and it carries the keyboard: the
   digits pick an option and Enter sends it, which matters most for the
   placement test, where a student answers a dozen questions in a row. */
function optionList(options, onPick, onSubmit) {
  const opts = el('div', 'opts');
  options.forEach((o, i) => {
    const b = el('button', 'opt');
    b.type = 'button';
    b.appendChild(el('span', 'k', String(i + 1)));
    b.appendChild(el('span', null, o));
    b.onclick = () => {
      opts.querySelectorAll('.opt').forEach(x => x.classList.toggle('is-active', x === b));
      onPick(i);
    };
    opts.appendChild(b);
  });
  opts.dataset.keys = '1';
  opts._submit = onSubmit;
  if (options.length <= 9) {
    const tip = el('p', 'hint');
    tip.append('Press ', el('kbd', null, '1'), '–',
      el('kbd', null, String(options.length)), ' to choose, ',
      el('kbd', null, 'Enter'), ' to answer.');
    opts.appendChild(tip);
  }
  return opts;
}

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''));

  // Escape leaves Socratic mode from anywhere, including mid-answer. It is the
  // one mode a student can feel stuck inside.
  if (e.key === 'Escape' && SOC.on) { e.preventDefault(); $('#socQuit').click(); return; }

  // A bare slash jumps to the question box, the way it does in every other
  // search-shaped thing. Only when not already typing, or it eats the slash.
  if (e.key === '/' && !typing && S.me) { e.preventDefault(); openAsk(); return; }

  const opts = document.querySelector('.view:not([hidden]) .opts[data-keys]');
  if (!opts || typing) return;
  if (/^[1-9]$/.test(e.key)) {
    const b = opts.querySelectorAll('.opt')[Number(e.key) - 1];
    if (b) { e.preventDefault(); b.click(); b.focus(); }
  } else if ((e.key === 'Enter' || e.code === 'Enter' || e.code === 'NumpadEnter') && opts._submit) {
    e.preventDefault();
    opts._submit();
  }
});

/* --- the rail --- */

const RAIL_KEY = 'ethel.rail.tight';
function setRail(tight) {
  document.body.classList.toggle('rail-tight', tight);
  try { localStorage.setItem(RAIL_KEY, tight ? '1' : '0'); } catch { /* private mode */ }
}
// Phones start closed regardless of what was stored, because on a phone the
// rail is an overlay and starting on top of the page would be absurd.
(function initRail() {
  let tight = false;
  try { tight = localStorage.getItem(RAIL_KEY) === '1'; } catch { /* private mode */ }
  if (window.matchMedia('(max-width: 760px)').matches) tight = true;
  document.body.classList.toggle('rail-tight', tight);
})();

$('#railToggle').onclick = () => setRail(!document.body.classList.contains('rail-tight'));
$('#railOpen').onclick = () => setRail(false);
$('#scrim').onclick = () => setRail(true);
$('#newAsk').onclick = () => openAsk();

function paintAvatar(name) {
  const a = $('#avatar');
  const initial = (name || '?').trim().charAt(0).toUpperCase();
  a.textContent = initial || '?';
  a.title = name || '';
  a.hidden = false;
}

/* --- the question box --- */

function openAsk() {
  show('ask');
  const box = $('#askInput');
  box.value = loadDraft();
  autoGrow(box);
  paintSend();
  box.focus();
}

/* A textarea that grows with what is in it, to a ceiling set in CSS. Reset to
   auto first or it can only ever get taller. */
function autoGrow(node) {
  if (!node) return;
  // Cleared before measuring: a text box can only ever grow if its own
  // height is not still constraining scrollHeight.
  node.style.height = 'auto';
  node.style.height = Math.min(node.scrollHeight, 190) + 'px';
}

function paintSend() {
  const has = $('#askInput').value.trim().length > 0;
  const b = $('#askSend');
  b.disabled = !has;
  b.classList.toggle('ready', has);
}

/* A half-typed question survives a trip to the lesson and back. sessionStorage
   rather than localStorage: it should not outlive the browser, and it should
   never follow one student to the next on a shared machine. */
const DRAFT_KEY = 'ethel.ask.draft';
function saveDraft(v) { try { sessionStorage.setItem(DRAFT_KEY, v); } catch { /* private mode */ } }
function loadDraft() { try { return sessionStorage.getItem(DRAFT_KEY) || ''; } catch { return ''; } }

$('#askInput').addEventListener('input', () => {
  autoGrow($('#askInput'));
  paintSend();
  saveDraft($('#askInput').value);
});

// Enter sends, Shift+Enter starts a line. A textarea does not submit its form
// on Enter the way an input does, so this is what makes the box behave.
$('#askInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('#askForm').requestSubmit();
  }
});

/* --- Socratic: keep the send button honest --- */
function paintSocSend() {
  const b = $('#socSend');
  if (b) b.classList.toggle('ready', $('#socInput').value.trim().length > 0);
}
$('#socInput').addEventListener('input', paintSocSend);

/* --- reference material: drop files onto the card they belong to ---
   The buttons still work. This is for the far more common gesture of dragging
   a folder of lecture notes straight out of a file manager. */
(function dropUploads() {
  const list = $('#sourceList');
  const cardUnder = (e) => e.target.closest && e.target.closest('.source');
  let lit = null;
  const unlight = () => { if (lit) lit.classList.remove('drop'); lit = null; };

  list.addEventListener('dragover', (e) => {
    const card = cardUnder(e);
    if (!card) return;
    e.preventDefault();
    if (lit !== card) { unlight(); lit = card; card.classList.add('drop'); }
  });
  list.addEventListener('dragleave', (e) => { if (cardUnder(e) === lit && !lit.contains(e.relatedTarget)) unlight(); });
  list.addEventListener('drop', (e) => {
    const card = cardUnder(e);
    if (!card) return;
    e.preventDefault();
    unlight();
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) uploadTo(card.dataset.id, files);
  });
  // Without this the browser navigates away to display the dropped file, which
  // loses the student's session and looks like a crash.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());
})();

/* --- keep the plan beside the lesson current --- */
async function refreshPlan() {
  try {
    const data = await api('/api/study');
    const box = $('#planBody');
    if (!box || !box.children.length) return;
    // Matched by lesson id rather than by position. Index matching looks
    // equivalent and is, right up until the plan gains or loses a row between
    // the two calls and every bar shows the wrong lesson's progress.
    data.lessons.forEach(l => {
      const row = box.querySelector(`.planrow[data-lesson="${l.lesson_id}"]`);
      const fill = row && row.querySelector('.mastery i');
      if (fill) fill.style.width = Math.round(l.mastery * 100) + '%';
    });
  } catch { /* the plan is decoration here; a failure must not stop the lesson */ }
}
