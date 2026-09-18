# Handral Dentistry Website

## Files
- `index.html` — Public patient website (handraldentistry.com)
- `clinic/` — Staff clinic management app (handraldentistry.com/clinic)

## Deploy to GitHub Pages

1. Create repo on GitHub named `handraldentistry-website`
2. Upload all files
3. Settings → Pages → Source: main branch / root
4. Add custom domain: handraldentistry.com

## DNS Setup (at your domain registrar)
Add these DNS records:
```
Type: A      Name: @    Value: 185.199.108.153
Type: A      Name: @    Value: 185.199.109.153
Type: A      Name: @    Value: 185.199.110.153
Type: A      Name: @    Value: 185.199.111.153
Type: CNAME  Name: www  Value: YOUR-USERNAME.github.io
```

## Before Going Live - Update These
1. Replace all `+91 94480 XXXXX` with real phone numbers
2. Replace `https://wa.me/919448000000` with real WhatsApp number
3. Add real clinic addresses if needed
4. Add real photos to replace placeholders

## Going online (staff login + cross-device sync)

The clinic app now uses **Firebase** so staff can sign in and see the same
patient data from any device, instead of everything living only in one
browser's storage. This needs a one-time setup by whoever owns the Google
account for the clinic. The code is ready; these are the steps to activate it.

### 1. Create the Firebase project
1. Go to [console.firebase.google.com](https://console.firebase.google.com) and create a project (any name, e.g. "handral-dentistry").
2. **Authentication** → Sign-in method → enable **Email/Password**.
3. **Authentication** → Users → add one user per staff member who needs access (their email + a password you set — there is no public self-signup).
4. **Firestore Database** → Create database → start in production mode, pick a region close to India (e.g. `asia-south1`).
5. Project settings → General → "Your apps" → add a **Web app** → copy the `firebaseConfig` object it gives you.

### 2. Wire the app to your project
Open `clinic/cloud.js` and paste your real values into the `firebaseConfig` object near the top (these values are not secret — they just identify your project; actual access is controlled by Firestore rules + sign-in).

### 3. Deploy the security rules and the AI proxy function
This needs Node.js and the Firebase CLI (`npm install -g firebase-tools`, then `firebase login`).
```
firebase use --add           # pick your project, alias it "default"
firebase functions:secrets:set ANTHROPIC_API_KEY   # paste your console.anthropic.com key when prompted
firebase deploy --only firestore:rules,functions
```
After it deploys, copy the printed `aiProxy` function URL (looks like
`https://us-central1-<your-project>.cloudfunctions.net/aiProxy`) into the
`AI_PROXY_URL` constant near the top of `clinic/cloud.js`.

### 4. Push and go live
Commit and push the updated `clinic/cloud.js`, redeploy the site (GitHub Pages
updates automatically on push to `main`). Staff can now open `/clinic`, sign
in with the email/password you created for them, and their existing data on
that device is uploaded automatically the first time they sign in — nothing
already entered is lost. Changes on one device now show up on every other
signed-in device within a couple of seconds, without needing a manual sync.

### Notes
- The "Export Data" button in Settings still works and is a good habit to keep as an offline backup regardless of cloud sync.
- Very large patient photos (multi-MB) can exceed Firestore's 1MB-per-record limit; if that becomes an issue, ask about adding Firebase Storage for photos specifically.
- To remove a staff member's access, delete their user in Firebase Console → Authentication → Users.
