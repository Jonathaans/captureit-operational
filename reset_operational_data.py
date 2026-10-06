#!/usr/bin/env python3
"""Back up the SQLite database, then clear operational rows while retaining accounts and configuration."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TABLES_TO_CLEAR = (
    "push_deliveries",
    "push_devices",
    "notifications",
    "notification_reads",
    "payroll_assignment_claims",
    "payroll_lines",
    "payroll_batches",
    "inhouse_monthly_salary_claims",
    "inhouse_payroll_payouts",
    "inhouse_payroll_batches",
    "inhouse_attendance",
    "kpi_reviews",
    "audit_logs",
    "sessions",
    "calendar_code_queue",
    "events",
)
TABLES_TO_KEEP = (
    "notification_preferences",
    "vehicles",
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "positions",
    "fee_rates",
    "user_fee_rates",
    "compensation_history",
    "inhouse_salary_rates",
    "skills",
    "schema_migrations",
)


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def main() -> int:
    load_dotenv()
    configured = Path(os.environ.get("OPS_DB_PATH", str(ROOT / "ops.sqlite3")))
    db_path = configured if configured.is_absolute() else (Path.cwd() / configured).resolve()
    if not db_path.is_file():
        print(f"Database tidak ditemukan: {db_path}")
        print("Jalankan aplikasi setidaknya sekali atau periksa OPS_DB_PATH di .env.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.backup-{stamp}{db_path.suffix}")

    conn = sqlite3.connect(db_path, timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(f"Database: {db_path}")
        print("Data operasional yang akan dikosongkan:")
        for table in TABLES_TO_CLEAR:
            print(f"  {table}: {table_count(conn, table)} baris")
        print("Data akun, ACL, tarif per akun, posisi, dan master skill akan dipertahankan.")
        confirmation = input("Ketik RESET DATA OPERASIONAL untuk lanjut: ").strip()
        if confirmation != "RESET DATA OPERASIONAL":
            print("Dibatalkan; database tidak diubah.")
            return 0

        backup = sqlite3.connect(backup_path)
        try:
            conn.backup(backup)
        finally:
            backup.close()
        print(f"Backup dibuat: {backup_path}")

        before = {table: table_count(conn, table) for table in TABLES_TO_CLEAR}
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table in TABLES_TO_CLEAR:
                conn.execute(f'DELETE FROM "{table}"')
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"Foreign key check gagal: {violations[:5]}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        print("Reset selesai. Baris yang dihapus:")
        for table in TABLES_TO_CLEAR:
            print(f"  {table}: {before[table]}")
        print("Akun dan konfigurasi tersimpan:")
        for table in TABLES_TO_KEEP:
            print(f"  {table}: {table_count(conn, table)} baris")
        print("Semua sesi login dihapus; pengguna perlu masuk kembali.")
        return 0
    except (sqlite3.Error, OSError) as exc:
        print(f"Reset gagal: {exc}")
        print("Jika backup sudah dibuat, salinan tersebut tetap tersedia.")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
