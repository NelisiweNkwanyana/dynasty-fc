"""
Automated check of every integrated module, using the local demo database.
Run:  python tools/local_demo/selftest.py
Prints PASS/FAIL per check. Does not touch your real database.
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
os.environ['DEMO_SQLITE'] = os.path.join(HERE, 'selftest.db')
os.environ['DOC_STORAGE_DIR'] = os.path.join(HERE, 'selftest_documents')
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['ML_AUTOTRAIN'] = 'false'
for k in [k for k in os.environ if k.startswith('ERPNEXT_') or k.startswith('ANTHROPIC_')]:
    del os.environ[k]
if os.path.exists(os.environ['DEMO_SQLITE']):
    os.remove(os.environ['DEMO_SQLITE'])
sys.path.insert(0, HERE)
import run_demo  # noqa: E402
run_demo.DB = os.environ['DEMO_SQLITE']
run_demo.build()
sys.path.insert(0, os.path.join(HERE, 'shim'))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
os.chdir(os.path.join(ROOT, 'backend'))
from app import app  # noqa: E402

S = os.path.join(HERE, 'samples')
results = []


def check(name, ok, info=''):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f'  — {info}' if info else ''))


admin = app.test_client()
admin.post('/api/staff-login', json={'email': 'admin@dynastyfc.com', 'password': 'admin123'})
anon = app.test_client()

# permissions
check('Visitors cannot list documents', anon.get('/api/docs').status_code == 401)
check('Visitors cannot read finance', anon.get('/api/fin/overview').status_code == 401 and anon.get('/api/finance').status_code == 403)

# documents
ids = {}
for fn in ['invoice_sportsmans.pdf', 'receipt_spar_photo.jpg', 'medical_cert_scan.pdf', 'registration_lindiwe.docx', 'consent_mia_lee.docx']:
    with open(os.path.join(S, fn), 'rb') as fh:
        r = admin.post('/api/docs/upload', data={'files': (io.BytesIO(fh.read()), fn)}, content_type='multipart/form-data')
    d = r.get_json()['documents'][0] if r.status_code == 200 else None
    ids[fn] = d and d['document_id']
    needs_ocr = fn.endswith(('.jpg', 'scan.pdf'))
    if needs_ocr and d and d['extraction_method'] == 'none':
        check(f'Read {fn}', True, 'skipped – Tesseract OCR not installed (brew install tesseract)')
        continue
    check(f'Read + classify {fn}', bool(d) and d['status'] in ('ready', 'needs_review'), d and f"{d['doc_type']} {d['status']}")
inv = admin.get(f"/api/docs/{ids['invoice_sportsmans.pdf']}").get_json()
check('Invoice fields extracted', inv['fields'].get('total') == 4830.0 and inv['fields'].get('document_number') == 'INV-20431', str({k: inv['fields'].get(k) for k in ('supplier', 'total', 'document_number')}))
with open(os.path.join(S, 'invoice_sportsmans.pdf'), 'rb') as fh:
    dup = admin.post('/api/docs/upload', data={'files': (io.BytesIO(fh.read()), 'copy.pdf')}, content_type='multipart/form-data').get_json()['documents'][0]
check('Duplicate invoice detected', any('duplicate' in i['message'].lower() or 'already uploaded' in i['message'] for i in dup['issues']))
r = admin.post(f"/api/docs/{ids['invoice_sportsmans.pdf']}/approve").get_json()
check('Approved invoice routed to finance', r.get('routed_to') == 'finance', r.get('message'))
r = admin.post(f"/api/docs/{ids['registration_lindiwe.docx']}/approve").get_json()
check('Approved registration created player', r.get('routed_to') == 'players', r.get('message'))

# finance
with open(os.path.join(S, 'fnb_export.csv'), 'rb') as fh:
    r = admin.post('/api/fin/import', data={'file': (io.BytesIO(fh.read()), 'fnb_export.csv')}, content_type='multipart/form-data').get_json()
check('Bank CSV imported with column mapping', r.get('stats', {}).get('total') == 9, str(r.get('stats')))
check('Duplicate + invalid lines flagged', r['stats']['duplicates'] >= 1 and r['stats']['flagged'] >= 1)
a = admin.post(f"/api/fin/batches/{r['batch_id']}/approve-clean").get_json()
check('Clean lines approved into ledger', a.get('approved', 0) >= 5, f"{a.get('approved')} approved")
with open(os.path.join(S, 'fnb_export.csv'), 'rb') as fh:
    r2 = admin.post('/api/fin/import', data={'file': (io.BytesIO(fh.read()), 'again.csv')}, content_type='multipart/form-data').get_json()
check('Re-importing the same file is caught', r2['stats']['clean'] == 0, str(r2['stats']))
o = admin.get('/api/fin/overview').get_json()
check('Finance overview from approved data', o['approved_count'] >= 6, f"balance R{o['balance']}")

# ML
import predictor  # noqa: E402
t = time.time()
version, metrics = predictor.train('selftest')
sel = metrics['comparison'][metrics['selected']]
base = metrics['comparison']['Baseline (always the historical average)']
check('Model trained on real history', metrics['n_used'] > 50000, f"{metrics['n_used']} matches in {time.time() - t:.1f}s")
check('Model beats baseline on unseen matches', sel['log_loss'] < base['log_loss'], f"log loss {sel['log_loss']} vs {base['log_loss']}, accuracy {sel['accuracy']}")
predictor.refresh_predictions()
p = admin.get('/api/ml/predictions').get_json()
fx = p['fixtures'][0] if p['fixtures'] else None
check('Fixtures get probabilities', bool(fx and fx['probabilities'] and abs(sum(fx['probabilities'].values()) - 1) < 0.01), fx and str(fx['dynasty']))
r = admin.put(f"/api/team-matches/{fx['match_id']}/result", json={'home_score': 2, 'away_score': 0}).get_json()
check('Recording a result grades the prediction', any(a.get('evaluation') for a in r.get('automation', [])), str(r.get('automation')))

# assistant
ans = lambda c, q: c.post('/api/assistant/ask', json={'question': q}).get_json()
a1 = ans(admin, 'How much have we spent this month?')
check('Assistant answers finance for admin', a1['intent'] == 'finance_summary', a1['answer'][:80])
a2 = ans(anon, 'How much have we spent this month?')
check('Assistant refuses finance for visitors', a2['intent'] == 'denied', a2['answer'])
a3 = ans(anon, 'When is the next match?')
check('Assistant answers fixtures publicly', a3['intent'] == 'fixtures', a3['answer'][:80])

# automation / audit
log = admin.get('/api/audit-log').get_json()
check('Audit trail records actions', len(log) > 10, f'{len(log)} entries')
check('Public stats endpoint', anon.get('/api/public/stats').get_json()['players'] > 0)
check('Public match centre', 'fixtures' in anon.get('/api/public/match-centre').get_json())

# ---------------------------------------------------------------- v4
import hashlib  # noqa: E402
from core import db as _db  # noqa: E402
with _db() as (_, cur):
    cur.execute("INSERT INTO users (username, password_hash, email, role, is_active) VALUES (%s,%s,%s,%s,1)",
                ('Legacy Lerato', hashlib.sha256(b'OldPass2019').hexdigest(), 'lerato@dynastyfc.com', 'Club Administrator'))
legacy = app.test_client()
ok = legacy.post('/api/staff-login', json={'email': 'lerato@dynastyfc.com', 'password': 'OldPass2019'}).status_code == 200
with _db() as (_, cur):
    cur.execute("SELECT password_hash FROM users WHERE email = 'lerato@dynastyfc.com'")
    h = cur.fetchall()[0]['password_hash']
check('Old SHA-256 password still logs in and is upgraded to salted PBKDF2', ok and h.startswith('pbkdf2:'), h[:20] + '…')
check('Default admin123 password is flagged as weak', admin.get('/api/me/security').get_json()['weak_password'] is True)
bad = admin.post('/api/me/password', json={'current_password': 'nope', 'new_password': 'Better2026x'})
weak = admin.post('/api/me/password', json={'current_password': 'admin123', 'new_password': 'password'})
good = admin.post('/api/me/password', json={'current_password': 'admin123', 'new_password': 'Dynasty-Pitch-2026'})
relog = app.test_client().post('/api/staff-login', json={'email': 'admin@dynastyfc.com', 'password': 'Dynasty-Pitch-2026'}).status_code
check('Password change: wrong current refused, weak refused, strong accepted', bad.status_code == 400 and weak.status_code == 400 and good.status_code == 200 and relog == 200)
check('Warning clears after changing password', admin.get('/api/me/security').get_json()['weak_password'] is False)
cu = admin.post('/api/admin/users', json={'username': 'Coach Thabo', 'email': 'thabo@dynastyfc.com', 'role': 'Coach'}).get_json()
check('Admin creates a coach login with a temporary password', bool(cu.get('temporary_password')), f"user {cu.get('user_id')}")
coach = app.test_client()
cl = coach.post('/api/staff-login', json={'email': 'thabo@dynastyfc.com', 'password': cu['temporary_password']})
check('Coach can log in to the dashboard', cl.status_code == 200 and coach.get('/dashboard').status_code == 200)
check('Coach is blocked from finance, accounts and messages',
      coach.get('/api/fin/overview').status_code == 403 and coach.get('/api/admin/users').status_code == 403 and coach.get('/api/messages').status_code == 403)
dup = admin.post('/api/admin/users', json={'username': 'X', 'email': 'thabo@dynastyfc.com', 'role': 'Coach'}).status_code
selfdemote = admin.put('/api/admin/users/1', json={'role': 'Coach'}).status_code
check('Duplicate emails and self-demotion are refused', dup == 409 and selfdemote == 400)

# edits
fx_all = admin.get('/api/team-matches').get_json()
up = next(m for m in fx_all if not m['is_completed'])
same = coach.put(f"/api/team-matches/{up['match_id']}", json={'home_team': 'Dynasty FC', 'away_team': 'dynasty fc'}).status_code
edit = coach.put(f"/api/team-matches/{up['match_id']}", json={'venue': 'Edited Park', 'match_time': '09:30'}).status_code
after = next(m for m in admin.get('/api/team-matches').get_json() if m['match_id'] == up['match_id'])
check('Coach edits a fixture; invalid edits are refused', same == 400 and edit == 200 and after['venue'] == 'Edited Park')
done = next(m for m in admin.get('/api/team-matches').get_json() if m['is_completed'])
corr = admin.put(f"/api/team-matches/{done['match_id']}", json={'home_score': 3, 'away_score': 3}).status_code
check('Correcting a score is accepted and validated', corr == 200 and admin.put(f"/api/team-matches/{done['match_id']}", json={'home_score': -1}).status_code == 400)
admin.post('/api/staff', json={'first_name': 'Nomsa', 'last_name': 'Dlamini', 'role': 'Coach', 'employment_status': 'Volunteer', 'contact_email': 'nomsa@example.com', 'date_joined': '2024-02-01'})
st = admin.get('/api/staff').get_json()[0]
check('Staff edit validates email and saves', admin.put(f"/api/staff/{st['staff_id']}", json={'contact_email': 'not-an-email'}).status_code == 400
      and admin.put(f"/api/staff/{st['staff_id']}", json={'role': 'Head Coach'}).status_code == 200)
admin.post('/api/inventory', json={'item_name': 'Size 5 balls', 'category': 'Equipment', 'quantity': 12, 'item_condition': 'Good', 'storage_location': 'Container'})
it = admin.get('/api/inventory').get_json()[0]
iid = it.get('item_id') or it.get('inventory_id')
check('Inventory edit and delete work, negative quantity refused',
      admin.put(f'/api/inventory/{iid}', json={'quantity': -2}).status_code == 400
      and admin.put(f'/api/inventory/{iid}', json={'quantity': 7, 'item_condition': 'Worn'}).status_code == 200
      and admin.delete(f'/api/inventory/{iid}').status_code == 200
      and coach.delete(f'/api/inventory/{iid}').status_code == 403)

# tasks
tasks = admin.get('/api/tasks').get_json()
doc_tasks = [t for t in tasks if (t.get('source_key') or '').startswith('doc:')]
check('Documents needing review create follow-up tasks', bool(doc_tasks), f'{len(tasks)} open tasks')
if doc_tasks:
    did = int(doc_tasks[0]['source_key'].split(':')[1])
    admin.post(f'/api/docs/{did}/reject', json={'reason': 'selftest'})
    still = [t for t in admin.get('/api/tasks').get_json() if t.get('source_key') == f'doc:{did}']
    check('Deciding the document closes its task automatically', not still)
np = admin.post('/api/players', json={'mysafa_id': 'ST-1', 'first_name': 'Sipho', 'last_name': 'Test', 'date_of_birth': '2011-04-02'}).get_json()
k = f"assess:{np['player_id']}"
made = any(t.get('source_key') == k for t in coach.get('/api/tasks').get_json())
coach.post(f"/api/pdi/players/{np['player_id']}/assessments", json={a: 60 for a in ('pace', 'technique', 'passing', 'defending', 'physical', 'mentality')})
closed = not any(t.get('source_key') == k for t in coach.get('/api/tasks').get_json())
check('New player → coach gets an assessment task, closed when assessed', made and closed)
nt = admin.post('/api/tasks', json={'title': 'Book the bus for Saturday', 'due_date': '2020-01-01'}).get_json()
overdue = any(t['task_id'] == nt['task_id'] and t['overdue'] for t in admin.get('/api/tasks').get_json())
check('Manual task shows as overdue and can be ticked off', overdue and admin.post(f"/api/tasks/{nt['task_id']}/done").status_code == 200)
fin_task_hidden = all(t['capability'] in ('documents', 'development', 'players', 'fixtures', 'documents_medical', 'predictions', 'teams', 'public') for t in coach.get('/api/tasks').get_json())
check('Coach does not see finance tasks', fin_task_hidden)
ov = admin.get('/api/overview').get_json()
check('Overview reports open/overdue tasks', isinstance(ov.get('tasks'), dict), str(ov.get('tasks')))
ta = ans(admin, 'What tasks are open?')
check('Assistant answers "what tasks are open"', ta['intent'] == 'tasks', ta['answer'][:90])

# analytics + pandas + public
an = admin.get('/api/analytics').get_json()
check('Analytics computed for admin', all(k in an for k in ('results', 'development', 'documents', 'finance', 'model', 'assistant')),
      f"{sum(t['played'] for t in an['results']['totals'].values())} results, {an['documents']['total']} docs")
ac = coach.get('/api/analytics').get_json()
check('Analytics hides finance from coaches', 'finance' not in ac and 'assistant' not in ac and 'development' in ac)
check('Visitors cannot open analytics or tasks', anon.get('/api/analytics').status_code == 401 and anon.get('/api/tasks').status_code == 401)
dp = metrics.get('data_preparation') or {}
check('Training data prepared with pandas (cleaning report)', bool(dp) and dp.get('clean_rows', 0) > 50000, str({k: dp.get(k) for k in list(dp)[:5]}))
ss = anon.get('/api/public/squad-stats').get_json()
check('Public squad stats for team pages', bool(ss) and all('form' in v and 'next' in v for v in ss.values()), ', '.join(f"{k}: {v['W']}-{v['D']}-{v['L']}" for k, v in ss.items()))

# payments (PayFast) – simulated ITN, no network
dn = {'amount': 250, 'name': 'Thandi Nkosi', 'email': 'thandi@example.com'}
check('Donate page exists and refuses when PayFast is not configured',
      anon.get('/donate').status_code == 200 and anon.post('/api/donate/start', json=dn).status_code == 503)
os.environ.update({'PAYFAST_MERCHANT_ID': '10000100', 'PAYFAST_MERCHANT_KEY': '46f0cd694581a', 'PAYFAST_PASSPHRASE': 'jt7NOE43FZPn',
                   'PAYFAST_SANDBOX': 'true', 'PAYFAST_VALIDATE': 'false', 'SITE_URL': 'https://example.org'})
st_ = anon.post('/api/donate/start', json=dn).get_json()
check('Donation start returns signed PayFast fields', st_.get('success') and len(st_['payfast_data']['signature']) == 32 and 'sandbox' in st_['payfast_url'])
import payments  # noqa: E402
itn = [('m_payment_id', st_['reference']), ('pf_payment_id', '1089250'), ('payment_status', 'COMPLETE'), ('item_name', 'Donation to Dynasty FC'),
       ('amount_gross', '250.00'), ('amount_fee', '-5.75'), ('amount_net', '244.25'), ('name_first', 'Thandi'), ('email_address', 'thandi@example.com'), ('merchant_id', '10000100')]
forged = anon.post('/api/payfast/notify', data=dict(itn + [('signature', 'x' * 32)])).status_code
wrong_amt = [(k, '25.00' if k == 'amount_gross' else v) for k, v in itn]
cheap = anon.post('/api/payfast/notify', data=dict(wrong_amt + [('signature', payments.signature(wrong_amt, 'jt7NOE43FZPn'))])).status_code
good_ = anon.post('/api/payfast/notify', data=dict(itn + [('signature', payments.signature(itn, 'jt7NOE43FZPn'))])).status_code
with _db() as (_, cur):
    cur.execute("SELECT payment_status FROM donations WHERE payment_ref = %s", (st_['reference'],))
    pst = cur.fetchall()[0]['payment_status']
led = admin.get('/api/fin/transactions').get_json()
led = led.get('transactions', led) if isinstance(led, dict) else led
check('PayFast ITN: forged and wrong-amount callbacks refused, genuine one marks donation paid',
      forged == 400 and cheap == 400 and good_ == 200 and pst == 'paid', f'{forged}/{cheap}/{good_} → {pst}')
check('Paid online donation reaches the finance ledger', any('Thandi' in str(t.get('description', '')) or 'DFC-' in str(t.get('reference', '')) for t in led))
for k in ('PAYFAST_MERCHANT_ID', 'PAYFAST_MERCHANT_KEY', 'PAYFAST_PASSPHRASE', 'PAYFAST_SANDBOX', 'PAYFAST_VALIDATE', 'SITE_URL'):
    os.environ.pop(k, None)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
