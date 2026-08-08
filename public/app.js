const state = {
  locations: [],
  courses: {},
  pricing: null,
  tutors: [],
  currentUser: null,
  activeApplyTutor: null,
  activeReviewsTutor: null,
  activeVideoPurchase: null,
};

let tutorPhotoDataUrl = null;
const MAX_PHOTO_BYTES = 1.5 * 1024 * 1024;

// --- Tabs ---
document.querySelectorAll('#app-tabs .tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#app-tabs .tab-btn').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'dashboard') renderDashboard();
    if (btn.dataset.tab === 'register') renderRegisterGate();
    if (btn.dataset.tab === 'my-lessons') renderMyLessons();
    if (btn.dataset.tab === 'videos') renderVideos();
    if (btn.dataset.tab === 'news') renderNews();
  });
});

// --- Init ---
async function init() {
  state.currentUser = await fetchJSON('/api/auth/me');
  applyAuthUiState();
  renderAuthArea();
  if (state.currentUser) await loadAuthenticatedData();
}

function applyAuthUiState() {
  const loggedIn = !!state.currentUser;
  document.getElementById('app-tabs').classList.toggle('hidden', !loggedIn);
  document.getElementById('auth-gate').classList.toggle('hidden', loggedIn);
  document.getElementById('app-main').classList.toggle('hidden', !loggedIn);
}

async function loadAuthenticatedData() {
  state.locations = await fetchJSON('/api/locations');
  state.courses = await fetchJSON('/api/courses');
  state.pricing = await fetchJSON('/api/pricing');
  populateLocationSelects();
  populateCourseFilter();
  populateCourseCheckboxes();
  renderRegisterGate();
  await refreshTutors();
}

function populateLocationSelects() {
  const filterSel = document.getElementById('filter-location');
  const registerSel = document.getElementById('register-location');
  filterSel.innerHTML = '<option value="">All locations</option>' +
    state.locations.map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
  registerSel.innerHTML = state.locations.map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
}

function populateCourseFilter() {
  const sel = document.getElementById('filter-subject');
  const groups = Object.entries(state.courses).map(([sector, courses]) => `
    <optgroup label="${escapeHtml(sector)} sector">
      ${courses.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('')}
    </optgroup>
  `).join('');
  sel.innerHTML = '<option value="">All courses</option>' + groups;
}

function populateCourseCheckboxes() {
  const container = document.getElementById('course-checkboxes');
  container.innerHTML = Object.entries(state.courses).map(([sector, courses]) => `
    <div class="course-group">
      <h4>${escapeHtml(sector)} sector</h4>
      ${courses.map((c) => `
        <label class="course-option">
          <input type="checkbox" name="subjects" value="${escapeHtml(c)}" />
          ${escapeHtml(c)}
        </label>
      `).join('')}
    </div>
  `).join('');

  const note = document.getElementById('register-rate-note');
  if (note && state.pricing) {
    note.textContent = `Rate is set by level, not by you: K${state.pricing.levelRates[0]}/hour at Level 1, rising to K${state.pricing.levelRates[state.pricing.levelRates.length - 1]}/hour at Level ${state.pricing.maxLevel} (max).`;
  }
}

async function refreshTutors(params = {}) {
  const qs = new URLSearchParams(params).toString();
  state.tutors = await fetchJSON('/api/tutors' + (qs ? `?${qs}` : ''));
  renderTutorList();
}

function ratingBadge(t) {
  if (!t.reviewCount) return '<span class="rating-badge">No reviews yet</span>';
  return `<span class="rating-badge"><span class="stars">★</span> ${t.averageRating} (${t.reviewCount} review${t.reviewCount === 1 ? '' : 's'})</span>`;
}

function levelBadge(t) {
  return t.classTutoringUnlocked
    ? `<span class="level-badge">Level ${t.level}/${t.maxLevel} (max) — Class tutoring</span>`
    : `<span class="level-badge">Level ${t.level}/${t.maxLevel} — 1:1 tutoring</span>`;
}

function avatarHtml(t, extraClass = '') {
  if (t.photoUrl) {
    return `<img class="avatar ${extraClass}" src="${t.photoUrl}" alt="${escapeHtml(t.name || '')}" />`;
  }
  const initial = (t.name || '?').trim().charAt(0).toUpperCase() || '?';
  return `<div class="avatar ${extraClass}">${initial}</div>`;
}

function lessonStatusLabel(status) {
  if (status === 'pending_verification') return 'Awaiting confirmation';
  if (status === 'verified') return 'Verified by student';
  if (status === 'auto_released') return 'Auto-released (no response)';
  return status;
}

function renderTutorList() {
  const container = document.getElementById('tutor-list');
  if (state.tutors.length === 0) {
    container.innerHTML = '<p>No tutors match that search.</p>';
    return;
  }
  container.innerHTML = state.tutors.map((t) => `
    <div class="tutor-card">
      ${levelBadge(t)}
      <div class="tutor-card-header">
        ${avatarHtml(t)}
        <h3>${escapeHtml(t.name)}</h3>
      </div>
      ${ratingBadge(t)}
      <div class="meta">${escapeHtml(t.subjects.join(', '))}</div>
      <div class="meta">${escapeHtml(t.location)}</div>
      <div class="meta">K${t.hourlyRate} / hour</div>
      <p>${escapeHtml(t.bio || '')}</p>
      <div class="actions">
        <button class="secondary" data-apply="${t.id}">Apply</button>
        <button class="secondary" data-reviews="${t.id}">Reviews</button>
      </div>
    </div>
  `).join('');

  container.querySelectorAll('[data-apply]').forEach((btn) => {
    btn.addEventListener('click', () => openApplyModal(Number(btn.dataset.apply)));
  });
  container.querySelectorAll('[data-reviews]').forEach((btn) => {
    btn.addEventListener('click', () => openReviewsModal(Number(btn.dataset.reviews)));
  });
}

document.getElementById('filter-apply').addEventListener('click', () => {
  const subject = document.getElementById('filter-subject').value;
  const location = document.getElementById('filter-location').value;
  refreshTutors({ ...(subject ? { subject } : {}), ...(location ? { location } : {}) });
});

// --- Auth ---
document.querySelectorAll('[data-authtab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-authtab]').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.auth-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`${btn.dataset.authtab}-form`).classList.add('active');
    document.getElementById('auth-result').textContent = '';
  });
});

function renderAuthArea() {
  const area = document.getElementById('auth-area');
  if (state.currentUser) {
    const streak = state.currentUser.currentStreak || 0;
    const streakHtml = streak > 0
      ? `<span class="streak-badge">🔥 ${streak} day${streak === 1 ? '' : 's'}</span>`
      : '';
    area.innerHTML = `
      <span class="who">Hi, ${escapeHtml(state.currentUser.name)} (${escapeHtml(state.currentUser.role)})</span>
      ${streakHtml}
      <button id="logout-btn" class="secondary">Log out</button>
    `;
    document.getElementById('logout-btn').addEventListener('click', async () => {
      await fetchJSON('/api/auth/logout', { method: 'POST' });
      state.currentUser = null;
      state.tutors = [];
      applyAuthUiState();
      renderAuthArea();
    });
  } else {
    area.innerHTML = '';
  }
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('auth-result');
  try {
    state.currentUser = await fetchJSON('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    applyAuthUiState();
    renderAuthArea();
    await loadAuthenticatedData();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

document.getElementById('signup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('auth-result');
  try {
    state.currentUser = await fetchJSON('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    applyAuthUiState();
    renderAuthArea();
    await loadAuthenticatedData();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

// --- Register (Become a Tutor) gating ---
function renderRegisterGate() {
  const gate = document.getElementById('register-gate');
  const form = document.getElementById('tutor-form');
  if (!state.currentUser) {
    gate.textContent = 'Log in as a tutor to create a listing.';
    gate.className = 'result';
    form.style.display = 'none';
  } else if (state.currentUser.role !== 'tutor') {
    gate.textContent = "You're logged in as a student — sign up for a separate tutor account to list courses.";
    gate.className = 'result';
    form.style.display = 'none';
  } else {
    gate.textContent = `Listing courses as ${state.currentUser.name}.`;
    gate.className = 'result success';
    form.style.display = 'flex';
  }
}

document.getElementById('tutor-photo-input').addEventListener('change', (e) => {
  const file = e.target.files[0];
  const errorEl = document.getElementById('tutor-photo-error');
  const preview = document.getElementById('tutor-photo-preview');
  errorEl.textContent = '';
  tutorPhotoDataUrl = null;
  preview.classList.add('hidden');
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    errorEl.textContent = 'Please choose an image file.';
    e.target.value = '';
    return;
  }
  if (file.size > MAX_PHOTO_BYTES) {
    errorEl.textContent = 'Photo is too large (max 1.5MB) — choose a smaller image.';
    e.target.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    tutorPhotoDataUrl = reader.result;
    preview.src = tutorPhotoDataUrl;
    preview.classList.remove('hidden');
  };
  reader.readAsDataURL(file);
});

document.getElementById('tutor-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const formData = new FormData(form);
  const data = Object.fromEntries(formData.entries());
  data.subjects = formData.getAll('subjects');
  delete data.photo;
  if (tutorPhotoDataUrl) data.photoDataUrl = tutorPhotoDataUrl;
  const resultEl = document.getElementById('register-result');
  if (data.subjects.length === 0) {
    resultEl.textContent = 'Select at least one course.';
    resultEl.className = 'result error';
    return;
  }
  try {
    const tutor = await fetchJSON('/api/tutors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    resultEl.textContent = `Registered! You're listed as ${tutor.name} at ${tutor.location}, starting at K${tutor.hourlyRate}/hour.`;
    resultEl.className = 'result success';
    form.reset();
    tutorPhotoDataUrl = null;
    document.getElementById('tutor-photo-preview').classList.add('hidden');
    await refreshTutors();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

// --- App subscription (monthly hour pool, any tutor) ---

function renderSubscribeForm() {
  const section = document.getElementById('subscribe-section');
  section.innerHTML = `
    <div class="subscribe-box">
      <h4>Subscribe &amp; pay (simulated — no real charge)</h4>
      <form id="subscribe-form" class="form">
        <label>Hours per month <input name="hoursPerMonth" type="number" min="1" value="8" /></label>
        <div class="payment-methods">
          <label><input type="radio" name="paymentMethod" value="airtel_money" checked /> Airtel Money</label>
          <label><input type="radio" name="paymentMethod" value="bank_card" /> Bank card</label>
        </div>
        <div id="payment-fields"></div>
        <button type="submit" id="subscribe-submit-btn"></button>
      </form>
      <p id="subscribe-result" class="result"></p>
    </div>
  `;

  const hoursInput = document.querySelector('#subscribe-form input[name=hoursPerMonth]');
  const submitBtn = document.getElementById('subscribe-submit-btn');
  function updatePrice() {
    const hours = Math.max(1, Number(hoursInput.value) || 1);
    const price = round2(state.pricing.platformHourlyRate * hours * (1 - state.pricing.subscriptionDiscount));
    submitBtn.textContent = `Subscribe for K${price}/month`;
  }
  hoursInput.addEventListener('input', updatePrice);
  updatePrice();

  const fieldsEl = document.getElementById('payment-fields');
  function renderPaymentFields() {
    const method = document.querySelector('input[name=paymentMethod]:checked').value;
    fieldsEl.innerHTML = method === 'airtel_money'
      ? `<label>Airtel Money number <input name="airtelPhone" placeholder="0977123456" required /></label>`
      : `
        <label>Card number <input name="cardNumber" placeholder="4111 1111 1111 1111" required /></label>
        <label>Expiry (MM/YY) <input name="cardExpiry" placeholder="09/28" required /></label>
        <label>CVC <input name="cardCvc" placeholder="123" required /></label>
      `;
  }
  renderPaymentFields();
  document.querySelectorAll('input[name=paymentMethod]').forEach((r) => r.addEventListener('change', renderPaymentFields));

  document.getElementById('subscribe-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    const resultEl = document.getElementById('subscribe-result');
    try {
      const response = await fetchJSON('/api/subscriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (response.requiresApproval) {
        renderPendingApproval(response);
      } else {
        resultEl.textContent = `Subscribed! Charged K${response.monthlyPrice}/month via ${response.paymentDetail}. Reference: ${response.paymentReference}.`;
        resultEl.className = 'result success';
        e.target.querySelector('button[type=submit]').disabled = true;
        await renderMyLessons();
      }
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });
}

function renderPendingApproval(paymentRequest) {
  const section = document.getElementById('subscribe-section');
  section.innerHTML = `
    <div class="subscribe-box">
      <h4>Payment prompt sent</h4>
      <p>📱 A prompt to pay <strong>K${paymentRequest.monthlyPrice}</strong> was sent to <strong>${escapeHtml(paymentRequest.phone)}</strong> via Airtel Money. Enter your Airtel Money PIN on your phone to approve.</p>
      <p class="meta">Sandbox note: there's no real phone here, so use the buttons below to simulate what happens on it.</p>
      <div class="actions">
        <button id="pr-approve-btn">Simulate: Approved on phone</button>
        <button id="pr-decline-btn" class="secondary">Simulate: Declined</button>
      </div>
      <p id="pr-result" class="result"></p>
    </div>
  `;

  document.getElementById('pr-approve-btn').addEventListener('click', async () => {
    const resultEl = document.getElementById('pr-result');
    try {
      const sub = await fetchJSON(`/api/payment-requests/${paymentRequest.paymentRequestId}/approve`, { method: 'POST' });
      section.innerHTML = `<p class="result success">Subscribed! Charged K${sub.monthlyPrice}/month via ${sub.paymentDetail}. Reference: ${sub.paymentReference}.</p>`;
      await renderMyLessons();
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });

  document.getElementById('pr-decline-btn').addEventListener('click', async () => {
    const resultEl = document.getElementById('pr-result');
    try {
      await fetchJSON(`/api/payment-requests/${paymentRequest.paymentRequestId}/decline`, { method: 'POST' });
      resultEl.textContent = 'Payment declined on phone. You can try subscribing again.';
      resultEl.className = 'result error';
      document.getElementById('pr-approve-btn').disabled = true;
      document.getElementById('pr-decline-btn').disabled = true;
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });
}

// --- Apply modal ---
const applyModal = document.getElementById('apply-modal');
document.getElementById('apply-close').addEventListener('click', () => applyModal.classList.add('hidden'));

function openApplyModal(tutorId) {
  state.activeApplyTutor = tutorId;
  const tutor = state.tutors.find((t) => t.id === tutorId) || {};
  document.getElementById('apply-tutor-name').textContent = `Apply to ${tutor.name || ''}`;
  document.getElementById('apply-result').textContent = '';
  document.getElementById('apply-form').reset();

  const gate = document.getElementById('apply-gate');
  const form = document.getElementById('apply-form');
  if (!state.currentUser) {
    gate.textContent = 'Log in as a student to apply.';
    gate.className = 'result';
    form.style.display = 'none';
  } else if (state.currentUser.role !== 'student') {
    gate.textContent = "You're logged in as a tutor — applying requires a student account.";
    gate.className = 'result';
    form.style.display = 'none';
  } else {
    gate.textContent = '';
    form.style.display = 'flex';
  }
  applyModal.classList.remove('hidden');
}

document.getElementById('apply-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('apply-result');
  try {
    await fetchJSON('/api/applications', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorId: state.activeApplyTutor, ...data }),
    });
    resultEl.textContent = 'Application sent.';
    resultEl.className = 'result success';
    e.target.reset();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

// --- Reviews modal ---
const reviewsModal = document.getElementById('reviews-modal');
document.getElementById('reviews-close').addEventListener('click', () => reviewsModal.classList.add('hidden'));

async function openReviewsModal(tutorId) {
  state.activeReviewsTutor = tutorId;
  const tutor = state.tutors.find((t) => t.id === tutorId) || {};
  document.getElementById('reviews-tutor-name').textContent = `Reviews for ${tutor.name || ''}`;
  document.getElementById('review-result').textContent = '';
  document.getElementById('review-form').reset();
  await renderReviews();
  reviewsModal.classList.remove('hidden');
}

async function renderReviews() {
  const tutorId = state.activeReviewsTutor;
  const reviews = await fetchJSON(`/api/tutors/${tutorId}/reviews`);
  const listEl = document.getElementById('reviews-list');
  listEl.innerHTML = reviews.length === 0
    ? '<p class="meta">No reviews yet.</p>'
    : reviews.map((r) => `
      <div class="review-item">
        <strong>${'★'.repeat(r.rating)}${'☆'.repeat(5 - r.rating)}</strong> — ${escapeHtml(r.studentName)}
        <div class="meta">${new Date(r.createdAt).toLocaleString()}</div>
        ${r.comment ? `<div>${escapeHtml(r.comment)}</div>` : ''}
      </div>
    `).join('');

  const gate = document.getElementById('reviews-gate');
  const form = document.getElementById('review-form');
  const alreadyReviewed = state.currentUser && reviews.some((r) => r.studentUserId === state.currentUser.id);

  if (!state.currentUser) {
    gate.textContent = 'Log in as a student to leave a review.';
    gate.className = 'result';
    form.style.display = 'none';
  } else if (state.currentUser.role !== 'student') {
    gate.textContent = "You're logged in as a tutor — reviewing requires a student account.";
    gate.className = 'result';
    form.style.display = 'none';
  } else if (alreadyReviewed) {
    gate.textContent = "You've already reviewed this tutor.";
    gate.className = 'result';
    form.style.display = 'none';
  } else {
    gate.textContent = '';
    form.style.display = 'flex';
  }
}

document.getElementById('review-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('review-result');
  try {
    await fetchJSON(`/api/tutors/${state.activeReviewsTutor}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    resultEl.textContent = 'Review submitted.';
    resultEl.className = 'result success';
    e.target.reset();
    await renderReviews();
    await refreshTutors();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

// --- Dashboard ---
async function renderDashboard() {
  const gate = document.getElementById('dashboard-gate');
  const body = document.getElementById('dashboard-body');

  if (!state.currentUser || state.currentUser.role !== 'tutor') {
    gate.textContent = 'Log in as a tutor to see your listings, applications, subscribers, and level.';
    gate.className = 'result';
    body.style.display = 'none';
    return;
  }
  gate.textContent = '';
  body.style.display = 'block';

  const myTutors = await fetchJSON('/api/tutors/mine');
  const sel = document.getElementById('dashboard-tutor-select');

  if (myTutors.length === 0) {
    sel.innerHTML = '';
    document.getElementById('dashboard-content').innerHTML = '<p class="meta">You haven\'t created a tutor listing yet — use the "Become a Tutor" tab.</p>';
    return;
  }

  sel.innerHTML = myTutors.map((t) => `<option value="${t.id}">${escapeHtml(t.subjects.join(', '))}</option>`).join('');
  sel.onchange = () => renderDashboardTutor(Number(sel.value));
  renderDashboardTutor(Number(sel.value));
}

async function renderDashboardTutor(tutorId) {
  if (!tutorId) return;
  const tutor = await fetchJSON(`/api/tutors/${tutorId}`);
  const apps = await fetchJSON(`/api/tutors/${tutorId}/applications`);
  const tutorLessons = await fetchJSON(`/api/tutors/${tutorId}/lessons`);

  document.getElementById('dashboard-content').innerHTML = `
    <div class="tutor-card" style="max-width:400px;">
      <div class="tutor-card-header">${avatarHtml(tutor)}<h3>${escapeHtml(tutor.name)}</h3></div>
      ${levelBadge(tutor)}
      ${ratingBadge(tutor)}
      <div class="meta">${escapeHtml(tutor.subjects.join(', '))} · ${escapeHtml(tutor.location)}</div>
      <div class="meta">K${tutor.hourlyRate} / hour</div>
      ${!tutor.classTutoringUnlocked ? '<button id="level-up-btn">Level up (demo)</button>' : '<p class="meta">Max level reached — can teach whole classes.</p>'}
    </div>
    <h3 style="margin-top:1.5rem;">Applications received (${apps.length})</h3>
    <p class="meta">When you're physically with one of these students, tap "Start lesson" — it draws 1 hour from their app subscription (they need an active one with hours left).</p>
    ${apps.length === 0 ? '<p class="meta">No applications yet.</p>' : apps.map((a) => `
      <div class="application-item">
        <strong>${escapeHtml(a.studentName)}</strong> (${escapeHtml(a.studentEmail)})
        <div class="meta">${new Date(a.createdAt).toLocaleString()} · status: ${a.status}</div>
        ${a.message ? `<div>${escapeHtml(a.message)}</div>` : ''}
        <button class="secondary" data-start-lesson="${a.id}" style="margin-top:0.4rem;">Start lesson</button>
      </div>
    `).join('')}
    <h3 style="margin-top:1.5rem;">Lessons (${tutorLessons.length})</h3>
    ${tutorLessons.length === 0 ? '<p class="meta">No lessons started yet — tap "Start lesson" when you\'re with a student.</p>' : tutorLessons.slice().reverse().map((l) => `
      <div class="lesson-item">
        <strong>${escapeHtml(l.studentName)}</strong>
        <span class="status-pill ${l.status === 'pending_verification' ? 'pending' : l.status}">${lessonStatusLabel(l.status)}</span>
        <div class="meta">Started ${new Date(l.startedAt).toLocaleString()} · payout K${l.payoutAmount}</div>
      </div>
    `).join('')}
  `;

  const levelUpBtn = document.getElementById('level-up-btn');
  if (levelUpBtn) {
    levelUpBtn.addEventListener('click', async () => {
      await fetchJSON(`/api/tutors/${tutorId}/level-up`, { method: 'POST' });
      await refreshTutors();
      renderDashboardTutor(tutorId);
    });
  }

  document.querySelectorAll('[data-start-lesson]').forEach((btn) => {
    btn.addEventListener('click', () => startLesson(Number(btn.dataset.startLesson), tutorId));
  });
}

// --- Lesson verification (QR / code) ---
const lessonModal = document.getElementById('lesson-modal');
document.getElementById('lesson-close').addEventListener('click', () => {
  lessonModal.classList.add('hidden');
  renderDashboard();
});

async function startLesson(applicationId, tutorId) {
  document.getElementById('lesson-tutor-name').textContent = 'Starting lesson…';
  document.getElementById('lesson-content').innerHTML = '<p class="meta">Generating QR code…</p>';
  lessonModal.classList.remove('hidden');
  try {
    const lesson = await fetchJSON(`/api/applications/${applicationId}/lessons`, { method: 'POST' });
    renderLessonQr(lesson, tutorId);
  } catch (err) {
    document.getElementById('lesson-tutor-name').textContent = 'Could not start lesson';
    document.getElementById('lesson-content').innerHTML = `<p class="result error">${escapeHtml(err.message)}</p>`;
  }
}

function renderLessonQr(lesson, tutorId) {
  document.getElementById('lesson-tutor-name').textContent = `Lesson with ${lesson.studentName}`;
  document.getElementById('lesson-content').innerHTML = `
    <p>Have ${escapeHtml(lesson.studentName)} scan this with their phone camera, or read them the code to type into "My Lessons".</p>
    <div class="qr-box">
      <img src="${lesson.qrDataUrl}" alt="QR code" />
      <div class="code">${lesson.code}</div>
    </div>
    <p class="meta">Payout for this lesson: K${lesson.payoutAmount}. If ${escapeHtml(lesson.studentName)} doesn't confirm, it auto-releases to you in ${state.pricing.lessonAutoReleaseSeconds}s anyway — a non-responsive student can't cost you the payment.</p>
    <p id="lesson-status" class="result"><span class="status-pill pending">${lessonStatusLabel(lesson.status)}</span></p>
    <button id="lesson-refresh-btn" class="secondary">Refresh status</button>
  `;
  document.getElementById('lesson-refresh-btn').addEventListener('click', async () => {
    const updated = await fetchJSON(`/api/lessons/${lesson.id}`);
    if (updated.status !== 'pending_verification') {
      document.getElementById('lesson-content').innerHTML = `<p class="result success">${lessonStatusLabel(updated.status)} — K${updated.payoutAmount} is yours.</p>`;
    } else {
      document.getElementById('lesson-status').innerHTML = `<span class="status-pill pending">${lessonStatusLabel(updated.status)}</span>`;
    }
  });
}

// --- My Lessons (student) ---
document.getElementById('verify-code-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('verify-code-result');
  try {
    const lesson = await fetchJSON('/api/lessons/verify-by-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    resultEl.textContent = `Confirmed! K${lesson.payoutAmount} released to ${lesson.tutorName}.`;
    resultEl.className = 'result success';
    e.target.reset();
    await renderMyLessons();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

async function renderMyLessons() {
  const gate = document.getElementById('my-lessons-gate');
  const body = document.getElementById('my-lessons-body');

  if (!state.currentUser || state.currentUser.role !== 'student') {
    gate.textContent = 'Log in as a student to see your subscription and lessons.';
    gate.className = 'result';
    body.style.display = 'none';
    return;
  }
  gate.textContent = '';
  body.style.display = 'block';

  const subs = await fetchJSON('/api/subscriptions/mine');
  const activeSub = subs.find((s) => s.status === 'active');
  const statusEl = document.getElementById('subscription-status');

  if (activeSub) {
    statusEl.innerHTML = `
      <div class="subscription-item">
        <strong>Active</strong> — ${activeSub.hoursRemaining}/${activeSub.hoursPerMonth} hours left this month, K${activeSub.monthlyPrice}/month
        <div class="meta">Paid via ${escapeHtml(activeSub.paymentDetail)} · started ${new Date(activeSub.createdAt).toLocaleString()}</div>
      </div>
    `;
    document.getElementById('subscribe-section').innerHTML = '';
  } else {
    statusEl.innerHTML = '<p class="meta">No active subscription — subscribe below to book lessons with any tutor.</p>';
    renderSubscribeForm();
  }

  const myLessons = await fetchJSON('/api/lessons/mine');
  document.getElementById('my-lessons-list').innerHTML = myLessons.length === 0
    ? '<p class="meta">No lessons logged yet.</p>'
    : myLessons.slice().reverse().map((l) => `
      <div class="lesson-item">
        <strong>${escapeHtml(l.tutorName)}</strong>
        <span class="status-pill ${l.status === 'pending_verification' ? 'pending' : l.status}">${lessonStatusLabel(l.status)}</span>
        <div class="meta">Started ${new Date(l.startedAt).toLocaleString()}</div>
      </div>
    `).join('');
}

// --- Video Lessons ---

async function renderVideos() {
  renderVideoUploadSection();
  const items = await fetchJSON('/api/videos');
  const listEl = document.getElementById('video-list');
  listEl.innerHTML = items.length === 0
    ? '<p class="meta">No video lessons posted yet.</p>'
    : items.map((v) => videoCardHtml(v)).join('');

  listEl.querySelectorAll('[data-buy-video]').forEach((btn) => {
    const video = items.find((v) => v.id === Number(btn.dataset.buyVideo));
    btn.addEventListener('click', () => openVideoPurchaseModal(video));
  });
  listEl.querySelectorAll('[data-watch-video]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.watchVideo;
      const target = document.getElementById(`video-player-${id}`);
      if (target.innerHTML) {
        target.innerHTML = '';
        return;
      }
      target.innerHTML = `<video class="video-player" controls src="/api/videos/${id}/stream"></video>`;
    });
  });
}

function videoCardHtml(v) {
  let actionHtml;
  if (v.purchased) {
    actionHtml = `<button data-watch-video="${v.id}">▶ Watch</button>`;
  } else if (state.currentUser.role === 'student') {
    actionHtml = `<button data-buy-video="${v.id}">Buy &amp; watch — K${v.price}</button>`;
  } else {
    actionHtml = `<span class="meta">K${v.price} (students only)</span>`;
  }
  return `
    <div class="tutor-card">
      <h3>${escapeHtml(v.title)}</h3>
      <div class="meta">by ${escapeHtml(v.tutorName)}</div>
      <p>${escapeHtml(v.description || '')}</p>
      <div class="actions">${actionHtml}</div>
      <div id="video-player-${v.id}"></div>
    </div>
  `;
}

function renderVideoUploadSection() {
  const section = document.getElementById('video-upload-section');
  if (!state.currentUser || state.currentUser.role !== 'tutor') {
    section.innerHTML = '';
    return;
  }
  section.innerHTML = `
    <div class="subscribe-box" style="margin-top:0;">
      <h4>Post a video lesson</h4>
      <form id="video-upload-form" class="form">
        <label>Title <input name="title" required /></label>
        <label>Description <textarea name="description" rows="2"></textarea></label>
        <label>Price (K${state.pricing.videoMinPrice}–K${state.pricing.videoMaxPrice}) <input name="price" type="number" min="${state.pricing.videoMinPrice}" max="${state.pricing.videoMaxPrice}" value="${state.pricing.videoMinPrice}" required /></label>
        <label>Video file <input name="video" type="file" accept="video/*" required /></label>
        <button type="submit">Upload</button>
      </form>
      <p id="video-upload-result" class="result"></p>
    </div>
    <h4 style="margin-top:1.5rem;">My uploaded videos</h4>
    <div id="my-videos-list"></div>
  `;

  document.getElementById('video-upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const resultEl = document.getElementById('video-upload-result');
    try {
      await fetchJSON('/api/videos', { method: 'POST', body: formData });
      resultEl.textContent = 'Uploaded!';
      resultEl.className = 'result success';
      e.target.reset();
      await renderVideos();
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });

  renderMyVideos();
}

async function renderMyVideos() {
  const myVideos = await fetchJSON('/api/videos/mine');
  const el = document.getElementById('my-videos-list');
  if (!el) return;
  el.innerHTML = myVideos.length === 0
    ? '<p class="meta">You haven\'t posted any videos yet.</p>'
    : myVideos.map((v) => `
      <div class="lesson-item">
        <strong>${escapeHtml(v.title)}</strong> — K${v.price}
        <div class="meta">${v.purchaseCount} purchase${v.purchaseCount === 1 ? '' : 's'} · K${v.totalEarned} earned</div>
      </div>
    `).join('');
}

const videoPurchaseModal = document.getElementById('video-purchase-modal');
document.getElementById('video-purchase-close').addEventListener('click', () => {
  videoPurchaseModal.classList.add('hidden');
  renderVideos();
});

function openVideoPurchaseModal(video) {
  state.activeVideoPurchase = video;
  document.getElementById('video-purchase-title').textContent = `Buy "${video.title}"`;
  document.getElementById('video-purchase-content').innerHTML = `
    <form id="video-purchase-form" class="form">
      <div class="payment-methods">
        <label><input type="radio" name="paymentMethod" value="airtel_money" checked /> Airtel Money</label>
        <label><input type="radio" name="paymentMethod" value="bank_card" /> Bank card</label>
      </div>
      <div id="video-payment-fields"></div>
      <button type="submit">Pay K${video.price}</button>
    </form>
    <p id="video-purchase-result" class="result"></p>
  `;

  const fieldsEl = document.getElementById('video-payment-fields');
  function renderPaymentFields() {
    const method = document.querySelector('#video-purchase-form input[name=paymentMethod]:checked').value;
    fieldsEl.innerHTML = method === 'airtel_money'
      ? `<label>Airtel Money number <input name="airtelPhone" placeholder="0977123456" required /></label>`
      : `
        <label>Card number <input name="cardNumber" placeholder="4111 1111 1111 1111" required /></label>
        <label>Expiry (MM/YY) <input name="cardExpiry" placeholder="09/28" required /></label>
        <label>CVC <input name="cardCvc" placeholder="123" required /></label>
      `;
  }
  renderPaymentFields();
  document.querySelectorAll('#video-purchase-form input[name=paymentMethod]').forEach((r) => r.addEventListener('change', renderPaymentFields));

  document.getElementById('video-purchase-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    const resultEl = document.getElementById('video-purchase-result');
    try {
      const response = await fetchJSON(`/api/videos/${video.id}/purchase`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (response.requiresApproval) {
        renderVideoPendingApproval(response);
      } else {
        resultEl.textContent = `Purchased! Charged K${response.price} via ${response.paymentDetail}. Reference: ${response.paymentReference}.`;
        resultEl.className = 'result success';
        e.target.querySelector('button[type=submit]').disabled = true;
      }
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });

  videoPurchaseModal.classList.remove('hidden');
}

function renderVideoPendingApproval(paymentRequest) {
  const content = document.getElementById('video-purchase-content');
  content.innerHTML = `
    <p>📱 A prompt to pay <strong>K${paymentRequest.price}</strong> was sent to <strong>${escapeHtml(paymentRequest.phone)}</strong> via Airtel Money.</p>
    <p class="meta">Sandbox note: there's no real phone here, so use the buttons below to simulate what happens on it.</p>
    <div class="actions">
      <button id="video-pr-approve-btn">Simulate: Approved on phone</button>
      <button id="video-pr-decline-btn" class="secondary">Simulate: Declined</button>
    </div>
    <p id="video-pr-result" class="result"></p>
  `;

  document.getElementById('video-pr-approve-btn').addEventListener('click', async () => {
    const resultEl = document.getElementById('video-pr-result');
    try {
      const purchase = await fetchJSON(`/api/video-payment-requests/${paymentRequest.paymentRequestId}/approve`, { method: 'POST' });
      content.innerHTML = `<p class="result success">Purchased! Charged K${purchase.price} via ${purchase.paymentDetail}. Reference: ${purchase.paymentReference}.</p>`;
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });

  document.getElementById('video-pr-decline-btn').addEventListener('click', async () => {
    const resultEl = document.getElementById('video-pr-result');
    try {
      await fetchJSON(`/api/video-payment-requests/${paymentRequest.paymentRequestId}/decline`, { method: 'POST' });
      resultEl.textContent = 'Payment declined on phone. You can try purchasing again.';
      resultEl.className = 'result error';
      document.getElementById('video-pr-approve-btn').disabled = true;
      document.getElementById('video-pr-decline-btn').disabled = true;
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'result error';
    }
  });
}

// --- School News ---
document.getElementById('news-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const resultEl = document.getElementById('news-result');
  try {
    await fetchJSON('/api/news', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    resultEl.textContent = 'Added.';
    resultEl.className = 'result success';
    e.target.reset();
    await renderNews();
  } catch (err) {
    resultEl.textContent = err.message;
    resultEl.className = 'result error';
  }
});

async function renderNews() {
  const items = await fetchJSON('/api/news');
  document.getElementById('news-list').innerHTML = items.length === 0
    ? '<p class="meta">No news items yet.</p>'
    : items.map((n) => `
      <div class="news-item">
        <h4>${escapeHtml(n.title)}<span class="source-tag">${escapeHtml(n.source)}</span></h4>
        <div class="meta">${n.publishedDate} · added by ${escapeHtml(n.addedBy)}</div>
        <p>${escapeHtml(n.summary)}</p>
        ${n.url ? `<a href="${escapeHtml(n.url)}" target="_blank" rel="noopener">Read more</a>` : ''}
        <div><button class="secondary" data-delete-news="${n.id}" style="margin-top:0.4rem;">Remove</button></div>
      </div>
    `).join('');

  document.querySelectorAll('[data-delete-news]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await fetchJSON(`/api/news/${btn.dataset.deleteNews}`, { method: 'DELETE' });
      await renderNews();
    });
  });
}

// --- Utils ---
async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  return res.json();
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

init();
