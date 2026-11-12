"""
Dynasty FC — shared core for the integrated modules.

* db()            – connection/cursor context manager (dict rows, commit/rollback)
* capabilities    – one permission map used by every module and the assistant
* events          – in-process event bus so modules react to each other
* notify()/audit() – notifications for staff and an audit trail of who did what

SQL here and in the modules is kept portable between MySQL and SQLite:
no ON DUPLICATE KEY, no NOW(), no reserved words as column names.
"""
import json
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import jsonify, session

from database.db import get_db_connection

# --------------------------------------------------------------------- roles
ROLE_CAPS = {
    'System Administrator': {'public', 'players', 'teams', 'fixtures', 'development', 'documents',
                             'documents_finance', 'documents_medical', 'finance', 'finance_approve',
                             'predictions', 'staff', 'audit', 'admin'},
    'Club Administrator': {'public', 'players', 'teams', 'fixtures', 'development', 'documents',
                           'documents_finance', 'finance', 'finance_approve', 'predictions', 'staff', 'audit'},
    'Coach': {'public', 'players', 'teams', 'fixtures', 'development', 'documents', 'documents_medical',
              'predictions'},
    'Player': {'public'},
}
STAFF_ROLES = ('System Administrator', 'Club Administrator', 'Coach')

# which capability is needed to see a document of a given access level
DOC_ACCESS_CAP = {'general': 'documents', 'finance': 'documents_finance',
                  'medical': 'documents_medical', 'admin': 'admin'}


def current_role():
    return session.get('role')


def caps_for(role=None):
    role = role if role is not None else current_role()
    return ROLE_CAPS.get(role, {'public'})


def can(cap, role=None):
    return cap in caps_for(role)


def require(cap):
    """API decorator: 401 when logged out, 403 without the capability."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if cap != 'public' and 'user_id' not in session:
                return jsonify({'error': 'Please log in'}), 401
            if not can(cap):
                return jsonify({'error': 'You do not have permission for this'}), 403
            return f(*args, **kwargs)
        return wrapper
    return deco


# ------------------------------------------------------------------------ db
_tx = threading.local()


@contextmanager
def db():
    """Connection + dict cursor. Commits on success, rolls back on error.
    Events emitted inside a transaction run only after the outermost one commits,
    so handlers always see the committed rows (and never run if it rolled back)."""
    depth = getattr(_tx, 'depth', 0)
    if depth == 0:
        _tx.pending = []
    _tx.depth = depth + 1
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    ok = False
    try:
        yield conn, cur
        conn.commit()
        ok = True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        finally:
            conn.close()
        _tx.depth -= 1
        if _tx.depth == 0:
            pending, _tx.pending = _tx.pending, []
            if ok:
                for event, payload in pending:
                    _dispatch(event, payload)


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, timedelta):
        s = int(v.total_seconds())
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    if isinstance(v, (bytes, bytearray)):
        return None  # blobs are never sent to the browser
    return v


def clean_row(row):
    return {k: jsonable(v) for k, v in (row or {}).items()}


def clean_rows(rows):
    return [clean_row(r) for r in rows]


def to_date(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def to_float(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def now():
    return datetime.now().replace(microsecond=0)


def columns(cur, table):
    try:
        cur.execute(f"SELECT * FROM {table} WHERE 1=0")
        cur.fetchall()
        return {c[0] for c in cur.description}
    except Exception:
        return set()


def table_exists(cur, table):
    return bool(columns(cur, table))


def run_schema(statements, indexes=()):
    with db() as (conn, cur):
        for s in statements:
            cur.execute(s)
    for ix in indexes:  # MySQL has no CREATE INDEX IF NOT EXISTS
        try:
            with db() as (conn, cur):
                cur.execute(ix)
        except Exception:
            pass


def as_json(v):
    return json.dumps(v, default=jsonable, ensure_ascii=False)


def from_json(s, default=None):
    if not s:
        return default
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------- audit + notifications
CORE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS audit_log (
        audit_id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        username VARCHAR(120),
        action VARCHAR(80) NOT NULL,
        entity VARCHAR(60),
        entity_id VARCHAR(60),
        details LONGTEXT,
        created_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        notification_id INT AUTO_INCREMENT PRIMARY KEY,
        kind VARCHAR(40),
        title VARCHAR(255),
        body TEXT,
        link VARCHAR(255),
        capability VARCHAR(40),
        is_dismissed TINYINT DEFAULT 0,
        created_at DATETIME
    )""",
]
CORE_INDEXES = [
    "CREATE INDEX idx_audit_created ON audit_log (created_at)",
    "CREATE INDEX idx_notif_created ON notifications (created_at)",
]


def audit(action, entity=None, entity_id=None, details=None, cur=None):
    row = (session.get('user_id') if _has_request() else None,
           session.get('username') if _has_request() else 'system',
           action[:80], entity, None if entity_id is None else str(entity_id)[:60],
           as_json(details) if details is not None else None, now())
    sql = ("INSERT INTO audit_log (user_id, username, action, entity, entity_id, details, created_at) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s)")
    try:
        if cur is not None:
            cur.execute(sql, row)
        else:
            with db() as (_, c):
                c.execute(sql, row)
    except Exception as e:
        print(f"[core] audit failed: {e}")


def notify(kind, title, body='', link=None, capability='documents', cur=None):
    sql = ("INSERT INTO notifications (kind, title, body, link, capability, is_dismissed, created_at) "
           "VALUES (%s, %s, %s, %s, %s, 0, %s)")
    args = (kind, title[:255], body, link, capability, now())
    try:
        if cur is not None:
            cur.execute(sql, args)
        else:
            with db() as (_, c):
                c.execute(sql, args)
    except Exception as e:
        print(f"[core] notify failed: {e}")


def _has_request():
    try:
        from flask import has_request_context
        return has_request_context()
    except Exception:
        return False


# ------------------------------------------------------------------ events
_handlers = {}


def on(event):
    def deco(fn):
        _handlers.setdefault(event, []).append(fn)
        return fn
    return deco


def emit(event, **payload):
    """Run every handler for an event (after the current transaction commits).
    A failing handler never breaks the request that triggered it; the failure
    is recorded instead."""
    if getattr(_tx, 'depth', 0) > 0:
        _tx.pending.append((event, payload))
        return []
    return _dispatch(event, payload)


def _dispatch(event, payload):
    results = []
    for fn in _handlers.get(event, []):
        try:
            results.append(fn(**payload))
        except Exception as e:
            print(f"[events] {event} -> {fn.__name__} failed: {e}")
            audit('event_failed', 'event', event, {'handler': fn.__name__, 'error': str(e)})
            notify('error', f'Automation step failed: {fn.__name__}', str(e)[:500], capability='admin')
    return results


# ---------------------------------------------------------------- blueprint
def register_core_routes(app):
    from flask import Blueprint, request

    bp = Blueprint('core', __name__)

    @bp.route('/api/me/capabilities')
    def my_caps():
        return jsonify({'role': current_role(), 'logged_in': 'user_id' in session,
                        'capabilities': sorted(caps_for())})

    @bp.route('/api/notifications')
    @require('documents')
    def list_notifications():
        mine = caps_for()
        with db() as (_, cur):
            cur.execute("SELECT * FROM notifications WHERE is_dismissed = 0 ORDER BY created_at DESC, notification_id DESC LIMIT 60")
            rows = [r for r in cur.fetchall() if (r.get('capability') or 'documents') in mine]
        return jsonify(clean_rows(rows))

    @bp.route('/api/notifications/<int:nid>/dismiss', methods=['POST'])
    @require('documents')
    def dismiss(nid):
        with db() as (_, cur):
            cur.execute("UPDATE notifications SET is_dismissed = 1 WHERE notification_id = %s", (nid,))
        return jsonify({'success': True})

    @bp.route('/api/notifications/dismiss-all', methods=['POST'])
    @require('documents')
    def dismiss_all():
        with db() as (_, cur):
            cur.execute("UPDATE notifications SET is_dismissed = 1 WHERE is_dismissed = 0")
        return jsonify({'success': True})

    @bp.route('/api/audit-log')
    @require('audit')
    def audit_log():
        limit = min(int(request.args.get('limit', 200)), 1000)
        with db() as (_, cur):
            cur.execute(f"SELECT audit_id, username, action, entity, entity_id, details, created_at "
                        f"FROM audit_log ORDER BY audit_id DESC LIMIT {limit}")
            rows = clean_rows(cur.fetchall())
        for r in rows:
            r['details'] = from_json(r.get('details'), r.get('details'))
        return jsonify(rows)

    app.register_blueprint(bp)

    # Close APIs that exposed club data to anyone on the internet.
    STAFF_ONLY_GET = {'/api/finance', '/api/inventory', '/api/donors', '/api/attendance'}

    @app.before_request
    def _lock_open_apis():
        if request.method == 'GET' and request.path in STAFF_ONLY_GET and current_role() not in STAFF_ROLES:
            return jsonify({'error': 'Staff access only'}), 403
