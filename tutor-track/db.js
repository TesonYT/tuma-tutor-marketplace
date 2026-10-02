const fs = require('fs');
const path = require('path');
const bcrypt = require('bcryptjs');
const { Pool } = require('pg');

let pool = null;

// The rest of the sandbox runs without a database; the tutor track only switches on when
// DATABASE_URL is set.
function enabled() {
  return Boolean(process.env.DATABASE_URL);
}

function getPool() {
  if (!enabled()) return null;
  if (!pool) {
    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      ssl: process.env.PGSSL === 'require' ? { rejectUnauthorized: false } : undefined,
      max: 5,
    });
  }
  return pool;
}

const query = (text, params) => getPool().query(text, params);

async function init() {
  if (!enabled()) return false;
  await query(fs.readFileSync(path.join(__dirname, '..', 'db', 'schema.sql'), 'utf8'));
  // Seed/refresh the admin login from env so there is no default password to forget about.
  const { ADMIN_EMAIL, ADMIN_PASSWORD } = process.env;
  if (ADMIN_EMAIL && ADMIN_PASSWORD) {
    const hash = await bcrypt.hash(ADMIN_PASSWORD, 12);
    await query(
      `INSERT INTO admin_users (email, password_hash) VALUES ($1, $2)
       ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash`,
      [ADMIN_EMAIL.trim().toLowerCase(), hash],
    );
  }
  return true;
}

module.exports = { enabled, getPool, query, init };
