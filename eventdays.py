"""Per-day crew mapping for events that span more than one WIB calendar day.

An assignment of a multi-day event owns explicit rows in event_assignment_days.
Assignments without rows (single-day events, and assignments created before this
feature) cover every day of the event, so older data keeps working.
Attendance for multi-day assignments lives in attendance_days; the legacy
attendance row is kept as a roll-up so payroll, claims and notifications still
work at assignment level.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def _local(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=WIB)).astimezone(WIB)


def event_dates(event) -> list[str]:
    """WIB dates the event occupies. The end instant is exclusive: 00:00 does not add a day."""
    start, end = _local(event['starts_at']), _local(event['ends_at'])
    if end <= start:
        return [start.date().isoformat()]
    day, last = start.date(), (end - timedelta(microseconds=1)).date()
    days = []
    while day <= last:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def is_multiday(event) -> bool:
    """True for events longer than 24 hours that touch more than one WIB date.

    Overnight events (for example 20:00-04:00) stay ordinary single events.
    """
    start, end = _local(event['starts_at']), _local(event['ends_at'])
    return end - start > timedelta(hours=24) and len(event_dates(event)) > 1


def today_wib() -> str:
    return datetime.now(WIB).date().isoformat()


def assignment_days(conn, event, assignment_id: int) -> list[str]:
    all_days = event_dates(event)
    rows = [r[0] for r in conn.execute(
        'SELECT work_date FROM event_assignment_days WHERE assignment_id=? ORDER BY work_date', (assignment_id,))]
    return [d for d in rows if d in all_days] if rows else all_days


def user_event_days(conn, event, user_id: int) -> set[str]:
    """Days a user is occupied on `event`, over all of their assignments to it."""
    days: set[str] = set()
    for row in conn.execute('SELECT id FROM event_assignments WHERE event_id=? AND user_id=?', (event['id'], user_id)):
        days.update(assignment_days(conn, event, row['id']))
    return days


def normalize_days(event, requested) -> list[str]:
    """Validate a requested day list against the event; None/empty means every day."""
    valid = event_dates(event)
    if requested in (None, '', []):
        return valid
    if not isinstance(requested, (list, tuple)):
        raise ValueError('Pilih hari kerja yang valid.')
    chosen = sorted({str(d) for d in requested})
    if any(d not in valid for d in chosen):
        raise ValueError('Hari kerja harus berada dalam tanggal event.')
    if not chosen:
        raise ValueError('Pilih minimal satu hari kerja.')
    return chosen


def save_days(conn, event, assignment_id: int, days: list[str]) -> None:
    """Persist explicit day coverage (multi-day events only)."""
    if not is_multiday(event):
        return
    conn.execute('DELETE FROM event_assignment_days WHERE assignment_id=?', (assignment_id,))
    conn.executemany('INSERT INTO event_assignment_days(assignment_id,work_date) VALUES(?,?)',
                     [(assignment_id, d) for d in days])
    refresh_rollup(conn, event, assignment_id)


def day_attendance(conn, event, assignment_id: int) -> list[dict]:
    """Per-day attendance for a multi-day assignment ([] for single-day events)."""
    if not is_multiday(event):
        return []
    days = assignment_days(conn, event, assignment_id)
    rows = {r['work_date']: r for r in conn.execute('SELECT * FROM attendance_days WHERE assignment_id=?', (assignment_id,))}
    result = []
    for day in days:
        r = rows.get(day)
        result.append({
            'work_date': day, 'status': r['status'] if r else 'not_started',
            'check_in_at': r['check_in_at'] if r else None, 'check_out_at': r['check_out_at'] if r else None,
            'check_in_latitude': r['check_in_latitude'] if r else None, 'check_in_longitude': r['check_in_longitude'] if r else None,
            'check_in_accuracy_m': r['check_in_accuracy_m'] if r else None,
            'check_out_latitude': r['check_out_latitude'] if r else None, 'check_out_longitude': r['check_out_longitude'] if r else None,
            'check_out_accuracy_m': r['check_out_accuracy_m'] if r else None,
            'has_check_in_photo': bool(r and r['check_in_photo_path']), 'has_check_out_photo': bool(r and r['check_out_photo_path']),
            'corrected': bool(r and r['corrected_at']), 'note': (r['note'] if r and r['corrected_at'] else None),
            # A day that has passed without a finished record needs a coordinator decision.
            'needs_action': day < today_wib() and (not r or r['status'] in ('not_started', 'checked_in')),
        })
    return result


def refresh_rollup(conn, event, assignment_id: int) -> None:
    """Keep the assignment-level attendance row consistent with per-day records."""
    days = assignment_days(conn, event, assignment_id)
    rows = {r['work_date']: r for r in conn.execute('SELECT * FROM attendance_days WHERE assignment_id=?', (assignment_id,))}
    mine = [rows[d] for d in days if d in rows]
    done = [r for r in mine if r['status'] == 'checked_out']
    open_ = [r for r in mine if r['status'] == 'checked_in']
    resolved = len(mine) == len(days) and all(r['status'] in ('checked_out', 'absent') for r in mine)
    if open_ or (done and not resolved):
        status = 'checked_in'
    elif resolved and done:
        status = 'checked_out'
    elif resolved and mine:
        status = 'absent'
    else:
        status = 'not_started'
    ins = [r['check_in_at'] for r in mine if r['check_in_at']]
    outs = [r['check_out_at'] for r in done if r['check_out_at']]
    conn.execute('UPDATE attendance SET status=?,check_in_at=?,check_out_at=? WHERE assignment_id=?',
                 (status, min(ins) if ins else None, max(outs) if status == 'checked_out' and outs else None, assignment_id))


def worked_days(conn, assignment_id: int) -> int:
    """Paid days: checked-out days of a multi-day assignment, otherwise 1."""
    total = conn.execute('SELECT COUNT(*) FROM attendance_days WHERE assignment_id=?', (assignment_id,)).fetchone()[0]
    if not total:
        return 1
    return max(1, conn.execute("SELECT COUNT(*) FROM attendance_days WHERE assignment_id=? AND status='checked_out'",
                               (assignment_id,)).fetchone()[0])
