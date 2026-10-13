# Multi-clinic cloud sync setup

This connects the Handral Dentistry app running on separate devices at
Bagalkot, Vidyagiri, and Navanagar to **one shared Google Sheet**, so all
three clinics see the same patients, appointments, billing, and staff
records. It's free, uses only your Google account, and takes about five
minutes.

**What does and doesn't sync:** patients, appointments, transactions,
invoices, staff, consultants, and consultation cases all sync. **Patient
photos stay on the device they were taken on** and are not uploaded —
Google Sheets cells can't hold something that large. Attendance punch
records also stay device-local (they're tied to who physically punched in
at that location).

## 1. Create the Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) and create a new
   blank spreadsheet.
2. Name it something like **"Handral Dentistry Data"**.

## 2. Add the sync script

1. In the Sheet, go to **Extensions → Apps Script**.
2. Delete whatever starter code is in `Code.gs`.
3. Open the `Code.gs` file in this folder, copy its entire contents, and
   paste it into the Apps Script editor.
4. Click the save icon (or Ctrl/Cmd+S).

## 3. Deploy it as a Web App

1. Click **Deploy → New deployment**.
2. Click the gear icon next to "Select type" and choose **Web app**.
3. Set:
   - **Description**: anything, e.g. "Handral sync v1"
   - **Execute as**: **Me** (your Google account)
   - **Who has access**: **Anyone**
4. Click **Deploy**.
5. Google will ask you to authorize the script (since it's your own
   script accessing your own Sheet). Click **Authorize access**, pick your
   account, click **Advanced → Go to (your project name) (unsafe)**, then
   **Allow**. This warning is expected for scripts you write yourself.
6. Copy the **Web app URL** shown — it looks like:
   `https://script.google.com/macros/s/AKfycb.../exec`

## 4. Connect every clinic's device

On **each** of the 3 clinics' computers/tablets:

1. Open the clinic app → **⚙️ Settings** tab.
2. Paste the Web App URL into **☁️ Multi-Clinic Cloud Sync**.
3. Click **💾 Save URL**, then **🧪 Test Connection** to confirm it says
   "Connected".

That's it — the app will now automatically push local changes to the
shared Sheet a couple of seconds after you make them, and pull in changes
from the other clinics every ~25 seconds (and whenever you switch back to
the browser tab). You can also tap **🔄 Sync Now** any time to force an
immediate sync in both directions.

## Updating the script later

If you ever change `Code.gs` (e.g. to pull in a newer version from this
repo), you must **create a new deployment version** for the change to take
effect — editing the code alone isn't enough:

**Deploy → Manage deployments → ✏️ (edit) → Version: New version → Deploy**

The Web App URL stays the same, so nothing needs to change on the clinic
devices.

## Troubleshooting

- **"Test Connection" fails / Failed to fetch**: double-check the URL was
  pasted in full (ending in `/exec`, not `/dev`), and that the deployment's
  "Who has access" is set to **Anyone** (not "Anyone with Google account").
- **Server returned non-JSON**: usually means the deployment is stale.
  Create a new deployment version (see above) and update the URL if it
  changed.
- **Changes from another clinic aren't showing up**: make sure that
  device's Settings page shows "✓ Connected" and that you've waited ~25
  seconds, or tap "🔄 Sync Now".
