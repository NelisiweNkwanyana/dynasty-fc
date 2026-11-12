"""
Dynasty FC Assistant — answers questions from the club's own records.

* Every answer is built from database queries or approved documents; when the
  data isn't there it says so instead of guessing.
* Each kind of question needs a capability (see core.ROLE_CAPS). Visitors only
  get public information; coaches don't get finance; finance documents are
  only searched for users allowed to open them.
* Optional: set ANTHROPIC_API_KEY to let Claude phrase the answer. It only
  receives the facts retrieved for this user and is told to use nothing else.
"""
import os
import re
from datetime import date, timedelta

import requests
from flask import Blueprint, jsonify, request, session

from core import as_json, audit, caps_for, clean_rows, current_role, db, now, require, run_schema, to_date

bp = Blueprint('assistant', __name__)

SCHEMA = ["""CREATE TABLE IF NOT EXISTS assistant_log (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    role VARCHAR(40),
    question TEXT,
    intent VARCHAR(40),
    answered TINYINT,
    sources TEXT,
    created_at DATETIME
)"""]

CLUB = {'name': 'Dynasty Football Club', 'founded': 2016, 'home': 'Fiat Grounds, Soweto, Johannesburg',
        'phone': '+27 63 911 1277', 'email': 'dynastyfcadmin@gmail.com', 'instagram': '@dynastyfc_2016',
        'teams': 'Dynamights (boys) and Diamond Divas (girls), Under 10 and Under 15'}


def init_assistant(app):
    run_schema(SCHEMA)
    app.register_blueprint(bp)


def R(x):
    return f"R{float(x):,.2f}".replace(',', ' ')


def pdi_squad(**kw):
    """player_dev expects a plain (tuple) cursor."""
    from database.db import get_db_connection
    from player_dev import _load_squad
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        return _load_squad(cur, **kw)
    finally:
        cur.close()
        conn.close()


def player_rows(cur):
    cur.execute("SELECT * FROM players WHERE is_active = 1")
    return cur.fetchall()


def mentioned_players(q, players):
    ql = ' ' + re.sub(r'[^a-z ]', ' ', q.lower()) + ' '
    hits = []
    for p in players:
        fn, ln = (p.get('first_name') or '').lower(), (p.get('last_name') or '').lower()
        score = (2 if fn and f' {fn} ' in ql else 0) + (2 if ln and f' {ln} ' in ql else 0)
        if score >= 2:
            hits.append((score, p))
    hits.sort(key=lambda x: -x[0])
    if hits and hits[0][0] == 4:
        return [p for s, p in hits if s == 4]
    return [p for _, p in hits]


def team_filter(q):
    ql = q.lower()
    if 'dynamight' in ql or 'boys' in ql:
        return 'Dynamights'
    if 'diva' in ql or 'girls' in ql:
        return 'Diamond Divas'
    return None


# ================================================================ handlers
# Each returns (answer_text, sources) or None if it can't answer.

def h_help(q, caps):
    can = []
    if 'public' in caps:
        can += ['upcoming fixtures and recent results', 'match predictions', 'how many players are registered', 'club information']
    if 'players' in caps:
        can += ['a player\'s details ("Tell me about Mia Lee")', 'who is injured']
    if 'development' in caps:
        can += ['top performers and most improved players']
    if 'documents' in caps:
        can += ['which player documents are outstanding', 'documents waiting for review', 'searching uploaded documents']
    if 'finance' in caps:
        can += ['this month\'s income and expenses', 'the latest approved transactions', 'budgets', 'the latest invoice']
    if 'predictions' in caps:
        can += ['how accurate the prediction model is']
    return "I answer from Dynasty FC's own records. You can ask me about: " + '; '.join(can) + '.', []


def h_club(q, caps):
    ql = q.lower()
    if any(w in ql for w in ('contact', 'phone', 'email', 'call', 'reach')):
        return f"You can reach Dynasty FC on {CLUB['phone']} or {CLUB['email']} (Instagram {CLUB['instagram']}).", [{'label': 'Contact page', 'link': '/contact'}]
    if any(w in ql for w in ('where', 'location', 'address', 'ground', 'train')):
        return f"Dynasty FC is based at {CLUB['home']}.", [{'label': 'About', 'link': '/about'}]
    if any(w in ql for w in ('join', 'register', 'trial', 'sign up', 'volunteer')):
        return "To join, use the registration page or get in touch through Get Involved; volunteers can apply there too.", [{'label': 'Get Involved', 'link': '/get-involved'}]
    years = date.today().year - CLUB['founded']
    return (f"{CLUB['name']} was founded in {CLUB['founded']} ({years} years ago) and plays at {CLUB['home']}. "
            f"Teams: {CLUB['teams']}."), [{'label': 'About', 'link': '/about'}]


def h_player_count(q, caps):
    tf = team_filter(q)
    with db() as (_, cur):
        rows = player_rows(cur)
    rows = [p for p in rows if not tf or p.get('team_group') == tf]
    by = {}
    for p in rows:
        k = f"{p.get('team_group')} {p.get('age_group')}"
        by[k] = by.get(k, 0) + 1
    detail = ', '.join(f'{v} in {k}' for k, v in sorted(by.items()))
    return f"There are {len(rows)} active registered players{' in ' + tf if tf else ''}: {detail}.", [{'label': 'Players', 'link': '/dashboard#players' if 'players' in caps else '/teams'}]


def h_player(q, caps):
    with db() as (_, cur):
        players = player_rows(cur)
    hits = mentioned_players(q, players)
    if not hits:
        return None
    if len(hits) > 1 and len({(p['first_name'], p['last_name']) for p in hits}) > 1:
        names = ', '.join(f"{p['first_name']} {p['last_name']}" for p in hits[:6])
        return f"I found more than one player matching that: {names}. Which one do you mean?", []
    p = hits[0]
    parts = [f"{p['first_name']} {p['last_name']} plays {p.get('position') or 'an unspecified position'} for {p.get('team_group')} {p.get('age_group')}"
             + (f", shirt #{p['jersey_number']}" if p.get('jersey_number') else '') + '.']
    parts.append('Currently injured.' if p.get('is_injured') else 'Currently fit.')
    sources = [{'label': f"Player profile: {p['first_name']} {p['last_name']}", 'link': f"/dashboard#player/{p['player_id']}"}]
    if 'development' in caps:
        try:
            s = pdi_squad(player_id=p['player_id'])
            if s and s[0]['overall'] is not None:
                s = s[0]
                st = s['stats']
                parts.append(f"Overall rating {s['overall']} ({'+' if (s['season_change'] or 0) >= 0 else ''}{s['season_change'] or 0} this season), "
                             f"{st['appearances']} appearances, {st['goals']} goals, {st['assists']} assists, attendance {st['attendance_pct'] if st['attendance_pct'] is not None else '–'}%.")
                if s.get('development_focus'):
                    parts.append(f"Development focus: {s['development_focus']}.")
        except Exception as e:
            print(f'[assistant] development lookup failed: {e}')
    if 'documents' in caps:
        try:
            from documents import outstanding_documents
            miss = [x for x in outstanding_documents() if x['player_id'] == p['player_id']]
            parts.append('Outstanding documents: ' + ', '.join(miss[0]['missing_labels']) + '.' if miss else 'All required documents are on file.')
        except Exception:
            pass
    return ' '.join(parts), sources


def h_injured(q, caps):
    with db() as (_, cur):
        rows = [p for p in player_rows(cur) if p.get('is_injured')]
    if not rows:
        return 'No players are marked as injured right now.', []
    names = '; '.join(f"{p['first_name']} {p['last_name']} ({p.get('team_group')})" for p in rows)
    return f"{len(rows)} player{'s are' if len(rows) != 1 else ' is'} marked injured: {names}.", [{'label': 'Player Development', 'link': '/dashboard#development'}]


def h_top(q, caps):
    tf = team_filter(q)
    squad = [p for p in pdi_squad(team=tf) if p['is_active']]
    rated = [p for p in squad if p['overall'] is not None]
    if not rated:
        return 'No players have been assessed yet, so I cannot rank them.', []
    ql = q.lower()
    if 'improv' in ql or 'progress' in ql:
        lst = sorted([p for p in rated if p['season_change'] is not None], key=lambda p: -p['season_change'])[:5]
        body = '; '.join(f"{p['first_name']} {p['last_name']} (+{p['season_change']}, now {p['overall']})" for p in lst)
        return f"Most improved this season{' in ' + tf if tf else ''}: {body}.", [{'label': 'Player Development', 'link': '/dashboard#development'}]
    if 'goal' in ql or 'scor' in ql:
        lst = sorted(squad, key=lambda p: -(p['stats']['goals'] or 0))[:5]
        body = '; '.join(f"{p['first_name']} {p['last_name']} ({p['stats']['goals']})" for p in lst)
        return f"Top scorers{' in ' + tf if tf else ''}: {body}.", [{'label': 'Player Development', 'link': '/dashboard#development'}]
    lst = sorted(rated, key=lambda p: -p['overall'])[:5]
    body = '; '.join(f"{p['first_name']} {p['last_name']} ({p['overall']})" for p in lst)
    return f"Highest rated players{' in ' + tf if tf else ''}: {body}.", [{'label': 'Player Development', 'link': '/dashboard#development'}]


def h_fixtures(q, caps):
    from predictor import fixture_cards, active_model
    tf = team_filter(q)
    try:
        cards = fixture_cards(limit=10, public=True) if active_model() else []
    except Exception:
        cards = []
    if not cards:
        with db() as (_, cur):
            cur.execute("SELECT * FROM team_matches WHERE is_completed = 0 OR is_completed IS NULL ORDER BY match_date")
            cards = [{'date': str(to_date(m['match_date'])), 'time': str(m.get('match_time') or '')[:5], 'home_team': m['home_team'],
                      'away_team': m['away_team'], 'venue': m.get('venue'), 'team_group': m.get('team_group'), 'dynasty': None,
                      'data_quality': None} for m in cur.fetchall() if to_date(m['match_date']) and to_date(m['match_date']) >= date.today()]
    cards = [c for c in cards if not tf or c['team_group'] == tf]
    if not cards:
        return f"There are no upcoming fixtures{' for ' + tf if tf else ''} on the system.", [{'label': 'Fixtures', 'link': '/fixtures'}]
    lines = []
    wants_pred = any(w in q.lower() for w in ('predict', 'chance', 'win', 'odds', 'probab'))
    for c in cards[:5]:
        s = f"{c['date']} {c['time']}: {c['home_team']} vs {c['away_team']} ({c['team_group']}) at {c.get('venue') or 'venue TBC'}"
        if c.get('dynasty') and (wants_pred or len(cards) <= 3):
            d = c['dynasty']
            s += f" — model estimate: Dynasty win {d['win']:.0%}, draw {d['draw']:.0%}, loss {d['loss']:.0%}"
            if c.get('data_quality') in ('insufficient', 'limited'):
                s += f" ({c['data_quality']} match history, treat with caution)"
        lines.append(s)
    return 'Upcoming fixtures: ' + '; '.join(lines) + '.', [{'label': 'Fixtures & results', 'link': '/fixtures'}]


def h_results(q, caps):
    tf = team_filter(q)
    with db() as (_, cur):
        cur.execute("SELECT * FROM team_matches WHERE is_completed = 1 ORDER BY match_date DESC")
        rows = [r for r in cur.fetchall() if not tf or r.get('team_group') == tf][:5]
    if not rows:
        return 'No results have been recorded yet.', [{'label': 'Fixtures & results', 'link': '/fixtures'}]
    lines = [f"{to_date(r['match_date'])}: {r['home_team']} {r['home_score']}–{r['away_score']} {r['away_team']} ({r.get('team_group')})" for r in rows]
    return 'Latest results: ' + '; '.join(lines) + '.', [{'label': 'Fixtures & results', 'link': '/fixtures'}]


def h_finance_summary(q, caps):
    from finance import sync_donations
    sync_donations()
    today = date.today()
    start = today.replace(day=1)
    with db() as (_, cur):
        cur.execute("SELECT txn_date, amount, status FROM finance_transactions")
        rows = cur.fetchall()
    appr = [r for r in rows if r['status'] == 'approved']
    m = [r for r in appr if to_date(r['txn_date']) >= start]
    inc = sum(float(r['amount']) for r in m if float(r['amount']) > 0)
    exp = -sum(float(r['amount']) for r in m if float(r['amount']) < 0)
    pend = sum(1 for r in rows if r['status'] == 'pending')
    with db() as (_, cur):
        cur.execute("SELECT COUNT(*) AS n FROM finance_import_rows WHERE decision = 'pending'")
        pend_rows = cur.fetchall()[0]['n']
    return (f"So far in {start.strftime('%B %Y')}: income {R(inc)}, expenses {R(exp)}, net {R(inc - exp)} (approved transactions only). "
            f"Ledger balance of all approved transactions: {R(sum(float(r['amount']) for r in appr))}. "
            f"Waiting for approval: {pend} transaction(s) and {pend_rows} imported line(s)."), [{'label': 'Finance', 'link': '/dashboard#finance'}]


def h_transactions(q, caps):
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_transactions WHERE status = 'approved' ORDER BY txn_date DESC, txn_id DESC LIMIT 5")
        rows = cur.fetchall()
    if not rows:
        return 'There are no approved transactions yet.', []
    lines = [f"{to_date(r['txn_date'])} {r['description']} {R(r['amount'])} ({r['category']})" for r in rows]
    return 'Latest approved transactions: ' + '; '.join(lines) + '.', [{'label': 'Finance ledger', 'link': '/dashboard#finance'}]


def h_budget(q, caps):
    today = date.today().replace(day=1)
    with db() as (_, cur):
        cur.execute("SELECT * FROM finance_budgets")
        budgets = cur.fetchall()
        cur.execute("SELECT category, amount, txn_date FROM finance_transactions WHERE status='approved'")
        tx = [t for t in cur.fetchall() if to_date(t['txn_date']) >= today and float(t['amount']) < 0]
    if not budgets:
        return 'No budgets have been set yet. Finance approvers can add them on the Finance page.', [{'label': 'Finance', 'link': '/dashboard#finance'}]
    lines = []
    for b in budgets:
        spent = -sum(float(t['amount']) for t in tx if t['category'] == b['category'])
        lim = float(b['monthly_limit'])
        lines.append(f"{b['category']}: {R(spent)} of {R(lim)}" + (' — OVER BUDGET' if spent > lim else ''))
    return f"Budgets for {today.strftime('%B')}: " + '; '.join(lines) + '.', [{'label': 'Finance', 'link': '/dashboard#finance'}]


def h_latest_invoice(q, caps):
    from core import from_json
    with db() as (_, cur):
        cur.execute("SELECT document_id, title, status, fields_json, created_at FROM club_documents WHERE doc_type = 'invoice' ORDER BY document_id DESC LIMIT 1")
        rows = cur.fetchall()
    if not rows:
        return 'No invoices have been uploaded yet.', [{'label': 'Documents', 'link': '/dashboard#documents'}]
    d = rows[0]
    f = from_json(d['fields_json'], {})
    return (f"The latest invoice is #{d['document_id']} “{d['title']}” from {f.get('supplier') or 'an unknown supplier'}"
            f"{', number ' + f['document_number'] if f.get('document_number') else ''}, dated {f.get('document_date') or 'unknown'}, "
            f"total {R(f['total']) if f.get('total') else 'unknown'}. Status: {d['status'].replace('_', ' ')}."), \
        [{'label': d['title'], 'link': f"/dashboard#documents/{d['document_id']}"}]


def h_outstanding(q, caps):
    from documents import outstanding_documents
    rows = outstanding_documents()
    tf = team_filter(q)
    rows = [r for r in rows if not tf or r.get('team_group') == tf]
    if not rows:
        return 'Every active player has all required documents on file.', []
    from collections import Counter
    c = Counter(m for r in rows for m in r['missing_labels'])
    sample = '; '.join(f"{r['first_name']} {r['last_name']} ({', '.join(r['missing_labels'])})" for r in rows[:8])
    return (f"{len(rows)} player(s) have outstanding documents — " + ', '.join(f'{v} missing {k.lower()}' for k, v in c.items())
            + f". For example: {sample}" + ('…' if len(rows) > 8 else '.')), [{'label': 'Outstanding documents', 'link': '/dashboard#documents/outstanding'}]


def h_doc_queue(q, caps):
    from core import DOC_ACCESS_CAP
    levels = [l for l, cap in DOC_ACCESS_CAP.items() if cap in caps]
    with db() as (_, cur):
        cur.execute("SELECT status, access_level FROM club_documents")
        rows = [r for r in cur.fetchall() if r['access_level'] in levels]
    from collections import Counter
    c = Counter(r['status'] for r in rows)
    waiting = c['needs_review'] + c['ready'] + c['needs_manual_entry']
    return (f"{waiting} document(s) are waiting for a decision ({c['ready']} ready to approve, {c['needs_review']} need review, "
            f"{c['needs_manual_entry']} need manual entry). {c['approved']} approved and {c['rejected']} rejected so far."), \
        [{'label': 'Document centre', 'link': '/dashboard#documents'}]


def h_model(q, caps):
    from predictor import active_model, track_record
    from core import from_json
    m = active_model()
    if not m:
        return 'The prediction model has not been trained yet.', []
    met = from_json(m['metrics_json'], {})
    sel = met['comparison'][met['selected']]
    base = met['comparison'].get('Baseline (always the historical average)', {})
    tr = track_record()
    s = (f"The current model ({m['algorithm']}, version {m['version']}) was trained on {met['n_used']:,} historical matches. "
         f"On {met['test_period'][0]} to {met['test_period'][1]} matches it had never seen it picked the right outcome {sel['accuracy']:.0%} of the time "
         f"(log loss {sel['log_loss']}, versus {base.get('accuracy', 0):.0%} and {base.get('log_loss')} for always guessing the average).")
    if tr['evaluated']:
        s += f" On Dynasty FC matches so far it is {tr['correct']}/{tr['evaluated']} correct."
    return s, [{'label': 'Match Intelligence', 'link': '/dashboard#ml'}]


def h_donations(q, caps):
    from finance import sync_donations
    sync_donations()
    with db() as (_, cur):
        cur.execute("SELECT amount, txn_date FROM finance_transactions WHERE source='donation' AND status='approved'")
        rows = cur.fetchall()
    start = date.today().replace(day=1)
    return (f"Paid donations recorded: {len(rows)} totalling {R(sum(float(r['amount']) for r in rows))}, of which "
            f"{R(sum(float(r['amount']) for r in rows if to_date(r['txn_date']) >= start))} this month."), [{'label': 'Finance', 'link': '/dashboard#finance'}]


def h_tasks(q, caps):
    from tasks import visible
    with db() as (_, cur):
        cur.execute("SELECT title, due_date, capability FROM follow_up_tasks WHERE status = 'open' ORDER BY due_date IS NULL, due_date")
        rows = visible(cur.fetchall())
    if not rows:
        return 'There are no open follow-up tasks for you right now.', [{'label': 'Tasks', 'link': '/dashboard#tasks'}]
    today = date.today()
    over = [r for r in rows if to_date(r['due_date']) and to_date(r['due_date']) < today]
    lines = [f"{r['title']}" + (f" (due {to_date(r['due_date'])})" if r['due_date'] else '') for r in rows[:6]]
    return (f"You have {len(rows)} open task(s){f', {len(over)} overdue' if over else ''}: " + '; '.join(lines) + ('…' if len(rows) > 6 else '.')), \
        [{'label': 'Tasks', 'link': '/dashboard#tasks'}]


def h_search_docs(q, caps):
    from documents import search_documents
    hits = search_documents(q, caps)
    if not hits:
        return None
    lines = [f"“{h['title']}” ({h['type']}): …{h['snippet']}…" for h in hits[:3]]
    return 'Here is what I found in approved documents: ' + ' | '.join(lines), \
        [{'label': h['title'], 'link': f"/dashboard#documents/{h['document_id']}"} for h in hits[:3]]


INTENTS = [
    # (name, capability, pattern, handler)
    ('help', 'public', r'^\s*(hi|hello|hey|help|what can you|who are you)\b', h_help),
    ('tasks', 'documents', r'\b(tasks?|to-?do|follow[- ]?ups?|what (do|should) (i|we) (need to )?do|overdue)\b', h_tasks),
    ('doc_search', 'documents', r'(doctor|certificate|document|letter|contract|agreement|form|statement)\b.*\b(say|said|says|mention|state|states|contain)|what (does|did) the .* (say|state)|search (the )?(documents|docs)', 'SEARCH'),
    ('model', 'public', r'(model|prediction).*(accura|reliab|good|perform|trained)|how accurate', h_model),
    ('latest_invoice', 'documents_finance', r'(latest|last|recent|newest).*invoice|invoice.*(latest|last|recent)', h_latest_invoice),
    ('outstanding_docs', 'documents', r'(outstanding|missing|still need|incomplete).*(document|paperwork|form|consent|medical|registration)|document.*(outstanding|missing)', h_outstanding),
    ('doc_queue', 'documents', r'(document|docs?).*(review|queue|pending|waiting|approve)|(waiting|pending).*document', h_doc_queue),
    ('budget', 'finance', r'budget', h_budget),
    ('donations', 'finance', r'donation|donor', h_donations),
    ('transactions', 'finance', r'(latest|recent|last).*(transaction|payment|expense)|approved transactions', h_transactions),
    ('finance_summary', 'finance', r'income|expens|spend|spent|money|balance|finance|financial|cash|profit|net\b', h_finance_summary),
    ('injured', 'players', r'injur|unfit|not fit|medical status', h_injured),
    ('top', 'development', r'(top|best|highest|most improved|improv|leading|top scorer|scorers)', h_top),
    ('results', 'public', r'result|score|last (game|match)|won|lost|played', h_results),
    ('fixtures', 'public', r'fixture|upcoming|next (game|match)|when (do|does|is|are)|schedule|predict|chance|odds|probab', h_fixtures),
    ('player_count', 'public', r'how many (players|kids|children|girls|boys)|number of players|players (are )?registered', h_player_count),
    ('club', 'public', r'founded|history|where|location|contact|phone|email|join|trial|about the club|address|volunteer', h_club),
]

PUBLIC_REFUSAL = {
    'finance': 'Finance information is only available to authorised club staff.',
    'documents_finance': 'Financial documents are only available to finance staff.',
    'documents': 'Club documents are only available to logged-in staff.',
    'players': 'Player details are only available to logged-in club staff.',
    'development': 'Player ratings are only available to coaching staff.',
    'predictions': 'Model details are only available to staff.',
}


def answer(question, caps):
    q = question.strip()
    ql = q.lower()
    denied = None
    # a named player takes priority for staff
    if 'players' in caps and re.search(r'[A-Za-z]{3,}', q):
        if not re.search(r'how many|count|injur|top|best|improv|scor', ql):
            res = h_player(q, caps)
            if res:
                return 'player', res[0], res[1]
    for name, cap, pat, fn in INTENTS:
        if re.search(pat, ql):
            if cap not in caps:
                denied = denied or cap
                continue
            res = h_search_docs(q, caps) if fn == 'SEARCH' else fn(q, caps)
            if res:
                return name, res[0], res[1]
    if any(c in caps for c in ('documents', 'documents_finance', 'documents_medical')):
        res = h_search_docs(q, caps)
        if res:
            return 'document_search', res[0], res[1]
    if 'players' not in caps and re.search(r'\b(tell me about|who is|info(rmation)? (on|about)|details (of|for|about)|how is)\b', ql):
        return 'denied', PUBLIC_REFUSAL['players'], []
    if denied:
        return 'denied', PUBLIC_REFUSAL.get(denied, 'You do not have access to that information.'), []
    text, src = h_help(q, caps)
    return 'unknown', "I couldn't find that in the club's records. " + text, src


def llm_phrase(question, facts, role):
    key = os.getenv('ANTHROPIC_API_KEY')
    if not key:
        return None
    try:
        r = requests.post(os.getenv('ANTHROPIC_API_URL', 'https://api.anthropic.com/v1/messages'), timeout=20, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}, json={
            'model': os.getenv('ANTHROPIC_MODEL', 'claude-haiku-5-5'), 'max_tokens': 400,
            'system': ("You are the Dynasty FC club assistant. Answer the user's question using ONLY the facts provided. "
                       "If the facts do not answer it, say the information is not in the club's records. Never invent names, "
                       "numbers, dates or results. Keep it short and friendly. Use rand (R) for money."),
            'messages': [{'role': 'user', 'content': f"User role: {role or 'public visitor'}\nQuestion: {question}\n\nFacts from the club database:\n{facts}"}]})
        r.raise_for_status()
        return ''.join(b.get('text', '') for b in r.json().get('content', []) if b.get('type') == 'text').strip() or None
    except Exception as e:
        print(f'[assistant] LLM phrasing skipped: {e}')
        return None


@bp.route('/api/assistant/ask', methods=['POST'])
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get('question') or '').strip()[:500]
    if not q:
        return jsonify({'error': 'Ask a question'}), 400
    caps = caps_for()
    try:
        intent, text, sources = answer(q, caps)
    except Exception as e:
        print(f'[assistant] error: {e}')
        intent, text, sources = 'error', 'Sorry, I could not look that up just now. Please try again.', []
    phrased = llm_phrase(q, text, current_role()) if intent not in ('denied', 'error', 'unknown') else None
    try:
        with db() as (_, cur):
            cur.execute("INSERT INTO assistant_log (user_id, role, question, intent, answered, sources, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (session.get('user_id'), current_role(), q, intent, 0 if intent in ('unknown', 'denied', 'error') else 1, as_json(sources), now()))
            if intent in ('finance_summary', 'transactions', 'budget', 'latest_invoice', 'donations'):
                audit('assistant_finance_query', 'assistant', intent, {'question': q}, cur=cur)
    except Exception as e:
        print(f'[assistant] log failed: {e}')
    return jsonify({'answer': phrased or text, 'grounded_answer': text, 'sources': sources, 'intent': intent,
                    'phrased_by_ai': bool(phrased), 'role': current_role() or 'public'})


@bp.route('/api/assistant/suggestions')
def suggestions():
    caps = caps_for()
    s = ['When is the next match?', 'What were the latest results?', 'How many players are registered?']
    if 'players' in caps:
        s = ['Who is injured?', 'Tell me about Mia Lee'] + s
    if 'development' in caps:
        s.insert(0, 'Who are the most improved players?')
    if 'documents' in caps:
        s.append('Which player documents are outstanding?')
        s.insert(0, 'What tasks are open?')
    if 'finance' in caps:
        s = ['How much have we spent this month?', 'Show the latest approved transactions', 'Find the latest invoice'] + s
    return jsonify(s[:7])


@bp.route('/api/assistant/log')
@require('audit')
def log():
    with db() as (_, cur):
        cur.execute("SELECT * FROM assistant_log ORDER BY log_id DESC LIMIT 100")
        return jsonify(clean_rows(cur.fetchall()))
