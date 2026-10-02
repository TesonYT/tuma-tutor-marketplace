const express = require('express');
const db = require('./db');
const config = require('./config');
const { notify, adminEmail } = require('./notify');
const { esc, label, list, layout, termsBlurb, checkboxes } = require('./views');

const router = express.Router();

// --- tiny in-memory rate limiter for public POSTs (per IP; resets on restart) ---
const hits = new Map();
function limit(max, windowMs) {
  return (req, res, next) => {
    const now = Date.now();
    const key = `${req.path}|${req.ip}`;
    const recent = (hits.get(key) || []).filter((t) => now - t < windowMs);
    if (recent.length >= max) return res.status(429).send(layout({ title: 'Slow down', body: '<h1>Too many requests</h1><p>Please try again later.</p>' }));
    recent.push(now);
    hits.set(key, recent);
    next();
  };
}

const arr = (v) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
const str = (v, max = 2000) => String(v ?? '').trim().slice(0, max);
const pick = (values, allowed) => values.filter((v) => allowed.includes(v));
const wordCount = (s) => (s.match(/\S+/g) || []).length;

function requireDb(req, res, next) {
  if (db.enabled()) return next();
  res.status(503).send(layout({ title: 'Not available', body: '<h1>Tutor track not available</h1><p>The database is not configured on this server (set DATABASE_URL).</p>' }));
}
router.use(requireDb);

// ---------------------------------------------------------------- /tutors
router.get('/tutors', (req, res) => {
  res.send(layout({
    title: 'Tutor track',
    body: `<h1>Learn with a real tutor</h1>
<p>Ethel is an offline AI tutor. When you want a person alongside it, our human tutors are vetted by application, written screening and an interview before they appear here.</p>
<div class="cards">
  <a class="card" href="/tutors/browse"><h2>Find a tutor</h2><p>Browse approved tutors by language and subject.</p></a>
  <a class="card" href="/tutors/book"><h2>Request a tutor</h2><p>Tell us what you need and we will match and introduce you. ${esc(termsBlurb())}.</p></a>
  <a class="card" href="/tutors/apply"><h2>Become a tutor</h2><p>Know a subject well and can explain it clearly? Apply. Tutors are paid a standard rate per session.</p></a>
</div>`,
  }));
});

// ---------------------------------------------------------------- /tutors/apply
function applyForm(v = {}, errors = []) {
  const t = config.terms();
  const sel = (name, opts) => opts.map(([val, text]) => `<option value="${val}"${v[name] === val ? ' selected' : ''}>${esc(text)}</option>`).join('');
  return `<h1>Apply to tutor</h1>
${errors.length ? `<div class="errors"><strong>Please fix:</strong><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
<form method="post" action="/tutors/apply" class="form">
  <input type="text" name="website" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">
  <fieldset><legend>You</legend>
    <label>Full name *<input name="full_name" required maxlength="200" value="${esc(v.full_name)}"></label>
    <label>Email *<input type="email" name="email" required maxlength="200" value="${esc(v.email)}"></label>
    <label>Phone / WhatsApp *<input name="phone" required maxlength="40" value="${esc(v.phone)}"></label>
    <label>Location (town / district) *<input name="location" required maxlength="200" value="${esc(v.location)}"></label>
  </fieldset>
  <fieldset><legend>What you can tutor</legend>
    <div class="group"><span>Languages you can tutor in</span>${checkboxes('languages', config.LANGUAGES, arr(v.languages))}</div>
    <div class="group"><span>Subjects</span>${checkboxes('subjects', config.SUBJECTS, arr(v.subjects))}</div>
    <div class="group"><span>Grade levels you are comfortable with</span>${checkboxes('levels', config.LEVELS, arr(v.levels))}</div>
    <label>Hours per week you can offer<input type="number" name="hours_per_week" min="1" max="80" value="${esc(v.hours_per_week)}"></label>
    <label>Preferred mode<select name="mode">${sel('mode', Object.entries(config.MODES))}</select></label>
  </fieldset>
  <fieldset><legend>Background</legend>
    <label>Current occupation or study status<input name="occupation" maxlength="200" value="${esc(v.occupation)}"></label>
    <label>Teaching or tutoring experience (optional)<textarea name="experience" rows="3" maxlength="2000">${esc(v.experience)}</textarea></label>
    <label>Relevant qualification (optional)<textarea name="qualifications" rows="2" maxlength="2000">${esc(v.qualifications)}</textarea></label>
  </fieldset>
  <fieldset><legend>Screening question</legend>
    <label>Explain something you know well to a ten-year-old, in about 150 words. Pick any topic. *
      <textarea name="screening_answer" rows="9" required>${esc(v.screening_answer)}</textarea></label>
    <small>This answer matters more than a CV: it shows clarity, patience and command of language.</small>
  </fieldset>
  <fieldset><legend>Payout details</legend>
    <p class="note">Tutors are paid by mobile money (or bank transfer). Standard rate: ${esc(termsBlurb())}${t.payoutFrequency ? `, paid ${esc(t.payoutFrequency)}` : ''}.</p>
    <label>Mobile money / account number *<input name="payout_number" required maxlength="40" value="${esc(v.payout_number)}"></label>
    <label>Provider<select name="payout_provider">${sel('payout_provider', Object.entries(config.PROVIDERS))}</select></label>
    <label>Name registered on that account * <small>(often differs from your name above; a mismatch is the most common cause of failed payouts)</small>
      <input name="payout_name" required maxlength="200" value="${esc(v.payout_name)}"></label>
    <label class="chk"><input type="checkbox" name="rate_accepted" value="1"${v.rate_accepted ? ' checked' : ''}> I understand the standard session rate and accept it *</label>
  </fieldset>
  <label class="chk"><input type="checkbox" name="consent" value="1"${v.consent ? ' checked' : ''}> I agree to be contacted for an interview and to a background / reference check *</label>
  <button class="btn" type="submit">Submit application</button>
</form>`;
}

router.get('/tutors/apply', (req, res) => res.send(layout({ title: 'Apply to tutor', body: applyForm() })));

router.post('/tutors/apply', limit(5, 60 * 60 * 1000), express.urlencoded({ extended: false, limit: '64kb' }), async (req, res) => {
  const b = req.body;
  if (b.website) return res.redirect('/tutors/apply/thanks'); // honeypot: pretend success to bots
  const v = {
    full_name: str(b.full_name, 200), email: str(b.email, 200).toLowerCase(), phone: str(b.phone, 40), location: str(b.location, 200),
    languages: pick(arr(b.languages), config.LANGUAGES), subjects: pick(arr(b.subjects), config.SUBJECTS), levels: pick(arr(b.levels), config.LEVELS),
    hours_per_week: str(b.hours_per_week, 3), mode: pick([str(b.mode, 20)], Object.keys(config.MODES))[0] || null,
    occupation: str(b.occupation, 200), experience: str(b.experience), qualifications: str(b.qualifications),
    screening_answer: str(b.screening_answer, 4000), payout_number: str(b.payout_number, 40),
    payout_provider: pick([str(b.payout_provider, 10)], Object.keys(config.PROVIDERS))[0] || null, payout_name: str(b.payout_name, 200),
    rate_accepted: b.rate_accepted === '1', consent: b.consent === '1',
  };
  const errors = [];
  if (!v.full_name) errors.push('Full name is required.');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.email)) errors.push('A valid email is required.');
  if (!v.phone) errors.push('Phone / WhatsApp is required.');
  if (!v.location) errors.push('Location is required.');
  if (v.hours_per_week && !(Number(v.hours_per_week) >= 1 && Number(v.hours_per_week) <= 80)) errors.push('Hours per week must be between 1 and 80.');
  const words = wordCount(v.screening_answer);
  if (words < config.SCREENING_MIN_WORDS) errors.push(`Your explanation is too short (${words} words). Aim for about 150.`);
  if (words > config.SCREENING_MAX_WORDS) errors.push(`Your explanation is too long (${words} words). Aim for about 150.`);
  if (!v.payout_number) errors.push('Payout number is required.');
  if (!v.payout_name) errors.push('The name registered on the payout account is required.');
  if (!v.rate_accepted) errors.push('Please confirm you accept the standard session rate.');
  if (!v.consent) errors.push('Consent to an interview and background check is required.');
  if (errors.length) return res.status(400).send(layout({ title: 'Apply to tutor', body: applyForm({ ...b, ...v }, errors) }));

  try {
    await db.query(
      `INSERT INTO tutor_applications
        (full_name, email, phone, location, languages, subjects, levels, hours_per_week, mode, occupation, experience,
         qualifications, screening_answer, consent, payout_number, payout_provider, payout_name, rate_accepted)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)`,
      [v.full_name, v.email, v.phone, v.location, v.languages, v.subjects, v.levels, v.hours_per_week ? Number(v.hours_per_week) : null,
        v.mode, v.occupation, v.experience, v.qualifications, v.screening_answer, v.consent, v.payout_number, v.payout_provider,
        v.payout_name, v.rate_accepted],
    );
  } catch (err) {
    console.error('apply failed:', err);
    return res.status(500).send(layout({ title: 'Error', body: '<h1>Something went wrong</h1><p>Your application was not saved. Please try again.</p>' }));
  }
  notify({ to: v.email, subject: 'We received your Ethel tutor application', text: `Hi ${v.full_name},\n\nThanks for applying to tutor with Ethel. We will read your application and be in touch about next steps.` });
  if (adminEmail()) notify({ to: adminEmail(), subject: 'New tutor application', text: `${v.full_name} (${v.location}) applied. Review it at /admin/tutors.` });
  res.redirect('/tutors/apply/thanks');
});

router.get('/tutors/apply/thanks', (req, res) => res.send(layout({
  title: 'Application received',
  body: '<h1>Application received</h1><p>Thank you. We have emailed you an acknowledgement. Next we read your application, then arrange a short interview call if it is a fit.</p>',
})));

// ---------------------------------------------------------------- /tutors/browse
router.get('/tutors/browse', async (req, res) => {
  const lang = pick([str(req.query.language, 20)], config.LANGUAGES)[0];
  const subj = pick([str(req.query.subject, 40)], config.SUBJECTS)[0];
  const { rows } = await db.query(
    `SELECT id, display_name, bio, photo_url, languages, subjects, levels, availability_note, mode
       FROM tutors WHERE active AND ($1::text IS NULL OR $1 = ANY(languages)) AND ($2::text IS NULL OR $2 = ANY(subjects))
      ORDER BY created_at DESC`,
    [lang || null, subj || null],
  );
  const opt = (o, cur) => o.map((x) => `<option value="${esc(x)}"${cur === x ? ' selected' : ''}>${esc(label(x))}</option>`).join('');
  const cards = rows.map((t) => `<article class="card tutor">
    ${t.photo_url && /^https?:\/\//.test(t.photo_url) ? `<img src="${esc(t.photo_url)}" alt="" loading="lazy">` : ''}
    <h2>${esc(t.display_name)}</h2><p>${esc(t.bio)}</p>
    <dl><dt>Languages</dt><dd>${esc(list(t.languages))}</dd><dt>Subjects</dt><dd>${esc(list(t.subjects))}</dd>
    <dt>Levels</dt><dd>${esc(list(t.levels))}</dd><dt>Mode</dt><dd>${esc(config.MODES[t.mode] || '-')}</dd>
    ${t.availability_note ? `<dt>Availability</dt><dd>${esc(t.availability_note)}</dd>` : ''}</dl>
    <a class="btn" href="/tutors/book?tutor=${esc(t.id)}">Request ${esc(t.display_name)}</a></article>`).join('');
  res.send(layout({
    title: 'Browse tutors',
    body: `<h1>Our tutors</h1><p class="note">${esc(termsBlurb())}</p>
<form method="get" class="filters"><select name="language"><option value="">Any language</option>${opt(config.LANGUAGES, lang)}</select>
<select name="subject"><option value="">Any subject</option>${opt(config.SUBJECTS, subj)}</select><button class="btn">Filter</button></form>
<div class="cards">${cards || '<p>No approved tutors match yet. <a href="/tutors/book">Request a tutor</a> anyway and we will find someone.</p>'}</div>`,
  }));
});

// ---------------------------------------------------------------- /tutors/book
async function bookForm(v = {}, errors = []) {
  const { rows } = await db.query('SELECT id, display_name FROM tutors WHERE active ORDER BY display_name');
  const t = config.terms();
  return `<h1>Request a tutor</h1>
<p class="note">${esc(termsBlurb())}. Pay before the session by ${esc(t.acceptedProviders)}; we will send payment details when we match you.</p>
${errors.length ? `<div class="errors"><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
<form method="post" action="/tutors/book" class="form">
  <input type="text" name="website" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">
  <label>Tutor<select name="tutor_id"><option value="">No preference</option>${rows.map((r) => `<option value="${esc(r.id)}"${v.tutor_id === r.id ? ' selected' : ''}>${esc(r.display_name)}</option>`).join('')}</select></label>
  <label>Your name *<input name="student_name" required maxlength="200" value="${esc(v.student_name)}"></label>
  <label>Phone / WhatsApp / email *<input name="student_contact" required maxlength="200" value="${esc(v.student_contact)}"></label>
  <label>Location<input name="student_location" maxlength="200" value="${esc(v.student_location)}"></label>
  <label>Subject<input name="subject" maxlength="200" value="${esc(v.subject)}"></label>
  <label>Preferred language<select name="preferred_language"><option value="">Any</option>${config.LANGUAGES.map((l) => `<option value="${l}"${v.preferred_language === l ? ' selected' : ''}>${esc(label(l))}</option>`).join('')}</select></label>
  <label>Preferred times<input name="preferred_times" maxlength="300" placeholder="e.g. weekday evenings" value="${esc(v.preferred_times)}"></label>
  <label>Notes<textarea name="notes" rows="3" maxlength="2000">${esc(v.notes)}</textarea></label>
  <button class="btn" type="submit">Send request</button>
</form>`;
}

router.get('/tutors/book', async (req, res) => {
  const tutor = /^[0-9a-f-]{36}$/i.test(str(req.query.tutor, 40)) ? str(req.query.tutor, 40) : '';
  res.send(layout({ title: 'Request a tutor', body: await bookForm({ tutor_id: tutor }) }));
});

router.post('/tutors/book', limit(10, 60 * 60 * 1000), express.urlencoded({ extended: false, limit: '32kb' }), async (req, res) => {
  const b = req.body;
  if (b.website) return res.redirect('/tutors/book/thanks');
  const v = {
    tutor_id: /^[0-9a-f-]{36}$/i.test(str(b.tutor_id, 40)) ? str(b.tutor_id, 40) : null,
    student_name: str(b.student_name, 200), student_contact: str(b.student_contact, 200), student_location: str(b.student_location, 200),
    subject: str(b.subject, 200), preferred_language: pick([str(b.preferred_language, 20)], config.LANGUAGES)[0] || null,
    preferred_times: str(b.preferred_times, 300), notes: str(b.notes),
  };
  const errors = [];
  if (!v.student_name) errors.push('Your name is required.');
  if (!v.student_contact) errors.push('A way to contact you is required.');
  if (errors.length) return res.status(400).send(layout({ title: 'Request a tutor', body: await bookForm({ ...b, ...v }, errors) }));
  try {
    const tutor = v.tutor_id ? (await db.query('SELECT id FROM tutors WHERE id = $1 AND active', [v.tutor_id])).rows[0] : null;
    await db.query(
      `INSERT INTO booking_requests (tutor_id, student_name, student_contact, student_location, subject, preferred_language, preferred_times, notes, amount_zmw)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [tutor ? tutor.id : null, v.student_name, v.student_contact, v.student_location, v.subject, v.preferred_language, v.preferred_times, v.notes, config.terms().rateZmw],
    );
  } catch (err) {
    console.error('booking failed:', err);
    return res.status(500).send(layout({ title: 'Error', body: '<h1>Something went wrong</h1><p>Your request was not saved. Please try again.</p>' }));
  }
  if (adminEmail()) notify({ to: adminEmail(), subject: 'New booking request', text: `${v.student_name} (${v.student_contact}) wants a tutor${v.subject ? ` for ${v.subject}` : ''}. See /admin/tutors/bookings.` });
  res.redirect('/tutors/book/thanks');
});

router.get('/tutors/book/thanks', (req, res) => res.send(layout({
  title: 'Request received',
  body: '<h1>Request received</h1><p>We will match you with a tutor and introduce you by WhatsApp, phone or email.</p>',
})));

module.exports = router;
