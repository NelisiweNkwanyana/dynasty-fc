"""
Player Development Intelligence (PDI) for Dynasty FC.

Adds, on top of the existing `players` table:
  * player_profiles      – photo, preferred foot, development focus
  * player_season_stats  – appearances, goals, assists, minutes, attendance
  * player_assessments   – dated coach ratings across six attributes

All routes are staff-only and return JSON. SQL is kept deliberately
portable (no MySQL-only functions in queries) so the module can be
tested without a MySQL server.
"""
import os
import random
import hashlib
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, session, current_app
from werkzeug.utils import secure_filename

from database.db import get_db_connection

pdi = Blueprint('pdi', __name__)

STAFF_ROLES = ('System Administrator', 'Club Administrator', 'Coach')
ATTRS = ('pace', 'technique', 'passing', 'defending', 'physical', 'mentality')
CURRENT_SEASON = os.getenv('PDI_SEASON', '2026')

# ----------------------------------------------------------------- helpers

def api_staff_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not logged in'}), 401
        if session.get('role') not in STAFF_ROLES:
            return jsonify({'error': 'Staff access only'}), 403
        return f(*args, **kwargs)
    return wrapper


def _conn():
    return get_db_connection()


def _rows(cursor):
    cols = [c[0] for c in cursor.description] if cursor.description else []
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    return v


def table_columns(cursor, table):
    """Column names of a table, using a zero-row select (works on any SQL DB)."""
    cursor.execute(f"SELECT * FROM {table} WHERE 1=0")
    cursor.fetchall()
    return {c[0] for c in cursor.description}


def _exec_quiet(cursor, sql):
    try:
        cursor.execute(sql)
        return True
    except Exception:
        return False


def overall_of(a):
    if not a:
        return None
    vals = [a.get(k) for k in ATTRS if a.get(k) is not None]
    return round(sum(vals) / len(vals)) if vals else None


def tier_of(ovr):
    if ovr is None:
        return 'unrated'
    if ovr >= 80:
        return 'elite'
    if ovr >= 70:
        return 'gold'
    if ovr >= 60:
        return 'silver'
    return 'bronze'


# ------------------------------------------------------------ schema setup

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS player_profiles (
        player_id INT PRIMARY KEY,
        photo_url VARCHAR(255),
        preferred_foot VARCHAR(10),
        development_focus VARCHAR(255),
        is_sample TINYINT DEFAULT 0,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_season_stats (
        player_id INT NOT NULL,
        season VARCHAR(10) NOT NULL,
        appearances INT DEFAULT 0,
        goals INT DEFAULT 0,
        assists INT DEFAULT 0,
        minutes_played INT DEFAULT 0,
        sessions_attended INT DEFAULT 0,
        sessions_total INT DEFAULT 0,
        updated_at DATETIME,
        PRIMARY KEY (player_id, season)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_assessments (
        assessment_id INT AUTO_INCREMENT PRIMARY KEY,
        player_id INT NOT NULL,
        assessed_on DATE NOT NULL,
        pace INT NOT NULL,
        technique INT NOT NULL,
        passing INT NOT NULL,
        defending INT NOT NULL,
        physical INT NOT NULL,
        mentality INT NOT NULL,
        notes TEXT,
        assessed_by INT,
        created_at DATETIME
    )
    """,
]


def ensure_schema():
    conn = _conn()
    cur = conn.cursor()
    try:
        for stmt in SCHEMA:
            cur.execute(stmt)
        # index for timeline lookups (ignore if it already exists)
        _exec_quiet(cur, "CREATE INDEX idx_assess_player ON player_assessments (player_id, assessed_on)")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def legacy_hash(password):
    # Matches the existing app.hash_password (sha256 hex) so seeded logins work.
    return hashlib.sha256(password.encode()).hexdigest()


def seed_admin():
    """Create the dashboard admin account if it does not exist yet.
    Never overwrites an existing account's password."""
    email = os.getenv('ADMIN_SEED_EMAIL', 'admin@dynastyfc.com').strip().lower()
    password = os.getenv('ADMIN_SEED_PASSWORD', 'admin123')
    if not email or not password:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM users WHERE LOWER(email) = %s", (email,))
        if cur.fetchall():
            return
        cur.execute(
            "INSERT INTO users (username, password_hash, email, role, is_active) "
            "VALUES (%s, %s, %s, %s, 1)",
            ('Admin', __import__('accounts').hash_password(password), email, 'System Administrator'))
        conn.commit()
        print(f"[PDI] Created admin account {email}")
    finally:
        cur.close()
        conn.close()


# Placeholder names – rename players from the dashboard to match the real squad.
BOYS = [
    ('Lwazi', 'Mokoena', 'Goalkeeper', 1, 'Right'),
    ('Kagiso', 'Dlamini', 'Centre Back', 4, 'Right'),
    ('Ethan', 'van Wyk', 'Centre Back', 5, 'Left'),
    ('Liam', 'Botha', 'Right Back', 2, 'Right'),
    ('Jayden', 'Pillay', 'Left Back', 3, 'Left'),
    ('Sipho', 'Khumalo', 'Defensive Midfielder', 6, 'Right'),
    ('Mateo', 'Fernandes', 'Central Midfielder', 8, 'Right'),
    ('Ayanda', 'Zulu', 'Central Midfielder', 10, 'Left'),
    ('Ruan', 'Pretorius', 'Attacking Midfielder', 14, 'Right'),
    ('Daniel', 'Chen', 'Left Winger', 11, 'Left'),
    ('Luca', 'Smit', 'Right Winger', 7, 'Right'),
    ('Tumelo', 'Nkosi', 'Striker', 9, 'Right'),
    ('Nathan', 'Jacobs', 'Striker', 19, 'Both'),
]
GIRLS = [
    ('Amahle', 'Ndlovu', 'Goalkeeper', 1, 'Right'),
    ('Naledi', 'Mahlangu', 'Centre Back', 4, 'Right'),
    ('Zara', 'Adams', 'Centre Back', 5, 'Left'),
    ('Lerato', 'Molefe', 'Right Back', 2, 'Right'),
    ('Isabella', 'Naidoo', 'Left Back', 3, 'Left'),
    ('Mia', 'Lee', 'Defensive Midfielder', 6, 'Right'),
    ('Kayla', 'Daniels', 'Central Midfielder', 8, 'Right'),
    ('Thando', 'Mthembu', 'Central Midfielder', 16, 'Both'),
    ('Ava', 'Coetzee', 'Attacking Midfielder', 10, 'Right'),
    ('Palesa', 'Sithole', 'Left Winger', 11, 'Left'),
    ('Sofia', 'Martins', 'Right Winger', 7, 'Right'),
    ('Nomsa', 'Radebe', 'Striker', 9, 'Right'),
    ('Chloe', 'Hendricks', 'Striker', 18, 'Right'),
    ('Keabetswe', 'Moloi', 'Central Midfielder', 15, 'Right'),
    ('Emma', 'Steyn', 'Wing Back', 12, 'Left'),
]

POSITION_PROFILE = {
    # base attribute emphasis per position (pace, tec, pas, def, phy, men)
    'Goalkeeper': (48, 55, 58, 70, 66, 68),
    'Centre Back': (58, 54, 58, 72, 70, 64),
    'Right Back': (68, 58, 62, 64, 62, 60),
    'Left Back': (68, 58, 62, 64, 62, 60),
    'Wing Back': (70, 60, 63, 60, 62, 60),
    'Defensive Midfielder': (58, 62, 66, 68, 66, 66),
    'Central Midfielder': (62, 66, 70, 58, 62, 66),
    'Attacking Midfielder': (66, 72, 70, 46, 56, 64),
    'Left Winger': (76, 70, 62, 42, 56, 60),
    'Right Winger': (76, 70, 62, 42, 56, 60),
    'Striker': (70, 68, 58, 40, 66, 64),
}

FOCUS = {
    'Goalkeeper': 'Distribution under pressure and command of the box',
    'Centre Back': 'Playing out from the back and aerial duels',
    'Right Back': 'Timing of overlapping runs and recovery speed',
    'Left Back': 'Timing of overlapping runs and recovery speed',
    'Wing Back': 'Final-third delivery and defensive transitions',
    'Defensive Midfielder': 'Scanning before receiving and switching play',
    'Central Midfielder': 'Forward passing range and box-to-box endurance',
    'Attacking Midfielder': 'Decision-making in the final third',
    'Left Winger': '1v1 finishing and tracking back',
    'Right Winger': '1v1 finishing and tracking back',
    'Striker': 'Movement in the box and first-time finishing',
}

ASSESS_DATES = [date(2026, 2, 14), date(2026, 4, 18), date(2026, 6, 20), date(2026, 8, 22), date(2026, 10, 10)]
NOTES = [
    'Pre-season baseline assessment.',
    'Settling into the system; good attitude at training.',
    'Clear improvement in decision-making on the ball.',
    'Consistent performer; leads by example in sessions.',
    'Strong progress this season – ready for more responsibility.',
]


def seed_u15_squads():
    """Insert the U15 boys (Dynamights) and girls (Diamond Divas) with photos and
    sample development history. Skips anything already seeded."""
    if os.getenv('SEED_U15_PLAYERS', 'true').lower() != 'true':
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        pcols = table_columns(cur, 'players')
        now = datetime.now()
        created = 0
        squads = [('Dynamights', 'u15b', BOYS), ('Diamond Divas', 'u15g', GIRLS)]
        for team, prefix, roster in squads:
            for i, (first, last, pos, num, foot) in enumerate(roster, 1):
                mysafa = f"DFC-{prefix.upper()}-{i:02d}"
                cur.execute("SELECT player_id FROM players WHERE mysafa_id = %s", (mysafa,))
                found = cur.fetchall()
                if found:
                    continue
                rnd = random.Random(f"{mysafa}-{CURRENT_SEASON}")
                dob = date(2012, rnd.randint(1, 12), rnd.randint(1, 28))
                values = {
                    'mysafa_id': mysafa, 'first_name': first, 'last_name': last,
                    'date_of_birth': dob, 'position': pos, 'team_group': team,
                    'age_group': 'U15', 'is_active': 1, 'jersey_number': num,
                    'is_injured': 1 if rnd.random() < 0.08 else 0,
                }
                cols = [c for c in values if c in pcols]
                cur.execute(
                    f"INSERT INTO players ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    [values[c] for c in cols])
                pid = cur.lastrowid
                if not pid:
                    cur.execute("SELECT player_id FROM players WHERE mysafa_id = %s", (mysafa,))
                    pid = cur.fetchall()[0][0]

                cur.execute(
                    "INSERT INTO player_profiles (player_id, photo_url, preferred_foot, development_focus, is_sample, updated_at) "
                    "VALUES (%s, %s, %s, %s, 1, %s)",
                    (pid, f"/images/players/{prefix}-{i:02d}.jpg", foot, FOCUS.get(pos), now))

                # season stats
                attack = pos in ('Striker', 'Left Winger', 'Right Winger', 'Attacking Midfielder')
                mid = 'Midfielder' in pos
                apps = rnd.randint(10, 18)
                goals = rnd.randint(5, 16) if attack else (rnd.randint(1, 6) if mid else rnd.randint(0, 2))
                if pos == 'Goalkeeper':
                    goals = 0
                assists = rnd.randint(3, 10) if (attack or mid) else rnd.randint(0, 3)
                total = 64
                attended = rnd.randint(51, 63) if rnd.random() > 0.14 else rnd.randint(38, 46)
                cur.execute(
                    "INSERT INTO player_season_stats (player_id, season, appearances, goals, assists, minutes_played, "
                    "sessions_attended, sessions_total, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (pid, CURRENT_SEASON, apps, goals, assists, apps * rnd.randint(52, 70), attended, total, now))

                # assessment timeline with a believable growth curve
                base = POSITION_PROFILE.get(pos, (60,) * 6)
                talent = rnd.randint(-6, 8)
                growth = rnd.uniform(1.2, 3.6)
                for step, d in enumerate(ASSESS_DATES):
                    vals = []
                    for b in base:
                        v = b + talent + step * growth + rnd.randint(-3, 3)
                        vals.append(max(30, min(95, round(v))))
                    cur.execute(
                        "INSERT INTO player_assessments (player_id, assessed_on, pace, technique, passing, defending, "
                        "physical, mentality, notes, assessed_by, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (pid, d, *vals, NOTES[step], None, now))
                created += 1
        conn.commit()
        if created:
            print(f"[PDI] Seeded {created} U15 players with photos and development history")
    finally:
        cur.close()
        conn.close()


def init_pdi(app):
    """Run migrations + seeds once at startup. Never crashes the app."""
    app.register_blueprint(pdi)
    for step in (ensure_schema, seed_admin, seed_u15_squads):
        try:
            step()
        except Exception as e:  # DB down, permissions, etc.
            print(f"[PDI] {step.__name__} skipped: {e}")


# ------------------------------------------------------------------ queries

def _load_squad(cur, team=None, player_id=None):
    q = ("SELECT p.*, pr.photo_url, pr.preferred_foot, pr.development_focus, pr.is_sample "
         "FROM players p LEFT JOIN player_profiles pr ON pr.player_id = p.player_id WHERE 1=1")
    params = []
    if team:
        q += " AND p.team_group = %s"
        params.append(team)
    if player_id:
        q += " AND p.player_id = %s"
        params.append(player_id)
    cur.execute(q, params)
    players = _rows(cur)
    if not players:
        return []
    ids = [p['player_id'] for p in players]
    marks = ', '.join(['%s'] * len(ids))

    cur.execute(f"SELECT * FROM player_season_stats WHERE season = %s AND player_id IN ({marks})",
                [CURRENT_SEASON, *ids])
    stats = {s['player_id']: s for s in _rows(cur)}

    cur.execute(f"SELECT * FROM player_assessments WHERE player_id IN ({marks}) "
                f"ORDER BY player_id, assessed_on, assessment_id", ids)
    history = {}
    for a in _rows(cur):
        history.setdefault(a['player_id'], []).append(a)

    out = []
    for p in players:
        hist = history.get(p['player_id'], [])
        latest = hist[-1] if hist else None
        first = hist[0] if hist else None
        prev = hist[-2] if len(hist) > 1 else None
        s = stats.get(p['player_id'], {})
        total = s.get('sessions_total') or 0
        att = round(100 * (s.get('sessions_attended') or 0) / total) if total else None
        ovr = overall_of(latest)
        out.append({
            'player_id': p['player_id'],
            'first_name': p.get('first_name'),
            'last_name': p.get('last_name'),
            'position': p.get('position'),
            'jersey_number': p.get('jersey_number'),
            'team_group': p.get('team_group'),
            'age_group': p.get('age_group'),
            'is_injured': bool(p.get('is_injured')),
            'is_active': bool(p.get('is_active', 1)),
            'photo_url': p.get('photo_url'),
            'preferred_foot': p.get('preferred_foot'),
            'development_focus': p.get('development_focus'),
            'is_sample': bool(p.get('is_sample')),
            'overall': ovr,
            'previous_overall': overall_of(prev),
            'season_change': (ovr - overall_of(first)) if (ovr is not None and first) else None,
            'tier': tier_of(ovr),
            'attributes': {k: latest[k] for k in ATTRS} if latest else None,
            'last_assessed': _iso(latest['assessed_on']) if latest else None,
            'stats': {
                'appearances': s.get('appearances', 0),
                'goals': s.get('goals', 0),
                'assists': s.get('assists', 0),
                'minutes_played': s.get('minutes_played', 0),
                'sessions_attended': s.get('sessions_attended', 0),
                'sessions_total': total,
                'attendance_pct': att,
            },
            '_history': hist,
        })
    return out


def _public(p, with_history=False):
    d = {k: v for k, v in p.items() if k != '_history'}
    if with_history:
        d['assessments'] = [{
            'assessment_id': a['assessment_id'],
            'assessed_on': _iso(a['assessed_on']),
            'overall': overall_of(a),
            **{k: a[k] for k in ATTRS},
            'notes': a.get('notes'),
        } for a in p['_history']]
    return d


# ------------------------------------------------------------------- routes

@pdi.route('/api/pdi/squad', methods=['GET'])
@api_staff_required
def squad():
    team = request.args.get('team') or None
    conn = _conn(); cur = conn.cursor()
    try:
        players = [p for p in _load_squad(cur, team=team) if p['is_active']]
        players.sort(key=lambda p: (p['overall'] is None, -(p['overall'] or 0)))
        return jsonify({'season': CURRENT_SEASON, 'players': [_public(p) for p in players]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@pdi.route('/api/pdi/players/<int:player_id>', methods=['GET'])
@api_staff_required
def player_detail(player_id):
    conn = _conn(); cur = conn.cursor()
    try:
        found = _load_squad(cur, player_id=player_id)
        if not found:
            return jsonify({'error': 'Player not found'}), 404
        return jsonify(_public(found[0], with_history=True))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


def _int_in_range(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


@pdi.route('/api/pdi/players/<int:player_id>/assessments', methods=['POST'])
@api_staff_required
def add_assessment(player_id):
    data = request.get_json(silent=True) or {}
    vals = {k: _int_in_range(data.get(k), 1, 99) for k in ATTRS}
    bad = [k for k, v in vals.items() if v is None]
    if bad:
        return jsonify({'error': f"Ratings must be whole numbers 1–99: {', '.join(bad)}"}), 400
    try:
        on = datetime.strptime(data.get('assessed_on') or date.today().isoformat(), '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'assessed_on must be YYYY-MM-DD'}), 400
    if on > date.today():
        return jsonify({'error': 'Assessment date cannot be in the future'}), 400
    notes = (data.get('notes') or '').strip()[:2000]
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT player_id FROM players WHERE player_id = %s", (player_id,))
        if not cur.fetchall():
            return jsonify({'error': 'Player not found'}), 404
        cur.execute(
            "INSERT INTO player_assessments (player_id, assessed_on, pace, technique, passing, defending, "
            "physical, mentality, notes, assessed_by, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (player_id, on, *[vals[k] for k in ATTRS], notes, session.get('user_id'), datetime.now()))
        conn.commit()
        from core import emit
        emit('player.assessed', player_id=player_id)
        return jsonify({'success': True, 'overall': overall_of(vals)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@pdi.route('/api/pdi/players/<int:player_id>/stats', methods=['PUT'])
@api_staff_required
def update_stats(player_id):
    data = request.get_json(silent=True) or {}
    fields = ('appearances', 'goals', 'assists', 'minutes_played', 'sessions_attended', 'sessions_total')
    vals = {f: _int_in_range(data.get(f, 0), 0, 100000) for f in fields}
    if any(v is None for v in vals.values()):
        return jsonify({'error': 'Stats must be whole numbers of zero or more'}), 400
    if vals['sessions_attended'] > vals['sessions_total']:
        return jsonify({'error': 'Sessions attended cannot exceed total sessions'}), 400
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM player_season_stats WHERE player_id = %s AND season = %s", (player_id, CURRENT_SEASON))
        cur.execute(
            "INSERT INTO player_season_stats (player_id, season, appearances, goals, assists, minutes_played, "
            "sessions_attended, sessions_total, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (player_id, CURRENT_SEASON, *[vals[f] for f in fields], datetime.now()))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@pdi.route('/api/pdi/players/<int:player_id>/profile', methods=['PUT'])
@api_staff_required
def update_profile(player_id):
    """Rename / re-position a player and update their development focus."""
    data = request.get_json(silent=True) or {}
    conn = _conn(); cur = conn.cursor()
    try:
        pcols = table_columns(cur, 'players')
        updates = {}
        for k in ('first_name', 'last_name', 'position', 'jersey_number', 'is_injured'):
            if k in data and k in pcols:
                updates[k] = data[k]
        if 'first_name' in updates and not str(updates['first_name']).strip():
            return jsonify({'error': 'First name is required'}), 400
        if updates:
            sets = ', '.join(f"{k} = %s" for k in updates)
            cur.execute(f"UPDATE players SET {sets} WHERE player_id = %s", [*updates.values(), player_id])
        if 'development_focus' in data or 'preferred_foot' in data:
            cur.execute("SELECT player_id FROM player_profiles WHERE player_id = %s", (player_id,))
            if cur.fetchall():
                cur.execute("UPDATE player_profiles SET development_focus = %s, preferred_foot = %s, updated_at = %s "
                            "WHERE player_id = %s",
                            (data.get('development_focus'), data.get('preferred_foot'), datetime.now(), player_id))
            else:
                cur.execute("INSERT INTO player_profiles (player_id, development_focus, preferred_foot, updated_at) "
                            "VALUES (%s, %s, %s, %s)",
                            (player_id, data.get('development_focus'), data.get('preferred_foot'), datetime.now()))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@pdi.route('/api/pdi/players/<int:player_id>/photo', methods=['POST'])
@api_staff_required
def upload_photo(player_id):
    f = request.files.get('file')
    if not f or '.' not in (f.filename or ''):
        return jsonify({'error': 'Choose an image file'}), 400
    ext = f.filename.rsplit('.', 1)[1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'error': 'Use a JPG, PNG or WEBP image'}), 400
    name = f"player_{player_id}_{uuid.uuid4().hex[:10]}.{ext}"
    f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(name)))
    url = f"/uploads/{name}"
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT player_id FROM player_profiles WHERE player_id = %s", (player_id,))
        if cur.fetchall():
            cur.execute("UPDATE player_profiles SET photo_url = %s, updated_at = %s WHERE player_id = %s",
                        (url, datetime.now(), player_id))
        else:
            cur.execute("INSERT INTO player_profiles (player_id, photo_url, updated_at) VALUES (%s, %s, %s)",
                        (player_id, url, datetime.now()))
        conn.commit()
        return jsonify({'success': True, 'photo_url': url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@pdi.route('/api/pdi/insights', methods=['GET'])
@api_staff_required
def insights():
    team = request.args.get('team') or None
    conn = _conn(); cur = conn.cursor()
    try:
        squad_ = [p for p in _load_squad(cur, team=team) if p['is_active']]
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

    rated = [p for p in squad_ if p['overall'] is not None]
    att = [p for p in squad_ if p['stats']['attendance_pct'] is not None]
    brief = lambda p: {k: p[k] for k in ('player_id', 'first_name', 'last_name', 'position', 'photo_url',
                                         'team_group', 'overall', 'season_change')} | {'stats': p['stats']}

    stale_cutoff = date.today() - timedelta(days=int(os.getenv('PDI_STALE_DAYS', '45')))
    never = [p for p in squad_ if not p['last_assessed']]
    stale = [p for p in squad_ if p['last_assessed'] and date.fromisoformat(p['last_assessed']) < stale_cutoff]

    # squad average per attribute (latest assessments)
    avg_attrs = {}
    if rated:
        for k in ATTRS:
            avg_attrs[k] = round(sum(p['attributes'][k] for p in rated) / len(rated), 1)

    # season trend: squad average overall at each assessment date
    by_date = {}
    for p in squad_:
        for a in p['_history']:
            by_date.setdefault(_iso(a['assessed_on']), []).append(overall_of(a))
    trend = [{'date': d, 'overall': round(sum(v) / len(v), 1), 'count': len(v)}
             for d, v in sorted(by_date.items())]

    teams = {}
    for p in squad_:
        t = teams.setdefault(p['team_group'] or 'Unassigned', {'players': 0, 'rated': 0, 'ovr_sum': 0, 'goals': 0})
        t['players'] += 1
        t['goals'] += p['stats']['goals'] or 0
        if p['overall'] is not None:
            t['rated'] += 1
            t['ovr_sum'] += p['overall']
    team_summary = [{'team': k, 'players': v['players'], 'goals': v['goals'],
                     'avg_overall': round(v['ovr_sum'] / v['rated'], 1) if v['rated'] else None}
                    for k, v in sorted(teams.items())]

    return jsonify({
        'season': CURRENT_SEASON,
        'squad_size': len(squad_),
        'rated': len(rated),
        'avg_overall': round(sum(p['overall'] for p in rated) / len(rated), 1) if rated else None,
        'avg_attendance': round(sum(p['stats']['attendance_pct'] for p in att) / len(att)) if att else None,
        'total_goals': sum(p['stats']['goals'] or 0 for p in squad_),
        'injured': [brief(p) for p in squad_ if p['is_injured']],
        'top_rated': [brief(p) for p in sorted(rated, key=lambda p: -p['overall'])[:5]],
        'most_improved': [brief(p) for p in sorted([p for p in rated if p['season_change'] is not None],
                                                   key=lambda p: -p['season_change'])[:5]],
        'top_scorers': [brief(p) for p in sorted(squad_, key=lambda p: -(p['stats']['goals'] or 0))[:5]],
        'top_assists': [brief(p) for p in sorted(squad_, key=lambda p: -(p['stats']['assists'] or 0))[:5]],
        'attendance_alerts': [brief(p) for p in sorted([p for p in att if p['stats']['attendance_pct'] < 75],
                                                       key=lambda p: p['stats']['attendance_pct'])],
        'needs_assessment': [brief(p) for p in never + stale],
        'squad_attributes': avg_attrs,
        'trend': trend,
        'teams': team_summary,
        'has_sample_data': any(p['is_sample'] for p in squad_),
    })
