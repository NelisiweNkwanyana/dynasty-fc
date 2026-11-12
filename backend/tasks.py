"""
Dynasty FC — follow-up tasks.

Events create tasks for the right people (by capability), with a due date.
Each task has a source key, so the same situation never creates two open
tasks, and the task closes itself automatically when the underlying thing is
done (result recorded, document decided, assessment saved, …). Staff can also
add their own tasks and tick them off.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session

from core import audit, caps_for, clean_rows, db, now, on, require, run_schema, to_date

bp = Blueprint('tasks', __name__)

SCHEMA = ["""CREATE TABLE IF NOT EXISTS follow_up_tasks (
    task_id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    details TEXT,
    link VARCHAR(255),
    capability VARCHAR(40),
    due_date DATE,
    status VARCHAR(20),
    source_key VARCHAR(120),
    created_by_name VARCHAR(120),
    created_at DATETIME,
    completed_at DATETIME,
    completed_by_name VARCHAR(120),
    auto_completed TINYINT DEFAULT 0
)"""]
INDEXES = ["CREATE INDEX idx_tasks_key ON follow_up_tasks (source_key)", "CREATE INDEX idx_tasks_status ON follow_up_tasks (status)"]


def init_tasks(app):
    run_schema(SCHEMA, INDEXES)
    app.register_blueprint(bp)


def create_task(title, details='', link=None, capability='documents', due=None, source_key=None, cur=None):
    def run(c):
        if source_key:
            c.execute("SELECT task_id FROM follow_up_tasks WHERE source_key = %s AND status = 'open'", (source_key,))
            if c.fetchall():
                return None
        c.execute("""INSERT INTO follow_up_tasks (title, details, link, capability, due_date, status, source_key, created_by_name, created_at)
                     VALUES (%s,%s,%s,%s,%s,'open',%s,%s,%s)""",
                  (title[:255], details, link, capability, to_date(due), source_key, 'automation', now()))
        return c.lastrowid
    if cur is not None:
        return run(cur)
    with db() as (_, c):
        return run(c)


def complete_tasks(source_key, cur=None):
    def run(c):
        c.execute("UPDATE follow_up_tasks SET status='done', completed_at=%s, completed_by_name=%s, auto_completed=1 "
                  "WHERE source_key = %s AND status = 'open'", (now(), 'automation', source_key))
        return c.rowcount
    if cur is not None:
        return run(cur)
    with db() as (_, c):
        return run(c)


# ------------------------------------------------------------ event rules
@on('document.processed')
def _doc_processed(document_id, doc_type, status):
    if status in ('needs_review', 'needs_manual_entry'):
        from core import DOC_ACCESS_CAP
        from documents import ACCESS_FOR_TYPE, DOC_TYPES
        create_task(f'Review document #{document_id} ({DOC_TYPES.get(doc_type, doc_type)})',
                    'The system could not verify everything automatically. Check the highlighted fields, then approve or reject.',
                    f'/dashboard#documents/{document_id}', DOC_ACCESS_CAP[ACCESS_FOR_TYPE.get(doc_type, 'general')],
                    date.today() + timedelta(days=3), f'doc:{document_id}')


@on('document.approved')
def _doc_approved(document_id, **kw):
    complete_tasks(f'doc:{document_id}')


@on('document.rejected')
def _doc_rejected(document_id, **kw):
    complete_tasks(f'doc:{document_id}')


@on('player.registered')
def _registered(player_id, **kw):
    with db() as (_, cur):
        cur.execute("SELECT first_name, last_name FROM players WHERE player_id = %s", (player_id,))
        p = cur.fetchall()
    if p:
        create_task(f"Baseline assessment for {p[0]['first_name']} {p[0]['last_name']}",
                    'New player – record their first ratings so development can be tracked.',
                    f'/dashboard#player/{player_id}', 'development', date.today() + timedelta(days=7), f'assess:{player_id}')


@on('player.assessed')
def _assessed(player_id):
    complete_tasks(f'assess:{player_id}')


@on('player.fitness')
def _fitness(player_id, fit, valid_until=None, name=''):
    if fit:
        complete_tasks(f'injury:{player_id}')
    else:
        due = to_date(valid_until) or date.today() + timedelta(days=14)
        create_task(f'Follow up on {name or "player"}\'s injury', 'Medical certificate says not fit to play. Get an updated certificate before they return.',
                    f'/dashboard#player/{player_id}', 'players', due, f'injury:{player_id}')


@on('match.result_recorded')
def _result(match_id):
    complete_tasks(f'result:{match_id}')
    return None


@on('finance.batch.created')
def _batch(batch_id, flagged, filename):
    if flagged:
        create_task(f'Review {flagged} flagged line(s) in {filename}', 'Possible duplicates or problems need a finance decision.',
                    f'/dashboard#finance/batch/{batch_id}', 'finance_approve', date.today() + timedelta(days=5), f'batch:{batch_id}')


@on('finance.batch.completed')
def _batch_done(batch_id):
    complete_tasks(f'batch:{batch_id}')


@on('contract.stored')
def _contract(document_id, party, end_date):
    end = to_date(end_date)
    if end:
        create_task(f'Renew agreement with {party or "partner"}', f'Ends {end.isoformat()}.', f'/dashboard#documents/{document_id}',
                    'finance', end - timedelta(days=30), f'contract:{document_id}')


def missing_result_tasks():
    """Called by the daily checks: one task per past fixture without a score."""
    with db() as (_, cur):
        cur.execute("SELECT match_id, match_date, home_team, away_team FROM team_matches WHERE is_completed = 0 OR is_completed IS NULL")
        for r in cur.fetchall():
            d = to_date(r['match_date'])
            if d and d < date.today():
                create_task(f"Record the result: {r['home_team']} vs {r['away_team']}", f'Played {d.isoformat()}.',
                            '/dashboard#matches', 'fixtures', d + timedelta(days=1), f"result:{r['match_id']}", cur=cur)


# ----------------------------------------------------------------- routes
def visible(rows):
    mine = caps_for()
    return [r for r in rows if (r.get('capability') or 'documents') in mine]


@bp.route('/api/tasks')
@require('documents')
def list_tasks():
    status = request.args.get('status', 'open')
    with db() as (_, cur):
        cur.execute("SELECT * FROM follow_up_tasks WHERE status = %s ORDER BY due_date IS NULL, due_date, task_id DESC LIMIT 300",
                    (status,))
        rows = clean_rows(visible(cur.fetchall()))
    today = date.today().isoformat()
    for r in rows:
        r['overdue'] = bool(r['status'] == 'open' and r['due_date'] and r['due_date'] < today)
    return jsonify(rows)


@bp.route('/api/tasks', methods=['POST'])
@require('documents')
def add_task():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Give the task a title'}), 400
    due = to_date(data.get('due_date'))
    cap = data.get('capability') if data.get('capability') in caps_for() else 'documents'
    with db() as (_, cur):
        cur.execute("""INSERT INTO follow_up_tasks (title, details, capability, due_date, status, created_by_name, created_at)
                       VALUES (%s,%s,%s,%s,'open',%s,%s)""", (title[:255], (data.get('details') or '')[:2000], cap, due,
                                                             session.get('username'), now()))
        tid = cur.lastrowid
        audit('task_created', 'task', tid, {'title': title}, cur=cur)
    return jsonify({'success': True, 'task_id': tid})


@bp.route('/api/tasks/<int:task_id>/done', methods=['POST'])
@require('documents')
def done(task_id):
    with db() as (_, cur):
        cur.execute("SELECT capability FROM follow_up_tasks WHERE task_id = %s", (task_id,))
        r = cur.fetchall()
        if not r or (r[0]['capability'] or 'documents') not in caps_for():
            return jsonify({'error': 'Not found'}), 404
        cur.execute("UPDATE follow_up_tasks SET status='done', completed_at=%s, completed_by_name=%s WHERE task_id=%s",
                    (now(), session.get('username'), task_id))
        audit('task_completed', 'task', task_id, cur=cur)
    return jsonify({'success': True})


def open_task_summary():
    with db() as (_, cur):
        cur.execute("SELECT capability, due_date FROM follow_up_tasks WHERE status = 'open'")
        rows = visible(cur.fetchall())
    today = date.today()
    return {'open': len(rows), 'overdue': sum(1 for r in rows if to_date(r['due_date']) and to_date(r['due_date']) < today)}
