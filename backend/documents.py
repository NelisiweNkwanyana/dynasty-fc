"""
Dynasty FC — Smart Document Centre.

Upload once -> extract text (PDF text layer, OCR for scans/photos, DOCX, CSV/XLSX)
-> classify -> extract fields -> validate (incl. duplicates) -> staff review queue
-> on approval, route the data to the module that needs it:

  invoice / receipt     -> approved finance transaction (then accounting sync)
  bank_statement        -> finance import batch (rows go through finance validation)
  player_registration   -> creates or updates the player record
  medical_certificate   -> links to player, updates fit / injured status
  consent_form          -> links to player
  contract              -> stored + renewal reminder for finance

Files are kept in a private folder and only served to users allowed to see them.
"""
import hashlib
import io
import os
import re
import shutil
import uuid
from collections import Counter
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, send_file, session
from werkzeug.utils import secure_filename

from core import (DOC_ACCESS_CAP, as_json, audit, can, caps_for, clean_row, db, emit, from_json,
                  notify, now, on, require, run_schema, to_date, columns)

bp = Blueprint('documents', __name__)

BASE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.getenv('DOC_STORAGE_DIR', os.path.join(BASE, '..', 'private_documents'))
ALLOWED = {'pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'bmp', 'docx', 'txt', 'csv', 'xlsx'}
MAX_OCR_PAGES = int(os.getenv('DOC_MAX_OCR_PAGES', '6'))

DOC_TYPES = {
    'invoice': 'Invoice', 'receipt': 'Receipt', 'bank_statement': 'Bank statement',
    'player_registration': 'Player registration', 'medical_certificate': 'Medical certificate',
    'consent_form': 'Consent form', 'contract': 'Contract / agreement', 'other': 'Other',
}
ACCESS_FOR_TYPE = {
    'invoice': 'finance', 'receipt': 'finance', 'bank_statement': 'finance', 'contract': 'finance',
    'medical_certificate': 'medical', 'player_registration': 'general', 'consent_form': 'general',
    'other': 'general',
}
REQUIRED_PLAYER_DOCS = ('player_registration', 'consent_form', 'medical_certificate')

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS club_documents (
        document_id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255),
        original_name VARCHAR(255),
        stored_name VARCHAR(255),
        mime_ext VARCHAR(10),
        size_bytes INT,
        sha256 VARCHAR(64),
        doc_type VARCHAR(40),
        type_confidence DECIMAL(5,3),
        access_level VARCHAR(20),
        status VARCHAR(30),
        extraction_method VARCHAR(40),
        page_count INT,
        extracted_text LONGTEXT,
        fields_json LONGTEXT,
        issues_json LONGTEXT,
        player_id INT,
        routed_to VARCHAR(60),
        routed_ref VARCHAR(120),
        uploaded_by INT,
        uploaded_by_name VARCHAR(120),
        reviewed_by_name VARCHAR(120),
        reviewed_at DATETIME,
        review_note TEXT,
        created_at DATETIME,
        updated_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS player_documents (
        link_id INT AUTO_INCREMENT PRIMARY KEY,
        player_id INT NOT NULL,
        document_id INT NOT NULL,
        doc_type VARCHAR(40),
        valid_until DATE,
        created_at DATETIME
    )""",
]
INDEXES = [
    "CREATE INDEX idx_docs_status ON club_documents (status)",
    "CREATE INDEX idx_docs_sha ON club_documents (sha256)",
    "CREATE INDEX idx_pdocs_player ON player_documents (player_id)",
]


def init_documents(app):
    os.makedirs(STORAGE, exist_ok=True)
    run_schema(SCHEMA, INDEXES)
    app.register_blueprint(bp)


# =========================================================== text extraction
def tesseract_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_image(img):
    import pytesseract
    from PIL import ImageOps
    g = ImageOps.grayscale(img)
    if g.width < 1400:  # small photos OCR much better when upscaled
        k = 1400 / g.width
        g = g.resize((int(g.width * k), int(g.height * k)))
    g = ImageOps.autocontrast(g)
    return pytesseract.image_to_string(g, config='--psm 6')


def extract_text(path, ext):
    """Returns (text, method, page_count, problem)."""
    ext = ext.lower()
    try:
        if ext == 'pdf':
            import pdfplumber
            texts, ocr_pages, pages = [], 0, 0
            with pdfplumber.open(path) as pdf:
                pages = len(pdf.pages)
                for i, page in enumerate(pdf.pages):
                    t = page.extract_text() or ''
                    if len(t.strip()) < 30 and i < MAX_OCR_PAGES:
                        if not tesseract_available():
                            texts.append(t)
                            continue
                        img = page.to_image(resolution=220).original
                        t = _ocr_image(img)
                        ocr_pages += 1
                    texts.append(t)
            text = '\n'.join(texts).strip()
            method = 'pdf_text' if ocr_pages == 0 else ('ocr' if ocr_pages == pages else 'pdf_text+ocr')
            if not text and not tesseract_available():
                return '', 'none', pages, 'This PDF is a scan and OCR (Tesseract) is not installed on the server.'
            return text, method, pages, None
        if ext in ('png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'bmp'):
            if not tesseract_available():
                return '', 'none', 1, 'OCR (Tesseract) is not installed on the server, so images cannot be read.'
            from PIL import Image
            with Image.open(path) as img:
                return _ocr_image(img.convert('RGB')).strip(), 'ocr', 1, None
        if ext == 'docx':
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append('  '.join(c.text.strip() for c in row.cells))
            return '\n'.join(parts).strip(), 'docx', 1, None
        if ext in ('txt', 'csv'):
            with open(path, 'rb') as fh:
                return fh.read().decode('utf-8', errors='ignore').strip(), 'text', 1, None
        if ext == 'xlsx':
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets[:3]:
                for row in ws.iter_rows(values_only=True):
                    vals = [str(v) for v in row if v is not None]
                    if vals:
                        lines.append('  '.join(vals))
            return '\n'.join(lines).strip(), 'xlsx', len(wb.worksheets), None
    except Exception as e:
        return '', 'failed', 0, f'Could not read the file: {e}'
    return '', 'none', 0, 'Unsupported file type'


# =========================================================== classification
RULES = {
    'invoice': [('tax invoice', 6), ('invoice', 4), ('invoice no', 4), ('bill to', 3), ('due date', 3),
                ('amount due', 3), ('vat', 1), ('subtotal', 2), ('payment terms', 2), ('banking details', 1)],
    'receipt': [('receipt', 6), ('thank you for your', 2), ('cash', 1), ('change', 1), ('card', 1),
                ('paid', 2), ('till', 2), ('pos', 1), ('approved', 1), ('total paid', 3)],
    'bank_statement': [('statement', 4), ('opening balance', 5), ('closing balance', 5), ('account number', 2),
                       ('transaction', 2), ('debit', 1), ('credit', 1), ('balance', 1), ('bank', 2)],
    'player_registration': [('registration', 5), ('player', 3), ('date of birth', 3), ('mysafa', 5),
                            ('guardian', 2), ('parent', 1), ('position', 2), ('age group', 3), ('school', 1)],
    'medical_certificate': [('medical', 5), ('doctor', 3), ('dr ', 1), ('clinic', 2), ('fit to play', 6),
                            ('fit for', 3), ('practitioner', 3), ('diagnosis', 3), ('injury', 2), ('certify', 2)],
    'consent_form': [('consent', 6), ('i hereby', 2), ('permission', 3), ('indemnity', 4),
                     ('guardian signature', 4), ('parent/guardian', 3), ('photographs', 1)],
    'contract': [('agreement', 5), ('contract', 5), ('the parties', 3), ('hereby agree', 3), ('term of', 2),
                 ('sponsor', 3), ('sponsorship', 4), ('signed at', 2), ('witness', 1), ('commencement', 2)],
}


def rule_scores(text):
    t = ' ' + re.sub(r'\s+', ' ', text.lower()) + ' '
    scores = {}
    for typ, words in RULES.items():
        s = 0.0
        for w, wt in words:
            n = t.count(w)
            if n:
                s += wt * min(n, 3) ** 0.5
        scores[typ] = s
    return scores


_learned = {'n': -1, 'model': None}


def learned_model():
    """A TF-IDF classifier trained on documents staff approved (it learns from
    corrections). Retrains when the number of approved documents changes."""
    try:
        with db() as (_, cur):
            cur.execute("SELECT doc_type, extracted_text FROM club_documents WHERE status = 'approved' AND extracted_text IS NOT NULL")
            rows = [r for r in cur.fetchall() if (r['extracted_text'] or '').strip()]
    except Exception:
        return None
    if len(rows) == _learned['n']:
        return _learned['model']
    _learned['n'] = len(rows)
    counts = Counter(r['doc_type'] for r in rows)
    usable = [r for r in rows if counts[r['doc_type']] >= 3]
    if len({r['doc_type'] for r in usable}) < 2:
        _learned['model'] = None
        return None
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    m = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True), LogisticRegression(max_iter=500))
    m.fit([r['extracted_text'] for r in usable], [r['doc_type'] for r in usable])
    _learned['model'] = m
    return m


def classify(text, filename=''):
    if len(text.strip()) < 15:
        return 'other', 0.0, {}
    sc = rule_scores(text + ' ' + filename.replace('_', ' ').replace('-', ' '))
    total = sum(sc.values())
    probs = {k: (v / total if total else 0) for k, v in sc.items()}
    m = learned_model()
    if m is not None:
        p = dict(zip(m.classes_, m.predict_proba([text])[0]))
        probs = {k: 0.5 * probs.get(k, 0) + 0.5 * p.get(k, 0) for k in set(probs) | set(p)}
    best = max(probs, key=probs.get)
    if sc.get(best, 0) < 4 and (m is None):
        return 'other', round(probs[best], 3), probs
    ranked = sorted(probs.values(), reverse=True)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0)
    conf = min(0.99, 0.45 * ranked[0] + 0.55 * min(1, margin * 2.2) + (0.15 if sc.get(best, 0) >= 10 else 0))
    return best, round(conf, 3), probs


# =========================================================== field extraction
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
DATE_PATTERNS = [
    (r'\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b', 'ymd'),
    (r'\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b', 'dmy'),
    (r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b', 'd_mon_y'),
    (r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b', 'mon_d_y'),
]


def parse_date(s):
    if s is None:
        return None
    s = str(s).strip()
    for pat, kind in DATE_PATTERNS:
        m = re.search(pat, s)
        if not m:
            continue
        try:
            if kind == 'ymd':
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == 'dmy':
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if mo > 12 and d <= 12:
                    d, mo = mo, d
            elif kind == 'd_mon_y':
                mo = MONTHS.get(m.group(2)[:3].lower())
                d, y = int(m.group(1)), int(m.group(3))
            else:
                mo = MONTHS.get(m.group(1)[:3].lower())
                d, y = int(m.group(2)), int(m.group(3))
            if not mo:
                continue
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def all_dates(text):
    out = []
    for pat, _ in DATE_PATTERNS:
        for m in re.finditer(pat, text):
            d = parse_date(m.group(0))
            if d and 1950 <= d.year <= 2100:
                out.append((m.start(), d))
    out.sort()
    seen, res = set(), []
    for pos, d in out:
        if d not in seen:
            seen.add(d)
            res.append((pos, d))
    return res


AMOUNT_RE = r'(?:R|ZAR)?\s?-?\d{1,3}(?:[ ,]\d{3})*(?:[.,]\d{2})|(?:R|ZAR)\s?-?\d+(?:[.,]\d{2})?'


def parse_amount(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    t = str(s).strip()
    neg = t.startswith('-') or t.startswith('(') or t.endswith('-') or ' -' in t or t.upper().endswith('DR')
    t = re.sub(r'(?i)zar|r(?=\s?\d)|cr$|dr$|[()\s ]', '', t).replace('-', '')
    if not re.search(r'\d', t):
        return None
    if ',' in t and '.' in t:
        t = t.replace(',', '') if t.rfind('.') > t.rfind(',') else t.replace('.', '').replace(',', '.')
    elif ',' in t:
        t = t.replace(',', '.') if re.search(r',\d{2}$', t) else t.replace(',', '')
    try:
        v = round(float(t), 2)
    except ValueError:
        return None
    return -v if neg else v


def labelled_amount(text, labels):
    for lab in labels:
        for line in text.splitlines():
            if re.search(lab, line, re.I):
                nums = re.findall(AMOUNT_RE, line)
                if nums:
                    return parse_amount(nums[-1])
    return None


def labelled_value(text, labels, pattern=r'[:\-#]?\s*(.+)'):
    for lab in labels:
        m = re.search(lab + pattern, text, re.I)
        if m:
            v = m.group(1).strip().split('\n')[0].strip(' :.-')
            if v:
                return v[:120]
    return None


def labelled_date(text, labels):
    for lab in labels:
        for line in text.splitlines():
            if re.search(lab, line, re.I):
                d = parse_date(line)
                if d:
                    return d
    return None


EMAIL_RE = r'[\w.+-]+@[\w-]+\.[\w.-]+'
PHONE_RE = r'(?:\+27|0)(?:[\s-]?\d){9}'


def guess_category(text):
    t = text.lower()
    table = [('Equipment', ['ball', 'cone', 'bib', 'kit', 'jersey', 'boots', 'equipment', 'net', 'sports']),
             ('Transport', ['taxi', 'bus', 'transport', 'fuel', 'petrol', 'diesel', 'uber', 'shuttle']),
             ('Facilities', ['pitch', 'field hire', 'venue', 'stadium', 'facility', 'ground']),
             ('Refreshments', ['water', 'food', 'catering', 'lunch', 'snack', 'refreshment', 'drinks']),
             ('Medical', ['medical', 'physio', 'first aid', 'pharmacy', 'clinic']),
             ('Registration & fees', ['registration', 'affiliation', 'safa', 'league fee', 'referee']),
             ('Sponsorship', ['sponsor']),
             ('Donations', ['donation', 'donor'])]
    for cat, words in table:
        if any(w in t for w in words):
            return cat
    return 'General'


def luhn_ok(num):
    digits = [int(c) for c in num]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def first_meaningful_line(text, skip=('invoice', 'tax', 'receipt', 'page', 'statement')):
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 3 and not any(w in s.lower() for w in skip) and not re.search(r'\d{4,}', s):
            return s[:120]
    return None


def extract_fields(doc_type, text):
    f = {}
    dates = all_dates(text)
    emails = re.findall(EMAIL_RE, text)
    phones = [re.sub(r'[\s-]', '', p) for p in re.findall(PHONE_RE, text)]
    if doc_type in ('invoice', 'receipt'):
        f['supplier'] = (labelled_value(text, [r'\bfrom\b', r'\bsupplier\b', r'\bvendor\b', r'\bissued by\b'])
                         or first_meaningful_line(text))
        num = re.search(r'(?:invoice|inv|receipt|tax invoice)\s*(?:no\.?|number|num|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})', text, re.I)
        f['document_number'] = num.group(1) if num else None
        f['document_date'] = (labelled_date(text, [r'invoice date', r'receipt date', r'\bdate\b(?!\s*of birth)', r'issued'])
                              or (dates[0][1] if dates else None))
        f['due_date'] = labelled_date(text, [r'due date', r'payment due', r'due by']) if doc_type == 'invoice' else None
        f['subtotal'] = labelled_amount(text, [r'sub\s*-?\s*total', r'total excl', r'amount excl'])
        f['vat'] = labelled_amount(text, [r'\bvat\b', r'\btax\b(?!\s*invoice)'])
        f['total'] = labelled_amount(text, [r'(?:grand|invoice)\s+total', r'amount\s+due', r'balance\s+due', r'total\s+(?:incl|due|paid|amount)', r'\btotal\b'])
        if f['total'] is None:
            amts = [parse_amount(a) for a in re.findall(AMOUNT_RE, text)]
            amts = [a for a in amts if a]
            f['total'] = max(amts) if amts else None
        f['currency'] = 'ZAR'
        f['category'] = guess_category(text)
        f['description'] = (labelled_value(text, [r'\bdescription\b', r'\bitems?\b(?=\s*:)', r'\bin respect of\b']) or f'{DOC_TYPES[doc_type]} from {f["supplier"] or "supplier"}')[:200]
    elif doc_type == 'bank_statement':
        acc = re.search(r'account\s*(?:no\.?|number)?\s*[:#]?\s*(\d[\d\s-]{5,})', text, re.I)
        f['account_last4'] = re.sub(r'\D', '', acc.group(1))[-4:] if acc else None
        f['opening_balance'] = labelled_amount(text, [r'opening balance'])
        f['closing_balance'] = labelled_amount(text, [r'closing balance'])
        f['transactions'] = statement_lines(text)
    elif doc_type == 'player_registration':
        full = labelled_value(text, [r'(?:player|full)\s*name', r'\bname of player'])
        first = labelled_value(text, [r'first\s*name', r'\bname\b(?!\s*of)'])
        last = labelled_value(text, [r'surname', r'last\s*name'])
        if full and not (first and last):
            parts = full.split()
            first, last = (parts[0], ' '.join(parts[1:])) if len(parts) > 1 else (full, last)
        f['first_name'] = (first or '').split('  ')[0].strip() or None
        f['last_name'] = (last or '').split('  ')[0].strip() or None
        f['date_of_birth'] = labelled_date(text, [r'date of birth', r'\bdob\b', r'born'])
        mys = re.search(r'mysafa\s*(?:id|no\.?|number)?\s*[:#]?\s*([A-Z0-9\-]{4,})', text, re.I)
        f['mysafa_id'] = mys.group(1) if mys else None
        f['position'] = labelled_value(text, [r'position'])
        team = re.search(r'(dynamights|diamond divas)', text, re.I)
        f['team_group'] = {'dynamights': 'Dynamights', 'diamond divas': 'Diamond Divas'}.get(team.group(1).lower()) if team else None
        ag = re.search(r'\bU\s?-?(\d{1,2})\b|under\s*(\d{1,2})', text, re.I)
        f['age_group'] = f"U{ag.group(1) or ag.group(2)}" if ag else None
        f['guardian_name'] = labelled_value(text, [r'(?:parent|guardian)(?:\s*/\s*guardian)?\s*name', r'next of kin'])
        f['guardian_email'] = emails[0] if emails else None
        f['guardian_phone'] = phones[0] if phones else None
        sa_id = re.search(r'\b(\d{13})\b', text)
        f['id_number_last4'] = sa_id.group(1)[-4:] if sa_id else None
        f['_id_valid'] = luhn_ok(sa_id.group(1)) if sa_id else None
        f['_id_dob'] = None
        if sa_id:
            n = sa_id.group(1)
            yy, mm, dd = int(n[:2]), int(n[2:4]), int(n[4:6])
            try:
                f['_id_dob'] = date((2000 if yy <= date.today().year % 100 else 1900) + yy, mm, dd)
            except ValueError:
                pass
    elif doc_type == 'medical_certificate':
        f['player_name'] = labelled_value(text, [r'(?:patient|player)\s*name', r'this is to certify that', r'\bname\b'])
        f['practitioner'] = labelled_value(text, [r'(?:doctor|practitioner|dr)\b\.?', r'signed'])
        f['issue_date'] = labelled_date(text, [r'\bdate\b', r'issued']) or (dates[0][1] if dates else None)
        f['valid_until'] = labelled_date(text, [r'valid until', r'expires', r'expiry', r'until'])
        low = text.lower()
        if re.search(r'not\s+fit|unfit|not\s+cleared|should not (?:play|participate)', low):
            f['fit_to_play'] = 'no'
        elif re.search(r'fit to play|fit for|cleared to|medically fit', low):
            f['fit_to_play'] = 'yes'
        else:
            f['fit_to_play'] = None
    elif doc_type == 'consent_form':
        f['player_name'] = labelled_value(text, [r'(?:player|child|participant)\s*(?:\'s)?\s*name', r'\bname of (?:player|child)'])
        f['guardian_name'] = labelled_value(text, [r'(?:parent|guardian)(?:\s*/\s*guardian)?\s*name', r'signed by', r'\bi,'])
        f['signed_date'] = labelled_date(text, [r'\bdate\b', r'signed']) or (dates[-1][1] if dates else None)
        f['consent_given'] = 'yes' if re.search(r'i (?:hereby )?(?:give|grant) (?:my )?(?:consent|permission)|i consent|consent:\s*yes', text, re.I) else None
        f['guardian_phone'] = phones[0] if phones else None
    elif doc_type == 'contract':
        f['party'] = labelled_value(text, [r'between', r'sponsor\s*name', r'party'])
        f['start_date'] = labelled_date(text, [r'commenc', r'start', r'effective'])
        f['end_date'] = labelled_date(text, [r'terminat', r'end date', r'expir', r'until'])
        f['value'] = labelled_amount(text, [r'value', r'amount', r'sum of', r'fee'])
        f['category'] = 'Sponsorship' if 'sponsor' in text.lower() else 'Contract'
    f['emails'] = emails[:3]
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in f.items()}


def statement_lines(text):
    """Parse 'date  description  amount [balance]' lines from a bank statement."""
    out = []
    for line in text.splitlines():
        d = parse_date(line[:20])
        if not d:
            continue
        rest = re.sub(r'^\s*\S+(?:\s+[A-Za-z]{3,9}\s+\d{4})?\s*', '', line, count=1)
        nums = re.findall(r'-?\(?(?:R\s?)?\d{1,3}(?:[ ,]\d{3})*(?:[.,]\d{2})\)?(?:\s?(?:CR|DR|Cr|Dr))?', rest)
        if not nums:
            continue
        amt_raw = nums[-2] if len(nums) >= 2 else nums[-1]
        desc = rest
        for n in nums:
            desc = desc.replace(n, ' ')
        desc = re.sub(r'\s+', ' ', desc).strip(' -|')
        amt = parse_amount(amt_raw)
        if amt is None or not desc:
            continue
        if not amt_raw.strip().startswith('-') and re.search(r'\b(dr|debit|payment to|purchase|fee)\b', line, re.I) and amt > 0:
            amt = -amt
        out.append({'date': d.isoformat(), 'description': desc[:200], 'amount': amt})
    return out[:500]


# =============================================================== validation
def validate(doc_type, f, sha=None, doc_id=None):
    issues = []

    def err(field, msg):
        issues.append({'level': 'error', 'field': field, 'message': msg})

    def warn(field, msg):
        issues.append({'level': 'warning', 'field': field, 'message': msg})

    today = date.today()

    def check_date(field, required=False, past_only=False):
        v = f.get(field)
        if not v:
            if required:
                err(field, 'Missing date')
            return None
        d = to_date(v)
        if not d:
            err(field, 'Not a valid date')
            return None
        if past_only and d > today + timedelta(days=1):
            err(field, 'Date is in the future')
        if d.year < 2000:
            warn(field, 'Date looks very old – please check')
        return d

    if doc_type in ('invoice', 'receipt'):
        if not f.get('supplier'):
            err('supplier', 'Supplier not found')
        if doc_type == 'invoice' and not f.get('document_number'):
            warn('document_number', 'Invoice number not found')
        check_date('document_date', required=True, past_only=True)
        total = f.get('total')
        if total in (None, ''):
            err('total', 'Total amount not found')
        else:
            try:
                total = float(total)
                if total <= 0:
                    err('total', 'Total must be more than zero')
                if total > 500000:
                    warn('total', 'Unusually large amount – please double-check')
            except (TypeError, ValueError):
                err('total', 'Total is not a number')
                total = None
        try:
            if f.get('subtotal') not in (None, '') and f.get('vat') not in (None, '') and total:
                if abs(float(f['subtotal']) + float(f['vat']) - float(total)) > 1.0:
                    warn('total', 'Subtotal + VAT does not add up to the total')
        except (TypeError, ValueError):
            pass
        if doc_type == 'invoice' and f.get('due_date'):
            dd, di = to_date(f['due_date']), to_date(f.get('document_date'))
            if dd and di and dd < di:
                warn('due_date', 'Due date is before the invoice date')
        if f.get('document_number') and f.get('supplier'):
            dup = find_duplicate_number(f['document_number'], f['supplier'], doc_id)
            if dup:
                err('document_number', f'Same supplier and number as document #{dup} – likely a duplicate')
    elif doc_type == 'bank_statement':
        if not f.get('transactions'):
            err('transactions', 'No transactions could be read from this statement')
    elif doc_type == 'player_registration':
        for fld in ('first_name', 'last_name'):
            if not f.get(fld):
                err(fld, 'Missing')
        dob = check_date('date_of_birth', required=True, past_only=True)
        if dob:
            age = (today - dob).days / 365.25
            if age < 4 or age > 21:
                warn('date_of_birth', f'Player would be {age:.0f} years old – please check')
        if not f.get('mysafa_id'):
            warn('mysafa_id', 'MYSAFA ID not found – a temporary ID will be assigned')
        if f.get('_id_valid') is False:
            err('id_number_last4', 'The ID number on the form fails its checksum')
        if f.get('_id_dob') and dob and to_date(f['_id_dob']) != dob:
            warn('date_of_birth', 'Date of birth does not match the ID number')
        if f.get('guardian_email') and not re.fullmatch(EMAIL_RE, f['guardian_email']):
            warn('guardian_email', 'Email address looks invalid')
        if not (f.get('guardian_phone') or f.get('guardian_email')):
            warn('guardian_phone', 'No guardian contact details found')
    elif doc_type == 'medical_certificate':
        if not f.get('player_name'):
            err('player_name', 'Player name not found')
        check_date('issue_date', required=True, past_only=True)
        vu = check_date('valid_until')
        if vu and vu < today:
            warn('valid_until', 'Certificate has expired')
        if f.get('fit_to_play') not in ('yes', 'no'):
            err('fit_to_play', 'Could not tell whether the player is fit to play')
    elif doc_type == 'consent_form':
        if not f.get('player_name'):
            err('player_name', 'Player name not found')
        if not f.get('guardian_name'):
            warn('guardian_name', 'Guardian name not found')
        check_date('signed_date', required=True, past_only=True)
        if f.get('consent_given') != 'yes':
            err('consent_given', 'No clear statement of consent found')
    elif doc_type == 'contract':
        if not f.get('party'):
            warn('party', 'Other party not found')
        s, e = check_date('start_date'), check_date('end_date')
        if s and e and e < s:
            err('end_date', 'End date is before the start date')
        if e and e < today:
            warn('end_date', 'This agreement has already ended')
    else:
        warn('doc_type', 'Document type is unknown – choose one so it can be routed')
    if sha:
        dup = find_duplicate_file(sha, doc_id)
        if dup:
            err('file', f'This exact file was already uploaded as document #{dup}')
    return issues


def find_duplicate_file(sha, doc_id=None):
    with db() as (_, cur):
        cur.execute("SELECT document_id, status FROM club_documents WHERE sha256 = %s AND status <> 'rejected'", (sha,))
        for r in cur.fetchall():
            # the earliest upload (or an approved copy) is the original; later uploads are the duplicates
            if r['document_id'] != doc_id and (doc_id is None or r['document_id'] < doc_id or r['status'] == 'approved'):
                return r['document_id']
    return None


def find_duplicate_number(number, supplier, doc_id=None):
    with db() as (_, cur):
        cur.execute("SELECT document_id, status, fields_json FROM club_documents WHERE doc_type IN ('invoice','receipt') AND status <> 'rejected'")
        for r in cur.fetchall():
            if r['document_id'] == doc_id:
                continue
            if doc_id is not None and r['document_id'] > doc_id and r['status'] != 'approved':
                continue
            f = from_json(r['fields_json'], {})
            if (str(f.get('document_number') or '').strip().lower() == str(number).strip().lower()
                    and str(f.get('supplier') or '').strip().lower()[:20] == str(supplier).strip().lower()[:20]):
                return r['document_id']
    return None


def status_for(issues, confidence, problem=None):
    if problem:
        return 'needs_manual_entry'
    if any(i['level'] == 'error' for i in issues) or confidence < 0.55:
        return 'needs_review'
    return 'ready'


# ================================================================ processing
def process_file(fs, title=None, forced_type=None):
    original = secure_filename(fs.filename or 'document')
    ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if ext not in ALLOWED:
        raise ValueError(f'{original}: file type .{ext} is not supported')
    data = fs.read()
    if not data:
        raise ValueError(f'{original}: the file is empty')
    sha = hashlib.sha256(data).hexdigest()
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(STORAGE, stored)
    with open(path, 'wb') as fh:
        fh.write(data)
    text, method, pages, problem = extract_text(path, ext)
    if forced_type in DOC_TYPES:
        doc_type, conf = forced_type, 1.0
    else:
        doc_type, conf, _ = classify(text, original)
    fields = extract_fields(doc_type, text) if text else {}
    issues = validate(doc_type, fields, sha) if not problem else [{'level': 'error', 'field': 'file', 'message': problem}]
    status = status_for(issues, conf, problem)
    with db() as (_, cur):
        cur.execute("""INSERT INTO club_documents (title, original_name, stored_name, mime_ext, size_bytes, sha256, doc_type,
                       type_confidence, access_level, status, extraction_method, page_count, extracted_text, fields_json,
                       issues_json, uploaded_by, uploaded_by_name, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    ((title or original)[:255], original[:255], stored, ext, len(data), sha, doc_type, conf,
                     ACCESS_FOR_TYPE[doc_type], status, method, pages, text, as_json(fields), as_json(issues),
                     session.get('user_id'), session.get('username'), now(), now()))
        doc_id = cur.lastrowid
        audit('document_uploaded', 'document', doc_id, {'type': doc_type, 'confidence': conf, 'status': status}, cur=cur)
        if status != 'ready':
            notify('document', f'Review needed: {original}',
                   f'{DOC_TYPES[doc_type]} · {sum(1 for i in issues if i["level"] == "error")} issue(s) to resolve',
                   f'/dashboard#documents/{doc_id}', capability=DOC_ACCESS_CAP[ACCESS_FOR_TYPE[doc_type]], cur=cur)
    emit('document.processed', document_id=doc_id, doc_type=doc_type, status=status)
    return doc_id


def load_doc(doc_id, check_access=True):
    with db() as (_, cur):
        cur.execute("SELECT * FROM club_documents WHERE document_id = %s", (doc_id,))
        rows = cur.fetchall()
    if not rows:
        return None
    d = rows[0]
    if check_access and not can(DOC_ACCESS_CAP.get(d['access_level'], 'admin')):
        return 'forbidden'
    return d


def public_doc(d, full=False):
    out = clean_row({k: v for k, v in d.items() if k not in ('extracted_text', 'fields_json', 'issues_json', 'stored_name')})
    out['type_label'] = DOC_TYPES.get(d['doc_type'], d['doc_type'])
    out['fields'] = {k: v for k, v in from_json(d.get('fields_json'), {}).items() if not k.startswith('_')}
    out['issues'] = from_json(d.get('issues_json'), [])
    out['can_approve'] = can_approve(d)
    if full:
        out['extracted_text'] = d.get('extracted_text') or ''
    return out


def can_approve(d):
    lvl = d['access_level']
    if lvl == 'finance':
        return can('finance_approve')
    return can(DOC_ACCESS_CAP.get(lvl, 'admin'))


# ================================================================== routing
def find_player(cur, name=None, mysafa_id=None, dob=None):
    pcols = columns(cur, 'players')
    if mysafa_id and 'mysafa_id' in pcols:
        cur.execute("SELECT * FROM players WHERE mysafa_id = %s", (mysafa_id,))
        r = cur.fetchall()
        if r:
            return r[0]
    if not name:
        return None
    tokens = [t.lower() for t in re.findall(r"[A-Za-z'\-]+", name) if len(t) > 1]
    if not tokens:
        return None
    cur.execute("SELECT * FROM players")
    best, best_score = None, 0
    for p in cur.fetchall():
        full = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".lower()
        score = sum(1 for t in tokens if t in full.split())
        if dob and str(p.get('date_of_birth') or '')[:10] == str(dob)[:10]:
            score += 2
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 2 else None


def link_player_doc(cur, player_id, doc_id, doc_type, valid_until=None):
    cur.execute("INSERT INTO player_documents (player_id, document_id, doc_type, valid_until, created_at) VALUES (%s,%s,%s,%s,%s)",
                (player_id, doc_id, doc_type, to_date(valid_until), now()))
    cur.execute("UPDATE club_documents SET player_id = %s WHERE document_id = %s", (player_id, doc_id))


def route_document(d, f):
    """Send an approved document's data to the right module. Returns (routed_to, ref, message)."""
    t, doc_id = d['doc_type'], d['document_id']
    if t in ('invoice', 'receipt'):
        import finance
        amount = -abs(float(f['total']))
        txn_id = finance.create_transaction(
            txn_date=to_date(f.get('document_date')) or date.today(), amount=amount,
            description=f"{f.get('supplier') or 'Supplier'} – {f.get('description') or DOC_TYPES[t]}"[:250],
            category=f.get('category') or 'General', counterparty=f.get('supplier'),
            reference=f.get('document_number'), source='document', source_id=str(doc_id),
            status='approved', note=f'From {DOC_TYPES[t].lower()} #{doc_id}')
        return 'finance', f'txn:{txn_id}', f'Expense of R{abs(amount):,.2f} added to the finance ledger'
    if t == 'bank_statement':
        import finance
        batch_id, stats = finance.create_batch_from_rows(
            [{'date': x['date'], 'description': x['description'], 'amount': x['amount']} for x in f.get('transactions', [])],
            filename=d['original_name'], source=f'document:{doc_id}')
        return 'finance_import', f'batch:{batch_id}', f"{stats['total']} statement lines sent to the finance import queue ({stats['flagged']} flagged)"
    if t == 'player_registration':
        with db() as (_, cur):
            p = find_player(cur, f"{f.get('first_name') or ''} {f.get('last_name') or ''}", f.get('mysafa_id'), f.get('date_of_birth'))
            pcols = columns(cur, 'players')
            if p:
                upd = {}
                for src, col in (('guardian_email', 'contact_email'), ('guardian_phone', 'contact_phone'),
                                 ('position', 'position'), ('team_group', 'team_group'), ('age_group', 'age_group')):
                    if f.get(src) and col in pcols and not p.get(col):
                        upd[col] = f[src]
                if f.get('date_of_birth') and 'date_of_birth' in pcols and not p.get('date_of_birth'):
                    upd['date_of_birth'] = f['date_of_birth']
                if upd:
                    cur.execute(f"UPDATE players SET {', '.join(k + ' = %s' for k in upd)} WHERE player_id = %s",
                                [*upd.values(), p['player_id']])
                link_player_doc(cur, p['player_id'], doc_id, t)
                pid, msg = p['player_id'], f"Linked to existing player {p.get('first_name')} {p.get('last_name')}" + (f" and filled {', '.join(upd)}" if upd else '')
            else:
                vals = {'mysafa_id': f.get('mysafa_id') or f"PENDING-{doc_id}", 'first_name': f['first_name'],
                        'last_name': f['last_name'], 'date_of_birth': f.get('date_of_birth'), 'position': f.get('position'),
                        'contact_email': f.get('guardian_email'), 'contact_phone': f.get('guardian_phone'),
                        'team_group': f.get('team_group') or 'Dynamights', 'age_group': f.get('age_group') or 'U15',
                        'is_active': 1}
                cols = [c for c in vals if c in pcols]
                cur.execute(f"INSERT INTO players ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [vals[c] for c in cols])
                pid = cur.lastrowid
                link_player_doc(cur, pid, doc_id, t)
                msg = f"New player {f['first_name']} {f['last_name']} created in {vals['team_group']} {vals['age_group']}"
        emit('player.registered', player_id=pid, document_id=doc_id)
        return 'players', f'player:{pid}', msg
    if t in ('medical_certificate', 'consent_form'):
        with db() as (_, cur):
            p = find_player(cur, f.get('player_name'))
            if not p:
                raise ValueError(f"No player matches '{f.get('player_name')}'. Correct the player name, then approve again.")
            link_player_doc(cur, p['player_id'], doc_id, t, f.get('valid_until'))
            msg = f"Linked to {p.get('first_name')} {p.get('last_name')}"
            if t == 'medical_certificate' and 'is_injured' in columns(cur, 'players') and f.get('fit_to_play') in ('yes', 'no'):
                injured = 1 if f['fit_to_play'] == 'no' else 0
                cur.execute("UPDATE players SET is_injured = %s WHERE player_id = %s", (injured, p['player_id']))
                msg += ' and marked ' + ('injured (not fit to play)' if injured else 'fit to play')
                notify('player', f"{p.get('first_name')} {p.get('last_name')} is {'NOT fit' if injured else 'cleared'} to play",
                       f'Based on medical certificate #{doc_id}', '/dashboard#development', capability='players', cur=cur)
                emit('player.fitness', player_id=p['player_id'], fit=not injured, valid_until=f.get('valid_until'),
                     name=f"{p.get('first_name')} {p.get('last_name')}")
        return 'players', f"player:{p['player_id']}", msg
    if t == 'contract':
        end = to_date(f.get('end_date'))
        if end:
            notify('finance', f"Agreement with {f.get('party') or 'partner'} ends {end.isoformat()}",
                   f'Contract #{doc_id} – plan the renewal before it lapses', f'/dashboard#documents/{doc_id}', capability='finance')
        emit('contract.stored', document_id=doc_id, party=f.get('party'), end_date=f.get('end_date'))
        return 'contracts', f'document:{doc_id}', 'Stored in contracts' + (f' with renewal reminder for {end.isoformat()}' if end else '')
    return 'archive', f'document:{doc_id}', 'Archived'


def mirror_to_vault(d):
    """Keep the existing documents_vault list in step (best effort)."""
    try:
        with db() as (_, cur):
            if 'title' in columns(cur, 'documents_vault'):
                cur.execute("INSERT INTO documents_vault (title, file_name, file_path, document_type, description, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s)",
                            (d['title'], d['original_name'], f"/api/docs/{d['document_id']}/file", DOC_TYPES.get(d['doc_type']),
                             'Processed by the Smart Document Centre', d.get('uploaded_by')))
    except Exception as e:
        print(f'[documents] vault mirror skipped: {e}')


# ==================================================================== routes
def _visible_levels():
    mine = caps_for()
    return [lvl for lvl, cap in DOC_ACCESS_CAP.items() if cap in mine]


@bp.route('/api/docs', methods=['GET'])
@require('documents')
def list_docs():
    levels = _visible_levels()
    q = "SELECT * FROM club_documents WHERE access_level IN (" + ','.join(['%s'] * len(levels)) + ")"
    args = list(levels)
    if request.args.get('status'):
        q += " AND status = %s"
        args.append(request.args['status'])
    if request.args.get('type'):
        q += " AND doc_type = %s"
        args.append(request.args['type'])
    q += " ORDER BY document_id DESC LIMIT 300"
    with db() as (_, cur):
        cur.execute(q, args)
        rows = cur.fetchall()
    return jsonify({'documents': [public_doc(r) for r in rows], 'types': DOC_TYPES, 'ocr_available': tesseract_available()})


@bp.route('/api/docs/upload', methods=['POST'])
@require('documents')
def upload_docs():
    files = request.files.getlist('files') or request.files.getlist('file')
    if not files:
        return jsonify({'error': 'Choose at least one file'}), 400
    forced = request.form.get('doc_type') or None
    results, errors = [], []
    for fs in files[:20]:
        try:
            doc_id = process_file(fs, forced_type=forced)
            results.append(public_doc(load_doc(doc_id, check_access=False)))
        except Exception as e:
            errors.append(str(e))
    return jsonify({'documents': results, 'errors': errors}), (200 if results else 400)


@bp.route('/api/docs/<int:doc_id>', methods=['GET'])
@require('documents')
def get_doc(doc_id):
    d = load_doc(doc_id)
    if d is None:
        return jsonify({'error': 'Not found'}), 404
    if d == 'forbidden':
        return jsonify({'error': 'You do not have access to this document'}), 403
    return jsonify(public_doc(d, full=True))


@bp.route('/api/docs/<int:doc_id>', methods=['PUT'])
@require('documents')
def update_doc(doc_id):
    d = load_doc(doc_id)
    if d in (None, 'forbidden'):
        return jsonify({'error': 'Not found'}), 404
    if d['status'] in ('approved', 'rejected'):
        return jsonify({'error': f"This document is already {d['status']}"}), 400
    data = request.get_json(silent=True) or {}
    doc_type = data.get('doc_type') or d['doc_type']
    if doc_type not in DOC_TYPES:
        return jsonify({'error': 'Unknown document type'}), 400
    if doc_type != d['doc_type'] and not data.get('fields'):
        fields = extract_fields(doc_type, d.get('extracted_text') or '')  # re-extract for the new type
    else:
        old = from_json(d['fields_json'], {})
        fields = {**old, **(data.get('fields') or {})}
    issues = validate(doc_type, fields, d['sha256'], doc_id)
    conf = 1.0 if (doc_type != d['doc_type'] or data.get('fields')) else float(d['type_confidence'] or 0)
    status = status_for(issues, conf)
    access = ACCESS_FOR_TYPE[doc_type]
    with db() as (_, cur):
        cur.execute("""UPDATE club_documents SET doc_type=%s, type_confidence=%s, access_level=%s, fields_json=%s,
                       issues_json=%s, status=%s, title=%s, updated_at=%s WHERE document_id=%s""",
                    (doc_type, conf, access, as_json(fields), as_json(issues), status,
                     (data.get('title') or d['title'])[:255], now(), doc_id))
        audit('document_edited', 'document', doc_id, {'type': doc_type, 'status': status}, cur=cur)
    return jsonify(public_doc(load_doc(doc_id, check_access=False), full=True))


@bp.route('/api/docs/<int:doc_id>/approve', methods=['POST'])
@require('documents')
def approve_doc(doc_id):
    d = load_doc(doc_id)
    if d in (None, 'forbidden'):
        return jsonify({'error': 'Not found'}), 404
    if not can_approve(d):
        return jsonify({'error': 'Only finance approvers can approve financial documents'}), 403
    if d['status'] == 'approved':
        return jsonify({'error': 'Already approved'}), 400
    f = from_json(d['fields_json'], {})
    issues = validate(d['doc_type'], f, d['sha256'], doc_id)
    errs = [i for i in issues if i['level'] == 'error']
    if errs:
        with db() as (_, cur):
            cur.execute("UPDATE club_documents SET issues_json=%s, status='needs_review', updated_at=%s WHERE document_id=%s",
                        (as_json(issues), now(), doc_id))
        return jsonify({'error': 'Fix these before approving: ' + '; '.join(f"{i['field']}: {i['message']}" for i in errs),
                        'issues': issues}), 400
    try:
        routed_to, ref, msg = route_document(d, f)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    with db() as (_, cur):
        cur.execute("""UPDATE club_documents SET status='approved', routed_to=%s, routed_ref=%s, reviewed_by_name=%s,
                       reviewed_at=%s, review_note=%s, updated_at=%s WHERE document_id=%s""",
                    (routed_to, ref, session.get('username'), now(), msg, now(), doc_id))
        audit('document_approved', 'document', doc_id, {'routed_to': routed_to, 'ref': ref, 'result': msg}, cur=cur)
    mirror_to_vault(d)
    emit('document.approved', document_id=doc_id, doc_type=d['doc_type'], routed_to=routed_to, ref=ref)
    return jsonify({'success': True, 'message': msg, 'routed_to': routed_to, 'ref': ref,
                    'document': public_doc(load_doc(doc_id, check_access=False))})


@bp.route('/api/docs/<int:doc_id>/reject', methods=['POST'])
@require('documents')
def reject_doc(doc_id):
    d = load_doc(doc_id)
    if d in (None, 'forbidden'):
        return jsonify({'error': 'Not found'}), 404
    reason = ((request.get_json(silent=True) or {}).get('reason') or '').strip()[:500]
    with db() as (_, cur):
        cur.execute("UPDATE club_documents SET status='rejected', reviewed_by_name=%s, reviewed_at=%s, review_note=%s, updated_at=%s WHERE document_id=%s",
                    (session.get('username'), now(), reason or 'Rejected', now(), doc_id))
        audit('document_rejected', 'document', doc_id, {'reason': reason}, cur=cur)
    emit('document.rejected', document_id=doc_id)
    return jsonify({'success': True})


@bp.route('/api/docs/<int:doc_id>/reprocess', methods=['POST'])
@require('documents')
def reprocess_doc(doc_id):
    d = load_doc(doc_id)
    if d in (None, 'forbidden'):
        return jsonify({'error': 'Not found'}), 404
    if d['status'] == 'approved':
        return jsonify({'error': 'Approved documents cannot be reprocessed'}), 400
    path = os.path.join(STORAGE, d['stored_name'])
    text, method, pages, problem = extract_text(path, d['mime_ext'])
    doc_type, conf, _ = classify(text, d['original_name'])
    fields = extract_fields(doc_type, text) if text else {}
    issues = validate(doc_type, fields, d['sha256'], doc_id) if not problem else [{'level': 'error', 'field': 'file', 'message': problem}]
    with db() as (_, cur):
        cur.execute("""UPDATE club_documents SET doc_type=%s, type_confidence=%s, access_level=%s, status=%s, extraction_method=%s,
                       page_count=%s, extracted_text=%s, fields_json=%s, issues_json=%s, updated_at=%s WHERE document_id=%s""",
                    (doc_type, conf, ACCESS_FOR_TYPE[doc_type], status_for(issues, conf, problem), method, pages, text,
                     as_json(fields), as_json(issues), now(), doc_id))
    return jsonify(public_doc(load_doc(doc_id, check_access=False), full=True))


@bp.route('/api/docs/<int:doc_id>/file', methods=['GET'])
@require('documents')
def doc_file(doc_id):
    d = load_doc(doc_id)
    if d in (None, 'forbidden'):
        return jsonify({'error': 'Not found'}), 404
    path = os.path.join(STORAGE, d['stored_name'])
    if not os.path.exists(path):
        return jsonify({'error': 'The stored file is missing (server storage was reset)'}), 410
    audit('document_viewed', 'document', doc_id)
    return send_file(path, download_name=d['original_name'], as_attachment=request.args.get('download') == '1')


@bp.route('/api/docs/<int:doc_id>', methods=['DELETE'])
@require('admin')
def delete_doc(doc_id):
    d = load_doc(doc_id, check_access=False)
    if not d:
        return jsonify({'error': 'Not found'}), 404
    with db() as (_, cur):
        cur.execute("DELETE FROM player_documents WHERE document_id = %s", (doc_id,))
        cur.execute("DELETE FROM club_documents WHERE document_id = %s", (doc_id,))
        audit('document_deleted', 'document', doc_id, {'name': d['original_name']}, cur=cur)
    try:
        os.remove(os.path.join(STORAGE, d['stored_name']))
    except OSError:
        pass
    return jsonify({'success': True})


def outstanding_documents():
    with db() as (_, cur):
        cur.execute("SELECT player_id, first_name, last_name, team_group, age_group FROM players WHERE is_active = 1")
        players = cur.fetchall()
        cur.execute("SELECT pd.player_id, pd.doc_type, pd.valid_until FROM player_documents pd "
                    "JOIN club_documents cd ON cd.document_id = pd.document_id WHERE cd.status = 'approved'")
        have = {}
        for r in cur.fetchall():
            vu = to_date(r['valid_until'])
            if r['doc_type'] == 'medical_certificate' and vu and vu < date.today():
                continue
            have.setdefault(r['player_id'], set()).add(r['doc_type'])
    out = []
    for p in players:
        missing = [t for t in REQUIRED_PLAYER_DOCS if t not in have.get(p['player_id'], set())]
        if missing:
            out.append({**clean_row(p), 'missing': missing, 'missing_labels': [DOC_TYPES[m] for m in missing]})
    return out


@bp.route('/api/docs/outstanding', methods=['GET'])
@require('documents')
def outstanding():
    rows = outstanding_documents()
    return jsonify({'players': rows, 'required': [DOC_TYPES[t] for t in REQUIRED_PLAYER_DOCS]})


@bp.route('/api/docs/stats', methods=['GET'])
@require('documents')
def doc_stats():
    levels = _visible_levels()
    with db() as (_, cur):
        cur.execute("SELECT status, doc_type, access_level FROM club_documents")
        rows = [r for r in cur.fetchall() if r['access_level'] in levels]
    return jsonify({'by_status': Counter(r['status'] for r in rows), 'by_type': Counter(r['doc_type'] for r in rows),
                    'total': len(rows), 'ocr_available': tesseract_available()})


def search_documents(query, caps, limit=5):
    """TF-IDF search over approved documents the user may see (used by the assistant)."""
    levels = [lvl for lvl, cap in DOC_ACCESS_CAP.items() if cap in caps]
    if not levels:
        return []
    with db() as (_, cur):
        cur.execute("SELECT document_id, title, doc_type, access_level, extracted_text, fields_json, created_at FROM club_documents "
                    "WHERE status = 'approved' AND access_level IN (" + ','.join(['%s'] * len(levels)) + ")", levels)
        docs = [r for r in cur.fetchall() if (r['extracted_text'] or '').strip()]
    if not docs:
        return []
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    corpus = [f"{d['title']} {DOC_TYPES.get(d['doc_type'], '')} {d['extracted_text']}" for d in docs]
    vec = TfidfVectorizer(stop_words='english', sublinear_tf=True)
    X = vec.fit_transform(corpus)
    sims = cosine_similarity(vec.transform([query]), X)[0]
    ranked = sorted(zip(sims, docs), key=lambda x: -x[0])
    out = []
    for s, d in ranked[:limit]:
        if s < 0.04:
            break
        text = d['extracted_text']
        words = [w for w in re.findall(r'\w{4,}', query.lower())]
        pos = min([text.lower().find(w) for w in words if text.lower().find(w) >= 0] or [0])
        snippet = re.sub(r'\s+', ' ', text[max(0, pos - 80): pos + 220]).strip()
        out.append({'document_id': d['document_id'], 'title': d['title'], 'type': DOC_TYPES.get(d['doc_type']),
                    'score': round(float(s), 3), 'snippet': snippet, 'fields': from_json(d['fields_json'], {})})
    return out
