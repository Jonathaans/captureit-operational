"""Per-employee daily rosters and immutable attendance schedule snapshots (WIB)."""
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
SNAPSHOT_COLUMNS = {
    "shift_name": "TEXT", "scheduled_end_at": "TEXT",
    "late_grace_minutes": "INTEGER", "early_grace_minutes": "INTEGER",
}


def migrate(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(inhouse_attendance)")}
    for name, kind in SNAPSHOT_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE inhouse_attendance ADD COLUMN {name} {kind}")


def bounds(work_date, schedule):
    start = datetime.fromisoformat(f"{work_date}T{schedule['work_start']}:00").replace(tzinfo=WIB)
    end = datetime.fromisoformat(f"{work_date}T{schedule['work_end']}:00").replace(tzinfo=WIB)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def schedule_for(conn, user_id, work_date, default):
    row = conn.execute("SELECT * FROM inhouse_shifts WHERE user_id=? AND work_date=?", (user_id, work_date)).fetchone()
    result = {**default, "shift_name": "Reguler", "work_date": work_date, "custom_shift": False}
    if row:
        result.update({key: row[key] for key in (*default, "shift_name")})
        result["custom_shift"] = True
    start, end = bounds(work_date, result)
    result.update(scheduled_start_at=start.isoformat(), scheduled_end_at=end.isoformat(), overnight=end.date() > start.date())
    return result


def record_schedule(record, fallback):
    """Legacy rows retain their known start; new records freeze the whole schedule."""
    record = dict(record)
    result = dict(fallback)
    for target, column in (("work_start", "scheduled_start"), ("work_end", "scheduled_end"),
                           ("shift_name", "shift_name"), ("late_grace_minutes", "late_grace_minutes"),
                           ("early_grace_minutes", "early_grace_minutes")):
        if record.get(column) is not None:
            result[target] = record[column]
    start, end = bounds(record["work_date"], result)
    result.update(work_date=record["work_date"], scheduled_start_at=start.isoformat(),
                  scheduled_end_at=record.get("scheduled_end_at") or end.isoformat(), overnight=end.date() > start.date())
    return result


def open_record(conn, user_id):
    return conn.execute("""SELECT * FROM inhouse_attendance WHERE user_id=? AND status='checked_in'
        ORDER BY work_date DESC, id DESC LIMIT 1""", (user_id,)).fetchone()


def checkin_options(conn, user_id, now, default):
    today = now.date().isoformat()
    options = [schedule_for(conn, user_id, today, default)]
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    previous = schedule_for(conn, user_id, yesterday, default)
    # A night shift may start late, after midnight, but never after its scheduled end.
    if previous["overnight"] and now < datetime.fromisoformat(previous["scheduled_end_at"]):
        options.insert(0, previous)
    return [s for s in options if not conn.execute("""SELECT 1 FROM inhouse_attendance
        WHERE user_id=? AND work_date=? AND status!='not_started'""", (user_id, s["work_date"])).fetchone()]


def public_record(row):
    result = dict(row)
    for kind in ("check_in", "check_out"):
        result[f"has_{kind}_photo"] = bool(result.pop(f"{kind}_photo_path", None))
    return result
