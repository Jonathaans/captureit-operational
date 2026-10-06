"""FCM adapter and retryable SQLite outbox. Firebase is optional until enabled."""
import importlib.util
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import notification_center as notices

SDK_VERSION = '12.19.0'
_firebase_app = None
_firebase_lock = threading.Lock()


def config():
    origin = os.environ.get('OPS_PUBLIC_ORIGIN', '').rstrip('/')
    parsed = urlparse(origin)
    web = {key: os.environ.get(env, '').strip() for key, env in {
        'apiKey': 'FIREBASE_WEB_API_KEY', 'projectId': 'FIREBASE_PROJECT_ID',
        'messagingSenderId': 'FIREBASE_MESSAGING_SENDER_ID', 'appId': 'FIREBASE_WEB_APP_ID'}.items()}
    vapid = os.environ.get('FIREBASE_WEB_VAPID_KEY', '').strip()
    enabled = os.environ.get('PUSH_ENABLED', 'false').lower() in {'true', '1', 'yes'}
    https = bool(parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password
                 and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
    credential = os.environ.get('FIREBASE_SERVICE_ACCOUNT_FILE', '')
    ready = bool(enabled and https and all(web.values()) and vapid and credential and Path(credential).is_file()
                 and importlib.util.find_spec('firebase_admin'))
    return {'enabled': enabled, 'ready': ready, 'firebase': web, 'vapid_key': vapid, 'origin': origin if https else '',
            'sdk_version': SDK_VERSION, 'message': 'Konfigurasi push tersedia. Aktifkan dan tes pada perangkat ini.' if ready else
            'Push perangkat belum diaktifkan oleh pengelola. Notifikasi tetap tersedia di aplikasi.'}


def send_fcm(device, notification):
    global _firebase_app
    import firebase_admin
    from firebase_admin import credentials, messaging
    with _firebase_lock:
        if _firebase_app is None:
            _firebase_app = firebase_admin.initialize_app(
                credentials.Certificate(os.environ['FIREBASE_SERVICE_ACCOUNT_FILE']),
                {'projectId': os.environ['FIREBASE_PROJECT_ID'], 'httpTimeout': 15}, name='captureit-push')
    # No salary, bank reference, brief, event name, or other private content enters FCM.
    data = {'notice': notification['public_id'], 'url': config()['origin']+'/?notice='+notification['public_id']}
    kwargs = {'token': device['token'], 'data': data}
    ttl = max(0, min(172800, int((datetime.fromisoformat(notification['expires_at'])-datetime.now(timezone.utc)).total_seconds())))
    if device['platform'] == 'web':
        # Data-only: the service worker checks the active account before showing anything.
        kwargs['webpush'] = messaging.WebpushConfig(headers={'TTL': str(ttl), 'Urgency': 'high' if notification['urgent'] else 'normal'})
    else:
        kwargs['notification'] = messaging.Notification(title='Capture It Ops', body='Ada pembaruan untuk akun Anda. Buka aplikasi untuk melihatnya.')
        kwargs['android'] = messaging.AndroidConfig(priority='high' if notification['urgent'] else 'normal',
            ttl=timedelta(seconds=ttl), notification=messaging.AndroidNotification(tag=notification['public_id'], channel_id='captureit_updates'))
    return messaging.send(messaging.Message(**kwargs), app=_firebase_app)


def eligible(conn, device, n, user_loader, live_loader, now):
    user = user_loader(conn, device['user_id'])
    session = conn.execute('SELECT user_id,expires_at FROM sessions WHERE token_hash=?', (device['session_hash'],)).fetchone()
    if not device['active'] or not user or device['user_id'] != n['user_id'] or not session or session['user_id'] != device['user_id'] or datetime.fromisoformat(session['expires_at']) <= now:
        return 'device_or_session_inactive', None
    if datetime.fromisoformat(n['expires_at']) <= now or n['read_at']:
        return 'expired_or_read', None
    live = {item['key'] for item in live_loader(conn, user)} if n['live_key'] else None
    if not notices.allowed(conn, user, n, live):
        return 'access_or_task_changed', None
    prefs = notices.preferences(conn, n['user_id'])
    if n['kind']!='push_test' and not prefs['categories'].get(n['category'], False):
        return 'preference_disabled', None
    return None, notices.quiet_until(prefs, now) if not n['urgent'] else None


def process_once(get_db, user_loader, live_loader, sender=None, now=None, limit=20):
    now = now or datetime.now(timezone.utc)
    current = now.isoformat(timespec='microseconds')
    with get_db() as conn:
        conn.execute("UPDATE push_deliveries SET status='cancelled',last_error='expired' WHERE status IN ('pending','sending') AND notification_id IN (SELECT id FROM notifications WHERE expires_at<=?)", (current,))
    if sender is None and not config()['ready']:
        return 0
    sender = sender or send_fcm
    handled = 0
    for _ in range(limit):
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = conn.execute("""SELECT * FROM push_deliveries WHERE (status='pending' AND next_attempt_at<=?)
                OR (status='sending' AND lease_until<=?) ORDER BY id LIMIT 1""", (current, current)).fetchone()
            if not job:
                break
            job = dict(job)
            n = dict(conn.execute('SELECT * FROM notifications WHERE id=?', (job['notification_id'],)).fetchone())
            device = dict(conn.execute('SELECT * FROM push_devices WHERE id=?', (job['device_id'],)).fetchone())
            error, deferred = eligible(conn, device, n, user_loader, live_loader, now)
            if error:
                conn.execute("UPDATE push_deliveries SET status='cancelled',last_error=? WHERE id=?", (error, job['id']))
                continue
            if deferred:
                conn.execute("UPDATE push_deliveries SET status='pending',next_attempt_at=?,lease_until=NULL WHERE id=?", (deferred, job['id']))
                continue
            lease = (now+timedelta(minutes=5)).isoformat(timespec='microseconds')
            conn.execute("UPDATE push_deliveries SET status='sending',lease_until=?,attempts=attempts+1 WHERE id=?", (lease, job['id']))
        try:
            provider_id = sender(device, n)
            outcome, code = 'sent', None
        except Exception as exc:
            # Never persist/log provider messages: they can include credentials or tokens.
            code = type(exc).__name__
            outcome = 'failed' if code in {'UnregisteredError', 'SenderIdMismatchError', 'InvalidArgumentError'} or job['attempts']+1>=6 else 'pending'
            provider_id = None
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''UPDATE push_deliveries SET status=?,provider_id=?,last_error=?,lease_until=NULL,next_attempt_at=?
                WHERE id=? AND status='sending' AND lease_until=?''', (outcome, str(provider_id)[:250] if provider_id else None,
                code, (now+timedelta(seconds=min(3600, 60*(2**min(job['attempts'], 6))))).isoformat(timespec='microseconds'), job['id'], lease))
            if code == 'UnregisteredError':
                # Only disable the registration we actually attempted, never a newly rebound device.
                conn.execute('UPDATE push_devices SET active=0 WHERE id=? AND session_hash=? AND token_hash=?',
                    (device['id'], device['session_hash'], device['token_hash']))
        handled += 1
    return handled
