// Minimal server-side HTML helpers. Everything user-supplied goes through esc().
const config = require('./config');

const esc = (v) =>
  String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const label = (s) => String(s || '').replace(/_/g, ' ');
const list = (a) => (a && a.length ? a.map(label).join(', ') : '-');
const date = (d) => (d ? new Date(d).toISOString().slice(0, 16).replace('T', ' ') : '-');

function layout({ title, body, admin = false, flash = '' }) {
  const nav = admin
    ? `<a href="/admin/tutors">Applications</a><a href="/admin/tutors/tutors">Tutors</a><a href="/admin/tutors/bookings">Bookings</a>
       <form method="post" action="/admin/tutors/logout" class="inline"><button class="link">Log out</button></form>`
    : `<a href="/tutors">Tutor track</a><a href="/tutors/browse">Browse tutors</a><a href="/tutors/book">Request a tutor</a><a href="/tutors/apply">Apply to tutor</a>`;
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} - Ethel</title><link rel="stylesheet" href="/tutors.css"></head>
<body><header><a class="brand" href="/tutors">Ethel</a><nav>${nav}</nav></header>
<main>${flash ? `<p class="flash">${esc(flash)}</p>` : ''}${body}</main>
<footer>Ethel human tutor track</footer></body></html>`;
}

function termsBlurb() {
  const t = config.terms();
  const parts = [];
  parts.push(t.rateZmw !== null ? `K${t.rateZmw} per session` : 'Session rate to be confirmed');
  if (t.sessionMinutes !== null) parts.push(`${t.sessionMinutes} minutes`);
  return parts.join(' - ');
}

function checkboxes(name, options, selected = []) {
  return options
    .map((o) => `<label class="chk"><input type="checkbox" name="${name}" value="${esc(o)}"${selected.includes(o) ? ' checked' : ''}> ${esc(label(o))}</label>`)
    .join('');
}

module.exports = { esc, label, list, date, layout, termsBlurb, checkboxes };
