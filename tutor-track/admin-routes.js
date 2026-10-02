const crypto = require('crypto');
const express = require('express');
const bcrypt = require('bcryptjs');
const db = require('./db');
const config = require('./config');
const { notify } = require('./notify');
const { esc, label, list, date, layout } = require('./views');

const router = express.Router();
const form = express.urlencoded({ extended: false, limit: '64kb' });
const str = (v, max = 2000) => String(v ?? '').trim().slice(0, max);
const UUID = /^[0-9a-f-]{36}$/i;
const page = (res, title, body, flash) => res.send(layout({ title, body, admin: true, flash }));

router.use((req, res, next) => (db.enabled() ? next() : res.status(503).send(layout({ title: 'Not available', body: '<h1>Database not configured</h1>' }))));

// ---------------------------------------------------------------- auth
const failures = new Map(); // ip -> [timestamps]
router.get('/admin/tutors/login', (req, res) => {
  res.send(layout({
    title: 'Admin login',
    body: `<h1>Admin login</h1><form method="post" class="form narrow"><label>Email<input name="email" type="email" required autocomplete="username"></label>
<label>Password<input name="password" type="password" required autocomplete="current-password"></label><button class="btn">Log in</button></form>`,
  }));
});

router.post('/admin/tutors/login', form, async (req, res) => {
  const now = Date.now();
  const recent = (failures.get(req.ip) || []).filter((t) => now - t < 15 * 60 * 1000);
  if (recent.length >= 10) return res.status(429).send(layout({ title: 'Locked', body: '<h1>Too many attempts</h1><p>Try again in 15 minutes.</p>' }));
  const { rows } = await db.query('SELECT id, password_hash FROM admin_users WHERE email = $1', [str(req.body.email, 200).toLowerCase()]);
  // Compare against a dummy hash when the email is unknown so timing does not reveal which emails exist.
  const hash = rows[0] ? rows[0].password_hash : '$2a$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinvalidinv';
  const ok = await bcrypt.compare(String(req.body.password || ''), hash).catch(() => false);
  if (!ok || !rows[0]) {
    recent.push(now);
    failures.set(req.ip, recent);
    return res.status(401).send(layout({ title: 'Admin login', body: '<h1>Admin login</h1><p class="errors">Wrong email or password.</p><p><a href="/admin/tutors/login">Try again</a></p>' }));
  }
  failures.delete(req.ip);
  req.session.regenerate((err) => {
    if (err) return res.status(500).send('Session error');
    req.session.tutorAdminId = rows[0].id;
    req.session.csrf = crypto.randomBytes(16).toString('hex');
    res.redirect('/admin/tutors');
  });
});

router.use('/admin/tutors', (req, res, next) => {
  if (req.path === '/login') return next();
  if (!req.session.tutorAdminId) return res.redirect('/admin/tutors/login');
  res.set('Cache-Control', 'no-store');
  next();
});

// Every admin POST carries the session's CSRF token.
const csrf = (req) => `<input type="hidden" name="_csrf" value="${esc(req.session.csrf)}">`;
router.use('/admin/tutors', (req, res, next) => {
  if (req.method !== 'POST' || req.path === '/login' || req.path === '/logout') return next();
  form(req, res, () => {
    if (req.body && req.body._csrf === req.session.csrf) return next();
    res.status(403).send(layout({ title: 'Forbidden', body: '<h1>Invalid form token</h1><p>Reload the page and try again.</p>', admin: true }));
  });
});

router.post('/admin/tutors/logout', (req, res) => req.session.destroy(() => res.redirect('/admin/tutors/login')));

// ---------------------------------------------------------------- applications queue
router.get('/admin/tutors', async (req, res) => {
  const status = config.STATUSES.includes(req.query.status) ? req.query.status : null;
  const lang = config.LANGUAGES.includes(req.query.language) ? req.query.language : null;
  const { rows } = await db.query(
    `SELECT id, full_name, location, languages, subjects, status, created_at FROM tutor_applications
      WHERE ($1::text IS NULL OR status = $1) AND ($2::text IS NULL OR $2 = ANY(languages)) ORDER BY created_at DESC LIMIT 500`,
    [status, lang],
  );
  const counts = (await db.query('SELECT status, count(*)::int AS n FROM tutor_applications GROUP BY status')).rows;
  const opt = (o, cur) => o.map((x) => `<option value="${esc(x)}"${cur === x ? ' selected' : ''}>${esc(label(x))}</option>`).join('');
  page(res, 'Applications', `<h1>Applications</h1>
<p class="note">${counts.map((c) => `${esc(label(c.status))}: ${c.n}`).join(' | ') || 'No applications yet.'}</p>
<form method="get" class="filters"><select name="status"><option value="">Any status</option>${opt(config.STATUSES, status)}</select>
<select name="language"><option value="">Any language</option>${opt(config.LANGUAGES, lang)}</select><button class="btn">Filter</button></form>
<table><thead><tr><th>Applied</th><th>Name</th><th>Location</th><th>Languages</th><th>Subjects</th><th>Status</th></tr></thead><tbody>
${rows.map((r) => `<tr><td>${esc(date(r.created_at))}</td><td><a href="/admin/tutors/applications/${esc(r.id)}">${esc(r.full_name)}</a></td><td>${esc(r.location)}</td>
<td>${esc(list(r.languages))}</td><td>${esc(list(r.subjects))}</td><td><span class="pill ${esc(r.status)}">${esc(label(r.status))}</span></td></tr>`).join('')}
</tbody></table>`);
});

// ---------------------------------------------------------------- application detail
router.get('/admin/tutors/applications/:id', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const a = (await db.query('SELECT * FROM tutor_applications WHERE id = $1', [req.params.id])).rows[0];
  if (!a) return res.status(404).send('Not found');
  const tutor = (await db.query('SELECT id FROM tutors WHERE application_id = $1', [a.id])).rows[0];
  const rubric = a.rubric || {};
  const scores = config.RUBRIC.map(([k]) => Number(rubric[k])).filter((n) => n >= 1);
  const next = config.NEXT_STATUSES[a.status] || [];
  const first = a.full_name.split(/\s+/)[0];
  const lastInitial = (a.full_name.split(/\s+/)[1] || '')[0];
  const actions = next.map((s) => {
    if (s === 'approved') {
      return `<form method="post" action="/admin/tutors/applications/${a.id}/approve" class="form boxed">${csrf(req)}<h3>Approve and publish</h3>
<label>Display name (public)<input name="display_name" required maxlength="100" value="${esc(lastInitial ? `${first} ${lastInitial}.` : first)}"></label>
<label>Bio (public)<textarea name="bio" rows="3" maxlength="1000"></textarea></label>
<label>Photo URL (optional, https)<input name="photo_url" maxlength="500"></label>
<label>Availability note<input name="availability_note" maxlength="300" placeholder="weekday evenings"></label>
<button class="btn">Approve</button></form>`;
    }
    return `<form method="post" action="/admin/tutors/applications/${a.id}/status" class="inline">${csrf(req)}<input type="hidden" name="to" value="${s}">
<button class="btn ${s === 'rejected' || s === 'withdrawn' ? 'danger' : ''}">${s === 'rejected' ? 'Reject' : s === 'withdrawn' ? 'Mark withdrawn' : `Advance to ${esc(label(s))}`}</button></form>`;
  }).join(' ');
  page(res, a.full_name, `<p><a href="/admin/tutors">&larr; All applications</a></p><h1>${esc(a.full_name)} <span class="pill ${esc(a.status)}">${esc(label(a.status))}</span></h1>
<section class="answer"><h2>Screening answer</h2><blockquote>${esc(a.screening_answer).replace(/\n/g, '<br>')}</blockquote></section>
<section><h2>Actions</h2>${actions || '<p>No further steps: this application is closed.</p>'}${tutor ? `<p>Published as a tutor. <a href="/admin/tutors/tutors">Manage</a></p>` : ''}</section>
<section><h2>Notes and interview rubric</h2>
<form method="post" action="/admin/tutors/applications/${a.id}/notes" class="form">${csrf(req)}
${config.RUBRIC.map(([k, text]) => `<label>${esc(text)} (1-5)<input type="number" name="${k}" min="1" max="5" value="${esc(rubric[k] ?? '')}"></label>`).join('')}
${scores.length ? `<p class="note">Rubric total: ${scores.reduce((x, y) => x + y, 0)} / ${config.RUBRIC.length * 5}</p>` : ''}
<label>Admin notes<textarea name="admin_notes" rows="5" maxlength="5000">${esc(a.admin_notes)}</textarea></label><button class="btn">Save</button></form></section>
<section><h2>Application</h2><dl>
<dt>Email</dt><dd>${esc(a.email)}</dd><dt>Phone</dt><dd>${esc(a.phone)}</dd><dt>Location</dt><dd>${esc(a.location)}</dd>
<dt>Languages</dt><dd>${esc(list(a.languages))}</dd><dt>Subjects</dt><dd>${esc(list(a.subjects))}</dd><dt>Levels</dt><dd>${esc(list(a.levels))}</dd>
<dt>Hours / week</dt><dd>${esc(a.hours_per_week ?? '-')}</dd><dt>Mode</dt><dd>${esc(config.MODES[a.mode] || '-')}</dd>
<dt>Occupation</dt><dd>${esc(a.occupation || '-')}</dd><dt>Experience</dt><dd>${esc(a.experience || '-')}</dd><dt>Qualifications</dt><dd>${esc(a.qualifications || '-')}</dd>
<dt>Payout</dt><dd>${esc(config.PROVIDERS[a.payout_provider] || '-')} ${esc(a.payout_number)} (${esc(a.payout_name)})</dd>
<dt>Accepted rate</dt><dd>${a.rate_accepted ? 'yes' : 'no'}</dd><dt>Consent</dt><dd>${a.consent ? 'yes' : 'no'}</dd>
<dt>Applied</dt><dd>${esc(date(a.created_at))}</dd><dt>Updated</dt><dd>${esc(date(a.updated_at))}</dd></dl></section>`, req.query.msg);
});

router.post('/admin/tutors/applications/:id/notes', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const rubric = {};
  for (const [k] of config.RUBRIC) {
    const n = Number(req.body[k]);
    if (Number.isInteger(n) && n >= 1 && n <= 5) rubric[k] = n;
  }
  await db.query('UPDATE tutor_applications SET admin_notes = $2, rubric = $3, updated_at = now() WHERE id = $1',
    [req.params.id, str(req.body.admin_notes, 5000), Object.keys(rubric).length ? rubric : null]);
  res.redirect(`/admin/tutors/applications/${req.params.id}?msg=Saved`);
});

router.post('/admin/tutors/applications/:id/status', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const to = str(req.body.to, 30);
  const a = (await db.query('SELECT status, email, full_name FROM tutor_applications WHERE id = $1', [req.params.id])).rows[0];
  if (!a) return res.status(404).send('Not found');
  if (to === 'approved' || !(config.NEXT_STATUSES[a.status] || []).includes(to)) {
    return res.redirect(`/admin/tutors/applications/${req.params.id}?msg=${encodeURIComponent(`Cannot move from ${label(a.status)} to ${label(to)}`)}`);
  }
  await db.query('UPDATE tutor_applications SET status = $2, updated_at = now() WHERE id = $1', [req.params.id, to]);
  if (to === 'rejected') {
    notify({ to: a.email, subject: 'Your Ethel tutor application', text: `Hi ${a.full_name},\n\nThank you for applying. We are not able to offer you a place right now. You are welcome to reapply in future.` });
  }
  res.redirect(`/admin/tutors/applications/${req.params.id}`);
});

// Approval creates the tutors row (spec section 4: "Create a row in tutors, publish profile").
router.post('/admin/tutors/applications/:id/approve', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const client = await db.getPool().connect();
  try {
    await client.query('BEGIN');
    const a = (await client.query('SELECT * FROM tutor_applications WHERE id = $1 FOR UPDATE', [req.params.id])).rows[0];
    if (!a) { await client.query('ROLLBACK'); return res.status(404).send('Not found'); }
    if (!(config.NEXT_STATUSES[a.status] || []).includes('approved')) {
      await client.query('ROLLBACK');
      return res.redirect(`/admin/tutors/applications/${a.id}?msg=${encodeURIComponent('Only interviewed applications can be approved')}`);
    }
    const photo = /^https:\/\//.test(str(req.body.photo_url, 500)) ? str(req.body.photo_url, 500) : null;
    await client.query(
      `INSERT INTO tutors (application_id, display_name, bio, photo_url, languages, subjects, levels, availability_note, mode, payout_number, payout_provider, payout_name)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
      [a.id, str(req.body.display_name, 100) || a.full_name.split(/\s+/)[0], str(req.body.bio, 1000), photo, a.languages, a.subjects, a.levels,
        str(req.body.availability_note, 300), a.mode, a.payout_number, a.payout_provider, a.payout_name],
    );
    await client.query("UPDATE tutor_applications SET status = 'approved', updated_at = now() WHERE id = $1", [a.id]);
    await client.query('COMMIT');
    notify({ to: a.email, subject: 'Welcome to Ethel tutors', text: `Hi ${a.full_name},\n\nYour application was approved and your profile is now live. We will introduce you to students as they request tutors.` });
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    console.error('approve failed:', err);
    return res.status(500).send('Approval failed');
  } finally {
    client.release();
  }
  res.redirect(`/admin/tutors/applications/${req.params.id}?msg=Approved+and+published`);
});

// ---------------------------------------------------------------- published tutors
router.get('/admin/tutors/tutors', async (req, res) => {
  const { rows } = await db.query('SELECT * FROM tutors ORDER BY created_at DESC');
  page(res, 'Tutors', `<h1>Tutors</h1><table><thead><tr><th>Name</th><th>Subjects</th><th>Payout</th><th>Active</th><th></th></tr></thead><tbody>
${rows.map((t) => `<tr><td>${esc(t.display_name)}</td><td>${esc(list(t.subjects))}</td><td>${esc(config.PROVIDERS[t.payout_provider] || '-')} ${esc(t.payout_number)} (${esc(t.payout_name)})</td>
<td>${t.active ? 'yes' : 'no'}</td><td><form method="post" action="/admin/tutors/tutors/${esc(t.id)}/toggle" class="inline">${csrf(req)}<button class="btn small">${t.active ? 'Hide' : 'Show'}</button></form></td></tr>`).join('')}
</tbody></table>`, req.query.msg);
});

router.post('/admin/tutors/tutors/:id/toggle', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  await db.query('UPDATE tutors SET active = NOT active WHERE id = $1', [req.params.id]);
  res.redirect('/admin/tutors/tutors');
});

// ---------------------------------------------------------------- bookings (manual matching + payments)
router.get('/admin/tutors/bookings', async (req, res) => {
  const status = config.BOOKING_STATUSES.includes(req.query.status) ? req.query.status : null;
  const rows = (await db.query(
    `SELECT b.*, t.display_name, t.payout_provider, t.payout_number, t.payout_name FROM booking_requests b LEFT JOIN tutors t ON t.id = b.tutor_id
      WHERE ($1::text IS NULL OR b.status = $1) ORDER BY b.created_at DESC LIMIT 300`, [status])).rows;
  const tutors = (await db.query('SELECT id, display_name FROM tutors WHERE active ORDER BY display_name')).rows;
  const cards = rows.map((b) => `<article class="card booking"><h3>${esc(b.student_name)} <span class="pill ${esc(b.status)}">${esc(b.status)}</span> <span class="pill ${esc(b.payment_status)}">${esc(b.payment_status)}</span></h3>
<p>${esc(b.student_contact)} | ${esc(b.student_location || '-')} | ${esc(b.subject || '-')} | ${esc(label(b.preferred_language || 'any language'))} | ${esc(b.preferred_times || '-')}</p>
${b.notes ? `<p><em>${esc(b.notes)}</em></p>` : ''}<p class="note">Requested ${esc(date(b.created_at))}. Tutor: ${esc(b.display_name || 'not matched')}.
Amount: ${b.amount_zmw !== null ? `K${esc(b.amount_zmw)}` : 'not set'}${b.paid_at ? `, paid ${esc(date(b.paid_at))}` : ''}${b.tutor_paid_out ? `, tutor paid out ${esc(date(b.payout_at))}` : ''}</p>
<form method="post" action="/admin/tutors/bookings/${esc(b.id)}" class="form inline-form">${csrf(req)}
<select name="tutor_id"><option value="">No tutor</option>${tutors.map((t) => `<option value="${esc(t.id)}"${t.id === b.tutor_id ? ' selected' : ''}>${esc(t.display_name)}</option>`).join('')}</select>
<select name="status">${config.BOOKING_STATUSES.map((s) => `<option${s === b.status ? ' selected' : ''}>${s}</option>`).join('')}</select>
<input name="amount_zmw" type="number" step="0.01" min="0" placeholder="Amount (ZMW)" value="${esc(b.amount_zmw ?? '')}"><button class="btn small">Save</button></form>
${b.payment_status === 'unpaid' ? `<form method="post" action="/admin/tutors/bookings/${esc(b.id)}/payment" class="form inline-form">${csrf(req)}<input type="hidden" name="direction" value="in">
<input name="reference" placeholder="Mobile money reference" required maxlength="100"><button class="btn small">Record payment received</button></form>` : ''}
${b.payment_status === 'paid' && !b.tutor_paid_out && b.tutor_id ? `<form method="post" action="/admin/tutors/bookings/${esc(b.id)}/payment" class="form inline-form">${csrf(req)}<input type="hidden" name="direction" value="out">
<input name="amount" type="number" step="0.01" min="0" placeholder="Payout amount" required><input name="reference" placeholder="Payout reference" required maxlength="100">
<button class="btn small">Record tutor payout (${esc(config.PROVIDERS[b.payout_provider] || '?')} ${esc(b.payout_number)}, ${esc(b.payout_name)})</button></form>` : ''}
</article>`).join('');
  page(res, 'Bookings', `<h1>Booking requests</h1><form method="get" class="filters"><select name="status"><option value="">Any status</option>${config.BOOKING_STATUSES.map((s) => `<option${s === status ? ' selected' : ''}>${s}</option>`).join('')}</select><button class="btn">Filter</button></form>
<div class="stack">${cards || '<p>No booking requests.</p>'}</div>`, req.query.msg);
});

router.post('/admin/tutors/bookings/:id', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const status = config.BOOKING_STATUSES.includes(req.body.status) ? req.body.status : null;
  const tutorId = UUID.test(str(req.body.tutor_id, 40)) ? str(req.body.tutor_id, 40) : null;
  const amount = req.body.amount_zmw !== '' && Number(req.body.amount_zmw) >= 0 ? Number(req.body.amount_zmw) : null;
  if (!status) return res.status(400).send('Bad status');
  await db.query('UPDATE booking_requests SET tutor_id = $2, status = $3, amount_zmw = $4 WHERE id = $1', [req.params.id, tutorId, status, amount]);
  res.redirect('/admin/tutors/bookings');
});

router.post('/admin/tutors/bookings/:id/payment', async (req, res) => {
  if (!UUID.test(req.params.id)) return res.status(404).send('Not found');
  const inbound = req.body.direction === 'in';
  const client = await db.getPool().connect();
  try {
    await client.query('BEGIN');
    const b = (await client.query(
      `SELECT b.*, t.payout_provider FROM booking_requests b LEFT JOIN tutors t ON t.id = b.tutor_id WHERE b.id = $1 FOR UPDATE OF b`, [req.params.id])).rows[0];
    if (!b) { await client.query('ROLLBACK'); return res.status(404).send('Not found'); }
    const amount = inbound ? Number(b.amount_zmw) : Number(req.body.amount);
    if (!(amount >= 0) || Number.isNaN(amount)) {
      await client.query('ROLLBACK');
      return res.redirect(`/admin/tutors/bookings?msg=${encodeURIComponent('Set the booking amount first')}`);
    }
    if (inbound && b.payment_status !== 'unpaid') { await client.query('ROLLBACK'); return res.redirect('/admin/tutors/bookings'); }
    if (!inbound && (b.payment_status !== 'paid' || b.tutor_paid_out || !b.tutor_id)) { await client.query('ROLLBACK'); return res.redirect('/admin/tutors/bookings'); }
    await client.query(
      'INSERT INTO payments (booking_id, direction, amount_zmw, provider, reference, recorded_by) VALUES ($1,$2,$3,$4,$5,$6)',
      [b.id, inbound ? 'in' : 'out', amount, inbound ? null : b.payout_provider, str(req.body.reference, 100), 'admin'],
    );
    await client.query(
      inbound
        ? "UPDATE booking_requests SET payment_status = 'paid', paid_at = now() WHERE id = $1"
        : 'UPDATE booking_requests SET tutor_paid_out = true, payout_at = now() WHERE id = $1',
      [b.id],
    );
    await client.query('COMMIT');
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    console.error('payment failed:', err);
    return res.status(500).send('Could not record payment');
  } finally {
    client.release();
  }
  res.redirect('/admin/tutors/bookings');
});

module.exports = router;
