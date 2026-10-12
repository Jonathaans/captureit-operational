"""Manual operational hours, independent from imported Calendar dates."""
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def moment(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Tanggal dan jam event tidak valid.') from exc
    return result.replace(tzinfo=WIB) if result.tzinfo is None else result.astimezone(WIB)


def manual_bounds(payload):
    starts, ends = moment(payload.get('starts_at')), moment(payload.get('ends_at'))
    if ends <= starts or ends - starts > timedelta(days=14):
        raise ValueError('Jam selesai harus setelah jam mulai, maksimal 14 hari.')
    return starts.isoformat(timespec='seconds'), ends.isoformat(timespec='seconds')


def calendar_dates(starts_at, ends_at):
    starts, ends = moment(starts_at), moment(ends_at)
    first = starts.replace(hour=0, minute=0, second=0, microsecond=0)
    last = ends.replace(hour=0, minute=0, second=0, microsecond=0)
    if ends > last or last <= first:
        last += timedelta(days=1)
    return first.isoformat(), last.isoformat()


def promoted_schedule(intake, payload):
    if payload.get('starts_at') or payload.get('ends_at'):
        starts, ends = manual_bounds(payload)
        return starts, ends, 0
    starts, ends = calendar_dates(intake['starts_at'], intake['ends_at'])
    return starts, ends, 1


def migrate(conn):
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(events)')}
    new = 'schedule_manual' not in columns
    for name, definition in {'schedule_manual':'INTEGER NOT NULL DEFAULT 0', 'schedule_pending':'INTEGER NOT NULL DEFAULT 0',
                             'calendar_start_date':"TEXT NOT NULL DEFAULT ''", 'calendar_end_date':"TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns: conn.execute(f'ALTER TABLE events ADD COLUMN {name} {definition}')
    if new:
        conn.execute('UPDATE events SET schedule_manual=1 WHERE google_event_id IS NOT NULL')


def assignment_has_attendance(conn, assignment_id):
    for table in ('attendance', 'attendance_days'):
        if conn.execute(f"""SELECT 1 FROM {table} WHERE assignment_id=? AND
            (status!='not_started' OR check_in_at IS NOT NULL OR check_out_at IS NOT NULL
             OR check_in_photo_path IS NOT NULL OR check_out_photo_path IS NOT NULL) LIMIT 1""", (assignment_id,)).fetchone():
            return True
    return bool(conn.execute("SELECT 1 FROM audit_logs WHERE entity_type IN ('attendance','attendance_day','event_attendance') AND entity_id=? LIMIT 1", (assignment_id,)).fetchone())


def event_has_attendance(conn, event_id):
    return any(assignment_has_attendance(conn, row['id']) for row in conn.execute('SELECT id FROM event_assignments WHERE event_id=?', (event_id,)))
