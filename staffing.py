"""Advisory Crew/PIC schedule warnings. Loading and travel buffers are excluded."""
from __future__ import annotations

import hashlib
import json
from eventdays import event_dates, is_multiday, user_event_days
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def local_time(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=WIB)).astimezone(WIB)


def staff_window(conn, event):
    start, end = local_time(event['starts_at']), local_time(event['ends_at'])
    if end <= start:
        raise ValueError('Jam selesai event harus sesudah jam mulai untuk memeriksa jadwal tim.')
    return start, end


def staff_availability(conn, event, user_id, buffer=None, days=None):
    """Every shared WIB day warns; even overlaps can be acknowledged and saved.

    `buffer` is accepted for old internal callers but has no scheduling effect.
    Endpoints are half-open: midnight does not occupy the next day.
    """
    start, end = staff_window(conn, event)
    conflicts = []
    others = conn.execute('''SELECT e.*,GROUP_CONCAT(DISTINCT a.assignment_type) AS roles
        FROM event_assignments a JOIN events e ON e.id=a.event_id
        LEFT JOIN attendance att ON att.assignment_id=a.id
        WHERE a.user_id=? AND e.id!=? AND e.status!='cancelled'
          AND COALESCE(att.status,'not_started')!='absent'
        GROUP BY e.id ORDER BY e.starts_at,e.id''', (user_id, event['id'])).fetchall()
    my_dates = event_dates(event)
    mine = set(days) if days is not None else set(my_dates)
    for other in others:
        other_start, other_end = staff_window(conn, other)
        multi = is_multiday(event) or is_multiday(other)
        if multi:
            # Multi-day events only collide on days the person actually works on both.
            shared = sorted(mine & user_event_days(conn, other, user_id))
            if not shared:
                continue
            overlap = False
            gap = None
            extra = {'shared_days': shared}
        else:
            overlap = start < other_end and other_start < end
            first_day = max(start.date(), other_start.date())
            last_day = min((end - timedelta(microseconds=1)).date(), (other_end - timedelta(microseconds=1)).date())
            if not overlap and first_day > last_day:
                continue
            gap = round((start - other_end if other_end <= start else other_start - end).total_seconds() / 60, 2)
            extra = {}
        conflicts.append({'kind': 'overlap' if overlap else 'same_day', 'event_id': other['id'],
            'title': other['title'], 'project_code': other['project_code'], 'location': other['location'],
            'starts_at': other_start.isoformat(), 'ends_at': other_end.isoformat(),
            'roles': sorted(other['roles'].split(',')), 'gap_minutes': None if overlap else gap, **extra})
    conflicts.sort(key=lambda c: (c['starts_at'], c['event_id']))
    snapshot = {'policy': 'event-day-v2', 'event_id': event['id'], 'user_id': user_id,
                'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
                'location': event['location'], 'conflicts': conflicts}
    if days is not None:
        snapshot['days'] = sorted(days)
    signature = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    severity = 'red' if any(c['kind'] == 'overlap' for c in conflicts) else 'yellow' if conflicts else 'green'
    return {**snapshot, 'signature': signature, 'blocked': False,
            'needs_confirmation': bool(conflicts), 'severity': severity}


def event_staff_conflicts(conn, event, viewer):
    if event['status'] == 'cancelled':
        return []
    manager = 'events.assign' in viewer['permissions']
    assignments = conn.execute('''SELECT DISTINCT a.user_id,u.full_name
        FROM event_assignments a JOIN users u ON u.id=a.user_id
        LEFT JOIN attendance att ON att.assignment_id=a.id
        WHERE a.event_id=? AND COALESCE(att.status,'not_started')!='absent' ''', (event['id'],)).fetchall()
    result = []
    for person in assignments:
        if not manager and person['user_id'] != viewer['id']:
            continue
        available = staff_availability(conn, event, person['user_id'],
                                       days=sorted(user_event_days(conn, event, person['user_id'])) if is_multiday(event) else None)
        if available['conflicts']:
            result.append({'user_id': person['user_id'], 'full_name': person['full_name'],
                           'severity': available['severity'], 'conflicts': available['conflicts']})
    return result
