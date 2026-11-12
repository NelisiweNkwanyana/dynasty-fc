"""
Dynasty FC — club analytics for the dashboard. Every figure is computed from
the database at request time; sections the user may not see are left out.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify

from core import can, db, from_json, require, to_date

bp = Blueprint('analytics', __name__)


def init_analytics(app):
    app.register_blueprint(bp)


def _dt(v):
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)[:19])
    except (TypeError, ValueError):
        return None


def _months(n=12):
    m = date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    return out[::-1]


@bp.route('/api/analytics')
@require('documents')
def analytics():
    out = {}
    months = _months(12)
    keys = [m.strftime('%Y-%m') for m in months]

    # --- results by month, per squad (Dynasty perspective)
    with db() as (_, cur):
        cur.execute("SELECT match_date, home_team, away_team, home_score, away_score, team_group FROM team_matches WHERE is_completed = 1")
        matches = cur.fetchall()
    res = defaultdict(lambda: {k: {'W': 0, 'D': 0, 'L': 0, 'gf': 0, 'ga': 0} for k in keys})
    totals = defaultdict(lambda: {'W': 0, 'D': 0, 'L': 0, 'gf': 0, 'ga': 0, 'played': 0})
    for m in matches:
        d = to_date(m['match_date'])
        if not d or m['home_score'] is None:
            continue
        hs, as_ = int(m['home_score']), int(m['away_score'])
        home_is_club = 'dynasty' in str(m['home_team']).lower()
        gf, ga = (hs, as_) if home_is_club else (as_, hs)
        r = 'W' if gf > ga else ('D' if gf == ga else 'L')
        sq = m['team_group'] or 'Club'
        t = totals[sq]
        t[r] += 1; t['gf'] += gf; t['ga'] += ga; t['played'] += 1
        k = d.strftime('%Y-%m')
        if k in res[sq]:
            res[sq][k][r] += 1; res[sq][k]['gf'] += gf; res[sq][k]['ga'] += ga
    out['results'] = {'months': [m.strftime('%b') for m in months], 'keys': keys,
                      'squads': {sq: [v[k] for k in keys] for sq, v in res.items()}, 'totals': totals}

    # --- player development: squad rating trend and attendance
    if can('development'):
        try:
            from assistant import pdi_squad
            squad = [p for p in pdi_squad() if p['is_active']]
            by_date = defaultdict(lambda: defaultdict(list))
            att = defaultdict(list)
            for p in squad:
                for a in p['_history']:
                    ovr = round(sum(a[k] for k in ('pace', 'technique', 'passing', 'defending', 'physical', 'mentality')) / 6)
                    by_date[p['team_group']][str(to_date(a['assessed_on']))].append(ovr)
                if p['stats']['attendance_pct'] is not None:
                    att[p['team_group']].append(p['stats']['attendance_pct'])
            out['development'] = {
                'rating_trend': {sq: [{'date': d, 'avg': round(sum(v) / len(v), 1), 'n': len(v)} for d, v in sorted(ds.items())] for sq, ds in by_date.items()},
                'attendance': {sq: round(sum(v) / len(v)) for sq, v in att.items() if v},
                'rated_players': sum(1 for p in squad if p['overall'] is not None), 'players': len(squad)}
        except Exception as e:
            out['development'] = {'error': str(e)}

    # --- documents: throughput and turnaround
    from core import DOC_ACCESS_CAP
    from core import caps_for
    levels = [l for l, c in DOC_ACCESS_CAP.items() if c in caps_for()]
    with db() as (_, cur):
        cur.execute("SELECT created_at, reviewed_at, status, doc_type, access_level, type_confidence FROM club_documents")
        docs = [d for d in cur.fetchall() if d['access_level'] in levels]
    weeks = []
    start = date.today() - timedelta(days=date.today().weekday()) - timedelta(weeks=7)
    for i in range(8):
        ws = start + timedelta(weeks=i)
        we = ws + timedelta(days=7)
        inw = [d for d in docs if _dt(d['created_at']) and ws <= _dt(d['created_at']).date() < we]
        weeks.append({'week': ws.strftime('%d %b'), 'uploaded': len(inw), 'approved': sum(1 for d in inw if d['status'] == 'approved')})
    hours = [(_dt(d['reviewed_at']) - _dt(d['created_at'])).total_seconds() / 3600 for d in docs
             if d['reviewed_at'] and _dt(d['reviewed_at']) and _dt(d['created_at'])]
    by_type = defaultdict(int)
    for d in docs:
        by_type[d['doc_type']] += 1
    out['documents'] = {'weeks': weeks, 'total': len(docs), 'by_type': by_type,
                        'avg_hours_to_decision': round(sum(hours) / len(hours), 1) if hours else None,
                        'auto_ready_share': round(sum(1 for d in docs if float(d['type_confidence'] or 0) >= 0.55) / len(docs), 2) if docs else None}

    # --- finance trend
    if can('finance'):
        with db() as (_, cur):
            cur.execute("SELECT txn_date, amount FROM finance_transactions WHERE status = 'approved'")
            tx = cur.fetchall()
        fin = {k: {'income': 0.0, 'expenses': 0.0} for k in keys}
        for t in tx:
            d = to_date(t['txn_date'])
            k = d.strftime('%Y-%m') if d else None
            if k in fin:
                a = float(t['amount'])
                fin[k]['income' if a > 0 else 'expenses'] += abs(a)
        out['finance'] = [{'month': months[i].strftime('%b'), **{x: round(v, 2) for x, v in fin[k].items()}} for i, k in enumerate(keys)]

    # --- prediction model track record
    if can('predictions'):
        with db() as (_, cur):
            cur.execute("SELECT is_correct, log_loss, evaluated_at FROM ml_predictions WHERE actual_outcome IS NOT NULL ORDER BY evaluated_at")
            ev = cur.fetchall()
        run, correct = [], 0
        for i, e in enumerate(ev, start=1):
            correct += int(e['is_correct'] or 0)
            run.append(round(correct / i, 3))
        out['model'] = {'graded': len(ev), 'running_accuracy': run}

    # --- assistant usage
    if can('audit'):
        with db() as (_, cur):
            cur.execute("SELECT answered, intent, created_at FROM assistant_log")
            logs = cur.fetchall()
        intents = defaultdict(int)
        for l in logs:
            intents[l['intent']] += 1
        out['assistant'] = {'questions': len(logs), 'answered_share': round(sum(int(l['answered'] or 0) for l in logs) / len(logs), 2) if logs else None,
                            'top_topics': sorted(intents.items(), key=lambda x: -x[1])[:6]}
    return jsonify(out)
