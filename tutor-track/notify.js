// Email hook. The spec wants an auto-acknowledgement to applicants and an email to the admin on each
// booking request. No mail provider is chosen yet, so this logs the message and, if
// NOTIFY_WEBHOOK_URL is set, POSTs {to, subject, text} to it (e.g. a Zapier/Resend/Make endpoint).
// Failures never break the request that triggered them.
async function notify({ to, subject, text }) {
  console.log(`[notify] to=${to} subject=${subject}`);
  const url = process.env.NOTIFY_WEBHOOK_URL;
  if (!url) return;
  try {
    await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ to, subject, text }) });
  } catch (err) {
    console.error('[notify] webhook failed:', err.message);
  }
}

const adminEmail = () => process.env.ADMIN_NOTIFY_EMAIL || process.env.ADMIN_EMAIL || null;

module.exports = { notify, adminEmail };
