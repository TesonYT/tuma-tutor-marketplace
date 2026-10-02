// Fixed lists and the payment terms the spec leaves blank ("TO FILL IN"). Terms come from env so
// they can be set without a deploy; unset ones render as "to be confirmed" rather than a made-up number.

const num = (v) => (v !== undefined && v !== '' && !Number.isNaN(Number(v)) ? Number(v) : null);

module.exports = {
  LANGUAGES: ['english', 'silozi', 'bemba', 'nyanja', 'tonga', 'other'],
  SUBJECTS: [
    'Mathematics', 'English', 'Science', 'Biology', 'Chemistry', 'Physics', 'Social studies',
    'Civic education', 'Geography', 'History', 'ICT', 'Business studies', 'Accounting', 'Other',
  ],
  LEVELS: ['Primary (grades 1-7)', 'Junior secondary (grades 8-9)', 'Senior secondary (grades 10-12)', 'College / university'],
  MODES: { in_person: 'In person', online: 'Online', either: 'Either' },
  PROVIDERS: { airtel: 'Airtel Money', mtn: 'MTN MoMo', bank: 'Bank transfer', other: 'Other' },

  STATUSES: ['applied', 'screening', 'interview_scheduled', 'interviewed', 'approved', 'rejected', 'withdrawn'],
  // Allowed next steps from each status (spec section 4). approved/rejected/withdrawn are terminal here.
  NEXT_STATUSES: {
    applied: ['screening', 'rejected', 'withdrawn'],
    screening: ['interview_scheduled', 'rejected', 'withdrawn'],
    interview_scheduled: ['interviewed', 'rejected', 'withdrawn'],
    interviewed: ['approved', 'rejected', 'withdrawn'],
    approved: [],
    rejected: [],
    withdrawn: [],
  },
  BOOKING_STATUSES: ['new', 'matched', 'confirmed', 'closed'],
  RUBRIC: [
    ['clarity', 'Clarity of explanation'],
    ['language', 'Language fluency'],
    ['subject', 'Subject command'],
    ['patience', 'Patience and manner with children'],
    ['reliability', 'Reliability signals'],
  ],
  // Screening answer length: spec asks for "about 150 words".
  SCREENING_MIN_WORDS: 40,
  SCREENING_MAX_WORDS: 400,

  terms() {
    return {
      rateZmw: num(process.env.SESSION_RATE_ZMW),
      sessionMinutes: num(process.env.SESSION_MINUTES),
      platformSharePct: num(process.env.PLATFORM_SHARE_PCT),
      payoutFrequency: process.env.PAYOUT_FREQUENCY || null, // weekly | fortnightly | monthly
      acceptedProviders: process.env.STUDENT_PAYMENT_PROVIDERS || 'Airtel Money, MTN MoMo',
    };
  },
};
