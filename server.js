const express = require('express');
const session = require('express-session');
const bcrypt = require('bcryptjs');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');
const multer = require('multer');
const QRCode = require('qrcode');

const app = express();
app.use(express.json({ limit: '4mb' })); // raised to fit base64 tutor profile photos
app.use(session({
  secret: crypto.randomBytes(32).toString('hex'),
  resave: false,
  saveUninitialized: false,
  cookie: { httpOnly: true, sameSite: 'lax' },
}));
app.use(express.static(path.join(__dirname, 'public')));

// Video files live outside public/ deliberately — they're only reachable through the
// access-controlled /api/videos/:id/stream route, never as a static file.
const VIDEO_UPLOAD_DIR = path.join(__dirname, 'uploads', 'videos');
fs.mkdirSync(VIDEO_UPLOAD_DIR, { recursive: true });
const VIDEO_MIN_PRICE = 5;
const VIDEO_MAX_PRICE = 100;
const videoUpload = multer({
  storage: multer.diskStorage({
    destination: VIDEO_UPLOAD_DIR,
    filename: (req, file, cb) => {
      const ext = path.extname(file.originalname) || '.mp4';
      cb(null, `${Date.now()}-${crypto.randomBytes(4).toString('hex')}${ext}`);
    },
  }),
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    if (!file.mimetype.startsWith('video/')) return cb(new Error('Only video files are allowed'));
    cb(null, true);
  },
});

// --- In-memory data store (resets on restart — sandbox only, not persisted) ---

const LOCATIONS = [
  'Kwame Nkrumah University (Kabwe, Zambia)',
  'Remote / Online',
];

// Sourced from https://www.nkrumah.edu.zm/home/programmes.php (School of Business Studies
// and School of Education programme lists) — deliberately scoped to just these two sectors.
const COURSES = {
  Education: [
    "Bachelor of Primary Education",
    "Bachelor of Arts with Education - Physical Education Sport & Mathematics",
    "Bachelor of Arts with Education - Special Education Needs & Physical Education and Sport",
    "Bachelor of Arts with Education - Special Education Needs & French",
    "Bachelor of Arts in Educational Administration and Management",
    "Bachelor of Education in Sociology of Education",
    "Bachelor of Education in Educational Psychology",
    "Bachelor of Education in Early Childhood Education",
    "Early Childhood Education Teacher's Diploma",
    "Primary Teacher's Diploma",
  ],
  Business: [
    "Bachelor of Business Studies with Education",
    "Bachelor of ICT with Education",
    "Bachelor of Arts in Entrepreneurship with Education",
    "Bachelor of Arts in Entrepreneurship",
    "Bachelor of Arts in Economics",
    "Bachelor of Accountancy",
    "Bachelor of Business Administration",
    "Bachelor of Science in Marketing",
    "Bachelor of Arts in Human Resources",
    "Bachelor of Science in Procurement and Supply Chain Management",
  ],
};

const ALL_COURSES = [...COURSES.Education, ...COURSES.Business];

const MAX_LEVEL = 10;
// Fixed platform-wide hourly rate by tutor level (ZMW/hour) — level 1 = K15, level 10 (max) = K25,
// linearly interpolated in between. Tutors no longer set their own rate.
const LEVEL_RATES = [15, 16, 17, 18, 19, 21, 22, 23, 24, 25];

function hourlyRateForLevel(level) {
  const clamped = Math.min(Math.max(Math.round(level), 1), MAX_LEVEL);
  return LEVEL_RATES[clamped - 1];
}

// Demo-only placeholder — real commission structure is an open question, not decided.
const COMMISSION_RATE = 0.15;
// Demo-only placeholder discount for bundling hours into a monthly subscription.
const SUBSCRIPTION_DISCOUNT = 0.2;
// Blended flat rate the platform subscription is priced on (average of LEVEL_RATES) — since the
// subscription is to the app, not to any one tutor, it can't be priced off a single tutor's rate.
// Each tutor is still paid their own level rate per lesson regardless of this number.
const PLATFORM_HOURLY_RATE = 20;

const PAYMENT_METHODS = ['airtel_money', 'bank_card'];

// Demo-only: real deployment would give a student ~24-48h to confirm attendance before
// auto-releasing payment. Compressed to 60s here so the flow is actually testable live.
const LESSON_AUTO_RELEASE_MS = 60 * 1000;
const MAX_PHOTO_DATA_URL_LENGTH = 2_000_000; // ~1.5MB of image data, base64-encoded

let nextUserId = 1;
let nextTutorId = 1;
let nextApplicationId = 1;
let nextReviewId = 1;
let nextSubscriptionId = 1;
let nextPaymentRequestId = 1;
let nextLessonId = 1;
let nextNewsId = 1;
let nextVideoId = 1;
let nextVideoPurchaseId = 1;
let nextVideoPaymentRequestId = 1;

const users = []; // { id, name, email, passwordHash, role: 'student' | 'tutor' }

const tutors = [
  {
    id: nextTutorId++,
    ownerId: null,
    name: 'Mwansa Banda',
    email: 'mwansa@example.com',
    subjects: ['Bachelor of Accountancy', 'Bachelor of Business Administration'],
    location: LOCATIONS[0],
    level: 1,
    bio: 'Final-year accountancy student, tutors first-years in accounting and business fundamentals.',
    photoUrl: null,
  },
  {
    id: nextTutorId++,
    ownerId: null,
    name: 'Chanda Mulenga',
    email: 'chanda@example.com',
    subjects: ['Bachelor of Education in Early Childhood Education', 'Bachelor of Primary Education'],
    location: LOCATIONS[1],
    level: MAX_LEVEL,
    bio: 'Education graduate, tutors early childhood and primary education coursework online.',
    photoUrl: null,
  },
];

const applications = [];
const reviews = []; // { id, tutorId, studentUserId, studentName, rating, comment, createdAt }
// Subscription is to the APP, not to any one tutor: a student buys a pool of hours per month and
// can spend them with any tutor. { id, studentUserId, studentName, hoursPerMonth, hoursRemaining,
// monthlyPrice, paymentMethod, paymentReference, paymentDetail, status, createdAt }
const subscriptions = [];
// Simulated Airtel Money "prompt to pay" (STK-push-style) requests, awaiting approval on the
// student's phone. { id, studentUserId, studentName, hoursPerMonth, monthlyPrice, phone, status: 'pending'|'approved'|'declined', createdAt }
const paymentRequests = [];
// In-person lesson verification. status: 'pending_verification' | 'verified' | 'auto_released'
// { id, applicationId, tutorId, tutorName, studentUserId, studentName, payoutAmount, verifyToken, startedAt, autoReleaseAt, verifiedAt, releasedAt }
const lessons = [];
const lessonTimers = new Map(); // lessonId -> Timeout, cleared once verified early

// School news/updates — manually curated (not live-scraped: Facebook blocks scraping and
// requires API access we don't have, and picking what's "worth showing" needs human judgment
// anyway). Seeded from real items on https://www.nkrumah.edu.zm/home/ as a starting point.
const news = [
  {
    id: nextNewsId++,
    title: 'KNU and OISE University of Toronto Enhance Global Learning',
    summary: 'Kwame Nkrumah University partnered with the Ontario Institute for Studies in Education for their first Global Classroom meeting, marking a new international collaboration.',
    source: 'website',
    url: 'https://www.nkrumah.edu.zm/home/',
    publishedDate: '2024-04-24',
    addedBy: 'Seeded from university website',
    createdAt: new Date().toISOString(),
  },
  {
    id: nextNewsId++,
    title: 'Academic Calendar Adjustments for August 2026 ODL Residential Sessions',
    summary: 'The university announced changes to the August 2026 residential session schedule for Open and Distance Learning (ODL) students.',
    source: 'website',
    url: 'https://www.nkrumah.edu.zm/home/',
    publishedDate: '2026-07-24',
    addedBy: 'Seeded from university website',
    createdAt: new Date().toISOString(),
  },
];

// Recorded lesson videos a tutor posts for students to buy and watch on demand.
// { id, tutorId, tutorName, title, description, price, filename, mimeType, sizeBytes, createdAt }
const videos = [];
// { id, videoId, videoTitle, tutorId, studentUserId, studentName, price, tutorReceives,
//   paymentMethod, paymentReference, paymentDetail, purchasedAt }
const videoPurchases = [];
// Airtel Money prompt-to-pay requests for a video purchase, same pattern as subscription payment.
// { id, videoId, videoTitle, tutorId, studentUserId, studentName, price, phone, status, createdAt }
const videoPaymentRequests = [];

// --- Auth helpers ---

function publicUser(u) {
  return { id: u.id, name: u.name, email: u.email, role: u.role, currentStreak: u.currentStreak, longestStreak: u.longestStreak };
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// Called whenever a session resolves to a user (login, register, or just opening the app with
// an existing session) — so a streak counts a day as "shown up" without forcing a fresh login
// every time. No-ops if already counted today.
function markDailyLogin(user) {
  const today = todayStr();
  if (user.lastLoginDate === today) return;
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  user.currentStreak = user.lastLoginDate === yesterday ? user.currentStreak + 1 : 1;
  user.longestStreak = Math.max(user.longestStreak, user.currentStreak);
  user.lastLoginDate = today;
}

function requireAuth(role) {
  return (req, res, next) => {
    const user = users.find((u) => u.id === req.session.userId);
    if (!user) return res.status(401).json({ error: 'Log in required' });
    if (role && user.role !== role) return res.status(403).json({ error: `Must be logged in as a ${role}` });
    req.user = user;
    next();
  };
}

function tutorWithRating(t) {
  const tutorReviews = reviews.filter((r) => r.tutorId === t.id);
  const averageRating = tutorReviews.length
    ? round2(tutorReviews.reduce((sum, r) => sum + r.rating, 0) / tutorReviews.length)
    : null;
  return {
    ...t,
    averageRating,
    reviewCount: tutorReviews.length,
    hourlyRate: hourlyRateForLevel(t.level),
    maxLevel: MAX_LEVEL,
    classTutoringUnlocked: t.level >= MAX_LEVEL,
  };
}

// Simulated payment authorization — no real payment gateway is called. Card numbers/CVCs are
// never stored: only a masked last-4 is kept, matching how a real integration would handle it
// (raw PAN/CVC belong to the payment processor, not this app's database).
function validateCardPayment(body) {
  const cardNumber = String(body.cardNumber || '').replace(/\s+/g, '');
  const expiry = String(body.cardExpiry || '').trim();
  const cvc = String(body.cardCvc || '').trim();
  if (!/^\d{12,19}$/.test(cardNumber)) {
    throw new PaymentError('Enter a valid card number');
  }
  if (!/^(0[1-9]|1[0-2])\/\d{2}$/.test(expiry)) {
    throw new PaymentError('Card expiry must be in MM/YY format');
  }
  if (!/^\d{3,4}$/.test(cvc)) {
    throw new PaymentError('Enter a valid CVC');
  }
  return {
    method: 'bank_card',
    reference: `CARD-${crypto.randomBytes(4).toString('hex').toUpperCase()}`,
    detail: `Card ending ${cardNumber.slice(-4)}`,
  };
}

function createSubscriptionRecord(user, hours, monthlyPrice, payment) {
  return {
    id: nextSubscriptionId++,
    studentUserId: user.id,
    studentName: user.name,
    hoursPerMonth: hours,
    hoursRemaining: hours,
    monthlyPrice,
    paymentMethod: payment.method,
    paymentReference: payment.reference,
    paymentDetail: payment.detail,
    status: 'active',
    createdAt: new Date().toISOString(),
  };
}

// Marks a lesson as confirmed (whether by the student's action or by timeout) and clears
// its pending auto-release timer. Idempotent: no-ops if the lesson isn't still pending.
function finalizeLesson(lesson, status) {
  if (lesson.status !== 'pending_verification') return lesson;
  lesson.status = status;
  const timestampField = status === 'verified' ? 'verifiedAt' : 'releasedAt';
  lesson[timestampField] = new Date().toISOString();
  const timer = lessonTimers.get(lesson.id);
  if (timer) {
    clearTimeout(timer);
    lessonTimers.delete(lesson.id);
  }
  return lesson;
}

function videoWithAccess(v, user) {
  let purchased = false;
  if (user) {
    if (videoPurchases.some((p) => p.videoId === v.id && p.studentUserId === user.id)) purchased = true;
    const tutor = tutors.find((t) => t.id === v.tutorId);
    if (tutor && tutor.ownerId === user.id) purchased = true; // tutor can preview their own upload
  }
  return { ...v, purchased };
}

function createVideoPurchaseRecord(video, user, payment) {
  return {
    id: nextVideoPurchaseId++,
    videoId: video.id,
    videoTitle: video.title,
    tutorId: video.tutorId,
    studentUserId: user.id,
    studentName: user.name,
    price: video.price,
    tutorReceives: round2(video.price * (1 - COMMISSION_RATE)),
    paymentMethod: payment.method,
    paymentReference: payment.reference,
    paymentDetail: payment.detail,
    purchasedAt: new Date().toISOString(),
  };
}

class PaymentError extends Error {}

// --- Auth routes ---

app.post('/api/auth/register', (req, res) => {
  const { name, email, password, role } = req.body;
  if (!name || !email || !password || !['student', 'tutor'].includes(role)) {
    return res.status(400).json({ error: 'Missing or invalid fields' });
  }
  if (password.length < 6) {
    return res.status(400).json({ error: 'Password must be at least 6 characters' });
  }
  if (users.some((u) => u.email.toLowerCase() === email.toLowerCase())) {
    return res.status(400).json({ error: 'An account with that email already exists' });
  }
  const user = {
    id: nextUserId++,
    name,
    email,
    passwordHash: bcrypt.hashSync(password, 10),
    role,
    currentStreak: 0,
    longestStreak: 0,
    lastLoginDate: null,
  };
  users.push(user);
  markDailyLogin(user);
  req.session.userId = user.id;
  res.status(201).json(publicUser(user));
});

app.post('/api/auth/login', (req, res) => {
  const { email, password } = req.body;
  const user = users.find((u) => u.email.toLowerCase() === String(email || '').toLowerCase());
  if (!user || !bcrypt.compareSync(password || '', user.passwordHash)) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }
  markDailyLogin(user);
  req.session.userId = user.id;
  res.json(publicUser(user));
});

app.post('/api/auth/logout', (req, res) => {
  req.session.destroy(() => res.json({ ok: true }));
});

app.get('/api/auth/me', (req, res) => {
  const user = users.find((u) => u.id === req.session.userId);
  if (user) markDailyLogin(user);
  res.json(user ? publicUser(user) : null);
});

// --- Reference data (marketplace is login-gated: any authenticated account, either role) ---

app.get('/api/locations', requireAuth(), (req, res) => res.json(LOCATIONS));

app.get('/api/courses', requireAuth(), (req, res) => res.json(COURSES));

app.get('/api/pricing', requireAuth(), (req, res) => res.json({
  levelRates: LEVEL_RATES,
  maxLevel: MAX_LEVEL,
  platformHourlyRate: PLATFORM_HOURLY_RATE,
  commissionRate: COMMISSION_RATE,
  subscriptionDiscount: SUBSCRIPTION_DISCOUNT,
  paymentMethods: PAYMENT_METHODS,
  lessonAutoReleaseSeconds: LESSON_AUTO_RELEASE_MS / 1000,
  videoMinPrice: VIDEO_MIN_PRICE,
  videoMaxPrice: VIDEO_MAX_PRICE,
}));

// --- News (curated, not live-scraped — see comment on the `news` array) ---

app.get('/api/news', requireAuth(), (req, res) => {
  res.json(news.slice().sort((a, b) => b.publishedDate.localeCompare(a.publishedDate)));
});

app.post('/api/news', requireAuth(), (req, res) => {
  const { title, summary, source, url, publishedDate } = req.body;
  if (!title || !summary) {
    return res.status(400).json({ error: 'Title and summary are required' });
  }
  const item = {
    id: nextNewsId++,
    title,
    summary,
    source: ['facebook', 'website', 'other'].includes(source) ? source : 'other',
    url: url || '',
    publishedDate: publishedDate || todayStr(),
    addedBy: req.user.name,
    createdAt: new Date().toISOString(),
  };
  news.push(item);
  res.status(201).json(item);
});

app.delete('/api/news/:id', requireAuth(), (req, res) => {
  const idx = news.findIndex((n) => n.id === Number(req.params.id));
  if (idx === -1) return res.status(404).json({ error: 'News item not found' });
  news.splice(idx, 1);
  res.json({ ok: true });
});

// --- Tutors ---

app.get('/api/tutors', requireAuth(), (req, res) => {
  const { subject, location } = req.query;
  let result = tutors;
  if (subject) {
    const q = subject.toLowerCase();
    result = result.filter((t) => t.subjects.some((s) => s.toLowerCase().includes(q)));
  }
  if (location) {
    result = result.filter((t) => t.location === location);
  }
  res.json(result.map(tutorWithRating));
});

app.get('/api/tutors/mine', requireAuth('tutor'), (req, res) => {
  res.json(tutors.filter((t) => t.ownerId === req.user.id).map(tutorWithRating));
});

app.get('/api/tutors/:id', requireAuth(), (req, res) => {
  const tutor = tutors.find((t) => t.id === Number(req.params.id));
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  res.json(tutorWithRating(tutor));
});

app.post('/api/tutors', requireAuth('tutor'), (req, res) => {
  const { subjects, location, bio, photoDataUrl } = req.body;
  if (!subjects || !location) {
    return res.status(400).json({ error: 'Missing required fields' });
  }
  const subjectList = Array.isArray(subjects) ? subjects : [subjects];
  const invalid = subjectList.filter((s) => !ALL_COURSES.includes(s));
  if (subjectList.length === 0 || invalid.length > 0) {
    return res.status(400).json({
      error: `Courses must be selected from the Kwame Nkrumah Education/Business sector list. Invalid: ${invalid.join(', ') || 'none selected'}`,
    });
  }
  let photoUrl = null;
  if (photoDataUrl) {
    if (typeof photoDataUrl !== 'string' || !photoDataUrl.startsWith('data:image/')) {
      return res.status(400).json({ error: 'Profile photo must be an image file' });
    }
    if (photoDataUrl.length > MAX_PHOTO_DATA_URL_LENGTH) {
      return res.status(400).json({ error: 'Profile photo is too large (max ~1.5MB)' });
    }
    photoUrl = photoDataUrl;
  }
  const tutor = {
    id: nextTutorId++,
    ownerId: req.user.id,
    name: req.user.name,
    email: req.user.email,
    subjects: subjectList,
    location,
    level: 1,
    bio: bio || '',
    photoUrl,
  };
  tutors.push(tutor);
  res.status(201).json(tutorWithRating(tutor));
});

app.post('/api/tutors/:id/level-up', requireAuth('tutor'), (req, res) => {
  const tutor = tutors.find((t) => t.id === Number(req.params.id));
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  if (tutor.ownerId !== req.user.id) return res.status(403).json({ error: 'Not your tutor profile' });
  tutor.level = Math.min(tutor.level + 1, MAX_LEVEL);
  res.json(tutorWithRating(tutor));
});

app.get('/api/tutors/:id/applications', requireAuth('tutor'), (req, res) => {
  const tutor = tutors.find((t) => t.id === Number(req.params.id));
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  if (tutor.ownerId !== req.user.id) return res.status(403).json({ error: 'Not your tutor profile' });
  res.json(applications.filter((a) => a.tutorId === tutor.id));
});

app.post('/api/applications', requireAuth('student'), (req, res) => {
  const { tutorId, message } = req.body;
  const tutor = tutors.find((t) => t.id === Number(tutorId));
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  const application = {
    id: nextApplicationId++,
    tutorId: tutor.id,
    studentUserId: req.user.id,
    studentName: req.user.name,
    studentEmail: req.user.email,
    message: message || '',
    status: 'pending',
    createdAt: new Date().toISOString(),
  };
  applications.push(application);
  res.status(201).json(application);
});

// --- Reviews ---

app.get('/api/tutors/:id/reviews', requireAuth(), (req, res) => {
  const tutorId = Number(req.params.id);
  res.json(reviews.filter((r) => r.tutorId === tutorId));
});

app.post('/api/tutors/:id/reviews', requireAuth('student'), (req, res) => {
  const tutorId = Number(req.params.id);
  const tutor = tutors.find((t) => t.id === tutorId);
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  const ratingNum = Number(req.body.rating);
  if (!Number.isInteger(ratingNum) || ratingNum < 1 || ratingNum > 5) {
    return res.status(400).json({ error: 'Rating must be an integer from 1 to 5' });
  }
  if (reviews.some((r) => r.tutorId === tutorId && r.studentUserId === req.user.id)) {
    return res.status(400).json({ error: 'You have already reviewed this tutor' });
  }
  const review = {
    id: nextReviewId++,
    tutorId,
    studentUserId: req.user.id,
    studentName: req.user.name,
    rating: ratingNum,
    comment: req.body.comment || '',
    createdAt: new Date().toISOString(),
  };
  reviews.push(review);
  res.status(201).json(review);
});

// --- App subscription (monthly hour pool, spendable with any tutor) — payment is simulated ---

app.get('/api/subscriptions/mine', requireAuth('student'), (req, res) => {
  res.json(subscriptions.filter((s) => s.studentUserId === req.user.id));
});

app.post('/api/subscriptions', requireAuth('student'), (req, res) => {
  const { hoursPerMonth, paymentMethod } = req.body;
  if (subscriptions.some((s) => s.studentUserId === req.user.id && s.status === 'active')) {
    return res.status(400).json({ error: 'You already have an active subscription' });
  }
  if (!PAYMENT_METHODS.includes(paymentMethod)) {
    return res.status(400).json({ error: 'Payment method must be Airtel Money or a bank card' });
  }

  const hours = Math.max(1, Number(hoursPerMonth) || 1);
  const monthlyPrice = round2(PLATFORM_HOURLY_RATE * hours * (1 - SUBSCRIPTION_DISCOUNT));

  if (paymentMethod === 'airtel_money') {
    const phone = String(req.body.airtelPhone || '').trim();
    if (!/^0\d{9}$/.test(phone)) {
      return res.status(400).json({ error: 'Enter a valid Airtel Money number, e.g. 0977123456' });
    }
    if (paymentRequests.some((p) => p.studentUserId === req.user.id && p.status === 'pending')) {
      return res.status(400).json({ error: 'A payment prompt is already pending — check your phone' });
    }
    const paymentRequest = {
      id: nextPaymentRequestId++,
      studentUserId: req.user.id,
      studentName: req.user.name,
      hoursPerMonth: hours,
      monthlyPrice,
      phone,
      status: 'pending',
      createdAt: new Date().toISOString(),
    };
    paymentRequests.push(paymentRequest);
    return res.status(202).json({
      requiresApproval: true,
      paymentRequestId: paymentRequest.id,
      phone,
      monthlyPrice,
      message: `A payment prompt for K${monthlyPrice} has been sent to ${phone}. Approve it on your phone to activate the subscription.`,
    });
  }

  let payment;
  try {
    payment = validateCardPayment(req.body);
  } catch (err) {
    if (err instanceof PaymentError) return res.status(400).json({ error: err.message });
    throw err;
  }
  const subscription = createSubscriptionRecord(req.user, hours, monthlyPrice, payment);
  subscriptions.push(subscription);
  res.status(201).json(subscription);
});

// Simulates the student approving the Airtel Money "prompt to pay" on their phone.
app.post('/api/payment-requests/:id/approve', requireAuth('student'), (req, res) => {
  const paymentRequest = paymentRequests.find((p) => p.id === Number(req.params.id));
  if (!paymentRequest) return res.status(404).json({ error: 'Payment request not found' });
  if (paymentRequest.studentUserId !== req.user.id) return res.status(403).json({ error: 'Not your payment request' });
  if (paymentRequest.status !== 'pending') return res.status(400).json({ error: `Payment request already ${paymentRequest.status}` });

  if (subscriptions.some((s) => s.studentUserId === req.user.id && s.status === 'active')) {
    paymentRequest.status = 'declined';
    return res.status(400).json({ error: 'You already have an active subscription' });
  }

  paymentRequest.status = 'approved';
  const payment = {
    method: 'airtel_money',
    reference: `AIRTEL-${crypto.randomBytes(4).toString('hex').toUpperCase()}`,
    detail: `Airtel Money ${paymentRequest.phone}`,
  };
  const subscription = createSubscriptionRecord(req.user, paymentRequest.hoursPerMonth, paymentRequest.monthlyPrice, payment);
  subscriptions.push(subscription);
  res.status(201).json(subscription);
});

app.post('/api/payment-requests/:id/decline', requireAuth('student'), (req, res) => {
  const paymentRequest = paymentRequests.find((p) => p.id === Number(req.params.id));
  if (!paymentRequest) return res.status(404).json({ error: 'Payment request not found' });
  if (paymentRequest.studentUserId !== req.user.id) return res.status(403).json({ error: 'Not your payment request' });
  if (paymentRequest.status !== 'pending') return res.status(400).json({ error: `Payment request already ${paymentRequest.status}` });
  paymentRequest.status = 'declined';
  res.json({ id: paymentRequest.id, status: 'declined' });
});

// --- Lessons: proof-of-attendance before a tutor's payout for that hour is released ---
//
// A tutor taps "Start lesson" once they're physically with the student. That generates a QR
// code + fallback numeric code tied to this one lesson. The student confirms either by scanning
// the QR (which opens /verify/:token in their phone's browser) or by typing the code into the
// app. If the student never confirms — including simply being unreachable — the payout still
// auto-releases to the tutor after LESSON_AUTO_RELEASE_MS, so a non-responsive student can never
// cost the tutor their payment.

app.get('/api/tutors/:id/lessons', requireAuth('tutor'), (req, res) => {
  const tutor = tutors.find((t) => t.id === Number(req.params.id));
  if (!tutor) return res.status(404).json({ error: 'Tutor not found' });
  if (tutor.ownerId !== req.user.id) return res.status(403).json({ error: 'Not your tutor profile' });
  res.json(lessons.filter((l) => l.tutorId === tutor.id));
});

app.get('/api/lessons/mine', requireAuth('student'), (req, res) => {
  // verifyToken is deliberately withheld from students: it must come from the tutor's QR/code
  // display, never from the student's own API, or "proof of attendance" would prove nothing.
  res.json(lessons.filter((l) => l.studentUserId === req.user.id).map(({ verifyToken, ...rest }) => rest));
});

app.get('/api/lessons/:id', requireAuth('tutor'), (req, res) => {
  const lesson = lessons.find((l) => l.id === Number(req.params.id));
  if (!lesson) return res.status(404).json({ error: 'Lesson not found' });
  const tutor = tutors.find((t) => t.id === lesson.tutorId);
  if (!tutor || tutor.ownerId !== req.user.id) return res.status(403).json({ error: 'Not your lesson' });
  res.json(lesson);
});

// A tutor starts a lesson against a student who applied to them. That draws 1 hour from the
// student's platform-wide subscription pool (not tied to this specific tutor) and pays the
// tutor their own level rate — the platform absorbs the difference between what any given
// student's subscription was priced at and what any given tutor is owed.
app.post('/api/applications/:id/lessons', requireAuth('tutor'), async (req, res) => {
  const application = applications.find((a) => a.id === Number(req.params.id));
  if (!application) return res.status(404).json({ error: 'Application not found' });
  const tutor = tutors.find((t) => t.id === application.tutorId);
  if (!tutor || tutor.ownerId !== req.user.id) return res.status(403).json({ error: 'Not your application to start a lesson for' });

  const subscription = subscriptions.find((s) => s.studentUserId === application.studentUserId && s.status === 'active');
  if (!subscription || subscription.hoursRemaining < 1) {
    return res.status(400).json({ error: `${application.studentName} has no active subscription hours remaining` });
  }
  subscription.hoursRemaining -= 1;

  const lesson = {
    id: nextLessonId++,
    applicationId: application.id,
    tutorId: tutor.id,
    tutorName: tutor.name,
    studentUserId: application.studentUserId,
    studentName: application.studentName,
    payoutAmount: round2(hourlyRateForLevel(tutor.level) * (1 - COMMISSION_RATE)),
    verifyToken: crypto.randomBytes(4).toString('hex'),
    status: 'pending_verification',
    startedAt: new Date().toISOString(),
    autoReleaseAt: new Date(Date.now() + LESSON_AUTO_RELEASE_MS).toISOString(),
    verifiedAt: null,
    releasedAt: null,
  };
  lessons.push(lesson);
  lessonTimers.set(lesson.id, setTimeout(() => finalizeLesson(lesson, 'auto_released'), LESSON_AUTO_RELEASE_MS));

  const verifyUrl = `${req.protocol}://${req.get('host')}/verify/${lesson.verifyToken}`;
  const qrDataUrl = await QRCode.toDataURL(verifyUrl, { margin: 1, width: 240 });
  res.status(201).json({ ...lesson, verifyUrl, qrDataUrl, code: lesson.verifyToken, hoursRemaining: subscription.hoursRemaining });
});

app.post('/api/lessons/verify-by-code', requireAuth('student'), (req, res) => {
  const code = String(req.body.code || '').trim().toLowerCase();
  const lesson = lessons.find((l) => l.verifyToken === code);
  if (!lesson || lesson.studentUserId !== req.user.id) {
    return res.status(404).json({ error: "Invalid code, or this lesson isn't assigned to your account" });
  }
  if (lesson.status !== 'pending_verification') {
    return res.status(400).json({ error: `This lesson was already ${lesson.status.replace('_', ' ')}` });
  }
  finalizeLesson(lesson, 'verified');
  res.json(lesson);
});

// Scanning the QR opens this page (GET, read-only — no state change on a bare visit/prefetch).
app.get('/verify/:token', (req, res) => {
  const lesson = lessons.find((l) => l.verifyToken === req.params.token);
  res.type('html').send(renderVerifyPage(req, lesson));
});

// The page's "Confirm" button submits here — this is the only thing that actually verifies.
app.post('/verify/:token', (req, res) => {
  const lesson = lessons.find((l) => l.verifyToken === req.params.token);
  const user = users.find((u) => u.id === req.session.userId);
  if (lesson && user && user.id === lesson.studentUserId && lesson.status === 'pending_verification') {
    finalizeLesson(lesson, 'verified');
  }
  res.type('html').send(renderVerifyPage(req, lesson));
});

function renderVerifyPage(req, lesson) {
  const user = users.find((u) => u.id === req.session.userId);
  let body;
  if (!lesson) {
    body = `<p class="result error">This QR code / link isn't valid.</p>`;
  } else if (lesson.status === 'verified' || lesson.status === 'auto_released') {
    body = `<p class="result success">This lesson with ${escapeHtml(lesson.tutorName)} was already confirmed — K${lesson.payoutAmount} has been released to the tutor.</p>`;
  } else if (!user) {
    body = `<p class="result">Log in to Tuma as ${escapeHtml(lesson.studentName)} first, then scan this code again.</p>`;
  } else if (user.id !== lesson.studentUserId) {
    body = `<p class="result error">This lesson belongs to a different student account.</p>`;
  } else {
    body = `
      <p>Confirm you were tutored by <strong>${escapeHtml(lesson.tutorName)}</strong> just now.</p>
      <p class="meta">Confirming releases K${lesson.payoutAmount} to the tutor for this lesson. If you don't confirm, it releases automatically in a short window anyway — this only speeds that up.</p>
      <form method="POST">
        <button type="submit">Confirm I was tutored</button>
      </form>
    `;
  }
  return `<!DOCTYPE html>
<html><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Confirm lesson</title><link rel="stylesheet" href="/style.css" /></head>
<body style="max-width:480px;margin:2rem auto;padding:0 1rem;">
<h2>Lesson confirmation</h2>
${body}
</body></html>`;
}

// --- Video lessons (recorded, purchase-to-watch) ---

app.get('/api/videos', requireAuth(), (req, res) => {
  res.json(videos.map((v) => videoWithAccess(v, req.user)));
});

app.get('/api/videos/mine', requireAuth('tutor'), (req, res) => {
  const myTutor = tutors.find((t) => t.ownerId === req.user.id);
  const myVideos = myTutor ? videos.filter((v) => v.tutorId === myTutor.id) : [];
  res.json(myVideos.map((v) => ({
    ...v,
    purchaseCount: videoPurchases.filter((p) => p.videoId === v.id).length,
    totalEarned: round2(videoPurchases.filter((p) => p.videoId === v.id).reduce((sum, p) => sum + p.tutorReceives, 0)),
  })));
});

app.post('/api/videos', requireAuth('tutor'), (req, res, next) => {
  videoUpload.single('video')(req, res, (err) => {
    if (err) return res.status(400).json({ error: err.message });
    next();
  });
}, (req, res) => {
  const tutor = tutors.find((t) => t.ownerId === req.user.id);
  if (!tutor) {
    if (req.file) fs.unlink(req.file.path, () => {});
    return res.status(400).json({ error: 'Create your tutor listing first (Become a Tutor tab)' });
  }
  if (!req.file) {
    return res.status(400).json({ error: 'A video file is required' });
  }
  const { title, description } = req.body;
  const price = round2(Number(req.body.price));
  if (!title) {
    fs.unlink(req.file.path, () => {});
    return res.status(400).json({ error: 'Title is required' });
  }
  if (!Number.isFinite(price) || price < VIDEO_MIN_PRICE || price > VIDEO_MAX_PRICE) {
    fs.unlink(req.file.path, () => {});
    return res.status(400).json({ error: `Price must be between K${VIDEO_MIN_PRICE} and K${VIDEO_MAX_PRICE}` });
  }
  const video = {
    id: nextVideoId++,
    tutorId: tutor.id,
    tutorName: tutor.name,
    title,
    description: description || '',
    price,
    filename: req.file.filename,
    mimeType: req.file.mimetype,
    sizeBytes: req.file.size,
    createdAt: new Date().toISOString(),
  };
  videos.push(video);
  res.status(201).json(videoWithAccess(video, req.user));
});

// Streams the actual video bytes — only to a purchaser or the uploading tutor. res.sendFile
// uses Express's underlying `send` package, which handles Range requests automatically so
// video scrubbing/seeking works.
app.get('/api/videos/:id/stream', requireAuth(), (req, res) => {
  const video = videos.find((v) => v.id === Number(req.params.id));
  if (!video) return res.status(404).json({ error: 'Video not found' });
  const tutor = tutors.find((t) => t.id === video.tutorId);
  const isOwner = tutor && tutor.ownerId === req.user.id;
  const purchased = videoPurchases.some((p) => p.videoId === video.id && p.studentUserId === req.user.id);
  if (!isOwner && !purchased) {
    return res.status(403).json({ error: 'Purchase this video to watch it' });
  }
  res.sendFile(path.join(VIDEO_UPLOAD_DIR, video.filename));
});

app.post('/api/videos/:id/purchase', requireAuth('student'), (req, res) => {
  const video = videos.find((v) => v.id === Number(req.params.id));
  if (!video) return res.status(404).json({ error: 'Video not found' });
  if (videoPurchases.some((p) => p.videoId === video.id && p.studentUserId === req.user.id)) {
    return res.status(400).json({ error: 'You already purchased this video' });
  }
  const { paymentMethod } = req.body;
  if (!PAYMENT_METHODS.includes(paymentMethod)) {
    return res.status(400).json({ error: 'Payment method must be Airtel Money or a bank card' });
  }

  if (paymentMethod === 'airtel_money') {
    const phone = String(req.body.airtelPhone || '').trim();
    if (!/^0\d{9}$/.test(phone)) {
      return res.status(400).json({ error: 'Enter a valid Airtel Money number, e.g. 0977123456' });
    }
    if (videoPaymentRequests.some((p) => p.videoId === video.id && p.studentUserId === req.user.id && p.status === 'pending')) {
      return res.status(400).json({ error: 'A payment prompt for this video is already pending — check your phone' });
    }
    const paymentRequest = {
      id: nextVideoPaymentRequestId++,
      videoId: video.id,
      videoTitle: video.title,
      tutorId: video.tutorId,
      studentUserId: req.user.id,
      studentName: req.user.name,
      price: video.price,
      phone,
      status: 'pending',
      createdAt: new Date().toISOString(),
    };
    videoPaymentRequests.push(paymentRequest);
    return res.status(202).json({
      requiresApproval: true,
      paymentRequestId: paymentRequest.id,
      phone,
      price: video.price,
      message: `A payment prompt for K${video.price} has been sent to ${phone}. Approve it on your phone to unlock the video.`,
    });
  }

  let payment;
  try {
    payment = validateCardPayment(req.body);
  } catch (err) {
    if (err instanceof PaymentError) return res.status(400).json({ error: err.message });
    throw err;
  }
  const purchase = createVideoPurchaseRecord(video, req.user, payment);
  videoPurchases.push(purchase);
  res.status(201).json({ ...purchase, video: videoWithAccess(video, req.user) });
});

app.post('/api/video-payment-requests/:id/approve', requireAuth('student'), (req, res) => {
  const paymentRequest = videoPaymentRequests.find((p) => p.id === Number(req.params.id));
  if (!paymentRequest) return res.status(404).json({ error: 'Payment request not found' });
  if (paymentRequest.studentUserId !== req.user.id) return res.status(403).json({ error: 'Not your payment request' });
  if (paymentRequest.status !== 'pending') return res.status(400).json({ error: `Payment request already ${paymentRequest.status}` });

  const video = videos.find((v) => v.id === paymentRequest.videoId);
  if (!video) return res.status(404).json({ error: 'Video not found' });
  if (videoPurchases.some((p) => p.videoId === video.id && p.studentUserId === req.user.id)) {
    paymentRequest.status = 'declined';
    return res.status(400).json({ error: 'You already purchased this video' });
  }

  paymentRequest.status = 'approved';
  const payment = {
    method: 'airtel_money',
    reference: `AIRTEL-${crypto.randomBytes(4).toString('hex').toUpperCase()}`,
    detail: `Airtel Money ${paymentRequest.phone}`,
  };
  const purchase = createVideoPurchaseRecord(video, req.user, payment);
  videoPurchases.push(purchase);
  res.status(201).json({ ...purchase, video: videoWithAccess(video, req.user) });
});

app.post('/api/video-payment-requests/:id/decline', requireAuth('student'), (req, res) => {
  const paymentRequest = videoPaymentRequests.find((p) => p.id === Number(req.params.id));
  if (!paymentRequest) return res.status(404).json({ error: 'Payment request not found' });
  if (paymentRequest.studentUserId !== req.user.id) return res.status(403).json({ error: 'Not your payment request' });
  if (paymentRequest.status !== 'pending') return res.status(400).json({ error: `Payment request already ${paymentRequest.status}` });
  paymentRequest.status = 'declined';
  res.json({ id: paymentRequest.id, status: 'declined' });
});

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

const PORT = process.env.PORT || 4173;
app.listen(PORT, () => {
  console.log(`Tutor marketplace sandbox running at http://localhost:${PORT}`);
});
