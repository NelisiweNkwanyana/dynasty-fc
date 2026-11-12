"""
Dynasty FC — automation rules and the live dashboard overview.

Event rules (fired by the modules, run after their database changes commit):
  document.approved          -> routed by documents.route_document (finance / players)
  finance.transaction.approved -> accounting sync + budget check   (finance.py)
  match.created              -> prediction for the new fixture      (predictor.py)
  match.result_recorded      -> grade prediction, refresh form + notify (predictor.py + here)
  player.registered          -> coach reminder for a baseline assessment (here)

Scheduled-style checks run whenever staff open the dashboard (no cron needed):
  medical certificates expiring, contracts ending, past fixtures without a result.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify

from core import audit, can, caps_for, clean_rows, db, from_json, notify, now, on, require, to_date

bp = Blueprint('automation', __name__)

RULES = [
    {'when': 'A document is approved', 'then': 'Invoices/receipts become ledger expenses; bank statements become a finance import; registrations create or update players; medical certificates update fitness; consent forms link to the player', 'audit_action': 'document_approved'},
    {'when': 'A finance transaction is approved', 'then': 'It is posted to ERPNext and checked against its category budget', 'audit_action': 'finance_txn_created'},
    {'when': 'A paid donation is recorded', 'then': 'It appears in the finance ledger as income (once)', 'audit_action': 'finance_txn_created'},
    {'when': 'A fixture is created', 'then': 'The model predicts it immediately', 'audit_action': 'ml_model_trained'},
    {'when': 'A result is recorded', 'then': 'The prediction is graded, team form updates and other fixtures are re-predicted', 'audit_action': 'ml_prediction_evaluated'},
    {'when': 'A new player is registered', 'then': 'Coaches are reminded to do a baseline assessment', 'audit_action': 'document_approved'},
    {'when': 'Daily checks (on dashboard load)', 'then': 'Expiring medical certificates, ending contracts and fixtures missing a result raise alerts', 'audit_action': None},
]


def init_automation(app):
    app.register_blueprint(bp)


@on('player.registered')
def _new_player(player_id, document_id=None):
    with db() as (_, cur):
        cur.execute("SELECT first_name, last_name, team_group FROM players WHERE player_id = %s", (player_id,))
        p = cur.fetchall()
        if p:
            p = p[0]
            notify('player', f"New player: {p['first_name']} {p['last_name']} ({p['team_group']})",
                   'Record a baseline assessment so their development can be tracked.', f'/dashboard#player/{player_id}',
                   capability='development', cur=cur)
    return {'module': 'automation', 'reminder': 'baseline assessment'}


@on('match.result_recorded')
def _result_notice(match_id):
    with db() as (_, cur):
        cur.execute("SELECT home_team, away_team, home_score, away_score, team_group FROM team_matches WHERE match_id = %s", (match_id,))
        m = cur.fetchall()
        if m:
            m = m[0]
            notify('match', f"Result: {m['home_team']} {m['home_score']}–{m['away_score']} {m['away_team']}",
                   f"{m['team_group']} · public results page and predictions updated", '/dashboard#ml', capability='fixtures', cur=cur)
    return {'module': 'automation', 'notified': True}


def _notified_recently(cur, title, days):
    cur.execute("SELECT created_at FROM notifications WHERE title = %s ORDER BY notification_id DESC LIMIT 1", (title,))
    r = cur.fetchall()
    if not r:
        return False
    d = r[0]['created_at']
    if isinstance(d, str):
        from datetime import datetime
        try:
            d = datetime.fromisoformat(d[:19])
        except ValueError:
            return False
    return bool(d) and (now() - d).days < days


def run_checks():
    today = date.today()
    raised = 0
    try:
        from tasks import missing_result_tasks
        missing_result_tasks()
    except Exception as e:
        print(f'[automation] task check skipped: {e}')
    with db() as (_, cur):
        # medical certificates expiring within 14 days
        try:
            cur.execute("""SELECT pd.valid_until, p.first_name, p.last_name, p.player_id FROM player_documents pd
                           JOIN players p ON p.player_id = pd.player_id WHERE pd.doc_type = 'medical_certificate'""")
            for r in cur.fetchall():
                vu = to_date(r['valid_until'])
                if vu and today <= vu <= today + timedelta(days=14):
                    t = f"Medical certificate expiring: {r['first_name']} {r['last_name']} ({vu.isoformat()})"
                    if not _notified_recently(cur, t, 7):
                        notify('player', t, 'Ask the family for an updated certificate.', f"/dashboard#player/{r['player_id']}", capability='documents_medical', cur=cur)
                        raised += 1
        except Exception as e:
            print(f'[automation] medical check skipped: {e}')
        # past fixtures with no result
        try:
            cur.execute("SELECT match_id, match_date, home_team, away_team FROM team_matches WHERE is_completed = 0 OR is_completed IS NULL")
            for r in cur.fetchall():
                d = to_date(r['match_date'])
                if d and d < today:
                    t = f"Result missing: {r['home_team']} vs {r['away_team']} ({d.isoformat()})"
                    if not _notified_recently(cur, t, 3):
                        notify('match', t, 'Record the score so predictions and team form stay accurate.', '/dashboard#matches', capability='fixtures', cur=cur)
                        raised += 1
        except Exception as e:
            print(f'[automation] fixture check skipped: {e}')
    return raised


@bp.route('/api/public/squad-stats')
def squad_stats():
    """Per-squad record, last five results, goals and next fixture – from team_matches only (no player data)."""
    with db() as (_, cur):
        cur.execute("SELECT match_id, team_group, age_group, home_team, away_team, home_score, away_score, match_date, match_time, venue, is_completed FROM team_matches")
        rows = cur.fetchall()
    out = {}
    today = date.today()
    for m in sorted(rows, key=lambda r: (to_date(r['match_date']) or date.min, r['match_id'])):
        sq = m['team_group'] or 'Dynasty FC'
        s = out.setdefault(sq, {'played': 0, 'W': 0, 'D': 0, 'L': 0, 'gf': 0, 'ga': 0, 'form': [], 'next': None, 'clean_sheets': 0})
        d = to_date(m['match_date'])
        club_home = 'dynasty' in str(m['home_team']).lower() or not ('dynasty' in str(m['away_team']).lower())
        if m['is_completed'] and m['home_score'] is not None:
            gf, ga = (int(m['home_score']), int(m['away_score'])) if club_home else (int(m['away_score']), int(m['home_score']))
            r = 'W' if gf > ga else 'D' if gf == ga else 'L'
            s['played'] += 1; s[r] += 1; s['gf'] += gf; s['ga'] += ga; s['clean_sheets'] += int(ga == 0)
            s['form'] = (s['form'] + [r])[-5:]
        elif d and d >= today and s['next'] is None:
            s['next'] = {'opponent': m['away_team'] if club_home else m['home_team'], 'home': club_home, 'date': d.isoformat(),
                         'time': str(m['match_time'] or '')[:5] or None, 'venue': m['venue'], 'age_group': m['age_group']}
    for s in out.values():
        s['win_rate'] = round(s['W'] / s['played'], 3) if s['played'] else None
    return jsonify(out)


@bp.route('/api/public/stats')
def public_stats():
    """Live numbers for the public home page (no personal data)."""
    with db() as (_, cur):
        cur.execute("SELECT team_group, age_group FROM players WHERE is_active = 1")
        ps = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM team_matches WHERE is_completed = 1")
        played = cur.fetchall()[0]['n']
    teams = {(p['team_group'], p['age_group']) for p in ps if p['team_group']}
    founded = 2016
    return jsonify({'founded': founded, 'players': len(ps), 'teams': len(teams), 'years_running': date.today().year - founded,
                    'matches_played': played})


@bp.route('/api/automation/rules')
@require('documents')
def rules():
    with db() as (_, cur):
        cur.execute("SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action")
        counts = {r['action']: r['n'] for r in cur.fetchall()}
    return jsonify([{**r, 'times': counts.get(r['audit_action'], 0) if r['audit_action'] else None} for r in RULES])


@bp.route('/api/overview')
@require('documents')
def overview():
    """Live numbers for the dashboard home, only for modules this user may see."""
    run_checks()
    caps = caps_for()
    out = {'generated_at': now().isoformat()}
    with db() as (_, cur):
        cur.execute("SELECT * FROM players WHERE is_active = 1")
        ps = cur.fetchall()
        out['players'] = {'total': len(ps), 'injured': sum(1 for p in ps if p.get('is_injured')),
                          'by_team': {t: sum(1 for p in ps if p['team_group'] == t) for t in sorted({p['team_group'] for p in ps if p['team_group']})}}
        cur.execute("SELECT is_completed, match_date, home_score, away_score, home_team FROM team_matches")
        ms = cur.fetchall()
        done = [m for m in ms if m['is_completed']]
        def dyn_res(m):
            hs, as_ = int(m['home_score'] or 0), int(m['away_score'] or 0)
            mine, theirs = (hs, as_) if 'dynasty' in str(m['home_team']).lower() else (as_, hs)
            return 'W' if mine > theirs else ('D' if mine == theirs else 'L')
        recent = sorted(done, key=lambda m: str(m['match_date']))[-5:]
        out['matches'] = {'played': len(done), 'upcoming': sum(1 for m in ms if not m['is_completed'] and to_date(m['match_date']) and to_date(m['match_date']) >= date.today()),
                          'form': [dyn_res(m) for m in recent]}
    if can('documents'):
        from core import DOC_ACCESS_CAP
        levels = [l for l, c in DOC_ACCESS_CAP.items() if c in caps]
        with db() as (_, cur):
            cur.execute("SELECT status, access_level FROM club_documents")
            docs = [d for d in cur.fetchall() if d['access_level'] in levels]
        from documents import outstanding_documents
        out['documents'] = {'waiting': sum(1 for d in docs if d['status'] in ('ready', 'needs_review', 'needs_manual_entry')),
                            'ready': sum(1 for d in docs if d['status'] == 'ready'),
                            'approved': sum(1 for d in docs if d['status'] == 'approved'),
                            'players_missing_docs': len(outstanding_documents())}
    if can('finance'):
        from finance import sync_donations
        sync_donations()
        start = date.today().replace(day=1)
        with db() as (_, cur):
            cur.execute("SELECT txn_date, amount, status, sync_status FROM finance_transactions")
            tx = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM finance_import_rows WHERE decision='pending'")
            pr = cur.fetchall()[0]['n']
        appr = [t for t in tx if t['status'] == 'approved']
        mon = [t for t in appr if to_date(t['txn_date']) >= start]
        out['finance'] = {'month_income': round(sum(float(t['amount']) for t in mon if float(t['amount']) > 0), 2),
                          'month_expenses': round(-sum(float(t['amount']) for t in mon if float(t['amount']) < 0), 2),
                          'balance': round(sum(float(t['amount']) for t in appr), 2),
                          'pending': sum(1 for t in tx if t['status'] == 'pending') + pr,
                          'sync_failed': sum(1 for t in appr if t['sync_status'] == 'failed')}
    if can('predictions'):
        try:
            from predictor import active_model, fixture_cards, track_record
            m = active_model()
            if m:
                met = from_json(m['metrics_json'], {})
                cards = fixture_cards(limit=1)
                out['predictions'] = {'model': m['algorithm'], 'version': m['version'],
                                      'test_accuracy': met['comparison'][met['selected']]['accuracy'],
                                      'track_record': {k: v for k, v in track_record().items() if k != 'recent'},
                                      'next': cards[0] if cards else None}
        except Exception as e:
            print(f'[automation] predictions overview skipped: {e}')
    try:
        from tasks import open_task_summary
        out['tasks'] = open_task_summary()
    except Exception:
        out['tasks'] = None
    with db() as (_, cur):
        cur.execute("SELECT capability FROM notifications WHERE is_dismissed = 0")
        out['notifications'] = sum(1 for n in cur.fetchall() if (n['capability'] or 'documents') in caps)
    return jsonify(out)
