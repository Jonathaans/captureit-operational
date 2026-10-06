#!/usr/bin/env python3
"""Backup, verify and restore for Capture It Ops.

    python backup.py                 make a backup now (safe while the server is running)
    python backup.py list            show existing backups
    python backup.py verify [FILE]   check a backup can be restored (newest one if FILE is omitted)
    python backup.py restore FILE --yes   restore a backup (STOP THE SERVER FIRST)

One backup is one .zip containing a consistent copy of the SQLite database (taken with SQLite's own
backup API, so it is valid even while people are using the app), the private photo/document folders,
and a manifest with checksums and row counts.

Settings (environment or .env):
    OPS_DB_PATH          database file (default: ops.sqlite3 next to this script)
    BACKUP_DIR           where backups go (default: ./backups)  -> put this on ANOTHER disk if you can
    BACKUP_COPY_DIR      optional second destination, e.g. a Google Drive / external-drive folder
    BACKUP_KEEP_DAYS     delete backups older than this many days (default 14; the newest 3 are always kept)
    *_STORAGE_DIR        the same folder overrides the server uses for photos and documents
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIB = timezone(timedelta(hours=7))
PREFIX = "ops-backup-"
KEEP_AT_LEAST = 3
# (folder name inside the zip, environment override, default folder name next to the database)
STORAGE = [
    ("attendance-photos", "ATTENDANCE_STORAGE_DIR", "attendance-photos"),
    ("inhouse-attendance-photos", "INHOUSE_ATTENDANCE_STORAGE_DIR", "inhouse-attendance-photos"),
    ("cash-advance-documents", "CASH_ADVANCE_STORAGE_DIR", "cash-advance-documents"),
    ("private-profile-uploads", "PROFILE_STORAGE_DIR", "private-profile-uploads"),
    ("branding-assets", "BRANDING_STORAGE_DIR", "branding-assets"),
]
CHECK_TABLES = ("users", "events", "event_assignments", "attendance", "payroll_batches", "audit_logs")


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def db_path() -> Path:
    return Path(os.environ.get("OPS_DB_PATH") or ROOT / "ops.sqlite3")


def backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR") or ROOT / "backups")


def storage_dirs() -> list[tuple[str, Path]]:
    base = db_path().parent
    return [(name, Path(os.environ.get(env, "").strip() or base / default)) for name, env, default in STORAGE]


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP_DAYS", "14")))
    except ValueError:
        return 14


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_counts(db_file: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    conn = sqlite3.connect(str(db_file))
    try:
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in CHECK_TABLES:
            if table in existing:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    return counts


def snapshot_database(target: Path) -> None:
    """Consistent copy of the live database using SQLite's online backup API."""
    source = sqlite3.connect(db_path(), timeout=30)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def make_backup() -> Path:
    if not db_path().exists():
        raise SystemExit(f"Database tidak ditemukan: {db_path()}")
    out_dir = backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(WIB).strftime("%Y%m%d-%H%M%S")
    final = out_dir / f"{PREFIX}{stamp}.zip"
    partial = out_dir / f".{PREFIX}{stamp}.partial"
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "ops.sqlite3"
        snapshot_database(snapshot)
        check = sqlite3.connect(snapshot)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok":
            raise SystemExit(f"Database gagal pemeriksaan integritas ({integrity}). Backup dibatalkan.")
        manifest = {"created_at": datetime.now(WIB).isoformat(timespec="seconds"), "database": str(db_path().name),
                    "database_sha256": sha256(snapshot), "tables": table_counts(snapshot), "folders": {}}
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "ops.sqlite3")
            for name, folder in storage_dirs():
                files = [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []
                manifest["folders"][name] = len(files)
                for path in files:
                    archive.write(path, f"files/{name}/{path.relative_to(folder).as_posix()}")
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    partial.replace(final)   # a half-written backup never carries the final name
    try:
        verify_backup(final, quiet=True)
    except SystemExit:
        final.unlink(missing_ok=True)   # never leave a backup that cannot be restored
        raise
    copy_dir = os.environ.get("BACKUP_COPY_DIR", "").strip()
    if copy_dir:
        Path(copy_dir).mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, Path(copy_dir) / final.name)
    prune(out_dir)
    if copy_dir:
        prune(Path(copy_dir))
    return final


def backups(directory: Path | None = None) -> list[Path]:
    folder = directory or backup_dir()
    return sorted(folder.glob(f"{PREFIX}*.zip")) if folder.is_dir() else []


def prune(directory: Path) -> list[Path]:
    """Delete backups older than BACKUP_KEEP_DAYS, but never the newest few."""
    files = backups(directory)
    cutoff = time.time() - keep_days() * 86400
    removed = []
    for path in files[:-KEEP_AT_LEAST] if len(files) > KEEP_AT_LEAST else []:
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def verify_backup(archive_path: Path, quiet: bool = False) -> dict:
    """Open the backup the way a restore would and prove it is usable. Any damage ends in a clear message."""
    try:
        return _verify_backup(archive_path, quiet)
    except Exception as exc:   # SystemExit (our own clear messages) is not an Exception, so it passes through untouched
        raise SystemExit(f"Backup rusak atau tidak dapat dibaca ({type(exc).__name__}): {archive_path.name}")


def _verify_backup(archive_path: Path, quiet: bool = False) -> dict:
    if not zipfile.is_zipfile(archive_path):
        raise SystemExit(f"Bukan file backup yang valid: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"File rusak di dalam backup: {bad}")
        names = set(archive.namelist())
        if "ops.sqlite3" not in names or "manifest.json" not in names:
            raise SystemExit("Backup tidak lengkap: ops.sqlite3 atau manifest.json tidak ada.")
        manifest = json.loads(archive.read("manifest.json"))
        with tempfile.TemporaryDirectory() as tmp:
            restored = Path(tmp) / "ops.sqlite3"
            restored.write_bytes(archive.read("ops.sqlite3"))
            if sha256(restored) != manifest.get("database_sha256"):
                raise SystemExit("Checksum database tidak cocok dengan manifest.")
            conn = sqlite3.connect(restored)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SystemExit("Pemeriksaan integritas database gagal.")
                broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                if broken and not quiet:
                    # Old data can contain orphan rows; report it but do not refuse to keep a backup.
                    print(f"    PERHATIAN: {len(broken)} baris dengan relasi yatim di database (tidak menghalangi restore).")
            finally:
                conn.close()
            if table_counts(restored) != manifest.get("tables"):
                raise SystemExit("Jumlah data di database tidak cocok dengan manifest.")
        for name, expected in manifest.get("folders", {}).items():
            actual = sum(1 for n in names if n.startswith(f"files/{name}/"))
            if actual != expected:
                raise SystemExit(f"Jumlah file folder {name} tidak cocok ({actual} dari {expected}).")
    if not quiet:
        print(f"OK  {archive_path.name}  dibuat {manifest['created_at']}")
        print("    data:", ", ".join(f"{k}={v}" for k, v in manifest["tables"].items()))
        print("    file:", ", ".join(f"{k}={v}" for k, v in manifest["folders"].items()))
    return manifest


def restore_backup(archive_path: Path) -> None:
    """Put a backup back. The server MUST be stopped. The current database is kept aside, never deleted."""
    verify_backup(archive_path, quiet=True)
    target = db_path()
    stamp = datetime.now(WIB).strftime("%Y%m%d-%H%M%S")
    if target.exists():
        aside = target.with_name(f"{target.name}.before-restore-{stamp}")
        shutil.copy2(target, aside)
        print(f"Database saat ini disimpan di: {aside}")
    for suffix in ("-wal", "-shm"):
        leftover = target.with_name(target.name + suffix)
        if leftover.exists():
            leftover.unlink()
    with zipfile.ZipFile(archive_path) as archive:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read("ops.sqlite3"))
        folders = dict(storage_dirs())
        restored = 0
        for name in archive.namelist():
            if not name.startswith("files/") or name.endswith("/"):
                continue
            _, folder_name, relative = name.split("/", 2)
            if folder_name not in folders:
                continue
            destination = folders[folder_name] / relative
            if not destination.resolve().is_relative_to(folders[folder_name].resolve()):
                continue   # never write outside the storage folder
            if destination.exists():
                continue   # keep any newer file already on disk
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
            restored += 1
    print(f"Database dipulihkan ke {target}. {restored} file foto/dokumen dikembalikan.")
    print("Jalankan server lagi, lalu cek beberapa event dan absensi.")


def main(argv: list[str]) -> int:
    load_dotenv()
    command = argv[1] if len(argv) > 1 else "backup"
    if command == "backup":
        path = make_backup()
        manifest = verify_backup(path, quiet=True)
        size = path.stat().st_size / 1_048_576
        print(f"Backup selesai: {path}  ({size:.1f} MB)")
        print("  data:", ", ".join(f"{k}={v}" for k, v in manifest["tables"].items()))
        return 0
    if command == "list":
        found = backups()
        if not found:
            print(f"Belum ada backup di {backup_dir()}")
        for path in found:
            print(f"{path.name}  {path.stat().st_size / 1_048_576:.1f} MB")
        return 0
    if command == "verify":
        target = Path(argv[2]) if len(argv) > 2 else (backups() or [None])[-1]
        if not target:
            raise SystemExit("Tidak ada backup untuk diperiksa.")
        verify_backup(Path(target))
        return 0
    if command == "restore":
        if len(argv) < 3 or "--yes" not in argv:
            raise SystemExit("Pemakaian: python backup.py restore FILE.zip --yes   (matikan server terlebih dahulu)")
        restore_backup(Path(argv[2]))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
