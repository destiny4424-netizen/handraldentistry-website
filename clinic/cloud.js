// ── Cloud Sync (Firebase Auth + Firestore) ─────────────────────────────────
// Loaded after the main app script, so it can read/write the same top-level
// `let` variables (patients, appointments, ...) and call the same render/save
// functions defined there. See README.md "Going online" for setup steps.
//
// Fill these two values in after creating your Firebase project and
// deploying the aiProxy Cloud Function (see README.md). The Firebase web
// config below is not a secret — it identifies your project, but every
// read/write is still gated by Firestore Security Rules + signed-in staff.
const firebaseConfig = {
  apiKey: "AIzaSyCBWVRVesXCEGJ-rfAdY_p3qpSkWjQ6D2A",
  authDomain: "handral-dentistry.firebaseapp.com",
  projectId: "handral-dentistry",
  storageBucket: "handral-dentistry.firebasestorage.app",
  messagingSenderId: "1004568027588",
  appId: "1:1004568027588:web:2dc655c6daf13a5df44491"
};
// URL of the deployed aiProxy Cloud Function. Filled in once the function is
// deployed (see README.md "Going online" step 3) — AI scanning won't work
// until this points at the real deployed URL.
// e.g. "https://us-central1-handral-dentistry.cloudfunctions.net/aiProxy"
const AI_PROXY_URL = "REPLACE_ME";

firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db = firebase.firestore();

const CLOUD_COLLECTIONS = ['patients','appointments','transactions','invoices','attendance','staff','consultants','consultCases'];

function getLocalArray(col){
  switch(col){
    case 'patients': return patients;
    case 'appointments': return appointments;
    case 'transactions': return transactions;
    case 'invoices': return invoices;
    case 'attendance': return attendance;
    case 'staff': return staff;
    case 'consultants': return consultants;
    case 'consultCases': return consultCases;
  }
}
function setLocalArray(col, arr){
  switch(col){
    case 'patients': patients = arr; break;
    case 'appointments': appointments = arr; break;
    case 'transactions': transactions = arr; break;
    case 'invoices': invoices = arr; break;
    case 'attendance': attendance = arr; break;
    case 'staff': staff = arr; break;
    case 'consultants': consultants = arr; break;
    case 'consultCases': consultCases = arr; break;
  }
}

function isSignedIn(){ return !!(auth.currentUser); }
function cloudEnabled(){ return isSignedIn(); }

function mergeById(existing, incoming){
  const map = new Map();
  (existing||[]).forEach(x=>{ if(x && x.id!=null) map.set(String(x.id), x); });
  (incoming||[]).forEach(x=>{ if(x && x.id!=null) map.set(String(x.id), x); });
  return Array.from(map.values());
}

function refreshAllViews(){
  try{
    ['updateStats','renderPatients','renderAppointments','renderTransactions','renderInvoices',
     'renderStaffList','renderStaffPunch','renderAttSummary','renderConsultants','renderCaseLog',
     'renderDentalChart'].forEach(fn=>{ if(typeof window[fn]==='function') window[fn](); });
  }catch(e){ console.warn('refreshAllViews error', e); }
}

function showSyncBadge(msg){
  const el = document.getElementById('cloudSyncStatus');
  if(el) el.textContent = msg;
}

// ── Diff-based push: only sends records that changed since the last sync ───
const _baseline = {};
CLOUD_COLLECTIONS.forEach(c=>_baseline[c]=new Map());

async function pushCollection(col){
  const arr = getLocalArray(col) || [];
  const baseline = _baseline[col];
  const currentIds = new Set();
  const toWrite = [];
  arr.forEach(rec=>{
    if(!rec || rec.id==null) return;
    const id = String(rec.id);
    currentIds.add(id);
    const json = JSON.stringify(rec);
    if(baseline.get(id) !== json) toWrite.push({id, json});
  });
  const toDelete = [];
  baseline.forEach((json,id)=>{ if(!currentIds.has(id)) toDelete.push(id); });
  if(!toWrite.length && !toDelete.length) return;
  const ops = toWrite.map(w=>({type:'set', id:w.id, json:w.json}))
    .concat(toDelete.map(id=>({type:'delete', id})));
  for(let i=0;i<ops.length;i+=450){
    const chunk = ops.slice(i,i+450);
    const batch = db.batch();
    chunk.forEach(op=>{
      const ref = db.collection(col).doc(op.id);
      if(op.type==='set') batch.set(ref, JSON.parse(op.json));
      else batch.delete(ref);
    });
    await batch.commit();
  }
  toWrite.forEach(w=>baseline.set(w.id, w.json));
  toDelete.forEach(id=>baseline.delete(id));
}

async function pushAllCollections(){
  if(!isSignedIn()) return;
  showSyncBadge('⬆️ Syncing…');
  try{
    for(const col of CLOUD_COLLECTIONS) await pushCollection(col);
    showSyncBadge('✅ Synced just now');
  }catch(e){
    console.warn('pushAllCollections failed', e);
    showSyncBadge('❌ Sync failed: '+e.message);
  }
}

let _pushTimer = null;
function scheduleCloudPush(){
  if(!isSignedIn()) return;
  if(_pushTimer) clearTimeout(_pushTimer);
  _pushTimer = setTimeout(()=>{ pushAllCollections(); }, 1200);
}

// ── Pull + merge (runs once on sign-in, so local-only data uploads safely) ─
async function pullAllOnce(){
  for(const col of CLOUD_COLLECTIONS){
    const snap = await db.collection(col).get();
    const remote = snap.docs.map(d=>d.data());
    setLocalArray(col, mergeById(getLocalArray(col), remote));
    _baseline[col] = new Map(remote.map(r=>[String(r.id), JSON.stringify(r)]));
  }
  saveLocal();
  refreshAllViews();
}

// ── Real-time listeners: other devices' changes arrive here live ──────────
let _unsubscribers = [];
function attachRealtimeListeners(){
  detachRealtimeListeners();
  CLOUD_COLLECTIONS.forEach(col=>{
    const unsub = db.collection(col).onSnapshot(snap=>{
      let changed = false;
      snap.docChanges().forEach(change=>{
        const data = change.doc.id ? {...change.doc.data()} : change.doc.data();
        const id = String(change.doc.id);
        const arr = getLocalArray(col);
        if(change.type==='removed'){
          setLocalArray(col, arr.filter(x=>String(x.id)!==id));
          _baseline[col].delete(id);
        } else {
          const idx = arr.findIndex(x=>String(x.id)===id);
          if(idx>=0) arr[idx]=data; else arr.push(data);
          _baseline[col].set(id, JSON.stringify(data));
        }
        changed = true;
      });
      if(changed){ saveLocal(); refreshAllViews(); }
    }, err=>console.warn('onSnapshot error for', col, err));
    _unsubscribers.push(unsub);
  });
}
function detachRealtimeListeners(){ _unsubscribers.forEach(u=>u()); _unsubscribers=[]; }

async function onSignedIn(){
  await pullAllOnce();     // bring in cloud data, keep any local-only records
  await pushAllCollections(); // upload anything that only existed on this device
  attachRealtimeListeners();
}

async function manualCloudSync(){
  if(!isSignedIn()){ showToast('Please sign in first'); return; }
  showSyncBadge('⏳ Syncing…');
  try{
    await pullAllOnce();
    await pushAllCollections();
    showToast('✅ Synced with cloud');
  }catch(e){
    showSyncBadge('❌ '+e.message);
    showToast('❌ Sync failed');
  }
}

// ── Legacy contract kept for a few call sites already wired in index.html ──
async function cloudGet(action, params={}){
  if(!isSignedIn()) return null;
  if(action==='ping') return {ok:true, time:Date.now()};
  if(action==='get_all'){
    const out = {};
    for(const col of CLOUD_COLLECTIONS){ const snap=await db.collection(col).get(); out[col]=snap.docs.map(d=>d.data()); }
    return out;
  }
  return null;
}
async function cloudPost(data){
  if(!isSignedIn()) return null;
  const {action, ...rest} = data||{};
  try{
    if(action==='bulk_patients'){
      const pts = rest.patients||[]; const clinic = rest.clinic;
      for(let i=0;i<pts.length;i+=450){
        const chunk = pts.slice(i,i+450);
        const batch = db.batch();
        chunk.forEach(p=>{
          const withClinic = {...p, clinic:p.clinic||clinic};
          const clean = JSON.parse(JSON.stringify(withClinic));
          batch.set(db.collection('patients').doc(String(p.id)), clean);
          _baseline.patients.set(String(p.id), JSON.stringify(clean));
        });
        await batch.commit();
      }
      return {created: pts.length};
    }
    const colFor = {save_patient:'patients', save_appointment:'appointments', save_transaction:'transactions', save_invoice:'invoices', delete_patient:'patients', delete_appointment:'appointments', delete_invoice:'invoices'};
    const col = colFor[action];
    if(!col || rest.id==null) return null;
    const id = String(rest.id);
    if(action.indexOf('delete_')===0){
      await db.collection(col).doc(id).delete();
      _baseline[col].delete(id);
    } else {
      const clean = JSON.parse(JSON.stringify(rest));
      await db.collection(col).doc(id).set(clean);
      _baseline[col].set(id, JSON.stringify(clean));
    }
    return {ok:true};
  }catch(e){
    console.warn('cloudPost failed', action, e);
    return {error: e.message};
  }
}
function queueSync(action, record){ cloudPost({action, ...record}).catch(()=>{}); }

// ── AI proxy: the Anthropic key lives only in the Cloud Function, never here ─
async function callClaudeAI(payload){
  if(!isSignedIn()) throw new Error('Not signed in');
  const token = await auth.currentUser.getIdToken();
  return fetch(AI_PROXY_URL, {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'Authorization':'Bearer '+token},
    body: JSON.stringify(payload)
  });
}

// ── Sign in / out ───────────────────────────────────────────────────────────
function showLoginScreen(show){
  const el = document.getElementById('loginScreen');
  if(el) el.style.display = show ? 'flex' : 'none';
}
function friendlyAuthError(e){
  const c = (e && e.code) || '';
  if(c.indexOf('user-not-found')>=0 || c.indexOf('wrong-password')>=0 || c.indexOf('invalid-credential')>=0) return 'Incorrect email or password.';
  if(c.indexOf('too-many-requests')>=0) return 'Too many attempts. Try again in a few minutes.';
  if(c.indexOf('invalid-email')>=0) return 'Enter a valid email address.';
  if(c.indexOf('network-request-failed')>=0) return 'Network error. Check your connection.';
  return 'Sign-in failed: '+((e && e.message) || 'unknown error');
}
async function staffSignIn(){
  const email = document.getElementById('loginEmail').value.trim();
  const pass = document.getElementById('loginPassword').value;
  const btn = document.getElementById('loginBtn');
  const err = document.getElementById('loginError');
  if(err) err.textContent = '';
  if(!email || !pass){ if(err) err.textContent = 'Enter email and password'; return; }
  if(btn){ btn.disabled = true; btn.textContent = 'Signing in…'; }
  try{
    await auth.signInWithEmailAndPassword(email, pass);
  }catch(e){
    if(err) err.textContent = friendlyAuthError(e);
  }
  if(btn){ btn.disabled = false; btn.textContent = 'Sign In'; }
}
function staffSignOut(){
  detachRealtimeListeners();
  auth.signOut();
}

auth.onAuthStateChanged(async user=>{
  const who = document.getElementById('cloudSignedInAs');
  if(user){
    showLoginScreen(false);
    if(who) who.textContent = user.email || user.uid;
    try{ await onSignedIn(); }
    catch(e){ console.warn('initial sync failed', e); showSyncBadge('❌ '+e.message); }
  } else {
    detachRealtimeListeners();
    if(who) who.textContent = '—';
    showSyncBadge('Not connected');
    showLoginScreen(true);
  }
  if(typeof checkApiWarning==='function') checkApiWarning();
});
