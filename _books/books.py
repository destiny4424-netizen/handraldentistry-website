#!/usr/bin/env python3
"""Handral Books: single-file bookkeeping server (v5).

Python 3 standard library only. pdfplumber is optional (needed for PDF import).
Data lives in books.db next to this file unless BOOKS_DB points elsewhere.
Listens on 127.0.0.1:3020 by default.
"""
import calendar
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
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
    ("Insurance", "expenses"), ("Bank charges", "expenses"),
    ("Loan interest", "expenses"), ("Other expense", "expenses"),
    ("Salary income", "other_income"), ("Interest received", "other_income"),
    ("Other income", "other_income"),
    ("Life insurance premium (80C)", "tax"),
    ("Health insurance premium (80D)", "tax"),
    ("Tax-saving investment (80C)", "tax"),
    ("Income tax and TDS paid", "tax"), ("Donations (80G)", "tax"),
    ("School fees (80C)", "tax"),
    ("Trading transfer", "invest"), ("Investments", "invest"),
    ("Loan received or repaid", "loans"), ("Credit card payment", "loans"),
    ("Own account transfer", "personal"),
    ("Cash deposit or withdrawal", "personal"),
    ("Family and friends", "personal"), ("Personal", "personal"),
]
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
    ("Disbursement Credit", "in", "Loan received or repaid"),
    ("JLOTH", "out", "Loan received or repaid"),
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
    ("BAJAJ FINANCE", "any", "Loan received or repaid"),
    ("EARLYSALARY", "any", "Loan received or repaid"),
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
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY, name TEXT, type TEXT, owner TEXT DEFAULT 'Self',
  bought TEXT DEFAULT '', cost REAL DEFAULT 0, value REAL DEFAULT 0,
  valued TEXT DEFAULT '', sold TEXT DEFAULT '', sale REAL DEFAULT 0,
  note TEXT DEFAULT '');
"""


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def payee_of(narration):
    """Best guess at who the money went to or came from, used to group entries."""
    n = narration.strip()
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


def parse_statement(name, data, password):
    rows, lines = read_rows(name, data, password)
    try:
        return rows_to_txns(rows or lines)
    except ValueError:
        if looks_like_pnl(rows + lines):
            raise ValueError("This looks like a broker P&L report, not a bank statement. "
                             "Open the Trading tab and use Import P&L report on that account.")
        raise


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
    people = [(head, [r["match"] for r in con.execute(f"SELECT match FROM {table}")])
              for table, head in PEOPLE.values()]
    n = 0
    for t in con.execute(
            "SELECT id,narration,payee,debit FROM txns WHERE category=''").fetchall():
        low = t["narration"].lower()
        head = t["debit"] > 0 and next(
            (h for h, ms in people
             if any(m.lower() in low or m == t["payee"] for m in ms)), None)
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
    return n


def import_statement(account_id, name, data, password):
    txns = parse_statement(name, data, password)
    with LOCK:
        con = db()
        seq = con.execute("SELECT COALESCE(MAX(seq),0) FROM txns").fetchone()[0]
        seen, added = {}, 0
        for t in txns:
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
        where = "category=? AND debit>0 AND (narration LIKE ? OR payee=?)"
        args = [head, f"%{c['match']}%", c["match"]]
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
    for g in ("invest", "loans", "personal"):
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


def export_xlsx(q):
    ew = [12, 10, 16, 60, 18, 14, 14, 14, 12, 26, 30, 12, 24, 10, 12]
    ent = entry_rows(q)
    money = lambda rows: [("", [float(v) if i in (5, 6) or (i == 7 and v is not None)
                                else v for i, v in enumerate(r)]) for r in rows]
    sheets = [
        ("ITR summary", itr_rows(q), [46, 10, 16, 16], False),
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
            elif u.path == "/api/consultants":
                self.send(api_consultants(q))
            elif u.path == "/api/staff":
                self.send(api_people("staff", q))
            elif u.path == "/api/cash":
                self.send(api_cash(q))
            elif u.path == "/api/itr":
                self.send(api_itr(q))
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
                    match = " ".join(str(d.get("match") or name).split())
                    if len(match) < 4:
                        raise ValueError("Enter at least 4 letters of the name.")
                    con.execute(f"INSERT OR IGNORE INTO {table}(name,match) VALUES(?,?)",
                                (name or match, match))
                    if table == "staff":
                        con.execute("UPDATE staff SET role=? WHERE match=?",
                                    (" ".join(str(d.get("role") or "").split()), match))
                    like = (f"%{match}%", match.upper())
                    cur = con.execute(
                        "UPDATE txns SET category=? WHERE category='' AND debit>0"
                        " AND (narration LIKE ? OR payee=?)", (head,) + like)
                    res["changed"] = cur.rowcount
                    res["other"] = con.execute(
                        "SELECT COUNT(*) FROM txns WHERE category NOT IN ('', ?)"
                        " AND debit>0 AND (narration LIKE ? OR payee=?)",
                        (head,) + like).fetchone()[0]
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
  <div class="card"><div class="row">
    <input id="newacct" class="grow" type="text" placeholder="New account name, e.g. HDFC 6324">
    <select id="newowner" aria-label="Owner"></select>
    <select id="newkind" aria-label="Account type"></select>
    <button class="pri" id="addacct">Add account</button></div></div>
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
<section id="staff">
  <h2>Staff salaries</h2>
  <p class="mute">Add each staff member once. Salary payments to them are filed under Staff salaries
  automatically, now and on every future import.</p>
  <div class="card"><div class="filters" style="margin:0">
    <input id="st_name" type="text" placeholder="Staff name">
    <input id="st_role" type="text" placeholder="Role, e.g. Assistant (optional)">
    <input id="st_match" type="text" placeholder="Name as shown in bank entries (optional)">
    <button class="pri" id="st_add">Add staff</button></div>
    <p class="mute" style="margin:8px 0 0">Banks often shorten names. If no payments are found, search
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
    <input id="c_match" type="text" placeholder="Name as shown in bank entries (optional)">
    <button class="pri" id="c_add">Add consultant</button></div>
    <p class="mute" style="margin:8px 0 0">Banks often shorten names. If no payments are found, search
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
  <div class="card" id="dl">
    <h3>Download</h3>
    <div class="filters" style="margin:8px 0">
      <select id="dl_fy" aria-label="Year to download"></select>
      <select id="dl_bank" aria-label="Bank to download"></select></div>
    <div class="row">
      <button class="pri" id="exp_xlsx">Excel workbook</button>
      <button id="exp_sum">ITR summary CSV</button>
      <button id="export">All entries CSV</button></div>
    <p class="mute" style="margin:8px 0 0">The Excel workbook has the ITR summary, income and
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
  <button data-tab="staff">Salaries</button>
  <button data-tab="consult">Consultants</button>
  <button data-tab="rules">Rules</button>
  <button data-tab="reports">ITR</button>
</nav>
<dialog id="dlg">
  <div class="row"><b class="grow" id="d_amt"></b><span class="mute" id="d_date"></span></div>
  <p id="d_narr" style="word-break:break-word;margin:8px 0"></p>
  <label for="d_cat">Category</label><select id="d_cat"></select>
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
  <label for="b_cat">Category</label><select id="b_cat"></select>
  <label for="b_clinic">Clinic</label><select id="b_clinic"></select>
  <label id="b_rulewrap"><input type="checkbox" id="b_rule" checked> Remember these names for future imports</label>
  <div class="row" style="margin-top:14px;justify-content:flex-end">
    <button id="b_cancel">Cancel</button><button class="pri" id="b_save">Apply to all shown</button></div>
</dialog>
<dialog id="gdlg">
  <div class="row"><b class="grow" id="g_title"></b><span class="num" id="g_amt"></span></div>
  <p class="mute" id="g_sample" style="word-break:break-word;margin:8px 0"></p>
  <label for="g_cat">Category</label><select id="g_cat"></select>
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
function catOptions(first){
  let h=first||'';
  for(const [key,label] of S.groups){
    h+='<optgroup label="'+esc(label)+'">'+S.cats.filter(c=>c[1]===key)
      .map(c=>'<option>'+esc(c[0])+'</option>').join('')+'</optgroup>';}
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
    keepValue(id,catOptions(id==='d_cat'?'<option value="">Unsorted</option>':''));
  for(const id of ['d_clinic','b_clinic','r_clinic','g_clinic'])
    keepValue(id,clinicOptions('<option value="">No clinic</option>'));
  keepValue('f_cat',catOptions('<option value="">All categories</option><option value="__none__">Unsorted</option>'));
  keepValue('rp_clinic',clinicOptions('<option value="">All clinics</option>'));
  keepValue('f_month','<option value="">Whole year</option>'+
    fyMonths().map(m=>'<option value="'+m[0]+'">'+m[1]+'</option>').join(''));
  $('rlist').innerHTML=S.rules.length?S.rules.map(r=>
    '<div class="tx" style="cursor:default"><div class="grow"><b>'+esc(r.pattern)+'</b> <span class="mute">'+
    (r.field==='payee'?'name is exactly this, ':'narration contains this, ')+
    ({any:'in or out',in:'money in',out:'money out'}[r.dir]||'')+'</span><div class="mute">'+esc(r.category)+
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
      ' <span class="chip '+(t.category?'':'none')+'">'+esc(t.category||'Unsorted')+
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
    h+='<div class="card scroll"><h3>'+esc(label)+'</h3><table><tr><th>Category</th><th>Entries</th>'+
      (one?'<th>Amount</th>':'<th>Money in</th><th>Money out</th>')+'</tr>';
    let tn=0,ta=0,tc=0,td=0;
    for(const c of names){const k=cat[c];tn+=k.n;ta+=one*(k.c-k.d);tc+=k.c;td+=k.d;
      h+='<tr class="go" data-cat="'+esc(c)+'"><td>'+esc(c)+'</td><td>'+k.n+'</td>'+
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
  let t='<tr><th>Category</th>'+months.map(m=>'<th>'+m[1].slice(0,3)+'</th>').join('')+'<th>Total</th></tr>';
  const line=(name,fn,cls)=>'<tr class="'+(cls||'')+'"><td>'+esc(name)+'</td>'+
    months.map(m=>'<td class="num">'+(fn(m[0])?r0(fn(m[0])):'')+'</td>').join('')+
    '<td class="num"><b>'+r0(all(fn))+'</b></td></tr>';
  for(const [g,label] of [['receipts','Clinic receipts'],['expenses','Clinic expenses']]){
    t+='<tr class="grp"><td colspan="14">'+label+'</td></tr>';
    for(const c of inGroup(g))t+=line(c,m=>sum(c,m));
    t+=line('Total',m=>tot(g,m),'tot');}
  t+=line('Clinic profit',m=>tot('receipts',m)-tot('expenses',m),'tot');
  $('rtable').innerHTML=t;
  await loadItr();
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
  $('abank').textContent=A.bank?'Bank payments sorted as Investments in '+fyLabel(+y)+': '+inr(A.bank)+'. Add those purchases here too so the totals are complete.':'';
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
  if(tab==='staff')return loadStaff();if(tab==='banking')return loadCash();
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
$('heads').onclick=run(async e=>{const tr=e.target.closest('tr.go');if(!tr)return;
  payeeFilter='';$('f_account').value='';$('f_month').value='';$('f_dir').value='';$('f_q').value='';
  await go('txns');$('f_cat').value=tr.dataset.cat;await loadTxns(true);});
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
  $('bdlg').close();toast('Sorted '+r.changed+' entries as '+cat);await loadState();await refresh();});
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
  $('gdlg').close();toast('Sorted '+r.changed+' entries as '+$('g_cat').value);await loadState();await loadSort();});
$('r_add').onclick=run(async()=>{
  const r=await api('/api/rule',{pattern:$('r_pat').value,dir:$('r_dir').value,category:$('r_cat').value,clinic:$('r_clinic').value});
  $('r_pat').value='';toast('Rule added and applied to '+r.changed+' entries');await loadState();});
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
run(async()=>{await loadState();await loadCash();})();
</script></body></html>"""


if __name__ == "__main__":
    init()
    print(f"Handral Books running on http://{HOST}:{PORT}  (data: {DB})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
