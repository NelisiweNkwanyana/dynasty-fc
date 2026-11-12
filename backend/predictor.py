"""
Dynasty FC — match prediction model.

Training data
  * 90k+ real historical results (openfootball, public domain / CC0) shipped in
    backend/data/historical_matches.csv.gz
  * the club's own completed fixtures (team_matches)
  * any local-league history staff import as CSV (ml_history)

Features (computed strictly from matches *before* each game — no leakage)
  Elo difference incl. home advantage, points-per-game over the last 5,
  goals for/against over the last 10, home form at home, away form away.

Models
  multinomial logistic regression vs. histogram gradient boosting, compared on
  a chronological hold-out (the most recent 15% of matches) by log loss, with
  accuracy, Brier score, a class-frequency baseline and a calibration table.
  The better model is refitted on all data and stored in the database.

Predictions for Dynasty fixtures use the club's own results to compute the
features; when a team has little history the prediction says so.
"""
import gzip
import io
import os
import pickle
import threading
import math
from collections import defaultdict, deque, Counter
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from core import (as_json, audit, can, clean_row, clean_rows, db, emit, from_json, notify, now, on, require,
                  run_schema, to_date)

bp = Blueprint('predictor', __name__)
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, 'data', 'historical_matches.csv.gz')

FEATURES = ['elo_diff', 'ppg5_diff', 'gf10_diff', 'ga10_diff', 'home_ppg_home5', 'away_ppg_away5', 'gd10_home', 'gd10_away']
FEATURE_LABELS = {
    'elo_diff': 'Rating difference (incl. home advantage)', 'ppg5_diff': 'Points per game, last 5 (difference)',
    'gf10_diff': 'Goals scored per game, last 10 (difference)', 'ga10_diff': 'Goals conceded per game, last 10 (difference)',
    'home_ppg_home5': 'Home team form at home', 'away_ppg_away5': 'Away team form away',
    'gd10_home': 'Home goal difference per game', 'gd10_away': 'Away goal difference per game'}
CLASSES = ['H', 'D', 'A']
ELO_K, ELO_HOME, ELO_START = 20.0, 60.0, 1500.0
NEUTRAL = 1.35
MIN_PRIOR = 3

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ml_models (
        model_id INT AUTO_INCREMENT PRIMARY KEY,
        version VARCHAR(60),
        algorithm VARCHAR(60),
        trained_at DATETIME,
        n_train INT,
        n_test INT,
        metrics_json LONGTEXT,
        model_blob LONGBLOB,
        is_active TINYINT DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS ml_predictions (
        pred_id INT AUTO_INCREMENT PRIMARY KEY,
        match_id INT NOT NULL,
        model_version VARCHAR(60),
        p_home DECIMAL(6,4),
        p_draw DECIMAL(6,4),
        p_away DECIMAL(6,4),
        predicted_outcome VARCHAR(2),
        data_quality VARCHAR(20),
        features_json TEXT,
        actual_outcome VARCHAR(2),
        is_correct TINYINT,
        log_loss DECIMAL(8,4),
        created_at DATETIME,
        evaluated_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS ml_history (
        hist_id INT AUTO_INCREMENT PRIMARY KEY,
        match_date DATE,
        competition VARCHAR(80),
        home_team VARCHAR(120),
        away_team VARCHAR(120),
        home_goals INT,
        away_goals INT,
        team_group VARCHAR(60),
        age_group VARCHAR(20),
        created_at DATETIME
    )""",
]
INDEXES = ["CREATE INDEX idx_mlpred_match ON ml_predictions (match_id)"]

_lock = threading.Lock()
_cache = {'model': None, 'version': None}


def init_predictor(app):
    run_schema(SCHEMA, INDEXES)
    app.register_blueprint(bp)
    if os.getenv('ML_AUTOTRAIN', 'true').lower() == 'true':
        threading.Thread(target=_autotrain, daemon=True).start()


def _autotrain():
    try:
        if active_model() is None:
            print('[ML] no trained model yet – training in the background…')
            train()
        refresh_predictions()
    except Exception as e:
        print(f'[ML] background training skipped: {e}')


# ================================================================ data
def is_club(name):
    return 'dynasty' in str(name or '').lower()


def squad_key(name, team_group, age_group):
    base = 'Dynasty FC' if is_club(name) else str(name or '').strip()
    tag = ' '.join(x for x in (team_group, age_group) if x)
    return f'{base} [{tag}]' if tag else base


def prepare_public_history():
    """Load and clean the historical results with pandas. Returns (rows, report)."""
    import pandas as pd
    df = pd.read_csv(DATA_FILE, compression='gzip')
    raw = len(df)
    df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce')
    for c in ('home_goals', 'away_goals'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['home_team'] = df['home_team'].astype(str).str.strip()
    df['away_team'] = df['away_team'].astype(str).str.strip()
    df = df.dropna(subset=['match_date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
    df = df[(df['home_goals'] >= 0) & (df['away_goals'] >= 0) & (df['home_goals'] <= 20) & (df['away_goals'] <= 20)]
    df = df[df['home_team'] != df['away_team']]
    df = df[df['match_date'] <= pd.Timestamp.today()]
    df = df.drop_duplicates(subset=['match_date', 'home_team', 'away_team'])
    df = df.sort_values(['match_date', 'competition', 'home_team'])
    report = {'raw_rows': int(raw), 'clean_rows': int(len(df)), 'dropped_rows': int(raw - len(df)),
              'competitions': int(df['competition'].nunique()), 'teams': int(pd.concat([df['home_team'], df['away_team']]).nunique()),
              'from': df['match_date'].min().date().isoformat(), 'to': df['match_date'].max().date().isoformat(),
              'home_win_rate': round(float((df['home_goals'] > df['away_goals']).mean()), 3),
              'draw_rate': round(float((df['home_goals'] == df['away_goals']).mean()), 3),
              'avg_goals': round(float((df['home_goals'] + df['away_goals']).mean()), 2)}
    rows = list(zip(df['match_date'].dt.date, df['competition'], df['home_team'], df['away_team'],
                    df['home_goals'].astype(int), df['away_goals'].astype(int), ['public'] * len(df)))
    return rows, report


_data_report = {}


def load_matches(include_public=True):
    """All completed matches as (date, comp, home, away, hg, ag, origin) sorted by date."""
    out = []
    if include_public and os.path.exists(DATA_FILE):
        rows, report = prepare_public_history()
        _data_report.clear()
        _data_report.update(report)
        out.extend(rows)
    out.extend(local_matches())
    out.sort(key=lambda m: (m[0], m[1], m[2]))
    return out


def local_matches():
    out = []
    with db() as (_, cur):
        try:
            cur.execute("SELECT match_id, match_date, home_team, away_team, home_score, away_score, team_group, age_group "
                        "FROM team_matches WHERE is_completed = 1 AND home_score IS NOT NULL AND away_score IS NOT NULL")
            for r in cur.fetchall():
                d = to_date(r['match_date'])
                if d:
                    out.append((d, f"club:{r['team_group']}:{r['age_group']}", squad_key(r['home_team'], r['team_group'], r['age_group']),
                                squad_key(r['away_team'], r['team_group'], r['age_group']), int(r['home_score']), int(r['away_score']), 'club'))
        except Exception as e:
            print(f'[ML] club results unavailable: {e}')
        cur.execute("SELECT * FROM ml_history")
        for r in cur.fetchall():
            d = to_date(r['match_date'])
            if d:
                out.append((d, r['competition'] or 'local', squad_key(r['home_team'], r['team_group'], r['age_group']),
                            squad_key(r['away_team'], r['team_group'], r['age_group']), int(r['home_goals']), int(r['away_goals']), 'imported'))
    return out


# ============================================================ features
class TeamState:
    __slots__ = ('elo', 'res', 'gf', 'ga', 'home_res', 'away_res', 'n', 'log')

    def __init__(self):
        self.elo = ELO_START
        self.res = deque(maxlen=5)
        self.gf = deque(maxlen=10)
        self.ga = deque(maxlen=10)
        self.home_res = deque(maxlen=5)
        self.away_res = deque(maxlen=5)
        self.n = 0
        self.log = deque(maxlen=10)  # recent matches for display


def _avg(d, default=NEUTRAL):
    return sum(d) / len(d) if d else default


def features_for(h, a):
    gf_h, ga_h, gf_a, ga_a = _avg(h.gf), _avg(h.ga), _avg(a.gf), _avg(a.ga)
    return {
        'elo_diff': (h.elo + ELO_HOME - a.elo) / 100.0,
        'ppg5_diff': _avg(h.res) - _avg(a.res),
        'gf10_diff': gf_h - gf_a,
        'ga10_diff': ga_h - ga_a,
        'home_ppg_home5': _avg(h.home_res),
        'away_ppg_away5': _avg(a.away_res),
        'gd10_home': gf_h - ga_h,
        'gd10_away': gf_a - ga_a,
    }


def update(states, d, home, away, hg, ag, comp=None):
    h, a = states[home], states[away]
    exp_h = 1 / (1 + 10 ** ((a.elo - (h.elo + ELO_HOME)) / 400))
    s_h = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
    margin = math.log(abs(hg - ag) + 1) + 1 if hg != ag else 1
    delta = ELO_K * margin * (s_h - exp_h)
    h.elo += delta
    a.elo -= delta
    ph, pa = (3, 0) if hg > ag else ((1, 1) if hg == ag else (0, 3))
    h.res.append(ph); a.res.append(pa)
    h.home_res.append(ph); a.away_res.append(pa)
    h.gf.append(hg); h.ga.append(ag); a.gf.append(ag); a.ga.append(hg)
    h.n += 1; a.n += 1
    h.log.append({'date': d.isoformat(), 'opponent': away, 'venue': 'H', 'gf': hg, 'ga': ag, 'result': 'W' if hg > ag else ('D' if hg == ag else 'L')})
    a.log.append({'date': d.isoformat(), 'opponent': home, 'venue': 'A', 'gf': ag, 'ga': hg, 'result': 'W' if ag > hg else ('D' if hg == ag else 'L')})


def build_dataset(matches):
    states = defaultdict(TeamState)
    X, y, dates = [], [], []
    for d, comp, home, away, hg, ag, origin in matches:
        h, a = states[home], states[away]
        if h.n >= MIN_PRIOR and a.n >= MIN_PRIOR:
            f = features_for(h, a)
            X.append([f[k] for k in FEATURES])
            y.append('H' if hg > ag else ('D' if hg == ag else 'A'))
            dates.append(d)
        update(states, d, home, away, hg, ag, comp)
    return X, y, dates, states


# ============================================================ training
def _metrics(y_true, proba, classes):
    import numpy as np
    from sklearn.metrics import accuracy_score
    idx = {c: i for i, c in enumerate(classes)}
    P = np.asarray(proba)
    Y = np.zeros_like(P)
    for i, t in enumerate(y_true):
        Y[i, idx[t]] = 1
    pred = [classes[i] for i in P.argmax(1)]
    brier = float(((P - Y) ** 2).sum(1).mean())
    # calibration: confidence of the predicted class vs how often it was right
    conf = P.max(1)
    bins = []
    for lo in (0.3, 0.4, 0.5, 0.6, 0.7):
        hi = lo + 0.1 if lo < 0.7 else 1.01
        m = (conf >= lo) & (conf < hi)
        if m.sum() >= 30:
            bins.append({'range': f'{int(lo*100)}–{int(min(hi,1)*100)}%', 'matches': int(m.sum()),
                         'avg_confidence': round(float(conf[m].mean()), 3),
                         'actual_hit_rate': round(float((np.array(pred)[m] == np.array(y_true)[m]).mean()), 3)})
    ll = float(-np.log(np.clip((P * Y).sum(1), 1e-12, 1)).mean())  # columns follow `classes`
    return {'log_loss': round(ll, 4),
            'accuracy': round(float(accuracy_score(y_true, pred)), 4), 'brier': round(brier, 4), 'calibration': bins}


def train(triggered_by='system'):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    with _lock:
        t0 = datetime.now()
        matches = load_matches()
        X, y, dates, _ = build_dataset(matches)
        if len(X) < 500:
            raise RuntimeError(f'Not enough historical matches to train ({len(X)})')
        X, y = np.array(X, dtype=float), np.array(y)
        cut = int(len(X) * 0.85)
        Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
        prior = Counter(ytr)
        base_p = np.array([[prior[c] / len(ytr) for c in CLASSES]] * len(yte))
        candidates = {
            'Logistic regression': make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=1.0)),
            'Gradient boosting': HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=4,
                                                                min_samples_leaf=80, l2_regularization=1.0, random_state=7),
        }
        results = {'Baseline (always the historical average)': _metrics(yte, base_p, CLASSES)}
        fitted = {}
        for name, mdl in candidates.items():
            mdl.fit(Xtr, ytr)
            order = [list(mdl.classes_).index(c) for c in CLASSES]
            results[name] = _metrics(yte, mdl.predict_proba(Xte)[:, order], CLASSES)
            fitted[name] = mdl
        best = min(candidates, key=lambda n: results[n]['log_loss'])
        final = candidates[best]
        final.fit(X, y)  # refit on everything for production
        coef = None
        if best == 'Logistic regression':
            lr = final.steps[-1][1]
            coef = {CLASSES[i] if lr.classes_[i] in CLASSES else lr.classes_[i]: dict(zip(FEATURES, map(lambda v: round(float(v), 3), lr.coef_[i])))
                    for i in range(len(lr.classes_))}
        club_n = sum(1 for m in matches if m[6] == 'club')
        imported_n = sum(1 for m in matches if m[6] == 'imported')
        version = datetime.now().strftime('%Y%m%d-%H%M%S')
        metrics = {
            'selected': best, 'comparison': results, 'test_period': [dates[cut].isoformat(), dates[-1].isoformat()],
            'train_period': [dates[0].isoformat(), dates[cut - 1].isoformat()], 'n_matches_total': len(matches),
            'n_used': len(X), 'n_club': club_n, 'n_imported': imported_n,
            'outcome_rates_test': {c: round(float((yte == c).mean()), 3) for c in CLASSES},
            'features': FEATURES, 'feature_labels': FEATURE_LABELS, 'coefficients': coef,
            'seconds': round((datetime.now() - t0).total_seconds(), 1), 'triggered_by': triggered_by,
            'data_source': 'openfootball (CC0, public domain) + Dynasty FC results + imported local history',
            'data_preparation': dict(_data_report),
        }
        blob = pickle.dumps({'model': final, 'classes': list(final.classes_), 'features': FEATURES})
        with db() as (_, cur):
            cur.execute("UPDATE ml_models SET is_active = 0")
            cur.execute("INSERT INTO ml_models (version, algorithm, trained_at, n_train, n_test, metrics_json, model_blob, is_active) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,1)", (version, best, now(), int(cut), int(len(X) - cut), as_json(metrics), blob))
            audit('ml_model_trained', 'ml_model', version, {'algorithm': best, 'log_loss': results[best]['log_loss'],
                                                             'accuracy': results[best]['accuracy']}, cur=cur)
        _cache['model'], _cache['version'] = None, None
        print(f"[ML] trained {best} on {len(X)} matches in {metrics['seconds']}s – test log loss {results[best]['log_loss']}, accuracy {results[best]['accuracy']}")
        return version, metrics


def active_model():
    with db() as (_, cur):
        cur.execute("SELECT model_id, version, algorithm, trained_at, n_train, n_test, metrics_json FROM ml_models WHERE is_active = 1 ORDER BY model_id DESC LIMIT 1")
        rows = cur.fetchall()
    return rows[0] if rows else None


def loaded_model():
    m = active_model()
    if not m:
        return None, None
    if _cache['version'] != m['version']:
        with db() as (_, cur):
            cur.execute("SELECT model_blob FROM ml_models WHERE model_id = %s", (m['model_id'],))
            blob = cur.fetchall()[0]['model_blob']
        _cache['model'] = pickle.loads(bytes(blob))
        _cache['version'] = m['version']
    return _cache['model'], m['version']


# ============================================================ prediction
def local_states():
    states = defaultdict(TeamState)
    for d, comp, home, away, hg, ag, origin in sorted(local_matches(), key=lambda m: m[0]):
        update(states, d, home, away, hg, ag, comp)
    return states


def quality(h, a):
    n = min(h.n, a.n)
    if n < MIN_PRIOR:
        return 'insufficient'
    if n < 8:
        return 'limited'
    return 'good'


def predict_probs(bundle, f):
    import numpy as np
    model, classes = bundle['model'], bundle['classes']
    p = model.predict_proba(np.array([[f[k] for k in FEATURES]], dtype=float))[0]
    return {c: float(p[classes.index(c)]) for c in CLASSES}


def upcoming_fixtures(cur):
    cur.execute("SELECT * FROM team_matches WHERE is_completed = 0 OR is_completed IS NULL ORDER BY match_date ASC")
    out = []
    for r in cur.fetchall():
        d = to_date(r['match_date'])
        if d and d >= date.today() - timedelta(days=1):
            out.append(r)
    return out


def refresh_predictions():
    bundle, version = loaded_model()
    if not bundle:
        return 0
    states = local_states()
    n = 0
    with db() as (_, cur):
        for m in upcoming_fixtures(cur):
            hk, ak = squad_key(m['home_team'], m['team_group'], m['age_group']), squad_key(m['away_team'], m['team_group'], m['age_group'])
            h, a = states[hk], states[ak]
            f = features_for(h, a)
            p = predict_probs(bundle, f)
            q = quality(h, a)
            best = max(p, key=p.get)
            cur.execute("DELETE FROM ml_predictions WHERE match_id = %s AND actual_outcome IS NULL", (m['match_id'],))
            cur.execute("""INSERT INTO ml_predictions (match_id, model_version, p_home, p_draw, p_away, predicted_outcome, data_quality,
                           features_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (m['match_id'], version, round(p['H'], 4), round(p['D'], 4), round(p['A'], 4), best, q,
                         as_json({k: round(v, 3) for k, v in f.items()}), now()))
            n += 1
    return n


def evaluate_match(match_id):
    with db() as (_, cur):
        cur.execute("SELECT home_score, away_score FROM team_matches WHERE match_id = %s", (match_id,))
        r = cur.fetchall()
        if not r or r[0]['home_score'] is None:
            return None
        hg, ag = int(r[0]['home_score']), int(r[0]['away_score'])
        actual = 'H' if hg > ag else ('D' if hg == ag else 'A')
        cur.execute("SELECT * FROM ml_predictions WHERE match_id = %s ORDER BY pred_id DESC LIMIT 1", (match_id,))
        p = cur.fetchall()
        if not p:
            return None
        p = p[0]
        prob = float({'H': p['p_home'], 'D': p['p_draw'], 'A': p['p_away']}[actual])
        ll = -math.log(max(prob, 1e-6))
        correct = 1 if p['predicted_outcome'] == actual else 0
        cur.execute("UPDATE ml_predictions SET actual_outcome=%s, is_correct=%s, log_loss=%s, evaluated_at=%s WHERE pred_id=%s",
                    (actual, correct, round(ll, 4), now(), p['pred_id']))
        audit('ml_prediction_evaluated', 'match', match_id, {'predicted': p['predicted_outcome'], 'actual': actual,
                                                              'probability_of_actual': round(prob, 3)}, cur=cur)
    return {'predicted': p['predicted_outcome'], 'actual': actual, 'correct': bool(correct), 'probability_of_actual': round(prob, 3)}


@on('match.result_recorded')
def _on_result(match_id):
    ev = evaluate_match(match_id)
    n = refresh_predictions()  # form changed → update the other fixtures
    return {'module': 'predictions', 'evaluation': ev, 'fixtures_updated': n}


@on('match.created')
def _on_created(match_id):
    return {'module': 'predictions', 'fixtures_updated': refresh_predictions()}


def club_perspective(m, p):
    """Probability that Dynasty wins/draws/loses."""
    if is_club(m['home_team']):
        return {'win': p['H'], 'draw': p['D'], 'loss': p['A']}
    if is_club(m['away_team']):
        return {'win': p['A'], 'draw': p['D'], 'loss': p['H']}
    return None


def fixture_cards(limit=20, public=False):
    states = local_states()
    with db() as (_, cur):
        fixtures = upcoming_fixtures(cur)[:limit]
        ids = [f['match_id'] for f in fixtures]
        preds = {}
        if ids:
            cur.execute("SELECT * FROM ml_predictions WHERE actual_outcome IS NULL AND match_id IN (" + ','.join(['%s'] * len(ids)) + ")", ids)
            for p in cur.fetchall():
                preds[p['match_id']] = p
    out = []
    for m in fixtures:
        hk, ak = squad_key(m['home_team'], m['team_group'], m['age_group']), squad_key(m['away_team'], m['team_group'], m['age_group'])
        h, a = states[hk], states[ak]
        p = preds.get(m['match_id'])
        probs = {'H': float(p['p_home']), 'D': float(p['p_draw']), 'A': float(p['p_away'])} if p else None

        def team_info(st):
            return {'played': st.n, 'form': [x['result'] for x in list(st.log)[-5:]],
                    'gf_avg': round(_avg(st.gf, 0), 2) if st.gf else None, 'ga_avg': round(_avg(st.ga, 0), 2) if st.ga else None,
                    'rating': round(st.elo), 'recent': list(st.log)[-5:][::-1]}
        card = {
            'match_id': m['match_id'], 'date': str(to_date(m['match_date'])), 'time': str(m.get('match_time') or '')[:5],
            'home_team': m['home_team'], 'away_team': m['away_team'], 'venue': m.get('venue'),
            'team_group': m.get('team_group'), 'age_group': m.get('age_group'),
            'probabilities': {k: round(v, 3) for k, v in probs.items()} if probs else None,
            'dynasty': {k: round(v, 3) for k, v in club_perspective(m, probs).items()} if probs and club_perspective(m, probs) else None,
            'predicted_outcome': p['predicted_outcome'] if p else None, 'data_quality': p['data_quality'] if p else quality(h, a),
            'model_version': p['model_version'] if p else None, 'home': team_info(h), 'away': team_info(a),
        }
        if not public and p:
            card['features'] = from_json(p['features_json'], {})
        out.append(card)
    return out


def track_record():
    with db() as (_, cur):
        cur.execute("""SELECT p.*, t.home_team, t.away_team, t.home_score, t.away_score, t.match_date, t.team_group
                       FROM ml_predictions p JOIN team_matches t ON t.match_id = p.match_id
                       WHERE p.actual_outcome IS NOT NULL ORDER BY p.evaluated_at DESC""")
        rows = clean_rows(cur.fetchall())
    n = len(rows)
    return {'evaluated': n, 'correct': sum(r['is_correct'] or 0 for r in rows),
            'accuracy': round(sum(r['is_correct'] or 0 for r in rows) / n, 3) if n else None,
            'avg_log_loss': round(sum(float(r['log_loss']) for r in rows) / n, 3) if n else None,
            'recent': rows[:10]}


# ================================================================ routes
@bp.route('/api/ml/status')
@require('predictions')
def status():
    m = active_model()
    if not m:
        return jsonify({'trained': False, 'training': _lock.locked()})
    out = clean_row({k: v for k, v in m.items() if k != 'metrics_json'})
    out['metrics'] = from_json(m['metrics_json'], {})
    out['trained'] = True
    out['training'] = _lock.locked()
    out['track_record'] = track_record()
    return jsonify(out)


@bp.route('/api/ml/train', methods=['POST'])
@require('predictions')
def train_route():
    if _lock.locked():
        return jsonify({'error': 'Training is already running'}), 409
    try:
        from flask import session
        version, metrics = train(triggered_by=session.get('username') or 'staff')
        n = refresh_predictions()
        notify('ml', f'Prediction model retrained ({metrics["selected"]})',
               f"Test accuracy {metrics['comparison'][metrics['selected']]['accuracy']:.0%}, {n} fixtures updated", '/dashboard#ml',
               capability='predictions')
        return jsonify({'success': True, 'version': version, 'metrics': metrics, 'fixtures_updated': n})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ml/predictions')
@require('predictions')
def predictions_staff():
    if active_model() is None:
        return jsonify({'trained': False, 'fixtures': [], 'training': _lock.locked()})
    if request.args.get('refresh') == '1':
        refresh_predictions()
    return jsonify({'trained': True, 'fixtures': fixture_cards(), 'track_record': track_record()})


@bp.route('/api/public/match-centre')
def predictions_public():
    model = active_model()
    with db() as (_, cur):
        cur.execute("SELECT match_id, match_date, match_time, home_team, away_team, home_score, away_score, venue, team_group, age_group "
                    "FROM team_matches WHERE is_completed = 1 ORDER BY match_date DESC LIMIT 12")
        results = clean_rows(cur.fetchall())
    cards = fixture_cards(public=True) if model else []
    tr = track_record() if model else None
    met = from_json(model['metrics_json'], {}) if model else {}
    sel = met.get('comparison', {}).get(met.get('selected'), {}) if met else {}
    base = met.get('comparison', {}).get('Baseline (always the historical average)', {}) if met else {}
    return jsonify({'fixtures': cards, 'results': results, 'model': {
        'trained': bool(model), 'version': model['version'] if model else None, 'algorithm': model['algorithm'] if model else None,
        'test_accuracy': sel.get('accuracy'), 'baseline_accuracy': base.get('accuracy'), 'trained_on': met.get('n_used'),
        'accuracy_live': tr['accuracy'] if tr else None, 'evaluated': tr['evaluated'] if tr else 0}})


@bp.route('/api/ml/history/import', methods=['POST'])
@require('predictions')
def import_history():
    """CSV: date, home_team, away_team, home_goals, away_goals (+ optional competition).
    The squad (team_group / age_group) comes from the form."""
    import csv
    from documents import parse_date
    f = request.files.get('file')
    team_group, age_group = request.form.get('team_group'), request.form.get('age_group')
    if not f:
        return jsonify({'error': 'Choose a CSV file'}), 400
    text = f.read().decode('utf-8-sig', errors='ignore')
    rows = list(csv.DictReader(io.StringIO(text)))
    norm = lambda r: {k.strip().lower().replace(' ', '_'): (v or '').strip() for k, v in r.items() if k}
    added, problems = 0, []
    with db() as (_, cur):
        for i, r in enumerate(map(norm, rows), start=2):
            d = parse_date(r.get('date') or r.get('match_date'))
            try:
                hg, ag = int(r.get('home_goals') or r.get('fthg')), int(r.get('away_goals') or r.get('ftag'))
            except (TypeError, ValueError):
                problems.append(f'line {i}: goals missing'); continue
            home, away = r.get('home_team') or r.get('hometeam'), r.get('away_team') or r.get('awayteam')
            if not (d and home and away):
                problems.append(f'line {i}: date or team missing'); continue
            if d > date.today():
                problems.append(f'line {i}: future date'); continue
            cur.execute("SELECT hist_id FROM ml_history WHERE match_date=%s AND home_team=%s AND away_team=%s", (d, home, away))
            if cur.fetchall():
                problems.append(f'line {i}: already imported'); continue
            cur.execute("INSERT INTO ml_history (match_date, competition, home_team, away_team, home_goals, away_goals, team_group, age_group, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (d, (r.get('competition') or 'local')[:80], home[:120], away[:120], hg, ag, team_group, age_group, now()))
            added += 1
        audit('ml_history_import', 'ml_history', None, {'added': added, 'problems': len(problems)}, cur=cur)
    n = refresh_predictions() if added else 0
    return jsonify({'added': added, 'problems': problems[:30], 'fixtures_updated': n})
