#!/usr/bin/env python3
"""Handral Books: single-file bookkeeping server (v5).

Python 3 standard library only. pdfplumber is optional (needed for PDF import).
Data lives in books.db next to this file unless BOOKS_DB points elsewhere.
Listens on 127.0.0.1:3020 by default.
"""
import base64
import calendar
import csv
import email.header
import email.utils
import html as htmllib
import http.cookiejar
import imaplib
import hashlib
import io
import json
import os
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import date as Date, timedelta
from xml.sax.saxutils import escape as xesc
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("BOOKS_DB") or os.path.join(DIR, "books.db")
HOST = os.environ.get("BOOKS_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOOKS_PORT", "3020"))
LOCK = threading.Lock()
MAX_BYTES = 15 * 1024 * 1024

# (name, group). GROUPS sets the order and headings used across the app.
GROUPS = [
    ("receipts", "Clinic receipts"),
    ("expenses", "Clinic expenses"),
    ("other_income", "Other income"),
    ("tax", "Tax and deduction payments"),
    ("invest", "Trading and investments"),
    ("assets", "Assets, deposits, taxes and parties"),
    ("loans", "Loans and cards"),
    ("personal", "Transfers and personal"),
]
CATS = [
    ("Patient receipts", "receipts"), ("Other clinic income", "receipts"),
    ("Lab charges", "expenses"), ("Dental materials", "expenses"),
    ("Staff salaries", "expenses"), ("Consultant fees", "expenses"),
    ("Rent", "expenses"),
    ("Utilities and phone", "expenses"), ("Equipment and repairs", "expenses"),
    ("Marketing", "expenses"), ("Fuel and travel", "expenses"),
    ("Professional fees and subscriptions", "expenses"),
    ("Clinic insurance", "expenses"), ("Bank charges", "expenses"),
    ("Loan interest", "expenses"), ("Other expense", "expenses"),
    ("Salary income", "other_income"), ("Interest received", "other_income"),
    ("Other income", "other_income"),
    ("Term insurance premium (80C)", "tax"),
    ("Life insurance premium (80C)", "tax"),
    ("Health insurance premium (80D)", "tax"),
    ("Tax-saving investment (80C)", "tax"),
    ("Income tax and TDS paid", "tax"), ("Donations (80G)", "tax"),
    ("School fees (80C)", "tax"),
    ("Trading transfer", "invest"), ("Investments", "invest"), ("Chit fund", "invest"),
    ("Fixed assets (equipment, furniture)", "assets"), ("Deposits paid (rent, electricity)", "assets"),
    ("Sundry creditors (suppliers, labs)", "assets"), ("Sundry debtors (insurance, corporates)", "assets"),
    ("GST, TDS and professional tax", "assets"), ("Capital introduced", "assets"),
    ("Bank overdraft", "assets"),
    ("Car loan", "loans"), ("Jewel loan", "loans"), ("Personal loan", "loans"),
    ("Home loan", "loans"), ("Education loan", "loans"), ("Business loan", "loans"),
    ("Other loan", "loans"), ("Hand loans given", "loans"), ("Credit card payment", "loans"),
    ("Own account transfer", "personal"),
    ("Cash deposit or withdrawal", "personal"),
    ("Vehicle insurance", "personal"), ("Other insurance", "personal"),
    ("Family and friends", "personal"), ("Personal", "personal"),
]
# Each head as a Tally ledger under a Tally group, for CAs: head -> (ledger, group).
TALLY = {
    "Patient receipts": ("Professional Fees Received", "Direct Incomes"),
    "Other clinic income": ("Other Clinic Income", "Direct Incomes"),
    "Dental materials": ("Purchase - Dental Materials", "Purchase Accounts"),
    "Lab charges": ("Lab Charges", "Direct Expenses"),
    "Consultant fees": ("Consultant Fees", "Direct Expenses"),
    "Staff salaries": ("Salaries & Wages", "Indirect Expenses"),
    "Rent": ("Rent", "Indirect Expenses"),
    "Utilities and phone": ("Electricity & Telephone", "Indirect Expenses"),
    "Equipment and repairs": ("Repairs & Maintenance", "Indirect Expenses"),
    "Marketing": ("Advertisement & Marketing", "Indirect Expenses"),
    "Fuel and travel": ("Conveyance & Travelling", "Indirect Expenses"),
    "Professional fees and subscriptions": ("Professional Fees & Subscriptions", "Indirect Expenses"),
    "Clinic insurance": ("Insurance - Clinic", "Indirect Expenses"),
    "Bank charges": ("Bank Charges", "Indirect Expenses"),
    "Loan interest": ("Interest on Loans", "Indirect Expenses"),
    "Other expense": ("Miscellaneous Expenses", "Indirect Expenses"),
    "Interest received": ("Interest Received", "Indirect Incomes"),
    "Other income": ("Other Income", "Indirect Incomes"),
    "Salary income": ("Salary Income (Proprietor)", "Capital Account"),
    "Term insurance premium (80C)": ("Drawings - Term Insurance (80C)", "Capital Account"),
    "Life insurance premium (80C)": ("Drawings - LIC Premium (80C)", "Capital Account"),
    "Health insurance premium (80D)": ("Drawings - Mediclaim (80D)", "Capital Account"),
    "Tax-saving investment (80C)": ("Drawings - PPF/ELSS/APY (80C)", "Capital Account"),
    "Income tax and TDS paid": ("Drawings - Income Tax", "Capital Account"),
    "Donations (80G)": ("Drawings - Donations (80G)", "Capital Account"),
    "School fees (80C)": ("Drawings - Tuition Fees (80C)", "Capital Account"),
    "Vehicle insurance": ("Drawings - Vehicle Insurance", "Capital Account"),
    "Other insurance": ("Drawings - Other Insurance", "Capital Account"),
    "Family and friends": ("Drawings - Family", "Capital Account"),
    "Personal": ("Drawings - Personal", "Capital Account"),
    "Trading transfer": ("Investment - Trading Account", "Investments"),
    "Investments": ("Investments", "Investments"),
    "Chit fund": ("Chit Fund", "Investments"),
    "Car loan": ("Car Loan", "Secured Loans"),
    "Jewel loan": ("Gold Loan", "Secured Loans"),
    "Home loan": ("Housing Loan", "Secured Loans"),
    "Personal loan": ("Personal Loan", "Unsecured Loans"),
    "Education loan": ("Education Loan", "Unsecured Loans"),
    "Business loan": ("Business Loan", "Unsecured Loans"),
    "Other loan": ("Other Loans", "Unsecured Loans"),
    "Credit card payment": ("Credit Card", "Current Liabilities"),
    "Hand loans given": ("Loans & Advances - Friends", "Loans & Advances (Asset)"),
    "Fixed assets (equipment, furniture)": ("Dental Equipment & Furniture", "Fixed Assets"),
    "Deposits paid (rent, electricity)": ("Security Deposits", "Deposits (Asset)"),
    "Sundry creditors (suppliers, labs)": ("Sundry Creditors", "Sundry Creditors"),
    "Sundry debtors (insurance, corporates)": ("Sundry Debtors", "Sundry Debtors"),
    "GST, TDS and professional tax": ("Duties & Taxes", "Duties & Taxes"),
    "Capital introduced": ("Capital Introduced", "Capital Account"),
    "Bank overdraft": ("Bank OD A/c", "Bank OD A/c"),
    "Own account transfer": ("Inter-Bank Transfer", "Bank Accounts"),
    "Cash deposit or withdrawal": ("Cash", "Cash-in-Hand"),
    "": ("Suspense A/c", "Suspense A/c"),
}
# Tally's predefined groups in its own order (P&L, then Balance Sheet), with their parents.
TALLY_PARENT = {
    "Sales Accounts": "", "Direct Incomes": "", "Purchase Accounts": "", "Direct Expenses": "",
    "Indirect Incomes": "", "Indirect Expenses": "",
    "Capital Account": "", "Reserves & Surplus": "Capital Account",
    "Loans (Liability)": "", "Secured Loans": "Loans (Liability)",
    "Unsecured Loans": "Loans (Liability)", "Bank OD A/c": "Loans (Liability)",
    "Current Liabilities": "", "Duties & Taxes": "Current Liabilities",
    "Provisions": "Current Liabilities", "Sundry Creditors": "Current Liabilities",
    "Fixed Assets": "", "Investments": "",
    "Current Assets": "", "Stock-in-Hand": "Current Assets", "Deposits (Asset)": "Current Assets",
    "Loans & Advances (Asset)": "Current Assets", "Sundry Debtors": "Current Assets",
    "Bank Accounts": "Current Assets", "Cash-in-Hand": "Current Assets",
    "Misc. Expenses (ASSET)": "", "Branch / Divisions": "", "Suspense A/c": "",
}
TALLY_GROUPS = list(TALLY_PARENT)
BOOK_LEDGERS = {v[0] for v in TALLY.values()} | {"Cash"}
# Heads whose entries are posted to a party ledger named after the payer or payee.
PARTY_GROUPS = ("Sundry Creditors", "Sundry Debtors")
PNL_GROUPS = ("Direct Incomes", "Purchase Accounts", "Direct Expenses", "Indirect Incomes",
              "Indirect Expenses")
KIND_GROUP = {"Bank": "Bank Accounts", "Credit card": "Current Liabilities",
              "Trading": "Investments", "Cash": "Cash-in-Hand"}
CONSULT = "Consultant fees"
SALARY = "Staff salaries"
# Named people whose bank payments are filed under a fixed head: kind -> (table, head).
PEOPLE = {"consultant": ("consultants", CONSULT), "staff": ("staff", SALARY)}
CASH_HEAD = "Patient receipts"
TREATMENTS = ["Consultation", "Scaling and cleaning", "Filling", "Root canal",
              "Extraction", "Crown or cap", "Bridge", "Denture", "Implant", "Braces",
              "Aligners", "X-ray", "Whitening", "Surgery"]
CLINICS = ["Main", "Vidyagiri", "Navanagar", "Common"]
OWNERS = ["Self", "Daughter"]
KINDS = ["Bank", "Credit card", "Trading", "Cash"]

# Starter rules: (text to find in narration, direction, category). Edit in the app.
SEED_RULES = [
    ("RAVICHANDRA", "any", "Own account transfer"),
    ("HANDRAL RAVICH", "any", "Own account transfer"),
    ("MONEYLICIOUS", "any", "Trading transfer"),
    ("ZERODHA", "any", "Trading transfer"),
    ("FYERS", "any", "Trading transfer"),
    ("KOTAK SEC", "any", "Trading transfer"),
    ("SALARY", "in", "Salary income"),
    ("Int.Pd", "in", "Interest received"),
    ("CRED Club", "out", "Credit card payment"),
    ("Disbursement Credit", "in", "Other loan"),
    ("JLOTH", "out", "Jewel loan"),
    ("Dentalkart", "out", "Dental materials"),
    ("DENTICITY", "out", "Dental materials"),
]
# Added when an older database is upgraded (and on a fresh one).
SEED_RULES_2 = [
    ("RAISE SECURITIES", "any", "Trading transfer"),
    ("RAISESECURITIES", "any", "Trading transfer"),
    ("ANGEL ONE", "any", "Trading transfer"),
    ("LICPGINEW", "out", "Life insurance premium (80C)"),
    ("Life Insurance Co", "out", "Life insurance premium (80C)"),
    ("CAMS-", "any", "Investments"),
    ("MUTUALFUND", "any", "Investments"),
    ("BAJAJ FINANCE", "any", "Personal loan"),
    ("EARLYSALARY", "any", "Personal loan"),
    ("RETURN CHARGES", "out", "Bank charges"),
    ("JL APPRAISER", "out", "Bank charges"),
]

# Added in database version 3.
SEED_RULES_3 = [
    ("SCHOOL", "out", "School fees (80C)"),
    ("VIDYALAYA", "out", "School fees (80C)"),
    ("CASH DEP", "in", "Cash deposit or withdrawal"),
    ("BY CASH", "in", "Cash deposit or withdrawal"),
]

# Added in database version 5.
SEED_RULES_5 = [
    ("STAR HEALTH", "out", "Health insurance premium (80D)"),
    ("NIVA BUPA", "out", "Health insurance premium (80D)"),
    ("CARE HEALTH", "out", "Health insurance premium (80D)"),
    ("ADITYA BIRLA HEALTH", "out", "Health insurance premium (80D)"),
    ("MANIPAL CIGNA", "out", "Health insurance premium (80D)"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS txns(
  id INTEGER PRIMARY KEY, account_id INTEGER, date TEXT, narration TEXT,
  ref TEXT, debit REAL, credit REAL, balance REAL,
  category TEXT DEFAULT '', clinic TEXT DEFAULT '', note TEXT DEFAULT '',
  seq INTEGER, hash TEXT UNIQUE, payee TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_txn_date ON txns(date);
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY, pattern TEXT, dir TEXT, category TEXT, clinic TEXT,
  field TEXT DEFAULT 'narration');
CREATE TABLE IF NOT EXISTS consultants(
  id INTEGER PRIMARY KEY, name TEXT, match TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS staff(
  id INTEGER PRIMARY KEY, name TEXT, match TEXT UNIQUE, role TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS trading_pnl(
  id INTEGER PRIMARY KEY, account_id INTEGER, fy INTEGER,
  intraday REAL DEFAULT 0, fno REAL DEFAULT 0, stcg REAL DEFAULT 0,
  ltcg REAL DEFAULT 0, dividends REAL DEFAULT 0, charges REAL DEFAULT 0,
  turnover REAL DEFAULT 0, note TEXT DEFAULT '', UNIQUE(account_id, fy));
CREATE TABLE IF NOT EXISTS itr_returns(
  id INTEGER PRIMARY KEY, owner TEXT DEFAULT 'Self', ay INTEGER, form TEXT,
  name TEXT DEFAULT '', pan TEXT DEFAULT '', filename TEXT, uploaded TEXT,
  summary TEXT, flat TEXT, data BLOB);
CREATE TABLE IF NOT EXISTS loans(
  id INTEGER PRIMARY KEY, name TEXT, type TEXT, lender TEXT DEFAULT '',
  owner TEXT DEFAULT 'Self', purpose TEXT DEFAULT 'Personal', amount REAL DEFAULT 0,
  start TEXT DEFAULT '', rate REAL DEFAULT 0, emi REAL DEFAULT 0, match TEXT DEFAULT '',
  closed TEXT DEFAULT '', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS loan_years(
  loan_id INTEGER, fy INTEGER, interest REAL, outstanding REAL, UNIQUE(loan_id, fy));
CREATE TABLE IF NOT EXISTS policies(
  id INTEGER PRIMARY KEY, name TEXT, type TEXT, insurer TEXT DEFAULT '',
  policy_no TEXT DEFAULT '', owner TEXT DEFAULT 'Self', insured TEXT DEFAULT 'Self and family',
  senior INTEGER DEFAULT 0, purpose TEXT DEFAULT 'Personal', cover REAL DEFAULT 0,
  premium REAL DEFAULT 0, frequency TEXT DEFAULT 'Yearly', due TEXT DEFAULT '',
  match TEXT DEFAULT '', closed TEXT DEFAULT '', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS mf_uploads(
  id INTEGER PRIMARY KEY, owner TEXT, filename TEXT, uploaded TEXT, kind TEXT,
  added INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS mf_txns(
  id INTEGER PRIMARY KEY, upload_id INTEGER, owner TEXT, folio TEXT DEFAULT '',
  scheme TEXT, date TEXT, type TEXT, units REAL, amount REAL, hash TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS mf_cg(
  id INTEGER PRIMARY KEY, upload_id INTEGER, owner TEXT, scheme TEXT, sold TEXT,
  bought TEXT DEFAULT '', units REAL DEFAULT 0, sale REAL DEFAULT 0, cost REAL DEFAULT 0,
  stcg REAL DEFAULT 0, ltcg REAL DEFAULT 0, hash TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS mf_nav(scheme TEXT PRIMARY KEY, date TEXT, nav REAL);
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY, name TEXT, type TEXT, owner TEXT DEFAULT 'Self',
  bought TEXT DEFAULT '', cost REAL DEFAULT 0, value REAL DEFAULT 0,
  valued TEXT DEFAULT '', sold TEXT DEFAULT '', sale REAL DEFAULT 0,
  note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS mail_log(
  id INTEGER PRIMARY KEY, msgid TEXT UNIQUE, date TEXT, subject TEXT, account TEXT DEFAULT '',
  status TEXT, found INTEGER DEFAULT 0, added INTEGER DEFAULT 0, detail TEXT DEFAULT '',
  checked TEXT, file BLOB, filename TEXT DEFAULT '');
"""


def spellings(match):
    """A person's names in bank entries: "GIRISH V CHOUR, vgirish5" -> two spellings."""
    return [m for m in (" ".join(x.split()) for x in str(match or "").split(",")) if len(m) >= 3]


def person_where(match):
    alts = spellings(match)
    sql = " OR ".join(["narration LIKE ? OR payee=?"] * len(alts)) or "0"
    args = [x for m in alts for x in (f"%{m}%", m.upper())]
    return f"({sql})", args


def add_person(con, table, name, match, role=None):
    """Add a consultant or staff member, or add spellings to one with the same name."""
    name = " ".join(str(name or "").split()) or match
    old = con.execute(f"SELECT * FROM {table} WHERE LOWER(name)=LOWER(?) OR match=?",
                      (name, match)).fetchone()
    if old:
        merged = ", ".join(dict.fromkeys(spellings(old["match"]) + spellings(match)))
        con.execute(f"UPDATE {table} SET match=? WHERE id=?", (merged, old["id"]))
        pid, added = old["id"], 0
    else:
        pid, added = con.execute(f"INSERT INTO {table}(name,match) VALUES(?,?)",
                                 (name, match)).lastrowid, 1
    if table == "staff" and role is not None:
        con.execute("UPDATE staff SET role=? WHERE id=?", (role, pid))
    return added


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def payee_of(narration):
    """Best guess at who the money went to or came from, used to group entries."""
    n = narration.strip()
    at = n.upper().find("UPI/")
    if at > 0:  # e.g. SBI "BY TRANSFER- UPI/CR/ref/NAME/BANK/..."
        n = n[at:]
    u = n.upper()
    name = ""
    if u.startswith("UPI/"):
        parts = n.split("/")
        name = parts[3] if len(parts) > 3 else ""
    elif u.startswith("UPI-"):
        parts = n.split("-")
        name = parts[1] if len(parts) > 1 else ""
    elif u.startswith("ATW"):
        name = "ATM WITHDRAWAL"
    elif u.startswith("ACH D"):
        name = re.sub(r"(?i)^ACH D\s*-\s*", "", n).split("-")[0]
    elif re.match(r"(MB/)?(NEFT|RTGS|IMPS)", u):
        for part in re.split(r"[-/]", n)[1:]:
            part = part.strip()
            if len(part) > 4 and not re.search(r"\d", part) and part.upper() not in (
                    "NEFT", "RTGS", "IMPS", "HDFC", "NETBANK"):
                name = part
                break
    if not name.strip():
        name = " ".join(re.sub(r"[^A-Za-z ]", " ", n).split()[:3])
    name = " ".join(re.sub(r"[^A-Za-z0-9&. ]", " ", name).upper().split())
    return name[:30] or "OTHER"


def init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = db()
    # WAL lets backups and readers run while the app is writing.
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    fresh = not con.execute("SELECT 1 FROM rules LIMIT 1").fetchone()
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        cols = lambda t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        if "payee" not in cols("txns"):
            con.execute("ALTER TABLE txns ADD COLUMN payee TEXT DEFAULT ''")
        if "field" not in cols("rules"):
            con.execute("ALTER TABLE rules ADD COLUMN field TEXT DEFAULT 'narration'")
        for r in con.execute("SELECT id, narration FROM txns").fetchall():
            con.execute("UPDATE txns SET payee=? WHERE id=?",
                        (payee_of(r["narration"]), r["id"]))
        have = {r[0] for r in con.execute("SELECT pattern FROM rules")}
        seeds = (SEED_RULES if fresh else []) + SEED_RULES_2
        con.executemany(
            "INSERT INTO rules(pattern,dir,category,clinic) VALUES(?,?,?,'')",
            [s for s in seeds if s[0] not in have])
        # Version 1 filed EarlySalary loan credits under salary; send them back
        # through the rules so they land under loans.
        con.execute("UPDATE txns SET category='' WHERE category='Salary income'"
                    " AND narration LIKE '%EARLYSALARY%'")
        apply_rules(con)
        con.execute("PRAGMA user_version=2")
    con.execute("CREATE INDEX IF NOT EXISTS ix_txn_payee ON txns(payee)")
    acols = [r[1] for r in con.execute("PRAGMA table_info(accounts)")]
    if "owner" not in acols:
        con.execute("ALTER TABLE accounts ADD COLUMN owner TEXT DEFAULT 'Self'")
    if "kind" not in acols:
        con.execute("ALTER TABLE accounts ADD COLUMN kind TEXT DEFAULT 'Bank'")
    if "bank_match" not in acols:
        con.execute("ALTER TABLE accounts ADD COLUMN bank_match TEXT DEFAULT ''")
    if version < 3:
        have = {r[0] for r in con.execute("SELECT pattern FROM rules")}
        con.executemany(
            "INSERT INTO rules(pattern,dir,category,clinic) VALUES(?,?,?,'')",
            [s for s in SEED_RULES_3 if s[0] not in have])
        apply_rules(con)
        con.execute("PRAGMA user_version=3")
    if version < 4:  # one category per kind of loan instead of "Loan received or repaid"
        old = "Loan received or repaid"
        for pat, cat in (("JLOTH", "Jewel loan"), ("JEWEL", "Jewel loan"), ("GOLD LOAN", "Jewel loan"),
                         ("EARLYSALARY", "Personal loan"), ("BAJAJ FINANCE", "Personal loan"),
                         ("CAR LOAN", "Car loan"), ("AUTO LOAN", "Car loan"),
                         ("HOME LOAN", "Home loan"), ("HOUSING LOAN", "Home loan")):
            con.execute("UPDATE txns SET category=? WHERE category=? AND narration LIKE ?",
                        (cat, old, f"%{pat}%"))
            con.execute("UPDATE rules SET category=? WHERE category=? AND pattern LIKE ?",
                        (cat, old, f"%{pat}%"))
        con.execute("UPDATE txns SET category='Other loan' WHERE category=?", (old,))
        con.execute("UPDATE rules SET category='Other loan' WHERE category=?", (old,))
        con.execute("PRAGMA user_version=4")
    if version < 5:  # clinic insurance gets its own name; common health insurers sorted
        con.execute("UPDATE txns SET category='Clinic insurance' WHERE category='Insurance'")
        con.execute("UPDATE rules SET category='Clinic insurance' WHERE category='Insurance'")
        have = {r[0] for r in con.execute("SELECT pattern FROM rules")}
        con.executemany(
            "INSERT INTO rules(pattern,dir,category,clinic) VALUES(?,?,?,'')",
            [s for s in SEED_RULES_5 if s[0] not in have])
        apply_rules(con)
        con.execute("PRAGMA user_version=5")
    if version < 6:
        rows = con.execute("SELECT id, narration, payee FROM txns").fetchall()
        new = {r["id"]: payee_of(r["narration"]) for r in rows}
        spread = {}
        for r in rows:
            spread.setdefault(r["payee"], set()).add(new[r["id"]])
        blobs = [p for p, names in spread.items() if len(names) >= 3]
        if blobs:  # one old name covered several real payers: its sorting was wrong
            marks = ",".join("?" * len(blobs))
            con.execute(f"DELETE FROM rules WHERE field='payee' AND pattern IN ({marks})", blobs)
            con.execute(f"UPDATE txns SET category='', clinic='' WHERE payee IN ({marks})", blobs)
        con.executemany("UPDATE txns SET payee=? WHERE id=?", [(p, i) for i, p in new.items()])
        apply_rules(con)
        con.execute("PRAGMA user_version=6")
    if version < 7:  # payments from anyone else are patient receipts, across all years
        apply_rules(con)
        con.execute("PRAGMA user_version=7")
    if version < 8:  # version 7 also filed money into the daughter's accounts; take that back
        con.execute("UPDATE txns SET category='', clinic='' WHERE category='Patient receipts' AND"
                    " account_id IN (SELECT id FROM accounts WHERE owner='Daughter')")
        con.execute("PRAGMA user_version=8")
    if version < 9:  # small payments are personal expenses, across all years
        apply_rules(con)
        con.execute("PRAGMA user_version=9")
    con.commit()
    con.close()


# ---------- statement parsing ----------

DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d-%b-%y",
                "%d-%b-%Y", "%d %b %Y", "%d %b %y", "%Y-%m-%d", "%d.%m.%Y")


def parse_date(cell, serial=False):
    """Text date to YYYY-MM-DD. With serial=True also accepts Excel day numbers."""
    s = str(cell or "").strip().split("\n")[0].split("(")[0].strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", s)  # "2025-04-01 00:00:00"
    if s != str(cell or "").strip():
        return parse_date(s)
    if serial and re.fullmatch(r"\d{5}(\.0+)?", s) and 30000 < float(s) < 60000:
        return (Date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat()
    return None


def parse_num(cell):
    s = str(cell or "").replace(",", "").replace("₹", "").strip()
    s = re.sub(r"(?i)\s*(cr|dr)\.?$", "", s)
    if s in ("", "-"):
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def parse_signed(cell):
    s = str(cell or "").replace(",", "").strip()
    return -parse_num(s) if s.startswith("-") else parse_num(s)


def find_columns(row):
    """Map a header row to column roles. Returns None if it is not a header."""
    cols = {}
    for i, cell in enumerate(row):
        c = " ".join(str(cell or "").lower().split())
        if not c:
            continue
        if "balance" in c:
            cols.setdefault("balance", i)
        elif re.search(r"narration|particular|description|remark|details", c):
            cols.setdefault("narr", i)
        elif re.search(r"ref|chq|cheque", c):
            cols.setdefault("ref", i)
        elif re.search(r"debit|withdraw|\bdr\b", c):
            cols.setdefault("debit", i)
        elif re.search(r"credit|deposit|\bcr\b", c):
            cols.setdefault("credit", i)
        elif "date" in c and not c.startswith("value"):
            cols.setdefault("date", i)
    if "date" in cols and "narr" in cols and ("debit" in cols or "credit" in cols):
        return cols
    return None


def rows_to_txns(rows):
    cols, out = None, []
    for row in rows:
        if cols is None:
            cols = find_columns(row)
            continue
        if find_columns(row):
            continue

        def get(key):
            i = cols.get(key)
            return row[i] if i is not None and i < len(row) else ""

        date = parse_date(get("date"), serial=True)
        narr = re.sub(r"\s*/\s*", "/", " ".join(str(get("narr") or "").split()))
        if not date:
            if out and narr and not str(get("date") or "").strip():
                out[-1]["narration"] += " " + narr
            continue
        debit, credit = parse_num(get("debit")), parse_num(get("credit"))
        if debit == 0 and credit == 0:
            continue
        out.append(dict(date=date, narration=narr,
                        ref=" ".join(str(get("ref") or "").split()),
                        debit=debit, credit=credit,
                        balance=parse_signed(get("balance"))))
    if cols is None:
        raise ValueError(
            "Could not find the column headings (Date, Narration, Debit, Credit). "
            "Check that this is a bank statement.")
    if len(out) > 1 and out[0]["date"] > out[-1]["date"]:
        out.reverse()
    return out


def csv_rows(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    counts = {d: text[:5000].count(d) for d in (",", "\t", ";", "|")}
    delim = max(counts, key=counts.get)
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    except csv.Error:
        raise ValueError("Could not read this file. Use the CSV, Excel or PDF file "
                         "downloaded from the bank or broker website.")


def _cell(v):
    """Excel cell value as text; whole numbers without a trailing .0."""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return "" if v is None else str(v)


LOCKED_EXCEL = ValueError(
    "This Excel file is password-protected. Open it in Excel, remove the password "
    "(File, Info, Protect Workbook), save it, and import it again.")


def xlsx_rows(data):
    """All sheets of an .xlsx file as rows of text, using only the standard library."""
    m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Could not open this Excel file.")
    names = z.namelist()
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(m + "si"):
            shared.append("".join(t.text or "" for t in si.iter(m + "t")))
    sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[-1]))
    rows = []
    for sheet in sheets:
        for row in ET.fromstring(z.read(sheet)).iter(m + "row"):
            cells = {}
            for i, c in enumerate(row.iter(m + "c")):
                letters = re.match(r"[A-Z]+", c.get("r", ""))
                col = i
                if letters:
                    col = 0
                    for ch in letters.group():
                        col = col * 26 + ord(ch) - 64
                    col -= 1
                kind, v = c.get("t"), c.find(m + "v")
                if kind == "s" and v is not None:
                    val = shared[int(v.text)]
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(m + "t"))
                else:
                    val = v.text if v is not None and v.text else ""
                    if kind not in ("str", "e", "b") and val:
                        try:
                            val = _cell(float(val))
                        except ValueError:
                            pass
                cells[col] = val
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        rows.append([])
    return rows


def xls_rows(data):
    """Old-style .xls files, via xlrd (installed on the droplet by the updater)."""
    try:
        import xlrd
    except ImportError:
        raise ValueError("Old .xls files need one more package, which installs itself "
                         "within 5 minutes. Try again shortly, or save it as .xlsx.")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as e:
        if "encrypt" in str(e).lower() or "password" in str(e).lower():
            raise LOCKED_EXCEL
        raise ValueError("Could not open this Excel file. Save it as .xlsx and try again.")
    rows = []
    for sh in book.sheets():
        rows += [[_cell(v) for v in sh.row_values(r)] for r in range(sh.nrows)] + [[]]
    return rows


def pdf_rows(data, password):
    """Table rows from a PDF, plus each text line split into label and numbers."""
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF import needs pdfplumber. On the droplet run: "
                         "pip3 install --break-system-packages pdfplumber")
    rows, lines = [], []
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or None)
    except Exception:
        raise ValueError("Could not open the PDF. If it is locked, enter its password.")
    no_text = ValueError(
        "No readable text in this PDF. It is probably a scan or was made with "
        "Print to PDF. Download the file from the website again instead.")
    with pdf:
        if not (pdf.pages[0].extract_text() or "").strip():
            raise no_text
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
            for line in (page.extract_text() or "").splitlines():
                nums = re.findall(r"\(?-?[\d,]+\.?\d*\)?(?=\s|$)", line)
                label = re.sub(r"\(?-?[\d,]+\.?\d*\)?(?=\s|$)", " ", line)
                lines.append([" ".join(label.split())] + nums)
            page.flush_cache()
    return rows, lines


def read_rows(name, data, password):
    """Any supported file (CSV, Excel, PDF) as rows of cells, plus PDF text lines."""
    if len(data) > MAX_BYTES:
        raise ValueError("File is larger than 15 MB. Real statements are far smaller; "
                         "this is probably a scanned or printed copy.")
    if data[:4] == b"%PDF":
        return pdf_rows(data, password)
    if data[:2] == b"PK":
        return xlsx_rows(data), []
    if data[:4] == b"\xd0\xcf\x11\xe0":
        if b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d\x00P\x00a\x00c\x00k\x00a\x00g\x00e" in data:
            raise LOCKED_EXCEL
        return xls_rows(data), []
    if data.lstrip()[:1] == b"<":
        raise ValueError("This file is a web page saved as Excel. Open it in Excel and "
                         "save it as .xlsx or CSV, then import that.")
    return csv_rows(data), []


def looks_like_pnl(rows):
    text = " ".join(" ".join(str(c or "") for c in r) for r in rows[:400]).lower()
    return sum(w in text for w in ("realised", "realized", "p&l", "short term",
                                   "long term", "intraday", "turnover", "f&o")) >= 2


STMT_DATE = r"\d{1,2}[/\-. ](?:\d{1,2}|[A-Za-z]{3})[/\-. ]\d{2,4}"
STMT_AMOUNT = r"-?[\d,]+\.\d{2}(?:\s*(?:Cr|Dr|CR|DR)\b)?"
STMT_SKIP = r"opening balance|closing balance|statement summary|page \d|brought forward|carried forward"


def lines_to_txns(text):
    """Read statement lines like "01/04/24 UPI-NAME-... 01/04/24 500.00 12,345.67":
    a date first, the amount and balance last. Money in or out comes from how the
    balance moves, so it works without the Withdrawal and Deposit columns."""
    out, opening, follow = [], None, 0
    for line in text:
        line = " ".join(line.split())
        o = re.search(rf"opening balance\s*:?\s*(?:rs\.?|inr)?\s*({STMT_AMOUNT})", line, re.I)
        if o and opening is None:
            opening = parse_signed(o.group(1))
        m = re.match(rf"({STMT_DATE})\s+(.*)$", line)
        found = list(re.finditer(STMT_AMOUNT, line))
        nums = [f.group() for f in found]
        if m and len(nums) >= 2 and parse_date(m.group(1)) and not re.search(STMT_SKIP, line, re.I):
            body = line[m.end(1):found[-2].start()].strip()
            body = re.sub(rf"\s+{STMT_DATE}$", "", body)  # the value date
            ref = ""
            r = re.search(r"\s(\d{8,})$", body)
            if r:
                ref, body = r.group(1), body[:r.start()]
            bal = parse_signed(nums[-1])
            if re.search(r"dr\.?$", nums[-1], re.I):
                bal = -abs(bal)
            out.append(dict(date=parse_date(m.group(1)), narration=body.strip(), ref=ref,
                            amount=parse_num(nums[-2]), balance=bal))
            follow = 3
        elif re.search(r"bank limited|page no|closing balance includes|contents of this statement|"
                       r"registered office|gstin|joint holders|nomination|account branch|"
                       r"statement of account|generated|this is a computer", line, re.I):
            follow = 0  # the page footer or the next page's header: nothing more to join
        elif follow and out and not nums and not m and line and len(line) < 100 and not re.search(
                r"date|narration|particulars|withdraw|deposit|balance|statement|account|:", line, re.I):
            out[-1]["narration"] += " " + line  # a narration running onto the next line
            follow -= 1
    near = lambda a, b: abs(a - b) < 0.02
    # One entry is trusted only when the opening balance backs it up.
    if len(out) < 2 and not (out and opening is not None
                             and near(abs(out[0]["balance"] - opening), out[0]["amount"])):
        return []
    fwd = sum(near(abs(out[i]["balance"] - out[i - 1]["balance"]), out[i]["amount"])
              for i in range(1, len(out)))
    back = sum(near(abs(out[i - 1]["balance"] - out[i]["balance"]), out[i - 1]["amount"])
               for i in range(1, len(out)))
    if back > fwd:  # newest first
        out.reverse()
    txns = []
    for i, t in enumerate(out):
        if i:
            credit = t["balance"] > out[i - 1]["balance"]
        elif opening is not None and near(abs(t["balance"] - opening), t["amount"]):
            credit = t["balance"] > opening
        else:  # the first entry with no opening balance: go by its wording
            credit = bool(re.search(r"\b(cr|credit|deposit|by)\b", t["narration"], re.I))
        txns.append(dict(date=t["date"], narration=t["narration"], ref=t["ref"],
                         debit=0.0 if credit else t["amount"], credit=t["amount"] if credit else 0.0,
                         balance=t["balance"]))
    return txns


CARD_AMOUNT = r"(\+\s*)?(?:C|₹|Rs\.?|INR)?\s*([\d,]+\.\d{2})\s*(Cr|CR|Dr|DR)?\b"


def card_lines_to_txns(text):
    """Credit card statement lines: a date (and maybe a time) first and one amount
    last, with no running balance. Credits are marked "Cr" or "+"."""
    out = []
    for line in text:
        line = " ".join(line.replace("|", " ").split())
        m = re.match(rf"({STMT_DATE})(?:\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?\s+(.*)$", line)
        if not m or not parse_date(m.group(1)) or re.search(STMT_SKIP, line, re.I):
            continue
        amounts = list(re.finditer(CARD_AMOUNT, m.group(2)))
        if not amounts:
            continue
        a = amounts[-1]
        desc = m.group(2)[:a.start()]
        desc = re.sub(r"(\s+\+?\s*\d{1,6})+\s*$", "", desc)  # reward points
        desc = re.sub(r"\s+(C|₹|Rs\.?|INR|\+)\s*$", "", desc).strip()
        if not desc:
            continue
        amt = parse_num(a.group(2))
        credit = bool(a.group(1)) or (a.group(3) or "").lower() == "cr" or bool(
            re.search(r"payment received|reversal|refund|cashback", desc, re.I))
        out.append(dict(date=parse_date(m.group(1)), narration=desc, ref="",
                        debit=0.0 if credit else amt, credit=amt if credit else 0.0, balance=0.0))
    out.sort(key=lambda t: t["date"])
    return out


def parse_statement(name, data, password, kind="Bank"):
    if kind == "Credit card" and data[:4] == b"%PDF":
        txns = card_lines_to_txns(pdf_text_lines(data, password))
        if txns:
            return txns
    rows, lines = read_rows(name, data, password)
    try:
        txns = rows_to_txns(rows or lines)
    except ValueError:
        if looks_like_pnl(rows + lines):
            raise ValueError("This looks like a broker P&L report, not a bank statement. "
                             "Open the Trading tab and use Import P&L report on that account.")
        txns = None
        if data[:4] != b"%PDF":
            raise
    if not txns and data[:4] == b"%PDF":  # tables not found: read the text lines instead
        text = pdf_text_lines(data, password)
        txns = lines_to_txns(text)
        if not txns and re.search(r"credit card", " ".join(text[:60]), re.I):
            txns = card_lines_to_txns(text)
    if not txns:
        raise ValueError("No entries could be read from this file. Download the statement from "
                         "net banking as Excel or CSV (Delimited) and import that, or send the "
                         "file so its layout can be added.")
    return txns


# ---------- broker P&L reports ----------

# (field, pattern) in the order they are tried; the first match decides the field.
PNL_PATTERNS = [
    ("turnover", r"turnover"),
    ("dividends", r"dividend"),
    ("charges", r"charges|brokerage|\bstt\b|stamp duty|\bgst\b"),
    ("fno", r"f\s*&\s*o|\bfno\b|future|option|derivative|non[\s-]*specul"),
    ("intraday", r"intraday|specul"),
    ("ltcg", r"long[\s-]*term|\bltcg\b"),
    ("stcg", r"short[\s-]*term|\bstcg\b"),
]
PNL_HEADER = r"p\s*&\s*l|pnl|profit|realis|realiz|net\s+(gain|amount)|gain"


def num_cell(v):
    """A cell that is only a number, e.g. "1,234.50", "-12", "(1,000)" or "₹ 500 Cr"."""
    s = str(v if v is not None else "").replace("₹", "").replace(",", "").strip()
    s = re.sub(r"(?i)^rs\.?\s*|\s*(cr|dr)\.?$", "", s).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    s = s[1:].strip() if s.startswith("+") else s
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return -float(s) if neg else float(s)


def detect_fy(rows):
    """Financial year the report covers, from "FY 2025-26", "2025-2026" or its dates."""
    text = " ".join(" ".join(str(c or "") for c in r) for r in rows[:300])
    votes = {}
    for a, b in re.findall(r"(20\d\d)\s*[-/–]\s*((?:20)?\d\d)\b", text):
        if int(b[-2:]) == (int(a) + 1) % 100:
            votes[int(a)] = votes.get(int(a), 0) + 1
    if votes:
        return max(votes, key=votes.get)
    for d in re.findall(r"\b\d{1,2}[-/ ](?:\d{1,2}|[A-Za-z]{3})[-/ ]\d{2,4}\b|\b20\d\d-\d\d-\d\d\b", text):
        got = parse_date(d)
        if got:
            return fy_of(got)
    return None


GENERIC_PNL = r"p\s*&\s*l|\bpnl\b|profit|gain"


def pnl_pairs(rows):
    """(label, value, P&L column value) pairs from rows of cells, in three layouts:
    a label and its number in one row; labels in one row with numbers in the row
    below; and label, number, label, number across one row."""
    header, pcol = None, None
    for raw in rows:
        cells = []
        for c in raw:  # split "Realised P&L : 1,234" into label and number
            c = " ".join(str(c if c is not None else "").split())
            m = re.fullmatch(r"(.*[A-Za-z].*?)\s*[:=]\s*([(+\-]?[\d,₹ .]+\)?)", c)
            cells += [m.group(1), m.group(2)] if m and num_cell(m.group(2)) is not None else [c]
        if not any(cells):
            header = pcol = None
            continue
        nums = {i: num_cell(c) for i, c in enumerate(cells) if num_cell(c) is not None}
        texts = {i: c for i, c in enumerate(cells) if c and i not in nums}
        if any(parse_date(c) for c in texts.values()):
            continue  # a single trade, not a total
        if not nums:
            if len(texts) >= 2:  # a header row
                header = texts
                pcol = next((i for i, c in texts.items() if re.search(PNL_HEADER, c.lower())), pcol)
            continue
        filled = [i for i, c in enumerate(cells) if c]
        pairs = [(texts[i], nums[j]) for i, j in zip(filled, filled[1:]) if i in texts and j in nums]
        if len(texts) >= 2 and len(pairs) >= 2:  # label, number, label, number
            for label, v in pairs:
                yield label, v
        elif not texts and header:  # numbers under a row of labels
            for i, v in nums.items():
                if i in header:
                    yield header[i], v
        elif texts:
            label = " ".join(texts[i] for i in sorted(texts))
            yield label, (nums[pcol] if pcol in nums else nums[max(nums)])


def parse_pnl(name, data, password):
    """Pull yearly totals out of a broker P&L report. Nothing is saved here."""
    rows, lines = read_rows(name, data, password)
    text = " ".join(" ".join(str(c or "") for c in r) for r in rows + lines).lower()
    fno_hits = len(re.findall(r"f\s*&\s*o|\bfno\b|futures?|options?|derivative", text))
    intra_hits = len(re.findall(r"intraday", text))
    segment = "fno" if fno_hits and fno_hits >= intra_hits else "intraday" if intra_hits else "stcg"
    found = []
    for pair in pnl_pairs(rows + [[]] + lines):
        label, value = pair[0], pair[1]
        low = label.lower()
        if len(label) > 80:
            continue
        field = next((f for f, pat in PNL_PATTERNS if re.search(pat, low)), None)
        generic = not field and re.search(GENERIC_PNL, low)
        if generic:  # "Net P&L" with no segment named: use the segment the report is about
            field = segment
        if not field:
            continue
        if field == "turnover" and segment != "fno" and not re.search(PNL_PATTERNS[3][1], low):
            continue  # only F&O turnover matters for the audit limit
        rank = (3 if re.search(r"\bnet\b", low) else 2 if re.search(r"realis|realiz", low)
                else 1 if re.search(r"total|overall", low) else 0)
        found.append(dict(field=field, label=label, value=value, rank=rank, generic=bool(generic)))
    figures, used = {}, []
    for field, _ in TRADE_FIELDS:
        cands = [f for f in found if f["field"] == field]
        if not cands:
            continue
        if field in ("charges", "turnover"):
            pick = max(cands, key=lambda f: abs(f["value"]))  # the total is the largest
        else:  # a named segment over a generic P&L line, then net, realised, total
            pick = max(cands, key=lambda f: (not f["generic"], f["rank"]))
        figures[field] = round(pick["value"], 2)
        used.append(pick)
    if "charges" in figures and any(re.search(r"\bnet\b", f["label"].lower())
                                    for f in used if f["field"] in PNL_KEYS):
        figures.pop("charges")  # a net P&L already has the charges taken off
        used = [f for f in used if f["field"] != "charges"]
    if not figures:
        raise ValueError("Could not find P&L totals in this file. Enter the figures by hand "
                         "with Enter figures, or send this report so its layout can be added.")
    labels = dict(TRADE_FIELDS)
    return dict(figures=figures, fy=detect_fy(rows + lines),
                found=[dict(field=labels[f["field"]], value=f["value"],
                            label=f["label"] + (" (whole report)" if f["generic"] else ""))
                       for f in used])


def apply_rules(con):
    rules = con.execute(
        "SELECT * FROM rules ORDER BY LENGTH(pattern) DESC, id").fetchall()
    people = [(head, [m for r in con.execute(f"SELECT match FROM {table}")
                      for m in spellings(r["match"])])
              for table, head in PEOPLE.values()]
    loans = [(r["match"].lower(), r["type"], r["emi"] or 0) for r in con.execute(
        "SELECT match, type, emi FROM loans WHERE LENGTH(match) >= 3 ORDER BY LENGTH(match) DESC")]
    policies = [(r["match"].lower(), policy_category(r["type"], r["purpose"])) for r in con.execute(
        "SELECT match, type, purpose FROM policies WHERE LENGTH(match) >= 3"
        " ORDER BY LENGTH(match) DESC")]
    pay = patient_rule(con)
    small = small_rule(con)
    n = 0
    acols = [c[1] for c in con.execute("PRAGMA table_info(accounts)")]
    kind = "a.kind" if "kind" in acols else "NULL"
    owner = "a.owner" if "owner" in acols else "'Self'"
    for t in con.execute(
            f"SELECT t.id, t.narration, t.payee, t.debit, t.credit, {kind} kind, {owner} owner FROM txns t"
            " LEFT JOIN accounts a ON a.id=t.account_id WHERE t.category=''").fetchall():
        low = t["narration"].lower()
        hits = [(typ, emi) for m, typ, emi in loans if m in low]
        loan = next((typ for typ, emi in hits if emi and abs(emi - (t["debit"] or 0)) < 1),
                    hits[0][0] if hits else None) or (
            t["debit"] > 0 and next((c for m, c in policies if m in low), None))
        if loan:
            con.execute("UPDATE txns SET category=? WHERE id=?", (loan, t["id"]))
            n += 1
            continue
        head = t["debit"] > 0 and next(
            (h for h, ms in people
             if any(m.lower() in low or m.upper() == t["payee"] for m in ms)), None)
        if head:
            con.execute("UPDATE txns SET category=? WHERE id=?", (head, t["id"]))
            n += 1
            continue
        for r in rules:
            if r["field"] == "payee":
                if r["pattern"] != t["payee"]:
                    continue
            elif r["pattern"].lower() not in low:
                continue
            if r["dir"] == "in" and t["debit"] > 0:
                continue
            if r["dir"] == "out" and t["debit"] == 0:
                continue
            con.execute("UPDATE txns SET category=?, clinic=? WHERE id=?",
                        (r["category"], r["clinic"] or "", t["id"]))
            n += 1
            break
        else:
            if pay and is_patient_payment(t, low, pay):
                con.execute("UPDATE txns SET category=? WHERE id=?", ("Patient receipts", t["id"]))
                n += 1
            elif small and t["kind"] in (None, "Bank", "Credit card") and \
                    0 < (t["debit"] or 0) < small and not (t["credit"] or 0):
                con.execute("UPDATE txns SET category='Personal' WHERE id=?", (t["id"],))
                n += 1
    return n


def small_rule(con):
    """Payments below this amount are personal expenses (0 when the rule is off)."""
    if setting(con, "small_rule", "1") != "1":
        return 0
    try:
        return float(setting(con, "small_max", "2000") or 0)
    except ValueError:
        return 2000.0


# Money in that is not from a patient, whatever the payer's name.
NOT_PATIENT = (r"\binterest\b|\bint\.?\s*p(ai)?d\b|\bint\s+cr|refund|revers|\brev\b|cashback|dividend|"
               r"\breturn|cash\s*dep|by\s+cash|\bcdm\b|\bself\b|\bloan\b|disburs|\bemi\b|"
               r"\bmutual\s+fund|redemption|\bsalary\b|\bchit\b")
PATIENT_DEFAULT_EXCLUDE = "HANDRAL, RAVICHANDRA K, SUPRIYA H, SUPRIYA ENT, KIRAN HAN, VIDYA HAN, HARISH HAN"


def patient_rule(con):
    """Settings of the "payments from anyone else are patient receipts" rule, or None when off."""
    if setting(con, "patient_rule", "1") != "1":
        return None
    try:
        limit = float(setting(con, "patient_max", "50000") or 0)
    except ValueError:
        limit = 50000.0
    names = [x.strip().lower() for x in re.split(r"[,\n]", setting(con, "patient_exclude",
             PATIENT_DEFAULT_EXCLUDE)) if x.strip()]
    own = {d[-4:] for r in con.execute("SELECT name FROM accounts")
           for d in re.findall(r"\d{4,}", r["name"])}
    own_re = re.compile(r"(?:x|\*|\d){2,}(?:%s)\b" % "|".join(sorted(own))) if own else None
    return dict(limit=limit, names=names, own=own_re)


def is_patient_payment(t, low, rule):
    if t["owner"] == "Daughter":  # her account's money is not the practice's income
        return False
    if t["kind"] not in (None, "Bank") or not (t["credit"] or 0) > 0 or (t["debit"] or 0) > 0:
        return False
    if rule["limit"] and t["credit"] > rule["limit"] + 0.005:
        return False
    if any(n in low for n in rule["names"]) or re.search(NOT_PATIENT, low) or (
            rule["own"] and rule["own"].search(low)):
        return False
    return True


def import_statement(account_id, name, data, password):
    con = db()
    r = con.execute("SELECT kind FROM accounts WHERE id=?", (account_id,)).fetchone()
    con.close()
    return import_txns(account_id, parse_statement(name, data, password, r["kind"] if r else "Bank"))


def import_txns(account_id, txns):
    """Add entries to an account, skipping ones already there. An entry counts as
    already there when the account has one with the same date, amounts and balance,
    so overlapping monthly and yearly statements, or the same statement as PDF and
    Excel, are not added twice."""
    with LOCK:
        con = db()
        seq = con.execute("SELECT COALESCE(MAX(seq),0) FROM txns").fetchone()[0]
        have = {}
        if txns:
            for r in con.execute("SELECT date, debit, credit, balance FROM txns WHERE account_id=?"
                                 " AND date BETWEEN ? AND ?", (account_id, min(t["date"] for t in txns),
                                                              max(t["date"] for t in txns))):
                k = (r[0], round(r[1] or 0, 2), round(r[2] or 0, 2), round(r[3] or 0, 2))
                have[k] = have.get(k, 0) + 1
        seen, added = {}, 0
        for t in txns:
            k = (t["date"], round(t["debit"], 2), round(t["credit"], 2), round(t["balance"] or 0, 2))
            if have.get(k):
                have[k] -= 1
                continue
            key = "|".join(str(x) for x in (account_id, t["date"], t["narration"],
                                           t["debit"], t["credit"], abs(t["balance"])))
            seen[key] = seen.get(key, 0) + 1
            h = hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()
            seq += 1
            cur = con.execute(
                "INSERT OR IGNORE INTO txns(account_id,date,narration,ref,debit,"
                "credit,balance,seq,hash,payee) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (account_id, t["date"], t["narration"], t["ref"], t["debit"],
                 t["credit"], t["balance"], seq, h, payee_of(t["narration"])))
            added += cur.rowcount
        auto = apply_rules(con)
        con.commit()
        con.close()
    return dict(found=len(txns), added=added, duplicates=len(txns) - added,
                auto=auto, first=txns[0]["date"] if txns else "",
                last=txns[-1]["date"] if txns else "")


# ---------- statements from Gmail ----------
#
# HDFC sends three kinds of statement email:
#   - "HDFC Bank Combined Email Statement for <month>": a PDF of every account of one
#     customer attached (password: Customer ID). These never expire.
#   - "Email Account Statement of your HDFC Bank Account ***1234 ...": only a link to
#     the SmartStatement site, which keeps each statement for about three months.
#   - "... Credit Card Statement - <month>": the card statement PDF attached
#     (password: first 4 letters of the name in capitals + date of birth as DDMM).
# The app reads the mailbox over IMAP with a Gmail App Password, opens each statement
# with the passwords saved in the app, and imports it into the matching account.

MAIL_QUERY = ('from:hdfcbank after:{since} (subject:"Account Statement" OR '
              'subject:"Combined Email Statement" OR subject:"Credit Card Statement") '
              '-subject:"Year End"')
SMART_HOSTS = ("smartstatements.hdfc.bank.in", "smartstatements.hdfcbank.com")
SMART_LINK = (r"https://smartstatements\.hdfc(?:\.bank\.in|bank\.com)/HDFCRestFulService/"
              r"GetStatement\.jsp\?jobkey=[^\"'\s<>]+")
BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ACCT_LINE = r"\b(?:A/?C|Account)\s*(?:No|Number)\b\.?\s*:?\s*([X\*\d][X\*\d\s-]{6,24})"
MAIL = {"running": False, "message": ""}
MAIL_LOCK = threading.Lock()


def setting(con, key, default=""):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def digits_list(text):
    return [d[-4:] for d in re.findall(r"\d{2,}", text or "")]


def mail_settings(con):
    return dict(
        user=setting(con, "mail_user"), password=setting(con, "mail_pass"),
        pdf_passwords=[p.strip() for p in setting(con, "pdf_passwords").splitlines() if p.strip()],
        accounts=digits_list(setting(con, "mail_accounts", "6324 7177 9867")),
        daughter=digits_list(setting(con, "mail_daughter", "9867")),
        since=setting(con, "mail_since", "2023-04-01"))


def smart_encrypt(text):
    """The SmartStatement page scrambles the password this way before sending it."""
    key = "toUpperCase"
    prev = random.randint(1, 255)
    out = [f"{prev:02X}"]
    for i, c in enumerate(text):
        prev = ((ord(c) + prev) % 255) ^ ord(key[i % len(key)])
        out.append(f"{prev:02X}")
    return "".join(out)


def aes_ecb_decrypt(key, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    raw = d.update(data) + d.finalize()
    if raw and 1 <= raw[-1] <= 16 and raw.endswith(bytes([raw[-1]]) * raw[-1]):
        raw = raw[:-raw[-1]]
    return raw


class _HdfcOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname not in SMART_HOSTS:
            raise ValueError("The statement link sent us to another website; stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(opener, url, data=None, headers=None):
    if urllib.parse.urlparse(url).hostname not in SMART_HOSTS:
        raise ValueError("Not an HDFC SmartStatement address.")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": BROWSER, **(headers or {})})
    with opener.open(req, timeout=90) as r:
        return r.read()


def _input_value(page, name):
    tag = re.search(rf'<input[^>]*\bid=["\']{name}["\'][^>]*>', page, re.I)
    v = tag and re.search(r'\bvalue=["\']([^"\']*)["\']', tag.group(), re.I)
    return htmllib.unescape(v.group(1)) if v else ""


class LinkExpired(ValueError):
    """The statement link no longer works; trying again will not help."""


EXPIRED = ("HDFC no longer has this statement (its links work for about 3 months). "
           "Get this period again from NetBanking (Email Statement) and it is imported by itself.")


def smart_statement(link, passwords):
    """Open a SmartStatement link. Returns ("pdf", bytes) or ("html", text)."""
    link = htmllib.unescape(link)
    u = urllib.parse.urlparse(link)
    jobkey = urllib.parse.parse_qs(u.query).get("jobkey", [""])[0]
    netloc = "smartstatements.hdfc.bank.in" if u.hostname == "smartstatements.hdfcbank.com" else u.netloc
    base = f"{u.scheme}://{netloc}/HDFCRestFulService/"  # the old hdfcbank.com site is gone
    page_url = base + "GetStatement.jsp?jobkey=" + urllib.parse.quote(jobkey)
    if not passwords:
        raise ValueError("Add the statement passwords first.")
    for pw in passwords:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _HdfcOnly())
        try:
            page = _get(opener, page_url).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):  # HDFC answers 401 once a statement is withdrawn
                raise LinkExpired(EXPIRED)
            raise
        except urllib.error.URLError as e:
            if "hostname" in str(e.reason) or "Name or service" in str(e.reason):
                raise LinkExpired(EXPIRED)
            raise
        ke, seq = _input_value(page, "ke"), _input_value(page, "seqence")
        if not ke:
            raise LinkExpired(EXPIRED)
        try:
            token = _get(opener, base + "CRSGetToken?jobkey=" + urllib.parse.quote(jobkey)).decode().strip()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):
                raise LinkExpired(EXPIRED)
            raise
        body = urllib.parse.urlencode(dict(ke=ke, seqence=seq, pwd=smart_encrypt(token + pw))).encode()
        try:
            resp = _get(opener, base + "webresources/app/htmlformat", body, {
                "Content-Type": "application/x-www-form-urlencoded", "Referer": page_url}).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                continue  # this password was refused; try the next one
            raise
        m = re.search(r'Data\("([A-Za-z0-9+/=]+)"\s*,\s*"([^"]+)"\)', resp)
        if not m:
            continue  # wrong password; try the next one
        cipher = base64.b64decode(m.group(2).replace("\\n", "").replace("\n", ""))
        text = aes_ecb_decrypt(base64.b64decode(m.group(1)), cipher).decode("utf-8", "replace")
        b = re.search(r'(?is)id=["\']P_PRINT_BUTTON["\'][^>]*formaction=["\']([^"\']+)["\']', text) or \
            re.search(r'(?is)formaction=["\']([^"\']+)["\'][^>]*id=["\']P_PRINT_BUTTON["\']', text)
        if b:
            action = htmllib.unescape(b.group(1))
            for root in (page_url, base + "webresources/app/htmlformat"):
                try:
                    pdf = _get(opener, urllib.parse.urljoin(root, action), b"",
                               {"Content-Type": "text/plain", "Referer": page_url})
                except Exception:
                    continue
                if pdf[:4] == b"%PDF":
                    return "pdf", pdf
        return "html", text
    raise ValueError("None of the saved passwords opened this statement.")


def html_rows(text):
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", text):
        cells = [" ".join(htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", c)).split())
                 for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        if any(cells):
            rows.append(cells)
    return rows


def pdf_password(data, passwords):
    """The saved password that opens this PDF ("" if it is not locked)."""
    import pdfplumber
    for pw in [""] + list(passwords):
        try:
            with pdfplumber.open(io.BytesIO(data), password=pw or None) as pdf:
                pdf.pages[0].extract_text()
            return pw
        except Exception:
            continue
    raise ValueError("None of the saved passwords opened this PDF.")


def statement_sections(lines):
    """Split a combined statement into {last 4 digits of account: lines}."""
    out, cur = {}, None
    for ln in lines:
        s = " ".join(ln.split())
        m = re.search(ACCT_LINE, s, re.I)
        if m and not re.match(STMT_DATE, s):
            d = re.sub(r"\D", "", m.group(1))
            if len(d) >= 4:
                cur = d[-4:]
        out.setdefault(cur, []).append(s)
    return out


def mail_account(con, last4, kind, cfg, label=""):
    """Account id for these last digits; adds the account the first time."""
    pat = re.compile(rf"(?<!\d){re.escape(last4)}(?!\d)")
    rows = con.execute("SELECT id, name, kind FROM accounts").fetchall()
    for r in rows:
        if pat.search(r["name"]) and (r["kind"] == "Credit card") == (kind == "Credit card"):
            return r["id"]
    if kind == "Credit card" and len(last4) < 4:  # only the last 2 digits are known
        cards = [r for r in rows if r["kind"] == "Credit card"
                 and re.search(rf"{last4}(?!\d)", r["name"])]
        if len(cards) == 1:
            return cards[0]["id"]
    name = f"HDFC {label + ' ' if label else ''}{'card ' if kind == 'Credit card' else ''}{last4}"
    owner = "Daughter" if last4 in cfg["daughter"] else "Self"
    with LOCK:
        con.execute("INSERT OR IGNORE INTO accounts(name,owner,kind) VALUES(?,?,?)",
                          (name, owner, kind))
        con.commit()
    return con.execute("SELECT id FROM accounts WHERE name=?", (name,)).fetchone()[0]


def mail_import(con, last4, kind, txns, cfg, label=""):
    txns = [t for t in txns if t["date"] >= cfg["since"]]
    if not txns:
        return dict(found=0, added=0)
    return import_txns(mail_account(con, last4, kind, cfg, label), txns)


def _decode(v):
    try:
        return str(email.header.make_header(email.header.decode_header(v or "")))
    except Exception:
        return v or ""


def handle_statement(con, msg, subject, cfg):
    """Import one statement email. Returns (account label, found, added, detail, file, filename)."""
    pdfs, body = [], ""
    for part in msg.walk():
        name = _decode(part.get_filename() or "")
        data = part.get_payload(decode=True) if not part.is_multipart() else None
        if not data:
            continue
        if data[:4] == b"%PDF" or name.lower().endswith(".pdf"):
            pdfs.append((name or "statement.pdf", data))
        elif part.get_content_type() in ("text/html", "text/plain"):
            body += data.decode(part.get_content_charset() or "utf-8", "replace")
    pws = cfg["pdf_passwords"]

    if re.search(r"credit card", subject, re.I):
        if not pdfs:
            raise ValueError("No statement PDF attached.")
        name, data = pdfs[0]
        pw = pdf_password(data, pws)
        text = " ".join(pdf_text_lines(data, pw)[:80])
        c = re.search(r"Card\s*No\.?\s*:?\s*([\dX\* ]{12,23}\d)", text, re.I)
        last = re.sub(r"\D", "", c.group(1))[-4:] if c else ""
        if len(last) < 2:
            f = re.search(r"\d{4}X+(\d{2,4})", name)
            last = f.group(1) if f else "card"
        res = mail_import(con, last, "Credit card", parse_statement(name, data, pw, "Credit card"), cfg)
        return f"Card {last}", res["found"], res["added"], "", data, name

    one = re.search(r"\*{2,}\s*(\d{4})", subject)
    if one:  # a single account
        last4 = one.group(1)
        if last4 not in cfg["accounts"]:
            return f"A/c {last4}", 0, 0, "skipped: not in your list of accounts to import", None, ""
        if pdfs:
            name, data = pdfs[0]
        else:
            link = re.search(SMART_LINK, body)
            if not link:
                raise ValueError("No statement link or PDF in this email.")
            kind, data = smart_statement(link.group(), pws)
            if kind == "html":
                res = mail_import(con, last4, "Bank", rows_to_txns(html_rows(data)), cfg)
                return f"A/c {last4}", res["found"], res["added"], "", None, ""
            name = f"hdfc-{last4}.pdf"
        pw = pdf_password(data, pws)
        res = mail_import(con, last4, "Bank", parse_statement(name, data, pw), cfg)
        return f"A/c {last4}", res["found"], res["added"], "", data, name

    # Combined statement: one PDF holding several accounts.
    if not pdfs:
        link = re.search(SMART_LINK, body)
        if not link:
            raise ValueError("No statement PDF or link in this email.")
        kind, data = smart_statement(link.group(), pws)
        if kind == "html":
            raise ValueError("Combined statement came back as a web page only.")
        pdfs = [("combined.pdf", data)]
    name, data = pdfs[0]
    pw = pdf_password(data, pws)
    sections = statement_sections(pdf_text_lines(data, pw))
    labels, found, added, notes = [], 0, 0, []
    for last4, lines in sections.items():
        if last4 is None:
            continue
        if last4 not in cfg["accounts"]:
            notes.append(f"{last4} skipped")
            continue
        txns = lines_to_txns(lines)
        if not txns:
            notes.append(f"{last4}: no entries")
            continue
        res = mail_import(con, last4, "Bank", txns, cfg)
        labels.append(f"A/c {last4}")
        found += res["found"]
        added += res["added"]
    if not labels and not any("skipped" in n for n in notes):
        raise ValueError("Could not find the accounts in this combined statement. "
                         + "; ".join(notes))
    return ", ".join(labels) or "-", found, added, "; ".join(notes), data, name


def gmail_folder(M):
    """The All Mail folder (its name depends on the Gmail language)."""
    typ, folders = M.list()
    for f in folders or []:
        line = f.decode("utf-8", "replace") if isinstance(f, bytes) else str(f)
        if "\\All" in line:
            return '"' + re.findall(r'"([^"]+)"\s*$', line)[0] + '"' if '"' in line else line.split()[-1]
    return "INBOX"


def fetch_statements():
    con = db()
    cfg = mail_settings(con)
    if not cfg["user"] or not cfg["password"]:
        con.close()
        raise ValueError("Enter your Gmail address and App Password first.")
    since = cfg["since"].replace("-", "/")
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=90)
    done = errors = 0
    try:
        try:
            M.login(cfg["user"], cfg["password"].replace(" ", ""))
        except imaplib.IMAP4.error:
            raise ValueError("Gmail did not accept the address and App Password. Make a new "
                             "App Password at myaccount.google.com/apppasswords and save it here.")
        M.select(gmail_folder(M), readonly=True)
        q = MAIL_QUERY.format(since=since)
        typ, data = M.uid("SEARCH", "X-GM-RAW", '"' + q.replace("\\", "\\\\").replace('"', '\\"') + '"')
        uids = (data[0] or b"").split()
        for i, uid in enumerate(uids):
            MAIL["message"] = f"Checking email {i + 1} of {len(uids)}"
            typ, meta = M.uid("FETCH", uid, "(X-GM-MSGID)")
            mid = re.search(rb"X-GM-MSGID (\d+)", meta[0] if meta and meta[0] else b"")
            msgid = mid.group(1).decode() if mid else uid.decode()
            old = con.execute("SELECT status FROM mail_log WHERE msgid=?", (msgid,)).fetchone()
            if old and old[0] != "error":
                continue
            typ, raw = M.uid("FETCH", uid, "(BODY.PEEK[])")
            msg = email.message_from_bytes(raw[0][1])
            subject = " ".join(_decode(msg.get("Subject")).split())
            try:
                sent = email.utils.parsedate_to_datetime(msg.get("Date")).strftime("%Y-%m-%d")
            except Exception:
                sent = ""
            file, fname = None, ""
            try:
                acct, found, added, detail, file, fname = handle_statement(con, msg, subject, cfg)
                status = "skipped" if detail.startswith("skipped") else "ok"
                file = None  # keep files only for statements that failed
                done += 1
            except LinkExpired as e:
                acct, found, added, detail, status = "", 0, 0, str(e), "expired"
            except Exception as e:
                acct, found, added, detail, status = "", 0, 0, str(e) or e.__class__.__name__, "error"
                errors += 1
                for part in msg.walk():
                    d = None if part.is_multipart() else part.get_payload(decode=True)
                    if d and d[:4] == b"%PDF":
                        file, fname = d, _decode(part.get_filename() or "statement.pdf")
                        break
            with LOCK:
                con.execute(
                    "INSERT INTO mail_log(msgid,date,subject,account,status,found,added,detail,checked,file,filename)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(msgid) DO UPDATE SET account=excluded.account,"
                    " status=excluded.status, found=excluded.found, added=excluded.added,"
                    " detail=excluded.detail, checked=excluded.checked, file=excluded.file,"
                    " filename=excluded.filename",
                    (msgid, sent, subject, acct, status, found, added, detail,
                     datetime.now().strftime("%Y-%m-%d %H:%M"), file, fname))
                con.commit()
    finally:
        try:
            M.logout()
        except Exception:
            pass
        con.close()
    return done, errors


def start_mail_fetch():
    if not MAIL_LOCK.acquire(blocking=False):
        return False

    def work():
        MAIL["running"] = True
        MAIL["message"] = "Connecting to Gmail"
        try:
            done, errors = fetch_statements()
            MAIL["message"] = (f"Last checked {datetime.now().strftime('%d %b %H:%M')}: "
                               f"{done} new statement emails read"
                               + (f", {errors} need attention" if errors else ""))
        except Exception as e:
            MAIL["message"] = f"Last check failed: {e}"
        finally:
            MAIL["running"] = False
            MAIL_LOCK.release()

    threading.Thread(target=work, daemon=True).start()
    return True


def mail_scheduler():
    time.sleep(120)
    while True:
        try:
            con = db()
            ready = setting(con, "mail_user") and setting(con, "mail_pass")
            con.close()
            if ready:
                start_mail_fetch()
        except Exception:
            pass
        time.sleep(6 * 3600)


def api_mail():
    con = db()
    cfg = mail_settings(con)
    log = [dict(r) for r in con.execute(
        "SELECT id, date, subject, account, status, found, added, detail, checked,"
        " file IS NOT NULL has_file FROM mail_log ORDER BY date DESC, id DESC")]
    con.close()
    counts = {}
    for r in log:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return dict(user=cfg["user"], has_password=bool(cfg["password"]),
                pdf_passwords=len(cfg["pdf_passwords"]),
                accounts=" ".join(cfg["accounts"]), daughter=" ".join(cfg["daughter"]),
                since=cfg["since"], running=MAIL["running"], message=MAIL["message"],
                counts=counts, log=log)


def save_mail_settings(d):
    con = db()
    with LOCK:
        for key, field in (("mail_user", "user"), ("mail_accounts", "accounts"),
                           ("mail_daughter", "daughter"), ("mail_since", "since")):
            if field in d:
                v = str(d[field]).strip()
                if field == "since":
                    v = check_date(v) or "2023-04-01"
                con.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, v))
        # Passwords are only replaced when something new is typed in.
        if str(d.get("password") or "").strip():
            con.execute("INSERT OR REPLACE INTO settings VALUES('mail_pass',?)",
                        (str(d["password"]).strip(),))
        if str(d.get("pdf_passwords") or "").strip():
            con.execute("INSERT OR REPLACE INTO settings VALUES('pdf_passwords',?)",
                        (str(d["pdf_passwords"]).strip(),))
        con.commit()
    con.close()
    return api_mail()


# ---------- queries ----------

def fy_range(fy):
    y = int(fy)
    return f"{y}-04-01", f"{y + 1}-03-31"


def txn_filter(q):
    where, args = ["1=1"], []
    g = lambda k: (q.get(k) or [""])[0]
    if g("account"):
        where.append("account_id=?"); args.append(int(g("account")))
    if g("owner"):
        where.append("account_id IN (SELECT id FROM accounts WHERE owner=?)")
        args.append(g("owner"))
    if g("fy"):
        a, b = fy_range(g("fy"))
        where.append("date BETWEEN ? AND ?"); args += [a, b]
    if g("month"):
        where.append("substr(date,1,7)=?"); args.append(g("month"))
    if g("cat") == "__none__":
        where.append("category=''")
    elif g("cat"):
        where.append("category=?"); args.append(g("cat"))
    if g("clinic"):
        where.append("clinic=?"); args.append(g("clinic"))
    if g("dir") == "in":
        where.append("credit>0")
    elif g("dir") == "out":
        where.append("debit>0")
    if g("q"):
        where.append("narration LIKE ?"); args.append(f"%{g('q')}%")
    if g("payee"):
        where.append("payee=?"); args.append(g("payee"))
    return " AND ".join(where), args


def api_state():
    con = db()
    accounts = [dict(r) for r in con.execute(
        "SELECT a.id, a.name, a.owner, a.kind, COUNT(t.id) n, MIN(t.date) first, MAX(t.date) last,"
        " SUM(CASE WHEN t.category='' THEN 1 ELSE 0 END) open,"
        " CASE WHEN a.kind='Cash' THEN COALESCE(SUM(t.credit)-SUM(t.debit),0)"
        " ELSE (SELECT balance FROM txns x WHERE x.account_id=a.id"
        "  ORDER BY date DESC, seq DESC LIMIT 1) END balance"
        " FROM accounts a LEFT JOIN txns t ON t.account_id=a.id"
        " GROUP BY a.id ORDER BY a.name")]
    rules = [dict(r) for r in con.execute("SELECT * FROM rules ORDER BY id")]
    years = [r[0] for r in con.execute(
        "SELECT DISTINCT CASE WHEN substr(date,6,2)>='04' THEN CAST(substr(date,1,4) AS INT)"
        " ELSE CAST(substr(date,1,4) AS INT)-1 END FROM txns ORDER BY 1 DESC")]
    con.close()
    return dict(accounts=accounts, rules=rules, cats=CATS, groups=GROUPS, clinics=CLINICS,
                tally=[[c, TALLY[c][0], TALLY[c][1]] for c, _ in CATS], tally_groups=TALLY_GROUPS,
                owners=OWNERS, kinds=KINDS,
                years=[int(y) for y in years])


def api_txns(q):
    where, args = txn_filter(q)
    offset = int((q.get("offset") or ["0"])[0])
    con = db()
    tot = con.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(debit),0) d, COALESCE(SUM(credit),0) c"
        f" FROM txns WHERE {where}", args).fetchone()
    rows = [dict(r) for r in con.execute(
        f"SELECT t.*, a.name account FROM txns t JOIN accounts a ON a.id=t.account_id"
        f" WHERE {where} ORDER BY date DESC, seq DESC LIMIT 200 OFFSET ?",
        args + [offset])]
    con.close()
    return dict(count=tot["n"], debit=tot["d"], credit=tot["c"], rows=rows)


def api_report(q):
    where, args = txn_filter(q)
    con = db()
    rows = [dict(r) for r in con.execute(
        f"SELECT substr(date,1,7) m, category, clinic, SUM(debit) d, SUM(credit) c,"
        f" COUNT(*) n FROM txns WHERE {where} GROUP BY 1,2,3", args)]
    con.close()
    return dict(rows=rows)


def sort_group(con, d, payee, direction):
    """Put every unsorted entry of one payee under a head; optionally remember it."""
    sign = "credit>0" if direction == "in" else "debit>0"
    cur = con.execute(
        f"UPDATE txns SET category=?, clinic=? WHERE category='' AND payee=? AND {sign}",
        (d["category"], d.get("clinic", ""), payee))
    table = {head: t for t, head in PEOPLE.values()}.get(d["category"])
    if payee != "OTHER" and table and direction == "out":
        con.execute(f"INSERT OR IGNORE INTO {table}(name,match) VALUES(?,?)",
                    (payee.title(), payee))
    elif d.get("rule") and payee != "OTHER":
        con.execute("DELETE FROM rules WHERE field='payee' AND pattern=? AND dir=?",
                    (payee, direction))
        con.execute("INSERT INTO rules(pattern,dir,category,clinic,field)"
                    " VALUES(?,?,?,?,'payee')",
                    (payee, direction, d["category"], d.get("clinic", "")))
    return cur.rowcount


def api_people(kind, q):
    """Each consultant or staff member with what was paid to them, by month."""
    table, head = PEOPLE[kind]
    fy, owner = qget(q, "fy"), qget(q, "owner")
    con = db()
    out = []
    for c in con.execute(f"SELECT * FROM {table} ORDER BY name").fetchall():
        who, who_args = person_where(c["match"])
        where = f"category=? AND debit>0 AND {who}"
        args = [head] + who_args
        if fy:
            where += " AND date BETWEEN ? AND ?"
            args += list(fy_range(fy))
        if owner:
            where += " AND account_id IN (SELECT id FROM accounts WHERE owner=?)"
            args.append(owner)
        months = {r[0]: r[1] for r in con.execute(
            f"SELECT substr(date,1,7), SUM(debit) FROM txns WHERE {where} GROUP BY 1", args)}
        r = con.execute(f"SELECT COUNT(*) n, COALESCE(SUM(debit),0) total"
                        f" FROM txns WHERE {where}", args).fetchone()
        out.append(dict(c, n=r["n"], total=r["total"], months=months))
    con.close()
    return dict(rows=out)


def api_consultants(q):
    return api_people("consultant", q)


def cash_account(con):
    """The account that holds cash collections, created on first use."""
    r = con.execute("SELECT id FROM accounts WHERE kind='Cash' ORDER BY id LIMIT 1").fetchone()
    if r:
        return r[0]
    name, i = "Cash collections", 1
    while con.execute("SELECT 1 FROM accounts WHERE name=?", (name,)).fetchone():
        i += 1
        name = f"Cash collections {i}"
    return con.execute("INSERT INTO accounts(name,owner,kind) VALUES(?,'Self','Cash')",
                       (name,)).lastrowid


def api_patient_rule():
    con = db()
    r = dict(on=setting(con, "patient_rule", "1") == "1", max=setting(con, "patient_max", "50000"),
             exclude=setting(con, "patient_exclude", PATIENT_DEFAULT_EXCLUDE))
    r["count"], r["total"] = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(credit),0) FROM txns WHERE category='Patient receipts'").fetchone()
    r["small_on"] = setting(con, "small_rule", "1") == "1"
    r["small_max"] = setting(con, "small_max", "2000")
    r["small_count"], r["small_total"] = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(debit),0) FROM txns WHERE category='Personal'").fetchone()
    con.close()
    return r


def api_coverage():
    """For each bank and card account: which periods have entries and where
    statements are missing. In a bank account each day's balances must follow
    from the day before; a jump means entries (a statement) are missing there."""
    con = db()
    since = setting(con, "mail_since", "2023-04-01")
    today = Date.today().isoformat()
    out = []
    for a in con.execute("SELECT id, name, owner, kind FROM accounts WHERE kind IN ('Bank','Credit card')"
                         " ORDER BY name").fetchall():
        days = {}
        for r in con.execute("SELECT date, debit, credit, balance FROM txns WHERE account_id=?"
                             " ORDER BY date, seq", (a["id"],)):
            d = days.setdefault(r["date"], dict(net=0.0, bal=[], n=0))
            d["net"] += (r["credit"] or 0) - (r["debit"] or 0)
            d["bal"].append(round(r["balance"] or 0, 2))
            d["n"] += 1
        months = {}
        for k, d in days.items():
            months[k[:7]] = months.get(k[:7], 0) + d["n"]
        missing, quiet_note = [], []
        dates = sorted(days)
        if not dates:
            missing.append(dict(start=since, end=today, note="No statement imported yet"))
        else:
            first, last = dates[0], dates[-1]
            if first > (Date.fromisoformat(since) + timedelta(days=10)).isoformat():
                missing.append(dict(start=since, end=first, note="Before the first entry"))
            if last < (Date.today() - timedelta(days=40)).isoformat():
                missing.append(dict(start=last, end=today, note="After the last entry"))
            for p, n in zip(dates, dates[1:]):
                quiet = (Date.fromisoformat(n) - Date.fromisoformat(p)).days
                if a["kind"] == "Bank":
                    want = [round(x + days[n]["net"], 2) for x in days[p]["bal"]]
                    if not any(abs(w - y) < 1 for w in want for y in days[n]["bal"]):
                        gap = min((abs(w - y) for w in want for y in days[n]["bal"]), default=0)
                        missing.append(dict(start=p, end=n, amount=round(gap, 2),
                                            note="Balance does not follow on"))
                    elif quiet > 92:
                        quiet_note.append(dict(start=p, end=n))
                elif quiet > 45:  # a card has no running balance; a month without entries is a missed statement
                    missing.append(dict(start=p, end=n, note="No card entries in between"))
        out.append(dict(id=a["id"], name=a["name"], owner=a["owner"], kind=a["kind"],
                        first=dates[0] if dates else "", last=dates[-1] if dates else "",
                        entries=sum(months.values()), months=months, missing=missing,
                        quiet=quiet_note))
    con.close()
    return dict(since=since, today=today, accounts=out)


def api_cash(q):
    """Cash entries for the year with totals by month and treatment."""
    fy = qget(q, "fy")
    con = db()
    where, args = "a.kind='Cash'", []
    if fy:
        where += " AND date BETWEEN ? AND ?"
        args += list(fy_range(fy))
    rows = [dict(r) for r in con.execute(
        f"SELECT t.id, date, narration, ref treatment, payee, credit, debit, clinic, note,"
        f" category FROM txns t JOIN accounts a ON a.id=t.account_id WHERE {where}"
        f" ORDER BY date DESC, seq DESC", args)]
    patients = sorted({r[0].title() for r in con.execute(
        "SELECT DISTINCT payee FROM txns t JOIN accounts a ON a.id=t.account_id"
        " WHERE a.kind='Cash' AND payee NOT IN ('', 'CASH')")})
    treats = [r[0] for r in con.execute(
        "SELECT DISTINCT ref FROM txns t JOIN accounts a ON a.id=t.account_id"
        " WHERE a.kind='Cash' AND ref!=''")]
    acct = con.execute("SELECT id FROM accounts WHERE kind='Cash' ORDER BY id LIMIT 1").fetchone()
    con.close()
    by_treat, by_month = {}, {}
    for r in rows:
        amt = r["credit"] - r["debit"]
        by_treat[r["treatment"] or "Not given"] = by_treat.get(r["treatment"] or "Not given", 0) + amt
        by_month[r["date"][:7]] = by_month.get(r["date"][:7], 0) + amt
    return dict(rows=rows[:300], count=len(rows), total=sum(by_month.values()),
                by_treatment=sorted(by_treat.items(), key=lambda x: -x[1]),
                by_month=by_month, patients=patients, account=acct[0] if acct else None,
                treatments=TREATMENTS + [t for t in treats if t not in TREATMENTS])


def api_groups(q):
    """Uncategorised entries grouped by payee, biggest first."""
    where, args = txn_filter(q)
    con = db()
    rows = [dict(r) for r in con.execute(
        f"SELECT payee, CASE WHEN credit>0 THEN 'in' ELSE 'out' END dir, COUNT(*) n,"
        f" SUM(debit+credit) total, MIN(narration) sample FROM txns"
        f" WHERE category='' AND {where} GROUP BY 1,2"
        f" ORDER BY total DESC LIMIT 400", args)]
    tot = con.execute(
        f"SELECT COUNT(*) n, SUM(CASE WHEN category='' THEN 1 ELSE 0 END) open"
        f" FROM txns WHERE {where}", args).fetchone()
    con.close()
    return dict(rows=rows, total=tot["n"], open=tot["open"] or 0)


# ---------- trading accounts and investments ----------

# Yearly figures per trading account, copied from the broker's Tax P&L report.
TRADE_FIELDS = [
    ("intraday", "Intraday equity (speculative)"),
    ("fno", "F&O (non-speculative business)"),
    ("stcg", "Short-term capital gains"),
    ("ltcg", "Long-term capital gains"),
    ("dividends", "Dividends"),
    ("charges", "Charges not already deducted"),
    ("turnover", "F&O turnover (for audit limit)"),
]
PNL_KEYS = ("intraday", "fno", "stcg", "ltcg", "dividends")

# (type, months held before a sale counts as long term). 0: interest-type, no
# capital gain. -1: always taxed at slab rate (debt funds bought after Mar 2023).
ASSET_TYPES = [
    ("Mutual fund (equity)", 12), ("Mutual fund (debt)", -1),
    ("Shares (delivery)", 12), ("Gold", 24), ("Plot or land", 24),
    ("House or flat", 24), ("Fixed deposit", 0), ("PPF, EPF or NPS", 0),
    ("Insurance (LIC or ULIP)", 0), ("Other", 24),
]
TERM_OF = dict(ASSET_TYPES)


def trade_net(r):
    return sum(r[k] or 0 for k in PNL_KEYS) - (r["charges"] or 0)


def add_months(date, n):
    y, m, d = (int(x) for x in date.split("-"))
    m += n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d, calendar.monthrange(y, m)[1]):02d}"


def asset_term(r):
    """Short or long term for a sold investment, by type and holding period."""
    if not r.get("sold"):
        return ""
    rule = TERM_OF.get(r["type"], 24)
    if rule == 0:
        return "Interest or maturity, not capital gain"
    if rule == -1:
        return "Short term (slab rate)"
    if not r.get("bought"):
        return "Add purchase date"
    return "Long term" if r["sold"] > add_months(r["bought"], rule) else "Short term"


def check_date(v):
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        return datetime.strptime(v, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Date {v} is not valid.")


def to_num(v):
    s = str(v if v is not None else "").replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{s} is not a number.")


def api_trading(q):
    """Each trading account's P&L for the year (all years summed if none)."""
    fy, owner = qget(q, "fy"), qget(q, "owner")
    con = db()
    out = []
    accts = con.execute(
        "SELECT * FROM accounts WHERE kind='Trading'" + (" AND owner=?" if owner else "")
        + " ORDER BY name", [owner] if owner else []).fetchall()
    sums = ", ".join(f"COALESCE(SUM({k}),0) {k}" for k, _ in TRADE_FIELDS)
    for a in accts:
        args = [a["id"]] + ([int(fy)] if fy else [])
        p = dict(con.execute(
            f"SELECT {sums}, COALESCE(GROUP_CONCAT(NULLIF(note,''),'; '),'') note"
            f" FROM trading_pnl WHERE account_id=?" + (" AND fy=?" if fy else ""),
            args).fetchone())
        words = a["name"].split()
        match = (a["bank_match"] or "").strip() or (words[0] if words else "")
        added = withdrawn = 0
        if len(match) >= 3:
            w = ("category='Trading transfer' AND narration LIKE ? AND account_id IN"
                 " (SELECT id FROM accounts WHERE kind!='Trading')")
            targs = [f"%{match}%"]
            if fy:
                w += " AND date BETWEEN ? AND ?"
                targs += list(fy_range(fy))
            r = con.execute(f"SELECT COALESCE(SUM(debit),0) d, COALESCE(SUM(credit),0) c"
                            f" FROM txns WHERE {w}", targs).fetchone()
            added, withdrawn = r["d"], r["c"]
        out.append(dict(p, id=a["id"], name=a["name"], owner=a["owner"], match=match,
                        added=added, withdrawn=withdrawn, net=trade_net(p)))
    con.close()
    return dict(fields=TRADE_FIELDS, rows=out)


def api_assets(q):
    owner, fy = qget(q, "owner"), qget(q, "fy")
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM assets" + (" WHERE owner=?" if owner else "")
        + " ORDER BY type, name", [owner] if owner else [])]
    bank = 0
    if fy:
        w, args = "category='Investments' AND date BETWEEN ? AND ?", list(fy_range(fy))
        if owner:
            w += " AND account_id IN (SELECT id FROM accounts WHERE owner=?)"
            args.append(owner)
        bank = con.execute(f"SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0)"
                           f" FROM txns WHERE {w}", args).fetchone()[0]
    con.close()
    for r in rows:
        r["term"] = asset_term(r)
    return dict(types=[t for t, _ in ASSET_TYPES], rows=rows, bank=bank)


def sold_in(rows, fy):
    if not fy:
        return [r for r in rows if r["sold"]]
    a, b = fy_range(fy)
    return [r for r in rows if r["sold"] and a <= r["sold"] <= b]


def trading_sheet_rows(q):
    d = api_trading(q)
    out = [("h", ["Account", "Owner"] + [l for _, l in TRADE_FIELDS]
            + ["Net P&L", "Added from bank", "Withdrawn to bank", "Note"])]
    tot = [0.0] * (len(TRADE_FIELDS) + 3)
    for r in d["rows"]:
        nums = [r[k] or 0 for k, _ in TRADE_FIELDS] + [r["net"], r["added"], r["withdrawn"]]
        tot = [a + b for a, b in zip(tot, nums)]
        out.append(("", [r["name"], r["owner"]] + [float(round(v, 2)) for v in nums]
                    + [r["note"]]))
    out.append(("b", ["Total", ""] + [float(round(v, 2)) for v in tot]))
    return out


def asset_sheet_rows(q):
    out = [("h", ["Name", "Type", "Owner", "Bought on", "Cost", "Current value",
                  "Value as on", "Unrealised gain", "Sold on", "Sale value",
                  "Realised gain", "Term", "Note"])]
    for r in api_assets(q)["rows"]:
        cost = r["cost"] or 0
        if r["sold"]:
            mid = ["", "", "", r["sold"], float(r["sale"] or 0),
                   float(round((r["sale"] or 0) - cost, 2))]
        else:
            mid = [float(r["value"] or 0), r["valued"],
                   float(round((r["value"] or 0) - cost, 2)), "", "", ""]
        out.append(("", [r["name"], r["type"], r["owner"], r["bought"], float(cost)]
                    + mid + [r["term"], r["note"]]))
    return out


# ---------- previous income tax returns ----------

# (key, label, JSON keys in priority order, PDF line pattern)
ITR_FIELDS = [
    ("receipts", "Gross receipts from profession",
     ["GrsReceipt", "GrossReceipt", "GrsTrnOverOrGrsRcpt", "TotRevenueFrmOperations"],
     r"gross receipts?"),
    ("presumptive", "Presumptive income u/s 44ADA",
     ["TotPersumptiveInc44ADA", "TotPresumptiveInc44ADA"], r"44\s*ada"),
    ("business", "Income from business or profession",
     ["TotProfBusGain", "IncomeFromBusinessProf", "ProfBusGain"],
     r"business or profession|profits and gains"),
    ("salary", "Salary income", ["IncomeFromSal", "Salaries", "NetSalary", "TotalSalary"],
     r"^\s*(income (from|under the head) )?salar(y|ies)"),
    ("house", "House property income",
     ["TotalIncomeOfHP", "IncomeFromHP", "TotalIncomeChargeableUnHP"], r"house property"),
    ("stcg", "Short-term capital gains", ["TotalShortTerm", "TotalSTCG"], r"short[\s-]*term capital"),
    ("ltcg", "Long-term capital gains", ["TotalLongTerm", "TotalLTCG"], r"long[\s-]*term capital"),
    ("capital", "Capital gains (total)", ["TotalCapGains", "CapGain"], r"^\s*(income (from|under the head) )?capital gains?"),
    ("other", "Income from other sources", ["IncomeOthSrc", "TotIncFromOS", "IncFromOS"],
     r"other sources"),
    ("gross_total", "Gross total income", ["GrossTotIncome", "GrossTotalIncome"],
     r"gross total income"),
    ("d80c", "Deduction 80C", ["Section80C"], r"\b80\s*c\b"),
    ("d80d", "Deduction 80D", ["Section80D"], r"\b80\s*d\b"),
    ("d80g", "Deduction 80G", ["Section80G"], r"\b80\s*g\b"),
    ("d80tta", "Deduction 80TTA/80TTB", ["Section80TTA", "Section80TTB"], r"\b80\s*tt[ab]\b"),
    ("deductions", "Total deductions (Chapter VI-A)",
     ["TotalChapVIADeductions", "DeductionsUnderScheduleVIA", "TotalDeductions"],
     r"chapter[\s-]*vi[\s-]*a|total deductions?"),
    ("total_income", "Total income", ["TotalIncome", "TotIncome"],
     r"(?<!gross )\btotal income\b"),
    ("tax", "Tax payable", ["NetTaxLiability", "TotalTaxPayable", "GrossTaxLiability"],
     r"(net |total )?tax (payable|liability)"),
    ("tax_paid", "Taxes paid", ["TotalTaxesPaid"], r"taxes paid|total tax paid"),
    ("refund", "Refund due", ["RefundDue"], r"\brefund"),
]
ITR_LABEL = {k: label for k, label, _, _ in ITR_FIELDS}
ITR_USE_MAX = ("receipts",)


def mask_pan(pan):
    pan = str(pan or "").strip().upper()
    return pan[:3] + "XXXX" + pan[-3:] if len(pan) == 10 else ""


def flatten(obj, path=""):
    """Every scalar in a JSON tree as (path, value)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{path}/{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def _as_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return num_cell(v)


def parse_itr_json(obj):
    flat = list(flatten(obj))
    by_key = {}
    for path, v in flat:
        key = path.split("/")[-1].split("[")[0]
        by_key.setdefault(key, []).append((path, v))
    figures = {}
    for field, _, keys, _ in ITR_FIELDS:
        for k in keys:
            cands = [(p, _as_num(v)) for p, v in by_key.get(k, []) if _as_num(v) is not None]
            if not cands:
                continue
            if field in ITR_USE_MAX:
                figures[field] = max(v for _, v in cands)
            else:  # the shallowest; the allowed figure rather than the one claimed
                cands.sort(key=lambda c: ("Usr" in c[0], c[0].count("/")))
                figures[field] = cands[0][1]
            break
    ay = next((str(v) for p, v in flat if p.endswith("AssessmentYear") and str(v)[:4].isdigit()), "")
    form = next((m.group(1) for p, _ in flat for m in [re.search(r"\bITR([1-7])\b", p)] if m), "")
    first = next((v for p, v in flat if p.endswith("AssesseeName/FirstName")), "") or ""
    last = next((v for p, v in flat if p.endswith("AssesseeName/SurNameOrOrgName")), "") or ""
    pan = next((v for p, v in flat if p.endswith("/PAN") or p == "PAN"), "")
    banks = []
    for p, v in flat:
        if p.endswith("BankAccountNo"):
            base = p[: -len("BankAccountNo")]
            get = lambda k: next((x for q, x in flat if q == base + k), "")
            banks.append(dict(bank=str(get("BankName") or ""), ifsc=str(get("IFSCCode") or ""),
                              last4=str(v)[-4:]))
    scheme = "44ADA" if figures.get("presumptive") else ""
    return dict(ay=int(ay[:4]) if ay else None, form=f"ITR-{form}" if form else "",
                name=" ".join(f"{first} {last}".split()).title(), pan=mask_pan(pan),
                figures=figures, banks=banks, scheme=scheme), \
        [[p, v] for p, v in flat if v not in (None, "", 0, "0")]


def parse_itr_pdf(data, password):
    rows, lines = pdf_rows(data, password)
    text = "\n".join(" ".join(str(c or "") for c in r) for r in rows + lines)
    ay = re.search(r"(?i)assessment\s+year\s*[:\-]?\s*(20\d\d)\s*-\s*\d\d", text)
    form = re.search(r"\bITR\s*-?\s*([1-7])\b", text)
    pan = re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", text)
    figures, flat = {}, []
    for raw in lines + rows:
        cells = [str(c or "") for c in raw]
        nums = [num_cell(c) for c in cells if num_cell(c) is not None]
        label = " ".join(c for c in cells if num_cell(c) is None).strip()
        if not nums or not label:
            continue
        flat.append([label, nums[-1]])
        low = label.lower()
        for field, _, _, pat in ITR_FIELDS:
            if field not in figures and re.search(pat, low):
                figures[field] = nums[-1]
                break
    if not figures:
        raise ValueError("No figures found in this PDF. Upload the JSON of the return "
                         "(income-tax portal, View Filed Returns, Download JSON) instead.")
    return dict(ay=int(ay.group(1)) if ay else None,
                form=f"ITR-{form.group(1)}" if form else "", name="",
                pan=mask_pan(pan.group()) if pan else "", figures=figures, banks=[],
                scheme="44ADA" if figures.get("presumptive") else ""), flat


def parse_itr(name, data, password):
    if len(data) > MAX_BYTES:
        raise ValueError("File is larger than 15 MB; this is not an ITR file.")
    if data[:2] == b"PK":  # a zip holding the JSON
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            inner = next(n for n in z.namelist() if n.lower().endswith(".json"))
            data = z.read(inner)
        except (zipfile.BadZipFile, StopIteration):
            raise ValueError("This zip file has no ITR JSON inside.")
    if data[:4] == b"%PDF":
        return parse_itr_pdf(data, password)
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Upload the ITR JSON or PDF downloaded from the income-tax portal.")
    info, flat = parse_itr_json(obj)
    if not info["figures"]:
        raise ValueError("This JSON does not look like an income tax return.")
    return info, flat


# ---------- mutual fund statements ----------

MF_SELL = r"redempt|redeem|switch[\s-]*out|stp[\s-]*out|\bswp\b|withdrawal|\bsell|\bsale\b|\bsold\b"
MF_BUY = r"purchase|\bsip\b|systematic|switch[\s-]*in|stp[\s-]*in|reinvest|\bbuy|\bbought|allot|bonus|new fund|\bnfo\b"
MF_SKIP = r"stamp duty|\bstt\b|idcw paid|dividend paid|payout|tds|address|nominee|kyc"


def mf_kind(scheme):
    """equity, debt or other (gold, international, fund of funds) from the scheme name."""
    s = scheme.lower()
    if re.search(r"gold|silver|international|global|overseas|nasdaq|s&p|u\.?s\.? equity|"
                 r"fund of funds?|\bfof\b", s):
        return "other"
    if re.search(r"arbitrage|equity savings", s):
        return "equity"
    if re.search(r"liquid|debt|gilt|bond|money market|overnight|duration|corporate|banking "
                 r"(&|and) psu|credit risk|floater|floating|treasury|income fund|conservative "
                 r"hybrid|target maturity|\bsdl\b|g-?sec|fixed maturity|\bfmp\b", s):
        return "debt"
    return "equity"


def mf_term(kind, bought, sold):
    if not bought:
        return "Add purchase history"
    if kind == "debt" and bought >= "2023-04-01":
        return "Short term (slab rate)"
    months = 12 if kind == "equity" else (24 if sold >= "2024-07-23" else 36)
    return "Long term" if sold > add_months(bought, months) else "Short term"


def clean_scheme(s):
    s = re.split(r"\s*-?\s*ISIN\b|\(Advisor|Registrar\s*:", str(s))[0]
    s = re.sub(r"^[A-Z0-9]{2,10}-(?=[A-Za-z])", "", s.strip())
    s = s.replace("(Non-Demat)", "").replace("(Demat)", "")
    return " ".join(s.split()).strip(" -")[:120]


def pdf_text_lines(data, password):
    import pdfplumber
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or None)
    except Exception:
        raise ValueError("Could not open the PDF. If it is locked, enter its password "
                         "(for a CAMS or KFintech statement this is usually your PAN).")
    out = []
    with pdf:
        for page in pdf.pages:
            out += (page.extract_text() or "").splitlines()
            page.flush_cache()
    return out


def mf_columns(cells):
    cols = {}
    for i, c in enumerate(cells):
        l = c.lower()
        if not l:
            continue
        if re.search(r"short", l):
            cols.setdefault("st", i)
        elif re.search(r"long", l):
            if "lt" not in cols or "without" in l:  # prefer the gain without indexation
                cols["lt"] = i
        elif re.search(r"purchase|acquisition|acquired|buy", l) and "date" in l:
            cols.setdefault("bought", i)
        elif re.search(r"purchase|acquisition|cost", l) and re.search(r"amount|value|cost|price", l):
            cols.setdefault("cost", i)
        elif "date" in l and "nav" not in l:
            cols.setdefault("date", i)
        elif "folio" in l:
            cols.setdefault("folio", i)
        elif re.search(r"scheme|fund name|\bfund\b|security|isin name", l):
            cols.setdefault("scheme", i)
        elif "unit" in l and not re.search(r"balance|price|nav", l):
            cols.setdefault("units", i)
        elif re.search(r"\bnav\b|price", l):
            cols.setdefault("nav", i)
        elif re.search(r"amount|value|consideration|invested", l) and "market" not in l:
            cols.setdefault("amount", i)
        elif re.search(r"transaction|type|description|particular|order|nature", l):
            cols.setdefault("type", i)
    return cols


def parse_mf(name, data, password):
    """Mutual fund transactions or realised gains from a statement. Returns
    dict(kind, txns, gains, navs)."""
    if len(data) > MAX_BYTES:
        raise ValueError("File is larger than 15 MB.")
    if data[:4] == b"%PDF":
        rows, _ = pdf_rows(data, password)
        text = pdf_text_lines(data, password)
    else:
        rows, _ = read_rows(name, data, password)
        text = []
    txns, gains, navs = [], [], {}
    cols, scheme, folio = None, "", ""
    for raw in rows:
        cells = [" ".join(str(c if c is not None else "").split()) for c in raw]
        if not any(cells):
            continue
        got = mf_columns(cells)
        if not any(num_cell(c) is not None for c in cells) and (
                {"st", "lt"} <= got.keys() or {"date", "units"} <= got.keys()):
            cols = got
            continue
        texts = [c for c in cells if c and num_cell(c) is None and not parse_date(c, serial=True)]
        dated = [c for c in cells if parse_date(c, serial=True)]
        if not dated:  # a scheme heading between blocks
            if len(texts) == 1 and re.search(r"fund|scheme|plan|growth|idcw|isin", texts[0], re.I):
                scheme = clean_scheme(texts[0])
            m = re.search(r"folio\s*(no\.?)?\s*:?\s*([\w/ ]+)", " ".join(texts), re.I)
            if m:
                folio = m.group(2).strip()
            continue
        if not cols:
            continue
        get = lambda k: cells[cols[k]] if k in cols and cols[k] < len(cells) else ""
        num = lambda k: num_cell(get(k)) or 0
        sch = clean_scheme(get("scheme")) or scheme
        if not sch:
            continue
        if {"st", "lt"} <= cols.keys():  # a capital gains statement
            sold = parse_date(get("date"), serial=True)
            if not sold:
                continue
            gains.append(dict(scheme=sch, sold=sold, bought=parse_date(get("bought"), serial=True) or "",
                              units=abs(num("units")), sale=abs(num("amount")), cost=abs(num("cost")),
                              stcg=num("st"), ltcg=num("lt")))
            continue
        date = parse_date(get("date"), serial=True)
        units = num_cell(get("units"))
        if not date or not units:
            continue
        kind_text = get("type").lower()
        if re.search(MF_SKIP, kind_text):
            continue
        amount = abs(num("amount")) or abs(units) * abs(num("nav"))
        sell = bool(re.search(MF_SELL, kind_text)) or (units < 0 and not re.search(MF_BUY, kind_text))
        txns.append(dict(scheme=sch, folio=get("folio") or folio, date=date,
                         type="sell" if sell else "buy", units=abs(units), amount=amount))
        if num("nav"):
            navs[sch] = (date, abs(num("nav")))
    if not txns and not gains and text:  # CAS text: one transaction per line
        num_re = r"\(?-?[\d,]+\.\d+\)?"
        line_re = re.compile(rf"^(\d{{2}}-[A-Za-z]{{3}}-\d{{4}})\s+(.+?)\s+({num_re})\s+({num_re})\s+"
                             rf"({num_re})\s+({num_re})\s*$")
        for line in text:
            line = " ".join(line.split())
            m = re.search(r"folio\s*no\s*:\s*([\w/ ]+?)(\s{2,}|\s+[A-Z]{2,}\b|$)", line, re.I)
            if m:
                folio = m.group(1).strip()
            if re.search(r"\bISIN\b", line) or (re.search(r"\b(fund|scheme)\b", line, re.I)
                                               and re.search(r"growth|idcw|dividend|plan", line, re.I)
                                               and not re.match(r"\d{2}-", line)):
                scheme = clean_scheme(line)
                continue
            m = re.search(r"NAV on (\d{2}-[A-Za-z]{3}-\d{4})\s*:?\s*INR\s*([\d,]+\.\d+)", line)
            if m and scheme:
                navs[scheme] = (parse_date(m.group(1)), num_cell(m.group(2)))
            m = line_re.match(line)
            if not m or not scheme or re.search(MF_SKIP, m.group(2), re.I):
                continue
            amount, units, nav = num_cell(m.group(3)), num_cell(m.group(4)), num_cell(m.group(5))
            if not units:
                continue
            sell = bool(re.search(MF_SELL, m.group(2), re.I)) or units < 0
            txns.append(dict(scheme=scheme, folio=folio, date=parse_date(m.group(1)),
                             type="sell" if sell else "buy", units=abs(units),
                             amount=abs(amount or units * nav)))
            navs[scheme] = (parse_date(m.group(1)), abs(nav))
    if not txns and not gains:
        raise ValueError("No mutual fund transactions found. Upload the CAMS or KFintech "
                         "statement (CAS PDF, password usually your PAN), a capital gains "
                         "statement, or the transaction list from Groww, Coin or Kuvera.")
    return dict(kind="capital gains" if gains else "transactions", txns=txns, gains=gains,
                navs=navs)


def store_mf(owner, filename, parsed):
    with LOCK:
        con = db()
        uid = con.execute("INSERT INTO mf_uploads(owner,filename,uploaded,kind) VALUES(?,?,?,?)",
                          (owner, filename, datetime.now().strftime("%Y-%m-%d"),
                           parsed["kind"])).lastrowid
        seen, added = {}, 0
        for t in parsed["txns"]:
            key = "|".join(str(x) for x in (owner, t["scheme"], t["date"], t["type"],
                                           round(t["units"], 3), round(t["amount"], 2)))
            seen[key] = seen.get(key, 0) + 1
            h = hashlib.sha1(f"mf|{key}|{seen[key]}".encode()).hexdigest()
            added += con.execute(
                "INSERT OR IGNORE INTO mf_txns(upload_id,owner,folio,scheme,date,type,units,"
                "amount,hash) VALUES(?,?,?,?,?,?,?,?,?)",
                (uid, owner, t["folio"], t["scheme"], t["date"], t["type"], t["units"],
                 t["amount"], h)).rowcount
        for g in parsed["gains"]:
            key = "|".join(str(x) for x in (owner, g["scheme"], g["sold"], g["bought"],
                                           round(g["units"], 3), round(g["stcg"], 2),
                                           round(g["ltcg"], 2), round(g["sale"], 2)))
            seen[key] = seen.get(key, 0) + 1
            h = hashlib.sha1(f"mfcg|{key}|{seen[key]}".encode()).hexdigest()
            added += con.execute(
                "INSERT OR IGNORE INTO mf_cg(upload_id,owner,scheme,sold,bought,units,sale,cost,"
                "stcg,ltcg,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid, owner, g["scheme"], g["sold"], g["bought"], g["units"], g["sale"],
                 g["cost"], g["stcg"], g["ltcg"], h)).rowcount
        for sch, (d, nav) in parsed["navs"].items():
            if d and nav:
                con.execute("INSERT INTO mf_nav(scheme,date,nav) VALUES(?,?,?) ON CONFLICT(scheme)"
                            " DO UPDATE SET date=excluded.date, nav=excluded.nav"
                            " WHERE excluded.date >= mf_nav.date", (sch, d, nav))
        con.execute("UPDATE mf_uploads SET added=? WHERE id=?", (added, uid))
        con.commit()
        con.close()
    n = len(parsed["txns"]) + len(parsed["gains"])
    return dict(kind=parsed["kind"], found=n, added=added, duplicates=n - added,
                schemes=len({t["scheme"] for t in parsed["txns"] + parsed["gains"]}))


def mf_fifo(txns):
    """Match each sale against the oldest units first. Returns (open lots, sales)."""
    lots, sales = [], []
    for t in sorted(txns, key=lambda t: (t["date"], t["type"] != "buy")):
        if t["type"] == "buy":
            lots.append([t["date"], t["units"], t["amount"]])
            continue
        left = t["units"]
        while left > 1e-6 and lots:
            d, u, c = lots[0]
            take = min(u, left)
            cost = c * take / u
            sales.append(dict(bought=d, sold=t["date"], units=take, cost=cost,
                              sale=t["amount"] * take / t["units"]))
            lots[0][1] -= take
            lots[0][2] -= cost
            left -= take
            if lots[0][1] <= 1e-6:
                lots.pop(0)
        if left > 1e-3:
            sales.append(dict(bought="", sold=t["date"], units=left, cost=0,
                              sale=t["amount"] * left / t["units"]))
    return lots, sales


def api_mf(q):
    owner, fy = qget(q, "owner"), qget(q, "fy")
    w, args = ("WHERE owner=?", [owner]) if owner else ("", [])
    con = db()
    uploads = [dict(r) for r in con.execute(
        f"SELECT * FROM mf_uploads {w} ORDER BY id DESC", args)]
    txns = [dict(r) for r in con.execute(f"SELECT * FROM mf_txns {w}", args)]
    cg = [dict(r) for r in con.execute(f"SELECT * FROM mf_cg {w}", args)]
    navs = {r["scheme"]: (r["date"], r["nav"]) for r in con.execute("SELECT * FROM mf_nav")}
    con.close()
    groups = {}
    for t in txns:
        groups.setdefault((t["owner"], t["folio"], t["scheme"]), []).append(t)
    holdings, sales = [], []
    for (own, folio, sch), ts in groups.items():
        kind = mf_kind(sch)
        lots, sold = mf_fifo(ts)
        for x in sold:
            sales.append(dict(x, scheme=sch, owner=own, kind=kind,
                              term=mf_term(kind, x["bought"], x["sold"]),
                              old=bool(x["bought"]) and x["bought"] < "2018-02-01" and kind == "equity"))
        units = sum(l[1] for l in lots)
        if units > 1e-3:
            cost = sum(l[2] for l in lots)
            last = max(ts, key=lambda t: t["date"])
            nd, nav = navs.get(sch, (last["date"], last["amount"] / last["units"]))
            holdings.append(dict(scheme=sch, owner=own, folio=folio, kind=kind, units=units,
                                 cost=cost, nav=nav, nav_date=nd, value=units * nav,
                                 since=lots[0][0], old=lots[0][0] < "2018-02-01" and kind == "equity"))
    a, b = fy_range(fy) if fy else ("0000", "9999")
    cg_now = [g for g in cg if a <= g["sold"] <= b]
    rows = []
    if cg_now:  # a capital gains statement wins for the schemes it covers
        for g in cg_now:
            kind = mf_kind(g["scheme"])
            for v, term in ((g["stcg"], "Short term (slab rate)" if kind == "debt" and
                             (g["bought"] or "0") >= "2023-04-01" else "Short term"),
                            (g["ltcg"], "Long term")):
                if v:
                    rows.append(dict(scheme=g["scheme"], owner=g["owner"], kind=kind, sold=g["sold"],
                                     bought=g["bought"], units=g["units"], sale=g["sale"],
                                     cost=g["cost"], gain=v, term=term, old=False))
    covered = {g["scheme"].lower() for g in cg_now}
    own = [dict(x, gain=x["sale"] - x["cost"]) for x in sales
           if a <= x["sold"] <= b and x["scheme"].lower() not in covered]
    source = ("statement" if not own else "both") if cg_now else "computed"
    rows += own
    totals = {}
    for r in rows:
        k = ("Debt funds, slab rate" if r["term"].startswith("Short term (slab")
             else f"Purchase missing ({r['kind']})" if not r["bought"] and source != "statement"
             else f"{r['term']} ({r['kind']})")
        totals[k] = totals.get(k, 0) + r["gain"]
    holdings.sort(key=lambda h: -h["value"])
    rows.sort(key=lambda r: (r["sold"], r["scheme"]))
    return dict(uploads=uploads, holdings=holdings, gains=rows, source=source,
                totals=sorted(totals.items()))


def mf_sheet_rows(q):
    d = api_mf(q)
    out = []
    if d["gains"]:
        out.append(("h", ["Scheme", "Owner", "Bought on", "Sold on", "Units", "Sale value",
                          "Cost", "Gain", "Term"]))
        out += [("", [r["scheme"], r["owner"], r["bought"], r["sold"], float(round(r["units"], 3)),
                      float(round(r["sale"], 2)), float(round(r["cost"], 2)),
                      float(round(r["gain"], 2)), r["term"]]) for r in d["gains"]]
        out += [("b", [k, "", "", "", "", "", "", float(round(v, 2))]) for k, v in d["totals"]]
        out.append(("", []))
    if d["holdings"]:
        out.append(("h", ["Holding", "Owner", "Held since", "NAV date", "Units", "Value",
                          "Cost", "Gain", "Type"]))
        out += [("", [h["scheme"], h["owner"], h["since"], h["nav_date"], float(round(h["units"], 3)),
                      float(round(h["value"], 2)), float(round(h["cost"], 2)),
                      float(round(h["value"] - h["cost"], 2)), h["kind"]]) for h in d["holdings"]]
    return out


# ---------- loans ----------

LOAN_TYPES = ["Car loan", "Jewel loan", "Personal loan", "Home loan", "Education loan",
              "Business loan", "Other loan"]
LOAN_PURPOSES = ["Clinic", "Personal", "Home, self-occupied", "Home, let out"]


def loan_tax_note(typ, purpose):
    if purpose == "Home, self-occupied" or (typ == "Home loan" and purpose != "Home, let out"
                                            and purpose != "Clinic"):
        return ("Interest: section 24(b), up to 2 lakh a year for a self-occupied home. "
                "Principal: 80C within the 1.5 lakh limit. Old tax regime only.")
    if purpose == "Home, let out":
        return ("Interest: section 24(b) against the rent; a loss can be set off up to "
                "2 lakh a year. Principal: 80C.")
    if typ == "Education loan":
        return "Interest: section 80E, the full amount, for up to 8 years. Old tax regime only."
    if purpose == "Clinic":
        return ("Interest is a clinic expense if you keep books (not under 44ADA presumptive)."
                + (" The car can also be depreciated." if typ == "Car loan" else ""))
    return "No tax benefit for a loan used personally."


def loan_schedule(amount, rate, emi, start):
    """Month-by-month (date, interest, principal, balance) for an EMI loan."""
    bal, r, d, out = amount, rate / 1200, start, []
    if not (amount and rate and emi and start) or emi <= amount * r:
        return out
    for _ in range(480):
        d = add_months(d, 1)
        i = bal * r
        p = min(emi - i, bal)
        bal -= p
        out.append((d, i, p, bal))
        if bal <= 0.5:
            break
    return out


def api_loans(q):
    owner, fy = qget(q, "owner"), int(qget(q, "fy") or fy_of(datetime.now().strftime("%Y-%m-%d")))
    a, b = fy_range(fy)
    today = datetime.now().strftime("%Y-%m-%d")
    con = db()
    loans = [dict(r) for r in con.execute(
        "SELECT * FROM loans" + (" WHERE owner=?" if owner else "") + " ORDER BY closed!='', type, name",
        [owner] if owner else [])]
    shared = {}
    for r in con.execute("SELECT LOWER(match) m, COUNT(*) n FROM loans WHERE LENGTH(match) >= 3 GROUP BY 1"):
        shared[r["m"]] = r["n"] > 1
    out = []
    for l in loans:
        received = paid = 0
        if len(l["match"]) >= 3:
            sql = ("SELECT COALESCE(SUM(credit),0) c, COALESCE(SUM(debit),0) d FROM txns"
                   " WHERE category=? AND narration LIKE ? AND date BETWEEN ? AND ?")
            args = [l["type"], f"%{l['match']}%", a, b]
            if l["start"]:
                sql += " AND date >= ?"
                args.append(l["start"])
            if shared.get(l["match"].lower()):
                # Several loans paid through the same bank text (e.g. one app's loans): this
                # loan's entries are its EMIs and its own disbursement, or, while its EMI is
                # not known, whatever does not belong to the others.
                if l["emi"]:
                    sql += " AND (ABS(debit-?) < 1 OR ABS(credit-?) < 1)"
                    args += [l["emi"], l["amount"] or -1]
                else:
                    others = [o["emi"] for o in loans if o["id"] != l["id"] and o["emi"]
                              and o["match"].lower() == l["match"].lower()]
                    sql += "".join(" AND ABS(debit-?) >= 1" for _ in others)
                    args += others
            r = con.execute(sql, args).fetchone()
            received, paid = r["c"], r["d"]
        y = con.execute("SELECT * FROM loan_years WHERE loan_id=? AND fy=?", (l["id"], fy)).fetchone()
        sched = loan_schedule(l["amount"], l["rate"], l["emi"], l["start"])
        est_int = sum(i for d, i, _, _ in sched if a <= d <= b)
        est_prin = sum(p for d, _, p, _ in sched if a <= d <= b)
        upto = min(b, today)
        past = [x for x in sched if x[0] <= upto]
        est_out = past[-1][3] if past else (l["amount"] if sched and l["start"] <= upto else None)
        interest = y["interest"] if y and y["interest"] is not None else (est_int if sched else None)
        outstanding = y["outstanding"] if y and y["outstanding"] is not None else est_out
        out.append(dict(l, received=received, paid=paid, interest=interest,
                        interest_given=bool(y and y["interest"] is not None),
                        principal=(paid - interest) if y and y["interest"] is not None and paid
                        else (est_prin if sched else None),
                        outstanding=outstanding,
                        outstanding_given=bool(y and y["outstanding"] is not None),
                        note_tax=loan_tax_note(l["type"], l["purpose"])))
    w, args = "category IN (%s) AND date BETWEEN ? AND ?" % ",".join("?" * len(LOAN_TYPES)), LOAN_TYPES + [a, b]
    if owner:
        w += " AND account_id IN (SELECT id FROM accounts WHERE owner=?)"
        args.append(owner)
    allin = {r["category"]: (r["c"], r["d"], r["n"]) for r in con.execute(
        f"SELECT category, SUM(credit) c, SUM(debit) d, COUNT(*) n FROM txns WHERE {w} GROUP BY 1", args)}
    con.close()
    by_type = []
    for t in LOAN_TYPES:
        c, d, n = allin.get(t, (0, 0, 0))
        mine = [x for x in out if x["type"] == t]
        if n or mine:
            by_type.append(dict(type=t, received=c, paid=d, entries=n,
                                matched=sum(x["received"] + x["paid"] for x in mine)))
    return dict(rows=out, by_type=by_type, types=LOAN_TYPES, purposes=LOAN_PURPOSES)


def loan_sheet_rows(q):
    d = api_loans(q)
    if not d["rows"] and not d["by_type"]:
        return []
    out = [("h", ["Loan", "Type", "Lender", "Owner", "Used for", "Received this year",
                  "Paid this year", "Interest", "Principal", "Outstanding", "Tax note"])]
    f = lambda v: float(round(v, 2)) if v is not None else ""
    for l in d["rows"]:
        out.append(("", [l["name"], l["type"], l["lender"], l["owner"], l["purpose"],
                         f(l["received"]), f(l["paid"]),
                         f(l["interest"]), f(l["principal"]), f(l["outstanding"]), l["note_tax"]]))
    out += [("", []), ("h", ["All loan entries by type", "", "", "", "", "Received", "Paid"])]
    out += [("", [t["type"], "", "", "", "", f(t["received"]), f(t["paid"])]) for t in d["by_type"]]
    return out


# ---------- insurance ----------

POLICY_TYPES = ["Term insurance", "Life insurance (LIC, endowment)", "Health insurance",
                "Vehicle insurance", "Professional indemnity", "Clinic property or equipment",
                "Other insurance"]
POLICY_FREQ = {"Yearly": 12, "Half-yearly": 6, "Quarterly": 3, "Monthly": 1, "Single premium": 0}
INSURED = ["Self and family", "Parents"]
INSURANCE_CATS = ["Term insurance premium (80C)", "Life insurance premium (80C)",
                  "Health insurance premium (80D)", "Vehicle insurance", "Other insurance",
                  "Clinic insurance"]


def policy_category(typ, purpose):
    if typ == "Term insurance":
        return "Term insurance premium (80C)"
    if typ.startswith("Life insurance"):
        return "Life insurance premium (80C)"
    if typ == "Health insurance":
        return "Health insurance premium (80D)"
    if typ in ("Professional indemnity", "Clinic property or equipment") or purpose == "Clinic":
        return "Clinic insurance"
    return "Vehicle insurance" if typ == "Vehicle insurance" else "Other insurance"


def policy_tax_note(p):
    t = p["type"]
    if t == "Term insurance":
        return "80C, within the 1.5 lakh limit together with LIC, PPF, ELSS, school fees and home loan principal. Old tax regime only."
    if t.startswith("Life insurance"):
        return ("80C within the 1.5 lakh limit, if the premium is at most 10% of the sum assured "
                "(policies from April 2012). Old tax regime only.")
    if t == "Health insurance":
        lim = 50000 if p["senior"] else 25000
        who = "parents" if p["insured"] == "Parents" else "self, spouse and children"
        return (f"80D for {who}: up to {inr_text(lim)} a year"
                + (" (senior citizen)" if p["senior"] else "") + ". Old tax regime only.")
    if policy_category(t, p["purpose"]) == "Clinic insurance":
        return "Clinic expense if you keep books (not under 44ADA presumptive)."
    return "No tax benefit for personal use."


def next_due(due, freq, today):
    months = POLICY_FREQ.get(freq, 12)
    if not due or not months:
        return due if due and due >= today else ""
    d = due
    for _ in range(600):
        if d >= today:
            return d
        d = add_months(d, months)
    return ""


def api_insurance(q):
    owner, fy = qget(q, "owner"), int(qget(q, "fy") or fy_of(datetime.now().strftime("%Y-%m-%d")))
    a, b = fy_range(fy)
    today = datetime.now().strftime("%Y-%m-%d")
    soon = (Date.today() + timedelta(days=30)).isoformat()
    con = db()
    pols = [dict(r) for r in con.execute(
        "SELECT * FROM policies" + (" WHERE owner=?" if owner else "") + " ORDER BY closed!='', type, name",
        [owner] if owner else [])]
    rows = []
    for p in pols:
        cat = policy_category(p["type"], p["purpose"])
        paid = 0
        if len(p["match"]) >= 3:
            paid = con.execute("SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0) FROM txns"
                               " WHERE category=? AND narration LIKE ? AND date BETWEEN ? AND ?",
                               (cat, f"%{p['match']}%", a, b)).fetchone()[0]
        nd = "" if p["closed"] else next_due(p["due"], p["frequency"], today)
        rows.append(dict(p, category=cat, paid=paid, next_due=nd, due_soon=bool(nd and nd <= soon),
                         note_tax=policy_tax_note(p)))
    w, args = "category IN (%s) AND date BETWEEN ? AND ?" % ",".join("?" * len(INSURANCE_CATS)), INSURANCE_CATS + [a, b]
    if owner:
        w += " AND account_id IN (SELECT id FROM accounts WHERE owner=?)"
        args.append(owner)
    by_cat = {r["category"]: (r["d"] - r["c"], r["n"]) for r in con.execute(
        f"SELECT category, SUM(debit) d, SUM(credit) c, COUNT(*) n FROM txns WHERE {w} GROUP BY 1", args)}
    w80c = "category IN (?,?,?,?) AND date BETWEEN ? AND ?"
    a80c = ["Term insurance premium (80C)", "Life insurance premium (80C)",
            "Tax-saving investment (80C)", "School fees (80C)", a, b]
    if owner:
        w80c += " AND account_id IN (SELECT id FROM accounts WHERE owner=?)"
        a80c.append(owner)
    used80c = con.execute(f"SELECT COALESCE(SUM(debit)-SUM(credit),0) FROM txns WHERE {w80c}",
                          a80c).fetchone()[0]
    con.close()
    by_type = []
    for c in INSURANCE_CATS:
        amt, n = by_cat.get(c, (0, 0))
        linked = sum(r["paid"] for r in rows if r["category"] == c)
        if n or linked:
            by_type.append(dict(category=c, paid=amt, entries=n, loose=max(amt - linked, 0)))
    # 80D: the health premiums found, within the limits for each group
    health = [r for r in rows if r["type"] == "Health insurance" and not r["closed"]]
    d80 = []
    for grp in INSURED:
        mine = [r for r in health if r["insured"] == grp]
        if not mine:
            continue
        paid = sum(r["paid"] or (r["premium"] if POLICY_FREQ.get(r["frequency"]) == 12 else 0) for r in mine)
        limit = 50000 if any(r["senior"] for r in mine) else 25000
        d80.append(dict(group=grp, paid=paid, limit=limit, claim=min(paid, limit)))
    loose80d = by_cat.get("Health insurance premium (80D)", (0, 0))[0] - sum(r["paid"] for r in health)
    return dict(rows=rows, by_type=by_type, d80=d80, loose80d=max(loose80d, 0),
                used80c=used80c, types=POLICY_TYPES, freqs=list(POLICY_FREQ), insured=INSURED)


def insurance_sheet_rows(q):
    d = api_insurance(q)
    if not d["rows"] and not d["by_type"]:
        return []
    f = lambda v: float(round(v, 2)) if v else ""
    out = [("h", ["Policy", "Type", "Insurer", "Policy no.", "Owner", "Covers", "Cover",
                  "Premium", "Paid this year", "Next due", "Tax note"])]
    out += [("", [r["name"], r["type"], r["insurer"], r["policy_no"], r["owner"],
                  r["insured"] + (" (senior)" if r["senior"] else ""), f(r["cover"]), f(r["premium"]),
                  f(r["paid"]), r["next_due"], r["note_tax"]]) for r in d["rows"]]
    if d["d80"]:
        out += [("", []), ("h", ["80D", "", "", "", "", "Paid", "Limit", "Claimable"])]
        out += [("", [x["group"], "", "", "", "", f(x["paid"]), f(x["limit"]), f(x["claim"])]) for x in d["d80"]]
    out += [("", []), ("b", ["80C used this year (insurance, tax-saving, school fees)", "", "", "", "",
                             "", f(d["used80c"]), f(min(d["used80c"], 150000))])]
    return out


# ---------- sharing rules between computers or with a CA ----------

def export_rules():
    con = db()
    out = dict(app="Handral Books", version=1,
               rules=[dict(pattern=r["pattern"], dir=r["dir"], category=r["category"],
                           clinic=r["clinic"] or "", field=r["field"] or "narration")
                      for r in con.execute("SELECT * FROM rules ORDER BY id")],
               staff=[dict(name=r["name"], match=r["match"], role=r["role"] or "")
                      for r in con.execute("SELECT * FROM staff ORDER BY name")],
               consultants=[dict(name=r["name"], match=r["match"])
                            for r in con.execute("SELECT * FROM consultants ORDER BY name")],
               loans=[{k: r[k] for k in LOAN_FIELDS}
                      for r in con.execute("SELECT * FROM loans ORDER BY start, name")])
    con.close()
    return out


def import_rules(data):
    try:
        d = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("This is not a rules file. Use a .json file made by Download rules.")
    if not isinstance(d, dict) or not isinstance(d.get("rules", []), list):
        raise ValueError("This is not a rules file. Use a .json file made by Download rules.")
    cats = dict(CATS)
    added = dict(rules=0, staff=0, consultants=0, loans=0, skipped=0)
    with LOCK:
        con = db()
        have = {(r["pattern"], r["dir"], r["field"] or "narration")
                for r in con.execute("SELECT * FROM rules")}
        for r in d.get("rules", []):
            pat = " ".join(str(r.get("pattern") or "").split())
            field = "payee" if r.get("field") == "payee" else "narration"
            way = r.get("dir") if r.get("dir") in ("in", "out", "any") else "any"
            cat = r.get("category")
            if len(pat) < 3 or cat not in cats or (pat, way, field) in have:
                added["skipped"] += 1
                continue
            clinic = r.get("clinic") if r.get("clinic") in CLINICS else ""
            con.execute("INSERT INTO rules(pattern,dir,category,clinic,field) VALUES(?,?,?,?,?)",
                        (pat, way, cat, clinic, field))
            have.add((pat, way, field))
            added["rules"] += 1
        for kind, key in (("staff", "staff"), ("consultant", "consultants")):
            table = PEOPLE[kind][0]
            for x in d.get(key, []):
                match = ", ".join(spellings(x.get("match") or x.get("name")))
                if not match:
                    continue
                added[key] += add_person(con, table, x.get("name"), match,
                                         str(x["role"]) if kind == "staff" and x.get("role") else None)
        names = {r[0].lower() for r in con.execute("SELECT name FROM loans")}
        for x in d.get("loans", []) if isinstance(d.get("loans"), list) else []:
            name = " ".join(str(x.get("name") or "").split())
            if not name or name.lower() in names or x.get("type") not in LOAN_TYPES:
                added["skipped"] += 1
                continue
            v = {k: x.get(k) for k in LOAN_FIELDS}
            v["name"] = name
            v["owner"] = v["owner"] if v["owner"] in OWNERS else "Self"
            v["purpose"] = v["purpose"] if v["purpose"] in LOAN_PURPOSES else "Personal"
            for k in ("amount", "rate", "emi"):
                v[k] = to_num(v[k]) if str(v[k] or "").strip() else 0
            for k in ("start", "closed"):
                v[k] = check_date(v[k])
            for k in ("lender", "match", "note"):
                v[k] = str(v[k] or "").strip()
            con.execute(f"INSERT INTO loans({','.join(LOAN_FIELDS)}) VALUES({','.join('?' * len(LOAN_FIELDS))})",
                        [v[k] for k in LOAN_FIELDS])
            names.add(name.lower())
            added["loans"] += 1
        added["sorted"] = apply_rules(con)
        con.commit()
        con.close()
    return added


LOAN_FIELDS = ("name", "type", "lender", "owner", "purpose", "amount", "start", "rate", "emi",
               "match", "closed", "note")


def books_year(fy, owner):
    """This year's figures from the books, in the same shape as an ITR summary."""
    q = {"fy": [str(fy)]}
    if owner:
        q["owner"] = [owner]
    c = {r[1]: r for r in summary_rows(q)}
    inc = lambda *n: sum(c[x][3] - c[x][4] for x in n if x in c)
    out = lambda *n: sum(c[x][4] - c[x][3] for x in n if x in c)
    rec = inc("Patient receipts", "Other clinic income")
    exp = out(*[n for n, g in CATS if g == "expenses"])
    trade = api_trading(q)["rows"]
    sold = sold_in(api_assets(q)["rows"], str(fy))
    st = sum(r["stcg"] or 0 for r in trade) + sum(
        (r["sale"] or 0) - (r["cost"] or 0) for r in sold if r["term"].startswith("Short"))
    lt = sum(r["ltcg"] or 0 for r in trade) + sum(
        (r["sale"] or 0) - (r["cost"] or 0) for r in sold if r["term"] == "Long term")
    for r in api_mf(q)["gains"]:
        if r["term"] == "Long term":
            lt += r["gain"]
        else:
            st += r["gain"]
    cash = api_cash(q)["total"]
    return dict(receipts=rec, business=rec - exp, salary=inc("Salary income"),
                other=inc("Interest received", "Other income"), stcg=st, ltcg=lt,
                capital=st + lt,
                d80c=out("Life insurance premium (80C)", "Tax-saving investment (80C)",
                         "School fees (80C)"),
                d80d=out("Health insurance premium (80D)"), d80g=out("Donations (80G)"),
                tax_paid=out("Income tax and TDS paid"), cash=cash,
                trading=sum(r["net"] for r in trade))


def itr_insights(returns, fy, owner, now):
    """Plain checks comparing the latest earlier return with this year's books."""
    past = [r for r in returns if r["ay"] and r["ay"] - 1 < fy]
    if not past:
        return []
    last = max(past, key=lambda r: (r["ay"], len(r["figures"])))
    f, ay = last["figures"], f"AY {last['ay']}-{str(last['ay'] + 1)[2:]}"
    tips = []
    rec = now["receipts"]
    if f.get("presumptive"):
        base = f.get("receipts") or 0
        rate = f["presumptive"] / base if base else 0.5
        cash_share = now["cash"] / rec if rec else 0
        tips.append(
            f"{ay} was filed as {last['form'] or 'a return'} under presumptive taxation "
            f"(section 44ADA): {inr_text(f['presumptive'])} declared on receipts of "
            f"{inr_text(base)} ({rate * 100:.0f}%). This year's receipts so far are "
            f"{inr_text(rec)}; at the same rate that is {inr_text(rec * rate)}. "
            f"Cash is {cash_share * 100:.1f}% of receipts: the 44ADA limit is 75 lakh when "
            f"cash receipts are within 5%, otherwise 50 lakh.")
    elif f.get("business") is not None and f.get("receipts"):
        tips.append(f"{ay} declared {inr_text(f['business'])} from profession on receipts of "
                    f"{inr_text(f['receipts'])}. Books this year: receipts {inr_text(rec)}, "
                    f"profit {inr_text(now['business'])}.")
    for key, head in (("d80c", "80C (LIC, tax-saving investments, school fees)"),
                      ("d80d", "Health insurance premium (80D)"), ("d80g", "Donations (80G)")):
        if (f.get(key) or 0) > 0 and not now[key]:
            tips.append(f"{ay} claimed {inr_text(f[key])} under {ITR_LABEL[key].replace('Deduction ', '')}. Nothing is "
                        f"sorted under {head} this year yet; sort those payments so it is not missed.")
    for key, where in (("salary", "sort salary credits as Salary income"),
                       ("other", "sort bank and FD interest as Interest received"),
                       ("capital", "fill in the Trading and Assets tabs")):
        if (f.get(key) or 0) > 0 and not now[key]:
            tips.append(f"{ay} had {ITR_LABEL[key].lower()} of {inr_text(f[key])}; none found "
                        f"this year so far. If it applies again, {where}.")
    if last["banks"]:
        con = db()
        names = " ".join(r[0] for r in con.execute("SELECT name FROM accounts")).upper()
        con.close()
        for b in last["banks"]:
            if b["last4"] and b["last4"] not in names:
                tips.append(f"Bank account in {ay}: {b['bank'] or 'bank'} ending {b['last4']} "
                            f"({b['ifsc']}) is not in Banking. Add it as an account named with "
                            f"{b['last4']} in it and import its statement.")
    if (f.get("tax") or 0) > 10000:
        tips.append(f"Tax for {ay} was {inr_text(f['tax'])}. If this year is similar, advance tax "
                    f"is due (presumptive 44ADA filers may pay it all by 15 March; others in "
                    f"instalments by 15 Jun, 15 Sep, 15 Dec and 15 Mar). Tax paid in the books "
                    f"this year: {inr_text(now['tax_paid'])}.")
    return tips


def inr_text(v):
    """Rupees in Indian grouping, e.g. 12,34,567."""
    n = int(round(abs(v or 0)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        s = head + "," + tail
    return ("-" if (v or 0) < 0 else "") + "\u20b9" + s


def api_itr(q):
    owner = qget(q, "owner")
    fy = int(qget(q, "fy") or fy_of(datetime.now().strftime("%Y-%m-%d")))
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT id, owner, ay, form, name, pan, filename, uploaded, summary FROM itr_returns"
        + (" WHERE owner=?" if owner else "") + " ORDER BY ay DESC, id DESC",
        [owner] if owner else [])]
    con.close()
    for r in rows:
        s = json.loads(r.pop("summary") or "{}")
        r.update(figures=s.get("figures", {}), banks=s.get("banks", []), scheme=s.get("scheme", ""))
    best = {}
    for r in rows:  # per person and year, the upload with the most figures
        k = (r["owner"], r["ay"])
        if k not in best or len(r["figures"]) > len(best[k]["figures"]):
            best[k] = r
    years = sorted({r["ay"] for r in best.values() if r["ay"]}, reverse=True)[:5]
    now = books_year(fy, owner)
    cols = [dict(title=f"AY {fy + 1}-{str(fy + 2)[2:]} (books so far)", values=now)]
    for ay in years:
        vals = {}
        for r in best.values():
            if r["ay"] == ay:
                for k, v in r["figures"].items():
                    vals[k] = (vals.get(k) or 0) + v
        cols.append(dict(title=f"AY {ay}-{str(ay + 1)[2:]}", values=vals))
    keys = [k for k, *_ in ITR_FIELDS if any(c["values"].get(k) for c in cols)]
    return dict(returns=rows, columns=cols,
                compare=[[ITR_LABEL[k]] + [c["values"].get(k) for c in cols] for k in keys],
                insights=itr_insights(list(best.values()), fy, owner, now))


def itr_sheet_rows(q):
    d = api_itr(q)
    if not d["returns"]:
        return []
    out = [("h", ["Figure"] + [c["title"] for c in d["columns"]])]
    out += [("", [r[0]] + [float(round(v, 2)) if v is not None else "" for v in r[1:]])
            for r in d["compare"]]
    if d["insights"]:
        out += [("", []), ("h", ["Checks from the last return"])]
        out += [("", [t]) for t in d["insights"]]
    return out


def summary_rows(q):
    where, args = txn_filter(q)
    con = db()
    got = {r["category"]: r for r in con.execute(
        f"SELECT category, SUM(debit) d, SUM(credit) c, COUNT(*) n FROM txns"
        f" WHERE {where} GROUP BY 1", args)}
    con.close()
    labels = dict(GROUPS)
    out = []
    for name, grp in CATS + [("", "none")]:
        r = got.get(name)
        if r:
            out.append([labels.get(grp, "Uncategorised"), name or "Uncategorised",
                        r["n"], round(r["c"], 2), round(r["d"], 2),
                        round(r["c"] - r["d"], 2)])
    return out


def fy_of(date):
    y = int(date[:4])
    return y if date[5:7] >= "04" else y - 1


def fy_label(fy):
    return f"FY {fy}-{str(fy + 1)[2:]} (AY {fy + 1}-{str(fy + 2)[2:]})"


def qget(q, k):
    return (q.get(k) or [""])[0]


GRP_OF = dict(CATS)
CLINIC_GROUPS = ("receipts", "expenses")


def kind_of(category):
    """Clinic, Personal or Unsorted, from the head an entry sits under."""
    if not category:
        return "Unsorted"
    return "Clinic" if GRP_OF.get(category) in CLINIC_GROUPS else "Personal"


def scope_label(q):
    """Human title and file-name part for the year and bank chosen."""
    fy, acct = qget(q, "fy"), qget(q, "account")
    name = ""
    if acct:
        con = db()
        r = con.execute("SELECT name FROM accounts WHERE id=?", (int(acct),)).fetchone()
        con.close()
        name = r["name"] if r else ""
    who = qget(q, "owner") if qget(q, "owner") in OWNERS else ""
    title = (fy_label(int(fy)) if fy else "All years") + ", " + (name or "all accounts")
    if who:
        title += ", " + who
        name = (name + " " + who).strip()
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "all-accounts") + "-" + (
        f"FY{fy}-{str(int(fy) + 1)[2:]}" if fy else "all-years")
    return title, slug


def entry_rows(q):
    where, args = txn_filter(q)
    con = db()
    labels = dict(GROUPS)
    out = []
    for r in con.execute(
            f"SELECT t.*, a.name account, a.owner owner, a.kind kind FROM txns t"
            f" JOIN accounts a ON a.id=t.account_id WHERE {where} ORDER BY date, seq",
            args):
        fy = fy_of(r["date"])
        out.append([r["date"], f"{fy}-{str(fy + 1)[2:]}", r["account"], r["narration"],
                    r["ref"], r["debit"] or 0, r["credit"] or 0, r["balance"],
                    kind_of(r["category"]),
                    labels.get(GRP_OF.get(r["category"]), "Unsorted"),
                    r["category"] or "Unsorted", r["clinic"], r["note"],
                    r["owner"], r["kind"]])
    con.close()
    return out


ENTRY_HEAD = ["Date", "Financial year", "Account", "Narration", "Ref", "Money out",
              "Money in", "Balance", "Clinic or personal", "Head", "Category",
              "Clinic", "Note", "Owner", "Account type"]


def itr_rows(q):
    """ITR-ready statement: receipts, expenses, profit, other income, deductions."""
    cats = {r[1]: r for r in summary_rows(q)}
    val = lambda name, k: cats[name][k] if name in cats else 0
    names = lambda g: [n for n, grp in CATS if grp == g and n in cats]
    title, _ = scope_label(q)
    rows = [("t", [f"Handral Dentistry, ITR working, {title}"]), ("", [])]
    rows.append(("h", ["A. Clinic receipts (professional income)", "Entries", "Amount"]))
    rec = 0
    for n in names("receipts"):
        v = val(n, 3) - val(n, 4); rec += v
        rows.append(("", [n, cats[n][2], round(v, 2)]))
    rows.append(("b", ["Gross clinic receipts", "", round(rec, 2)]))
    rows.append(("", []))
    rows.append(("h", ["B. Clinic expenses", "Entries", "Amount"]))
    exp = 0
    for n in names("expenses"):
        v = val(n, 4) - val(n, 3); exp += v
        rows.append(("", [n, cats[n][2], round(v, 2)]))
    rows.append(("b", ["Total clinic expenses", "", round(exp, 2)]))
    rows.append(("", []))
    rows.append(("b", ["C. Net clinic profit (A minus B)", "", round(rec - exp, 2)]))
    rows.append(("", []))
    rows.append(("h", ["D. Other income", "Entries", "Amount"]))
    oth = 0
    for n in names("other_income"):
        v = val(n, 3) - val(n, 4); oth += v
        rows.append(("", [n, cats[n][2], round(v, 2)]))
    rows.append(("b", ["Total other income", "", round(oth, 2)]))
    rows.append(("", []))
    rows.append(("h", ["E. Tax and deduction payments", "Entries", "Amount paid"]))
    for n in names("tax"):
        rows.append(("", [n, cats[n][2], round(val(n, 4) - val(n, 3), 2)]))
    rows.append(("", []))
    rows.append(("h", ["F. Not income or expense (for reference)", "Entries",
                       "Money in", "Money out"]))
    for g in ("invest", "assets", "loans", "personal"):
        for n in names(g):
            rows.append(("", [n, cats[n][2], val(n, 3), val(n, 4)]))
    trade = api_trading(q)["rows"]
    if any(r[k] for r in trade for k, _ in TRADE_FIELDS):
        rows.append(("", []))
        rows.append(("h", ["G. Trading (from broker P&L statements)", "Accounts", "Amount"]))
        for k, label in TRADE_FIELDS:
            v = sum(r[k] or 0 for r in trade)
            if v:
                rows.append(("", [label, len(trade), round(v, 2)]))
        rows.append(("b", ["Net trading result (excluding turnover)", "",
                           round(sum(r["net"] for r in trade), 2)]))
    sold = sold_in(api_assets(q)["rows"], qget(q, "fy"))
    if sold:
        rows.append(("", []))
        rows.append(("h", ["H. Investments and property sold", "Term", "Gain"]))
        for r in sold:
            rows.append(("", [f"{r['name']} ({r['type']}, sold {r['sold']})", r["term"],
                              round((r["sale"] or 0) - (r["cost"] or 0), 2)]))
    ins = api_insurance(q) if qget(q, "fy") else None
    if ins and (ins["rows"] or ins["used80c"]):
        rows.append(("", []))
        rows.append(("h", ["K. Insurance", "Paid this year", "Tax section"]))
        for r in ins["rows"]:
            rows.append(("", [f"{r['name']} ({r['type']})", round(r["paid"], 2),
                              r["category"] if "(" in r["category"] else
                              "Clinic expense" if r["category"] == "Clinic insurance" else "None"]))
        for x in ins["d80"]:
            rows.append(("", [f"80D claimable, {x['group'].lower()}", round(x["claim"], 2),
                              f"limit {inr_text(x['limit'])}"]))
        rows.append(("b", ["80C used (term, LIC, tax-saving, school fees)", round(ins["used80c"], 2),
                           "limit 1.5 lakh in total"]))
    loans = api_loans(q)["rows"] if qget(q, "fy") else []
    if loans:
        rows.append(("", []))
        rows.append(("h", ["J. Loans", "Interest this year", "Principal", "Outstanding"]))
        for l in loans:
            rows.append(("", [f"{l['name']} ({l['type']}, {l['purpose']})",
                              round(l["interest"], 2) if l["interest"] is not None else "",
                              round(l["principal"], 2) if l["principal"] is not None else "",
                              round(l["outstanding"], 2) if l["outstanding"] is not None else ""]))
            rows.append(("", ["   " + l["note_tax"]]))
    mf = api_mf(q)
    if mf["totals"]:
        rows.append(("", []))
        rows.append(("h", ["I. Mutual fund capital gains (" + (
            "from capital gains statement" if mf["source"] == "statement" else
            "capital gains statement and transactions" if mf["source"] == "both" else
            "matched oldest units first") + ")", "", "Gain"]))
        for k, v in mf["totals"]:
            rows.append(("", [k, "", round(v, 2)]))
        rows.append(("", ["Equity long-term gains up to 1.25 lakh a year are exempt."]))
    if "Uncategorised" in cats:
        u = cats["Uncategorised"]
        rows.append(("", []))
        rows.append(("b", ["UNSORTED, not counted above", u[2], u[3], u[4]]))
    rows.append(("", []))
    rows.append(("", ["Bank entries plus cash entered under Cash collections. Trading"
                      " figures are as entered from broker statements. Final heads and"
                      " deductions are for the CA to decide."]))
    return rows


def split_rows(q):
    """Clinic vs personal totals."""
    agg = {}
    for r in summary_rows(q):
        k = kind_of("" if r[1] == "Uncategorised" else r[1])
        a = agg.setdefault(k, [0, 0, 0])
        a[0] += r[2]; a[1] += r[3]; a[2] += r[4]
    out = [("h", ["Type", "Entries", "Money in", "Money out", "Net"])]
    for k in ("Clinic", "Personal", "Unsorted"):
        if k in agg:
            a = agg[k]
            out.append(("", [k, a[0], round(a[1], 2), round(a[2], 2), round(a[1] - a[2], 2)]))
    return out


def month_rows(q):
    where, args = txn_filter(q)
    con = db()
    m = {}
    for r in con.execute(f"SELECT substr(date,1,7) m, category, SUM(debit) d, SUM(credit) c"
                         f" FROM txns WHERE {where} GROUP BY 1,2", args):
        a = m.setdefault(r["m"], [0, 0])
        g = GRP_OF.get(r["category"])
        if g == "receipts":
            a[0] += r["c"] - r["d"]
        elif g == "expenses":
            a[1] += r["d"] - r["c"]
    con.close()
    out = [("h", ["Month", "Clinic receipts", "Clinic expenses", "Net profit"])]
    t = [0, 0]
    for k in sorted(m):
        a = m[k]; t[0] += a[0]; t[1] += a[1]
        out.append(("", [k, round(a[0], 2), round(a[1], 2), round(a[0] - a[1], 2)]))
    out.append(("b", ["Total", round(t[0], 2), round(t[1], 2), round(t[0] - t[1], 2)]))
    return out


def bank_rows(q):
    where, args = txn_filter(q)
    con = db()
    out = [("h", ["Account", "Entries", "Money in", "Money out", "Unsorted entries",
                  "Owner", "Type"])]
    for r in con.execute(
            f"SELECT a.name, a.owner, a.kind, COUNT(*) n, SUM(credit) c, SUM(debit) d,"
            f" SUM(CASE WHEN category='' THEN 1 ELSE 0 END) o FROM txns t"
            f" JOIN accounts a ON a.id=t.account_id WHERE {where} GROUP BY a.id"
            f" ORDER BY a.name", args):
        out.append(("", [r["name"], r["n"], round(r["c"], 2), round(r["d"], 2), r["o"],
                         r["owner"], r["kind"]]))
    con.close()
    return out


def head_rows(q):
    out = [("h", ["Head", "Category", "Entries", "Money in", "Money out", "Net"])]
    return out + [("", r) for r in summary_rows(q)]


def consultant_rows(q):
    out = [("h", ["Consultant", "Name in bank entries", "Payments", "Total paid"])]
    for c in api_consultants(q)["rows"]:
        if c["n"]:
            out.append(("", [c["name"], c["match"], c["n"], round(c["total"], 2)]))
    return out


def staff_rows(q):
    fy = qget(q, "fy")
    months = []
    if fy:
        y = int(fy)
        months = [f"{y + (m < 4)}-{m:02d}" for m in list(range(4, 13)) + [1, 2, 3]]
    out = [("h", ["Staff", "Role", "Name in bank entries", "Payments", "Total paid"]
            + [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in months])]
    for c in api_people("staff", q)["rows"]:
        if c["n"]:
            out.append(("", [c["name"], c["role"], c["match"], c["n"], round(c["total"], 2)]
                        + [float(round(c["months"][m], 2)) if c["months"].get(m) else ""
                           for m in months]))
    return out


def cash_rows(q):
    d = api_cash(q)
    out = [("h", ["Month", "Cash collected"])]
    out += [("", [datetime.strptime(m, "%Y-%m").strftime("%b %Y"), float(round(v, 2))])
            for m, v in sorted(d["by_month"].items())]
    out.append(("b", ["Total", float(round(d["total"], 2))]))
    return out


def export_summary(q):
    buf = io.StringIO()
    w = csv.writer(buf)
    blocks = [itr_rows(q), [("h", ["Clinic vs personal"])] + split_rows(q),
              [("h", ["By head"])] + head_rows(q), [("h", ["By bank"])] + bank_rows(q)]
    cons = consultant_rows(q)
    if len(cons) > 1:
        blocks.append(cons)
    staff = staff_rows(q)
    if len(staff) > 1:
        blocks.append([("h", ["Staff salaries"])] + staff)
    cash = cash_rows(q)
    if len(cash) > 2:
        blocks.append([("h", ["Cash collections"])] + cash)
    trade = trading_sheet_rows(q)
    if len(trade) > 2:
        blocks.append([("h", ["Trading P&L"])] + trade)
    assets = asset_sheet_rows(q)
    if len(assets) > 1:
        blocks.append([("h", ["Investments"])] + assets)
    for b in blocks:
        w.writerows(r for _, r in b)
        w.writerow([])
    return buf.getvalue().encode("utf-8-sig")


def export_csv(q):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ENTRY_HEAD)
    w.writerows(entry_rows(q))
    return buf.getvalue().encode("utf-8-sig")


# ---------- Excel writer (no extra packages needed) ----------

def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _clean(v):
    return xesc(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v)))


def _sheet_xml(rows, widths, freeze):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if freeze:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1"'
                   ' topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                   '</sheetView></sheetViews>')
    out.append("<cols>" + "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths)) + "</cols><sheetData>")
    for n, (kind, cells) in enumerate(rows, 1):
        bold = kind in ("h", "b", "t")
        out.append(f'<row r="{n}">')
        for c, v in enumerate(cells):
            if v is None or v == "":
                continue
            ref = f"{_col(c)}{n}"
            if isinstance(v, float) or (isinstance(v, int) and kind != "t"):
                st = (3 if bold else 2) if isinstance(v, float) else (1 if bold else 0)
                out.append(f'<c r="{ref}" s="{st}"><v>{v}</v></c>')
            else:
                out.append(f'<c r="{ref}" s="{1 if bold else 0}" t="inlineStr">'
                           f'<is><t xml:space="preserve">{_clean(v)}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def make_xlsx(sheets):
    """sheets: list of (name, rows, widths, freeze). Rows are (kind, cells)."""
    names, seen = [], set()
    for name, *_ in sheets:
        n = re.sub(r"[\[\]\*\?/\\:]", " ", name)[:31] or "Sheet"
        while n.lower() in seen:
            n = n[:28] + " 2"
        seen.add(n.lower()); names.append(n)
    ns = "http://schemas.openxmlformats.org/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Types xmlns="{ns}package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="{ns}package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{ns}officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<workbook xmlns="{ns}spreadsheetml/2006/main" xmlns:r="{ns}officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{_clean(n)}" sheetId="{i}" r:id="rId{i}"/>'
                             for i, n in enumerate(names, 1)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="{ns}package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="{ns}officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="{ns}officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<styleSheet xmlns="{ns}spreadsheetml/2006/main">'
                   '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                   '<fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="4">'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                   '<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>'
                   '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
                   "</cellStyles></styleSheet>")
        for i, (_, rows, widths, freeze) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, widths, freeze))
    return buf.getvalue()


# ---------- Tally: ledgers, vouchers, Day Book, Trial Balance, P&L ----------

def tally_ledger(category):
    return TALLY.get(category, (category, "Suspense A/c"))


def account_ledger(name, kind):
    return ("Cash", "Cash-in-Hand") if kind == "Cash" else (name, KIND_GROUP.get(kind, "Bank Accounts"))


def tally_vouchers(q):
    """Every entry as a Tally voucher: (date, type, no, debit ledger, credit ledger,
    amount, narration, cost centre, bank ledger, ledger, ledger group, bank group)."""
    where, args = txn_filter(q)
    con = db()
    rows = con.execute(f"SELECT t.*, a.name acct, a.kind kind FROM txns t JOIN accounts a"
                       f" ON a.id=t.account_id WHERE {where} ORDER BY date, seq", args).fetchall()
    con.close()
    out, numbers = [], {}
    for r in rows:
        bank, bgroup = account_ledger(r["acct"], r["kind"])
        led, group = tally_ledger(r["category"])
        if group in PARTY_GROUPS and (r["payee"] or "").strip():
            party = r["payee"].strip().title()
            if party not in BOOK_LEDGERS:  # never merge a party into one of the fixed ledgers
                led = party
        amt = r["credit"] or r["debit"]
        if not amt:
            continue
        money_in = r["credit"] > 0
        vtype = ("Contra" if group in ("Bank Accounts", "Cash-in-Hand")
                 else "Receipt" if money_in else "Payment")
        numbers[vtype] = numbers.get(vtype, 0) + 1
        dr, cr = (bank, led) if money_in else (led, bank)
        out.append(dict(date=r["date"], type=vtype, no=numbers[vtype], dr=dr, cr=cr,
                        amount=round(amt, 2), narration=r["narration"],
                        centre=(r["clinic"] or "") if group in PNL_GROUPS else "",
                        bank=bank, bgroup=bgroup, ledger=led, group=group, note=r["note"] or ""))
    return out


def trial_balance(q):
    """Ledger balances for the period by Tally group. Opening balances are not
    included, so bank ledgers show the movement in the period."""
    bal, groups = {}, {}
    for v in tally_vouchers(q):
        for name, grp, sign in ((v["dr"], None, 1), (v["cr"], None, -1)):
            bal[name] = bal.get(name, 0) + sign * v["amount"]
        groups[v["ledger"]] = v["group"]
        groups[v["bank"]] = v["bgroup"]
    out = []
    for g in TALLY_GROUPS + sorted(set(groups.values()) - set(TALLY_GROUPS)):
        names = sorted(n for n in bal if groups.get(n) == g and abs(bal[n]) >= 0.005)
        if names:
            out.append((g, [(n, round(bal[n], 2)) for n in names]))
    return out


def tally_pnl(q):
    """Profit & Loss A/c in Tally's layout, for the clinic ledgers."""
    tb = dict(trial_balance(q))
    side = lambda g, sign: [(n, round(sign * v, 2)) for n, v in tb.get(g, [])]
    d = dict(direct_inc=side("Direct Incomes", -1), purchases=side("Purchase Accounts", 1),
             direct_exp=side("Direct Expenses", 1), indirect_inc=side("Indirect Incomes", -1),
             indirect_exp=side("Indirect Expenses", 1))
    tot = lambda k: round(sum(v for _, v in d[k]), 2)
    d["gross"] = round(tot("direct_inc") - tot("purchases") - tot("direct_exp"), 2)
    d["net"] = round(d["gross"] + tot("indirect_inc") - tot("indirect_exp"), 2)
    return d


def tally_report_rows(q):
    """Day Book, Trial Balance and P&L as sheets for the Excel workbook."""
    vs = tally_vouchers(q)
    day = [("h", ["Date", "Voucher type", "Vch no.", "Debit ledger", "Credit ledger", "Amount",
                  "Narration", "Cost centre"])]
    day += [("", [v["date"], v["type"], v["no"], v["dr"], v["cr"], float(v["amount"]),
                  v["narration"], v["centre"]]) for v in vs]
    tb = [("t", ["Trial Balance (movement for the period; opening balances not included)"]),
          ("h", ["Particulars", "Debit", "Credit"])]
    dr = cr = 0
    for g, items in trial_balance(q):
        gd = sum(v for _, v in items if v > 0)
        gc = -sum(v for _, v in items if v < 0)
        tb.append(("b", [g, float(round(gd, 2)) if gd else "", float(round(gc, 2)) if gc else ""]))
        for n, v in items:
            tb.append(("", ["    " + n, float(v) if v > 0 else "", float(-v) if v < 0 else ""]))
        dr += gd
        cr += gc
    tb.append(("b", ["Grand Total", float(round(dr, 2)), float(round(cr, 2))]))
    p = tally_pnl(q)
    pl = [("t", ["Profit & Loss A/c"]), ("h", ["Particulars (Dr)", "Amount", "Particulars (Cr)", "Amount"])]
    left = ([("Purchase Accounts", None)] + p["purchases"] + [("Direct Expenses", None)] + p["direct_exp"]
            + ([("Gross Profit c/o", p["gross"])] if p["gross"] >= 0 else []))
    right = ([("Direct Incomes", None)] + p["direct_inc"]
             + ([("Gross Loss c/o", -p["gross"])] if p["gross"] < 0 else []))
    left2 = ([("Gross Loss b/f", -p["gross"])] if p["gross"] < 0 else []) + [("Indirect Expenses", None)] \
        + p["indirect_exp"] + ([("Net Profit", p["net"])] if p["net"] >= 0 else [])
    right2 = ([("Gross Profit b/f", p["gross"])] if p["gross"] >= 0 else []) + [("Indirect Incomes", None)] \
        + p["indirect_inc"] + ([("Net Loss", -p["net"])] if p["net"] < 0 else [])
    for a, b in ((left, right), (left2, right2)):
        for i in range(max(len(a), len(b))):
            l = a[i] if i < len(a) else ("", None)
            r = b[i] if i < len(b) else ("", None)
            bold = l[1] is None and l[0] or r[1] is None and r[0]
            pl.append(("b" if bold else "", [l[0], float(l[1]) if l[1] is not None else "",
                                             r[0], float(r[1]) if r[1] is not None else ""]))
        pl.append(("", []))
    return pl, tb, day


def tally_xml(q):
    """A zip with ledgers and vouchers in TallyPrime's XML import format."""
    vs = tally_vouchers(q)
    ledgers = {}
    for v in vs:
        ledgers[v["ledger"]] = v["group"]
        ledgers[v["bank"]] = v["bgroup"]
    env = lambda report, body: (
        '<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data'
        '</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>' + report +
        '</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>'
        '</STATICVARIABLES></REQUESTDESC><REQUESTDATA>' + body + '</REQUESTDATA></IMPORTDATA>'
        '</BODY></ENVELOPE>\n')
    x = lambda t: _clean(t)
    masters = "".join(
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{x(n)}" ACTION="Create">'
        f'<NAME.LIST><NAME>{x(n)}</NAME></NAME.LIST><PARENT>{x(g)}</PARENT>'
        f'<ISBILLWISEON>No</ISBILLWISEON><ISCOSTCENTRESON>'
        f'{"Yes" if g in PNL_GROUPS else "No"}'
        f'</ISCOSTCENTRESON></LEDGER></TALLYMESSAGE>' for n, g in sorted(ledgers.items()))
    centres = "".join(
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><COSTCENTRE NAME="{x(c)}" ACTION="Create">'
        f'<NAME.LIST><NAME>{x(c)}</NAME></NAME.LIST><PARENT/></COSTCENTRE></TALLYMESSAGE>'
        for c in sorted({v["centre"] for v in vs if v["centre"]}))

    def entry(name, amount, debit, centre=""):
        cc = (f'<CATEGORYALLOCATIONS.LIST><CATEGORY>Primary Cost Category</CATEGORY>'
              f'<COSTCENTREALLOCATIONS.LIST><NAME>{x(centre)}</NAME><AMOUNT>'
              f'{-amount if debit else amount:.2f}</AMOUNT></COSTCENTREALLOCATIONS.LIST>'
              f'</CATEGORYALLOCATIONS.LIST>') if centre else ""
        return (f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{x(name)}</LEDGERNAME><ISDEEMEDPOSITIVE>'
                f'{"Yes" if debit else "No"}</ISDEEMEDPOSITIVE><AMOUNT>'
                f'{-amount if debit else amount:.2f}</AMOUNT>{cc}</ALLLEDGERENTRIES.LIST>')
    vouchers = "".join(
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{v["type"]}" ACTION="Create">'
        f'<DATE>{v["date"].replace("-", "")}</DATE><EFFECTIVEDATE>{v["date"].replace("-", "")}'
        f'</EFFECTIVEDATE><VOUCHERTYPENAME>{v["type"]}</VOUCHERTYPENAME><VOUCHERNUMBER>{v["no"]}'
        f'</VOUCHERNUMBER><NARRATION>{x((v["narration"] + (" | " + v["note"] if v["note"] else ""))[:500])}'
        f'</NARRATION>'
        + entry(v["dr"], v["amount"], True, v["centre"] if v["dr"] == v["ledger"] else "")
        + entry(v["cr"], v["amount"], False, v["centre"] if v["cr"] == v["ledger"] else "")
        + '</VOUCHER></TALLYMESSAGE>' for v in vs)
    readme = ("Handral Books export for TallyPrime\r\n\r\n"
              "1. Create or open the company in TallyPrime (try a test company first).\r\n"
              "2. Import > Masters > choose 1-masters.xml (ledgers and cost centres).\r\n"
              "3. Import > Transactions > choose 2-vouchers.xml.\r\n\r\n"
              "Receipt/Payment vouchers are against each bank ledger. Transfers between the\r\n"
              "proprietor's own accounts go through 'Inter-Bank Transfer' (Contra); its balance\r\n"
              "should be near zero once all accounts are imported. 'Suspense A/c' holds entries\r\n"
              "not yet sorted. Opening balances are not included.\r\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("1-masters.xml", env("All Masters", masters + centres))
        z.writestr("2-vouchers.xml", env("Vouchers", vouchers))
        z.writestr("README.txt", readme)
    return buf.getvalue()


def api_tally(q):
    p = tally_pnl(q)
    tb = trial_balance(q)
    return dict(pnl=p, tb=[[g, items] for g, items in tb],
                suspense=sum(-v for g, items in tb if g == "Suspense A/c" for _, v in items))


def export_xlsx(q):
    ew = [12, 10, 16, 60, 18, 14, 14, 14, 12, 26, 30, 12, 24, 10, 12]
    ent = entry_rows(q)
    money = lambda rows: [("", [float(v) if i in (5, 6) or (i == 7 and v is not None)
                                else v for i, v in enumerate(r)]) for r in rows]
    pl, tb, day = tally_report_rows(q)
    sheets = [
        ("ITR summary", itr_rows(q), [46, 10, 16, 16], False),
        ("Profit & Loss A-c", pl, [36, 16, 36, 16], False),
        ("Trial Balance", tb, [44, 16, 16], False),
        ("Day Book", day, [12, 10, 8, 34, 34, 14, 70, 12], True),
        ("Income and expense", head_rows(q), [28, 36, 9, 16, 16, 16], True),
        ("Clinic vs personal", split_rows(q), [16, 9, 16, 16, 16], True),
        ("By month", month_rows(q), [12, 16, 16, 16], True),
        ("By account", bank_rows(q), [22, 9, 16, 16, 16, 10, 12], True),
    ]
    if not qget(q, "owner"):
        per = {}
        for r in ent:
            a = per.setdefault(r[13], [0, 0.0, 0.0])
            a[0] += 1; a[1] += r[6]; a[2] += r[5]
        if len(per) > 1:
            sheets.append(("By person", [("h", ["Owner", "Entries", "Money in", "Money out"])]
                           + [("", [k, v[0], round(v[1], 2), round(v[2], 2)])
                              for k, v in per.items()], [14, 9, 16, 16], True))
    cons = consultant_rows(q)
    if len(cons) > 1:
        sheets.append(("Consultants", cons, [26, 26, 10, 16], True))
    staff = staff_rows(q)
    if len(staff) > 1:
        sheets.append(("Staff salaries", staff, [24, 18, 24, 10, 16] + [12] * 12, True))
    cash = cash_rows(q)
    if len(cash) > 2:
        sheets.append(("Cash by month", cash, [16, 16], True))
    trade = trading_sheet_rows(q)
    if len(trade) > 2:
        sheets.append(("Trading P&L", trade, [22, 10] + [16] * 11 + [30], True))
    assets = asset_sheet_rows(q)
    ins = insurance_sheet_rows(q)
    if ins:
        sheets.append(("Insurance", ins, [28, 22, 18, 16, 10, 18, 14, 14, 14, 12, 60], True))
    loans = loan_sheet_rows(q)
    if loans:
        sheets.append(("Loans", loans, [28, 14, 18, 10, 18, 14, 14, 14, 14, 14, 60], True))
    mf = mf_sheet_rows(q)
    if mf:
        sheets.append(("Mutual funds", mf, [44, 10, 12, 12, 12, 14, 14, 14, 22], True))
    if len(assets) > 1:
        sheets.append(("Investments", assets,
                       [30, 22, 10, 12, 14, 14, 12, 14, 12, 14, 14, 26, 30], True))
    itr = itr_sheet_rows(q)
    if itr:
        sheets.append(("Previous ITRs", itr, [40] + [20] * 6, True))
    sheets.append(("All entries", [("h", ENTRY_HEAD)] + money(ent), ew, True))
    for kind in ("Clinic", "Personal"):
        part = [r for r in ent if r[8] == kind]
        if part:
            sheets.append((kind + " entries", [("h", ENTRY_HEAD)] + money(part), ew, True))
    if not qget(q, "account"):
        banks = []
        for r in ent:
            if r[2] not in banks:
                banks.append(r[2])
        if len(banks) > 1:
            for b in banks:
                sheets.append((b, [("h", ENTRY_HEAD)] + money([r for r in ent if r[2] == b]),
                               ew, True))
    return make_xlsx(sheets)


# ---------- http ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200, extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                self.send(PAGE.encode(), "text/html; charset=utf-8")
            elif u.path == "/healthz":
                con = db()
                con.execute("SELECT COUNT(*) FROM accounts").fetchone()
                con.close()
                self.send({"ok": True})
            elif u.path == "/api/state":
                self.send(api_state())
            elif u.path == "/api/txns":
                self.send(api_txns(q))
            elif u.path == "/api/report":
                self.send(api_report(q))
            elif u.path == "/rules.json":
                self.send(json.dumps(export_rules(), indent=1).encode(), "application/json", extra={
                    "Content-Disposition": "attachment; filename=handral-books-rules.json"})
            elif u.path == "/api/consultants":
                self.send(api_consultants(q))
            elif u.path == "/api/staff":
                self.send(api_people("staff", q))
            elif u.path == "/api/cash":
                self.send(api_cash(q))
            elif u.path == "/api/itr":
                self.send(api_itr(q))
            elif u.path == "/api/mf":
                self.send(api_mf(q))
            elif u.path == "/api/insurance":
                self.send(api_insurance(q))
            elif u.path == "/api/loans":
                self.send(api_loans(q))
            elif u.path == "/api/itr/figures":
                con = db()
                r = con.execute("SELECT flat FROM itr_returns WHERE id=?",
                                (int(qget(q, "id")),)).fetchone()
                con.close()
                self.send({"rows": json.loads(r["flat"]) if r else []})
            elif u.path == "/api/itr/file":
                con = db()
                r = con.execute("SELECT filename, data FROM itr_returns WHERE id=?",
                                (int(qget(q, "id")),)).fetchone()
                con.close()
                if not r:
                    return self.send({"error": "Not found"}, code=404)
                fname = re.sub(r"[^A-Za-z0-9._-]+", "_", r["filename"] or "itr")
                self.send(bytes(r["data"]), "application/octet-stream", extra={
                    "Content-Disposition": f"attachment; filename={fname}"})
            elif u.path == "/api/patient-rule":
                self.send(api_patient_rule())
            elif u.path == "/api/coverage":
                self.send(api_coverage())
            elif u.path == "/api/mail":
                self.send(api_mail())
            elif u.path == "/api/mail/file":
                con = db()
                r = con.execute("SELECT filename, file FROM mail_log WHERE id=?",
                                (int(qget(q, "id")),)).fetchone()
                con.close()
                if not r or r["file"] is None:
                    return self.send({"error": "Not found"}, code=404)
                fname = re.sub(r"[^A-Za-z0-9._-]+", "_", r["filename"] or "statement.pdf")
                self.send(bytes(r["file"]), "application/pdf", extra={
                    "Content-Disposition": f"attachment; filename={fname}"})
            elif u.path == "/api/groups":
                self.send(api_groups(q))
            elif u.path == "/api/trading":
                self.send(api_trading(q))
            elif u.path == "/api/assets":
                self.send(api_assets(q))
            elif u.path == "/export-summary.csv":
                self.send(export_summary(q), "text/csv", extra={
                    "Content-Disposition":
                    f"attachment; filename=handral-itr-summary-{scope_label(q)[1]}.csv"})
            elif u.path == "/export.csv":
                self.send(export_csv(q), "text/csv", extra={
                    "Content-Disposition":
                    f"attachment; filename=handral-entries-{scope_label(q)[1]}.csv"})
            elif u.path == "/api/tally":
                self.send(api_tally(q))
            elif u.path == "/export-tally.zip":
                self.send(tally_xml(q), "application/zip", extra={
                    "Content-Disposition":
                    f"attachment; filename=handral-tally-{scope_label(q)[1]}.zip"})
            elif u.path == "/export.xlsx":
                self.send(export_xlsx(q), "application/vnd.openxmlformats-officedocument"
                          ".spreadsheetml.sheet", extra={
                    "Content-Disposition":
                    f"attachment; filename=handral-books-{scope_label(q)[1]}.xlsx"})
            else:
                self.send({"error": "Not found"}, code=404)
        except Exception as e:
            self.send({"error": str(e)}, code=500)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/import":
                res = import_statement(int(q["account"][0]), q["name"][0],
                                       self.body(), (q.get("pw") or [""])[0])
                return self.send(res)
            if u.path == "/api/mail/settings":
                return self.send(save_mail_settings(json.loads(self.body() or b"{}")))
            if u.path == "/api/mail/fetch":
                started = start_mail_fetch()
                return self.send(dict(api_mail(), started=started))
            if u.path == "/api/patient-rule":
                d = json.loads(self.body() or b"{}")
                with LOCK:
                    con = db()
                    for key, field in (("patient_rule", "on"), ("patient_max", "max"),
                                       ("patient_exclude", "exclude"), ("small_rule", "small_on"),
                                       ("small_max", "small_max")):
                        if field in d:
                            v = d[field]
                            v = ("1" if v else "0") if field in ("on", "small_on") else str(v).strip()
                            con.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, v))
                    n = apply_rules(con)
                    con.commit()
                    con.close()
                return self.send(dict(api_patient_rule(), sorted=n))
            if u.path == "/api/rules/import":
                return self.send(import_rules(self.body()))
            if u.path == "/api/mf/upload":
                parsed = parse_mf(qget(q, "name"), self.body(), qget(q, "pw"))
                owner = qget(q, "owner") if qget(q, "owner") in OWNERS else "Self"
                return self.send(store_mf(owner, qget(q, "name"), parsed))
            if u.path == "/api/itr/upload":
                data = self.body()
                info, flat = parse_itr(qget(q, "name"), data, qget(q, "pw"))
                if not info["ay"]:
                    raise ValueError("Could not find the assessment year in this file.")
                owner = qget(q, "owner") if qget(q, "owner") in OWNERS else "Self"
                with LOCK:
                    con = db()
                    con.execute(
                        "INSERT INTO itr_returns(owner,ay,form,name,pan,filename,uploaded,"
                        "summary,flat,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (owner, info["ay"], info["form"], info["name"], info["pan"],
                         qget(q, "name"), datetime.now().strftime("%Y-%m-%d"),
                         json.dumps({k: info[k] for k in ("figures", "banks", "scheme")}),
                         json.dumps(flat[:5000], default=str), data))
                    con.commit()
                    con.close()
                return self.send(dict(ok=True, ay=info["ay"], form=info["form"],
                                      found=len(info["figures"])))
            if u.path == "/api/trading/parse":
                return self.send(parse_pnl(qget(q, "name"), self.body(), qget(q, "pw")))
            d = json.loads(self.body() or b"{}")
            with LOCK:
                con = db()
                res = {"ok": True}
                if u.path == "/api/account":
                    name = d["name"].strip()
                    if not name:
                        raise ValueError("Enter an account name.")
                    owner = d.get("owner") if d.get("owner") in OWNERS else "Self"
                    kind = d.get("kind") if d.get("kind") in KINDS else "Bank"
                    if d.get("id"):
                        con.execute("UPDATE accounts SET name=?, owner=?, kind=? WHERE id=?",
                                    (name, owner, kind, d["id"]))
                    else:
                        con.execute("INSERT OR IGNORE INTO accounts(name,owner,kind)"
                                    " VALUES(?,?,?)", (name, owner, kind))
                elif u.path == "/api/account/delete":
                    con.execute("DELETE FROM txns WHERE account_id=?", (d["id"],))
                    con.execute("DELETE FROM trading_pnl WHERE account_id=?", (d["id"],))
                    con.execute("DELETE FROM accounts WHERE id=?", (d["id"],))
                elif u.path == "/api/txn":
                    con.execute("UPDATE txns SET category=?, clinic=?, note=? WHERE id=?",
                                (d["category"], d["clinic"], d.get("note", ""), d["id"]))
                elif u.path == "/api/bulk":
                    fq = {k: [str(v)] for k, v in d["filter"].items() if v}
                    where, args = txn_filter(fq)
                    cur = con.execute(
                        f"UPDATE txns SET category=?, clinic=? WHERE {where}",
                        [d["category"], d["clinic"]] + args)
                    res["changed"] = cur.rowcount
                elif u.path == "/api/group":
                    res["changed"] = sort_group(con, d, d["payee"], d["dir"])
                elif u.path == "/api/groups-bulk":
                    res["changed"] = sum(
                        sort_group(con, d, i["payee"], i["dir"]) for i in d["items"])
                elif u.path == "/api/txns-bulk":
                    ids = [int(i) for i in d["ids"]]
                    marks = ",".join("?" * len(ids))
                    cur = con.execute(
                        f"UPDATE txns SET category=?, clinic=? WHERE id IN ({marks})",
                        [d["category"], d.get("clinic", "")] + ids)
                    res["changed"] = cur.rowcount
                elif u.path in ("/api/consultant", "/api/staff"):
                    table, head = PEOPLE[u.path.rsplit("/", 1)[1]]
                    name = " ".join(str(d.get("name") or "").split())
                    match = ", ".join(spellings(d.get("match") or name))
                    if not match or min(len(m) for m in spellings(match)) < 4:
                        raise ValueError("Enter at least 4 letters of the name.")
                    role = " ".join(str(d.get("role") or "").split()) if table == "staff" else None
                    add_person(con, table, name, match, role or None)
                    who, who_args = person_where(match)
                    cur = con.execute(f"UPDATE txns SET category=? WHERE category='' AND debit>0"
                                      f" AND {who}", [head] + who_args)
                    res["changed"] = cur.rowcount
                    res["other"] = con.execute(
                        f"SELECT COUNT(*) FROM txns WHERE category NOT IN ('', ?) AND debit>0"
                        f" AND {who}", [head] + who_args).fetchone()[0]
                elif u.path == "/api/staff/delete":
                    con.execute("DELETE FROM staff WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/cash":
                    date = check_date(d.get("date"))
                    patient = " ".join(str(d.get("patient") or "").split())
                    treat = " ".join(str(d.get("treatment") or "").split())
                    amount = to_num(d.get("amount"))
                    if not date:
                        raise ValueError("Enter the date.")
                    if amount <= 0:
                        raise ValueError("Enter the amount received.")
                    clinic = d.get("clinic") if d.get("clinic") in CLINICS else ""
                    acct = cash_account(con)
                    seq = con.execute("SELECT COALESCE(MAX(seq),0)+1 FROM txns").fetchone()[0]
                    note = " ".join(str(d.get("note") or "").split())
                    narr = " - ".join(x for x in ("Cash collection", patient, treat, note) if x)
                    h = hashlib.sha1(f"cash|{time.time_ns()}|{narr}|{amount}".encode()).hexdigest()
                    con.execute(
                        "INSERT INTO txns(account_id,date,narration,ref,debit,credit,balance,"
                        "category,clinic,note,seq,hash,payee) VALUES(?,?,?,?,0,?,0,?,?,?,?,?,?)",
                        (acct, date, narr, treat, amount, CASH_HEAD, clinic,
                         note, seq, h, patient.upper()[:30] or "CASH"))
                elif u.path == "/api/cash/delete":
                    con.execute("DELETE FROM txns WHERE id=? AND account_id IN"
                                " (SELECT id FROM accounts WHERE kind='Cash')", (int(d["id"]),))
                elif u.path == "/api/trading":
                    acct, fy = int(d["account_id"]), int(d["fy"])
                    keys = [k for k, _ in TRADE_FIELDS]
                    con.execute(
                        f"INSERT INTO trading_pnl(account_id,fy,{','.join(keys)},note)"
                        f" VALUES(?,?,{','.join('?' * len(keys))},?)"
                        f" ON CONFLICT(account_id,fy) DO UPDATE SET "
                        + ", ".join(f"{k}=excluded.{k}" for k in keys + ["note"]),
                        [acct, fy] + [to_num(d.get(k)) for k in keys]
                        + [str(d.get("note") or "").strip()])
                    if "match" in d:
                        con.execute("UPDATE accounts SET bank_match=? WHERE id=?",
                                    (" ".join(str(d["match"]).split()), acct))
                elif u.path == "/api/asset":
                    name = " ".join(str(d.get("name") or "").split())
                    if not name:
                        raise ValueError("Enter a name for the investment.")
                    kind = d.get("type") if d.get("type") in TERM_OF else "Other"
                    owner = d.get("owner") if d.get("owner") in OWNERS else "Self"
                    bought, valued, sold = (check_date(d.get(k))
                                            for k in ("bought", "valued", "sold"))
                    cost, value, sale = (to_num(d.get(k)) for k in ("cost", "value", "sale"))
                    if sold and bought and sold < bought:
                        raise ValueError("Sale date is before the purchase date.")
                    if value and not valued:
                        valued = datetime.now().strftime("%Y-%m-%d")
                    vals = (name, kind, owner, bought, cost, value, valued, sold,
                            sale if sold else 0, str(d.get("note") or "").strip())
                    if d.get("id"):
                        con.execute("UPDATE assets SET name=?, type=?, owner=?, bought=?,"
                                    " cost=?, value=?, valued=?, sold=?, sale=?, note=?"
                                    " WHERE id=?", vals + (int(d["id"]),))
                    else:
                        con.execute("INSERT INTO assets(name,type,owner,bought,cost,value,"
                                    "valued,sold,sale,note) VALUES(?,?,?,?,?,?,?,?,?,?)", vals)
                elif u.path == "/api/loan":
                    name = " ".join(str(d.get("name") or "").split())
                    if not name:
                        raise ValueError("Enter a name for the loan, e.g. HDFC car loan.")
                    typ = d.get("type") if d.get("type") in LOAN_TYPES else "Other loan"
                    purpose = d.get("purpose") if d.get("purpose") in LOAN_PURPOSES else "Personal"
                    owner = d.get("owner") if d.get("owner") in OWNERS else "Self"
                    match = " ".join(str(d.get("match") or "").split())
                    if match and len(match) < 3:
                        raise ValueError("The text in bank entries must be at least 3 letters.")
                    vals = (name, typ, " ".join(str(d.get("lender") or "").split()), owner, purpose,
                            to_num(d.get("amount")), check_date(d.get("start")), to_num(d.get("rate")),
                            to_num(d.get("emi")), match, check_date(d.get("closed")),
                            str(d.get("note") or "").strip())
                    if d.get("id"):
                        lid = int(d["id"])
                        con.execute("UPDATE loans SET name=?, type=?, lender=?, owner=?, purpose=?,"
                                    " amount=?, start=?, rate=?, emi=?, match=?, closed=?, note=?"
                                    " WHERE id=?", vals + (lid,))
                    else:
                        lid = con.execute("INSERT INTO loans(name,type,lender,owner,purpose,amount,"
                                          "start,rate,emi,match,closed,note) VALUES(?,?,?,?,?,?,?,?,?,"
                                          "?,?,?)", vals).lastrowid
                    if d.get("fy"):
                        num = lambda k: None if str(d.get(k) or "").strip() == "" else to_num(d.get(k))
                        con.execute("INSERT INTO loan_years(loan_id,fy,interest,outstanding) VALUES(?,?,?,?)"
                                    " ON CONFLICT(loan_id,fy) DO UPDATE SET interest=excluded.interest,"
                                    " outstanding=excluded.outstanding",
                                    (lid, int(d["fy"]), num("interest"), num("outstanding")))
                    res["changed"] = 0
                    if match:  # file unsorted and generically sorted loan entries under this loan
                        marks = ",".join("?" * len(LOAN_TYPES))
                        res["changed"] = con.execute(
                            f"UPDATE txns SET category=? WHERE narration LIKE ? AND (category=''"
                            f" OR category IN ({marks}))", [typ, f"%{match}%"] + LOAN_TYPES).rowcount
                elif u.path == "/api/policy":
                    name = " ".join(str(d.get("name") or "").split())
                    if not name:
                        raise ValueError("Enter a name for the policy, e.g. HDFC Life term plan.")
                    typ = d.get("type") if d.get("type") in POLICY_TYPES else "Other insurance"
                    purpose = "Clinic" if d.get("purpose") == "Clinic" else "Personal"
                    match = " ".join(str(d.get("match") or "").split())
                    if match and len(match) < 3:
                        raise ValueError("The text in bank entries must be at least 3 letters.")
                    vals = (name, typ, " ".join(str(d.get("insurer") or "").split()),
                            str(d.get("policy_no") or "").strip(),
                            d.get("owner") if d.get("owner") in OWNERS else "Self",
                            d.get("insured") if d.get("insured") in INSURED else "Self and family",
                            1 if d.get("senior") else 0, purpose, to_num(d.get("cover")),
                            to_num(d.get("premium")),
                            d.get("frequency") if d.get("frequency") in POLICY_FREQ else "Yearly",
                            check_date(d.get("due")), match, check_date(d.get("closed")),
                            str(d.get("note") or "").strip())
                    if d.get("id"):
                        con.execute("UPDATE policies SET name=?, type=?, insurer=?, policy_no=?, owner=?,"
                                    " insured=?, senior=?, purpose=?, cover=?, premium=?, frequency=?,"
                                    " due=?, match=?, closed=?, note=? WHERE id=?", vals + (int(d["id"]),))
                    else:
                        con.execute("INSERT INTO policies(name,type,insurer,policy_no,owner,insured,senior,"
                                    "purpose,cover,premium,frequency,due,match,closed,note)"
                                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
                    res["changed"] = 0
                    if match:  # file this policy's premiums under the right head
                        marks = ",".join("?" * len(INSURANCE_CATS))
                        res["changed"] = con.execute(
                            f"UPDATE txns SET category=? WHERE debit>0 AND narration LIKE ? AND"
                            f" (category='' OR category IN ({marks}))",
                            [policy_category(typ, purpose), f"%{match}%"] + INSURANCE_CATS).rowcount
                elif u.path == "/api/policy/delete":
                    con.execute("DELETE FROM policies WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/loan/delete":
                    con.execute("DELETE FROM loan_years WHERE loan_id=?", (int(d["id"]),))
                    con.execute("DELETE FROM loans WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/mf/upload/delete":
                    for t in ("mf_txns", "mf_cg"):
                        con.execute(f"DELETE FROM {t} WHERE upload_id=?", (int(d["id"]),))
                    con.execute("DELETE FROM mf_uploads WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/itr/delete":
                    con.execute("DELETE FROM itr_returns WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/asset/delete":
                    con.execute("DELETE FROM assets WHERE id=?", (int(d["id"]),))
                elif u.path == "/api/consultant/delete":
                    con.execute("DELETE FROM consultants WHERE id=?", (d["id"],))
                elif u.path == "/api/rule":
                    pat = d["pattern"].strip()
                    if len(pat) < 3:
                        raise ValueError("Rule text must be at least 3 characters.")
                    con.execute(
                        "INSERT INTO rules(pattern,dir,category,clinic) VALUES(?,?,?,?)",
                        (pat, d.get("dir", "any"), d["category"], d.get("clinic", "")))
                    res["changed"] = apply_rules(con)
                elif u.path == "/api/rule/delete":
                    con.execute("DELETE FROM rules WHERE id=?", (d["id"],))
                else:
                    con.close()
                    return self.send({"error": "Not found"}, code=404)
                con.commit()
                con.close()
            self.send(res)
        except ValueError as e:
            self.send({"error": str(e)}, code=400)
        except Exception as e:
            self.send({"error": f"{type(e).__name__}: {e}"}, code=500)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handral Books</title>
<style>
:root{--bg:#f3f6f4;--card:#fff;--ink:#14261f;--mute:#5d6f67;--line:#d6dfda;
--brand:#0e6b55;--in:#1b7f4b;--out:#b3372b;--warn:#8a5a00;--warnbg:#fdf3d7}
@media(prefers-color-scheme:dark){:root{--bg:#0f1714;--card:#18231f;--ink:#e6efe9;
--mute:#93a69d;--line:#2b3a34;--brand:#3fb893;--in:#5fd08f;--out:#f08a7e;
--warn:#f2c35c;--warnbg:#3a2f12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 "Segoe UI",Roboto,"Noto Sans",system-ui,sans-serif;padding-bottom:76px}
header{display:flex;align-items:center;gap:8px 10px;padding:10px 16px;background:#0e6b55;color:#fff;flex-wrap:wrap}
header select{min-height:38px;padding:6px 8px;max-width:100%}
header h1{font-size:19px;margin:0;font-weight:650;letter-spacing:.2px;flex:1}
header label{font-size:13px;opacity:.85}
header select{background:rgba(255,255,255,.16);color:#fff;border:0}
header option{color:#000}
main{max-width:980px;margin:0 auto;padding:14px}
section{display:none}section.on{display:block}
h2{font-size:17px;margin:6px 0 10px}
h3{font-size:15px;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grow{flex:1;min-width:0;overflow-wrap:anywhere}
.mute{color:var(--mute);font-size:13px}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.in{color:var(--in)}.out{color:var(--out)}
button,select,input{font:inherit;color:inherit;border:1px solid var(--line);
background:var(--card);border-radius:8px;padding:9px 11px;min-height:42px}
button{cursor:pointer}
button.pri{background:#0e6b55;border-color:#0e6b55;color:#fff;font-weight:600}
button.link{border:0;background:none;color:var(--brand);padding:6px;min-height:0;font-weight:600}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:10px}
.tx{display:flex;gap:10px;padding:10px 2px;border-bottom:1px solid var(--line);cursor:pointer;align-items:center}
.tx:last-child{border:0}
.tx .n{overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;word-break:break-word}
.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:20px;border:1px solid var(--line);color:var(--mute)}
.chip.none{background:var(--warnbg);color:var(--warn);border-color:transparent}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:10px}
.stats .card{margin:0}.stats b{display:block;font-size:20px}
.warn{background:var(--warnbg);color:var(--warn);border-color:transparent}
.bar{height:8px;border-radius:8px;background:var(--line);overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--brand)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
#rtable th:first-child,#rtable td:first-child{position:sticky;left:0;background:var(--card)}
tr.grp td{font-weight:700;background:var(--bg)}tr.tot td{font-weight:700}
tr.go{cursor:pointer}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--card);border-top:1px solid var(--line)}
nav button{flex:1;border:0;border-radius:0;background:none;padding:14px 2px;color:var(--mute);font-weight:600;font-size:14px}
nav button.on{color:var(--brand);box-shadow:inset 0 3px 0 var(--brand)}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);
width:min(520px,94vw);padding:16px}
dialog::backdrop{background:rgba(0,0,0,.45)}
dialog label{display:block;margin:10px 0 4px;font-size:13px;color:var(--mute)}
dialog select,dialog input[type=text]{width:100%}
dialog input[type=checkbox]{min-height:0}
body{overflow-x:hidden}
.pick{display:flex;align-items:center;padding:10px 2px 10px 12px;margin:-10px 0;cursor:pointer}
.pick input{width:22px;height:22px;min-height:0;padding:0;accent-color:#0e6b55}
#selbar{position:fixed;left:0;right:0;bottom:52px;z-index:5;display:none;align-items:center;gap:8px;
padding:8px 12px;background:var(--ink);color:var(--bg)}
#selbar button{min-height:38px;padding:6px 12px}
#selbar .ghost{background:none;border-color:currentColor;color:inherit}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:4px 16px;margin-top:8px;font-size:14px}
.figs div{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dashed var(--line);padding:3px 0}
dialog .two{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
dialog input[type=number],dialog input[type=date]{width:100%}
dialog{max-height:92vh;overflow-y:auto}
@media(max-width:560px){nav{overflow-x:auto}nav button{flex:0 0 auto;min-width:66px;font-size:12px;padding:14px 4px}
#sweep .row{flex-direction:column;align-items:stretch}}
#toast{position:fixed;left:50%;bottom:90px;transform:translateX(-50%);background:var(--ink);
color:var(--bg);padding:10px 16px;border-radius:8px;display:none;max-width:92vw;z-index:9}
</style></head><body>
<header><h1>Handral Books</h1><select id="who" aria-label="Person"></select><select id="fy" aria-label="Financial year"></select></header>
<main>
<section id="banking" class="on">
  <h2>Cash collections from patients</h2>
  <div class="card">
    <div class="filters" style="margin:0">
      <input type="date" id="cs_date" aria-label="Date">
      <input type="number" id="cs_amt" step="0.01" min="0" placeholder="Amount">
      <input type="text" id="cs_note" placeholder="Note (optional)">
      <button class="pri" id="cs_add">Add cash</button></div>
    <p class="mute" style="margin:8px 0 0">Counted as patient receipts in clinic profit and the ITR summary.
    When you deposit this cash in the bank, sort that bank entry as Cash deposit or withdrawal
    so it is not counted twice.</p></div>
  <div class="stats" id="cs_stats"></div>
  <div class="card" id="cs_list"></div>
  <h2>Bank accounts</h2><div id="accts"></div>
  <div class="card" id="covcard"><div class="row"><h3 class="grow" style="margin:0">What's missing</h3>
    <button class="link" id="cov_go">Check statements</button></div><div id="cov"></div></div>
  <div class="card"><div class="row">
    <input id="newacct" class="grow" type="text" placeholder="New account name, e.g. HDFC 6324">
    <select id="newowner" aria-label="Owner"></select>
    <select id="newkind" aria-label="Account type"></select>
    <button class="pri" id="addacct">Add account</button></div></div>
  <h2>Statements from Gmail</h2>
  <div class="card" id="mailcard">
    <p class="mute" style="margin:0 0 8px">Reads HDFC account and credit card statements from your Gmail every
    6 hours and imports them into the matching account. Statements already imported are skipped.</p>
    <div class="filters" style="margin:0">
      <input type="email" id="m_user" placeholder="Gmail address" autocomplete="off">
      <input type="password" id="m_pass" placeholder="Gmail App Password" autocomplete="new-password">
      <label class="mute">Accounts to import <input type="text" id="m_accts" placeholder="e.g. 6324 7177 9867" title="Last 4 digits of the bank accounts to import"></label>
      <label class="mute">Daughter's <input type="text" id="m_daughter" placeholder="e.g. 9867" size="8" title="Accounts with these digits are marked as Daughter's"></label>
      <label class="mute">From <input type="date" id="m_since" aria-label="Import from"></label></div>
    <textarea id="m_pdfpw" rows="3" style="width:100%;margin-top:8px;font:inherit;-webkit-text-security:disc"
      placeholder="Statement passwords, one per line (Customer ID; NAME + first 4 digits of Customer ID; NAME + DDMM for the card; your daughter's too)" autocomplete="off"></textarea>
    <div class="row" style="margin-top:8px"><span class="mute grow" id="m_status"></span>
      <button id="m_save">Save</button><button class="pri" id="m_fetch">Fetch now</button></div>
    <details style="margin-top:8px"><summary class="mute">How to get a Gmail App Password</summary>
      <p class="mute">Open myaccount.google.com/apppasswords on your phone or laptop (2-Step Verification
      must be on), type the name Handral Books, press Create and copy the 16-letter password here.
      It only lets this app read mail; you can remove it there at any time.</p></details>
    <div id="m_log" style="margin-top:8px"></div></div>
  <input type="file" id="file" hidden accept=".csv,.txt,.pdf,.xls,.xlsx">
  <input type="file" id="pfile" hidden accept=".csv,.txt,.pdf,.xls,.xlsx">
</section>
<section id="sort">
  <div class="card"><div id="prog"></div><div class="bar"><i id="progbar"></i></div></div>
  <p class="mute">Unsorted entries are grouped by who paid or was paid, biggest first. Tap a
  group to put all its entries under one head. Do the known names first, then use the
  button at the bottom for the remaining patient payments.</p>
  <div class="filters">
    <select id="s_account"></select>
    <select id="s_dir"><option value="out">Money out</option><option value="in">Money in</option></select>
  </div>
  <div class="row" style="margin:-4px 2px 6px"><span class="mute grow">Tick the box on several names to sort them together.</span>
    <button class="link" id="s_all">Select all</button></div>
  <div class="card" id="glist"></div>
  <div class="card" id="sweep"></div>
</section>
<section id="txns">
  <div class="filters">
    <select id="f_account"></select><select id="f_month"></select>
    <select id="f_cat"></select>
    <select id="f_dir"><option value="">Money in and out</option>
      <option value="in">Money in</option><option value="out">Money out</option></select>
    <input id="f_q" type="text" placeholder="Search narration">
  </div>
  <div class="card"><div class="row">
    <div class="grow mute" id="tsum"></div>
    <button id="bulk">Categorise all shown</button></div></div>
  <div class="card" id="tlist"></div>
  <button id="more" hidden>Show more</button>
</section>
<section id="loans">
  <div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Loans</h2>
    <button class="pri" id="ln_add">Add loan</button></div>
  <p class="mute">Add each loan once (car, jewel, personal, home and so on) with the text your bank uses
  for it in entries, such as CAR LOAN, JLOTH or the loan account number. Its EMIs and disbursements are
  then kept under that loan on every import. For exact interest, enter it from the lender's interest
  certificate; otherwise it is estimated from the rate and EMI.</p>
  <div class="stats" id="ln_stats"></div>
  <div class="card scroll" id="ln_order" hidden></div>
  <div id="ln_list"></div>
  <div class="card scroll" id="ln_types" hidden></div>
</section>
<section id="insurance">
  <div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Insurance</h2>
    <button class="pri" id="po_add">Add policy</button></div>
  <p class="mute">Add each policy once (term, LIC, health, vehicle, clinic) with the text your bank uses for
  its premium, such as the insurer's name or the policy number. Premiums are then filed under the right
  head on every import, renewals are tracked, and 80C and 80D are worked out.</p>
  <div id="po_due"></div>
  <div class="stats" id="po_stats"></div>
  <div id="po_list"></div>
  <div class="card scroll" id="po_types" hidden></div>
</section>
<section id="staff">
  <h2>Staff salaries</h2>
  <p class="mute">Add each staff member once. Salary payments to them are filed under Staff salaries
  automatically, now and on every future import.</p>
  <div class="card"><div class="filters" style="margin:0">
    <input id="st_name" type="text" placeholder="Staff name">
    <input id="st_role" type="text" placeholder="Role, e.g. Assistant (optional)">
    <input id="st_match" type="text" placeholder="Name in bank entries; several spellings: separate with commas">
    <button class="pri" id="st_add">Add staff</button></div>
    <p class="mute" style="margin:8px 0 0">Banks often shorten names, and each bank differently. For another spelling, add the same name again with that spelling. If no payments are found, search
    the name in Entries and enter the spelling used there in the third box.</p></div>
  <div class="card" id="stlist"></div>
  <div class="card scroll" id="stmonths" hidden></div>
</section>
<section id="consult">
  <h2>Visiting consultants</h2>
  <p class="mute">Add each consultant once. Payments to them are filed under Consultant fees
  automatically, now and on every future import.</p>
  <div class="card"><div class="filters" style="margin:0">
    <input id="c_name" type="text" placeholder="Consultant name">
    <input id="c_match" type="text" placeholder="Name in bank entries; several spellings: separate with commas">
    <button class="pri" id="c_add">Add consultant</button></div>
    <p class="mute" style="margin:8px 0 0">Banks often shorten names, and each bank differently. For another spelling, add the same name again with that spelling. If no payments are found, search
    the name in Entries and enter the spelling used there in the second box.</p></div>
  <div class="card" id="clist"></div>
</section>
<section id="trading">
  <h2>Trading accounts</h2>
  <p class="mute">For each broker and year, copy the totals from its Tax P&amp;L report
  (Zerodha Console, Fyers, Kotak, Angel One and so on). Enter losses as negative numbers.
  Money moved between your banks and the broker is filled in from bank entries sorted as
  Trading transfer.</p>
  <div class="stats" id="tstats"></div>
  <div id="tacc"></div>
  <div class="card"><div class="row">
    <input id="t_new" class="grow" type="text" placeholder="New trading account, e.g. Zerodha">
    <select id="t_owner" aria-label="Owner"></select>
    <button class="pri" id="t_add">Add trading account</button></div></div>
</section>
<section id="assets">
  <div class="row" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Investments and property</h2>
    <button class="pri" id="as_add">Add investment</button></div>
  <div class="stats" id="astats"></div>
  <div class="card" id="mfcard">
    <div class="row"><h3 class="grow" style="margin:0">Mutual fund statements</h3>
      <select id="mf_owner" aria-label="Whose statement"></select>
      <button class="pri" id="mf_up">Upload statement</button></div>
    <p class="mute" style="margin:6px 0">CAMS or KFintech statement (CAS PDF, password usually your PAN in
    capitals), a capital gains statement, or the transaction list from Groww, Zerodha Coin or Kuvera
    (Excel or CSV). Sales are matched against the oldest units first to work out short and long term.</p>
    <div id="mf_gains"></div><div id="mf_hold"></div><div id="mf_ups"></div>
    <input type="file" id="mffile" hidden accept=".pdf,.xls,.xlsx,.csv">
  </div>
  <div id="alist"></div>
  <p class="mute" id="abank"></p>
  <p class="mute">Short or long term is worked out from the type and how long it was held
  (equity 12 months, property, gold and others 24 months, debt funds always slab rate).
  Your CA confirms the final tax treatment.</p>
</section>
<section id="rules">
  <h2>Rules</h2>
  <p class="mute">Rules categorise matching entries automatically on every import. They only
  touch unsorted entries.</p>
  <div class="card"><div class="filters" style="margin:0">
    <input id="r_pat" type="text" placeholder="Narration contains">
    <select id="r_dir"><option value="any">In or out</option>
      <option value="in">Money in only</option><option value="out">Money out only</option></select>
    <select id="r_cat"></select><select id="r_clinic"></select>
    <button class="pri" id="r_add">Add rule</button></div></div>
  <div class="card" id="pr_card"><div class="row"><div class="grow"><b>Payments from patients</b>
    <div class="mute">Money coming into any of your bank accounts (not your daughter's), in every year, from anyone not listed below is filed as
    Patient receipts (professional income), up to the amount given. Entries you sorted yourself and those caught by
    the rules below are left alone, as are cash deposits, interest, refunds, loans and dividends.</div></div>
    <label class="mute"><input type="checkbox" id="pr_on"> On</label></div>
    <div class="filters" style="margin:8px 0 0">
      <label class="mute">Up to ₹ <input type="number" id="pr_max" min="0" step="1" style="width:9em"></label>
      <label class="mute grow">Not from (family, own accounts) <input type="text" id="pr_ex" style="width:100%"></label>
      <button class="pri" id="pr_save">Save and apply</button></div>
    <div class="mute" id="pr_stat" style="margin-top:6px"></div>
    <div class="row" style="margin-top:14px"><div class="grow"><b>Small payments</b>
    <div class="mute">Money going out of any bank account or card, in every year, below this amount is filed as
    Personal (drawings), unless a rule, staff, consultant or loan already sorts it.</div></div>
    <label class="mute"><input type="checkbox" id="sm_on"> On</label></div>
    <div class="filters" style="margin:8px 0 0">
      <label class="mute">Below ₹ <input type="number" id="sm_max" min="0" step="1" style="width:9em"></label>
      <span class="mute grow" id="sm_stat"></span></div></div>
  <div class="card"><div class="row"><div class="grow"><b>Share rules</b><div class="mute">Save all rules, staff and
    consultants to a file, or add the ones in a file you were given. Adding sorts matching unsorted entries
    straight away; nothing already sorted is changed.</div></div>
    <button id="r_export">Download rules</button><button class="pri" id="r_import">Upload rules file</button></div>
    <input type="file" id="rfile" hidden accept=".json"></div>
  <div class="card" id="rlist"></div>
</section>
<section id="reports">
  <div class="row" style="margin-bottom:10px">
    <h2 class="grow" style="margin:0" id="rp_title">Summary for ITR</h2>
    <select id="rp_clinic"></select></div>
  <div class="stats" id="rstats"></div>
  <div id="heads"></div>
  <div id="chead"></div>
  <div class="card" id="itrcard">
    <div class="row"><h3 class="grow" style="margin:0">Previous income tax returns</h3>
      <select id="itr_owner" aria-label="Whose return"></select>
      <button class="pri" id="itr_up">Upload ITR</button></div>
    <p class="mute" style="margin:6px 0">Best: the JSON from the income-tax portal (e-File, Income Tax Returns,
    View Filed Returns, Download JSON). The ITR form PDF or ITR-V PDF also works. Each upload is compared
    with this year's books and checked for anything missed.</p>
    <div id="itr_tips"></div>
    <div class="scroll" id="itr_cmp"></div>
    <div id="itr_files"></div>
    <input type="file" id="itrfile" hidden accept=".json,.pdf,.zip">
  </div>
  <div class="card" id="tallycard">
    <div class="row"><h3 class="grow" style="margin:0">Tally view for your CA</h3>
      <button class="pri" id="exp_tally">Download for Tally (XML)</button></div>
    <p class="mute" style="margin:6px 0">Every entry is a Receipt, Payment or Contra voucher against its bank
    ledger; heads are Tally ledgers under Tally groups, and clinics are cost centres. The XML imports
    into TallyPrime (Import, Masters, then Import, Transactions). Uses the year and account chosen under
    Download below.</p>
    <div id="tally_pl"></div><div id="tally_tb"></div>
  </div>
  <div class="card" id="dl">
    <h3>Download</h3>
    <div class="filters" style="margin:8px 0">
      <select id="dl_fy" aria-label="Year to download"></select>
      <select id="dl_bank" aria-label="Bank to download"></select></div>
    <div class="row">
      <button class="pri" id="exp_xlsx">Excel workbook</button>
      <button id="exp_sum">ITR summary CSV</button>
      <button id="export">All entries CSV</button></div>
    <p class="mute" style="margin:8px 0 0">The Excel workbook has the Tally Profit &amp; Loss A/c, Trial Balance and
    Day Book, the ITR summary, income and
    expense by head, clinic vs personal, month-wise profit, consultants, and every entry.
    Pick one account for a separate file. The person chosen at the top applies to downloads too.</p></div>
  <h2>Clinic profit by month</h2>
  <div class="card scroll"><table id="rtable"></table></div>
  <p class="mute">Figures cover bank entries only. Cash from patients that was never deposited is
  not included. Trading profit or loss comes from your broker statements, not from these
  transfers. Your CA decides the final heads and deductions.</p>
</section>
</main>
<nav>
  <button data-tab="banking" class="on">Banking</button>
  <button data-tab="sort">Sort</button>
  <button data-tab="txns">Entries</button>
  <button data-tab="trading">Trading</button>
  <button data-tab="assets">Assets</button>
  <button data-tab="loans">Loans</button>
  <button data-tab="insurance">Insurance</button>
  <button data-tab="staff">Salaries</button>
  <button data-tab="consult">Consultants</button>
  <button data-tab="rules">Rules</button>
  <button data-tab="reports">ITR</button>
</nav>
<dialog id="dlg">
  <div class="row"><b class="grow" id="d_amt"></b><span class="mute" id="d_date"></span></div>
  <p id="d_narr" style="word-break:break-word;margin:8px 0"></p>
  <label for="d_cat">Ledger</label><select id="d_cat"></select>
  <label for="d_clinic">Clinic</label><select id="d_clinic"></select>
  <label for="d_note">Note</label><input id="d_note" type="text">
  <label><input type="checkbox" id="d_mk"> Also make a rule for narrations containing</label>
  <input id="d_pat" type="text">
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button id="d_cancel">Cancel</button><button class="pri" id="d_save">Save</button></div>
</dialog>
<dialog id="adlg">
  <h3>Edit account</h3>
  <label for="a_name">Name</label><input type="text" id="a_name">
  <label for="a_owner">Owner</label><select id="a_owner"></select>
  <label for="a_kind">Type</label><select id="a_kind"></select>
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button id="a_cancel">Cancel</button><button class="pri" id="a_save">Save</button></div>
</dialog>
<dialog id="bdlg">
  <b id="b_title"></b>
  <label for="b_cat">Ledger</label><select id="b_cat"></select>
  <label for="b_clinic">Clinic</label><select id="b_clinic"></select>
  <label id="b_rulewrap"><input type="checkbox" id="b_rule" checked> Remember these names for future imports</label>
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button id="b_cancel">Cancel</button><button class="pri" id="b_save">Apply to all shown</button></div>
</dialog>
<dialog id="gdlg">
  <div class="row"><b class="grow" id="g_title"></b><span class="num" id="g_amt"></span></div>
  <p class="mute" id="g_sample" style="word-break:break-word;margin:8px 0"></p>
  <label for="g_cat">Ledger</label><select id="g_cat"></select>
  <label for="g_clinic">Clinic</label><select id="g_clinic"></select>
  <label><input type="checkbox" id="g_rule" checked> Remember this for future imports</label>
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button class="link" id="g_view">View entries</button><span class="grow"></span>
    <button id="g_cancel">Cancel</button><button class="pri" id="g_save">Apply</button></div>
</dialog>
<dialog id="tdlg">
  <div class="row"><b class="grow" id="td_title"></b><span class="mute" id="td_fy"></span></div>
  <div id="td_found" class="mute" style="margin-top:8px"></div>
  <div id="td_fields" class="two"></div>
  <label for="td_note">Note</label><input type="text" id="td_note">
  <label for="td_match">Name of this broker in bank entries</label><input type="text" id="td_match">
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button id="td_cancel">Cancel</button><button class="pri" id="td_save">Save</button></div>
</dialog>
<dialog id="asdlg">
  <h3 id="as_title">Investment</h3>
  <label for="as_name">Name</label><input type="text" id="as_name" placeholder="e.g. Parag Parikh Flexi Cap, Plot at Navanagar">
  <div class="two">
    <div><label for="as_type">Type</label><select id="as_type"></select></div>
    <div><label for="as_owner">Owner</label><select id="as_owner"></select></div>
    <div><label for="as_bought">Bought on</label><input type="date" id="as_bought"></div>
    <div><label for="as_cost">Total cost</label><input type="number" step="0.01" id="as_cost"></div>
    <div><label for="as_value">Current value</label><input type="number" step="0.01" id="as_value"></div>
    <div><label for="as_valued">Value as on</label><input type="date" id="as_valued"></div>
    <div><label for="as_sold">Sold on (if sold)</label><input type="date" id="as_sold"></div>
    <div><label for="as_sale">Sale value</label><input type="number" step="0.01" id="as_sale"></div>
  </div>
  <label for="as_note">Note</label><input type="text" id="as_note">
  <div class="row" style="margin-top:14px">
    <button class="link" id="as_del">Delete</button><span class="grow"></span>
    <button id="as_cancel">Cancel</button><button class="pri" id="as_save">Save</button></div>
</dialog>
<dialog id="podlg">
  <h3 id="po_title">Policy</h3>
  <label for="po_name">Name</label><input type="text" id="po_name" placeholder="e.g. HDFC Life term plan, Star Health family floater">
  <div class="two">
    <div><label for="po_type">Type</label><select id="po_type"></select></div>
    <div><label for="po_purpose">Used for</label><select id="po_purpose"><option>Personal</option><option>Clinic</option></select></div>
    <div><label for="po_insurer">Insurer</label><input type="text" id="po_insurer"></div>
    <div><label for="po_policy_no">Policy number</label><input type="text" id="po_policy_no"></div>
    <div><label for="po_owner">Owner</label><select id="po_owner"></select></div>
    <div><label for="po_insured">Covers (health)</label><select id="po_insured"></select></div>
    <div><label for="po_cover">Cover or sum assured</label><input type="number" step="0.01" id="po_cover"></div>
    <div><label for="po_premium">Premium</label><input type="number" step="0.01" id="po_premium"></div>
    <div><label for="po_frequency">Paid</label><select id="po_frequency"></select></div>
    <div><label for="po_due">Next due date</label><input type="date" id="po_due"></div>
  </div>
  <label><input type="checkbox" id="po_senior"> Senior citizen (60 or older) covered, for the higher 80D limit</label>
  <label for="po_match">Text for this premium in bank entries</label>
  <input type="text" id="po_match" placeholder="e.g. STAR HEALTH, HDFC LIFE, or the policy number">
  <div class="two">
    <div><label for="po_closed">Stopped on (if stopped)</label><input type="date" id="po_closed"></div>
    <div><label for="po_note">Note</label><input type="text" id="po_note"></div>
  </div>
  <div class="row" style="margin-top:14px">
    <button class="link" id="po_del">Delete</button><span class="grow"></span>
    <button id="po_cancel">Cancel</button><button class="pri" id="po_save">Save</button></div>
</dialog>
<dialog id="lndlg">
  <h3 id="ln_title">Loan</h3>
  <label for="ln_name">Name</label><input type="text" id="ln_name" placeholder="e.g. HDFC car loan, IOB jewel loan">
  <div class="two">
    <div><label for="ln_type">Type</label><select id="ln_type"></select></div>
    <div><label for="ln_purpose">Used for</label><select id="ln_purpose"></select></div>
    <div><label for="ln_lender">Lender</label><input type="text" id="ln_lender"></div>
    <div><label for="ln_owner">Owner</label><select id="ln_owner"></select></div>
    <div><label for="ln_amount">Amount borrowed</label><input type="number" step="0.01" id="ln_amount"></div>
    <div><label for="ln_start">Taken on</label><input type="date" id="ln_start"></div>
    <div><label for="ln_rate">Interest rate (% a year)</label><input type="number" step="0.01" id="ln_rate"></div>
    <div><label for="ln_emi">EMI (leave empty for jewel loans)</label><input type="number" step="0.01" id="ln_emi"></div>
  </div>
  <label for="ln_match">Text for this loan in bank entries</label>
  <input type="text" id="ln_match" placeholder="e.g. CAR LOAN, JLOTH, or the loan account number">
  <div class="two">
    <div><label for="ln_closed">Closed on (if closed)</label><input type="date" id="ln_closed"></div>
    <div><label for="ln_note">Note</label><input type="text" id="ln_note"></div>
  </div>
  <h3 style="margin-top:14px" id="ln_fytitle"></h3>
  <div class="two">
    <div><label for="ln_interest">Interest paid (from certificate)</label><input type="number" step="0.01" id="ln_interest" placeholder="empty: estimate"></div>
    <div><label for="ln_outstanding">Outstanding at year end</label><input type="number" step="0.01" id="ln_outstanding" placeholder="empty: estimate"></div>
  </div>
  <div class="row" style="margin-top:14px">
    <button class="link" id="ln_del">Delete</button><span class="grow"></span>
    <button id="ln_cancel">Cancel</button><button class="pri" id="ln_save">Save</button></div>
</dialog>
<dialog id="ifdlg">
  <div class="row"><b class="grow" id="if_title"></b><button id="if_close">Close</button></div>
  <input type="text" id="if_q" placeholder="Search, e.g. 80C or Receipt" style="width:100%;margin:8px 0">
  <div id="if_list" style="max-height:60vh;overflow:auto"></div>
</dialog>
<div id="selbar"><span class="grow" id="seltext"></span>
  <button class="ghost" id="selclear">Clear</button><button class="pri" id="selgo">Sort selected</button></div>
<div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const inr=n=>Number(n||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
const r0=n=>Math.round(n||0).toLocaleString('en-IN');
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
let S={accounts:[],rules:[],cats:[],groups:[],clinics:[],years:[],owners:[],kinds:[]};let editAcct=null;
let cur=null,grp=null,offset=0,shown=0,importAcct=null,total=0,payeeFilter='',yearPicked=false;
let selKind='',sel=new Map(),bulkMode='filter';
function showSel(){const n=sel.size,bar=$('selbar');
  bar.style.bottom=document.querySelector('nav').offsetHeight+'px';bar.style.display=n?'flex':'none';
  if(selKind==='group'){const e=[...sel.values()].reduce((a,g)=>a+g.n,0);
    $('seltext').textContent=n+(n>1?' names':' name')+' selected ('+e+(e>1?' entries)':' entry)');}
  else $('seltext').textContent=n+(n>1?' entries':' entry')+' selected';}
function clearSel(){sel.clear();selKind='';document.querySelectorAll('.pick input').forEach(c=>c.checked=false);showSel();}
function pickBox(kind,key,val){
  const l=document.createElement('label');l.className='pick';
  const c=document.createElement('input');c.type='checkbox';c.setAttribute('aria-label','Select');l.appendChild(c);
  c.onchange=()=>{if(selKind!==kind){sel.clear();selKind=kind;}
    if(c.checked)sel.set(key,val);else sel.delete(key);showSel();};
  return l;}

function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';
  clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display='none',4500);}
async function api(path,body){
  const o=body===undefined?{}:{method:'POST',body:body instanceof Blob?body:JSON.stringify(body)};
  const r=await fetch(path,o);const j=await r.json();
  if(j.error)throw new Error(j.error);return j;}
const LED=c=>{const t=(S.tally||[]).find(x=>x[0]===c);return t?t[1]:(c||'Suspense A/c');};
const VCH=t=>{const g=((S.tally||[]).find(x=>x[0]===t.category)||[])[2];
  return g==='Bank Accounts'||g==='Cash-in-Hand'?'Contra':t.credit>0?'Receipt':'Payment';};
function catOptions(first){
  let h=first||'';
  for(const g of S.tally_groups||[]){const items=S.tally.filter(t=>t[2]===g);if(!items.length)continue;
    h+='<optgroup label="'+esc(g)+'">'+items.map(t=>'<option value="'+esc(t[0])+'">'+esc(t[1])+'</option>').join('')+'</optgroup>';}
  return h;}
function clinicOptions(first){return (first||'')+S.clinics.map(c=>'<option>'+esc(c)+'</option>').join('');}
function fyMonths(){const y=+$('fy').value,o=[];
  for(let i=0;i<12;i++){const m=(i+3)%12,yy=m<3?y+1:y;
    o.push([yy+'-'+String(m+1).padStart(2,'0'),MON[m]+' '+yy]);}return o;}
const yy=y=>String(y).slice(2);
const fyLabel=y=>'FY '+y+'-'+yy(y+1)+' (AY '+(y+1)+'-'+yy(y+2)+')';
function keepValue(id,html){const el=$(id),v=el.value;el.innerHTML=html;el.value=v;
  if(el.selectedIndex<0)el.selectedIndex=0;}

async function loadState(){
  S=await api('/api/state');
  const now=new Date(),thisFy=now.getMonth()>=3?now.getFullYear():now.getFullYear()-1;
  const years=[...new Set([...S.years,thisFy,thisFy-1,thisFy-2,thisFy-3,thisFy-4,thisFy-5])].sort((a,b)=>b-a);
  const want=yearPicked&&years.includes(+$('fy').value)?+$('fy').value:(S.years[0]||thisFy);
  $('fy').innerHTML=years.map(y=>'<option value="'+y+'">'+fyLabel(y)+'</option>').join('');
  $('fy').value=want;
  const ow=S.owners.map(o=>'<option>'+esc(o)+'</option>').join(''),kd=S.kinds.map(o=>'<option>'+esc(o)+'</option>').join('');
  keepValue('who','<option value="">Everyone</option>'+ow);keepValue('newowner',ow);keepValue('newkind',kd);
  $('a_owner').innerHTML=ow;$('a_kind').innerHTML=kd;keepValue('t_owner',ow);keepValue('as_owner',ow);
  $('dl_fy').innerHTML=years.map(y=>'<option value="'+y+'">'+fyLabel(y)+'</option>').join('')+'<option value="">All years together</option>';
  $('dl_fy').value=want;
  keepValue('dl_bank','<option value="">All accounts</option>'+S.accounts.filter(a=>!$('who').value||a.owner===$('who').value).map(a=>'<option value="'+a.id+'">'+esc(a.name)+' only</option>').join(''));
  $('accts').innerHTML=S.accounts.length?S.accounts.map(a=>
    '<div class="card"><div class="row"><div class="grow"><b>'+esc(a.name)+'</b> <span class="chip">'+esc(a.owner)+'</span> <span class="chip">'+esc(a.kind)+'</span><div class="mute">'+
    (a.n?a.n+' entries, '+a.first+' to '+a.last:'No statement imported yet')+'</div></div>'+
    (a.n?'<div class="num"><b>'+inr(a.balance)+'</b></div>':'')+'</div>'+
    '<div class="row" style="margin-top:8px">'+
    (a.open?'<span class="chip none">'+a.open+' unsorted</span>':(a.n?'<span class="chip">All sorted</span>':''))+
    '<span class="grow"></span><button class="link" data-view="'+a.id+'">View</button>'+
    '<button class="link" data-edit="'+a.id+'">Edit</button>'+
    '<button class="link" data-del="'+a.id+'">Delete</button>'+
    (a.kind==='Cash'?'':a.kind==='Trading'?'<button class="pri" data-pimp="'+a.id+'">Import P&amp;L report</button>'
      :'<button class="pri" data-imp="'+a.id+'">Import statement</button>')+'</div></div>').join('')
    :'<div class="card mute">Add your first bank account below, then import its statement.</div>';
  const acctOpts='<option value="">All accounts</option>'+
    S.accounts.filter(a=>!$('who').value||a.owner===$('who').value).map(a=>'<option value="'+a.id+'">'+esc(a.name)+'</option>').join('');
  keepValue('f_account',acctOpts);keepValue('s_account',acctOpts);
  for(const id of ['d_cat','b_cat','r_cat','g_cat'])
    keepValue(id,catOptions(id==='d_cat'?'<option value="">Suspense A/c (not sorted)</option>':''));
  for(const id of ['d_clinic','b_clinic','r_clinic','g_clinic'])
    keepValue(id,clinicOptions('<option value="">No clinic</option>'));
  keepValue('f_cat',catOptions('<option value="">All ledgers</option><option value="__none__">Suspense A/c (not sorted)</option>'));
  keepValue('rp_clinic',clinicOptions('<option value="">All clinics</option>'));
  keepValue('f_month','<option value="">Whole year</option>'+
    fyMonths().map(m=>'<option value="'+m[0]+'">'+m[1]+'</option>').join(''));
  $('rlist').innerHTML=S.rules.length?S.rules.map(r=>
    '<div class="tx" style="cursor:default"><div class="grow"><b>'+esc(r.pattern)+'</b> <span class="mute">'+
    (r.field==='payee'?'name is exactly this, ':'narration contains this, ')+
    ({any:'in or out',in:'money in',out:'money out'}[r.dir]||'')+'</span><div class="mute">'+esc(LED(r.category))+
    (r.clinic?', '+esc(r.clinic):'')+'</div></div><button class="link" data-rdel="'+r.id+'">Delete</button></div>').join('')
    :'<span class="mute">No rules yet.</span>';
}
function filter(){return {owner:$('who').value,fy:$('fy').value,account:$('f_account').value,month:$('f_month').value,
  cat:$('f_cat').value,dir:$('f_dir').value,q:$('f_q').value.trim(),payee:payeeFilter};}
const qs=o=>Object.entries(o).filter(e=>e[1]).map(e=>e[0]+'='+encodeURIComponent(e[1])).join('&');

async function loadSort(){
  clearSel();
  const dir=$('s_dir').value,f={owner:$('who').value,fy:$('fy').value,account:$('s_account').value};
  const d=await api('/api/groups?'+qs(f));
  const done=d.total-d.open,pct=d.total?Math.round(done*100/d.total):0;
  $('prog').innerHTML=d.total?'<b>'+done+' of '+d.total+' entries sorted</b> <span class="mute">('+d.open+' left)</span>'
    :'<span class="mute">No entries for this year. Import a statement from Banking, or pick another year at the top.</span>';
  $('progbar').style.width=pct+'%';
  const rows=d.rows.filter(g=>g.dir===dir),box=$('glist');box.innerHTML='';
  if(!rows.length)box.innerHTML='<span class="mute">'+(d.total?'Nothing left to sort on this side.':'')+'</span>';
  for(const g of rows){
    const el=document.createElement('div');el.className='tx';
    el.innerHTML='<div class="grow"><b>'+esc(g.payee)+'</b><div class="mute">'+g.n+(g.n>1?' entries':' entry')+'</div></div>'+
      '<div class="num '+(g.dir==='in'?'in':'out')+'">'+(g.dir==='in'?'+':'-')+inr(g.total)+'</div>';
    el.appendChild(pickBox('group',g.payee+'|'+g.dir,g));
    el.onclick=e=>{if(!e.target.closest('.pick'))openGroup(g);};box.appendChild(el);}
  const left=d.rows.filter(g=>g.dir==='in'),n=left.reduce((a,g)=>a+g.n,0),amt=left.reduce((a,g)=>a+g.total,0);
  $('sweep').style.display=n?'block':'none';
  $('sweep').innerHTML='<div class="row"><div class="grow"><b>'+n+' unsorted money-in entries</b>, total <span class="num in">'+inr(amt)+
    '</span><div class="mute">Sort transfers, loans and other non-patient money first. Then mark the rest as patient receipts.</div></div>'+
    '<button id="sweepbtn">Mark all as patient receipts</button></div>';
  if(n)$('sweepbtn').onclick=run(async()=>{
    if(!confirm('Mark all '+n+' unsorted money-in entries as Patient receipts?'))return;
    const r=await api('/api/bulk',{filter:{owner:$('who').value,fy:$('fy').value,account:$('s_account').value,cat:'__none__',dir:'in'},
      category:'Patient receipts',clinic:''});
    toast('Marked '+r.changed+' entries as patient receipts');await loadState();await loadSort();});
}
function openGroup(g){grp=g;
  $('g_title').textContent=g.payee+' ('+g.n+(g.n>1?' entries)':' entry)');
  $('g_amt').textContent=(g.dir==='in'?'+':'-')+inr(g.total);$('g_amt').className='num '+(g.dir==='in'?'in':'out');
  $('g_sample').textContent=g.sample;$('g_rule').checked=g.payee!=='OTHER';$('gdlg').showModal();}

async function loadTxns(reset){
  if(reset){offset=0;shown=0;$('tlist').innerHTML='';clearSel();}
  const d=await api('/api/txns?'+qs(filter())+'&offset='+offset);
  total=d.count;
  $('tsum').innerHTML=(payeeFilter?'<b>'+esc(payeeFilter)+'</b> <button class="link" id="clearp">Show everyone</button><br>':'')+
    d.count+' entries. In <span class="in num">'+inr(d.credit)+'</span>, out <span class="out num">'+inr(d.debit)+'</span>';
  if(payeeFilter)$('clearp').onclick=run(()=>{payeeFilter='';return loadTxns(true);});
  if(!d.count)$('tlist').innerHTML='<span class="mute">Nothing matches. Change the filters, pick another year at the top, or import a statement from Banking.</span>';
  const box=$('tlist');
  for(const t of d.rows){
    const el=document.createElement('div');el.className='tx';
    el.innerHTML='<div class="grow"><div class="n">'+esc(t.narration)+'</div><div class="mute">'+t.date+', '+esc(t.account)+
      ' <span class="chip">'+VCH(t)+'</span> <span class="chip '+(t.category?'':'none')+'">'+esc(LED(t.category))+
      (t.clinic?', '+esc(t.clinic):'')+'</span></div></div>'+
      '<div class="num '+(t.credit>0?'in':'out')+'">'+(t.credit>0?'+':'-')+inr(t.credit>0?t.credit:t.debit)+'</div>';
    el.appendChild(pickBox('txn',t.id,t));
    el.onclick=e=>{if(!e.target.closest('.pick'))openTxn(t);};box.appendChild(el);}
  shown+=d.rows.length;offset=shown;
  $('more').hidden=shown>=d.count;$('more').textContent='Show more ('+(d.count-shown)+' left)';
}
function openTxn(t){cur=t;
  $('d_amt').textContent=(t.credit>0?'+':'-')+inr(t.credit>0?t.credit:t.debit);
  $('d_amt').className='grow num '+(t.credit>0?'in':'out');
  $('d_date').textContent=t.date+', '+t.account;$('d_narr').textContent=t.narration;
  $('d_cat').value=t.category;$('d_clinic').value=t.clinic;$('d_note').value=t.note||'';
  $('d_mk').checked=false;$('d_pat').value=t.payee&&t.payee!=='OTHER'?t.payee:t.narration.slice(0,24);$('dlg').showModal();}

async function loadReport(){
  const y=+$('fy').value,f={owner:$('who').value,fy:y,clinic:$('rp_clinic').value};
  $('rp_title').textContent='Summary for ITR, '+fyLabel(y)+($('who').value?', '+$('who').value:'');
  const d=await api('/api/report?'+qs(f)),months=fyMonths(),grpOf={};
  S.cats.forEach(c=>grpOf[c[0]]=c[1]);
  const cat={},cell={},open={n:0,d:0,c:0};
  for(const r of d.rows){
    if(!r.category){open.n+=r.n;open.d+=r.d;open.c+=r.c;continue;}
    const k=cat[r.category]=cat[r.category]||{d:0,c:0,n:0};k.d+=r.d;k.c+=r.c;k.n+=r.n;
    const g=grpOf[r.category],v=g==='expenses'?r.d-r.c:r.c-r.d;
    cell[r.category]=cell[r.category]||{};cell[r.category][r.m]=(cell[r.category][r.m]||0)+v;}
  const inGroup=g=>S.cats.filter(c=>c[1]===g&&cat[c[0]]).map(c=>c[0]);
  const net=(g,sign)=>inGroup(g).reduce((a,c)=>a+sign*(cat[c].c-cat[c].d),0);
  const rec=net('receipts',1),exp=net('expenses',-1),oth=net('other_income',1);
  $('rstats').innerHTML='<div class="card"><span class="mute">Clinic receipts</span><b class="num in">'+inr(rec)+'</b></div>'+
    '<div class="card"><span class="mute">Clinic expenses</span><b class="num out">'+inr(exp)+'</b></div>'+
    '<div class="card"><span class="mute">Clinic profit</span><b class="num">'+inr(rec-exp)+'</b></div>'+
    '<div class="card"><span class="mute">Other income</span><b class="num">'+inr(oth)+'</b></div>'+
    (open.n?'<div class="card warn" id="gosort" style="cursor:pointer"><span>Not yet counted</span><b>'+open.n+
      ' unsorted</b><span class="num">in '+inr(open.c)+', out '+inr(open.d)+'</span></div>':'');
  if(open.n)$('gosort').onclick=()=>document.querySelector('nav [data-tab=sort]').click();
  let h='';
  for(const [g,label] of S.groups){
    const names=inGroup(g);if(!names.length)continue;
    const one=g==='receipts'||g==='other_income'?1:(g==='expenses'||g==='tax'?-1:0);
    h+='<div class="card scroll"><h3>'+esc(label)+'</h3><table><tr><th>Ledger</th><th>Entries</th>'+
      (one?'<th>Amount</th>':'<th>Money in</th><th>Money out</th>')+'</tr>';
    let tn=0,ta=0,tc=0,td=0;
    for(const c of names){const k=cat[c];tn+=k.n;ta+=one*(k.c-k.d);tc+=k.c;td+=k.d;
      h+='<tr class="go" data-cat="'+esc(c)+'"><td>'+esc(LED(c))+'</td><td>'+k.n+'</td>'+
        (one?'<td class="num">'+inr(one*(k.c-k.d))+'</td>':'<td class="num">'+inr(k.c)+'</td><td class="num">'+inr(k.d)+'</td>')+'</tr>';}
    h+='<tr class="tot"><td>Total</td><td>'+tn+'</td>'+(one?'<td class="num">'+inr(ta)+'</td>'
      :'<td class="num">'+inr(tc)+'</td><td class="num">'+inr(td)+'</td>')+'</tr></table></div>';}
  const cs=(await api('/api/consultants?'+qs({owner:$('who').value,fy:y}))).rows.filter(c=>c.n);
  $('chead').innerHTML=cs.length?'<div class="card scroll"><h3>Consultant fees by consultant</h3><table><tr><th>Consultant</th><th>Payments</th><th>Amount</th></tr>'+
    cs.map(c=>'<tr><td>'+esc(c.name)+'</td><td>'+c.n+'</td><td class="num">'+inr(c.total)+'</td></tr>').join('')+
    '<tr class="tot"><td>Total</td><td>'+cs.reduce((a,c)=>a+c.n,0)+'</td><td class="num">'+inr(cs.reduce((a,c)=>a+c.total,0))+'</td></tr></table></div>':'';
  $('heads').innerHTML=h||'<div class="card mute">Nothing sorted yet for this year. Use the Sort tab to put entries under heads.</div>';
  const sum=(c,m)=>(cell[c]||{})[m]||0,tot=(g,m)=>inGroup(g).reduce((a,c)=>a+sum(c,m),0);
  const all=fn=>months.reduce((a,m)=>a+fn(m[0]),0);
  let t='<tr><th>Ledger</th>'+months.map(m=>'<th>'+m[1].slice(0,3)+'</th>').join('')+'<th>Total</th></tr>';
  const line=(name,fn,cls)=>'<tr class="'+(cls||'')+'"><td>'+esc(name)+'</td>'+
    months.map(m=>'<td class="num">'+(fn(m[0])?r0(fn(m[0])):'')+'</td>').join('')+
    '<td class="num"><b>'+r0(all(fn))+'</b></td></tr>';
  for(const [g,label] of [['receipts','Clinic receipts'],['expenses','Clinic expenses']]){
    t+='<tr class="grp"><td colspan="14">'+label+'</td></tr>';
    for(const c of inGroup(g))t+=line(LED(c),m=>sum(c,m));
    t+=line('Total',m=>tot(g,m),'tot');}
  t+=line('Clinic profit',m=>tot('receipts',m)-tot('expenses',m),'tot');
  $('rtable').innerHTML=t;
  await loadItr();
  await loadTally();
}
async function loadTally(){
  const d=await api('/api/tally?'+qs({owner:$('who').value,fy:$('fy').value})),p=d.pnl;
  const rows=(title,items)=>items.length?'<tr class="grp"><td colspan="2">'+esc(title)+'</td></tr>'+
    items.map(i=>'<tr><td>&nbsp;&nbsp;'+esc(i[0])+'</td><td class="num">'+inr(i[1])+'</td></tr>').join(''):'';
  const line=(t,v)=>'<tr class="tot"><td>'+t+'</td><td class="num">'+inr(v)+'</td></tr>';
  const col=html=>'<div style="flex:1;min-width:280px"><table>'+html+'</table></div>';
  $('tally_pl').innerHTML='<h3 style="margin-top:12px">Profit &amp; Loss A/c, '+fyLabel(+$('fy').value).split(' (')[0]+'</h3>'+
    '<div class="row" style="align-items:flex-start;gap:16px">'+
    col('<tr><th>Particulars (Dr)</th><th>Amount</th></tr>'+rows('Purchase Accounts',p.purchases)+rows('Direct Expenses',p.direct_exp)+
      (p.gross>=0?line('Gross Profit c/o',p.gross):'')+(p.gross<0?line('Gross Loss b/f',-p.gross):'')+rows('Indirect Expenses',p.indirect_exp)+
      (p.net>=0?line('Net Profit',p.net):''))+
    col('<tr><th>Particulars (Cr)</th><th>Amount</th></tr>'+rows('Direct Incomes',p.direct_inc)+(p.gross<0?line('Gross Loss c/o',-p.gross):'')+
      (p.gross>=0?line('Gross Profit b/f',p.gross):'')+rows('Indirect Incomes',p.indirect_inc)+(p.net<0?line('Net Loss',-p.net):''))+'</div>';
  let dr=0,cr=0,t='<tr><th>Particulars</th><th>Debit</th><th>Credit</th></tr>';
  for(const [g,items] of d.tb){const gd=items.filter(i=>i[1]>0).reduce((a,i)=>a+i[1],0),gc=-items.filter(i=>i[1]<0).reduce((a,i)=>a+i[1],0);dr+=gd;cr+=gc;
    t+='<tr class="grp"><td>'+esc(g)+'</td><td class="num">'+(gd?inr(gd):'')+'</td><td class="num">'+(gc?inr(gc):'')+'</td></tr>'+
      items.map(i=>'<tr><td>&nbsp;&nbsp;'+esc(i[0])+'</td><td class="num">'+(i[1]>0?inr(i[1]):'')+'</td><td class="num">'+(i[1]<0?inr(-i[1]):'')+'</td></tr>').join('');}
  t+='<tr class="tot"><td>Grand Total</td><td class="num">'+inr(dr)+'</td><td class="num">'+inr(cr)+'</td></tr>';
  $('tally_tb').innerHTML=d.tb.length?'<details style="margin-top:12px"><summary><b>Trial Balance</b> <span class="mute">(movement for the year; opening balances not included)</span></summary>'+
    '<div class="scroll"><table>'+t+'</table></div></details>'+(d.suspense?'<p class="mute">Suspense A/c has entries not yet sorted; post them in the Sort tab.</p>':''):'';
}
let IF=[];
async function loadItr(){
  const d=await api('/api/itr?'+qs({owner:$('who').value,fy:$('fy').value}));
  keepValue('itr_owner',S.owners.map(o=>'<option'+(o===($('who').value||'Self')?' selected':'')+'>'+esc(o)+'</option>').join(''));
  $('itr_tips').innerHTML=d.insights.length?'<div class="card warn" style="margin:8px 0"><b>Checks from your last return</b><ul style="margin:6px 0 0;padding-left:20px">'+
    d.insights.map(t=>'<li style="margin:4px 0">'+esc(t)+'</li>').join('')+'</ul></div>':'';
  $('itr_cmp').innerHTML=d.returns.length?'<table><tr><th>Figure</th>'+d.columns.map(c=>'<th>'+esc(c.title)+'</th>').join('')+'</tr>'+
    d.compare.map(r=>'<tr><td>'+esc(r[0])+'</td>'+r.slice(1).map(v=>'<td class="num">'+(v==null?'':inr(v))+'</td>').join('')+'</tr>').join('')+'</table>':'';
  $('itr_files').innerHTML=d.returns.map(r=>'<div class="tx" style="cursor:default"><div class="grow"><b>AY '+r.ay+'-'+String(r.ay+1).slice(2)+'</b> '+
    (r.form?'<span class="chip">'+esc(r.form)+'</span> ':'')+(r.scheme?'<span class="chip">'+esc(r.scheme)+'</span> ':'')+'<span class="chip">'+esc(r.owner)+'</span>'+
    '<div class="mute">'+esc([r.name,r.pan,r.filename,'uploaded '+r.uploaded].filter(Boolean).join(', '))+'</div></div>'+
    '<button class="link" data-ifig="'+r.id+'">All figures</button><button class="link" data-ifile="'+r.id+'">Download</button>'+
    '<button class="link" data-idel="'+r.id+'">Delete</button></div>').join('');
}
function showFigures(){const q=$('if_q').value.toLowerCase();
  $('if_list').innerHTML='<table>'+IF.filter(r=>!q||String(r[0]).toLowerCase().includes(q)).slice(0,500)
    .map(r=>'<tr><td style="white-space:normal;word-break:break-word">'+esc(r[0])+'</td><td class="num">'+esc(r[1])+'</td></tr>').join('')+'</table>';}
function renderPeople(box,rows,kind,head,empty){box.innerHTML='';
  if(!rows.length){box.innerHTML='<span class="mute">'+empty+'</span>';return;}
  for(const c of rows){
    const el=document.createElement('div');el.className='tx';
    el.innerHTML='<div class="grow"><b>'+esc(c.name)+'</b>'+(c.role?' <span class="chip">'+esc(c.role)+'</span>':'')+
      '<div class="mute">'+c.n+(c.n===1?' payment':' payments')+' this year, matched on "'+esc(c.match)+'"</div></div>'+
      '<div class="num out">'+inr(c.total)+'</div><button class="link" data-pdel="1">Remove</button>';
    el.onclick=run(async e=>{
      if(e.target.dataset.pdel){if(!confirm('Remove '+c.name+' from the list? Entries already filed stay as they are.'))return;
        await api('/api/'+kind+'/delete',{id:c.id});return refresh();}
      payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';
      await go('txns');$('f_cat').value=head;$('f_q').value=c.match;await loadTxns(true);});
    box.appendChild(el);}
  box.insertAdjacentHTML('beforeend','<div class="tx" style="cursor:default"><b class="grow">Total this year</b><b class="num">'+
    inr(rows.reduce((a,c)=>a+c.total,0))+'</b></div>');
}
async function loadConsult(){
  const d=await api('/api/consultants?'+qs({owner:$('who').value,fy:$('fy').value}));
  renderPeople($('clist'),d.rows,'consultant','Consultant fees','No consultants added yet. Add a name above, or in the Sort tab choose Consultant fees for a name and it is added here.');
}
async function loadStaff(){
  const d=await api('/api/staff?'+qs({owner:$('who').value,fy:$('fy').value}));
  renderPeople($('stlist'),d.rows,'staff','Staff salaries','No staff added yet. Add a name above, or in the Sort tab choose Staff salaries for a name and it is added here.');
  const paid=d.rows.filter(c=>c.n),months=fyMonths(),box=$('stmonths');box.hidden=!paid.length;if(!paid.length)return;
  const cell=v=>'<td class="num">'+(v?r0(v):'')+'</td>';
  box.innerHTML='<h3>Month by month</h3><table><tr><th>Staff</th>'+months.map(m=>'<th>'+m[1].slice(0,3)+'</th>').join('')+'<th>Total</th></tr>'+
    paid.map(c=>'<tr><td>'+esc(c.name)+'</td>'+months.map(m=>cell(c.months[m[0]])).join('')+'<td class="num"><b>'+r0(c.total)+'</b></td></tr>').join('')+
    '<tr class="tot"><td>Total</td>'+months.map(m=>cell(paid.reduce((a,c)=>a+(c.months[m[0]]||0),0))).join('')+
    '<td class="num">'+r0(paid.reduce((a,c)=>a+c.total,0))+'</td></tr></table>';
}
let C=null;
const today=()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');};
async function loadCash(){
  C=await api('/api/cash?'+qs({fy:$('fy').value}));
  if(!$('cs_date').value)$('cs_date').value=today();
  const m=today().slice(0,7);
  $('cs_stats').innerHTML=stat('Cash this year',C.total)+stat('Cash this month',C.by_month[m]||0)+
    '<div class="card"><span class="mute">Entries this year</span><b>'+C.count+'</b></div>';
  const box=$('cs_list');
  if(!C.rows.length){box.innerHTML='<span class="mute">No cash entries for '+fyLabel(+$('fy').value)+' yet.</span>';return;}
  const months=fyMonths().filter(x=>C.by_month[x[0]]);
  box.innerHTML='<div class="row"><h3 class="grow" style="margin:0">Recent cash entries</h3>'+
    (C.account?'<button class="link" id="cs_all">View all in Entries</button>':'')+'</div>'+
    C.rows.slice(0,20).map(r=>'<div class="tx" style="cursor:default"><div class="grow"><b>'+r.date+'</b>'+
      '<div class="mute">'+esc(r.note||r.narration.replace(/^Cash collection( - )?/,''))+'</div></div><div class="num in">+'+inr(r.credit)+'</div>'+
      '<button class="link" data-csdel="'+r.id+'">Delete</button></div>').join('')+
    (months.length>1?'<h3 style="margin-top:12px">By month, this year</h3><table>'+months.map(x=>'<tr><td>'+x[1]+'</td><td class="num">'+inr(C.by_month[x[0]])+'</td></tr>').join('')+'</table>':'');
  if(C.account)$('cs_all').onclick=run(async()=>{payeeFilter='';$('f_month').value='';$('f_cat').value='';$('f_dir').value='';$('f_q').value='';
    await go('txns');$('f_account').value=C.account;await loadTxns(true);});
}
let T=null,tAcct=null,A=null,aCur=null;
const signed=n=>'<span class="num '+(n<0?'out':'in')+'">'+inr(n)+'</span>';
const stat=(label,v,col)=>'<div class="card"><span class="mute">'+label+'</span><b class="num'+(col?(v<0?' out':' in'):'')+'">'+inr(v)+'</b></div>';
async function loadTrading(){
  const y=$('fy').value;
  T=await api('/api/trading?'+qs({owner:$('who').value,fy:y}));
  const tot=k=>T.rows.reduce((a,r)=>a+(r[k]||0),0);
  $('tstats').innerHTML=T.rows.length?stat('Net trading P&amp;L',tot('net'),1)+stat('Intraday and F&amp;O',tot('intraday')+tot('fno'),1)+
    stat('Capital gains',tot('stcg')+tot('ltcg'),1)+stat('Added from bank',tot('added'))+stat('Withdrawn to bank',tot('withdrawn')):'';
  $('tacc').innerHTML=T.rows.length?T.rows.map(r=>
    '<div class="card"><div class="row"><div class="grow"><b>'+esc(r.name)+'</b> <span class="chip">'+esc(r.owner)+'</span>'+
    '<div class="mute">'+fyLabel(+y)+'</div></div><div style="text-align:right"><span class="mute">Net P&amp;L</span><br><b>'+signed(r.net)+'</b></div></div>'+
    '<div class="figs">'+T.fields.map(f=>'<div><span class="mute">'+esc(f[1])+'</span>'+(!r[f[0]]?'<span class="mute">-</span>':
      f[0]==='charges'||f[0]==='turnover'?'<span class="num">'+inr(r[f[0]])+'</span>':signed(r[f[0]]))+'</div>').join('')+'</div>'+
    '<div class="row" style="margin-top:8px"><span class="mute grow">Bank to broker: added '+inr(r.added)+', withdrawn '+inr(r.withdrawn)+
    (r.match?' (matched on "'+esc(r.match)+'")':'')+(r.note?'<br>'+esc(r.note):'')+'</span>'+
    '<button data-timp="'+r.id+'">Import P&amp;L report</button><button class="pri" data-tedit="'+r.id+'">Enter figures</button></div></div>').join('')
    :'<div class="card mute">No trading accounts yet. Add one below for each broker (Zerodha, Fyers, Kotak and so on).</div>';
}
function openTrade(id,imp){tAcct=T.rows.find(r=>r.id==id);
  $('td_title').textContent=tAcct.name;$('td_fy').textContent=fyLabel(+$('fy').value);
  $('td_fields').innerHTML=T.fields.map(f=>'<div><label for="tf_'+f[0]+'">'+esc(f[1])+'</label>'+
    '<input type="number" step="0.01" id="tf_'+f[0]+'" value="'+(tAcct[f[0]]||'')+'"></div>').join('');
  $('td_note').value=tAcct.note||'';$('td_match').value=tAcct.match||'';
  $('td_found').innerHTML=imp?'<b>Read from the file. Check these against the report, correct anything wrong, then Save.</b><br>'+
    imp.found.map(f=>esc(f.label)+' → '+inr(f.value)+' <i>('+esc(f.field)+')</i>').join('<br>'):'';
  if(imp)for(const k in imp.figures)$('tf_'+k).value=imp.figures[k];
  $('tdlg').showModal();}

const fyBounds=y=>[y+'-04-01',(+y+1)+'-03-31'];
async function loadAssets(){
  const y=$('fy').value,[a,b]=fyBounds(y);
  A=await api('/api/assets?'+qs({owner:$('who').value,fy:y}));
  keepValue('as_type',A.types.map(t=>'<option>'+esc(t)+'</option>').join(''));
  const held=A.rows.filter(r=>!r.sold),soldNow=A.rows.filter(r=>r.sold&&r.sold>=a&&r.sold<=b);
  const cost=held.reduce((s,r)=>s+(r.cost||0),0),val=held.reduce((s,r)=>s+(r.value||0),0);
  const real=soldNow.reduce((s,r)=>s+(r.sale||0)-(r.cost||0),0);
  $('astats').innerHTML=A.rows.length?stat('Invested (held now)',cost)+stat('Current value',val)+stat('Gain on holdings',val-cost,1)+
    stat('Gain on sales, '+fyLabel(+y).split(' (')[0],real,1):'';
  const pct=(g,c)=>c?' <span class="mute">('+(g/c*100).toFixed(1)+'%)</span>':'';
  let h='';
  for(const t of A.types){const list=held.filter(r=>r.type===t);if(!list.length)continue;
    const c=list.reduce((s,r)=>s+(r.cost||0),0),v=list.reduce((s,r)=>s+(r.value||0),0);
    h+='<div class="card scroll"><h3>'+esc(t)+'</h3><table><tr><th>Name</th><th>Owner</th><th>Cost</th><th>Value</th><th>Gain</th></tr>'+
      list.map(r=>'<tr class="go" data-aid="'+r.id+'"><td>'+esc(r.name)+(r.valued?'<div class="mute">value as on '+r.valued+'</div>':'')+'</td><td>'+esc(r.owner)+
        '</td><td class="num">'+inr(r.cost)+'</td><td class="num">'+inr(r.value)+'</td><td>'+signed((r.value||0)-(r.cost||0))+pct((r.value||0)-(r.cost||0),r.cost)+'</td></tr>').join('')+
      '<tr class="tot"><td>Total</td><td></td><td class="num">'+inr(c)+'</td><td class="num">'+inr(v)+'</td><td>'+signed(v-c)+pct(v-c,c)+'</td></tr></table></div>';}
  const soldTable=(title,list)=>list.length?'<div class="card scroll"><h3>'+title+'</h3><table><tr><th>Name</th><th>Sold on</th><th>Cost</th><th>Sale</th><th>Gain</th><th>Term</th></tr>'+
    list.map(r=>'<tr class="go" data-aid="'+r.id+'"><td>'+esc(r.name)+'<div class="mute">'+esc(r.type)+', '+esc(r.owner)+'</div></td><td>'+r.sold+'</td><td class="num">'+inr(r.cost)+
      '</td><td class="num">'+inr(r.sale)+'</td><td>'+signed((r.sale||0)-(r.cost||0))+'</td><td>'+esc(r.term)+'</td></tr>').join('')+'</table></div>':'';
  h+=soldTable('Sold in '+fyLabel(+y).split(' (')[0]+' (for ITR)',soldNow)+soldTable('Sold in other years',A.rows.filter(r=>r.sold&&!(r.sold>=a&&r.sold<=b)));
  $('alist').innerHTML=h||'<div class="card mute">Nothing added yet. Use Add investment for each mutual fund, plot, gold, house, FD and so on.</div>';
  await loadMf();
  $('abank').textContent=A.bank?'Bank payments sorted as Investments in '+fyLabel(+y)+': '+inr(A.bank)+'. Add those purchases here too so the totals are complete.':'';
}
let P=null,pCur=null;
async function loadInsurance(){
  const y=$('fy').value,fyName=fyLabel(+y).split(' (')[0];P=await api('/api/insurance?'+qs({owner:$('who').value,fy:y}));
  const due=P.rows.filter(r=>r.due_soon);
  $('po_due').innerHTML=due.length?'<div class="card warn"><b>Due in the next 30 days</b><br>'+due.map(r=>esc(r.name)+': '+r.next_due+
    (r.premium?', '+inr(r.premium):'')).join('<br>')+'</div>':'';
  const paid=P.rows.reduce((a,r)=>a+r.paid,0),claim=P.d80.reduce((a,x)=>a+x.claim,0);
  $('po_stats').innerHTML=(P.rows.length?stat('Premiums paid, '+fyName,paid):'')+
    '<div class="card"><span class="mute">80C used, '+fyName+'</span><b class="num">'+inr(P.used80c)+'</b><span class="mute">of 1,50,000'+(P.used80c>150000?' (limit reached)':'')+'</span></div>'+
    (P.d80.length?'<div class="card"><span class="mute">80D claimable</span><b class="num">'+inr(claim)+'</b><span class="mute">'+P.d80.map(x=>x.group.toLowerCase()+' '+inr(x.claim)+' of '+inr(x.limit)).join(', ')+'</span></div>':'');
  $('po_list').innerHTML=P.rows.length?P.rows.map(r=>'<div class="card"><div class="row"><div class="grow"><b>'+esc(r.name)+'</b> <span class="chip">'+esc(r.type)+'</span> <span class="chip">'+esc(r.owner)+'</span>'+
    (r.type==='Health insurance'?' <span class="chip">'+esc(r.insured)+(r.senior?', senior':'')+'</span>':'')+(r.closed?' <span class="chip">stopped '+r.closed+'</span>':'')+
    '<div class="mute">'+esc([r.insurer,r.policy_no?'policy '+r.policy_no:'',r.cover?'cover '+inr(r.cover):'',r.premium?'premium '+inr(r.premium)+' '+r.frequency.toLowerCase():''].filter(Boolean).join(', '))+'</div></div>'+
    '<div style="text-align:right"><span class="mute">Paid '+fyName+'</span><br><b class="num">'+inr(r.paid)+'</b>'+(r.next_due?'<div class="mute">next due '+r.next_due+'</div>':'')+'</div></div>'+
    '<p class="mute" style="margin:8px 0 0">Ledger: '+esc(LED(r.category))+'. '+esc(r.note_tax)+(r.match?'':' <b>Add the text for this premium in bank entries so payments are picked up.</b>')+'</p>'+
    '<div class="row" style="margin-top:8px"><span class="grow"></span>'+(r.match?'<button class="link" data-pview="'+r.id+'">View entries</button>':'')+
    '<button class="pri" data-pedit="'+r.id+'">Edit</button></div></div>').join('')
    :'<div class="card mute">No policies added yet. Use Add policy for each term, LIC, health, vehicle or clinic policy.</div>';
  const box=$('po_types');box.hidden=!P.by_type.length;
  box.innerHTML='<h3>All insurance payments in '+fyName+'</h3><table><tr><th>Head</th><th>Entries</th><th>Paid</th><th>Not linked to a policy</th></tr>'+
    P.by_type.map(t=>'<tr class="go" data-pcat="'+esc(t.category)+'"><td>'+esc(t.category)+'</td><td>'+t.entries+'</td><td class="num">'+inr(t.paid)+
      '</td><td class="num">'+(t.loose>0.5?inr(t.loose):'')+'</td></tr>').join('')+'</table>';
}
function openPolicy(r){pCur=r||null;
  keepValue('po_type',P.types.map(t=>'<option>'+esc(t)+'</option>').join(''));
  keepValue('po_frequency',P.freqs.map(t=>'<option>'+esc(t)+'</option>').join(''));
  keepValue('po_insured',P.insured.map(t=>'<option>'+esc(t)+'</option>').join(''));
  keepValue('po_owner',S.owners.map(o=>'<option>'+esc(o)+'</option>').join(''));
  $('po_title').textContent=r?'Edit policy':'Add policy';$('po_del').style.visibility=r?'visible':'hidden';
  const v=r||{name:'',type:'Term insurance',purpose:'Personal',insurer:'',policy_no:'',owner:$('who').value||'Self',insured:'Self and family',
    cover:'',premium:'',frequency:'Yearly',due:'',match:'',closed:'',note:'',senior:0};
  for(const k of ['name','type','purpose','insurer','policy_no','owner','insured','frequency','due','match','closed','note'])$('po_'+k).value=v[k]||'';
  for(const k of ['cover','premium'])$('po_'+k).value=v[k]||'';
  $('po_senior').checked=!!v.senior;$('podlg').showModal();}
let L=null,lCur=null;
async function loadLoans(){
  const y=$('fy').value;L=await api('/api/loans?'+qs({owner:$('who').value,fy:y}));
  const sum=k=>L.rows.reduce((a,r)=>a+(r[k]||0),0),fyName=fyLabel(+y).split(' (')[0];
  $('ln_stats').innerHTML=L.rows.length?stat('Outstanding',sum('outstanding'))+stat('Interest, '+fyName,sum('interest'))+
    stat('Paid (EMIs), '+fyName,sum('paid'))+stat('Received, '+fyName,sum('received')):'';
  const cell=(label,v,est)=>'<div><span class="mute">'+label+'</span><span class="num">'+(v==null?'-':inr(v)+(est?' <i class="mute">est.</i>':''))+'</span></div>';
  $('ln_list').innerHTML=L.rows.length?L.rows.map(l=>'<div class="card"><div class="row"><div class="grow"><b>'+esc(l.name)+'</b> <span class="chip">'+esc(l.type)+
    '</span> <span class="chip">'+esc(l.purpose)+'</span> <span class="chip">'+esc(l.owner)+'</span>'+(l.closed?' <span class="chip">closed '+l.closed+'</span>':'')+
    '<div class="mute">'+esc([l.lender,l.amount?'borrowed '+inr(l.amount):'',l.start?'on '+l.start:'',l.rate?l.rate+'%':'',l.emi?'EMI '+inr(l.emi):''].filter(Boolean).join(', '))+'</div></div>'+
    '<div style="text-align:right"><span class="mute">Outstanding</span><br><b class="num">'+(l.outstanding==null?'-':inr(l.outstanding))+'</b>'+(l.outstanding!=null&&!l.outstanding_given?' <i class="mute">est.</i>':'')+'</div></div>'+
    '<div class="figs">'+cell('Received this year',l.received)+cell('Paid this year',l.paid)+cell('Interest',l.interest,!l.interest_given)+cell('Principal repaid',l.principal,!l.interest_given)+'</div>'+
    '<p class="mute" style="margin:8px 0 0">'+esc(l.note_tax)+(l.match?'':' <b>Add the text for this loan in bank entries so its EMIs are picked up.</b>')+'</p>'+
    '<div class="row" style="margin-top:8px"><span class="grow"></span>'+(l.match?'<button class="link" data-lview="'+l.id+'">View entries</button>':'')+
    '<button class="pri" data-ledit="'+l.id+'">Edit</button></div></div>').join('')
    :'<div class="card mute">No loans added yet. Use Add loan for each car, jewel, personal or home loan.</div>';
  const open=L.rows.filter(l=>!l.closed).map(l=>Object.assign({},l,
    {monthly:l.rate&&l.outstanding?l.outstanding*l.rate/1200:null}))
    .sort((a,b)=>(b.rate||-1)-(a.rate||-1)||(b.outstanding||0)-(a.outstanding||0));
  const ob=$('ln_order');ob.hidden=open.length<2;
  if(open.length>1){const tot=k=>open.reduce((a,r)=>a+(r[k]||0),0);
    ob.innerHTML='<h3>Which loans to close first</h3><p class="mute" style="margin-top:0">Costliest rate first. Any spare money '+
      'paid into the loan at the top saves the most interest. Check its foreclosure or part-payment charges first.</p>'+
      '<table><tr><th>#</th><th>Loan</th><th>Rate</th><th>EMI</th><th>Outstanding</th><th>Interest a month</th></tr>'+
      open.map((l,i)=>'<tr class="go" data-lopen="'+l.id+'"><td>'+(i+1)+'</td><td>'+esc(l.name)+'</td><td class="num">'+(l.rate?l.rate+'%':'<span class="mute">add rate</span>')+
        '</td><td class="num">'+(l.emi?inr(l.emi):'<span class="mute">add EMI</span>')+'</td><td class="num">'+(l.outstanding==null?'-':inr(l.outstanding))+
        '</td><td class="num">'+(l.monthly==null?'-':inr(l.monthly))+'</td></tr>').join('')+
      '<tr><td></td><td><b>Total</b></td><td></td><td class="num"><b>'+inr(tot('emi'))+'</b></td><td class="num"><b>'+inr(tot('outstanding'))+
      '</b></td><td class="num"><b>'+inr(tot('monthly'))+'</b></td></tr></table>'+
      '<p class="mute">Outstanding is as of today, or the end of the year picked at the top; estimated from the rate and EMI unless you entered it. '+
      'Tap a loan to edit it.</p>';
    ob.querySelectorAll('[data-lopen]').forEach(tr=>tr.onclick=()=>openLoan(L.rows.find(x=>x.id==tr.dataset.lopen)));}
  const box=$('ln_types');box.hidden=!L.by_type.length;
  box.innerHTML='<h3>All loan entries in '+fyName+' by type</h3><table><tr><th>Type</th><th>Entries</th><th>Received</th><th>Paid</th><th>Not linked to a loan</th></tr>'+
    L.by_type.map(t=>{const loose=t.received+t.paid-t.matched;return '<tr class="go" data-ltype="'+esc(t.type)+'"><td>'+esc(t.type)+'</td><td>'+t.entries+'</td><td class="num">'+inr(t.received)+
      '</td><td class="num">'+inr(t.paid)+'</td><td class="num">'+(loose>0.5?inr(loose):'')+'</td></tr>';}).join('')+'</table>';
}
function openLoan(l){lCur=l||null;
  keepValue('ln_type',L.types.map(t=>'<option>'+esc(t)+'</option>').join(''));
  keepValue('ln_purpose',L.purposes.map(t=>'<option>'+esc(t)+'</option>').join(''));
  keepValue('ln_owner',S.owners.map(o=>'<option>'+esc(o)+'</option>').join(''));
  $('ln_title').textContent=l?'Edit loan':'Add loan';$('ln_del').style.visibility=l?'visible':'hidden';
  const v=l||{name:'',type:'Car loan',purpose:'Personal',lender:'',owner:$('who').value||'Self',amount:'',start:'',rate:'',emi:'',match:'',closed:'',note:''};
  for(const k of ['name','type','purpose','lender','owner','start','match','closed','note'])$('ln_'+k).value=v[k]||'';
  for(const k of ['amount','rate','emi'])$('ln_'+k).value=v[k]||'';
  $('ln_fytitle').textContent=fyLabel(+$('fy').value);
  $('ln_interest').value=l&&l.interest_given?l.interest:'';$('ln_outstanding').value=l&&l.outstanding_given?l.outstanding:'';
  $('lndlg').showModal();}
async function loadMf(){
  const y=$('fy').value,d=await api('/api/mf?'+qs({owner:$('who').value,fy:y}));
  keepValue('mf_owner',S.owners.map(o=>'<option'+(o===($('who').value||'Self')?' selected':'')+'>'+esc(o)+'</option>').join(''));
  const fyName=fyLabel(+y).split(' (')[0];
  let g='';
  if(d.gains.length){
    g='<h3 style="margin-top:12px">Mutual fund sales in '+fyName+' <span class="mute" style="font-weight:400">('+
      (d.source==='statement'?'from your capital gains statement':d.source==='both'?'capital gains statement, plus transactions for other funds':'worked out from transactions, oldest units first')+')</span></h3>'+
      '<div class="stats" style="margin:8px 0">'+d.totals.map(t=>stat(esc(t[0]),t[1],1)).join('')+'</div>'+
      '<div class="scroll"><table><tr><th>Scheme</th><th>Bought</th><th>Sold</th><th>Sale</th><th>Cost</th><th>Gain</th><th>Term</th></tr>'+
      d.gains.map(r=>'<tr><td style="white-space:normal">'+esc(r.scheme)+(r.old?' <span class="chip none">bought before Feb 2018</span>':'')+
        '</td><td>'+(r.bought||'-')+'</td><td>'+r.sold+'</td><td class="num">'+inr(r.sale)+'</td><td class="num">'+inr(r.cost)+
        '</td><td>'+signed(r.gain)+'</td><td>'+esc(r.term)+'</td></tr>').join('')+'</table></div>'+
      '<p class="mute">Equity long-term gains up to 1.25 lakh a year are exempt.'+
      (d.gains.some(r=>r.old)?' Units bought before 1 Feb 2018 can use the 31 Jan 2018 value as cost; your CA will apply this.':'')+
      (d.gains.some(r=>!r.bought&&d.source!=='statement')?' Some sales have no purchase in the uploaded statements: upload an older statement covering those purchases.':'')+'</p>';
  }else if(d.uploads.length)g='<p class="mute">No mutual fund sales in '+fyName+'.</p>';
  $('mf_gains').innerHTML=g;
  const hv=d.holdings.reduce((a,h)=>a+h.value,0),hc=d.holdings.reduce((a,h)=>a+h.cost,0);
  $('mf_hold').innerHTML=d.holdings.length?'<h3 style="margin-top:12px">Holdings from statements: value '+inr(hv)+', cost '+inr(hc)+', gain '+signed(hv-hc)+'</h3>'+
    '<div class="scroll"><table><tr><th>Scheme</th><th>Owner</th><th>Units</th><th>Cost</th><th>Value</th><th>Gain</th><th>Held since</th></tr>'+
    d.holdings.map(h=>'<tr><td style="white-space:normal">'+esc(h.scheme)+' <span class="chip">'+esc(h.kind)+'</span><div class="mute">NAV as on '+h.nav_date+'</div></td><td>'+esc(h.owner)+
      '</td><td class="num">'+h.units.toFixed(3)+'</td><td class="num">'+inr(h.cost)+'</td><td class="num">'+inr(h.value)+'</td><td>'+signed(h.value-h.cost)+
      '</td><td>'+h.since+'</td></tr>').join('')+'</table></div>':'';
  $('mf_ups').innerHTML=d.uploads.length?'<h3 style="margin-top:12px">Uploaded statements</h3>'+d.uploads.map(u=>
    '<div class="tx" style="cursor:default"><div class="grow">'+esc(u.filename)+' <span class="chip">'+esc(u.kind)+'</span> <span class="chip">'+esc(u.owner)+'</span>'+
    '<div class="mute">uploaded '+u.uploaded+', '+u.added+' new entries</div></div><button class="link" data-mfdel="'+u.id+'">Delete</button></div>').join(''):'';
}
function openAsset(r){aCur=r||null;
  $('as_title').textContent=r?'Edit investment':'Add investment';$('as_del').style.visibility=r?'visible':'hidden';
  const v=r||{name:'',type:A.types[0],owner:$('who').value||S.owners[0],bought:'',cost:'',value:'',valued:'',sold:'',sale:'',note:''};
  for(const k of ['name','type','owner','bought','valued','sold','note'])$('as_'+k).value=v[k]||'';
  for(const k of ['cost','value','sale'])$('as_'+k).value=v[k]||'';
  $('asdlg').showModal();}

function activeTab(){return document.querySelector('nav .on').dataset.tab;}
function refresh(){const tab=activeTab();
  if(tab==='txns')return loadTxns(true);if(tab==='reports')return loadReport();if(tab==='sort')return loadSort();
  if(tab==='consult')return loadConsult();if(tab==='trading')return loadTrading();
  if(tab==='staff')return loadStaff();if(tab==='loans')return loadLoans();if(tab==='insurance')return loadInsurance();if(tab==='banking')return loadCash();
  if(tab==='assets')return loadAssets();}
const run=fn=>async(...a)=>{try{await fn(...a);}catch(e){toast(e.message);}};
async function go(tab){
  clearSel();
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x.dataset.tab===tab));
  document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===tab));
  await loadState();await refresh();}

document.querySelector('nav').onclick=run(async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.tab!=='txns')payeeFilter='';await go(b.dataset.tab);});
$('a_cancel').onclick=()=>$('adlg').close();
$('a_save').onclick=run(async()=>{await api('/api/account',{id:editAcct.id,name:$('a_name').value,owner:$('a_owner').value,kind:$('a_kind').value});$('adlg').close();await loadState();await refresh();toast('Account updated');});
$('who').onchange=run(async()=>{$('f_account').value='';$('s_account').value='';$('dl_bank').value='';await loadState();await refresh();toast('Showing '+($('who').value||'everyone'));});
$('fy').onchange=run(async()=>{yearPicked=true;$('f_month').value='';await loadState();await refresh();
  toast('Showing '+fyLabel(+$('fy').value));});
for(const id of ['f_account','f_month','f_cat','f_dir'])$(id).onchange=run(()=>loadTxns(true));
for(const id of ['s_account','s_dir'])$(id).onchange=run(loadSort);
$('f_q').oninput=()=>{clearTimeout($('f_q').t);$('f_q').t=setTimeout(run(()=>loadTxns(true)),350);};
$('more').onclick=run(()=>loadTxns(false));
$('rp_clinic').onchange=run(loadReport);
const dlq=()=>qs({owner:$('who').value,fy:$('dl_fy').value,account:$('dl_bank').value,clinic:$('rp_clinic').value});
$('export').onclick=()=>{location.href='/export.csv?'+dlq();};
$('exp_xlsx').onclick=()=>{location.href='/export.xlsx?'+dlq();};
$('exp_sum').onclick=()=>{location.href='/export-summary.csv?'+dlq();};
$('exp_tally').onclick=()=>{location.href='/export-tally.zip?'+dlq();};
$('heads').onclick=run(async e=>{const tr=e.target.closest('tr.go');if(!tr)return;
  payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';$('f_q').value='';
  await go('txns');$('f_cat').value=tr.dataset.cat;await loadTxns(true);});
async function loadCoverage(){
  const d=await api('/api/coverage');
  const fmt=s=>{const p=s.split('-');return +p[2]+' '+MON[+p[1]-1]+' '+p[0];};
  const months=[];let [y,m]=d.since.slice(0,7).split('-').map(Number);const end=d.today.slice(0,7);
  while((y+'-'+String(m).padStart(2,'0'))<=end){months.push(y+'-'+String(m).padStart(2,'0'));m++;if(m>12){m=1;y++;}}
  let all=0;
  $('cov').innerHTML=d.accounts.map(a=>{
    const bad=new Set();for(const g of a.missing)for(const k of months)if(k>=g.start.slice(0,7)&&k<=g.end.slice(0,7))bad.add(k);
    all+=a.missing.length;
    const strip='<div style="display:flex;flex-wrap:wrap;gap:2px;margin:6px 0">'+months.map(k=>{
      const n=a.months[k]||0,c=bad.has(k)?'var(--warn)':n?'var(--in)':'#ccc';
      return '<span title="'+MON[+k.slice(5)-1]+' '+k.slice(0,4)+': '+(bad.has(k)?'missing entries':n+' entries')+'" style="width:14px;height:14px;border-radius:3px;background:'+c+'"></span>';}).join('')+'</div>';
    const list=a.missing.length?'<ul style="margin:4px 0 0 18px;padding:0">'+a.missing.map(g=>'<li>'+
      (g.note==='Balance does not follow on'?'Entries missing between <b>'+fmt(g.start)+'</b> and <b>'+fmt(g.end)+'</b> (balance jumps by '+inr(g.amount)+')'
       :g.note==='No statement imported yet'?'No statement imported yet'
       :g.note==='No card entries in between'?'Card statements missing between <b>'+fmt(g.start)+'</b> and <b>'+fmt(g.end)+'</b>'
       :'Missing <b>'+fmt(g.start)+'</b> to <b>'+fmt(g.end)+'</b> ('+g.note.toLowerCase()+')')+'</li>').join('')+'</ul>'
      :'<div class="mute">Complete'+(a.first?' from '+fmt(a.first)+' to '+fmt(a.last):'')+'</div>';
    const q=(a.quiet||[]).map(g=>'<div class="mute">No entries between '+fmt(g.start)+' and '+fmt(g.end)+
      '; the balance matches, so probably no transactions then. Check if unsure.</div>').join('');
    return '<div style="margin-top:12px"><b>'+esc(a.name)+'</b> <span class="chip">'+esc(a.owner)+'</span> <span class="chip">'+esc(a.kind)+'</span>'+
      (a.entries?' <span class="mute">'+a.entries+' entries, '+fmt(a.first)+' to '+fmt(a.last)+'</span>':'')+strip+list+q+'</div>';}).join('')+
    '<p class="mute" style="margin-top:10px">Squares are months from '+fmt(d.since)+': green has entries, grey has none, amber is missing. '+
    (all?'Import the statements for the amber periods; re-importing a period you already have is safe.':'Nothing missing.')+'</p>';}
$('cov_go').onclick=run(loadCoverage);
async function loadPatientRule(d){
  d=d||await api('/api/patient-rule');
  $('pr_on').checked=d.on;$('pr_max').value=d.max;$('pr_ex').value=d.exclude;
  $('pr_stat').textContent=d.count+' entries filed as Patient receipts in all, '+inr(d.total)+'.';
  $('sm_on').checked=d.small_on;$('sm_max').value=d.small_max;
  $('sm_stat').textContent=d.small_count+' entries filed as Personal in all, '+inr(d.small_total)+'.';}
$('pr_save').onclick=run(async()=>{const d=await api('/api/patient-rule',{on:$('pr_on').checked,max:$('pr_max').value,exclude:$('pr_ex').value,small_on:$('sm_on').checked,small_max:$('sm_max').value});
  toast(d.sorted+' more entries sorted');await loadPatientRule(d);await loadState();});
let MT=null;
async function loadMail(){
  const d=await api('/api/mail');
  if(document.activeElement!==$('m_user'))$('m_user').value=d.user||'';
  $('m_pass').placeholder=d.has_password?'App Password saved (type to change)':'Gmail App Password';
  $('m_pdfpw').placeholder=d.pdf_passwords?d.pdf_passwords+' statement passwords saved (type all of them again to change)':
    'Statement passwords, one per line (Customer ID; NAME + first 4 digits of Customer ID; NAME + DDMM for the card; your daughter\'s too)';
  if(document.activeElement!==$('m_accts'))$('m_accts').value=d.accounts;
  if(document.activeElement!==$('m_daughter'))$('m_daughter').value=d.daughter;
  if(document.activeElement!==$('m_since'))$('m_since').value=d.since;
  const c=d.counts||{};
  $('m_status').textContent=[(d.running?'Working: ':'')+(d.message||(d.log.length?'':d.user?'Not checked yet':'Not set up yet')),
    d.log.length?(c.ok||0)+' imported, '+(c.skipped||0)+' skipped, '+(c.expired||0)+' expired, '+(c.error||0)+' need attention':''].filter(Boolean).join(' · ');
  $('m_fetch').disabled=d.running;
  const badge=s=>s==='ok'?'<span class="chip">Imported</span>':s==='skipped'?'<span class="chip">Skipped</span>':s==='expired'?'<span class="chip">Link expired</span>':'<span class="chip none">Needs attention</span>';
  const errs=d.log.filter(r=>r.status==='error'),gone=d.log.filter(r=>r.status==='expired'),rest=d.log.filter(r=>r.status!=='error'&&r.status!=='expired');
  const row=r=>'<div class="tx" style="cursor:default"><div class="grow"><b>'+esc(r.date)+'</b> '+badge(r.status)+' '+esc(r.account||'')+
    '<div class="mute">'+esc(r.subject)+(r.detail?' — '+esc(r.detail):'')+'</div></div>'+
    '<div class="num">'+(r.status==='ok'?r.added+' new of '+r.found:'')+
    (r.has_file?' <a class="link" href="/api/mail/file?id='+r.id+'">PDF</a>':'')+'</div></div>';
  $('m_log').innerHTML=(errs.length?'<h3>Needs attention ('+errs.length+')</h3>'+errs.map(row).join('')+
    '<p class="mute">These are tried again on every check. Wrong password: add the right one above. Expired link: '+
    'get that period again from NetBanking or WhatsApp banking (it arrives by email and is imported by itself).</p>':'')+
    (gone.length?'<details><summary class="mute">Expired links ('+gone.length+'): HDFC no longer keeps these; the periods show in What\'s missing</summary>'+gone.map(row).join('')+'</details>':'')+
    (rest.length?'<details><summary class="mute">Imported emails ('+rest.length+')</summary>'+rest.map(row).join('')+'</details>':'');
  clearTimeout(MT);if(d.running)MT=setTimeout(run(loadMail),4000);
  else if(loadMail.was)await loadState();
  loadMail.was=d.running;
  return d;}
function mailBody(){return {user:$('m_user').value,password:$('m_pass').value,pdf_passwords:$('m_pdfpw').value,
  accounts:$('m_accts').value,daughter:$('m_daughter').value,since:$('m_since').value};}
$('m_save').onclick=run(async()=>{await api('/api/mail/settings',mailBody());$('m_pass').value='';$('m_pdfpw').value='';
  toast('Saved');await loadMail();});
$('m_fetch').onclick=run(async()=>{await api('/api/mail/settings',mailBody());$('m_pass').value='';$('m_pdfpw').value='';
  const d=await api('/api/mail/fetch',{});toast(d.started?'Checking Gmail for statements…':'Already checking');
  await loadMail();await loadState();});
$('addacct').onclick=run(async()=>{await api('/api/account',{name:$('newacct').value,owner:$('newowner').value,kind:$('newkind').value});$('newacct').value='';await loadState();});
$('accts').onclick=run(async e=>{const d=e.target.dataset;
  if(d.edit){editAcct=S.accounts.find(a=>a.id==d.edit);$('a_name').value=editAcct.name;$('a_owner').value=editAcct.owner;$('a_kind').value=editAcct.kind;$('adlg').showModal();}
  if(d.imp){importAcct=d.imp;$('file').value='';$('file').click();}
  if(d.pimp)pickPnl(d.pimp);
  if(d.view){payeeFilter='';await go('txns');$('f_account').value=d.view;await loadTxns(true);}
  if(d.del&&confirm('Delete this account and all its entries? This cannot be undone.')){
    await api('/api/account/delete',{id:+d.del});await loadState();}});
$('file').onchange=run(async()=>{const f=$('file').files[0];if(!f)return;
  let pw='';if(/\.pdf$/i.test(f.name))pw=prompt('PDF password (leave empty if the file is not locked)')||'';
  toast('Importing '+f.name+'...');
  const r=await api('/api/import?'+qs({account:importAcct,name:f.name,pw:pw}),f);
  toast('Imported '+r.added+' entries ('+r.first+' to '+r.last+'). '+r.duplicates+' already present. '+r.auto+' sorted by rules.');
  await loadState();});
$('d_cancel').onclick=()=>$('dlg').close();
$('d_save').onclick=run(async()=>{
  await api('/api/txn',{id:cur.id,category:$('d_cat').value,clinic:$('d_clinic').value,note:$('d_note').value});
  let msg='Saved';
  if($('d_mk').checked&&$('d_cat').value){
    const r=await api('/api/rule',{pattern:$('d_pat').value,dir:cur.credit>0?'in':'out',
      category:$('d_cat').value,clinic:$('d_clinic').value});
    msg='Saved. Rule added and applied to '+r.changed+' more entries';}
  $('dlg').close();toast(msg);await loadState();await loadTxns(true);});
$('bulk').onclick=()=>{if(!total)return toast('Nothing to categorise.');
  bulkMode='filter';$('b_rulewrap').style.display='none';$('b_save').textContent='Apply to all shown';
  $('b_title').textContent='Categorise all '+total+' entries matching the current filters';$('bdlg').showModal();};
$('b_cancel').onclick=()=>$('bdlg').close();
$('b_save').onclick=run(async()=>{
  const cat=$('b_cat').value,clinic=$('b_clinic').value;let r;
  if(bulkMode==='filter')r=await api('/api/bulk',{filter:filter(),category:cat,clinic:clinic});
  else if(selKind==='group')r=await api('/api/groups-bulk',{items:[...sel.values()].map(g=>({payee:g.payee,dir:g.dir})),
    category:cat,clinic:clinic,rule:$('b_rule').checked});
  else r=await api('/api/txns-bulk',{ids:[...sel.keys()],category:cat,clinic:clinic});
  $('bdlg').close();toast('Posted '+r.changed+' entries to '+LED(cat));await loadState();await refresh();});
$('selclear').onclick=clearSel;
$('selgo').onclick=()=>{bulkMode='sel';$('b_save').textContent='Apply to selected';
  $('b_rulewrap').style.display=selKind==='group'?'block':'none';
  $('b_title').textContent='Sort '+$('seltext').textContent.replace(' selected','');$('bdlg').showModal();};
$('s_all').onclick=()=>{const boxes=[...document.querySelectorAll('#glist .pick input')],on=boxes.some(c=>!c.checked);
  boxes.forEach(c=>{if(c.checked!==on){c.checked=on;c.onchange();}});};
const addPerson=(kind,head,ids)=>run(async()=>{
  const r=await api('/api/'+kind,{name:$(ids[0]).value,match:$(ids[1]).value,role:ids[2]?$(ids[2]).value:''});
  ids.forEach(id=>$(id).value='');
  toast(r.changed+' payments filed under '+head+'.'+(r.other?' '+r.other+' more are already under other heads; change them in Entries if needed.':''));
  await loadState();await refresh();});
$('c_add').onclick=addPerson('consultant','Consultant fees',['c_name','c_match']);
$('st_add').onclick=addPerson('staff','Staff salaries',['st_name','st_match','st_role']);
$('cs_add').onclick=run(async()=>{
  const amt=$('cs_amt').value;
  await api('/api/cash',{date:$('cs_date').value,amount:amt,note:$('cs_note').value});
  $('cs_amt').value='';$('cs_note').value='';$('cs_amt').focus();
  toast('Added '+inr(amt)+' cash');await loadState();await loadCash();});
$('cs_amt').onkeydown=$('cs_note').onkeydown=e=>{if(e.key==='Enter')$('cs_add').click();};
$('cs_list').onclick=run(async e=>{const id=e.target.dataset.csdel;if(!id||!confirm('Delete this cash entry?'))return;
  await api('/api/cash/delete',{id:+id});await loadState();await loadCash();});
$('g_cancel').onclick=()=>$('gdlg').close();
$('g_view').onclick=run(async()=>{$('gdlg').close();
  $('f_account').value=$('s_account').value;$('f_month').value='';$('f_q').value='';
  await go('txns');payeeFilter=grp.payee;$('f_cat').value='__none__';$('f_dir').value=grp.dir;await loadTxns(true);});
$('g_save').onclick=run(async()=>{
  const r=await api('/api/group',{payee:grp.payee,dir:grp.dir,category:$('g_cat').value,
    clinic:$('g_clinic').value,rule:$('g_rule').checked});
  $('gdlg').close();toast('Posted '+r.changed+' entries to '+LED($('g_cat').value));await loadState();await loadSort();});
$('r_add').onclick=run(async()=>{
  const r=await api('/api/rule',{pattern:$('r_pat').value,dir:$('r_dir').value,category:$('r_cat').value,clinic:$('r_clinic').value});
  $('r_pat').value='';toast('Rule added and applied to '+r.changed+' entries');await loadState();});
$('r_export').onclick=()=>{location.href='/rules.json';};
$('r_import').onclick=()=>{$('rfile').value='';$('rfile').click();};
$('rfile').onchange=run(async()=>{const f=$('rfile').files[0];if(!f)return;
  const r=await api('/api/rules/import',f);
  toast('Added '+r.rules+' rules, '+r.staff+' staff, '+r.consultants+' consultants, '+(r.loans||0)+' loans. '+r.sorted+' entries sorted.'+(r.skipped?' '+r.skipped+' already there.':''));
  await loadState();});
$('rlist').onclick=run(async e=>{const id=e.target.dataset.rdel;if(!id)return;
  await api('/api/rule/delete',{id:+id});await loadState();});
$('t_add').onclick=run(async()=>{const n=$('t_new').value.trim();if(!n)return toast('Enter the broker name.');
  await api('/api/account',{name:n,owner:$('t_owner').value,kind:'Trading'});$('t_new').value='';await loadState();await loadTrading();toast('Added '+n);});
$('tacc').onclick=e=>{const d=e.target.dataset;if(d.tedit)openTrade(d.tedit);if(d.timp)pickPnl(d.timp);};
let pnlAcct=null;
function pickPnl(id){pnlAcct=id;$('pfile').value='';$('pfile').click();}
$('pfile').onchange=run(async()=>{const f=$('pfile').files[0];if(!f)return;
  let pw='';if(/\.pdf$/i.test(f.name))pw=prompt('PDF password (leave empty if the file is not locked)')||'';
  toast('Reading '+f.name+'...');
  const r=await api('/api/trading/parse?'+qs({name:f.name,pw:pw}),f);
  if(r.fy){yearPicked=true;$('fy').value=r.fy;}
  await go('trading');openTrade(pnlAcct,r);});
$('td_cancel').onclick=()=>$('tdlg').close();
$('td_save').onclick=run(async()=>{const body={account_id:tAcct.id,fy:$('fy').value,note:$('td_note').value,match:$('td_match').value};
  for(const f of T.fields)body[f[0]]=$('tf_'+f[0]).value;
  await api('/api/trading',body);$('tdlg').close();await loadTrading();toast('Saved '+tAcct.name);});
$('as_add').onclick=()=>openAsset(null);
$('po_add').onclick=()=>openPolicy(null);
$('po_cancel').onclick=()=>$('podlg').close();
$('po_save').onclick=run(async()=>{const body={id:pCur?pCur.id:0,senior:$('po_senior').checked};
  for(const k of ['name','type','purpose','insurer','policy_no','owner','insured','cover','premium','frequency','due','match','closed','note'])body[k]=$('po_'+k).value;
  const r=await api('/api/policy',body);$('podlg').close();
  toast('Saved '+body.name+(r.changed?'. '+r.changed+' premium payments filed under it.':''));await loadState();await loadInsurance();});
$('po_del').onclick=run(async()=>{if(!pCur||!confirm('Delete '+pCur.name+'? Its payments stay where they are.'))return;
  await api('/api/policy/delete',{id:pCur.id});$('podlg').close();await loadInsurance();});
$('po_list').onclick=run(async e=>{const d=e.target.dataset;
  if(d.pedit)openPolicy(P.rows.find(r=>r.id==d.pedit));
  if(d.pview){const r=P.rows.find(x=>x.id==d.pview);payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';
    await go('txns');$('f_cat').value=r.category;$('f_q').value=r.match;await loadTxns(true);}});
$('po_types').onclick=run(async e=>{const tr=e.target.closest('tr[data-pcat]');if(!tr)return;
  payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';$('f_q').value='';
  await go('txns');$('f_cat').value=tr.dataset.pcat;await loadTxns(true);});
$('ln_add').onclick=()=>openLoan(null);
$('ln_cancel').onclick=()=>$('lndlg').close();
$('ln_save').onclick=run(async()=>{const body={id:lCur?lCur.id:0,fy:$('fy').value};
  for(const k of ['name','type','purpose','lender','owner','amount','start','rate','emi','match','closed','note','interest','outstanding'])body[k]=$('ln_'+k).value;
  const r=await api('/api/loan',body);$('lndlg').close();
  toast('Saved '+body.name+(r.changed?'. '+r.changed+' bank entries filed under it.':''));await loadState();await loadLoans();});
$('ln_del').onclick=run(async()=>{if(!lCur||!confirm('Delete '+lCur.name+'? Its bank entries stay under '+lCur.type+'.'))return;
  await api('/api/loan/delete',{id:lCur.id});$('lndlg').close();await loadLoans();});
$('ln_list').onclick=run(async e=>{const d=e.target.dataset;
  if(d.ledit)openLoan(L.rows.find(l=>l.id==d.ledit));
  if(d.lview){const l=L.rows.find(x=>x.id==d.lview);payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';
    await go('txns');$('f_cat').value=l.type;$('f_q').value=l.match;await loadTxns(true);}});
$('ln_types').onclick=run(async e=>{const tr=e.target.closest('tr[data-ltype]');if(!tr)return;
  payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';$('f_q').value='';
  await go('txns');$('f_cat').value=tr.dataset.ltype;await loadTxns(true);});
$('mf_up').onclick=()=>{$('mffile').value='';$('mffile').click();};
$('mffile').onchange=run(async()=>{const f=$('mffile').files[0];if(!f)return;
  let pw='';if(/\.pdf$/i.test(f.name))pw=prompt('PDF password. For CAMS and KFintech statements it is usually your PAN in capitals.')||'';
  toast('Reading '+f.name+'...');
  const r=await api('/api/mf/upload?'+qs({name:f.name,pw:pw,owner:$('mf_owner').value}),f);
  toast('Read '+r.found+' '+(r.kind==='capital gains'?'sales':'transactions')+' in '+r.schemes+' schemes. '+r.added+' new, '+r.duplicates+' already there.');
  await loadAssets();});
$('mf_ups').onclick=run(async e=>{const id=e.target.dataset.mfdel;if(!id||!confirm('Delete this statement and its entries?'))return;
  await api('/api/mf/upload/delete',{id:+id});await loadAssets();});
$('alist').onclick=e=>{const tr=e.target.closest('tr[data-aid]');if(tr)openAsset(A.rows.find(r=>r.id==tr.dataset.aid));};
$('as_cancel').onclick=()=>$('asdlg').close();
$('as_save').onclick=run(async()=>{const body={id:aCur?aCur.id:0};
  for(const k of ['name','type','owner','bought','cost','value','valued','sold','sale','note'])body[k]=$('as_'+k).value;
  await api('/api/asset',body);$('asdlg').close();await loadAssets();toast('Saved '+body.name);});
$('as_del').onclick=run(async()=>{if(!aCur||!confirm('Delete '+aCur.name+'?'))return;
  await api('/api/asset/delete',{id:aCur.id});$('asdlg').close();await loadAssets();});
$('itr_up').onclick=()=>{$('itrfile').value='';$('itrfile').click();};
$('itrfile').onchange=run(async()=>{const f=$('itrfile').files[0];if(!f)return;
  let pw='';if(/\.pdf$/i.test(f.name))pw=prompt('PDF password, if any. For ITR-V it is usually your PAN in small letters followed by date of birth as DDMMYYYY.')||'';
  toast('Reading '+f.name+'...');
  const r=await api('/api/itr/upload?'+qs({name:f.name,pw:pw,owner:$('itr_owner').value}),f);
  toast('Read '+(r.form||'return')+' for AY '+r.ay+'-'+String(r.ay+1).slice(2)+': '+r.found+' figures');await loadItr();});
$('itr_files').onclick=run(async e=>{const d=e.target.dataset;
  if(d.ifile)location.href='/api/itr/file?id='+d.ifile;
  if(d.idel&&confirm('Delete this uploaded return?')){await api('/api/itr/delete',{id:+d.idel});await loadItr();}
  if(d.ifig){IF=(await api('/api/itr/figures?id='+d.ifig)).rows;$('if_title').textContent='All figures in the file';
    $('if_q').value='';showFigures();$('ifdlg').showModal();}});
$('if_q').oninput=showFigures;$('if_close').onclick=()=>$('ifdlg').close();
run(async()=>{await loadState();await loadCash();await loadMail();await loadCoverage();await loadPatientRule();})();
</script></body></html>"""


if __name__ == "__main__":
    init()
    threading.Thread(target=mail_scheduler, daemon=True).start()
    print(f"Handral Books running on http://{HOST}:{PORT}  (data: {DB})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
