/**
 * Handral Dentistry — cloud sync backend.
 *
 * Deploy this as a Google Apps Script Web App bound to a Google Sheet.
 * All 3 clinics point their copy of the clinic app (Settings > Cloud Sync)
 * at the same deployed Web App URL to share one live patient /
 * appointment / billing database.
 *
 * See SETUP.md in this folder for step-by-step deployment instructions.
 *
 * Note: patient photos are never sent here (they're stripped client-side
 * before syncing) — a single photo can easily exceed a Sheets cell's
 * 50,000-character limit, so photos stay device-local only.
 */

var SHEETS = ['Patients','Appointments','Transactions','Invoices','Staff','Consultants','ConsultCases'];
var COLLECTION_KEY = {
  Patients: 'patients',
  Appointments: 'appointments',
  Transactions: 'transactions',
  Invoices: 'invoices',
  Staff: 'staff',
  Consultants: 'consultants',
  ConsultCases: 'consultCases'
};

function _sheet(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(['id', 'updatedAt', 'json']);
  }
  return sh;
}

function _readAll(name) {
  var sh = _sheet(name);
  var values = sh.getDataRange().getValues();
  var out = [];
  for (var i = 1; i < values.length; i++) {
    var row = values[i];
    if (!row[0]) continue;
    try { out.push(JSON.parse(row[2])); } catch (e) { /* skip corrupt row */ }
  }
  return out;
}

// Upsert records by id. Only overwrites an existing row if the incoming
// record is the same age or newer (last-write-wins), so a slow device
// pushing stale data can't clobber a newer edit from another clinic.
function _upsertMany(name, records) {
  if (!records || !records.length) return 0;
  var sh = _sheet(name);
  var values = sh.getDataRange().getValues();
  var rowOfId = {};
  for (var i = 1; i < values.length; i++) {
    if (values[i][0]) rowOfId[String(values[i][0])] = i + 1; // 1-based sheet row
  }
  var written = 0;
  records.forEach(function(rec) {
    if (!rec || rec.id == null) return;
    var id = String(rec.id);
    var updatedAt = Number(rec._updatedAt || Date.now());
    var json = JSON.stringify(rec);
    var rowNum = rowOfId[id];
    if (rowNum) {
      var existingUpdatedAt = Number(values[rowNum - 1][1] || 0);
      if (updatedAt >= existingUpdatedAt) {
        sh.getRange(rowNum, 1, 1, 3).setValues([[id, updatedAt, json]]);
        written++;
      }
    } else {
      sh.appendRow([id, updatedAt, json]);
      rowOfId[id] = sh.getLastRow();
      written++;
    }
  });
  return written;
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var action = e.parameter.action;
  if (action === 'ping') return _json({ok: true, time: Date.now()});
  if (action === 'get_all') {
    var result = {};
    SHEETS.forEach(function(name) { result[COLLECTION_KEY[name]] = _readAll(name); });
    return _json(result);
  }
  return _json({error: 'Unknown action: ' + action});
}

function doPost(e) {
  var data;
  try { data = JSON.parse(e.postData.contents); }
  catch (err) { return _json({error: 'Invalid JSON body'}); }

  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    if (data.action === 'bulk_sync') {
      var counts = {};
      SHEETS.forEach(function(name) {
        var key = COLLECTION_KEY[name];
        counts[key] = _upsertMany(name, data[key]);
      });
      return _json({ok: true, written: counts});
    }
    if (data.action === 'bulk_patients') {
      return _json({ok: true, created: _upsertMany('Patients', data.patients)});
    }
    if (data.action === 'save_patient')      return _json({ok: true, created: _upsertMany('Patients', [data])});
    if (data.action === 'save_appointment')  return _json({ok: true, created: _upsertMany('Appointments', [data])});
    if (data.action === 'save_transaction')  return _json({ok: true, created: _upsertMany('Transactions', [data])});
    if (data.action === 'save_invoice')      return _json({ok: true, created: _upsertMany('Invoices', [data])});
    return _json({error: 'Unknown action: ' + data.action});
  } finally {
    lock.releaseLock();
  }
}
