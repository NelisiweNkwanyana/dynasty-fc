"""
Dynasty FC — online donations through PayFast (South Africa).

The donate page already existed but had no backend, so the button failed.
This module adds it:

  GET  /donate                 the donate page
  POST /api/donate/start       records a *pending* donation and returns the signed
                               fields the page posts to PayFast
  POST /api/payfast/notify     PayFast's server-to-server ITN callback. The donation
                               is only marked paid after the signature, the amount
                               and (unless switched off) PayFast's own validate
                               endpoint all agree. Paid donations then flow into the
                               finance ledger automatically.
  GET  /payment/success, /payment/cancel   the existing thank-you / cancelled pages

Settings (environment variables):
  PAYFAST_MERCHANT_ID, PAYFAST_MERCHANT_KEY, PAYFAST_PASSPHRASE
  PAYFAST_SANDBOX=true            use sandbox.payfast.co.za while testing
  SITE_URL=https://dynasty-fc-webdev.onrender.com   (used for return/notify URLs)
  PAYFAST_VALIDATE=false          skip the call back to PayFast (local testing only)
Without a merchant id/key the page says online payment isn't set up – it never
pretends a payment went through.
"""
import hashlib
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

from flask import Blueprint, jsonify, render_template, request

from core import audit, db, emit, notify

bp = Blueprint('payments', __name__)

FIELD_ORDER = ['merchant_id', 'merchant_key', 'return_url', 'cancel_url', 'notify_url', 'name_first', 'name_last',
               'email_address', 'cell_number', 'm_payment_id', 'amount', 'item_name', 'item_description',
               'custom_str1']


def init_payments(app):
    app.register_blueprint(bp)


def _cfg():
    sandbox = os.getenv('PAYFAST_SANDBOX', 'false').lower() in ('1', 'true', 'yes')
    return {
        'merchant_id': os.getenv('PAYFAST_MERCHANT_ID', '').strip(),
        'merchant_key': os.getenv('PAYFAST_MERCHANT_KEY', '').strip(),
        'passphrase': os.getenv('PAYFAST_PASSPHRASE', '').strip(),
        'host': 'sandbox.payfast.co.za' if sandbox else 'www.payfast.co.za',
        'site': (os.getenv('SITE_URL') or request.host_url).rstrip('/'),
        'validate': os.getenv('PAYFAST_VALIDATE', 'true').lower() not in ('0', 'false', 'no'),
    }


def _enc(v):
    return quote_plus(str(v).strip()).replace('~', '%7E')  # same as PHP urlencode, which PayFast uses


def signature(pairs, passphrase=''):
    """PayFast signature: MD5 of the url-encoded, non-empty fields in the given order (+ passphrase)."""
    s = '&'.join(f"{k}={_enc(v)}" for k, v in pairs if str(v).strip() != '')
    if passphrase:
        s += f"&passphrase={_enc(passphrase)}"
    return hashlib.md5(s.encode()).hexdigest()


def _money(v):
    try:
        d = Decimal(str(v)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d


# ------------------------------------------------------------------ pages
@bp.route('/donate')
def donate_page():
    return render_template('donate.html')


@bp.route('/payment/success')
def payment_success():
    return render_template('payment_success.html')


@bp.route('/payment/cancel')
def payment_cancel():
    return render_template('payment_cancel.html')


# ------------------------------------------------------------------ API
@bp.route('/api/donate/start', methods=['POST'])
def start_donation():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()[:100]
    email = (data.get('email') or '').strip()[:150]
    amount = _money(data.get('amount'))
    if not name or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return jsonify({'success': False, 'error': 'Please give your name and a valid email address'}), 400
    if amount is None or amount < 10 or amount > 100000:
        return jsonify({'success': False, 'error': 'Donations must be between R10 and R100 000'}), 400
    cfg = _cfg()
    if not cfg['merchant_id'] or not cfg['merchant_key']:
        return jsonify({'success': False, 'error': 'Online card payments are not switched on yet. Please contact the club to donate by EFT.'}), 503
    ref = 'DFC-' + uuid.uuid4().hex[:10].upper()
    with db() as (_, cur):
        cur.execute("""INSERT INTO donations (donor_name, donor_email, donor_phone, amount, message, is_anonymous, payment_status, payment_ref)
                       VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)""",
                    (name, email, (data.get('phone') or '').strip()[:30] or None, float(amount),
                     (data.get('message') or '').strip()[:1000] or None, 1 if data.get('is_anonymous') else 0, ref))
    first, _, last = name.partition(' ')
    fields = {
        'merchant_id': cfg['merchant_id'], 'merchant_key': cfg['merchant_key'],
        'return_url': cfg['site'] + '/payment/success', 'cancel_url': cfg['site'] + '/payment/cancel',
        'notify_url': cfg['site'] + '/api/payfast/notify',
        'name_first': first[:100], 'name_last': last[:100], 'email_address': email,
        'm_payment_id': ref, 'amount': f'{amount:.2f}', 'item_name': 'Donation to Dynasty FC',
    }
    pairs = [(k, fields[k]) for k in FIELD_ORDER if k in fields]
    fields['signature'] = signature(pairs, cfg['passphrase'])
    return jsonify({'success': True, 'reference': ref, 'payfast_url': f"https://{cfg['host']}/eng/process", 'payfast_data': fields})


def _validate_with_payfast(host, body):
    try:
        import requests
        r = requests.post(f'https://{host}/eng/query/validate', data=body,
                          headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10)
        return r.text.strip() == 'VALID'
    except Exception as e:  # network trouble: refuse rather than accept blindly
        print(f'[payfast] validate call failed: {e}')
        return False


@bp.route('/api/payfast/notify', methods=['POST'])
def payfast_notify():
    cfg = _cfg()
    form = request.form
    pairs = [(k, v) for k, v in form.items(multi=False) if k != 'signature']
    if not form.get('signature') or signature(pairs, cfg['passphrase']) != form.get('signature'):
        audit('payfast_rejected', 'donation', form.get('m_payment_id', '')[:60], {'reason': 'bad signature'})
        return 'bad signature', 400
    ref = form.get('m_payment_id', '')
    with db() as (_, cur):
        cur.execute("SELECT donation_id, amount, payment_status, donor_name FROM donations WHERE payment_ref = %s", (ref,))
        rows = cur.fetchall()
    if not rows:
        return 'unknown payment', 404
    d = rows[0]
    if abs(Decimal(str(d['amount'])) - (_money(form.get('amount_gross')) or Decimal('-1'))) > Decimal('0.01'):
        audit('payfast_rejected', 'donation', d['donation_id'], {'reason': 'amount mismatch', 'got': form.get('amount_gross')})
        return 'amount mismatch', 400
    if cfg['validate'] and not _validate_with_payfast(cfg['host'], request.get_data(as_text=True)):
        audit('payfast_rejected', 'donation', d['donation_id'], {'reason': 'PayFast did not confirm'})
        return 'not confirmed', 400
    status = (form.get('payment_status') or '').upper()
    if str(d['payment_status']).lower() == 'paid':
        return 'OK'  # PayFast retries; already handled
    new = 'paid' if status == 'COMPLETE' else ('cancelled' if status == 'CANCELLED' else 'failed')
    with db() as (_, cur):
        cur.execute("UPDATE donations SET payment_status = %s, paid_at = %s WHERE donation_id = %s",
                    (new, datetime.now() if new == 'paid' else None, d['donation_id']))
        audit('donation_' + new, 'donation', d['donation_id'], {'pf_payment_id': form.get('pf_payment_id'), 'amount': form.get('amount_gross')}, cur=cur)
    if new == 'paid':
        try:
            from finance import sync_donations
            sync_donations()
        except Exception as e:
            print(f'[payfast] ledger sync later: {e}')
        notify('donation', f"New online donation: R{Decimal(str(d['amount'])):.2f}", 'Paid through PayFast and added to finance for approval.',
               '/dashboard#finance', 'finance')
        emit('donation.paid', donation_id=d['donation_id'])
    return 'OK'
