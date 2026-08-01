# Firebase setup for Traid

Traid is initialized for Firebase Hosting, Firebase Authentication, Cloud Firestore, and the local Firebase Emulator Suite.

## Hosting target

The configured Hosting target is `keitraid`, serving the existing `dashboard/` directory as a single-page app. The default Firebase project alias is also currently `keitraid`.

If the Firebase project ID and Hosting site ID are not both exactly `keitraid`, run:

```bash
firebase login
firebase use --add
firebase target:apply hosting keitraid YOUR_FIREBASE_HOSTING_SITE_ID
```

This updates `.firebaserc` while preserving `keitraid` as the local deployment target name.

## Enable Authentication providers

In Firebase Console, open **Authentication → Sign-in method** and enable:

1. Google
2. Phone

Add every production/custom domain to **Authentication → Settings → Authorized domains**. Phone verification SMS requires the Blaze plan and is billed per message. Use Firebase test phone numbers while developing.

## Create Firestore

Create the default Cloud Firestore database in Native mode, then deploy the included restrictive rules and empty index configuration.

```bash
firebase deploy --only firestore
```

Each authenticated user owns exactly one document at:

```text
users/{firebaseAuthUid}
```

On first sign-in the client creates an immutable `public_uid` with this format:

```text
YYMMDD + 14 uppercase letters/numbers
```

The date prefix comes from Firebase Authentication's server-generated account creation timestamp. Example shape: `260801A7M4Q9T2X8K3PL`.

Firestore rules prevent users from listing profiles, reading another user's profile, changing the public ID, changing creation time, assigning a role, deleting the record, or writing anywhere else.

## Firebase web configuration

On Firebase Hosting and the Firebase Hosting emulator, the dashboard reads project configuration from the reserved endpoint:

```text
/__/firebase/init.json
```

This avoids committing project configuration into source control. When running through an unrelated local static server, copy `dashboard/firebase-config.example.js` to `dashboard/firebase-config.local.js` and fill in the web app values. The local file is gitignored.

## Local test

```bash
npm install -g firebase-tools
firebase login
firebase use keitraid
firebase emulators:start
```

Open the Hosting emulator URL, normally `http://127.0.0.1:5000`.

## Deploy

```bash
firebase deploy --only hosting:keitraid,firestore
```

## Security boundary

Firebase Auth creates the user's Traid application identity. It does **not** grant access to the FastAPI administrator/trader session and does not grant permission to submit MT5 orders. Live-trading authorization remains server-controlled until Firebase ID-token verification and explicit role mapping are intentionally added to the backend.
