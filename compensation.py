"""Append-only compensation changes. Call all writes inside a caller transaction."""
from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def effective_date(value=None):
    if value is None or value == '':
        value = datetime.now(WIB).date().isoformat()
    if not isinstance(value, str):
        raise ValueError('Tanggal berlaku tidak valid.')
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise ValueError('Gunakan tanggal berlaku YYYY-MM-DD yang valid.') from exc
    return value


def change_reason(value=''):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('Alasan perubahan maksimal 1000 karakter.')
    return value.strip()


def migrate_history(conn):
    """Import only known balances, without fabricating missing changes/dates."""
    version = 'compensation_history_v1'
    if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (version,)).fetchone():
        return
    for kind, table, amount, stamp in (
        ('fee', 'user_fee_rates', 'base_fee_rupiah', 'created_at'),
        ('salary', 'inhouse_salary_rates', 'monthly_salary_rupiah', 'updated_at'),
    ):
        for row in conn.execute(f'SELECT * FROM {table} ORDER BY user_id'):
            actor = conn.execute('SELECT full_name FROM users WHERE id=?', (row['updated_by'],)).fetchone()
            conn.execute('''INSERT INTO compensation_history(user_id,kind,old_amount_rupiah,new_amount_rupiah,
                effective_from,reason,created_by,actor_name,created_at,source)
                VALUES(?,?,NULL,?,?,?,?,?,?,'migration')''',
                (row['user_id'], kind, row[amount], row['effective_from'] if kind == 'fee' else None,
                 'Saldo awal migrasi; riwayat koreksi sebelumnya tidak tersedia.', row['updated_by'],
                 actor['full_name'] if actor else '', row[stamp]))
    conn.execute('INSERT INTO schema_migrations(version) VALUES(?)', (version,))


def salary_at(conn, user_id, on_date):
    """Resolve by period start. Unknown-date migrated balances preserve legacy behavior."""
    row = conn.execute('''SELECT new_amount_rupiah FROM compensation_history
        WHERE user_id=? AND kind='salary' AND (effective_from<=? OR effective_from IS NULL)
        ORDER BY effective_from DESC,id DESC LIMIT 1''', (user_id, on_date)).fetchone()
    return row['new_amount_rupiah'] if row else 0


def append_change(conn, user_id, kind, amount, effective_from, reason, actor):
    if kind not in {'fee', 'salary'}:
        raise ValueError('Jenis tarif tidak valid.')
    if kind == 'fee':
        previous = conn.execute('''SELECT base_fee_rupiah,effective_from FROM user_fee_rates
            WHERE user_id=? AND effective_from<=? ORDER BY effective_from DESC LIMIT 1''', (user_id, effective_from)).fetchone()
        old_amount = previous['base_fee_rupiah'] if previous else None
    else:
        previous = conn.execute('''SELECT new_amount_rupiah,effective_from FROM compensation_history
            WHERE user_id=? AND kind='salary' AND (effective_from<=? OR effective_from IS NULL)
            ORDER BY effective_from DESC,id DESC LIMIT 1''', (user_id, effective_from)).fetchone()
        old_amount = previous['new_amount_rupiah'] if previous else None
    if previous and previous['effective_from'] == effective_from and old_amount == amount:
        return None
    if kind == 'fee':
        conn.execute('''INSERT INTO user_fee_rates(user_id,base_fee_rupiah,effective_from,updated_by) VALUES(?,?,?,?)
            ON CONFLICT(user_id,effective_from) DO UPDATE SET base_fee_rupiah=excluded.base_fee_rupiah,
            updated_by=excluded.updated_by,created_at=CURRENT_TIMESTAMP''', (user_id, amount, effective_from, actor['id']))
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    history_id = conn.execute('''INSERT INTO compensation_history(user_id,kind,old_amount_rupiah,new_amount_rupiah,
        effective_from,reason,created_by,actor_name,created_at,source) VALUES(?,?,?,?,?,?,?,?,?,'change')''',
        (user_id, kind, old_amount, amount, effective_from, reason, actor['id'], actor['full_name'], stamp)).lastrowid
    if kind == 'salary':
        current = salary_at(conn, user_id, datetime.now(WIB).date().isoformat())
        conn.execute('''INSERT INTO inhouse_salary_rates(user_id,monthly_salary_rupiah,updated_by,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET monthly_salary_rupiah=excluded.monthly_salary_rupiah,
            updated_by=excluded.updated_by,updated_at=excluded.updated_at''', (user_id, current, actor['id'], stamp))
    return history_id


def history_payload(conn, user_id, kind, before=None):
    person = conn.execute('SELECT id,full_name FROM users WHERE id=?', (user_id,)).fetchone()
    if not person:
        raise ValueError('Akun tidak ditemukan.')
    rows = [dict(r) for r in conn.execute('''SELECT id,old_amount_rupiah,new_amount_rupiah,effective_from,
        reason,created_by,actor_name,created_at,source FROM compensation_history
        WHERE user_id=? AND kind=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 101''',
        (user_id, kind, before, before))]
    has_more = len(rows) > 100
    rows = rows[:100]
    for row in rows:
        row['delta_rupiah'] = None if row['old_amount_rupiah'] is None else row['new_amount_rupiah'] - row['old_amount_rupiah']
        row['superseded'] = bool(conn.execute('''SELECT 1 FROM compensation_history
            WHERE user_id=? AND kind=? AND effective_from IS ? AND id>? LIMIT 1''',
            (user_id, kind, row['effective_from'], row['id'])).fetchone())
    today = datetime.now(WIB).date().isoformat()
    current = conn.execute('''SELECT new_amount_rupiah,effective_from FROM compensation_history
        WHERE user_id=? AND kind=? AND (effective_from<=? OR effective_from IS NULL)
        ORDER BY effective_from DESC,id DESC LIMIT 1''', (user_id, kind, today)).fetchone()
    return {'user': dict(person), 'kind': kind, 'today': today, 'current': dict(current) if current else None,
            'rows': rows, 'next_cursor': rows[-1]['id'] if has_more else None}
