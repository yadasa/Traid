const FIREBASE_SDK_VERSION = '12.16.0';
const sdk = moduleName => `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}/${moduleName}.js`;

const layer = document.getElementById('waitlistLayer');
const openers = [...document.querySelectorAll('[data-open-waitlist]')];
const closeButton = document.getElementById('waitlistClose');
const backdrop = document.getElementById('waitlistBackdrop');
const messageNode = document.getElementById('waitlistMessage');
const phoneInput = document.getElementById('waitlistPhone');
const codeInput = document.getElementById('waitlistCode');
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
let currentStep = 'phone';

function setMessage(message = '', type = '') {
  if (!messageNode) return;
  messageNode.textContent = message;
  messageNode.className = `waitlist-message ${type}`.trim();
}

function showStep(step) {
  currentStep = step;
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
    if (local.firebaseConfig?.apiKey && !local.firebaseConfig.apiKey.startsWith('REPLACE_')) {
      return local.firebaseConfig;
    }
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
    showStep('code');
    setMessage(`Verification code sent to ${normalized}.`, 'success');
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
          // The modal will surface read errors if the user opens it.
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
