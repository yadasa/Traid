const FIREBASE_SDK_VERSION = '12.16.0';
const sdk = moduleName => `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}/${moduleName}.js`;
const PUBLIC_UID_SUFFIX_LENGTH = 14;
const PUBLIC_UID_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';

function injectAccountUI() {
  if (document.getElementById('firebaseAccountButton')) return;

  const style = document.createElement('style');
  style.textContent = `
    .firebase-account-button {
      min-height: 35px; max-width: 170px; padding: 0 11px; display: inline-flex;
      align-items: center; gap: 7px; border: 1px solid var(--line); border-radius: 999px;
      background: rgba(16,23,46,.8); color: var(--text); cursor: pointer;
      font-size: 10px; font-weight: 800; white-space: nowrap; overflow: hidden;
    }
    .firebase-account-button .account-avatar {
      width: 22px; height: 22px; flex: 0 0 auto; display: grid; place-items: center;
      border-radius: 50%; background: linear-gradient(135deg,var(--blue),var(--purple));
      font-size: 9px; color: white;
    }
    .firebase-account-button .account-label { overflow: hidden; text-overflow: ellipsis; }
    .firebase-auth-layer { position: fixed; inset: 0; z-index: 120; display: none; }
    .firebase-auth-layer.open { display: grid; place-items: center; }
    .firebase-auth-backdrop { position: absolute; inset: 0; background: rgba(1,3,10,.76); backdrop-filter: blur(5px); }
    .firebase-auth-card {
      position: relative; width: min(440px,calc(100vw - 24px)); max-height: min(760px,calc(100dvh - 24px));
      overflow: auto; padding: 18px; border: 1px solid var(--line-strong); border-radius: 17px;
      background: linear-gradient(180deg,rgba(13,19,42,.99),rgba(7,11,27,.99)); box-shadow: var(--shadow);
    }
    .firebase-auth-card header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
    .firebase-auth-card h2 { margin: 3px 0 0; font-size: 18px; }
    .firebase-auth-card p { margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
    .firebase-auth-actions { display: grid; gap: 10px; margin-top: 17px; }
    .firebase-google-button, .firebase-phone-button, .firebase-signout-button {
      min-height: 44px; border: 1px solid var(--line-strong); border-radius: 10px;
      background: rgba(17,24,49,.9); color: var(--text); font-weight: 820; cursor: pointer;
    }
    .firebase-google-button { background: linear-gradient(135deg,rgba(59,130,246,.9),rgba(139,92,246,.82)); }
    .firebase-phone-grid { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .firebase-phone-grid input { min-width: 0; width: 100%; }
    .firebase-code-row { display: none; grid-template-columns: 1fr auto; gap: 8px; }
    .firebase-code-row.visible { display: grid; }
    .firebase-auth-divider { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 9px; }
    .firebase-auth-divider::before, .firebase-auth-divider::after { content: ''; height: 1px; flex: 1; background: var(--line); }
    .firebase-auth-message { min-height: 18px; margin-top: 10px; color: var(--muted); font-size: 10px; line-height: 1.45; }
    .firebase-auth-message.error { color: #fecdd3; }
    .firebase-auth-message.success { color: #b7f7e8; }
    .firebase-profile { display: none; margin-top: 16px; padding: 13px; border: 1px solid var(--line); border-radius: 12px; background: rgba(5,8,22,.52); }
    .firebase-profile.visible { display: grid; gap: 9px; }
    .firebase-profile-row { display: flex; justify-content: space-between; gap: 15px; color: var(--muted); font-size: 10px; }
    .firebase-profile-row strong { color: var(--text); text-align: right; overflow-wrap: anywhere; }
    #firebaseRecaptcha { min-height: 1px; }
    @media (max-width: 720px) {
      .firebase-account-button { width: 35px; min-width: 35px; padding: 0; justify-content: center; }
      .firebase-account-button .account-label { display: none; }
      .firebase-auth-layer.open { align-items: end; }
      .firebase-auth-card { width: 100%; max-height: 90dvh; border-radius: 18px 18px 0 0; padding-bottom: max(20px,env(safe-area-inset-bottom)); }
      .firebase-phone-grid, .firebase-code-row { grid-template-columns: 1fr; }
    }
  `;
  document.head.appendChild(style);

  const accountButton = document.createElement('button');
  accountButton.id = 'firebaseAccountButton';
  accountButton.className = 'firebase-account-button';
  accountButton.type = 'button';
  accountButton.setAttribute('aria-label', 'Open account sign in');
  accountButton.innerHTML = '<span class="account-avatar">↗</span><span class="account-label">Sign in</span>';

  const settingsButton = document.getElementById('settingsButton');
  const actions = settingsButton?.parentElement || document.querySelector('.top-actions');
  if (settingsButton) actions.insertBefore(accountButton, settingsButton);
  else actions?.appendChild(accountButton);

  document.body.insertAdjacentHTML('beforeend', `
    <section class="firebase-auth-layer" id="firebaseAuthLayer" aria-hidden="true">
      <div class="firebase-auth-backdrop" id="firebaseAuthBackdrop"></div>
      <div class="firebase-auth-card" role="dialog" aria-modal="true" aria-labelledby="firebaseAuthTitle">
        <header>
          <div><span class="eyebrow">Traid account</span><h2 id="firebaseAuthTitle">Sign in</h2><p>Use Google or a verified phone number. Trading permissions remain controlled separately by the MT5 operator session.</p></div>
          <button class="icon-button" id="firebaseAuthClose" type="button" aria-label="Close account dialog">×</button>
        </header>
        <div class="firebase-auth-actions" id="firebaseSignedOutActions">
          <button class="firebase-google-button" id="firebaseGoogleSignIn" type="button">Continue with Google</button>
          <div class="firebase-auth-divider">or use phone</div>
          <div class="firebase-phone-grid">
            <input id="firebasePhoneNumber" type="tel" autocomplete="tel" inputmode="tel" placeholder="+1 555 555 5555" aria-label="Phone number in international format" />
            <button class="firebase-phone-button" id="firebaseSendCode" type="button">Send code</button>
          </div>
          <div id="firebaseRecaptcha"></div>
          <div class="firebase-code-row" id="firebaseCodeRow">
            <input id="firebaseVerificationCode" inputmode="numeric" autocomplete="one-time-code" placeholder="6-digit code" aria-label="Verification code" />
            <button class="firebase-phone-button" id="firebaseVerifyCode" type="button">Verify</button>
          </div>
        </div>
        <div class="firebase-profile" id="firebaseProfile">
          <div class="firebase-profile-row"><span>Signed in as</span><strong id="firebaseProfileName">—</strong></div>
          <div class="firebase-profile-row"><span>Traid ID</span><strong id="firebasePublicUid">Creating…</strong></div>
          <div class="firebase-profile-row"><span>Provider</span><strong id="firebaseProvider">—</strong></div>
          <button class="firebase-signout-button" id="firebaseSignOut" type="button">Sign out</button>
        </div>
        <div class="firebase-auth-message" id="firebaseAuthMessage">Firebase is initializing…</div>
      </div>
    </section>
  `);

  const layer = document.getElementById('firebaseAuthLayer');
  const open = () => { layer.classList.add('open'); layer.setAttribute('aria-hidden', 'false'); };
  const close = () => { layer.classList.remove('open'); layer.setAttribute('aria-hidden', 'true'); };
  accountButton.addEventListener('click', open);
  document.getElementById('firebaseAuthClose').addEventListener('click', close);
  document.getElementById('firebaseAuthBackdrop').addEventListener('click', close);
  window.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
}

function setMessage(message, type = '') {
  const node = document.getElementById('firebaseAuthMessage');
  if (!node) return;
  node.textContent = message;
  node.className = `firebase-auth-message ${type}`.trim();
}

async function loadFirebaseConfig() {
  try {
    const response = await fetch('/__/firebase/init.json', { cache: 'no-store' });
    if (response.ok) {
      const config = await response.json();
      if (config?.apiKey && config?.projectId) return config;
    }
  } catch (_) {
    // A normal local web server does not provide Firebase Hosting's reserved namespace.
  }

  try {
    const local = await import('./firebase-config.local.js');
    if (local.firebaseConfig?.apiKey && !local.firebaseConfig.apiKey.startsWith('REPLACE_')) {
      return local.firebaseConfig;
    }
  } catch (_) {
    // Optional local config is intentionally absent in source control.
  }

  throw new Error('Firebase configuration was not found. Run through Firebase Hosting/emulators or add dashboard/firebase-config.local.js.');
}

function creationDatePrefix(user) {
  const date = new Date(user.metadata?.creationTime || Date.now());
  const year = String(date.getUTCFullYear()).slice(-2);
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  return `${year}${month}${day}`;
}

function randomSuffix(length = PUBLIC_UID_SUFFIX_LENGTH) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, value => PUBLIC_UID_ALPHABET[value % PUBLIC_UID_ALPHABET.length]).join('');
}

function profileValues(user) {
  return {
    display_name: user.displayName || null,
    email: user.email || null,
    phone_number: user.phoneNumber || null,
    photo_url: user.photoURL || null,
    provider_ids: [...new Set((user.providerData || []).map(item => item.providerId).filter(Boolean))],
  };
}

function initials(user) {
  const source = user.displayName || user.email || user.phoneNumber || 'U';
  return source.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
}

async function initializeFirebaseAccount() {
  injectAccountUI();

  const [appModule, authModule, firestoreModule] = await Promise.all([
    import(sdk('firebase-app')),
    import(sdk('firebase-auth')),
    import(sdk('firebase-firestore')),
  ]);
  const config = await loadFirebaseConfig();
  const app = appModule.initializeApp(config);
  const auth = authModule.getAuth(app);
  const db = firestoreModule.getFirestore(app);
  await authModule.setPersistence(auth, authModule.browserLocalPersistence);

  let phoneConfirmation = null;
  let recaptchaVerifier = null;
  let activePublicUid = null;

  const ensureUserProfile = async user => {
    const ref = firestoreModule.doc(db, 'users', user.uid);
    const values = profileValues(user);
    activePublicUid = await firestoreModule.runTransaction(db, async transaction => {
      const snapshot = await transaction.get(ref);
      if (!snapshot.exists()) {
        const publicUid = `${creationDatePrefix(user)}${randomSuffix()}`;
        transaction.set(ref, {
          auth_uid: user.uid,
          public_uid: publicUid,
          created_at: firestoreModule.serverTimestamp(),
          updated_at: firestoreModule.serverTimestamp(),
          last_sign_in_at: firestoreModule.serverTimestamp(),
          ...values,
          role: 'user',
          status: 'active',
        });
        return publicUid;
      }

      transaction.update(ref, {
        updated_at: firestoreModule.serverTimestamp(),
        last_sign_in_at: firestoreModule.serverTimestamp(),
        ...values,
      });
      return snapshot.data().public_uid;
    });
    return activePublicUid;
  };

  const renderUser = async user => {
    const button = document.getElementById('firebaseAccountButton');
    const signedOut = document.getElementById('firebaseSignedOutActions');
    const profile = document.getElementById('firebaseProfile');
    if (!user) {
      activePublicUid = null;
      button.innerHTML = '<span class="account-avatar">↗</span><span class="account-label">Sign in</span>';
      signedOut.style.display = 'grid';
      profile.classList.remove('visible');
      setMessage('Sign in with Google or phone to create your Traid account record.');
      return;
    }

    button.innerHTML = `<span class="account-avatar">${initials(user)}</span><span class="account-label">${user.displayName || user.email || user.phoneNumber || 'Account'}</span>`;
    signedOut.style.display = 'none';
    profile.classList.add('visible');
    document.getElementById('firebaseProfileName').textContent = user.displayName || user.email || user.phoneNumber || user.uid;
    document.getElementById('firebaseProvider').textContent = (user.providerData || []).map(item => item.providerId.replace('.com', '')).join(', ') || 'Firebase';
    document.getElementById('firebasePublicUid').textContent = 'Creating…';
    try {
      const publicUid = await ensureUserProfile(user);
      document.getElementById('firebasePublicUid').textContent = publicUid;
      setMessage('Account is signed in and synchronized with Firestore.', 'success');
    } catch (error) {
      document.getElementById('firebasePublicUid').textContent = 'Unavailable';
      setMessage(error.message || 'Could not create the Firestore account record.', 'error');
    }
  };

  authModule.onAuthStateChanged(auth, renderUser);
  authModule.getRedirectResult(auth).catch(error => setMessage(error.message, 'error'));

  document.getElementById('firebaseGoogleSignIn').addEventListener('click', async () => {
    setMessage('Opening Google sign-in…');
    const provider = new authModule.GoogleAuthProvider();
    provider.setCustomParameters({ prompt: 'select_account' });
    try {
      if (window.matchMedia('(max-width: 720px)').matches) {
        await authModule.signInWithRedirect(auth, provider);
      } else {
        await authModule.signInWithPopup(auth, provider);
      }
    } catch (error) {
      if (error.code === 'auth/popup-blocked') await authModule.signInWithRedirect(auth, provider);
      else setMessage(error.message, 'error');
    }
  });

  document.getElementById('firebaseSendCode').addEventListener('click', async () => {
    const phoneNumber = document.getElementById('firebasePhoneNumber').value.trim();
    if (!/^\+[1-9]\d{7,14}$/.test(phoneNumber.replace(/[\s()-]/g, ''))) {
      setMessage('Enter the phone number in international format, such as +17135551234.', 'error');
      return;
    }
    const normalized = phoneNumber.replace(/[\s()-]/g, '');
    try {
      setMessage('Complete the anti-abuse check, then the verification code will be sent.');
      if (recaptchaVerifier) recaptchaVerifier.clear();
      recaptchaVerifier = new authModule.RecaptchaVerifier(auth, 'firebaseRecaptcha', {
        size: 'normal',
      });
      phoneConfirmation = await authModule.signInWithPhoneNumber(auth, normalized, recaptchaVerifier);
      document.getElementById('firebaseCodeRow').classList.add('visible');
      document.getElementById('firebaseVerificationCode').focus();
      setMessage('Verification code sent.', 'success');
    } catch (error) {
      recaptchaVerifier?.clear();
      recaptchaVerifier = null;
      setMessage(error.message, 'error');
    }
  });

  document.getElementById('firebaseVerifyCode').addEventListener('click', async () => {
    const code = document.getElementById('firebaseVerificationCode').value.trim();
    if (!phoneConfirmation) return setMessage('Send a verification code first.', 'error');
    if (!/^\d{6}$/.test(code)) return setMessage('Enter the 6-digit verification code.', 'error');
    try {
      setMessage('Verifying code…');
      await phoneConfirmation.confirm(code);
      phoneConfirmation = null;
      document.getElementById('firebaseCodeRow').classList.remove('visible');
    } catch (error) {
      setMessage(error.message, 'error');
    }
  });

  document.getElementById('firebaseSignOut').addEventListener('click', async () => {
    try {
      await authModule.signOut(auth);
      setMessage('Signed out.', 'success');
    } catch (error) {
      setMessage(error.message, 'error');
    }
  });

  window.traidFirebase = {
    app,
    auth,
    db,
    get currentUser() { return auth.currentUser; },
    get publicUid() { return activePublicUid; },
    getIdToken: forceRefresh => auth.currentUser?.getIdToken(Boolean(forceRefresh)) || Promise.resolve(null),
  };
}

initializeFirebaseAccount().catch(error => {
  injectAccountUI();
  setMessage(error.message || 'Firebase initialization failed.', 'error');
  console.error('Traid Firebase initialization failed:', error);
});
