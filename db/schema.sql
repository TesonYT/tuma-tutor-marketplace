-- Ethel tutor track schema (Design Spec sections 3 and 7). Idempotent: safe to run on every start.
-- Needs Postgres 13+ (gen_random_uuid is built in).

CREATE TABLE IF NOT EXISTS tutor_applications (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name        text NOT NULL,
  email            text NOT NULL,
  phone            text NOT NULL,
  location         text NOT NULL,
  languages        text[],
  subjects         text[],
  levels           text[],
  hours_per_week   integer,
  mode             text CHECK (mode IN ('in_person', 'online', 'either')),
  occupation       text,
  experience       text,
  qualifications   text,
  screening_answer text NOT NULL,
  consent          boolean NOT NULL DEFAULT false,
  -- payout details (Payments section). Kept on the application so approval is a copy, not a re-ask.
  payout_number    text,
  payout_provider  text CHECK (payout_provider IN ('airtel', 'mtn', 'bank', 'other')),
  payout_name      text,
  rate_accepted    boolean NOT NULL DEFAULT false,
  status           text NOT NULL DEFAULT 'applied'
    CHECK (status IN ('applied', 'screening', 'interview_scheduled', 'interviewed', 'approved', 'rejected', 'withdrawn')),
  -- Interview rubric (spec section 4): {"clarity":1-5,"language":1-5,"subject":1-5,"patience":1-5,"reliability":1-5}
  rubric           jsonb,
  admin_notes      text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tutor_applications_status_idx ON tutor_applications (status);

-- Created only when an application is approved.
CREATE TABLE IF NOT EXISTS tutors (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id    uuid UNIQUE REFERENCES tutor_applications(id),
  display_name      text NOT NULL,
  bio               text,
  photo_url         text,
  languages         text[],
  subjects          text[],
  levels            text[],
  availability_note text,
  mode              text,
  payout_number     text,
  payout_provider   text CHECK (payout_provider IN ('airtel', 'mtn', 'bank', 'other')),
  payout_name       text,
  payout_notes      text,
  active            boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS booking_requests (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tutor_id           uuid REFERENCES tutors(id),   -- nullable: student may have no preference
  student_name       text NOT NULL,
  student_contact    text NOT NULL,
  student_location   text,
  subject            text,
  preferred_language text,
  preferred_times    text,
  notes              text,
  status             text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'matched', 'confirmed', 'closed')),
  amount_zmw         numeric,
  payment_status     text NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'paid', 'refunded')),
  paid_at            timestamptz,
  tutor_paid_out     boolean NOT NULL DEFAULT false,
  payout_at          timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS booking_requests_status_idx ON booking_requests (status);

-- Ledger: one row per payment in (from student) or out (to tutor).
CREATE TABLE IF NOT EXISTS payments (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  uuid REFERENCES booking_requests(id),
  direction   text NOT NULL CHECK (direction IN ('in', 'out')),
  amount_zmw  numeric NOT NULL,
  provider    text,
  reference   text,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  recorded_by text
);

CREATE TABLE IF NOT EXISTS admin_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  password_hash text NOT NULL
);
