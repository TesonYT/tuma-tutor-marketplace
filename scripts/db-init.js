// Applies db/schema.sql (and seeds the admin login from ADMIN_EMAIL / ADMIN_PASSWORD). Safe to re-run.
const db = require('../tutor-track/db');

db.init()
  .then((on) => {
    console.log(on ? 'Schema applied.' : 'DATABASE_URL is not set; nothing to do.');
    return db.enabled() ? db.getPool().end() : null;
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
