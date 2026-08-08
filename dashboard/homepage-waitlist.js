const FIREBASE_SDK_VERSION = '12.16.0';
const sdk = moduleName => `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}/${moduleName}.js`;

function installWaitlistSurface() {
  if (!document.querySelector('link[data-traid-waitlist-style]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = new URL('./homepage-waitlist.css', import.meta.url).href;
    link.dataset.traidWaitlistStyle = 'true';
    document.head.appendChild(link);
  }

  const nav = document.querySelector('.home-nav');
  const headerCta = document.querySelector('.home-header-cta');
  if (nav && headerCta && !nav.querySelector('[data-home-chart-link]')) {
    const chartLink = document.createElement('a');
    chartLink.href = '/chart';
    chartLink.textContent = 'Chart';
    chartLink.dataset.homeChartLink = 'true';
    nav.insertBefore(chartLink, headerCta);
  }

  const targets = [
    document.querySelector('.home-header-cta'),
    document.querySelector('.home-hero .home-button.primary'),
    ...document.querySelectorAll('.price-card .home-button'),
    document.querySelector('.home-final .home-button.primary'),
  ].filter(Boolean);

  targets.forEach((control, index) => {
    control.dataset.openWaitlist = 'true';
    control.href = '#waitlist';
    if (index === 0) control.innerHTML = '<span data-waitlist-label>Join waitlist</span>';
    else control.innerHTML = '<span data-waitlist-label>Join the waitlist</span>';
  });

  if (!document.getElementById('waitlist')) {
    const section = document.createElement('section');
    section.className = 'home-waitlist';
    section.id = 'waitlist';
    section.innerHTML = `
      <div class="home-wrap home-waitlist-grid">
        <div>
          <p class="home-kicker">Early access</p>
          <h2 class="home-section-title">Get a place before paid access opens.</h2>
          <p class="home-copy">The waitlist uses a verified phone number to keep duplicate and bot signups out. After verification, tell us what you trade and which access plan you would actually use.</p>
          <div class="home-waitlist-actions">
            <button class="home-button primary" type="button" data-open-waitlist><span data-waitlist-label>Join the waitlist</span></button>
            <a class="home-button secondary" href="/chart">Explore the chart</a>
          </div>
        </div>
        <div class="home-waitlist-steps">
          <div class="home-waitlist-step"><span>01</span><div><strong>Verify one phone number</strong><p>One SMS code keeps the list cleaner and prevents repeat signups. Verification does not opt you into marketing texts.</p></div></div>
          <div class="home-waitlist-step"><span>02</span><div><strong>Tell us what you trade</strong><p>Main market, experience level and weekly/monthly preference help shape the first access cohort.</p></div></div>
          <div class="home-waitlist-step"><span>03</span><div><strong>Choose how to hear from us</strong><p>Opt into email, SMS, or both for the early-access notice. You must choose at least one.</p></div></div>
        </div>
      </div>
    `;
    document.querySelector('.home-final')?.before(section);
  }

  if (!document.getElementById('waitlistLayer')) {
    document.body.insertAdjacentHTML('beforeend', `
      <section class="waitlist-layer" id="waitlistLayer" aria-hidden="true">
        <div class="waitlist-backdrop" id="waitlistBackdrop"></div>
        <div class="waitlist-dialog" role="dialog" aria-modal="true" aria-labelledby="waitlistDialogTitle">
          <div class="waitlist-dialog-header">
            <div class="waitlist-dialog-title"><span>Early access</span><strong id="waitlistDialogTitle">Traid waitlist</strong></div>
            <button class="waitlist-close" id="waitlistClose" type="button" aria-label="Close waitlist">×</button>
          </div>
          <div class="waitlist-progress" aria-hidden="true">
            <span data-waitlist-indicator="1" class="active">01 · VERIFY</span>
            <span data-waitlist-indicator="2">02 · PROFILE</span>
            <span data-waitlist-indicator="3">03 · DONE</span>
          </div>
          <div class="waitlist-body">
            <section class="waitlist-step-panel active" data-waitlist-step="phone">
              <p class="home-kicker">Step 1 of 2</p>
              <h2>Reserve your spot.</h2>
              <p>Enter your phone number. Traid sends a one-time verification code so one person cannot fill the list with duplicate signups.</p>
              <label class="waitlist-field"><span>Phone number</span><input id="waitlistPhone" type="tel" inputmode="tel" autocomplete="tel" placeholder="(713) 555-0123" /></label>
              <div id="waitlistRecaptcha"></div>
              <div class="waitlist-actions"><button class="home-button primary" id="waitlistSendCode" type="button">Send verification code</button></div>
              <p class="waitlist-fineprint">The verification text is transactional. Marketing texts are only sent if you explicitly opt in on the next step.</p>
            </section>

            <section class="waitlist-step-panel" data-waitlist-step="code">
              <p class="home-kicker">Phone verification</p>
              <h2>Enter the code.</h2>
              <p id="waitlistPhoneDisplay">We sent a six-digit code to your phone.</p>
              <label class="waitlist-field"><span>Verification code</span><input id="waitlistCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" /></label>
              <div class="waitlist-actions">
                <button class="home-button secondary" id="waitlistResendCode" type="button">Use another number</button>
                <button class="home-button primary" id="waitlistVerifyCode" type="button">Verify</button>
              </div>
            </section>

            <section class="waitlist-step-panel" data-waitlist-step="profile">
              <p class="home-kicker">Step 2 of 2</p>
              <h2>What would you use Traid for?</h2>
              <p>This is the useful part of the list: enough information to prioritize early access without turning signup into an application.</p>
              <form id="waitlistProfileForm">
                <div class="waitlist-form-grid">
                  <label class="waitlist-field"><span>First name</span><input id="waitlistFirstName" name="first_name" autocomplete="given-name" required /></label>
                  <label class="waitlist-field"><span>Last name <small>optional</small></span><input name="last_name" autocomplete="family-name" /></label>
                  <label class="waitlist-field full"><span>Email</span><input name="email" type="email" autocomplete="email" required placeholder="you@example.com" /></label>
                  <label class="waitlist-field full"><span>Instagram / X handle <small>optional</small></span><input name="social_handle" autocomplete="off" placeholder="@username" /></label>
                  <label class="waitlist-field"><span>Main market</span><select name="primary_market" required><option value="">Choose one</option><option value="XAUUSD">XAUUSD · Gold</option><option value="XAGUSD">XAGUSD · Silver</option><option value="EURUSD">EURUSD</option><option value="USDJPY">USDJPY</option><option value="NAS100">NAS100</option><option value="SPX500">SPX500</option><option value="other">Other</option></select></label>
                  <label class="waitlist-field"><span>Trading experience</span><select name="experience" required><option value="">Choose one</option><option value="new">New / learning</option><option value="under-1-year">Under 1 year</option><option value="1-3-years">1–3 years</option><option value="3-plus-years">3+ years</option></select></label>
                  <label class="waitlist-field full"><span>Which access would you prefer?</span><select name="plan_interest" required><option value="">Choose one</option><option value="weekly">$19.99 / week</option><option value="monthly">$59.99 / month</option><option value="either">Either</option></select></label>
                </div>
                <div class="waitlist-consent">
                  <label class="waitlist-check"><input type="checkbox" name="email_opt_in" /><span>Email me when Traid early access opens.</span></label>
                  <label class="waitlist-check"><input type="checkbox" name="sms_opt_in" /><span>Text me when Traid early access opens. Message/data rates may apply.</span></label>
                </div>
                <div class="waitlist-actions"><button class="home-button primary" id="waitlistSubmit" type="submit">Join waitlist</button></div>
              </form>
            </section>

            <section class="waitlist-step-panel" data-waitlist-step="success">
              <div class="waitlist-success-mark">✓</div>
              <p class="home-kicker">Saved</p>
              <h2 id="waitlistSuccessName">You're on the Traid waitlist.</h2>
              <p>Your verified signup is saved. When early access opens, we will use only the contact methods you selected.</p>
              <div class="waitlist-success-detail">
                <div><span>Status</span><strong>WAITING FOR EARLY ACCESS</strong></div>
                <div><span>Plan interest</span><strong id="waitlistSuccessPlan">—</strong></div>
              </div>
              <div class="waitlist-actions"><a class="home-button secondary" href="/chart">Explore the chart</a><button class="home-button primary" type="button" id="waitlistDone">Done</button></div>
            </section>

            <div class="waitlist-message" id="waitlistMessage" aria-live="polite"></div>
          </div>
        </div>
      </section>
    `);
  }
}

installWaitlistSurface();

const layer = document.getElementById('waitlistLayer');
const openers = [...document.querySelectorAll('[data-open-waitlist]')];
const closeButton = document.getElementById('waitlistClose');
const backdrop = document.getElementById('waitlistBackdrop');
const doneButton = document.getElementById('waitlistDone');
const messageNode = document.getElementById('waitlistMessage');
const phoneInput = document.getElementById('waitlistPhone');
const codeInput = document.getElementById('waitlistCode');
const phoneDisplay = document.getElementById('waitlistPhoneDisplay');
const sendCodeButton = document.getElementById('waitlistSendCode');
const verifyCodeButton = document.getElementById('waitlistVerifyCode');
const resendButton = document.getElementById('waitlistResendCode');
const profileForm = document.getElementById('waitlistProfileForm');
const successName = document.getElementById('waitlistSuccessName');
const successPlan = document.getElementById('waitlistSuccessPlan');

let firebaseReady = false;
let auth;
let db;
let authModule;
let firestoreModule;
let phoneConfirmation = null;
let recaptchaVerifier = null;
let currentWaitlistRecord = null;

function setMessage(message = '', type = '') {
  if (!messageNode) return;
  messageNode.textContent = message;
  messageNode.className = `waitlist-message ${type}`.trim();
}

function showStep(step) {
  document.querySelectorAll('[data-waitlist-step]').forEach(node => {
    node.classList.toggle('active', node.dataset.waitlistStep === step);
  });
  const order = { phone: 1, code: 1, profile: 2, success: 3 };
  const activeIndex = order[step] || 1;
  document.querySelectorAll('[data-waitlist-indicator]').forEach(node => {
    const index = Number(node.dataset.waitlistIndicator);
    node.classList.toggle('active', index <= activeIndex);
  });
  setMessage('');
  requestAnimationFrame(() => {
    const focusTarget = step === 'phone'
      ? phoneInput
      : step === 'code'
        ? codeInput
        : step === 'profile'
          ? document.getElementById('waitlistFirstName')
          : null;
    focusTarget?.focus({ preventScroll: true });
  });
}

function openWaitlist(event) {
  event?.preventDefault?.();
  if (!layer) return;
  layer.classList.add('open');
  layer.setAttribute('aria-hidden', 'false');
  document.body.classList.add('waitlist-open');
  if (!firebaseReady) {
    showStep('phone');
    setMessage('Connecting secure signup…');
    return;
  }
  routeAuthenticatedUser();
}

function closeWaitlist() {
  if (!layer) return;
  layer.classList.remove('open');
  layer.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('waitlist-open');
}

openers.forEach(button => button.addEventListener('click', openWaitlist));
closeButton?.addEventListener('click', closeWaitlist);
doneButton?.addEventListener('click', closeWaitlist);
backdrop?.addEventListener('click', closeWaitlist);
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && layer?.classList.contains('open')) closeWaitlist();
});

function normalizePhone(value) {
  const trimmed = String(value || '').trim();
  const compact = trimmed.replace(/[\s().-]/g, '');
  if (/^\+[1-9]\d{7,14}$/.test(compact)) return compact;
  const digits = compact.replace(/\D/g, '');
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith('1')) return `+${digits}`;
  return null;
}

async function loadFirebaseConfig() {
  try {
    const response = await fetch('/__/firebase/init.json', { cache: 'no-store' });
    if (response.ok) {
      const config = await response.json();
      if (config?.apiKey && config?.projectId) return config;
    }
  } catch (_) {
    // Local static servers do not expose Firebase Hosting's reserved config endpoint.
  }

  try {
    const local = await import('./firebase-config.local.js');
    if (local.firebaseConfig?.apiKey && !local.firebaseConfig.apiKey.startsWith('REPLACE_')) return local.firebaseConfig;
  } catch (_) {
    // Optional local config is intentionally absent from source control.
  }

  throw new Error('Firebase configuration was not found. Use Firebase Hosting/emulators or add dashboard/firebase-config.local.js for local testing.');
}

function trackingValues() {
  const query = new URLSearchParams(window.location.search);
  const safe = value => String(value || '').slice(0, 300) || null;
  return {
    source: 'homepage',
    utm_source: safe(query.get('utm_source')),
    utm_medium: safe(query.get('utm_medium')),
    utm_campaign: safe(query.get('utm_campaign')),
    utm_content: safe(query.get('utm_content')),
    referrer: safe(document.referrer),
  };
}

function planLabel(value) {
  if (value === 'weekly') return '$19.99 weekly access';
  if (value === 'monthly') return '$59.99 monthly access';
  return 'Either access option';
}

function markJoined(record = {}) {
  currentWaitlistRecord = record;
  openers.forEach(button => {
    button.dataset.waitlistJoined = 'true';
    const label = button.querySelector('[data-waitlist-label]');
    if (label) label.textContent = 'On the waitlist';
    else if (button.childNodes.length === 1) button.textContent = 'On the waitlist';
  });
  if (successName) successName.textContent = record.first_name ? `You're in, ${record.first_name}.` : "You're on the Traid waitlist.";
  if (successPlan) successPlan.textContent = planLabel(record.plan_interest);
}

async function loadExistingRecord(user) {
  if (!user || !firestoreModule || !db) return null;
  const ref = firestoreModule.doc(db, 'waitlist', user.uid);
  const snapshot = await firestoreModule.getDoc(ref);
  return snapshot.exists() ? snapshot.data() : null;
}

async function routeAuthenticatedUser() {
  if (!firebaseReady || !auth) return;
  const user = auth.currentUser;
  if (!user?.phoneNumber) {
    showStep('phone');
    return;
  }
  try {
    const existing = await loadExistingRecord(user);
    if (existing) {
      markJoined(existing);
      showStep('success');
      return;
    }
    showStep('profile');
  } catch (error) {
    showStep('profile');
    setMessage(error.message || 'Could not check your waitlist status.', 'error');
  }
}

async function resetRecaptcha() {
  recaptchaVerifier?.clear();
  recaptchaVerifier = new authModule.RecaptchaVerifier(auth, 'waitlistRecaptcha', { size: 'normal' });
  await recaptchaVerifier.render();
}

async function sendCode() {
  if (!firebaseReady) return setMessage('Signup is still connecting. Try again in a moment.', 'error');
  const normalized = normalizePhone(phoneInput?.value);
  if (!normalized) return setMessage('Enter a valid phone number. U.S. numbers can be entered normally; international numbers should include +country code.', 'error');
  sendCodeButton.disabled = true;
  resendButton.disabled = true;
  try {
    setMessage('Complete the anti-abuse check. We will send one verification code.');
    await resetRecaptcha();
    phoneConfirmation = await authModule.signInWithPhoneNumber(auth, normalized, recaptchaVerifier);
    if (phoneDisplay) phoneDisplay.textContent = `We sent a six-digit code to ${normalized}.`;
    showStep('code');
    setMessage('Verification code sent.', 'success');
  } catch (error) {
    recaptchaVerifier?.clear();
    recaptchaVerifier = null;
    setMessage(error.message || 'Could not send the verification code.', 'error');
  } finally {
    sendCodeButton.disabled = false;
    resendButton.disabled = false;
  }
}

async function verifyCode() {
  const code = String(codeInput?.value || '').trim();
  if (!phoneConfirmation) return setMessage('Send a verification code first.', 'error');
  if (!/^\d{6}$/.test(code)) return setMessage('Enter the 6-digit verification code.', 'error');
  verifyCodeButton.disabled = true;
  try {
    setMessage('Verifying…');
    const result = await phoneConfirmation.confirm(code);
    phoneConfirmation = null;
    const existing = await loadExistingRecord(result.user);
    if (existing) {
      markJoined(existing);
      showStep('success');
    } else {
      showStep('profile');
    }
  } catch (error) {
    setMessage(error.message || 'That code could not be verified.', 'error');
  } finally {
    verifyCodeButton.disabled = false;
  }
}

sendCodeButton?.addEventListener('click', sendCode);
resendButton?.addEventListener('click', () => {
  showStep('phone');
  setMessage('Enter the phone number again to send a new code.');
});
verifyCodeButton?.addEventListener('click', verifyCode);
phoneInput?.addEventListener('keydown', event => { if (event.key === 'Enter') sendCode(); });
codeInput?.addEventListener('keydown', event => { if (event.key === 'Enter') verifyCode(); });

profileForm?.addEventListener('submit', async event => {
  event.preventDefault();
  if (!firebaseReady || !auth?.currentUser?.phoneNumber) return setMessage('Verify your phone number first.', 'error');

  const data = new FormData(profileForm);
  const firstName = String(data.get('first_name') || '').trim();
  const lastName = String(data.get('last_name') || '').trim();
  const email = String(data.get('email') || '').trim().toLowerCase();
  const handle = String(data.get('social_handle') || '').trim();
  const primaryMarket = String(data.get('primary_market') || '').trim();
  const experience = String(data.get('experience') || '').trim();
  const planInterest = String(data.get('plan_interest') || '').trim();
  const smsOptIn = data.get('sms_opt_in') === 'on';
  const emailOptIn = data.get('email_opt_in') === 'on';

  if (!firstName) return setMessage('Enter your first name.', 'error');
  if (!/^\S+@\S+\.\S+$/.test(email)) return setMessage('Enter a valid email address.', 'error');
  if (!primaryMarket || !experience || !planInterest) return setMessage('Choose your main market, trading experience, and access preference.', 'error');
  if (!smsOptIn && !emailOptIn) return setMessage('Choose at least one way for us to tell you when access opens.', 'error');

  const submitButton = document.getElementById('waitlistSubmit');
  submitButton.disabled = true;
  try {
    setMessage('Saving your place…');
    const user = auth.currentUser;
    const record = {
      auth_uid: user.uid,
      phone_number: user.phoneNumber,
      first_name: firstName.slice(0, 80),
      last_name: lastName.slice(0, 80) || null,
      email: email.slice(0, 254),
      social_handle: handle.slice(0, 100) || null,
      primary_market: primaryMarket,
      experience,
      plan_interest: planInterest,
      sms_opt_in: smsOptIn,
      email_opt_in: emailOptIn,
      status: 'waiting',
      ...trackingValues(),
      created_at: firestoreModule.serverTimestamp(),
      updated_at: firestoreModule.serverTimestamp(),
    };
    await firestoreModule.setDoc(firestoreModule.doc(db, 'waitlist', user.uid), record);
    markJoined(record);
    showStep('success');
  } catch (error) {
    setMessage(error.message || 'Could not save your waitlist signup.', 'error');
  } finally {
    submitButton.disabled = false;
  }
});

async function initializeWaitlist() {
  if (!layer) return;
  try {
    const [appModule, loadedAuthModule, loadedFirestoreModule] = await Promise.all([
      import(sdk('firebase-app')),
      import(sdk('firebase-auth')),
      import(sdk('firebase-firestore')),
    ]);
    authModule = loadedAuthModule;
    firestoreModule = loadedFirestoreModule;
    const config = await loadFirebaseConfig();
    const app = appModule.getApps().length ? appModule.getApps()[0] : appModule.initializeApp(config);
    auth = authModule.getAuth(app);
    db = firestoreModule.getFirestore(app);

    const localHostnames = new Set(['localhost', '127.0.0.1', '::1']);
    const query = new URLSearchParams(window.location.search);
    const useEmulators = localHostnames.has(window.location.hostname)
      && (window.location.port === '5000' || query.get('firebaseEmulator') === '1');
    if (useEmulators) {
      authModule.connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
      firestoreModule.connectFirestoreEmulator(db, '127.0.0.1', 8080);
    }
    await authModule.setPersistence(auth, authModule.browserLocalPersistence);
    firebaseReady = true;

    authModule.onAuthStateChanged(auth, async user => {
      if (user?.phoneNumber) {
        try {
          const existing = await loadExistingRecord(user);
          if (existing) markJoined(existing);
        } catch (_) {
          // The modal surfaces read errors if the user opens it.
        }
      }
      if (layer.classList.contains('open')) routeAuthenticatedUser();
    });

    if (layer.classList.contains('open')) routeAuthenticatedUser();
  } catch (error) {
    firebaseReady = false;
    setMessage(error.message || 'Waitlist signup could not initialize.', 'error');
    console.error('Traid waitlist initialization failed:', error);
  }
}

initializeWaitlist();
