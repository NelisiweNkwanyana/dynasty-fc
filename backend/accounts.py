"""
Dynasty FC — accounts and password security.

* Passwords are stored as salted PBKDF2-SHA256 hashes (600 000 iterations).
  Old unsalted SHA-256 hashes still work and are upgraded automatically the
  next time that person logs in, so nobody is locked out.
* Failed and successful staff logins are written to the audit log.
* Everyone can change their own password; administrators can create staff
  logins, change roles, deactivate accounts and set temporary passwords.
"""
import hashlib
import hmac
import re
import secrets

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from core import audit, can, clean_rows, db, now, require

bp = Blueprint('accounts', __name__)

ROLES = ['System Administrator', 'Club Administrator', 'Coach', 'Player']
WEAK_DEFAULTS = {'admin123', 'password', '12345678', 'dynasty123', 'admin'}
_strong_ok = {'value': True}  # becomes False if the column cannot hold a modern hash


def init_accounts(app):
    app.register_blueprint(bp)


def ensure_hash_column():
    """Modern hashes are ~100 characters. Widen users.password_hash on MySQL if it is shorter."""
    try:
        with db() as (_, cur):
            cur.execute("SELECT CHARACTER_MAXIMUM_LENGTH AS n FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'password_hash'")
            rows = cur.fetchall()
        if rows and rows[0]['n'] and int(rows[0]['n']) < 200:
            try:
                with db() as (_, cur):
                    cur.execute("ALTER TABLE users MODIFY password_hash VARCHAR(255) NOT NULL")
                print('[accounts] widened users.password_hash to VARCHAR(255)')
            except Exception as e:
                _strong_ok['value'] = False
                print(f'[accounts] could not widen password_hash ({e}); keeping legacy hashes for now')
    except Exception:
        pass  # not MySQL (local demo) – nothing to do


def legacy_sha256(password):
    return hashlib.sha256(password.encode()).hexdigest()


def hash_password(password):
    if _strong_ok['value']:
        return generate_password_hash(password, method='pbkdf2:sha256:600000')
    return legacy_sha256(password)


def is_modern(hashed):
    return bool(hashed) and (hashed.startswith('pbkdf2:') or hashed.startswith('scrypt:'))


def verify_password(password, hashed):
    if not hashed or password is None:
        return False
    if is_modern(hashed):
        try:
            return check_password_hash(hashed, password)
        except Exception:
            return False
    return hmac.compare_digest(legacy_sha256(password), str(hashed))


def after_login(user, password):
    """Upgrade an old hash and remember whether the password is weak."""
    if _strong_ok['value'] and not is_modern(user.get('password_hash')):
        try:
            with db() as (_, cur):
                cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hash_password(password), user['user_id']))
        except Exception as e:
            print(f'[accounts] hash upgrade skipped: {e}')
    session['weak_password'] = is_weak(password, user.get('email'))
    audit('login', 'user', user['user_id'], {'role': user.get('role')})


def login_failed(email):
    audit('login_failed', 'user', (email or '')[:60])


def is_weak(password, email=None):
    p = password or ''
    return (p.lower() in WEAK_DEFAULTS or len(p) < 8 or not re.search(r'[A-Za-z]', p) or not re.search(r'\d', p)
            or (email and p.lower() == str(email).split('@')[0].lower()))


def policy_problem(password, email=None):
    if len(password or '') < 8:
        return 'Use at least 8 characters'
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return 'Use letters and at least one number'
    if password.lower() in WEAK_DEFAULTS:
        return 'That password is too common'
    if email and password.lower() == str(email).split('@')[0].lower():
        return 'Do not use your email name as the password'
    return None


# ================================================================== routes
@bp.route('/api/me/security')
def my_security():
    if 'user_id' not in session:
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'weak_password': bool(session.get('weak_password')), 'role': session.get('role'),
                    'username': session.get('username')})


@bp.route('/api/me/password', methods=['POST'])
def change_my_password():
    if 'user_id' not in session:
        return jsonify({'error': 'Please log in'}), 401
    data = request.get_json(silent=True) or {}
    cur_pw, new_pw = data.get('current_password') or '', data.get('new_password') or ''
    with db() as (_, cur):
        cur.execute("SELECT user_id, email, password_hash FROM users WHERE user_id = %s", (session['user_id'],))
        rows = cur.fetchall()
    if not rows or not verify_password(cur_pw, rows[0]['password_hash']):
        audit('password_change_failed', 'user', session['user_id'])
        return jsonify({'error': 'Your current password is not correct'}), 400
    problem = policy_problem(new_pw, rows[0]['email'])
    if problem:
        return jsonify({'error': problem}), 400
    if new_pw == cur_pw:
        return jsonify({'error': 'Choose a different password from the current one'}), 400
    with db() as (_, cur):
        cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hash_password(new_pw), session['user_id']))
        audit('password_changed', 'user', session['user_id'], cur=cur)
    session['weak_password'] = False
    return jsonify({'success': True})


@bp.route('/api/admin/users')
@require('admin')
def list_users():
    with db() as (_, cur):
        cur.execute("SELECT user_id, username, email, role, is_active FROM users ORDER BY role, username")
        rows = clean_rows(cur.fetchall())
    return jsonify({'users': rows, 'roles': ROLES, 'me': session.get('user_id')})


@bp.route('/api/admin/users', methods=['POST'])
@require('admin')
def create_user():
    data = request.get_json(silent=True) or {}
    name = (data.get('username') or '').strip()[:100]
    email = (data.get('email') or '').strip().lower()[:150]
    role = data.get('role')
    if not name or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) or role not in ROLES:
        return jsonify({'error': 'Name, a valid email and a role are required'}), 400
    temp = data.get('password') or ('Dfc' + secrets.token_urlsafe(6) + '7')
    problem = policy_problem(temp, email)
    if problem:
        return jsonify({'error': problem}), 400
    with db() as (_, cur):
        cur.execute("SELECT user_id FROM users WHERE LOWER(email) = %s", (email,))
        if cur.fetchall():
            return jsonify({'error': 'An account with that email already exists'}), 409
        cur.execute("INSERT INTO users (username, password_hash, email, role, is_active) VALUES (%s, %s, %s, %s, 1)",
                    (name, hash_password(temp), email, role))
        uid = cur.lastrowid
        audit('user_created', 'user', uid, {'email': email, 'role': role}, cur=cur)
    return jsonify({'success': True, 'user_id': uid, 'temporary_password': None if data.get('password') else temp})


@bp.route('/api/admin/users/<int:uid>', methods=['PUT'])
@require('admin')
def update_user(uid):
    data = request.get_json(silent=True) or {}
    with db() as (_, cur):
        cur.execute("SELECT user_id, role, is_active FROM users WHERE user_id = %s", (uid,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'Not found'}), 404
        u = rows[0]
        role = data.get('role', u['role'])
        active = 1 if data.get('is_active', u['is_active']) in (1, True, '1', 'true') else 0
        if role not in ROLES:
            return jsonify({'error': 'Unknown role'}), 400
        if uid == session.get('user_id') and (role != u['role'] or not active):
            return jsonify({'error': 'You cannot change your own role or deactivate yourself'}), 400
        if u['role'] == 'System Administrator' and (role != 'System Administrator' or not active):
            cur.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'System Administrator' AND is_active = 1")
            if cur.fetchall()[0]['n'] <= 1:
                return jsonify({'error': 'There must always be at least one active System Administrator'}), 400
        cur.execute("UPDATE users SET role = %s, is_active = %s WHERE user_id = %s", (role, active, uid))
        audit('user_updated', 'user', uid, {'role': role, 'active': active}, cur=cur)
    return jsonify({'success': True})


@bp.route('/api/admin/users/<int:uid>/reset-password', methods=['POST'])
@require('admin')
def reset_password(uid):
    temp = 'Dfc' + secrets.token_urlsafe(6) + '7'
    with db() as (_, cur):
        cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hash_password(temp), uid))
        audit('password_reset_by_admin', 'user', uid, cur=cur)
    return jsonify({'success': True, 'temporary_password': temp})
