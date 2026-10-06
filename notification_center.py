"""Private, durable inbox and transactional push outbox. No network calls here."""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
CATEGORIES = {'payroll': 'Slip gaji', 'schedule': 'Penjadwalan', 'design': 'Desain',
              'operations': 'Operasional', 'reminders': 'Pengingat'}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def preferences(conn, uid):
    row = conn.execute('SELECT * FROM notification_preferences WHERE user_id=?', (uid,)).fetchone()
    return {'categories': json.loads(row['categories']) if row else {key: True for key in CATEGORIES},
            'quiet_start': row['quiet_start'] if row else '', 'quiet_end': row['quiet_end'] if row else ''}


def save_preferences(conn, uid, data):
    categories = data.get('categories')
    if not isinstance(categories, dict) or set(categories) != set(CATEGORIES) or any(type(v) is not bool for v in categories.values()):
        raise ValueError('Pilih pengaturan setiap kategori notifikasi.')
    start, end = data.get('quiet_start', ''), data.get('quiet_end', '')
    if not all(isinstance(v, str) and (not v or re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', v)) for v in (start, end)):
        raise ValueError('Jam tenang tidak valid.')
    if bool(start) != bool(end) or (start and start == end):
        raise ValueError('Isi awal dan akhir jam tenang yang berbeda, atau kosongkan keduanya.')
    conn.execute('''INSERT INTO notification_preferences(user_id,categories,quiet_start,quiet_end) VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET categories=excluded.categories,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end''',
        (uid, json.dumps(categories), start, end))
    # Opting out also cancels already queued deliveries. Inbox records remain.
    for category, enabled in categories.items():
        if not enabled:
            conn.execute("UPDATE push_deliveries SET status='cancelled',last_error='preference_disabled' WHERE status IN ('pending','sending') AND notification_id IN (SELECT id FROM notifications WHERE user_id=? AND category=?)", (uid, category))
    return preferences(conn, uid)


def quiet_until(prefs, now):
    """Quiet hours defer ordinary pushes, in WIB. Urgent schedule changes bypass them."""
    start, end = prefs['quiet_start'], prefs['quiet_end']
    if not start or not end:
        return None
    local = now.astimezone(WIB)
    clock = local.strftime('%H:%M')
    inside = start <= clock < end if start < end else clock >= start or clock < end
    if not inside:
        return None
    hour, minute = map(int, end.split(':'))
    until = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if until <= local:
        until += timedelta(days=1)
    return until.astimezone(timezone.utc).isoformat(timespec='microseconds')


def can_event(conn, user, event_id):
    p = set(user['permissions'])
    return bool('events.read_all' in p or ('events.read_own' in p and conn.execute(
        'SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=?', (event_id, user['id'])).fetchone()))


def allowed(conn, user, row, live_keys=None):
    if row['user_id'] != user['id'] or not user.get('active', 1):
        return False
    if row['live_key'] and live_keys is not None and row['live_key'] not in live_keys:
        return False
    p = set(user['permissions'])
    if row['required_permission'] and row['required_permission'] not in p:
        return False
    if row['target_kind'] == 'payslip':
        if row['category'] != 'payroll':
            return False
        if row['required_permission'] == 'inhouse_payroll.read_own':
            return user.get('employment_type') == 'inhouse' and bool(conn.execute(
                "SELECT 1 FROM inhouse_payroll_payouts WHERE id=? AND user_id=? AND status='transferred'", (row['target_id'], user['id'])).fetchone())
        return user.get('employment_type') == 'freelancer' and bool(conn.execute('''SELECT 1 FROM payroll_batch_transfers t
            JOIN payroll_lines l ON l.batch_id=t.batch_id JOIN event_assignments a ON a.id=l.assignment_id
            WHERE t.batch_id=? AND a.user_id=?''', (row['target_id'], user['id'])).fetchone())
    if row['kind'] in {'sync_failed', 'sync_recovered'}:
        return True   # system notice for people who may run Calendar sync; the permission check above already applied
    if row['kind'] == 'assignment_removed':
        return bool(p & {'events.read_own', 'events.read_all'})
    if row['event_id']:
        return can_event(conn, user, row['event_id'])
    return row['kind'] == 'push_test'


def emit(conn, recipients, *, key, category, kind, title, body, event=None, tab='overview',
         target_kind='event', target_id=None, permission='', urgent=False, live_key='', push=True):
    event = dict(event) if event else {}
    created = stamp()
    result = []
    for uid in sorted(set(recipients)):
        if not conn.execute('SELECT 1 FROM users WHERE id=? AND active=1', (uid,)).fetchone():
            continue
        cur = conn.execute('''INSERT OR IGNORE INTO notifications(public_id,user_id,dedupe_key,category,kind,title,body,
            event_id,event_title,event_at,tab,target_kind,target_id,required_permission,urgent,live_key,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (secrets.token_hex(16), uid, key, category, kind, title, body,
            event.get('id'), event.get('title', ''), event.get('starts_at', ''), tab, target_kind, target_id,
            permission, int(urgent), live_key, created, (datetime.now(timezone.utc)+timedelta(days=2)).isoformat(timespec='microseconds')))
        if not cur.rowcount:
            continue
        nid = cur.lastrowid
        result.append(nid)
        if push and (kind=='push_test' or preferences(conn, uid)['categories'].get(category, False)):
            conn.execute('''INSERT INTO push_deliveries(notification_id,device_id,next_attempt_at)
                SELECT ?,id,? FROM push_devices WHERE user_id=? AND active=1''', (nid, created, uid))
    return result


def item(row):
    return {'key': 'notice:'+row['public_id'], 'id': row['id'], 'title': row['title'], 'body': row['body'],
            'event_id': row['event_id'], 'event_title': row['event_title'], 'event_at': row['event_at'] or row['created_at'],
            'created_at': row['created_at'], 'tab': row['tab'], 'category': row['category'],
            'target_kind': row['target_kind'], 'target_id': row['target_id'],
            'tone': 'red' if row['urgent'] else 'blue', 'read': bool(row['read_at'])}


def inbox(conn, user, live_items, before=None):
    live = {n['key']: n for n in live_items}
    rows = [dict(r) for r in conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC', (user['id'],))]
    visible = [r for r in rows if allowed(conn, user, r, live)]
    materialized = {r['live_key'] for r in visible if r['live_key']}
    new_assignments = {r['event_id'] for r in visible if r['kind'] == 'assignment'}
    changed_events = {r['event_id'] for r in visible if r['kind'] == 'event_cancelled'}
    legacy = [v for k, v in live.items() if k not in materialized and not (
        ':assignment:' in k and v['event_id'] in new_assignments or ':cancelled:' in k and v['event_id'] in changed_events)]
    selected = [r for r in visible if not before or r['id'] < before]
    page = selected[:100]
    return {'items': [item(r) for r in page] + ([] if before else legacy),
            'unread_count': sum(not r['read_at'] for r in visible)+sum(not n['read'] for n in legacy),
            'next_before': page[-1]['id'] if len(selected)>100 else None}


def sync_live(conn, user, live_items, push=True):
    """Keep actionable reminders durable; do not republish obsolete assignments."""
    for n in live_items:
        kind = n['key'].split(':')[2]
        if kind in {'assignment', 'cancelled'}:
            continue
        category = 'reminders' if kind in {'team', 'transport', 'warehouse', 'design', 'evaluation', 'event-tomorrow'} else 'operations'
        ids = emit(conn, [user['id']], key=n['key'], category=category, kind=kind,
                   title=n['title'], body=n['body'], event={'id': n['event_id'], 'title': n['event_title'], 'starts_at': n['event_at']},
                   tab=n['tab'], live_key=n['key'], urgent=n['tone']=='red', push=push)
        if n['read'] and ids:
            conn.execute('UPDATE notifications SET read_at=? WHERE id=?', (stamp(), ids[0]))


def mark_read(conn, user, keys, live_items, all_items=False):
    live = {n['key']: n for n in live_items}
    if not all_items and (not isinstance(keys, list) or len(keys)>2000 or any(not isinstance(k, str) or len(k)>200 for k in keys)):
        raise ValueError('Daftar notifikasi tidak valid.')
    rows = [dict(r) for r in conn.execute('SELECT * FROM notifications WHERE user_id=?', (user['id'],))]
    visible = {'notice:'+r['public_id']: r for r in rows if allowed(conn, user, r, live)}
    selected = list(visible)+list(live) if all_items else keys
    if any(k not in visible and k not in live for k in selected):
        raise ValueError('Notifikasi tidak tersedia untuk akun ini.')
    now = stamp()
    for key in selected:
        if key in visible:
            conn.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?', (now, visible[key]['id']))
        else:
            conn.execute('INSERT OR IGNORE INTO notification_reads(user_id,notification_key,read_at) VALUES(?,?,?)', (user['id'], key, now))


def assignment(conn, event, uid, assignment_id, removed=False, kind='crew'):
    return emit(conn, [uid], key=f'assignment:{assignment_id}:{"removed" if removed else "new"}', category='schedule',
                kind='assignment_removed' if removed else 'assignment', event=event, tab='team',
                target_kind='inbox' if removed else 'event', urgent=removed,
                title='Penugasan dibatalkan' if removed else 'Penugasan event baru',
                body='Anda tidak lagi ditugaskan pada event ini. Hubungi coordinator jika perlu konfirmasi.' if removed else
                'Anda ditugaskan sebagai '+('PIC' if kind=='pic' else 'Crew')+'. Buka jadwal dan konfirmasi melalui WhatsApp.')


def event_changed(conn, before, after):
    before, after = dict(before), dict(after)
    fields = {'starts_at': 'jam mulai', 'ends_at': 'jam selesai', 'location': 'lokasi', 'title': 'nama event', 'status': 'status'}
    changed = [name for key, name in fields.items() if before.get(key) != after.get(key)]
    if not changed:
        return
    recipients = [r[0] for r in conn.execute('SELECT DISTINCT user_id FROM event_assignments WHERE event_id=?', (after['id'],))]
    if after.get('coordinator_id'):
        recipients.append(after['coordinator_id'])
    cancelled = after['status'] == 'cancelled'
    emit(conn, recipients, key='event-change:'+secrets.token_hex(16), category='schedule', kind='event_cancelled' if cancelled else 'event_changed',
         title='Event dibatalkan' if cancelled else 'Jadwal event diperbarui',
         body='Periksa kembali jadwal Anda.' if cancelled else 'Perubahan '+', '.join(changed)+'. Buka rincian terbaru sebelum berangkat.',
         event=after, urgent=True)


def device_register(conn, user, data):
    token, device_key = data.get('token'), data.get('device_key')
    if not isinstance(token, str) or not 20 <= len(token) <= 4096 or not re.fullmatch(r'[A-Za-z0-9_:\-.]+', token):
        raise ValueError('Token push perangkat tidak valid.')
    if not isinstance(device_key, str) or not re.fullmatch(r'[a-f0-9]{64}', device_key):
        raise ValueError('Identitas perangkat tidak valid.')
    platform, label = data.get('platform', 'web'), data.get('label', 'Perangkat saya')
    if platform not in {'web', 'android'} or not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError('Informasi perangkat tidak valid.')
    digest = hashlib.sha256(token.encode()).hexdigest()
    old = conn.execute('SELECT * FROM push_devices WHERE token_hash=? OR device_key=?', (digest, device_key)).fetchall()
    for row in old:
        if row['user_id'] != user['id'] or row['session_hash'] != user['session_hash'] or row['token_hash'] != digest:
            conn.execute("UPDATE push_deliveries SET status='cancelled',last_error='device_rebound' WHERE device_id=? AND status IN ('pending','sending')", (row['id'],))
        conn.execute('UPDATE push_devices SET active=0 WHERE id=?', (row['id'],))
    row = conn.execute('SELECT id FROM push_devices WHERE token_hash=?', (digest,)).fetchone()
    if not row and conn.execute('SELECT COUNT(*) FROM push_devices WHERE user_id=? AND active=1', (user['id'],)).fetchone()[0]>=10:
        raise ValueError('Maksimal 10 perangkat aktif. Nonaktifkan salah satu perangkat dahulu.')
    now = stamp()
    conn.execute('''INSERT INTO push_devices(user_id,session_hash,token,token_hash,device_key,platform,label,active,updated_at)
        VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(token_hash) DO UPDATE SET user_id=excluded.user_id,session_hash=excluded.session_hash,
        token=excluded.token,device_key=excluded.device_key,platform=excluded.platform,label=excluded.label,active=1,updated_at=excluded.updated_at''',
        (user['id'], user['session_hash'], token, digest, device_key, platform, label.strip(), now))
    return conn.execute('SELECT id FROM push_devices WHERE token_hash=?', (digest,)).fetchone()[0]


def revoke_devices(conn, uid, session_hash=None, device_id=None):
    where, args = 'user_id=?', [uid]
    if session_hash:
        where += ' AND session_hash=?'; args.append(session_hash)
    if device_id is not None:
        where += ' AND id=?'; args.append(device_id)
    conn.execute(f"UPDATE push_deliveries SET status='cancelled',last_error='device_revoked' WHERE status IN ('pending','sending') AND device_id IN (SELECT id FROM push_devices WHERE {where})", args)
    conn.execute(f'UPDATE push_devices SET active=0 WHERE {where}', args)


def device_list(conn, user):
    return [{**{k: r[k] for k in ('id','label','platform','active','updated_at')}, 'this_session': r['session_hash']==user['session_hash']} for r in conn.execute(
        'SELECT id,label,platform,active,updated_at,session_hash FROM push_devices WHERE user_id=? AND active=1 ORDER BY updated_at DESC', (user['id'],))]


def sync_status(conn, ok, message, previous_status):
    """Tell administrators when Calendar sync starts failing, and once when it works again.

    At most one failure notice per day, so a 24h scheduler never floods the inbox.
    """
    today = datetime.now(WIB).date().isoformat()
    admins = [r[0] for r in conn.execute("""SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id
        JOIN roles r ON r.id=ur.role_id WHERE u.active=1 AND r.code='administrator'""")]
    if not admins:
        return []
    if not ok:
        return emit(conn, admins, key=f'sync_failed:{today}', category='operations', kind='sync_failed',
                    title='Sinkronisasi Google Calendar gagal', target_kind='inbox', permission='google.sync', urgent=True,
                    body=(str(message)[:400] + ' Buka Jadwal Event, perbaiki penyebabnya, lalu klik Sync Calendar.').strip())
    if previous_status == 'error':
        return emit(conn, admins, key=f'sync_recovered:{today}:{stamp()}', category='operations', kind='sync_recovered',
                    title='Sinkronisasi Google Calendar pulih', target_kind='inbox', permission='google.sync',
                    body='Sinkronisasi terakhir berhasil. Event Calendar sudah diperbarui.')
    return []
