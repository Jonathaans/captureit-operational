"""PIC closing reports. All writes run inside the caller's database transaction."""
import json
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
EDITABLE = {'draft', 'revision'}
MATERIALS = {'frame': 'Frame', 'lenticular': 'Lensa lenticular', 'magnet': 'Magnet', 'keychain': 'Keychain'}


class ClosingVersionConflict(ValueError):
    pass


def can_review_closing(user, event):
    return 'events.clear' in user['permissions'] and (
        event['coordinator_id'] == user['id'] or 'users.manage' in user['permissions'])


def is_event_pic(conn, event_id, user_id):
    return bool(conn.execute("SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=? AND assignment_type='pic'",
                             (event_id, user_id)).fetchone())


def can_read_closing(conn, event_id, user):
    return 'events.read_all' in user['permissions'] or ('events.read_own' in user['permissions'] and bool(conn.execute(
        'SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=?', (event_id, user['id'])).fetchone()))


def closing_detail(conn, event, user):
    row = conn.execute('SELECT * FROM event_closing_reports WHERE event_id=?', (event['id'],)).fetchone()
    report = dict(row) if row else {'event_id': event['id'], 'status': 'not_started', 'version': 0, 'review_note': ''}
    report['data'] = json.loads(report.pop('data_json', '{}'))
    if not row:
        legacy = conn.execute('SELECT ribbon_start,ribbon_end FROM event_operations WHERE event_id=?', (event['id'],)).fetchone()
        if legacy and (legacy['ribbon_start'] is not None or legacy['ribbon_end'] is not None):
            report['data']['ribbon_rolls'] = [{'start': legacy['ribbon_start'], 'end': legacy['ribbon_end']}]
    report['data'].setdefault('materials', {})
    report['context'] = json.loads(report.pop('context_json', '{}'))
    report['photos'] = [dict(r) for r in conn.execute(
        'SELECT id,kind,caption FROM event_closing_photos WHERE event_id=? ORDER BY id', (event['id'],))]
    for photo in report['photos']:
        photo['url'] = f"/api/closing-photos/{photo['id']}"
    report['history'] = [dict(r) for r in conn.execute('''SELECT h.action,h.note,h.version,h.created_at,u.full_name actor_name
        FROM event_closing_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.event_id=? ORDER BY h.id DESC''', (event['id'],))]
    report['can_edit'] = event['status'] == 'scheduled' and report['status'] in EDITABLE | {'not_started'} and can_read_closing(conn, event['id'], user) and is_event_pic(conn, event['id'], user['id'])
    report['can_review'] = event['status'] == 'scheduled' and report['status'] == 'submitted' and can_review_closing(user, event)
    report['can_clear'] = event['status'] == 'scheduled' and report['status'] == 'accepted' and can_review_closing(user, event)
    return report


def _text(data, key, limit=4000):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'Isian {key} tidak valid atau terlalu panjang (maks. {limit} karakter).')
    return value.strip()


def _number(value, label):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit() or not 0 <= int(value) <= 999999999:
        raise ValueError(f'{label} harus berupa bilangan bulat 0–999999999.')
    return int(value)


def validate_report(data, submitting):
    if not isinstance(data, dict):
        raise ValueError('Isi laporan tidak valid.')
    result = {key: _text(data, key) for key in ('service_note', 'issues', 'resolution', 'follow_up')}
    for key in ('actual_start', 'actual_end'):
        value = _text(data, key, 40)
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
            if stamp and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=WIB)
        except ValueError as exc:
            raise ValueError('Tanggal/jam aktual tidak valid.') from exc
        result[key] = stamp.astimezone(WIB).isoformat() if stamp else ''
    if result['actual_start'] and result['actual_end'] and result['actual_end'] < result['actual_start']:
        raise ValueError('Waktu selesai aktual tidak boleh mendahului waktu mulai.')
    result['service_result'] = _text(data, 'service_result', 20)
    if result['service_result'] not in {'', 'planned', 'changed'}:
        raise ValueError('Pilih kesesuaian layanan.')
    result['printing_mode'] = data.get('printing_mode', 'print')
    if result['printing_mode'] not in {'print', 'digital'}:
        raise ValueError('Jenis pencetakan tidak valid.')
    for key, label in [('prints_total', 'Total cetak'), ('prints_failed', 'Cetak gagal')]:
        result[key] = _number(data.get(key), label)
    materials = data.get('materials', {})
    if not isinstance(materials, dict) or any(key not in MATERIALS for key in materials):
        raise ValueError('Data pemakaian bahan tidak valid.')
    result['materials'] = {key: _number(materials.get(key), label) for key, label in MATERIALS.items()}
    if result['prints_total'] is not None and result['prints_failed'] is not None and result['prints_failed'] > result['prints_total']:
        raise ValueError('Cetak gagal tidak boleh melebihi total cetak.')
    rolls = data.get('ribbon_rolls', [])
    if not isinstance(rolls, list) or len(rolls) > 30:
        raise ValueError('Maksimal 30 roll ribbon per laporan.')
    result['ribbon_rolls'] = []
    for roll in rolls:
        if not isinstance(roll, dict):
            raise ValueError('Rincian ribbon tidak valid.')
        start, end = _number(roll.get('start'), 'Ribbon awal'), _number(roll.get('end'), 'Ribbon akhir')
        if start is None and end is None:
            continue
        if start is not None and end is not None and end > start:
            raise ValueError('Sisa akhir setiap roll tidak boleh melebihi sisa awal.')
        if submitting and (start is None or end is None):
            raise ValueError('Lengkapi sisa awal dan akhir setiap roll ribbon.')
        result['ribbon_rolls'].append({'start': start, 'end': end})
    if result['printing_mode'] == 'digital':
        if any(result[key] not in (None, 0) for key in ('prints_total', 'prints_failed')) or result['ribbon_rolls']:
            raise ValueError('Layanan tanpa cetak harus memiliki cetak nol dan tidak memakai ribbon.')
        result['prints_total'] = result['prints_failed'] = 0
    complete = all(r['start'] is not None and r['end'] is not None for r in result['ribbon_rolls'])
    result['ribbon_used'] = sum(r['start'] - r['end'] for r in result['ribbon_rolls']) if complete and result['ribbon_rolls'] else None
    if submitting:
        if not result['actual_start'] or not result['actual_end'] or not result['service_result']:
            raise ValueError('Lengkapi waktu aktual dan kesesuaian layanan sebelum mengirim laporan.')
        if datetime.fromisoformat(result['actual_end']) > datetime.now(WIB) + timedelta(minutes=5):
            raise ValueError('Laporan hanya dapat dikirim setelah pelaksanaan event selesai.')
        if result['service_result'] == 'changed' and not result['service_note']:
            raise ValueError('Jelaskan perubahan layanan dari rencana.')
        if result['issues'] and not result['resolution']:
            raise ValueError('Isi penanganan kendala, termasuk bila masih menunggu tindak lanjut.')
    return result


def event_context(conn, event):
    from operations import logistics_detail, transport_label
    context = {k: event[k] for k in ('title', 'project_code', 'starts_at', 'ends_at', 'location', 'coordinator_id')}
    context['team'] = [dict(r) for r in conn.execute('''SELECT u.full_name,a.assignment_type FROM event_assignments a
        JOIN users u ON u.id=a.user_id WHERE a.event_id=? ORDER BY a.id''', (event['id'],))]
    logistics = conn.execute('''SELECT o.driver_name,v.name vehicle_name,v.plate_number FROM event_operations o
        LEFT JOIN vehicles v ON v.id=o.vehicle_id WHERE o.event_id=?''', (event['id'],)).fetchone()
    context.update(dict(logistics) if logistics else {})
    context['transport_label'] = transport_label(logistics_detail(conn, event['id']))
    return context


def save_closing(conn, event, user, payload, parse_image):
    row = conn.execute('SELECT * FROM event_closing_reports WHERE event_id=?', (event['id'],)).fetchone()
    previous = dict(row) if row else {'status': 'not_started', 'version': 0}
    action = payload.get('action')
    if action not in {'draft', 'submit', 'accept', 'revise'}:
        raise ValueError('Aksi laporan tidak valid.')
    if event['status'] != 'scheduled':
        raise ValueError('Laporan event yang sudah clear atau dibatalkan tidak dapat diubah.')
    if action in {'draft', 'submit'}:
        if not can_read_closing(conn, event['id'], user):
            raise PermissionError('Akun ini tidak memiliki akses ke laporan event.')
        if not is_event_pic(conn, event['id'], user['id']):
            raise PermissionError('Hanya PIC yang ditugaskan pada event ini dapat mengisi laporan.')
        if payload.get('version') != previous['version']:
            raise ClosingVersionConflict('Laporan telah berubah. Periksa versi terbaru dan draft perangkat sebelum melanjutkan.')
        if previous['status'] not in EDITABLE | {'not_started'}:
            raise ValueError('Laporan sedang direview atau sudah diterima dan tidak dapat diedit.')
    else:
        if not can_review_closing(user, event):
            raise PermissionError('Hanya coordinator event ini atau Administrator yang dapat mereview laporan.')
        if previous['status'] != 'submitted':
            raise ValueError('Hanya laporan yang sudah dikirim yang dapat direview.')
    version = payload.get('version')
    if type(version) is not int or version != previous['version']:
        raise ClosingVersionConflict('Laporan telah berubah. Periksa versi terbaru dan draft perangkat sebelum melanjutkan.')
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    next_version = version + 1
    note = ''
    if action in {'draft', 'submit'}:
        data = validate_report(payload.get('data', {}), action == 'submit')
        photos = payload.get('photos', [])
        if not isinstance(photos, list) or len(photos) > 8:
            raise ValueError('Maksimal 8 foto dokumentasi per event.')
        existing = {r['id'] for r in conn.execute('SELECT id FROM event_closing_photos WHERE event_id=?', (event['id'],))}
        kept, incoming = set(), []
        for photo in photos:
            if not isinstance(photo, dict):
                raise ValueError('Foto dokumentasi tidak valid.')
            if 'id' in photo:
                if type(photo['id']) is not int or photo['id'] not in existing or photo['id'] in kept:
                    raise ValueError('Foto dokumentasi tidak ditemukan pada laporan ini.')
                kept.add(photo['id'])
            else:
                if photo.get('kind') not in {'setup', 'event'}:
                    raise ValueError('Pilih kategori foto Setup atau Event.')
                caption = _text(photo, 'caption', 150)
                content, mime, _ = parse_image(photo.get('image'), 500 * 1024, 'dokumentasi event')
                incoming.append((event['id'], photo['kind'], caption, mime, content, stamp))
        status = 'submitted' if action == 'submit' else ('revision' if previous['status'] == 'revision' else 'draft')
        context = json.dumps(event_context(conn, event), ensure_ascii=False)
        conn.execute('''INSERT INTO event_closing_reports(event_id,status,version,data_json,context_json,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,version=excluded.version,
            data_json=excluded.data_json,context_json=excluded.context_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (event['id'], status, next_version, json.dumps(data, ensure_ascii=False), context, user['id'], stamp))
        for photo_id in existing - kept:
            conn.execute('DELETE FROM event_closing_photos WHERE id=?', (photo_id,))
        conn.executemany('INSERT INTO event_closing_photos(event_id,kind,caption,mime,content,created_at) VALUES(?,?,?,?,?,?)', incoming)
        if action == 'submit':
            conn.execute('''UPDATE event_closing_reports SET submitted_by=?,submitted_at=?,reviewed_by=NULL,
                reviewed_at=NULL,review_note='' WHERE event_id=?''', (user['id'], stamp, event['id']))
    else:
        note = _text(payload, 'note', 2000)
        if action == 'revise' and not note:
            raise ValueError('Tuliskan bagian yang perlu direvisi oleh PIC.')
        status = 'accepted' if action == 'accept' else 'revision'
        conn.execute('''UPDATE event_closing_reports SET status=?,version=?,review_note=?,reviewed_by=?,reviewed_at=?,
            updated_by=?,updated_at=? WHERE event_id=?''', (status, next_version, note, user['id'], stamp, user['id'], stamp, event['id']))
    conn.execute('INSERT INTO event_closing_history(event_id,action,actor_id,note,version,created_at) VALUES(?,?,?,?,?,?)',
                 (event['id'], action, user['id'], note, next_version, stamp))
    return {'ok': True, 'status': status, 'version': next_version}


def approved_export(conn, event_id):
    row = conn.execute("SELECT data_json FROM event_closing_reports WHERE event_id=? AND status='accepted'", (event_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row['data_json'])
    rolls = data.get('ribbon_rolls', [])
    # Keep the 21-column template: sums preserve Total = Awal - Akhir across rolls.
    start = sum(r['start'] for r in rolls) if rolls else None
    end = sum(r['end'] for r in rolls) if rolls else None
    notes = ['Penutupan diterima.']
    if data.get('prints_total') is not None:
        notes.append(f"Total cetak: {data['prints_total']}.")
    if data.get('prints_failed') is not None:
        notes.append(f"Cetak gagal: {data['prints_failed']}.")
    if rolls:
        notes.append(f'Ribbon: {len(rolls)} roll; pemakaian {start-end}.')
    for key, label in MATERIALS.items():
        quantity = data.get('materials', {}).get(key)
        if quantity is not None:
            notes.append(f'{label}: {quantity} pcs.')
    for key, label in [('service_note', 'Layanan'), ('issues', 'Kendala'), ('resolution', 'Penanganan'), ('follow_up', 'Tindak lanjut')]:
        if data.get(key):
            notes.append(f'{label}: {data[key]}')
    if len(rolls) > 1:
        notes.append('Ribbon: ' + '; '.join(f"roll {i}: {r['start']} → {r['end']}" for i, r in enumerate(rolls, 1)))
    return start, end, start - end if rolls else None, '\n'.join(notes)
