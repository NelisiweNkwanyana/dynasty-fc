"""
Dynasty FC — Automated finance.

Import bank/spreadsheet transactions (CSV or Excel) into a staging area ->
auto-map columns -> validate -> detect duplicates -> auto-categorise ->
staff approve (clean rows in one click, flagged rows individually) ->
approved ledger -> budget checks -> sync to the open-source accounting
system (ERPNext) through its REST API.

Approved documents (invoices/receipts) and paid donations arrive here
automatically, so nothing is captured twice.
"""
import csv
import hashlib
import io
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import requests
from flask import Blueprint, Response, jsonify, request, session

from core import (as_json, audit, can, clean_row, clean_rows, columns, db, emit, from_json, notify, now, on,
                  require, run_schema, to_date, to_float)

bp = Blueprint('finance_mod', __name__)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS finance_transactions (
        txn_id INT AUTO_INCREMENT PRIMARY KEY,
        txn_date DATE NOT NULL,
        description VARCHAR(255),
        amount DECIMAL(14,2) NOT NULL,
        category VARCHAR(80),
        counterparty VARCHAR(160),
        reference VARCHAR(120),
        source VARCHAR(30),
        source_id VARCHAR(60),
        batch_id INT,
        dedupe_key VARCHAR(64),
        status VARCHAR(20),
        note TEXT,
        created_by_name VARCHAR(120),
        approved_by_name VARCHAR(120),
        approved_at DATETIME,
        sync_status VARCHAR(20),
        sync_ref VARCHAR(120),
        sync_error TEXT,
        synced_at DATETIME,
        created_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS finance_import_batches (
        batch_id INT AUTO_INCREMENT PRIMARY KEY,
        filename VARCHAR(255),
        source VARCHAR(60),
        column_map TEXT,
        total_rows INT,
        clean_rows INT,
        flagged_rows INT,
        invalid_rows INT,
        duplicate_rows INT,
        status VARCHAR(20),
        uploaded_by_name VARCHAR(120),
        created_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS finance_import_rows (
        row_id INT AUTO_INCREMENT PRIMARY KEY,
        batch_id INT NOT NULL,
        line_no INT,
        raw_json TEXT,
        txn_date DATE,
        description VARCHAR(255),
        amount DECIMAL(14,2),
        category VARCHAR(80),
        reference VARCHAR(120),
        dedupe_key VARCHAR(64),
        check_status VARCHAR(20),
        issues_json TEXT,
        duplicate_of VARCHAR(60),
        decision VARCHAR(20),
        txn_id INT,
        decided_by_name VARCHAR(120),
        decided_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS finance_budgets (
        budget_id INT AUTO_INCREMENT PRIMARY KEY,
        category VARCHAR(80) NOT NULL,
        monthly_limit DECIMAL(14,2) NOT NULL,
        created_at DATETIME
    )""",
]
INDEXES = [
    "CREATE INDEX idx_fin_txn_date ON finance_transactions (txn_date)",
    "CREATE INDEX idx_fin_txn_key ON finance_transactions (dedupe_key)",
    "CREATE INDEX idx_fin_rows_batch ON finance_import_rows (batch_id)",
]


def init_finance(app):
    run_schema(SCHEMA, INDEXES)
    app.register_blueprint(bp)


# =========================================================== helpers
def norm_desc(s):
    s = re.sub(r'[^a-z0-9 ]', ' ', str(s or '').lower())
    s = re.sub(r'\b\d{4,}\b', ' ', s)  # strip long reference numbers
    return re.sub(r'\s+', ' ', s).strip()


def dedupe_key(d, amount, desc):
    raw = f"{to_date(d)}|{round(float(amount), 2):.2f}|{norm_desc(desc)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def tokens(s):
    return {t for t in norm_desc(s).split() if len(t) > 2}


def similar(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


CATEGORY_RULES = [
    ('Donations', ['donation', 'donor', 'payfast', 'gift']),
    ('Sponsorship', ['sponsor']),
    ('Membership & fees', ['subs', 'subscription', 'membership', 'player fee', 'registration fee', 'fees']),
    ('Equipment', ['ball', 'cones', 'bibs', 'kit', 'jersey', 'boots', 'equipment', 'sportsmans', 'totalsports']),
    ('Transport', ['taxi', 'bus', 'transport', 'fuel', 'petrol', 'diesel', 'engen', 'shell', 'sasol', 'uber', 'bolt']),
    ('Facilities', ['pitch', 'field', 'venue', 'stadium', 'ground hire', 'facility']),
    ('Refreshments', ['water', 'food', 'catering', 'lunch', 'snack', 'spar', 'checkers', 'shoprite', 'pick n pay', 'woolworths', 'kfc']),
    ('Medical', ['medical', 'physio', 'first aid', 'pharmacy', 'clicks', 'dis-chem', 'dischem']),
    ('League & referees', ['safa', 'league', 'referee', 'affiliation', 'tournament']),
    ('Bank charges', ['fee', 'charge', 'service fee', 'admin fee', 'monthly fee']),
]


def categorise(desc, amount, history=None):
    t = ' ' + norm_desc(desc) + ' '
    # learned: the category staff approved most often for transactions with similar wording
    if history:
        votes = Counter()
        tk = tokens(desc)
        for h_desc, h_cat in history:
            if h_cat and tk and len(tk & tokens(h_desc)) >= max(1, min(2, len(tk))):
                votes[h_cat] += 1
        if votes:
            return votes.most_common(1)[0][0]
    for cat, words in CATEGORY_RULES:
        if any(f' {w}' in t for w in words):
            return cat
    return 'Other income' if amount and amount > 0 else 'General expense'


def category_history():
    try:
        with db() as (_, cur):
            cur.execute("SELECT description, category FROM finance_transactions WHERE status = 'approved' AND source <> 'donation' ORDER BY txn_id DESC LIMIT 2000")
            return [(r['description'], r['category']) for r in cur.fetchall()]
    except Exception:
        return []


# =========================================================== ledger writes
def create_transaction(txn_date, amount, description, category=None, counterparty=None, reference=None,
                       source='manual', source_id=None, batch_id=None, status='pending', note=None, cur=None):
    key = dedupe_key(txn_date, amount, description)
    args = (to_date(txn_date), description[:255] if description else None, round(float(amount), 2), (category or '')[:80] or None,
            (counterparty or '')[:160] or None, (reference or '')[:120] or None, source, source_id, batch_id, key,
            status, note, session.get('username') if _req() else 'system',
            session.get('username') if (_req() and status == 'approved') else None,
            now() if status == 'approved' else None,
            'pending' if status == 'approved' else None, now())
    sql = """INSERT INTO finance_transactions (txn_date, description, amount, category, counterparty, reference, source,
             source_id, batch_id, dedupe_key, status, note, created_by_name, approved_by_name, approved_at, sync_status, created_at)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
    if cur is not None:
        cur.execute(sql, args)
        txn_id = cur.lastrowid
    else:
        with db() as (_, c):
            c.execute(sql, args)
            txn_id = c.lastrowid
    audit('finance_txn_created', 'finance_transaction', txn_id, {'amount': amount, 'source': source, 'status': status}, cur=cur)
    if status == 'approved':
        emit('finance.transaction.approved', txn_id=txn_id)
    return txn_id


def _req():
    try:
        from flask import has_request_context
        return has_request_context()
    except Exception:
        return False


# =========================================================== file parsing
HEADER_ALIASES = {
    'date': ['date', 'transaction date', 'trans date', 'posting date', 'value date', 'txn date', 'datum'],
    'description': ['description', 'details', 'narrative', 'transaction description', 'reference description',
                    'memo', 'particulars', 'transaction details', 'payee', 'beneficiary'],
    'amount': ['amount', 'value', 'transaction amount', 'amount (zar)', 'amount zar', 'amt'],
    'debit': ['debit', 'debits', 'withdrawal', 'withdrawals', 'money out', 'paid out', 'debit amount'],
    'credit': ['credit', 'credits', 'deposit', 'deposits', 'money in', 'paid in', 'credit amount'],
    'reference': ['reference', 'ref', 'cheque number', 'transaction id', 'ref no', 'reference number'],
    'category': ['category', 'type'],
    'balance': ['balance', 'running balance'],
}


def _match_header(h):
    h = re.sub(r'\s+', ' ', str(h or '').strip().lower())
    for key, names in HEADER_ALIASES.items():
        if h in names:
            return key
    for key, names in HEADER_ALIASES.items():
        if any(h.startswith(n) for n in names):
            return key
    return None


def read_table(filename, data):
    """Return (header_row, data_rows) as lists of strings, locating the header
    even when a bank export has a few summary lines above it."""
    ext = filename.rsplit('.', 1)[-1].lower()
    if ext in ('xlsx', 'xlsm'):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    elif ext in ('csv', 'txt'):
        text = data.decode('utf-8-sig', errors='ignore')
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        grid = list(csv.reader(io.StringIO(text), dialect))
    else:
        raise ValueError('Upload a .csv or .xlsx file')
    grid = [r for r in grid if any(str(c or '').strip() for c in r)]
    for i, row in enumerate(grid[:25]):
        keys = {_match_header(c) for c in row}
        if 'date' in keys and ({'amount', 'debit', 'credit'} & keys):
            return [str(c or '').strip() for c in row], grid[i + 1:]
    raise ValueError('Could not find a header row with a Date column and an Amount (or Debit/Credit) column')


def column_map(header):
    m = {}
    for idx, h in enumerate(header):
        k = _match_header(h)
        if k and k not in m:
            m[k] = idx
    return m


def parse_rows(filename, data):
    from documents import parse_amount, parse_date
    header, rows = read_table(filename, data)
    cmap = column_map(header)
    out = []
    for n, r in enumerate(rows, start=1):
        def cell(k):
            i = cmap.get(k)
            return r[i] if i is not None and i < len(r) else None
        raw = {header[i]: (r[i].isoformat() if isinstance(r[i], (date, datetime)) else r[i])
               for i in range(min(len(header), len(r))) if header[i]}
        d = cell('date')
        d = d.date() if isinstance(d, datetime) else (d if isinstance(d, date) else parse_date(d))
        if cmap.get('amount') is not None:
            amount = parse_amount(cell('amount'))
        else:
            deb, cre = parse_amount(cell('debit')), parse_amount(cell('credit'))
            amount = None
            if cre not in (None, 0):
                amount = abs(cre)
            if deb not in (None, 0):
                amount = -abs(deb)
            if amount is None and (deb == 0 or cre == 0):
                amount = 0.0
        out.append({'line_no': n, 'raw': raw, 'date': d, 'description': str(cell('description') or '').strip(),
                    'amount': amount, 'reference': str(cell('reference') or '').strip() or None,
                    'category': str(cell('category') or '').strip() or None})
    return header, cmap, out


# =========================================================== validation
def check_rows(rows):
    """Adds issues/check_status/duplicate_of to each row."""
    today = date.today()
    with db() as (_, cur):
        cur.execute("SELECT txn_id, txn_date, amount, description, reference, dedupe_key FROM finance_transactions WHERE status <> 'rejected'")
        existing = cur.fetchall()
        cur.execute("SELECT row_id, dedupe_key FROM finance_import_rows WHERE decision = 'pending'")
        staged = {r['dedupe_key']: r['row_id'] for r in cur.fetchall() if r['dedupe_key']}
    by_key = {e['dedupe_key']: e['txn_id'] for e in existing}
    amounts = sorted(abs(float(e['amount'])) for e in existing) or [abs(r['amount']) for r in rows if r.get('amount')]
    median = amounts[len(amounts) // 2] if amounts else 0
    by_amount = defaultdict(list)
    for e in existing:
        by_amount[round(float(e['amount']), 2)].append(e)
    seen = {}
    history = category_history()
    for r in rows:
        issues = []
        if not r['date']:
            issues.append({'level': 'error', 'field': 'date', 'message': 'Missing or unreadable date'})
        elif r['date'] > today + timedelta(days=1):
            issues.append({'level': 'error', 'field': 'date', 'message': 'Date is in the future'})
        elif r['date'] < today - timedelta(days=730):
            issues.append({'level': 'warning', 'field': 'date', 'message': 'More than two years old'})
        if r['amount'] is None:
            issues.append({'level': 'error', 'field': 'amount', 'message': 'Missing or unreadable amount'})
        elif r['amount'] == 0:
            issues.append({'level': 'error', 'field': 'amount', 'message': 'Amount is zero'})
        elif median and abs(r['amount']) > max(10000, 10 * median):
            issues.append({'level': 'warning', 'field': 'amount', 'message': f'Much larger than usual (typical ≈ R{median:,.0f})'})
        if not r['description']:
            issues.append({'level': 'error', 'field': 'description', 'message': 'Missing description'})
        r['duplicate_of'] = None
        status = 'clean'
        if r['date'] and r['amount'] not in (None, 0) and r['description']:
            key = dedupe_key(r['date'], r['amount'], r['description'])
            r['dedupe_key'] = key
            if key in by_key:
                r['duplicate_of'] = f"txn:{by_key[key]}"
                issues.append({'level': 'error', 'field': 'duplicate', 'message': f'Already in the ledger (transaction #{by_key[key]})'})
                status = 'duplicate'
            elif key in seen:
                r['duplicate_of'] = f"line:{seen[key]}"
                issues.append({'level': 'error', 'field': 'duplicate', 'message': f'Same as line {seen[key]} in this file'})
                status = 'duplicate'
            elif key in staged:
                r['duplicate_of'] = f"row:{staged[key]}"
                issues.append({'level': 'error', 'field': 'duplicate', 'message': 'Already waiting for approval in another import'})
                status = 'duplicate'
            else:
                seen[key] = r['line_no']
                for e in by_amount.get(round(r['amount'], 2), []):
                    ed = to_date(e['txn_date'])
                    if ed and abs((ed - r['date']).days) <= 3 and (similar(e['description'], r['description']) >= 0.4
                                                                  or (r['reference'] and r['reference'] == e['reference'])):
                        r['duplicate_of'] = f"txn:{e['txn_id']}"
                        issues.append({'level': 'warning', 'field': 'duplicate',
                                       'message': f"Looks like transaction #{e['txn_id']} ({ed.isoformat()}, {e['description']})"})
                        break
        else:
            r['dedupe_key'] = None
        if not r.get('category'):
            r['category'] = categorise(r['description'], r['amount'], history)
        if status != 'duplicate':
            if any(i['level'] == 'error' for i in issues):
                status = 'invalid'
            elif issues:
                status = 'flagged'
        r['issues'] = issues
        r['check_status'] = status
    return rows


def create_batch_from_rows(rows, filename, source='upload', cmap=None):
    """rows: dicts with date (str/date), description, amount, optional reference/category/raw."""
    from documents import parse_date
    norm = []
    for i, r in enumerate(rows, start=1):
        d = r.get('date')
        d = d if isinstance(d, date) else parse_date(d)
        norm.append({'line_no': r.get('line_no', i), 'raw': r.get('raw', r), 'date': d,
                     'description': (r.get('description') or '').strip(), 'amount': to_float(r.get('amount')),
                     'reference': r.get('reference'), 'category': r.get('category')})
    checked = check_rows(norm)
    stats = Counter(r['check_status'] for r in checked)
    with db() as (_, cur):
        cur.execute("""INSERT INTO finance_import_batches (filename, source, column_map, total_rows, clean_rows, flagged_rows,
                       invalid_rows, duplicate_rows, status, uploaded_by_name, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (filename[:255], source[:60], as_json(cmap or {}), len(checked), stats['clean'], stats['flagged'],
                     stats['invalid'], stats['duplicate'], 'open', session.get('username') if _req() else 'system', now()))
        batch_id = cur.lastrowid
        for r in checked:
            cur.execute("""INSERT INTO finance_import_rows (batch_id, line_no, raw_json, txn_date, description, amount, category,
                           reference, dedupe_key, check_status, issues_json, duplicate_of, decision)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (batch_id, r['line_no'], as_json(r['raw']), r['date'], r['description'][:255] if r['description'] else None,
                         r['amount'], (r['category'] or '')[:80] or None, (str(r['reference'] or '')[:120] or None), r['dedupe_key'], r['check_status'],
                         as_json(r['issues']), r['duplicate_of'], 'rejected' if r['check_status'] == 'duplicate' and str(r['duplicate_of']).startswith('txn') else 'pending'))
        audit('finance_import', 'finance_batch', batch_id, dict(stats), cur=cur)
        flagged = stats['flagged'] + stats['invalid']
        notify('finance', f'Finance import ready for approval: {filename}',
               f"{stats['clean']} clean, {flagged} need attention, {stats['duplicate']} duplicates skipped",
               f'/dashboard#finance/batch/{batch_id}', capability='finance', cur=cur)
    needs_decision = sum(1 for r in checked if r['check_status'] != 'clean'
                         and not (r['check_status'] == 'duplicate' and str(r['duplicate_of']).startswith('txn')))
    emit('finance.batch.created', batch_id=batch_id, flagged=needs_decision, filename=filename)
    return batch_id, {'total': len(checked), 'clean': stats['clean'], 'flagged': stats['flagged'] + stats['invalid'],
                      'duplicates': stats['duplicate']}


# =========================================================== approvals
def approve_row(cur, row, force=False):
    if row['decision'] != 'pending':
        return None
    if row['check_status'] in ('invalid', 'duplicate') and not force:
        raise ValueError(f"Line {row['line_no']} has unresolved problems")
    txn_id = create_transaction(row['txn_date'], float(row['amount']), row['description'], row['category'],
                                reference=row['reference'], source='import', source_id=f"row:{row['row_id']}",
                                batch_id=row['batch_id'], status='approved', cur=cur)
    cur.execute("UPDATE finance_import_rows SET decision='approved', txn_id=%s, decided_by_name=%s, decided_at=%s WHERE row_id=%s",
                (txn_id, session.get('username'), now(), row['row_id']))
    return txn_id


def refresh_batch(cur, batch_id):
    cur.execute("SELECT decision FROM finance_import_rows WHERE batch_id = %s", (batch_id,))
    ds = [r['decision'] for r in cur.fetchall()]
    status = 'complete' if ds and all(d != 'pending' for d in ds) else 'open'
    cur.execute("UPDATE finance_import_batches SET status = %s WHERE batch_id = %s", (status, batch_id))
    if status == 'complete':
        emit('finance.batch.completed', batch_id=batch_id)


# =========================================================== accounting (ERPNext)
class ERPNext:
    """Minimal ERPNext (open-source ERP) connector using its REST API.
    Each approved transaction becomes one Journal Entry, identified by its
    reference 'DFC-<txn_id>' so a retry never creates a second entry."""

    def __init__(self):
        self.url = (os.getenv('ERPNEXT_URL') or '').rstrip('/')
        self.key = os.getenv('ERPNEXT_API_KEY')
        self.secret = os.getenv('ERPNEXT_API_SECRET')
        self.company = os.getenv('ERPNEXT_COMPANY')
        self.bank = os.getenv('ERPNEXT_BANK_ACCOUNT')
        self.income = os.getenv('ERPNEXT_INCOME_ACCOUNT')
        self.expense = os.getenv('ERPNEXT_EXPENSE_ACCOUNT')
        self.submit = os.getenv('ERPNEXT_SUBMIT', '0') == '1'
        try:
            self.account_map = json.loads(os.getenv('ERPNEXT_ACCOUNT_MAP') or '{}')
        except ValueError:
            self.account_map = {}

    @property
    def configured(self):
        return all([self.url, self.key, self.secret, self.company, self.bank, self.income, self.expense])

    def _h(self):
        return {'Authorization': f'token {self.key}:{self.secret}', 'Accept': 'application/json'}

    def test(self):
        r = requests.get(f'{self.url}/api/method/frappe.auth.get_logged_user', headers=self._h(), timeout=15)
        r.raise_for_status()
        return r.json().get('message')

    def find(self, ref):
        r = requests.get(f'{self.url}/api/resource/Journal Entry', headers=self._h(), timeout=15,
                         params={'filters': json.dumps([['cheque_no', '=', ref]]), 'fields': json.dumps(['name'])})
        r.raise_for_status()
        data = r.json().get('data') or []
        return data[0]['name'] if data else None

    def push(self, t):
        ref = f"DFC-{t['txn_id']}"
        existing = self.find(ref)
        if existing:
            return existing
        amt = abs(float(t['amount']))
        other = self.account_map.get(t.get('category') or '') or (self.income if float(t['amount']) > 0 else self.expense)
        bank_line = {'account': self.bank, 'debit_in_account_currency': amt if float(t['amount']) > 0 else 0,
                     'credit_in_account_currency': amt if float(t['amount']) < 0 else 0}
        other_line = {'account': other, 'debit_in_account_currency': bank_line['credit_in_account_currency'],
                      'credit_in_account_currency': bank_line['debit_in_account_currency']}
        doc = {'doctype': 'Journal Entry', 'voucher_type': 'Bank Entry', 'company': self.company,
               'posting_date': str(to_date(t['txn_date'])), 'cheque_no': ref, 'cheque_date': str(to_date(t['txn_date'])),
               'user_remark': f"{t.get('description') or ''} [{t.get('category') or ''}] via Dynasty FC ({t.get('source')})"[:500],
               'accounts': [bank_line, other_line], 'docstatus': 1 if self.submit else 0}
        r = requests.post(f'{self.url}/api/resource/Journal Entry', headers=self._h(), json=doc, timeout=20)
        if r.status_code >= 400:
            try:
                msg = r.json().get('exception') or r.json().get('message') or r.text
            except ValueError:
                msg = r.text
            raise RuntimeError(f'ERPNext {r.status_code}: {str(msg)[:300]}')
        return r.json()['data']['name']


def sync_transaction(txn_id):
    erp = ERPNext()
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_transactions WHERE txn_id = %s", (txn_id,))
        rows = cur.fetchall()
        if not rows:
            return 'missing'
        t = rows[0]
        if t['status'] != 'approved':
            return 'skipped'
        if not erp.configured:
            cur.execute("UPDATE finance_transactions SET sync_status='not_configured' WHERE txn_id=%s", (txn_id,))
            return 'not_configured'
    try:
        name = erp.push(t)
        with db() as (_, cur):
            cur.execute("UPDATE finance_transactions SET sync_status='synced', sync_ref=%s, sync_error=NULL, synced_at=%s WHERE txn_id=%s",
                        (name, now(), txn_id))
            audit('accounting_synced', 'finance_transaction', txn_id, {'erpnext': name}, cur=cur)
        return 'synced'
    except Exception as e:
        with db() as (_, cur):
            cur.execute("UPDATE finance_transactions SET sync_status='failed', sync_error=%s WHERE txn_id=%s", (str(e)[:1000], txn_id))
        return 'failed'


@on('finance.transaction.approved')
def _after_approval(txn_id):
    sync_transaction(txn_id)
    check_budget_for(txn_id)


def check_budget_for(txn_id):
    with db() as (_, cur):
        cur.execute("SELECT txn_date, category, amount FROM finance_transactions WHERE txn_id = %s", (txn_id,))
        rows = cur.fetchall()
        if not rows or float(rows[0]['amount']) >= 0:
            return
        t = rows[0]
        cur.execute("SELECT monthly_limit FROM finance_budgets WHERE category = %s", (t['category'],))
        b = cur.fetchall()
        if not b:
            return
        d = to_date(t['txn_date'])
        start = d.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        cur.execute("SELECT amount FROM finance_transactions WHERE status='approved' AND category=%s AND txn_date >= %s AND txn_date < %s",
                    (t['category'], start, end))
        spent = -sum(float(r['amount']) for r in cur.fetchall() if float(r['amount']) < 0)
        limit = float(b[0]['monthly_limit'])
        if spent > limit:
            notify('finance', f"Budget exceeded: {t['category']}",
                   f"R{spent:,.2f} spent in {start.strftime('%B %Y')} against a budget of R{limit:,.2f}",
                   '/dashboard#finance', capability='finance', cur=cur)


# =========================================================== donations
def sync_donations():
    """Bring paid donations from the existing donations table into the ledger (once each)."""
    added = 0
    try:
        with db() as (_, cur):
            dcols = columns(cur, 'donations')
            if not dcols:
                return 0
            cur.execute("SELECT * FROM donations")
            dons = cur.fetchall()
            cur.execute("SELECT source_id FROM finance_transactions WHERE source = 'donation'")
            have = {r['source_id'] for r in cur.fetchall()}
            for d in dons:
                if str(d.get('payment_status') or '').lower() not in ('paid', 'complete', 'completed', 'success'):
                    continue
                sid = str(d.get('donation_id'))
                if sid in have:
                    continue
                when = to_date(d.get('paid_at') or d.get('created_at')) or date.today()
                name = 'Anonymous donor' if d.get('is_anonymous') else (d.get('donor_name') or 'Donor')
                create_transaction(when, abs(float(d.get('amount') or 0)), f'Donation – {name}', 'Donations',
                                   counterparty=name, reference=d.get('payment_ref'), source='donation', source_id=sid,
                                   status='approved', cur=cur)
                added += 1
    except Exception as e:
        print(f'[finance] donation sync skipped: {e}')
    return added


# =========================================================== routes
@bp.route('/api/fin/import', methods=['POST'])
@require('finance')
def import_file():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Choose a CSV or Excel file'}), 400
    data = f.read()
    if len(data) > 8 * 1024 * 1024:
        return jsonify({'error': 'File is larger than 8 MB'}), 400
    try:
        header, cmap, rows = parse_rows(f.filename, data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    if not rows:
        return jsonify({'error': 'No transaction rows found under the header'}), 400
    mapping = {k: header[i] for k, i in cmap.items()}
    batch_id, stats = create_batch_from_rows(rows, f.filename, 'upload', mapping)
    return jsonify({'batch_id': batch_id, 'stats': stats, 'mapping': mapping})


@bp.route('/api/fin/batches')
@require('finance')
def batches():
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_import_batches ORDER BY batch_id DESC LIMIT 50")
        rows = clean_rows(cur.fetchall())
    for r in rows:
        r['column_map'] = from_json(r.get('column_map'), {})
    return jsonify(rows)


@bp.route('/api/fin/batches/<int:batch_id>')
@require('finance')
def batch_detail(batch_id):
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_import_batches WHERE batch_id = %s", (batch_id,))
        b = cur.fetchall()
        if not b:
            return jsonify({'error': 'Not found'}), 404
        cur.execute("SELECT * FROM finance_import_rows WHERE batch_id = %s ORDER BY line_no", (batch_id,))
        rows = clean_rows(cur.fetchall())
    for r in rows:
        r['issues'] = from_json(r.pop('issues_json', None), [])
        r.pop('raw_json', None)
    batch = clean_row(b[0])
    batch['column_map'] = from_json(batch.get('column_map'), {})
    return jsonify({'batch': batch, 'rows': rows, 'can_approve': can('finance_approve')})


@bp.route('/api/fin/rows/<int:row_id>', methods=['PUT'])
@require('finance')
def edit_row(row_id):
    from documents import parse_amount, parse_date
    data = request.get_json(silent=True) or {}
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_import_rows WHERE row_id = %s", (row_id,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'Not found'}), 404
        r = rows[0]
        if r['decision'] != 'pending':
            return jsonify({'error': 'This line is already decided'}), 400
    row = {'line_no': r['line_no'],
           'date': parse_date(data['date']) if 'date' in data else to_date(r['txn_date']),
           'description': (data.get('description', r['description']) or '').strip(),
           'amount': parse_amount(data['amount']) if 'amount' in data else to_float(r['amount']),
           'reference': data.get('reference', r['reference']),
           'category': data.get('category') or r['category']}
    # re-check without comparing the row to itself in the staging table
    with db() as (_, cur):
        cur.execute("UPDATE finance_import_rows SET dedupe_key = NULL WHERE row_id = %s", (row_id,))
    row = check_rows([row])[0]
    with db() as (_, cur):
        cur.execute("""UPDATE finance_import_rows SET txn_date=%s, description=%s, amount=%s, category=%s, reference=%s,
                       dedupe_key=%s, check_status=%s, issues_json=%s, duplicate_of=%s WHERE row_id=%s""",
                    (row['date'], (row['description'] or '')[:255], row['amount'], (row['category'] or '')[:80], (str(row['reference'] or '')[:120] or None), row['dedupe_key'],
                     row['check_status'], as_json(row['issues']), row['duplicate_of'], row_id))
        _recount(cur, r['batch_id'])
    return jsonify({'success': True, 'check_status': row['check_status'], 'issues': row['issues']})


def _recount(cur, batch_id):
    cur.execute("SELECT check_status FROM finance_import_rows WHERE batch_id = %s", (batch_id,))
    c = Counter(x['check_status'] for x in cur.fetchall())
    cur.execute("UPDATE finance_import_batches SET clean_rows=%s, flagged_rows=%s, invalid_rows=%s, duplicate_rows=%s WHERE batch_id=%s",
                (c['clean'], c['flagged'], c['invalid'], c['duplicate'], batch_id))


@bp.route('/api/fin/rows/<int:row_id>/decision', methods=['POST'])
@require('finance_approve')
def decide_row(row_id):
    data = request.get_json(silent=True) or {}
    decision = data.get('decision')
    if decision not in ('approve', 'reject'):
        return jsonify({'error': 'decision must be approve or reject'}), 400
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_import_rows WHERE row_id = %s", (row_id,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'Not found'}), 404
        r = rows[0]
        if r['decision'] != 'pending':
            return jsonify({'error': 'Already decided'}), 400
        if decision == 'reject':
            cur.execute("UPDATE finance_import_rows SET decision='rejected', decided_by_name=%s, decided_at=%s WHERE row_id=%s",
                        (session.get('username'), now(), row_id))
            audit('finance_row_rejected', 'finance_row', row_id, cur=cur)
            txn_id = None
        else:
            if r['check_status'] == 'invalid':
                return jsonify({'error': 'Fix the problems on this line first'}), 400
            if r['check_status'] == 'duplicate' and not data.get('override'):
                return jsonify({'error': 'This line is a duplicate. Confirm the override to import it anyway.'}), 400
            txn_id = approve_row(cur, r, force=True)
        refresh_batch(cur, r['batch_id'])
    return jsonify({'success': True, 'txn_id': txn_id})


@bp.route('/api/fin/batches/<int:batch_id>/approve-clean', methods=['POST'])
@require('finance_approve')
def approve_clean(batch_id):
    ids = []
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_import_rows WHERE batch_id = %s AND decision = 'pending' AND check_status = 'clean'", (batch_id,))
        for r in cur.fetchall():
            ids.append(approve_row(cur, r))
        refresh_batch(cur, batch_id)
        audit('finance_batch_approved_clean', 'finance_batch', batch_id, {'approved': len(ids)}, cur=cur)
    return jsonify({'success': True, 'approved': len(ids)})


@bp.route('/api/fin/transactions')
@require('finance')
def list_txns():
    sync_donations()
    q = "SELECT * FROM finance_transactions WHERE 1=1"
    args = []
    if request.args.get('status'):
        q += " AND status = %s"
        args.append(request.args['status'])
    if request.args.get('month'):
        m = request.args['month']
        start = date.fromisoformat(m + '-01')
        q += " AND txn_date >= %s AND txn_date < %s"
        args += [start, (start + timedelta(days=32)).replace(day=1)]
    q += " ORDER BY txn_date DESC, txn_id DESC LIMIT 500"
    with db() as (_, cur):
        cur.execute(q, args)
        rows = clean_rows(cur.fetchall())
    return jsonify(rows)


@bp.route('/api/fin/transactions', methods=['POST'])
@require('finance')
def manual_txn():
    from documents import parse_amount, parse_date
    data = request.get_json(silent=True) or {}
    d = parse_date(data.get('date'))
    amount = parse_amount(data.get('amount'))
    kind = data.get('kind')
    desc = (data.get('description') or '').strip()
    if not d or amount in (None, 0) or not desc:
        return jsonify({'error': 'Date, a non-zero amount and a description are required'}), 400
    if kind == 'expense':
        amount = -abs(amount)
    elif kind == 'income':
        amount = abs(amount)
    rows = check_rows([{'line_no': 1, 'date': d, 'description': desc, 'amount': amount,
                        'reference': data.get('reference'), 'category': data.get('category')}])
    dups = [i for i in rows[0]['issues'] if i['field'] == 'duplicate']
    if dups and not data.get('override'):
        return jsonify({'error': dups[0]['message'], 'duplicate': True}), 409
    txn_id = create_transaction(d, amount, desc, rows[0]['category'], data.get('counterparty'), data.get('reference'),
                                source='manual', status='pending')
    notify('finance', 'Manual transaction awaiting approval', f'{desc} – R{amount:,.2f}', '/dashboard#finance', capability='finance')
    return jsonify({'success': True, 'txn_id': txn_id})


@bp.route('/api/fin/transactions/<int:txn_id>/<action>', methods=['POST'])
@require('finance_approve')
def txn_action(txn_id, action):
    if action not in ('approve', 'reject', 'sync'):
        return jsonify({'error': 'Unknown action'}), 400
    with db() as (_, cur):
        cur.execute("SELECT status FROM finance_transactions WHERE txn_id = %s", (txn_id,))
        r = cur.fetchall()
        if not r:
            return jsonify({'error': 'Not found'}), 404
        if action == 'sync':
            pass
        elif r[0]['status'] != 'pending':
            return jsonify({'error': f"Transaction is already {r[0]['status']}"}), 400
        elif action == 'approve':
            cur.execute("UPDATE finance_transactions SET status='approved', approved_by_name=%s, approved_at=%s, sync_status='pending' WHERE txn_id=%s",
                        (session.get('username'), now(), txn_id))
            audit('finance_txn_approved', 'finance_transaction', txn_id, cur=cur)
        else:
            cur.execute("UPDATE finance_transactions SET status='rejected' WHERE txn_id=%s", (txn_id,))
            audit('finance_txn_rejected', 'finance_transaction', txn_id, cur=cur)
    if action == 'approve':
        emit('finance.transaction.approved', txn_id=txn_id)
    if action == 'sync':
        return jsonify({'success': True, 'result': sync_transaction(txn_id)})
    return jsonify({'success': True})


@bp.route('/api/fin/overview')
@require('finance')
def overview():
    added = sync_donations()
    today = date.today()
    with db() as (_, cur):
        cur.execute("SELECT txn_date, amount, category, status, sync_status FROM finance_transactions")
        txns = cur.fetchall()
        cur.execute("SELECT * FROM finance_budgets ORDER BY category")
        budgets = clean_rows(cur.fetchall())
        cur.execute("SELECT COUNT(*) AS n FROM finance_import_rows WHERE decision = 'pending'")
        pending_rows = cur.fetchall()[0]['n']
    appr = [t for t in txns if t['status'] == 'approved']
    months = []
    m = today.replace(day=1)
    for _ in range(6):
        months.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    months.reverse()
    by_month = []
    for ms in months:
        me = (ms + timedelta(days=32)).replace(day=1)
        inm = [t for t in appr if ms <= to_date(t['txn_date']) < me]
        inc = sum(float(t['amount']) for t in inm if float(t['amount']) > 0)
        exp = -sum(float(t['amount']) for t in inm if float(t['amount']) < 0)
        by_month.append({'month': ms.strftime('%Y-%m'), 'label': ms.strftime('%b'), 'income': round(inc, 2), 'expenses': round(exp, 2)})
    cur_start = today.replace(day=1)
    this_month = [t for t in appr if to_date(t['txn_date']) >= cur_start]
    cats = defaultdict(float)
    for t in this_month:
        if float(t['amount']) < 0:
            cats[t['category'] or 'Uncategorised'] += -float(t['amount'])
    for b in budgets:
        b['spent'] = round(cats.get(b['category'], 0), 2)
        b['remaining'] = round(float(b['monthly_limit']) - b['spent'], 2)
        b['pct'] = round(100 * b['spent'] / float(b['monthly_limit']), 1) if float(b['monthly_limit']) else None
    erp = ERPNext()
    return jsonify({
        'balance': round(sum(float(t['amount']) for t in appr), 2),
        'month_income': round(sum(float(t['amount']) for t in this_month if float(t['amount']) > 0), 2),
        'month_expenses': round(-sum(float(t['amount']) for t in this_month if float(t['amount']) < 0), 2),
        'by_month': by_month,
        'expense_categories': sorted([{'category': k, 'amount': round(v, 2)} for k, v in cats.items()], key=lambda x: -x['amount']),
        'pending_transactions': sum(1 for t in txns if t['status'] == 'pending'),
        'pending_import_rows': pending_rows,
        'budgets': budgets,
        'sync': dict(Counter((t['sync_status'] or 'n/a') for t in appr)),
        'accounting': {'system': 'ERPNext', 'configured': erp.configured, 'url': erp.url or None,
                       'submit': erp.submit},
        'donations_added': added,
        'approved_count': len(appr),
    })


@bp.route('/api/fin/budgets', methods=['GET', 'POST'])
@require('finance')
def budgets():
    if request.method == 'POST':
        if not can('finance_approve'):
            return jsonify({'error': 'Only finance approvers can set budgets'}), 403
        data = request.get_json(silent=True) or {}
        cat, lim = (data.get('category') or '').strip(), to_float(data.get('monthly_limit'))
        if not cat or not lim or lim <= 0:
            return jsonify({'error': 'Category and a positive monthly limit are required'}), 400
        with db() as (_, cur):
            cur.execute("DELETE FROM finance_budgets WHERE category = %s", (cat,))
            cur.execute("INSERT INTO finance_budgets (category, monthly_limit, created_at) VALUES (%s,%s,%s)", (cat, lim, now()))
            audit('budget_set', 'budget', cat, {'limit': lim}, cur=cur)
        return jsonify({'success': True})
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_budgets ORDER BY category")
        return jsonify(clean_rows(cur.fetchall()))


@bp.route('/api/fin/budgets/<int:budget_id>', methods=['DELETE'])
@require('finance_approve')
def delete_budget(budget_id):
    with db() as (_, cur):
        cur.execute("DELETE FROM finance_budgets WHERE budget_id = %s", (budget_id,))
    return jsonify({'success': True})


@bp.route('/api/fin/categories')
@require('finance')
def categories():
    base = [c for c, _ in CATEGORY_RULES] + ['Other income', 'General expense']
    with db() as (_, cur):
        cur.execute("SELECT DISTINCT category FROM finance_transactions")
        extra = [r['category'] for r in cur.fetchall() if r['category']]
    return jsonify(sorted(set(base + extra)))


@bp.route('/api/fin/accounting/test', methods=['POST'])
@require('finance_approve')
def accounting_test():
    erp = ERPNext()
    if not erp.configured:
        return jsonify({'ok': False, 'error': 'ERPNext is not configured. Set the ERPNEXT_* environment variables.'}), 400
    try:
        return jsonify({'ok': True, 'user': erp.test()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502


@bp.route('/api/fin/accounting/sync', methods=['POST'])
@require('finance_approve')
def accounting_sync_all():
    with db() as (_, cur):
        cur.execute("SELECT txn_id FROM finance_transactions WHERE status='approved' AND (sync_status IS NULL OR sync_status IN ('pending','failed','not_configured'))")
        ids = [r['txn_id'] for r in cur.fetchall()]
    results = Counter(sync_transaction(i) for i in ids)
    return jsonify({'attempted': len(ids), 'results': results})


@bp.route('/api/fin/export.csv')
@require('finance')
def export_csv():
    with db() as (_, cur):
        cur.execute("SELECT txn_id, txn_date, description, category, counterparty, reference, amount, source, sync_status, sync_ref "
                    "FROM finance_transactions WHERE status='approved' ORDER BY txn_date, txn_id")
        rows = clean_rows(cur.fetchall())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['ID', 'Date', 'Description', 'Category', 'Counterparty', 'Reference', 'Amount (ZAR)', 'Source', 'Accounting status', 'Accounting ref'])
    for r in rows:
        w.writerow([r['txn_id'], r['txn_date'], r['description'], r['category'], r['counterparty'], r['reference'],
                    f"{float(r['amount']):.2f}", r['source'], r['sync_status'], r['sync_ref']])
    audit('finance_export', 'finance', None, {'rows': len(rows)})
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=dynasty-fc-ledger.csv'})
