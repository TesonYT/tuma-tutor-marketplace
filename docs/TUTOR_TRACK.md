# Ethel tutor track

Implements the Google Doc "Ethel Tutor Marketplace — Design Spec" plus its "Tutor Payments" section.
It lives beside the older in-memory sandbox (`server.js`) and only switches on when `DATABASE_URL` is set.

## Run it

```bash
cp .env.example .env     # fill in; export the vars (the app does not load .env itself)
npm install
npm run db:init          # optional: also runs on server start
npm start
```

| Route | What |
|---|---|
| `/tutors` | Landing |
| `/tutors/apply` | Application form (incl. screening question, payout details, consent) |
| `/tutors/browse` | Approved tutors, filter by language / subject. Never shows contact or payout details |
| `/tutors/book` | Student booking request (manual matching, per spec section 6) |
| `/admin/tutors` | Review queue; detail page with screening answer, rubric, notes, status buttons |
| `/admin/tutors/tutors` | Published tutors, hide/show |
| `/admin/tutors/bookings` | Booking queue: assign tutor, status, record payment in / tutor payout |

Schema is `db/schema.sql` (idempotent). Code is in `tutor-track/`.

## Where this goes beyond or differs from the spec

- **`tutor_applications.rubric jsonb`** added to store the interview scores (spec section 4 suggests the rubric but has no column).
- **`payments` ledger table** is created now (the spec calls it optional); recording a payment writes a ledger row *and* sets the flags on the booking.
- **`tutors.application_id` is UNIQUE** so an application can only be approved once.
- **Payout details are collected at application time** (the spec's default). Its privacy alternative, collecting at approval only, is a small change to the form and approval step if you prefer it.
- **Admin login uses `admin_users`** (bcrypt) seeded from `ADMIN_EMAIL` / `ADMIN_PASSWORD`; one shared login is enough at this scale, as the spec says.
- **Status flow is enforced**: applied → screening → interview_scheduled → interviewed → approved, with reject/withdraw available before approval. Approval happens only through the approve form, which creates the `tutors` row in one transaction.
- **Email** is a stub (`tutor-track/notify.js`): it logs, and POSTs to `NOTIFY_WEBHOOK_URL` if set. Pick a mail provider to make acknowledgements real.

## Open decisions (from the spec's "TO FILL IN" / "Policies to decide")

Set via env, they show as "to be confirmed" until filled: `SESSION_RATE_ZMW`, `SESSION_MINUTES`, `PLATFORM_SHARE_PCT`, `PAYOUT_FREQUENCY`.

Still unwritten and needing your answer before launch: late-cancel policy, tutor no-show, short/cut-off sessions, free trial session, who bears mobile-money charges.

## Not built, deliberately (spec section 6)

Calendar sync, payment APIs, automated matching. Photo upload (profile photos are an https URL for now).

## Deployment notes

- Behind a proxy (Vercel etc.) set `app.set('trust proxy', 1)` so the rate limiters see real client IPs.
- The session store is in-memory; use a persistent store (e.g. `connect-pg-simple`) before running more than one instance.
