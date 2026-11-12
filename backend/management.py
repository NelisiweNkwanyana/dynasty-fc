"""
Dynasty FC — edit / delete routes that were missing from the core management
system (fixtures, staff records, inventory), with validation. Editing a
fixture re-runs its prediction; correcting a score re-grades it.
"""
import re
from datetime import date

from flask import Blueprint, jsonify, request

from core import audit, columns, db, emit, require, to_date

bp = Blueprint('management', __name__)


def init_management(app):
    app.register_blueprint(bp)


def _text(v, n):
    return (str(v).strip()[:n] if v is not None else '') or None


@bp.route('/api/team-matches/<int:match_id>', methods=['PUT'])
@require('fixtures')
def edit_fixture(match_id):
    data = request.get_json(silent=True) or {}
    with db() as (_, cur):
        cur.execute("SELECT * FROM team_matches WHERE match_id = %s", (match_id,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'Fixture not found'}), 404
        m = rows[0]
        upd = {}
        for k, n in (('home_team', 120), ('away_team', 120), ('venue', 150), ('team_group', 60), ('age_group', 20)):
            if k in data:
                upd[k] = _text(data[k], n)
        if 'match_date' in data:
            d = to_date(data['match_date'])
            if not d:
                return jsonify({'error': 'Match date is not valid'}), 400
            upd['match_date'] = d
        if 'match_time' in data:
            t = str(data['match_time'] or '').strip()
            if t and not re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?', t):
                return jsonify({'error': 'Time must look like 10:00'}), 400
            upd['match_time'] = t or None
        home, away = upd.get('home_team', m['home_team']), upd.get('away_team', m['away_team'])
        if not home or not away:
            return jsonify({'error': 'Both teams are required'}), 400
        if home.lower() == away.lower():
            return jsonify({'error': 'A team cannot play itself'}), 400
        score_changed = False
        if m.get('is_completed') and ('home_score' in data or 'away_score' in data):
            try:
                hs, as_ = int(data.get('home_score', m['home_score'])), int(data.get('away_score', m['away_score']))
            except (TypeError, ValueError):
                return jsonify({'error': 'Scores must be whole numbers'}), 400
            if hs < 0 or as_ < 0 or hs > 50 or as_ > 50:
                return jsonify({'error': 'Scores must be between 0 and 50'}), 400
            score_changed = (hs, as_) != (m['home_score'], m['away_score'])
            upd['home_score'], upd['away_score'] = hs, as_
        valid = columns(cur, 'team_matches')
        upd = {k: v for k, v in upd.items() if k in valid}
        if upd:
            cur.execute(f"UPDATE team_matches SET {', '.join(k + ' = %s' for k in upd)} WHERE match_id = %s", [*upd.values(), match_id])
            audit('fixture_edited', 'match', match_id, {k: str(v) for k, v in upd.items()}, cur=cur)
    if score_changed:
        emit('match.result_recorded', match_id=match_id)
    elif not m.get('is_completed'):
        emit('match.created', match_id=match_id)
    return jsonify({'success': True})


@bp.route('/api/staff/<int:staff_id>', methods=['PUT'])
@require('staff')
def edit_staff(staff_id):
    data = request.get_json(silent=True) or {}
    upd = {}
    for k, n in (('first_name', 80), ('last_name', 80), ('role', 80), ('employment_status', 40), ('contact_phone', 30)):
        if k in data:
            upd[k] = _text(data[k], n)
    if 'contact_email' in data:
        e = _text(data['contact_email'], 150)
        if e and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', e):
            return jsonify({'error': 'Email address is not valid'}), 400
        upd['contact_email'] = e
    if ('first_name' in upd and not upd['first_name']) or ('last_name' in upd and not upd['last_name']) or ('role' in upd and not upd['role']):
        return jsonify({'error': 'First name, last name and role are required'}), 400
    with db() as (_, cur):
        valid = columns(cur, 'staff')
        upd = {k: v for k, v in upd.items() if k in valid}
        if not upd:
            return jsonify({'error': 'Nothing to update'}), 400
        cur.execute(f"UPDATE staff SET {', '.join(k + ' = %s' for k in upd)} WHERE staff_id = %s", [*upd.values(), staff_id])
        if cur.rowcount == 0:
            return jsonify({'error': 'Staff member not found'}), 404
        audit('staff_edited', 'staff', staff_id, list(upd), cur=cur)
    return jsonify({'success': True})


CONDITIONS = ('New', 'Good', 'Worn', 'Damaged')


@bp.route('/api/inventory/<int:item_id>', methods=['PUT'])
@require('staff')
def edit_item(item_id):
    data = request.get_json(silent=True) or {}
    upd = {}
    for k, n in (('item_name', 120), ('category', 80), ('storage_location', 120)):
        if k in data:
            upd[k] = _text(data[k], n)
    if 'quantity' in data:
        try:
            q = int(data['quantity'])
        except (TypeError, ValueError):
            return jsonify({'error': 'Quantity must be a whole number'}), 400
        if q < 0:
            return jsonify({'error': 'Quantity cannot be negative'}), 400
        upd['quantity'] = q
    if 'item_condition' in data:
        if data['item_condition'] not in CONDITIONS:
            return jsonify({'error': 'Condition must be one of ' + ', '.join(CONDITIONS)}), 400
        upd['item_condition'] = data['item_condition']
    if ('item_name' in upd and not upd['item_name']) or ('category' in upd and not upd['category']):
        return jsonify({'error': 'Item name and category are required'}), 400
    with db() as (_, cur):
        valid = columns(cur, 'inventory_items')
        id_col = 'item_id' if 'item_id' in valid else ('inventory_id' if 'inventory_id' in valid else None)
        if not id_col:
            return jsonify({'error': 'Inventory table has no id column'}), 500
        upd = {k: v for k, v in upd.items() if k in valid}
        if not upd:
            return jsonify({'error': 'Nothing to update'}), 400
        cur.execute(f"UPDATE inventory_items SET {', '.join(k + ' = %s' for k in upd)} WHERE {id_col} = %s", [*upd.values(), item_id])
        if cur.rowcount == 0:
            return jsonify({'error': 'Item not found'}), 404
        audit('inventory_edited', 'inventory', item_id, list(upd), cur=cur)
    return jsonify({'success': True})


@bp.route('/api/inventory/<int:item_id>', methods=['DELETE'])
@require('staff')
def delete_item(item_id):
    with db() as (_, cur):
        valid = columns(cur, 'inventory_items')
        id_col = 'item_id' if 'item_id' in valid else 'inventory_id'
        cur.execute(f"DELETE FROM inventory_items WHERE {id_col} = %s", (item_id,))
        if cur.rowcount == 0:
            return jsonify({'error': 'Item not found'}), 404
        audit('inventory_deleted', 'inventory', item_id, cur=cur)
    return jsonify({'success': True})
