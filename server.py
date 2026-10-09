#!/usr/bin/env python3
"""Capture It Operations: small, dependency-free Python/SQLite MVP."""

from __future__ import annotations

import argparse
import base64
import csv
import getpass
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape
from operations import (EXPORT_HEADERS, can_manage_logistics, csv_value, export_event_rows, fleet_rows,
                        logistics_detail, make_event_list_xlsx, notifications_payload as operational_notifications, validate_logistics, vehicle_conflicts)
from operations import WIB, local_datetime, transport_modes, transport_label
from closing_reports import ClosingVersionConflict, can_read_closing, can_review_closing, closing_detail, save_closing
from staffing import event_staff_conflicts, staff_availability
import eventdays
from compensation import append_change, change_reason, effective_date, history_payload, migrate_history, salary_at
import notification_center as notices
import push_delivery


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DB_PATH = Path(os.environ.get("OPS_DB_PATH", str(ROOT / "ops.sqlite3")))
DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes"}
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
PASSWORD_ITERATIONS = 260_000
DB_LOCK = threading.RLock()
GOOGLE_SYNC_LOCK = threading.RLock()
BACKGROUND_TASKS_LOCK = threading.Lock()
BACKGROUND_TASKS_STARTED = False
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"),
    ("Referrer-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)


ROLE_LIST = [
    ("administrator", "Administrator"),
    ("head_operations", "Head Operations"),
    ("event_coordinator", "Event Coordinator"),
    ("head_finance", "Head Finance"),
    ("finance", "Finance"),
    ("admin_finance", "Admin Finance"),
    ("warehouse_head", "Warehouse Head"),
    ("warehouse_staff", "Warehouse Staff"),
    ("design_head", "Head Design"),
    ("design_team", "Team Design"),
    ("sales_staff", "Sales Staff"),
    ("content_team", "Content Team"),
    ("inhouse_employee", "In-house Staff"),
    ("crew", "Crew"),
    ("pic_event", "PIC Event"),
]

PERMISSION_DESCRIPTIONS = {
    "events.read_all": "Melihat seluruh jadwal operasional",
    "events.read_own": "Melihat event yang ditugaskan",
    "events.assign": "Mengatur koordinator dan penugasan event",
    "events.clear": "Menandai event clear",
    "events.project_code.manage": "Memasangkan kode project CRM ke event Calendar",
    "events.export_own": "Mengekspor jadwal event yang dikoordinasikan sendiri",
    "events.whatsapp.manage": "Mengelola status grup komunikasi event",
    "attendance.self": "Mengisi absensi penugasan sendiri",
    "attendance.manage": "Mengelola absensi event",
    "events.manual.delete": "Menghapus event manual (uji coba)",
    "attendance.correct": "Mengoreksi absensi crew yang terlewat (hanya Event Coordinator dan Head Operations)",
    "attendance.inhouse.self": "Mengisi absensi harian In-house sendiri",
    "attendance.inhouse.read_all": "Melihat absensi harian seluruh tim In-house",
    "inhouse_payroll.view": "Melihat penggajian bulanan In-house",
    "inhouse_payroll.manage": "Mengatur gaji pokok dan mencatat transfer payroll In-house",
    "inhouse_payroll.read_own": "Melihat slip gaji In-house sendiri",
    "payroll.read_own": "Melihat slip honor event Freelancer sendiri",
    "advances.read": "Melihat status uang jalan",
    "advances.request": "Mengajukan uang jalan untuk event sendiri",
    "advances.approve": "Menyetujui atau menolak uang jalan",
    "advances.transfer": "Mencatat transfer uang jalan",
    "advances.documents.read_all": "Melihat dan mengunduh dokumen pertanggungjawaban uang jalan",
    "warehouse.update": "Memperbarui kesiapan dan pengembalian alat",
    "design.read": "Melihat papan desain",
    "design.update": "Memperbarui status desain",
    "design.read_all": "Melihat semua kartu desain (tanpa ini hanya kartu yang ditugaskan kepadanya)",
    "design.assign": "Mengirim brief dan memilih designer",
    "payroll.view": "Melihat rekap penggajian operasional",
    "payroll.export": "Mengekspor payroll",
    "fees.manage": "Mengubah rate fee dasar",
    "skills.manage": "Mengelola daftar skill khusus dan fee tambahan",
    "kpi.evaluate_crew": "Menilai crew dan PIC event",
    "kpi.evaluate_operations": "Menilai koordinator dan warehouse",
    "kpi.evaluate_finance": "Menilai admin finance",
    "kpi.read": "Melihat ringkasan KPI",
    "kpi.read_own": "Melihat riwayat KPI sendiri",
    "users.manage": "Mengelola akun pengguna melalui perintah admin",
    "app.configure": "Mengatur tampilan aplikasi, aset brand, dan matriks akses role",
    "profile.read_own": "Melihat profil dan dokumen pribadi",
    "profile.update_own": "Memperbarui profil dan dokumen pribadi",
    "staff.directory.read": "Melihat direktori Crew/PIC",
    "staff.documents.read": "Melihat dokumen KTP Crew/PIC",
    "google.sync": "Menarik jadwal dari Google Calendar",
}

PROFILE_PERMISSIONS = {"profile.read_own", "profile.update_own"}

# Fee lists (freelance honor and In-house salary) are hidden from these roles. The restriction is
# applied after the ACL is resolved, so Configure cannot grant it back by accident.
FEE_RESTRICTED_PERMISSIONS = {"payroll.view", "payroll.export", "fees.manage", "skills.manage",
                              "inhouse_payroll.view", "inhouse_payroll.manage"}
FEE_RESTRICTED_ROLES = {"admin_finance", "sales_staff", "admin_sales"}

ROLE_PERMISSIONS = {
    "administrator": set(PERMISSION_DESCRIPTIONS),
    "head_operations": {
        "events.read_all", "events.assign", "events.whatsapp.manage", "attendance.manage", "attendance.correct", "advances.read",
        "attendance.inhouse.read_all", "attendance.inhouse.self", "inhouse_payroll.read_own",
        "warehouse.update", "design.read", "design.assign", "payroll.view", "fees.manage",
        "skills.manage", "kpi.evaluate_operations", "kpi.read", "google.sync",
        "staff.directory.read", "staff.documents.read",
    },
    "event_coordinator": {
        "events.read_all", "events.assign", "events.clear", "events.whatsapp.manage", "events.export_own", "attendance.manage", "attendance.correct", "advances.read",
        "attendance.inhouse.self", "inhouse_payroll.read_own",
        "design.read", "design.assign", "kpi.evaluate_crew", "kpi.read", "google.sync", "staff.directory.read",
    },
    "head_finance": {
        "events.read_all", "advances.read", "advances.approve", "advances.transfer",
        "advances.documents.read_all",
        "payroll.view", "payroll.export", "fees.manage", "kpi.evaluate_finance", "attendance.inhouse.read_all", "attendance.inhouse.self",
        "inhouse_payroll.view", "inhouse_payroll.manage", "inhouse_payroll.read_own",
        "skills.manage", "kpi.read", "google.sync",
    },
    "finance": {
        "events.read_all", "advances.read", "advances.approve", "advances.transfer",
        "advances.documents.read_all",
        "payroll.view", "payroll.export", "kpi.evaluate_finance", "kpi.read", "attendance.inhouse.read_all", "attendance.inhouse.self",
        "inhouse_payroll.view", "inhouse_payroll.manage", "inhouse_payroll.read_own",
    },
    "admin_finance": {
        "events.read_all", "advances.read", "kpi.read_own", "attendance.inhouse.read_all", "attendance.inhouse.self",
        "advances.documents.read_all",
        "inhouse_payroll.read_own",
    },
    "warehouse_head": {"events.read_all", "warehouse.update", "attendance.inhouse.self", "inhouse_payroll.read_own"},
    "warehouse_staff": {"events.read_all", "warehouse.update", "attendance.inhouse.self", "inhouse_payroll.read_own"},
    "design_head": {"events.read_all", "design.read", "design.read_all", "design.update", "design.assign",
                    "attendance.inhouse.self", "inhouse_payroll.read_own"},
    "design_team": {"events.read_all", "design.read", "design.update", "attendance.inhouse.self", "inhouse_payroll.read_own"},
    "sales_staff": {"attendance.inhouse.self", "inhouse_payroll.read_own"},
    "content_team": {"attendance.inhouse.self", "inhouse_payroll.read_own"},
    "inhouse_employee": {"attendance.inhouse.self", "inhouse_payroll.read_own"},
    "crew": {"events.read_own", "attendance.self", "kpi.read_own", "payroll.read_own"},
    "pic_event": {"events.read_own", "attendance.self", "advances.request", "advances.read", "kpi.read_own", "payroll.read_own"},
}
for _role_permissions in ROLE_PERMISSIONS.values():
    _role_permissions.update(PROFILE_PERMISSIONS)
# Whoever could already see the whole design board keeps doing so; only Team Design is narrowed to its own cards.
for _role, _role_permissions in ROLE_PERMISSIONS.items():
    if _role != "design_team" and "design.read" in _role_permissions:
        _role_permissions.add("design.read_all")

PERFORMANCE_CRITERIA = (
    ("work_quality", "Kualitas kerja dan hasil"),
    ("punctuality", "Ketepatan waktu"),
    ("teamwork", "Komunikasi dan kerja sama"),
    ("sop_equipment", "Kepatuhan SOP dan perawatan alat"),
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def insert_event_performance_review(conn: sqlite3.Connection, assignment_id: int, reviewer_id: int,
                                   scores: dict, note: str) -> tuple[int, int]:
    normalized = {}
    for code, _label in PERFORMANCE_CRITERIA:
        value = scores.get(code)
        if isinstance(value, bool):
            raise ValueError("Setiap aspek penilaian harus bernilai 1–5.")
        try:
            score = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Lengkapi semua aspek penilaian dengan skor 1–5.") from exc
        if score < 1 or score > 5:
            raise ValueError("Setiap aspek penilaian harus bernilai 1–5.")
        normalized[code] = score
    if set(scores) != {code for code, _label in PERFORMANCE_CRITERIA}:
        raise ValueError("Lengkapi seluruh aspek penilaian.")
    percent = round(sum(normalized.values()) * 100 / (5 * len(PERFORMANCE_CRITERIA)))
    cur = conn.execute("""INSERT INTO event_performance_reviews(assignment_id,reviewer_id,score_percent,note)
        VALUES(?,?,?,?)""", (assignment_id,reviewer_id,percent,note[:1000]))
    conn.executemany("""INSERT INTO event_performance_items(review_id,criterion_code,criterion_name,score)
        VALUES(?,?,?,?)""", [
            (cur.lastrowid, code, label, normalized[code]) for code, label in PERFORMANCE_CRITERIA
        ])
    return cur.lastrowid, percent


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.create_function('wib_date', 1, lambda value: local_datetime(value).date().isoformat() if value else None, deterministic=True)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def attendance_storage_dir() -> Path:
    configured = os.environ.get("ATTENDANCE_STORAGE_DIR", "").strip()
    return Path(configured) if configured else DB_PATH.parent / "attendance-photos"


def profile_storage_dir() -> Path:
    configured = os.environ.get("PROFILE_STORAGE_DIR", "").strip()
    return Path(configured) if configured else DB_PATH.parent / "private-profile-uploads"


def branding_storage_dir() -> Path:
    configured = os.environ.get("BRANDING_STORAGE_DIR", "").strip()
    return Path(configured) if configured else DB_PATH.parent / "branding-assets"


def parse_profile_image(data_url: object, max_bytes: int, field_label: str) -> tuple[bytes, str, str]:
    if not isinstance(data_url, str):
        raise ValueError(f"Pilih file gambar untuk {field_label}.")
    match = re.fullmatch(r"data:image/(jpeg|png);base64,([A-Za-z0-9+/]*={0,2})", data_url)
    if not match or len(data_url) > 900_000:
        raise ValueError(f"{field_label} harus berupa gambar JPG atau PNG yang valid dan berukuran kecil.")
    mime = "image/jpeg" if match.group(1) == "jpeg" else "image/png"
    try:
        content = base64.b64decode(match.group(2), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"File {field_label} tidak valid. Silakan pilih ulang.") from exc
    valid_image = (
        (mime == "image/jpeg" and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"))
        or (mime == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n") and content.endswith(b"IEND\xaeB`\x82"))
    )
    if len(content) > max_bytes or not valid_image:
        raise ValueError(f"File {field_label} harus berupa JPG/PNG valid dengan ukuran maksimal {max_bytes // 1024} KB.")
    extension = "jpg" if mime == "image/jpeg" else "png"
    return content, mime, extension


def parse_brand_asset(data_url: object, kind: str) -> tuple[bytes, str, str]:
    if not isinstance(data_url, str) or len(data_url) > 760_000:
        raise ValueError("File brand harus berukuran maksimal 500 KB.")
    match = re.fullmatch(r"data:image/(png|jpeg|x-icon|vnd\.microsoft\.icon);base64,([A-Za-z0-9+/]*={0,2})", data_url)
    if not match:
        raise ValueError("Pilih gambar PNG/JPG; favicon juga mendukung ICO.")
    claimed, encoded = match.groups()
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("File gambar tidak valid.") from exc
    if not content or len(content) > 500_000:
        raise ValueError("File brand harus berukuran maksimal 500 KB.")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, extension = "image/png", "png"
    elif content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        mime, extension = "image/jpeg", "jpg"
    elif content[:4] == b"\x00\x00\x01\x00":
        mime, extension = "image/vnd.microsoft.icon", "ico"
    else:
        raise ValueError("Isi file bukan gambar PNG, JPG, atau ICO yang valid.")
    claimed_ok = (claimed == "png" and extension == "png") or (claimed == "jpeg" and extension == "jpg") or (claimed in {"x-icon", "vnd.microsoft.icon"} and extension == "ico")
    if not claimed_ok or (kind == "logo" and extension == "ico"):
        raise ValueError("Format gambar tidak sesuai dengan aset yang dipilih.")
    return content, mime, extension


def store_profile_file(user_id: int, field: str, content: bytes, extension: str) -> str:
    root = profile_storage_dir()
    relative = Path(str(user_id)) / f"{field}-{secrets.token_hex(16)}.{extension}"
    path = root / relative
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content)
    try:
        root.chmod(0o700)
        path.parent.chmod(0o700)
        path.chmod(0o600)
    except OSError:
        pass
    return relative.as_posix()


def remove_profile_file(relative_path: str | None) -> None:
    if not relative_path:
        return
    root = profile_storage_dir().resolve()
    path = (root / relative_path).resolve()
    if path.is_relative_to(root):
        path.unlink(missing_ok=True)


def parse_attendance_photo(data_url: object) -> bytes:
    prefix = "data:image/jpeg;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("Ambil foto wajah dari kamera sebelum mengirim absensi.")
    encoded = data_url[len(prefix):]
    if len(encoded) > 930_000:
        raise ValueError("Ukuran foto terlalu besar. Ambil ulang dengan kamera.")
    try:
        photo = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Format foto tidak valid. Ambil ulang dengan kamera.") from exc
    if len(photo) > 700_000 or not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
        raise ValueError("Foto harus berupa JPEG yang valid. Ambil ulang dengan kamera.")
    return photo


def parse_attendance_location(value: object) -> tuple[float,float,float,str]:
    if not isinstance(value,dict):
        raise ValueError("Izinkan akses lokasi/GPS perangkat untuk absensi.")
    try:
        latitude=float(value.get("latitude")); longitude=float(value.get("longitude")); accuracy=float(value.get("accuracy_m"))
        captured_at=parse_iso(str(value.get("captured_at","")))
    except (TypeError,ValueError) as exc:
        raise ValueError("Data lokasi tidak valid. Aktifkan lokasi lalu ambil ulang foto.") from exc
    if not all(math.isfinite(v) for v in (latitude,longitude,accuracy)) or not -90<=latitude<=90 or not -180<=longitude<=180 or not 0<accuracy<=500:
        raise ValueError("Lokasi tidak cukup akurat (maksimal 500 meter). Aktifkan GPS dan coba di area terbuka.")
    now=datetime.now(timezone.utc)
    if abs((now-captured_at.astimezone(timezone.utc)).total_seconds())>600:
        raise ValueError("Lokasi sudah kedaluwarsa. Ambil ulang foto dan lokasi untuk absensi.")
    return latitude,longitude,accuracy,captured_at.astimezone(timezone.utc).isoformat(timespec="seconds")


def store_attendance_photo(assignment_id: int, action: str, photo: bytes) -> str:
    root = attendance_storage_dir()
    relative = Path(str(assignment_id)) / f"{action}-{secrets.token_hex(16)}.jpg"
    path = root / relative
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    path.write_bytes(photo)
    path.chmod(0o600)
    return relative.as_posix()


def inhouse_attendance_storage_dir() -> Path:
    configured=os.environ.get("INHOUSE_ATTENDANCE_STORAGE_DIR","").strip()
    return Path(configured) if configured else DB_PATH.parent/"inhouse-attendance-photos"


def cash_advance_storage_dir() -> Path:
    configured=os.environ.get("CASH_ADVANCE_STORAGE_DIR","").strip()
    return Path(configured) if configured else DB_PATH.parent/"cash-advance-documents"


def parse_cash_advance_document(data_url: object, filename: object) -> tuple[bytes,str,str,str]:
    if not isinstance(filename,str): raise ValueError("Pilih file PDF atau Excel.")
    safe_name=Path(filename.replace("\\","/")).name.strip()
    if not safe_name or len(safe_name)>180 or any(ord(ch)<32 for ch in safe_name): raise ValueError("Nama file tidak valid.")
    ext=Path(safe_name).suffix.lower()
    formats={
        ".pdf":("application/pdf",b"%PDF-"),
        ".xlsx":("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",b"PK\x03\x04"),
        ".xls":("application/vnd.ms-excel",b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    }
    if ext not in formats: raise ValueError("Format yang didukung: PDF, XLS, atau XLSX.")
    try:
        head,encoded=str(data_url).split(",",1)
        claimed=head.removeprefix("data:").removesuffix(";base64")
        content=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError,base64.binascii.Error) as exc: raise ValueError("Isi file tidak valid.") from exc
    mime,signature=formats[ext]
    if len(content)>10*1024*1024 or not content.startswith(signature) or claimed!=mime:
        raise ValueError("File harus PDF/XLS/XLSX yang valid, maksimal 10 MB.")
    return content,mime,ext,safe_name


def store_cash_advance_document(event_id: int, content: bytes, extension: str) -> str:
    root=cash_advance_storage_dir(); relative=Path(str(event_id))/f"{secrets.token_hex(20)}{extension}"; path=root/relative
    root.mkdir(parents=True,exist_ok=True,mode=0o700); root.chmod(0o700)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700); path.write_bytes(content); path.chmod(0o600)
    return relative.as_posix()


def store_inhouse_attendance_photo(user_id: int, action: str, photo: bytes) -> str:
    root=inhouse_attendance_storage_dir()
    relative=Path(str(user_id))/f"{action}-{secrets.token_hex(16)}.jpg"
    path=root/relative
    root.mkdir(parents=True,exist_ok=True,mode=0o700); root.chmod(0o700)
    path.parent.mkdir(exist_ok=True,mode=0o700)
    path.write_bytes(photo); path.chmod(0o600)
    return relative.as_posix()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('Password harus terdiri dari 12–1024 karakter.')


def rupiah(value, label='Nominal', maximum=2_000_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'\d{1,13}', str(value)):
        raise ValueError(f'{label} harus berupa rupiah bulat, tanpa desimal.')
    amount = int(value)
    if not 0 <= amount <= maximum:
        raise ValueError(f'{label} harus berada di antara Rp0 dan Rp{maximum:,}.')
    return amount


def account_classification(role_code: str) -> tuple[str, str]:
    employment = 'freelancer' if role_code in {'crew', 'pic_event'} else 'inhouse'
    department = {'administrator':'Management','head_operations':'Operations','event_coordinator':'Operations',
        'head_finance':'Finance','finance':'Finance','admin_finance':'Finance','warehouse_head':'Warehouse',
        'warehouse_staff':'Warehouse','design_team':'Design','design_head':'Design','sales_staff':'Sales','content_team':'Content'}.get(role_code,'Other')
    return employment, department


def check_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt64, expected64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode())
        expected = base64.urlsafe_b64decode(expected64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def audit(conn: sqlite3.Connection, actor_id: int | None, entity_type: str, entity_id: int | None,
          action: str, details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_logs(actor_id,entity_type,entity_id,action,details,created_at) VALUES(?,?,?,?,?,?)",
        (actor_id, entity_type, entity_id, action, json.dumps(details or {}, ensure_ascii=False), now_iso()),
    )


def seed_reference_data(conn: sqlite3.Connection) -> None:
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    design_columns = {r['name'] for r in conn.execute('PRAGMA table_info(design_tasks)')}
    for name, definition in {'brief_text': "TEXT NOT NULL DEFAULT ''", 'brief_version': 'INTEGER NOT NULL DEFAULT 0',
                             'brief_sent_at': 'TEXT', 'brief_sent_by': 'INTEGER REFERENCES users(id) ON DELETE SET NULL',
                             'final_url': "TEXT NOT NULL DEFAULT ''"}.items():
        if name not in design_columns:
            conn.execute(f'ALTER TABLE design_tasks ADD COLUMN {name} {definition}')
    conn.execute("INSERT OR IGNORE INTO app_settings(setting_key,setting_value) VALUES('draft_namespace',?)", (secrets.token_hex(16),))
    logistics_columns = {row['name'] for row in conn.execute('PRAGMA table_info(event_operations)')}
    if 'transport_modes' not in logistics_columns:
        conn.execute("ALTER TABLE event_operations ADD COLUMN transport_modes TEXT NOT NULL DEFAULT '[]'")
        conn.execute("UPDATE event_operations SET transport_modes='[\"fleet\"]' WHERE vehicle_id IS NOT NULL")
    for column in ('courier_name','courier_note','other_transport','transport_note'):
        if column not in logistics_columns:
            conn.execute(f"ALTER TABLE event_operations ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "employment_type" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN employment_type TEXT NOT NULL DEFAULT 'inhouse' CHECK (employment_type IN ('freelancer','inhouse'))")
    if "department" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN department TEXT NOT NULL DEFAULT ''")
    if "is_manual" not in {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}:
        conn.execute("ALTER TABLE events ADD COLUMN is_manual INTEGER NOT NULL DEFAULT 0")
    for table in ("attendance", "attendance_days"):
        att_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "corrected_by" not in att_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN corrected_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "corrected_at" not in att_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN corrected_at TEXT")
    event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "completed_at" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN completed_at TEXT")
    if "completed_by" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN completed_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
    if "operational_code" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN operational_code TEXT")
    if "project_code_is_temporary" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN project_code_is_temporary INTEGER NOT NULL DEFAULT 0 CHECK (project_code_is_temporary IN (0,1))")
    if "is_full_day_manual" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN is_full_day_manual INTEGER NOT NULL DEFAULT 0 CHECK (is_full_day_manual IN (0,1))")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_operational_code ON events(operational_code) WHERE operational_code IS NOT NULL AND operational_code!=''")
    attendance_columns = {row["name"] for row in conn.execute("PRAGMA table_info(attendance)").fetchall()}
    for column in ("check_in_photo_path", "check_out_photo_path","check_in_latitude","check_in_longitude","check_in_accuracy_m",
                   "check_out_latitude","check_out_longitude","check_out_accuracy_m"):
        if column not in attendance_columns:
            sql_type="REAL" if column.endswith(("latitude","longitude","accuracy_m")) else "TEXT"
            conn.execute(f"ALTER TABLE attendance ADD COLUMN {column} {sql_type}")
    inhouse_attendance_columns={row["name"] for row in conn.execute("PRAGMA table_info(inhouse_attendance)").fetchall()}
    for column in ("check_in_photo_path","check_out_photo_path","check_in_latitude","check_in_longitude","check_in_accuracy_m",
                   "check_out_latitude","check_out_longitude","check_out_accuracy_m"):
        if column not in inhouse_attendance_columns:
            sql_type="REAL" if column.endswith(("latitude","longitude","accuracy_m")) else "TEXT"
            conn.execute(f"ALTER TABLE inhouse_attendance ADD COLUMN {column} {sql_type}")
    inhouse_now = {row["name"] for row in conn.execute("PRAGMA table_info(inhouse_attendance)").fetchall()}
    for column, sql_type in (("scheduled_start", "TEXT"), ("scheduled_end", "TEXT"),
                             ("late_minutes", "INTEGER"), ("early_leave_minutes", "INTEGER")):
        if column not in inhouse_now:
            conn.execute(f"ALTER TABLE inhouse_attendance ADD COLUMN {column} {sql_type}")
    assignment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assignment_skills)").fetchall()}
    if "extra_fee_rupiah" not in assignment_columns:
        conn.execute("ALTER TABLE assignment_skills ADD COLUMN extra_fee_rupiah INTEGER NOT NULL DEFAULT 0")
    if "skill_name_snapshot" not in assignment_columns:
        conn.execute("ALTER TABLE assignment_skills ADD COLUMN skill_name_snapshot TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE assignment_skills SET skill_name_snapshot=(SELECT name FROM skills WHERE skills.id=assignment_skills.skill_id)")
    event_assignment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(event_assignments)").fetchall()}
    if "travel_buffer_minutes" not in event_assignment_columns:
        conn.execute("ALTER TABLE event_assignments ADD COLUMN travel_buffer_minutes INTEGER NOT NULL DEFAULT 60 CHECK (travel_buffer_minutes BETWEEN 0 AND 720)")
    if "travel_ack_signature" not in event_assignment_columns:
        conn.execute("ALTER TABLE event_assignments ADD COLUMN travel_ack_signature TEXT NOT NULL DEFAULT ''")
    for column, definition in (('schedule_ack_signature', "TEXT NOT NULL DEFAULT ''"),
                               ('schedule_acknowledged_at', 'TEXT'),
                               ('schedule_acknowledged_by', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')):
        if column not in event_assignment_columns:
            conn.execute(f'ALTER TABLE event_assignments ADD COLUMN {column} {definition}')
    if "base_fee_snapshot_rupiah" not in event_assignment_columns:
        conn.execute("ALTER TABLE event_assignments ADD COLUMN base_fee_snapshot_rupiah INTEGER CHECK (base_fee_snapshot_rupiah IS NULL OR base_fee_snapshot_rupiah >= 0)")
    duplicate_assignments=conn.execute("SELECT COUNT(*) FROM (SELECT assignment_id FROM payroll_lines GROUP BY assignment_id HAVING COUNT(*)>1)").fetchone()[0]
    if duplicate_assignments:
        print(f"WARNING: payroll audit found {duplicate_assignments} event assignments in multiple historical payroll batches; reconcile transfers before production.",file=sys.stderr,flush=True)
    conn.execute("""INSERT OR IGNORE INTO payroll_assignment_claims(assignment_id,batch_id,claimed_at)
        SELECT assignment_id,MIN(batch_id),CURRENT_TIMESTAMP FROM payroll_lines GROUP BY assignment_id""")
    transferred_payouts=conn.execute("""SELECT p.id payout_id,p.user_id,p.batch_id,b.period_start,b.period_end,p.transferred_at
        FROM inhouse_payroll_payouts p JOIN inhouse_payroll_batches b ON b.id=p.batch_id
        WHERE p.status='transferred' ORDER BY p.transferred_at,p.id""").fetchall()
    duplicate_inhouse_months=0
    for payout in transferred_payouts:
        month_start=date.fromisoformat(payout["period_start"]).replace(day=1)
        month_end=date.fromisoformat(payout["period_end"])
        while month_start<=month_end:
            month_key=month_start.strftime("%Y-%m")
            previous=conn.execute("SELECT payout_id FROM inhouse_monthly_salary_claims WHERE user_id=? AND payroll_month=?",
                (payout["user_id"],month_key)).fetchone()
            if previous and previous["payout_id"]!=payout["payout_id"]:
                duplicate_inhouse_months+=1
            conn.execute("INSERT OR IGNORE INTO inhouse_monthly_salary_claims(user_id,payroll_month,batch_id,payout_id,claimed_at) VALUES(?,?,?,?,?)",
                (payout["user_id"],month_key,payout["batch_id"],payout["payout_id"],payout["transferred_at"] or now_iso()))
            month_start=(month_start.replace(day=28)+timedelta(days=4)).replace(day=1)
    if duplicate_inhouse_months:
        print(f"WARNING: payroll audit found {duplicate_inhouse_months} historical in-house account-month overlaps; reconcile transfers before production.",file=sys.stderr,flush=True)
    for code, name in ROLE_LIST:
        conn.execute("INSERT OR IGNORE INTO roles(code,name) VALUES(?,?)", (code, name))
    conn.execute("""UPDATE users SET employment_type=CASE WHEN EXISTS(
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=users.id AND r.code IN ('crew','pic_event')
        ) THEN 'freelancer' ELSE 'inhouse' END""")
    departments = {"administrator":"Management","head_operations":"Operations","event_coordinator":"Operations",
        "head_finance":"Finance","finance":"Finance","admin_finance":"Finance","warehouse_head":"Warehouse",
        "warehouse_staff":"Warehouse","design_team":"Design","design_head":"Design","sales_staff":"Sales","content_team":"Content"}
    for role_code, department in departments.items():
        conn.execute("UPDATE users SET department=? WHERE department='' AND id IN (SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.code=?)", (department,role_code))
    for code, description in PERMISSION_DESCRIPTIONS.items():
        conn.execute("INSERT OR IGNORE INTO permissions(code,description) VALUES(?,?)", (code, description))
    for role_code, codes in ROLE_PERMISSIONS.items():
        role = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO role_acl_settings(role_id,customized) VALUES(?,0)", (role["id"],))
        acl = conn.execute("SELECT customized FROM role_acl_settings WHERE role_id=?", (role["id"],)).fetchone()
        if not acl["customized"]:
            conn.execute("DELETE FROM role_permissions WHERE role_id=?", (role["id"],))
            for code in codes:
                permission = conn.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()
                conn.execute("INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)", (role["id"], permission["id"]))
    for role_code in FEE_RESTRICTED_ROLES:
        conn.execute("""DELETE FROM role_permissions WHERE role_id=(SELECT id FROM roles WHERE code=?)
            AND permission_id IN (SELECT id FROM permissions WHERE code IN (%s))""" % ",".join("?" for _ in FEE_RESTRICTED_PERMISSIONS),
            (role_code, *sorted(FEE_RESTRICTED_PERMISSIONS)))
    for position in ("Crew", "Lead Crew", "PIC Event"):
        conn.execute("INSERT OR IGNORE INTO positions(name) VALUES(?)", (position,))
    conn.execute("INSERT OR IGNORE INTO skills(name,extra_fee_rupiah,active) VALUES(?,?,1)", ("Hologram", 50000))


def migrate_user_fee_rates(conn: sqlite3.Connection) -> None:
    version = "2026-09-per-user-fee-rates"
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
        return
    users = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    for user in users:
        assignment = conn.execute("""
            SELECT position_id FROM event_assignments
            WHERE user_id=? AND position_id IS NOT NULL
            ORDER BY assigned_at DESC, id DESC LIMIT 1
        """, (user["id"],)).fetchone()
        position_id = assignment["position_id"] if assignment else None
        if position_id is None:
            role = conn.execute("""
                SELECT p.id FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                JOIN positions p ON p.name=CASE WHEN r.code='pic_event' THEN 'PIC Event' ELSE 'Crew' END
                WHERE ur.user_id=? AND r.code IN ('crew','pic_event') LIMIT 1
            """, (user["id"],)).fetchone()
            position_id = role["id"] if role else None
        if position_id is None:
            continue
        old_rates = conn.execute("SELECT * FROM fee_rates WHERE position_id=? ORDER BY effective_from", (position_id,)).fetchall()
        for rate in old_rates:
            conn.execute("""
                INSERT OR IGNORE INTO user_fee_rates(user_id,base_fee_rupiah,effective_from,updated_by)
                VALUES(?,?,?,?)
            """, (user["id"],rate["base_fee_rupiah"],rate["effective_from"],rate["updated_by"]))
    conn.execute("INSERT INTO schema_migrations(version) VALUES(?)", (version,))


def seed_demo(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
        return
    demo_users = [
        ("Alya Pratama", "admin@captureit.local", "administrator"),
        ("Rangga Putra", "head.ops@captureit.local", "head_operations"),
        ("Nadia Putri", "coordinator@captureit.local", "event_coordinator"),
        ("Fajar Santoso", "head.finance@captureit.local", "head_finance"),
        ("Intan Maharani", "finance@captureit.local", "finance"),
        ("Sinta Lestari", "admin.finance@captureit.local", "admin_finance"),
        ("Bima Saputra", "warehouse.head@captureit.local", "warehouse_head"),
        ("Riko Pratama", "warehouse@captureit.local", "warehouse_staff"),
        ("Maya Anindya", "design@captureit.local", "design_team"),
        ("Dimas Ardi", "crew@captureit.local", "crew"),
        ("Raka Putra", "pic@captureit.local", "pic_event"),
        ("Naya Putri", "naya@captureit.local", "crew"),
        ("Fikri Hadi", "fikri@captureit.local", "crew"),
    ]
    uid = {}
    for full_name, email, role_code in demo_users:
        cur = conn.execute(
            "INSERT INTO users(full_name,email,password_hash,active) VALUES(?,?,?,1)",
            (full_name, email, password_hash("demo1234")),
        )
        uid[email] = cur.lastrowid
        rid = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()["id"]
        conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (cur.lastrowid, rid))

    positions = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM positions").fetchall()}
    # Base rates intentionally start at zero; enter the approved real rates per account in Settings.
    for user_id in uid.values():
        conn.execute("INSERT OR IGNORE INTO user_fee_rates(user_id,base_fee_rupiah,effective_from,updated_by) VALUES(?,?,?,?)",
                     (user_id, 0, "2026-01-01", uid["admin@captureit.local"]))

    events = [
        ("CI-OPS-26001", "Grand Opening · Senopati", "Corporate", "2026-10-03T09:00:00+07:00", "2026-10-03T19:00:00+07:00", "The Langham, Jakarta", 1, "scheduled"),
        ("CI-OPS-26002", "Wedding · Taman Mini", "Wedding", "2026-10-04T14:00:00+07:00", "2026-10-04T22:00:00+07:00", "Sasono Utomo, TMII", 1, "scheduled"),
        ("CI-OPS-26003", "Brand Activation · PIK", "Brand Activation", "2026-10-06T10:00:00+07:00", "2026-10-06T16:00:00+07:00", "Community Park PIK 2", 0, "scheduled"),
        ("CI-OPS-26004", "Birthday · Kemang", "Private Event", "2026-10-08T16:00:00+07:00", "2026-10-08T22:00:00+07:00", "The Dharmawangsa, Jakarta", 0, "scheduled"),
        ("CI-OPS-25988", "Company Gathering · BSD", "Corporate", "2026-09-20T10:00:00+07:00", "2026-09-20T18:00:00+07:00", "ICE BSD, Tangerang", 1, "completed"),
    ]
    for code, title, kind, starts, ends, location, full_day, status in events:
        cur = conn.execute(
            "INSERT INTO events(project_code,title,event_type,starts_at,ends_at,location,is_full_day,status,coordinator_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (code, title, kind, starts, ends, location, full_day, status, uid["coordinator@captureit.local"]),
        )
        event_id = cur.lastrowid
        conn.execute("INSERT INTO cash_advances(event_id,status,note) VALUES(?,?,?)", (event_id, "not_submitted", ""))
        conn.execute("INSERT INTO warehouse_checks(event_id,status) VALUES(?,?)", (event_id, "needs_prep"))
        conn.execute("INSERT INTO event_communications(event_id,status) VALUES(?,?)", (event_id, "not_created"))
        cur_task = conn.execute(
            "INSERT INTO design_tasks(event_id,title,status,assignee_id,due_at) VALUES(?,?,?,?,?)",
            (event_id, "Desain event", "brief_needed", uid["design@captureit.local"], starts),
        )
        if event_id == 1:
            conn.execute("UPDATE design_tasks SET status='in_progress' WHERE id=?", (cur_task.lastrowid,))
        elif event_id == 2:
            conn.execute("UPDATE design_tasks SET status='revision' WHERE id=?", (cur_task.lastrowid,))
        elif event_id == 3:
            conn.execute("UPDATE design_tasks SET status='client_review' WHERE id=?", (cur_task.lastrowid,))
        elif event_id == 5:
            conn.execute("UPDATE design_tasks SET status='approved' WHERE id=?", (cur_task.lastrowid,))

    conn.execute("UPDATE cash_advances SET status='approved',requested_by=?,requested_at=?,reviewed_by=?,reviewed_at=? WHERE event_id=1",
                 (uid["pic@captureit.local"], "2026-09-27T05:00:00+00:00", uid["head.finance@captureit.local"], "2026-09-27T08:00:00+00:00"))
    conn.execute("UPDATE cash_advances SET status='submitted',requested_by=?,requested_at=? WHERE event_id=2",
                 (uid["pic@captureit.local"], "2026-09-28T03:30:00+00:00"))
    conn.execute("UPDATE warehouse_checks SET status='ready',prepared_by=?,prepared_at=? WHERE event_id=1",
                 (uid["warehouse@captureit.local"], "2026-09-29T02:00:00+00:00"))
    conn.execute("UPDATE warehouse_checks SET status='preparing',prepared_by=?,prepared_at=? WHERE event_id=2",
                 (uid["warehouse.head@captureit.local"], "2026-09-29T03:00:00+00:00"))
    conn.execute("UPDATE warehouse_checks SET status='returned',returned_by=?,returned_at=? WHERE event_id=5",
                 (uid["warehouse@captureit.local"], "2026-09-20T15:00:00+00:00"))

    crew_pos = positions["Crew"]
    lead_pos = positions["Lead Crew"]
    pic_pos = positions["PIC Event"]
    assignments = [
        (1, "crew@captureit.local", "crew", lead_pos),
        (1, "naya@captureit.local", "crew", crew_pos),
        (1, "pic@captureit.local", "pic", pic_pos),
        (2, "crew@captureit.local", "crew", lead_pos),
        (2, "fikri@captureit.local", "crew", crew_pos),
        (2, "pic@captureit.local", "pic", pic_pos),
        (3, "naya@captureit.local", "crew", crew_pos),
        (3, "fikri@captureit.local", "crew", crew_pos),
        (3, "pic@captureit.local", "pic", pic_pos),
        (4, "crew@captureit.local", "crew", crew_pos),
        (4, "pic@captureit.local", "pic", pic_pos),
        (5, "crew@captureit.local", "crew", lead_pos),
        (5, "naya@captureit.local", "crew", crew_pos),
        (5, "pic@captureit.local", "pic", pic_pos),
    ]
    assignment_id = {}
    for event_id, email, kind, position_id in assignments:
        cur = conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type,position_id) VALUES(?,?,?,?)",
                           (event_id, uid[email], kind, position_id))
        assignment_id[(event_id, email)] = cur.lastrowid
        conn.execute("INSERT INTO attendance(assignment_id) VALUES(?)", (cur.lastrowid,))
    hologram_id = conn.execute("SELECT id FROM skills WHERE name='Hologram'").fetchone()["id"]
    conn.execute("INSERT INTO assignment_skills(assignment_id,skill_id,extra_fee_rupiah,skill_name_snapshot) VALUES(?,?,?,?)", (assignment_id[(1, "crew@captureit.local")], hologram_id, 50000, "Hologram"))
    conn.execute("UPDATE attendance SET status='checked_out',check_in_at=?,check_out_at=?,updated_by=? WHERE assignment_id IN (SELECT id FROM event_assignments WHERE event_id=5)",
                 ("2026-09-20T03:00:00+00:00", "2026-09-20T11:00:00+00:00", uid["head.ops@captureit.local"]))
    conn.execute("INSERT INTO kpi_reviews(reviewer_id,subject_id,event_id,category,score,note,review_period) VALUES(?,?,?,?,?,?,?)",
                 (uid["head.ops@captureit.local"], uid["coordinator@captureit.local"], 5, "Koordinasi event", 5, "Brief crew dan alur event berjalan rapi.", "2026-09"))
    conn.execute("UPDATE events SET completed_at=?,completed_by=? WHERE id=5",
                 ("2026-09-20T12:00:00+00:00", uid["coordinator@captureit.local"]))
    demo_performance = {
        "crew@captureit.local": ({"work_quality":5,"punctuality":4,"teamwork":5,"sop_equipment":4}, "Setup cepat dan rapi. Pertahankan komunikasi saat pergantian shift."),
        "naya@captureit.local": ({"work_quality":4,"punctuality":3,"teamwork":4,"sop_equipment":4}, "Hasil kerja baik. Perlu lebih tepat waktu saat persiapan awal."),
        "pic@captureit.local": ({"work_quality":4,"punctuality":5,"teamwork":4,"sop_equipment":4}, "Koordinasi dengan venue berjalan baik."),
    }
    for email, (scores, note) in demo_performance.items():
        insert_event_performance_review(conn, assignment_id[(5, email)], uid["coordinator@captureit.local"], scores, note)
    audit(conn, uid["admin@captureit.local"], "system", None, "demo_seeded", {"events": len(events)})


def initialize(seed: bool | None = None) -> None:
    with DB_LOCK, get_db() as conn:
        seed_reference_data(conn)
        migrate_user_fee_rates(conn)
        demo_enabled = DEMO_MODE if seed is None else seed
        if demo_enabled:
            seed_demo(conn)
        elif conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
            password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
            name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "Capture It Administrator").strip()
            if email and password:
                validate_password(password)
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise ValueError('BOOTSTRAP_ADMIN_EMAIL harus berupa alamat email yang valid.')
                cur = conn.execute("INSERT INTO users(full_name,email,password_hash,active) VALUES(?,?,?,1)", (name,email,password_hash(password)))
                role_id = conn.execute("SELECT id FROM roles WHERE code='administrator'").fetchone()["id"]
                conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (cur.lastrowid,role_id))
            else:
                print("Database kosong; atur BOOTSTRAP_ADMIN_EMAIL dan BOOTSTRAP_ADMIN_PASSWORD untuk membuat akun awal.", file=sys.stderr)
        conn.execute("""UPDATE users SET employment_type=CASE WHEN EXISTS(
            SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=users.id AND r.code IN ('crew','pic_event')
            ) THEN 'freelancer' ELSE 'inhouse' END""")
        departments = {"administrator":"Management","head_operations":"Operations","event_coordinator":"Operations",
            "head_finance":"Finance","finance":"Finance","admin_finance":"Finance","warehouse_head":"Warehouse",
            "warehouse_staff":"Warehouse","design_team":"Design","design_head":"Design","sales_staff":"Sales","content_team":"Content"}
        for role_code, department in departments.items():
            conn.execute("UPDATE users SET department=? WHERE department='' AND id IN (SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.code=?)", (department,role_code))
        migrate_history(conn)
        if not conn.execute("SELECT 1 FROM app_settings WHERE setting_key='notification_center_v1'").fetchone():
            # Establish a baseline without blasting existing reminders to devices on upgrade.
            collect_notifications(conn, push=False)
            conn.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES('notification_center_v1','1')")


def add_user(email: str, full_name: str, role_code: str, password: str) -> None:
    if role_code not in dict(ROLE_LIST):
        raise ValueError(f"Role tidak dikenal: {role_code}")
    validate_password(password)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email.strip()):
        raise ValueError('Masukkan alamat email yang valid.')
    if not 2 <= len(full_name.strip()) <= 100:
        raise ValueError('Nama harus terdiri dari 2–100 karakter.')
    employment, department = account_classification(role_code)
    with DB_LOCK, get_db() as conn:
        seed_reference_data(conn)
        cur = conn.execute("INSERT INTO users(full_name,email,password_hash,active,employment_type,department) VALUES(?,?,?,1,?,?)",
                           (full_name.strip(), email.strip().lower(), password_hash(password), employment, department))
        role_id = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()["id"]
        conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (cur.lastrowid, role_id))


def reset_user_password(email: str, password: str, actor_id: int | None = None) -> None:
    validate_password(password)
    normalized_email = email.strip().lower()
    with DB_LOCK, get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        target = conn.execute("SELECT id FROM users WHERE email=?", (normalized_email,)).fetchone()
        if not target:
            raise ValueError(f"Akun tidak ditemukan: {email}")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password), target["id"]))
        revoked = conn.execute('DELETE FROM sessions WHERE user_id=?', (target["id"],)).rowcount
        audit(conn, actor_id, 'user', target["id"], 'password_reset',
              {'email': normalized_email, 'sessions_revoked': revoked})


def role_data(conn: sqlite3.Connection, user_id: int) -> tuple[list[dict], set[str]]:
    roles = [dict(r) for r in conn.execute(
        "SELECT r.id,r.code,r.name FROM roles r JOIN user_roles ur ON ur.role_id=r.id WHERE ur.user_id=? ORDER BY r.name",
        (user_id,),
    ).fetchall()]
    permissions: set[str] = set()
    for role in roles:
        if role["code"] == "administrator":
            permissions.update(PERMISSION_DESCRIPTIONS)
            continue
        custom = conn.execute("SELECT customized FROM role_acl_settings WHERE role_id=?", (role["id"],)).fetchone()
        if custom and custom["customized"]:
            permissions.update(row["code"] for row in conn.execute(
                "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE rp.role_id=?",
                (role["id"],),
            ).fetchall())
        else:
            permissions.update(ROLE_PERMISSIONS.get(role["code"], set()))
    if any(role["code"] in FEE_RESTRICTED_ROLES for role in roles) and not any(role["code"] == "administrator" for role in roles):
        permissions.difference_update(FEE_RESTRICTED_PERMISSIONS)
    return roles, permissions


BRAND_DEFAULTS = {
    "primary": "#7353e8",
    "accent": "#ffb51b",
    "sidebar": "#21123b",
    "background": "#f5f3fa",
}


def branding_payload(conn: sqlite3.Connection) -> dict:
    values = {row["setting_key"]: row["setting_value"] for row in conn.execute(
        "SELECT setting_key,setting_value FROM app_settings WHERE setting_key LIKE 'brand_%'"
    ).fetchall()}
    colors = {key: values.get(f"brand_{key}", value) for key, value in BRAND_DEFAULTS.items()}
    updated = values.get("brand_updated_at", "default")
    return {
        "colors": colors,
        "logo_url": f"/api/branding/logo?v={updated}",
        "favicon_url": f"/api/branding/favicon?v={updated}",
    }


def configure_role_payload(conn: sqlite3.Connection) -> dict:
    rows = []
    for role in conn.execute("SELECT id,code,name FROM roles ORDER BY id").fetchall():
        if role["code"] == "administrator":
            codes = set(PERMISSION_DESCRIPTIONS)
            customized = False
        else:
            mode = conn.execute("SELECT customized FROM role_acl_settings WHERE role_id=?", (role["id"],)).fetchone()
            customized = bool(mode and mode["customized"])
            if customized:
                codes = {r["code"] for r in conn.execute(
                    "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE rp.role_id=?",
                    (role["id"],),
                ).fetchall()}
            else:
                codes = set(ROLE_PERMISSIONS.get(role["code"], set()))
            if role["code"] in FEE_RESTRICTED_ROLES:
                codes -= FEE_RESTRICTED_PERMISSIONS
        rows.append({"code": role["code"], "name": role["name"], "permissions": sorted(codes), "customized": customized})
    return {
        "roles": rows,
        "permissions": [{"code": code, "description": description} for code, description in PERMISSION_DESCRIPTIONS.items()],
    }


def google_configured() -> bool:
    return all(os.environ.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID"))


def google_sync_interval_seconds() -> int:
    """Return the configured interval; zero disables automatic synchronization."""
    try:
        hours = int(os.environ.get("GOOGLE_SYNC_INTERVAL_HOURS", "24"))
    except ValueError:
        hours = 24
    return max(0, hours) * 60 * 60


def google_sync_status(conn: sqlite3.Connection) -> dict:
    row = conn.execute("""SELECT last_attempt_at,last_success_at,status,trigger,message,
        queued_count,updated_count,skipped_count FROM google_calendar_sync_state WHERE id=1""").fetchone()
    if not row:
        return {"last_attempt_at": None, "last_success_at": None, "status": "never", "trigger": "",
                "message": "", "queued_count": 0, "updated_count": 0, "skipped_count": 0,
                "interval_hours": google_sync_interval_seconds() // 3600}
    return {**dict(row), "interval_hours": google_sync_interval_seconds() // 3600}


def google_sync_due(last_attempt_at: str | None, now: datetime | None = None,
                    interval_seconds: int | None = None) -> bool:
    interval = google_sync_interval_seconds() if interval_seconds is None else interval_seconds
    if interval <= 0:
        return False
    if not last_attempt_at:
        return True
    try:
        last = parse_iso(last_attempt_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - last).total_seconds() >= interval


def run_google_sync(trigger: str, actor_id: int | None = None) -> dict:
    """Run one serialized sync and persist its result for the UI and scheduler."""
    with GOOGLE_SYNC_LOCK:
        started_at = now_iso()
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO google_calendar_sync_state(id) VALUES (1)")
            previous = conn.execute("SELECT status FROM google_calendar_sync_state WHERE id=1").fetchone()
            previous_status = previous["status"] if previous else None
            conn.execute("""UPDATE google_calendar_sync_state SET last_attempt_at=?,status='running',
                trigger=?,message='Sinkronisasi sedang berjalan.' WHERE id=1""", (started_at, trigger))
        try:
            with get_db() as conn:
                result = google_sync(conn)
                if result.get("ok") and actor_id is not None:
                    audit(conn, actor_id, "google_calendar", None,
                          "synced" if trigger == "manual" else "auto_synced",
                          {key: result.get(key, 0) for key in ("queued", "queue_updated", "updated", "skipped")})
                finished_at = now_iso()
                conn.execute("""UPDATE google_calendar_sync_state SET status=?,
                    last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,
                    message=?,queued_count=?,updated_count=?,skipped_count=? WHERE id=1""",
                    ("success" if result.get("ok") else "error", int(bool(result.get("ok"))), finished_at,
                     result.get("message", "Sinkronisasi tidak berhasil."), result.get("queued", 0),
                     result.get("updated", 0) + result.get("queue_updated", 0), result.get("skipped", 0)))
                notices.sync_status(conn, bool(result.get("ok")), result.get("message", ""), previous_status)
        except Exception as exc:
            result = {"ok": False, "status": 502,
                      "message": f"Google Calendar belum dapat disinkronkan ({type(exc).__name__})."}
            try:
                with get_db() as conn:
                    conn.execute("UPDATE google_calendar_sync_state SET status='error',message=? WHERE id=1",
                                 (result["message"],))
                    notices.sync_status(conn, False, result["message"], previous_status)
            except sqlite3.Error:
                pass
        return result


def login_limits() -> tuple[int, int, int]:
    """(failures per email, failures per IP, lock/window seconds). Tunable through .env."""
    def number(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default
    return number("LOGIN_MAX_FAILURES", 5), number("LOGIN_IP_MAX_FAILURES", 30), number("LOGIN_LOCK_MINUTES", 15) * 60


def login_lock_remaining(conn: sqlite3.Connection, email: str, ip: str, now: float | None = None) -> int:
    """Seconds left on a login lock for this email or IP (0 = not locked).

    A lock is a sliding window: once the allowed number of failures has happened inside the window,
    sign-in stays blocked until the oldest of those failures ages out.
    """
    now = time.time() if now is None else now
    per_email, per_ip, window = login_limits()
    remaining = 0
    for column, value, limit in (("email", email, per_email), ("ip", ip, per_ip)):
        rows = conn.execute(f"SELECT attempted_at FROM login_attempts WHERE {column}=? AND attempted_at>? ORDER BY attempted_at DESC LIMIT ?",
                            (value, now - window, limit)).fetchall()
        if len(rows) >= limit:
            remaining = max(remaining, int(rows[limit - 1]["attempted_at"] + window - now) + 1)
    return remaining


def record_login_failure(conn: sqlite3.Connection, email: str, ip: str) -> None:
    now = time.time()
    conn.execute("DELETE FROM login_attempts WHERE attempted_at<?", (now - 86400,))
    conn.execute("INSERT INTO login_attempts(email,ip,attempted_at) VALUES(?,?,?)", (email, ip, now))
    per_email = login_limits()[0]
    if conn.execute("SELECT COUNT(*) FROM login_attempts WHERE email=? AND attempted_at>?", (email, now - login_limits()[2])).fetchone()[0] == per_email:
        audit(conn, None, "session", None, "login_locked", {"email": email[:120], "ip": ip})


def google_sync_scheduler() -> None:
    """Run an initial sync, then check once per minute for the daily due time."""
    while True:
        try:
            with GOOGLE_SYNC_LOCK:
                interval = google_sync_interval_seconds()
                if interval > 0 and google_configured():
                    with get_db() as conn:
                        row = conn.execute("SELECT last_attempt_at FROM google_calendar_sync_state WHERE id=1").fetchone()
                        last_attempt_at = row["last_attempt_at"] if row else None
                    if google_sync_due(last_attempt_at, interval_seconds=interval):
                        result = run_google_sync("automatic")
                        print(f"Google Calendar auto-sync: {result.get('message', 'selesai')}", flush=True)
        except Exception as exc:
            print(f"Google Calendar auto-sync error: {type(exc).__name__}", file=sys.stderr, flush=True)
        time.sleep(60)


def user_events(conn: sqlite3.Connection, user: dict) -> list[dict]:
    if not set(user['permissions']) & {'events.read_all', 'events.read_own'}:
        return []
    where = ""
    params: tuple = ()
    if "events.read_all" not in user["permissions"]:
        where = "WHERE EXISTS (SELECT 1 FROM event_assignments mine WHERE mine.event_id=e.id AND mine.user_id=?)"
        params = (user["id"],)
    rows = conn.execute(f"""
        SELECT e.id,e.project_code,e.operational_code,e.project_code_is_temporary,e.is_manual,e.title,e.event_type,e.starts_at,e.ends_at,e.location,e.is_full_day,e.status,
               u.full_name AS coordinator_name,e.coordinator_id,ca.updated_at AS advance_updated_at,
               eo.vehicle_id,eo.driver_name,eo.loading_date,eo.loading_time,eo.vehicle_return_at,
               eo.transport_modes,eo.courier_name,eo.other_transport,eo.updated_at AS logistics_updated_at,
               v.name AS vehicle_name,v.plate_number,v.vendor_name,
               COALESCE(ca.status,'not_submitted') AS advance_status,
               COALESCE(wc.status,'needs_prep') AS warehouse_status,
               COALESCE(ec.status,'not_created') AS group_status,
               COALESCE(dt.status,'brief_needed') AS design_status,dt.assignee_id AS design_assignee_id,
               (SELECT COUNT(*) FROM event_assignments ea WHERE ea.event_id=e.id AND ea.assignment_type='crew') AS crew_count,
               (SELECT GROUP_CONCAT(us.full_name, ', ') FROM event_assignments ea JOIN users us ON us.id=ea.user_id WHERE ea.event_id=e.id AND ea.assignment_type='crew') AS crew_names,
               (SELECT us.full_name FROM event_assignments ea JOIN users us ON us.id=ea.user_id WHERE ea.event_id=e.id AND ea.assignment_type='pic' LIMIT 1) AS pic_name,
               (SELECT ea.user_id FROM event_assignments ea WHERE ea.event_id=e.id AND ea.assignment_type='pic' LIMIT 1) AS pic_user_id
        FROM events e
        LEFT JOIN users u ON u.id=e.coordinator_id
        LEFT JOIN cash_advances ca ON ca.event_id=e.id
        LEFT JOIN warehouse_checks wc ON wc.event_id=e.id
        LEFT JOIN event_communications ec ON ec.event_id=e.id
        LEFT JOIN design_tasks dt ON dt.event_id=e.id
        LEFT JOIN event_operations eo ON eo.event_id=e.id
        LEFT JOIN vehicles v ON v.id=eo.vehicle_id
        {where}
        ORDER BY e.starts_at ASC
    """, params).fetchall()
    result = [dict(r) for r in rows]
    for event in result:
        event['transport_modes'] = transport_modes(event.get('transport_modes'), event.get('vehicle_id'))
        event['transport_label'] = transport_label(event)
    return result


def notification_user(conn, uid):
    row = conn.execute('SELECT id,active,employment_type FROM users WHERE id=? AND active=1', (uid,)).fetchone()
    if not row:
        return None
    roles, permissions = role_data(conn, uid)
    return {**dict(row), 'roles': roles, 'permissions': permissions}


def live_notifications(conn, user):
    return operational_notifications(conn, user, user_events(conn, user))['items']


def notifications_payload(conn, user, events=None, before=None):
    live = operational_notifications(conn, user, events if events is not None else user_events(conn, user))['items']
    return notices.inbox(conn, user, live, before)


def collect_notifications(conn, push=True):
    for row in conn.execute('SELECT id FROM users WHERE active=1').fetchall():
        user = notification_user(conn, row['id'])
        notices.sync_live(conn, user, live_notifications(conn, user), push=push)


def notification_scheduler():
    while True:
        try:
            with DB_LOCK, get_db() as conn:
                collect_notifications(conn)
            push_delivery.process_once(get_db, notification_user, live_notifications)
        except Exception as exc:
            print('Notification worker: '+type(exc).__name__, file=sys.stderr, flush=True)
        time.sleep(15)


def designers_for(conn, task, event):
    candidates = [task['assignee_id']] if task['assignee_id'] else [r['id'] for r in conn.execute('SELECT id FROM users WHERE active=1')]
    needed = {'design.read', 'design.update'} if task['assignee_id'] else {'design.read', 'design.update', 'design.read_all'}
    return [uid for uid in candidates if (u := notification_user(conn, uid)) and needed <= set(u['permissions']) and notices.can_event(conn, u, event['id'])]


def coordinator_event_export_rows(conn: sqlite3.Connection, user_id: int, start: str, end: str) -> list[dict]:
    start_date,end_exclusive=payroll_window(start,end)
    return [dict(r) for r in conn.execute("""SELECT e.project_code,e.operational_code,e.project_code_is_temporary,e.title,e.event_type,
        e.starts_at,e.ends_at,e.location,e.is_full_day,e.status,
        COALESCE((SELECT GROUP_CONCAT(u.full_name, ', ') FROM event_assignments a JOIN users u ON u.id=a.user_id
            WHERE a.event_id=e.id AND a.assignment_type='pic'),'') pic_names,
        COALESCE((SELECT GROUP_CONCAT(u.full_name, ', ') FROM event_assignments a JOIN users u ON u.id=a.user_id
            WHERE a.event_id=e.id AND a.assignment_type='crew'),'') crew_names,
        COALESCE(w.status,'needs_prep') warehouse_status,COALESCE(c.status,'not_created') group_status,
        COALESCE(d.status,'brief_needed') design_status,COALESCE(a.status,'not_submitted') advance_status
        FROM events e LEFT JOIN warehouse_checks w ON w.event_id=e.id
        LEFT JOIN event_communications c ON c.event_id=e.id LEFT JOIN design_tasks d ON d.event_id=e.id
        LEFT JOIN cash_advances a ON a.event_id=e.id
        WHERE e.coordinator_id=? AND substr(e.starts_at,1,10)>=? AND substr(e.starts_at,1,10)<?
        ORDER BY e.starts_at,e.project_code""",(user_id,start_date,end_exclusive)).fetchall()]


def advance_document_rows(conn: sqlite3.Connection,event_id: int | None = None) -> list[dict]:
    where="WHERE d.event_id=?" if event_id is not None else ""
    params=(event_id,) if event_id is not None else ()
    return [dict(row) for row in conn.execute(f"""SELECT d.id,d.event_id,d.original_filename,d.mime_type,d.file_size,d.uploaded_at,
        e.project_code,e.title event_title,e.starts_at,u.full_name uploader_name
        FROM cash_advance_documents d JOIN events e ON e.id=d.event_id JOIN users u ON u.id=d.uploaded_by
        {where} ORDER BY d.uploaded_at DESC""",params).fetchall()]


def event_detail(conn: sqlite3.Connection, event_id: int, viewer_id: int, can_review_team: bool = False,
                 can_read_advance_docs: bool = False) -> dict | None:
    event = conn.execute("""SELECT e.*,u.full_name AS coordinator_name,
        COALESCE(ca.status,'not_submitted') AS advance_status, ca.requested_by, ca.requested_at,
        ca.reviewed_by, ca.reviewed_at, ca.transfer_recorded_by, ca.transferred_at, ca.note AS advance_note,
        COALESCE(wc.status,'needs_prep') AS warehouse_status, wc.prepared_by, wc.prepared_at,
        wc.returned_by, wc.returned_at, wc.note AS warehouse_note,
        COALESCE(ec.status,'not_created') AS group_status, ec.group_link,
        COALESCE(dt.status,'brief_needed') AS design_status
        FROM events e LEFT JOIN users u ON u.id=e.coordinator_id
        LEFT JOIN cash_advances ca ON ca.event_id=e.id LEFT JOIN warehouse_checks wc ON wc.event_id=e.id
        LEFT JOIN event_communications ec ON ec.event_id=e.id
        LEFT JOIN design_tasks dt ON dt.event_id=e.id WHERE e.id=?""", (event_id,)).fetchone()
    if not event:
        return None
    result = dict(event)
    result["logistics"] = logistics_detail(conn,event_id)
    result["assignments"] = [dict(r) for r in conn.execute("""
        SELECT ea.id AS assignment_id,ea.user_id,ea.assignment_type,ea.position_id,u.full_name,u.email,
               p.name AS position_name, a.status AS attendance_status,a.check_in_at,a.check_out_at,
               a.check_in_latitude,a.check_in_longitude,a.check_in_accuracy_m,a.check_out_latitude,a.check_out_longitude,a.check_out_accuracy_m,
               a.corrected_at,CASE WHEN a.corrected_at IS NOT NULL THEN a.note END AS correction_note,
               CASE WHEN a.check_in_photo_path IS NOT NULL THEN 1 ELSE 0 END AS has_check_in_photo,
               CASE WHEN a.check_out_photo_path IS NOT NULL THEN 1 ELSE 0 END AS has_check_out_photo,
               GROUP_CONCAT(COALESCE(NULLIF(aks.skill_name_snapshot,''),s.name), ', ') AS skills,GROUP_CONCAT(s.id, ',') AS skill_ids
        FROM event_assignments ea JOIN users u ON u.id=ea.user_id
        LEFT JOIN positions p ON p.id=ea.position_id LEFT JOIN attendance a ON a.assignment_id=ea.id
        LEFT JOIN assignment_skills aks ON aks.assignment_id=ea.id LEFT JOIN skills s ON s.id=aks.skill_id
        WHERE ea.event_id=? GROUP BY ea.id ORDER BY CASE ea.assignment_type WHEN 'pic' THEN 0 ELSE 1 END,u.full_name
    """, (event_id,)).fetchall()]
    result["event_days"] = eventdays.event_dates(result)
    result["multi_day"] = eventdays.is_multiday(result)
    for row in result["assignments"]:
        row["days"] = eventdays.day_attendance(conn, result, row["assignment_id"]) if result["multi_day"] else []
        if result["multi_day"]:
            row["needs_action"] = any(day["needs_action"] for day in row["days"])
        else:
            row["needs_action"] = (row["attendance_status"] in ("not_started", "checked_in")
                                   and eventdays.event_dates(result)[-1] < eventdays.today_wib())
    result["attendance_needs_action"] = sum(1 for row in result["assignments"] if row["needs_action"])
    viewer_is_pic=any(row["user_id"]==viewer_id and row["assignment_type"]=="pic" for row in result["assignments"])
    result["advance_documents"] = advance_document_rows(conn,event_id) if can_read_advance_docs or viewer_is_pic else []
    visible_assignment_ids = [
        row["assignment_id"] for row in result["assignments"]
        if can_review_team or row["user_id"] == viewer_id
    ]
    performance = query_performance_reviews(conn, assignment_ids=visible_assignment_ids)
    by_assignment = {row["assignment_id"]: row for row in performance}
    for assignment in result["assignments"]:
        assignment["performance_review"] = by_assignment.get(assignment["assignment_id"])
    completer = conn.execute("SELECT full_name FROM users WHERE id=?", (result.get("completed_by"),)).fetchone() if result.get("completed_by") else None
    result["completed_by_name"] = completer["full_name"] if completer else None
    approved_design = conn.execute("SELECT final_url FROM design_tasks WHERE event_id=? AND status='approved' ORDER BY id LIMIT 1", (event_id,)).fetchone()
    result['approved_design_url'] = approved_design['final_url'] if approved_design else ''
    _, viewer_permissions = role_data(conn, viewer_id)
    if viewer_permissions & {'design.read', 'design.update'}:
        task = conn.execute('SELECT * FROM design_tasks WHERE event_id=? ORDER BY id LIMIT 1', (event_id,)).fetchone()
        result['design_task'] = dict(task) if task else None
        result['design_notes'] = [dict(r) for r in conn.execute('''SELECT c.body,c.created_at,u.full_name author_name FROM design_comments c
            LEFT JOIN users u ON u.id=c.author_id WHERE c.task_id=? ORDER BY c.id DESC LIMIT 20''', (task['id'],))] if task else []
        if task and 'design.read_all' not in viewer_permissions and task['assignee_id'] != viewer_id:
            # Team Design only sees the cards assigned to them
            result.update({'design_task': None, 'design_notes': [], 'design_restricted': True, 'approved_design_url': '', 'design_status': None})
    return result


def query_performance_reviews(conn: sqlite3.Connection, subject_ids: list[int] | None = None,
                              assignment_ids: list[int] | None = None) -> list[dict]:
    if subject_ids is not None:
        if not subject_ids:
            return []
        placeholders = ",".join("?" for _ in subject_ids)
        filter_sql, params = f"WHERE ea.user_id IN ({placeholders})", tuple(subject_ids)
    elif assignment_ids is not None:
        if not assignment_ids:
            return []
        placeholders = ",".join("?" for _ in assignment_ids)
        filter_sql, params = f"WHERE ea.id IN ({placeholders})", tuple(assignment_ids)
    else:
        filter_sql, params = "", ()
    rows = [dict(r) for r in conn.execute(f"""
        SELECT epr.id,epr.assignment_id,epr.reviewer_id,epr.score_percent,epr.note,epr.created_at,
               ea.user_id AS subject_id,ea.assignment_type,u.full_name AS subject_name,
               reviewer.full_name AS reviewer_name,e.id AS event_id,e.project_code,e.title AS event_title,
               e.starts_at,e.location
        FROM event_performance_reviews epr
        JOIN event_assignments ea ON ea.id=epr.assignment_id
        JOIN events e ON e.id=ea.event_id
        JOIN users u ON u.id=ea.user_id
        JOIN users reviewer ON reviewer.id=epr.reviewer_id
        {filter_sql}
        ORDER BY e.starts_at DESC,epr.created_at DESC
    """, params).fetchall()]
    if rows:
        review_ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in review_ids)
        items = conn.execute(f"""
            SELECT review_id,criterion_code,criterion_name,score
            FROM event_performance_items WHERE review_id IN ({placeholders})
            ORDER BY criterion_code
        """, tuple(review_ids)).fetchall()
        by_review = {row["id"]: [] for row in rows}
        for item in items:
            by_review[item["review_id"]].append({
                "code": item["criterion_code"],
                "name": item["criterion_name"],
                "score": item["score"],
            })
        for row in rows:
            row["criteria"] = by_review[row["id"]]
    return rows


def user_summary(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT id,full_name,email,employment_type,department FROM users WHERE id=?", (user_id,)).fetchone()
    roles, permissions = role_data(conn, user_id)
    return {**dict(row), "roles": roles, "role_names": [r["name"] for r in roles], "permissions": sorted(permissions)}


def user_profile_summary(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("""SELECT phone,profile_photo_path,ktp_front_path,ktp_back_path,updated_at
        FROM user_profiles WHERE user_id=?""", (user_id,)).fetchone()
    profile = dict(row) if row else {"phone":"","profile_photo_path":None,"ktp_front_path":None,"ktp_back_path":None,"updated_at":None}
    return {
        "phone": profile["phone"],
        "profile_photo_url": f"/api/profile-file/{user_id}/photo" if profile["profile_photo_path"] else None,
        "ktp_front_url": f"/api/profile-file/{user_id}/ktp_front" if profile["ktp_front_path"] else None,
        "ktp_back_url": f"/api/profile-file/{user_id}/ktp_back" if profile["ktp_back_path"] else None,
        "updated_at": profile["updated_at"],
    }


def staff_directory(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    if "staff.directory.read" not in viewer["permissions"]:
        return []
    rows = conn.execute("""SELECT u.id,u.full_name,u.email,u.active,COALESCE(up.phone,'') AS phone,
        COALESCE(GROUP_CONCAT(DISTINCT r.name),'') AS role_names,
        MAX(CASE WHEN r.code='crew' THEN 1 ELSE 0 END) AS is_crew,
        MAX(CASE WHEN r.code='pic_event' THEN 1 ELSE 0 END) AS is_pic,
        up.profile_photo_path,up.ktp_front_path,up.ktp_back_path
        FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id
        LEFT JOIN user_profiles up ON up.user_id=u.id
        WHERE u.active=1 AND r.code IN ('crew','pic_event')
        GROUP BY u.id ORDER BY u.full_name""").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        user_id = item["id"]
        item["photo_url"] = f"/api/profile-file/{user_id}/photo" if item.pop("profile_photo_path") else None
        item["has_ktp_front"] = bool(item.pop("ktp_front_path"))
        item["has_ktp_back"] = bool(item.pop("ktp_back_path"))
        if item["is_crew"] and item["is_pic"]:
            item["staff_type"] = "Crew / PIC"
        elif item["is_pic"]:
            item["staff_type"] = "PIC Event"
        else:
            item["staff_type"] = "Crew"
        result.append(item)
    return result


def dashboard_data(conn: sqlite3.Connection, user: dict, events: list[dict]) -> dict:
    today = datetime.now(WIB).date()
    upcoming = [e for e in events if e["status"] == "scheduled" and local_datetime(e["starts_at"]).date() >= today]
    return {
        "total_upcoming": len(upcoming),
        "needs_advance": sum(e["advance_status"] in {"submitted", "approved"} for e in upcoming) if "advances.read" in user["permissions"] else 0,
        "design_active": sum(e["design_status"] in {"brief_needed", "in_progress", "client_review", "revision"}
                             and ("design.read_all" in user["permissions"] or e.get("design_assignee_id") == user["id"]) for e in upcoming)
                         if "design.read" in user["permissions"] else 0,
        "warehouse_pending": sum(e["warehouse_status"] in {"needs_prep", "preparing", "waiting_return", "issue"} for e in upcoming) if "warehouse.update" in user["permissions"] else 0,
        "checked_events": sum(e["status"] == "completed" for e in events),
    }


def bootstrap_payload(conn: sqlite3.Connection, user: dict) -> dict:
    events = user_events(conn, user)
    advance_documents = advance_document_rows(conn) if "advances.documents.read_all" in user["permissions"] else []
    can_see_team = bool(user["permissions"] & {"kpi.evaluate_crew", "kpi.evaluate_operations", "kpi.evaluate_finance", "users.manage"})
    employees = []
    if can_see_team:
        employee_filter = "" if "users.manage" in user["permissions"] else "WHERE active=1"
        for row in conn.execute(f"SELECT id,full_name,email,active FROM users {employee_filter} ORDER BY full_name").fetchall():
            roles, _ = role_data(conn, row["id"])
            employees.append({**dict(row), "roles": roles, "role_names": [x["name"] for x in roles]})
    role_options = [{"code": code, "name": name} for code, name in ROLE_LIST] if "users.manage" in user["permissions"] else []
    calendar_code_queue = [dict(r) for r in conn.execute("""
        SELECT id,google_event_id,suggested_project_code,title,event_type,starts_at,ends_at,location,is_full_day,status
        FROM calendar_code_queue WHERE status!='cancelled' ORDER BY starts_at
    """).fetchall()] if ("events.project_code.manage" in user["permissions"] or "events.assign" in user["permissions"]) else []
    tasks = [dict(r) for r in conn.execute("""
        SELECT dt.id,dt.event_id,dt.title,dt.status,dt.due_at,dt.assignee_id,dt.brief_text,dt.brief_version,dt.brief_sent_at,dt.final_url,u.full_name AS assignee_name,
               e.project_code,e.title AS event_title,e.starts_at,e.location
        FROM design_tasks dt JOIN events e ON e.id=dt.event_id LEFT JOIN users u ON u.id=dt.assignee_id
        ORDER BY CASE dt.status WHEN 'brief_needed' THEN 1 WHEN 'in_progress' THEN 2 WHEN 'client_review' THEN 3 WHEN 'revision' THEN 4 ELSE 5 END, dt.due_at
    """).fetchall()] if "design.read" in user["permissions"] else []
    tasks = [task for task in tasks if notices.can_event(conn, user, task['event_id'])]
    if "design.read_all" not in user["permissions"]:
        tasks = [task for task in tasks if task["assignee_id"] == user["id"]]
    rates = []
    positions = [dict(r) for r in conn.execute("SELECT id,name FROM positions WHERE active=1 ORDER BY id").fetchall()]
    skills = [dict(r) for r in conn.execute("SELECT id,name,extra_fee_rupiah FROM skills WHERE active=1 ORDER BY name").fetchall()] if ("skills.manage" in user["permissions"] or "events.assign" in user["permissions"]) else []
    if "fees.manage" in user["permissions"]:
        rates = [dict(r) for r in conn.execute("""
            SELECT u.id AS user_id,u.full_name,u.email,
                   COALESCE((SELECT fr.base_fee_rupiah FROM user_fee_rates fr WHERE fr.user_id=u.id AND fr.effective_from<=date('now','+7 hours') ORDER BY fr.effective_from DESC LIMIT 1),0) AS base_fee_rupiah,
                   COALESCE((SELECT fr.effective_from FROM user_fee_rates fr WHERE fr.user_id=u.id AND fr.effective_from<=date('now','+7 hours') ORDER BY fr.effective_from DESC LIMIT 1),'') AS effective_from,
                   COALESCE((SELECT group_concat(r.name, ', ') FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id),'') AS role_names
            FROM users u WHERE u.active=1 AND u.employment_type='freelancer' ORDER BY u.full_name
        """).fetchall()]
    review_subjects = []
    reviews = []
    performance_reviews = []
    review_permissions = user["permissions"] & {"kpi.read", "kpi.read_own"}
    if review_permissions:
        if "users.manage" in user["permissions"]:
            review_subjects = [dict(r) for r in conn.execute("""
                SELECT u.id,u.full_name,COALESCE((SELECT group_concat(r.name, ', ') FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id),'') AS role_names
                FROM users u WHERE u.active=1 ORDER BY u.full_name
            """).fetchall()]
        else:
            target_codes = set()
            if "kpi.evaluate_crew" in user["permissions"]:
                target_codes.update(("crew", "pic_event"))
            if "kpi.evaluate_operations" in user["permissions"]:
                target_codes.update(("event_coordinator", "warehouse_head", "warehouse_staff"))
            if "kpi.evaluate_finance" in user["permissions"]:
                target_codes.add("admin_finance")
            if target_codes:
                placeholders = ",".join("?" for _ in target_codes)
                review_subjects = [dict(r) for r in conn.execute(f"""
                    SELECT DISTINCT u.id,u.full_name,COALESCE((SELECT group_concat(r.name, ', ') FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id),'') AS role_names
                    FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles target_role ON target_role.id=ur.role_id
                    WHERE u.active=1 AND target_role.code IN ({placeholders}) ORDER BY u.full_name
                """, tuple(sorted(target_codes))).fetchall()]
            elif "kpi.read_own" in user["permissions"]:
                review_subjects = [dict(r) for r in conn.execute("""
                    SELECT u.id,u.full_name,COALESCE((SELECT group_concat(r.name, ', ') FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id),'') AS role_names
                    FROM users u WHERE u.id=? AND u.active=1
                """, (user["id"],)).fetchall()]
        subject_ids = [row["id"] for row in review_subjects]
        if subject_ids:
            placeholders = ",".join("?" for _ in subject_ids)
            reviews = [dict(r) for r in conn.execute(f"""
            SELECT kr.id,kr.subject_id,kr.score,kr.category,kr.note,kr.review_period,kr.created_at,
                   reviewer.full_name AS reviewer_name,subject.full_name AS subject_name,e.project_code
            FROM kpi_reviews kr JOIN users reviewer ON reviewer.id=kr.reviewer_id
            JOIN users subject ON subject.id=kr.subject_id LEFT JOIN events e ON e.id=kr.event_id
            WHERE kr.subject_id IN ({placeholders}) ORDER BY kr.created_at DESC
        """, tuple(subject_ids)).fetchall()]
            role_codes = {role["code"] for role in user["roles"]}
            can_read_team_performance = "users.manage" in user["permissions"] or "kpi.evaluate_crew" in user["permissions"]
            if can_read_team_performance:
                performance_subject_ids = [row["id"] for row in review_subjects]
            elif "kpi.read_own" in user["permissions"] and role_codes & {"crew", "pic_event"}:
                performance_subject_ids = [user["id"]]
            else:
                performance_subject_ids = []
            performance_reviews = query_performance_reviews(conn, subject_ids=performance_subject_ids)
    return {
        "user": user_summary(conn, user["id"]),
        "draft_namespace": conn.execute("SELECT setting_value FROM app_settings WHERE setting_key='draft_namespace'").fetchone()[0],
        "profile": user_profile_summary(conn, user["id"]),
        "branding": branding_payload(conn),
        "configure": configure_role_payload(conn) if "app.configure" in user["permissions"] else None,
        "staff_directory": staff_directory(conn, user),
        "events": events,
        "vehicles": fleet_rows(conn) if "events.assign" in user["permissions"] else [],
        "notifications": notifications_payload(conn,user,events),
        "advance_documents": advance_documents,
        "positions": positions,
        "skills": skills,
        "dashboard": dashboard_data(conn, user, events),
        "design_tasks": tasks,
        "inhouse_schedule": inhouse_schedule(conn),
        "designers": [{'id': r['id'], 'full_name': r['full_name']} for r in conn.execute('SELECT id,full_name FROM users WHERE active=1 ORDER BY full_name')
                      if {'design.read','design.update'} <= role_data(conn, r['id'])[1]] if 'design.assign' in user['permissions'] else [],
        "rates": rates,
        "employees": employees,
        "roles": role_options,
        "calendar_code_queue": calendar_code_queue,
        "kpi_review_subjects": review_subjects,
        "kpi_reviews": reviews,
        "performance_reviews": performance_reviews,
        "performance_criteria": [{"code": code, "name": name} for code, name in PERFORMANCE_CRITERIA],
        "google_configured": google_configured(),
        "google_sync": google_sync_status(conn) if "google.sync" in user["permissions"] else None,
    }


def payroll_window(period: str, end_date: str | None = None) -> tuple[str, str]:
    """Return an inclusive payroll date range; accept legacy YYYY-MM periods."""
    if end_date is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
            raise ValueError("Periode harus berupa rentang tanggal yang valid.")
        try:
            start = date.fromisoformat(period)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("Periode harus berupa rentang tanggal yang valid.") from exc
        if start > end:
            raise ValueError("Tanggal awal tidak boleh melewati tanggal akhir.")
        return start.isoformat(), (end + timedelta(days=1)).isoformat()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", period):
            year, month = map(int, period.split("-"))
            start = date(year, month, 1)
            end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            return start.isoformat(), end.isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError("Periode harus berupa bulan YYYY-MM atau rentang tanggal yang valid.") from exc
    raise ValueError("Periode harus berupa bulan YYYY-MM atau rentang tanggal yang valid.")


def payroll_month_keys(start: str, end: str) -> list[str]:
    first=date.fromisoformat(start).replace(day=1)
    last=date.fromisoformat(end)
    keys=[]
    while first<=last:
        keys.append(first.strftime("%Y-%m"))
        first=(first.replace(day=28)+timedelta(days=4)).replace(day=1)
    return keys


def payroll_rows(conn: sqlite3.Connection, period: str, end_date: str | None = None) -> list[dict]:
    start, end_exclusive = payroll_window(period, end_date)
    records = conn.execute("""
        SELECT ea.id AS assignment_id,e.id AS event_id,e.project_code,e.title AS event_title,e.starts_at,e.is_full_day,
               u.id AS user_id,u.full_name,u.email,p.id AS position_id,p.name AS position_name,
               a.status AS attendance_status,a.check_in_at,a.check_out_at,
               COALESCE(ea.base_fee_snapshot_rupiah,(SELECT fr.base_fee_rupiah FROM user_fee_rates fr WHERE fr.user_id=u.id AND fr.effective_from<=wib_date(e.starts_at) ORDER BY fr.effective_from DESC LIMIT 1),0) AS base_fee,
               COALESCE((SELECT SUM(aks.extra_fee_rupiah) FROM assignment_skills aks WHERE aks.assignment_id=ea.id),0) AS skill_fee,
               COALESCE((SELECT GROUP_CONCAT(COALESCE(NULLIF(aks.skill_name_snapshot,''),s.name), ', ') FROM assignment_skills aks JOIN skills s ON s.id=aks.skill_id WHERE aks.assignment_id=ea.id), '') AS skill_names
        FROM event_assignments ea JOIN events e ON e.id=ea.event_id JOIN users u ON u.id=ea.user_id
        LEFT JOIN positions p ON p.id=ea.position_id JOIN attendance a ON a.assignment_id=ea.id
        WHERE wib_date(e.starts_at)>=? AND wib_date(e.starts_at)<? AND e.status!='cancelled' AND a.status='checked_out' AND u.employment_type='freelancer'
          AND NOT EXISTS (SELECT 1 FROM payroll_assignment_claims pc WHERE pc.assignment_id=ea.id)
        ORDER BY u.full_name,e.starts_at
    """, (start, end_exclusive)).fetchall()
    rows = []
    for rec in records:
        item = dict(rec)
        days = eventdays.worked_days(conn, item["assignment_id"])
        item["work_days"] = days
        item["base_fee"] *= days
        item["skill_fee"] *= days
        item["meal_fee"] = (35000 if item["is_full_day"] else 25000) * days
        item["fee"] = item["base_fee"] + item["skill_fee"]
        item["total"] = item["base_fee"] + item["meal_fee"] + item["skill_fee"]
        rows.append(item)
    return rows


INHOUSE_SCHEDULE_DEFAULT = {"work_start": "09:00", "work_end": "18:00", "late_grace_minutes": 0, "early_grace_minutes": 0}


def inhouse_schedule(conn: sqlite3.Connection) -> dict:
    """Office hours used to flag late arrival / early departure. Stored in app_settings, editable in Configure."""
    schedule = dict(INHOUSE_SCHEDULE_DEFAULT)
    for key in schedule:
        row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (f"inhouse_{key}",)).fetchone()
        if row:
            schedule[key] = row[0]
    for key in ("late_grace_minutes", "early_grace_minutes"):
        try:
            schedule[key] = int(schedule[key])
        except (TypeError, ValueError):
            schedule[key] = 0
    return schedule


def validate_inhouse_schedule(payload: dict) -> dict:
    def clock(name: str, label: str) -> str:
        value = str(payload.get(name, "")).strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"{label} harus berformat JJ:MM (24 jam).")
        return value
    def grace(name: str, label: str) -> int:
        value = payload.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().lstrip("-").isdigit():
            raise ValueError(f"{label} harus berupa angka menit.")
        number = int(value)
        if not 0 <= number <= 120:
            raise ValueError(f"{label} harus antara 0 dan 120 menit.")
        return number
    start, end = clock("work_start", "Jam masuk"), clock("work_end", "Jam pulang")
    if end <= start:
        raise ValueError("Jam pulang harus setelah jam masuk.")
    return {"work_start": start, "work_end": end,
            "late_grace_minutes": grace("late_grace_minutes", "Toleransi terlambat"),
            "early_grace_minutes": grace("early_grace_minutes", "Toleransi pulang awal")}


def wib_now() -> datetime:
    """Current time in WIB. One place for office-hours logic to read the clock (tests replace this, not datetime)."""
    return datetime.now(timezone(timedelta(hours=7)))


def scheduled_moment(work_date: str, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.fromisoformat(work_date).replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=timezone(timedelta(hours=7)))


def punctuality_note(late, early, late_grace: int = 0, early_grace: int = 0) -> str:
    """Human wording used in the app and in exports. None minutes = recorded before office hours existed."""
    parts = []
    if late is not None and late > late_grace:
        parts.append(f"Terlambat {late} menit")
    if early is not None and early > early_grace:
        parts.append(f"Pulang lebih awal {early} menit")
    return " · ".join(parts) if parts else ("Tepat waktu" if late is not None or early is not None else "")


def inhouse_attendance_rows(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    start_date,end_exclusive = payroll_window(start,end)
    return [dict(row) for row in conn.execute("""SELECT a.work_date,u.full_name,u.email,u.department,a.status,a.check_in_at,a.check_out_at,
        a.check_in_latitude,a.check_in_longitude,a.check_in_accuracy_m,a.check_out_latitude,a.check_out_longitude,a.check_out_accuracy_m,
        a.scheduled_start,a.scheduled_end,a.late_minutes,a.early_leave_minutes,
        CASE WHEN a.check_in_photo_path IS NOT NULL THEN 'Ya' ELSE 'Tidak' END AS check_in_photo,
        CASE WHEN a.check_out_photo_path IS NOT NULL THEN 'Ya' ELSE 'Tidak' END AS check_out_photo
        FROM inhouse_attendance a JOIN users u ON u.id=a.user_id
        WHERE u.active=1 AND u.employment_type='inhouse' AND a.work_date>=? AND a.work_date<?
        ORDER BY a.work_date,u.department,u.full_name""", (start_date,end_exclusive)).fetchall()]


INHOUSE_EXPORT_HEADERS = ["Tanggal", "Nama", "Email", "Departemen", "Status", "Jadwal masuk", "Check-in (WIB)", "Terlambat (menit)",
                          "Jadwal pulang", "Check-out (WIB)", "Pulang lebih awal (menit)", "Keterangan", "Foto masuk", "Foto pulang",
                          "GPS masuk latitude", "GPS masuk longitude", "Akurasi masuk (m)", "GPS pulang latitude", "GPS pulang longitude", "Akurasi pulang (m)"]


def inhouse_export_row(row: dict, schedule: dict) -> list:
    """One export line. Minutes/time are the values captured at check time, so later changes to office hours never rewrite history."""
    status = {"not_started": "Belum absen", "checked_in": "Sudah check-in", "checked_out": "Selesai"}.get(row["status"], row["status"])
    late, early = row.get("late_minutes"), row.get("early_leave_minutes")
    return [row["work_date"], row["full_name"], row["email"], row["department"], status,
            row.get("scheduled_start") or "", payroll_timestamp_wib(row["check_in_at"]), late if late is not None else "",
            row.get("scheduled_end") or "", payroll_timestamp_wib(row["check_out_at"]), early if early is not None else "",
            punctuality_note(late, early, schedule["late_grace_minutes"], schedule["early_grace_minutes"]),
            row["check_in_photo"], row["check_out_photo"], row["check_in_latitude"], row["check_in_longitude"], row["check_in_accuracy_m"],
            row["check_out_latitude"], row["check_out_longitude"], row["check_out_accuracy_m"]]


def make_inhouse_attendance_xlsx(rows: list[dict], schedule: dict | None = None) -> bytes:
    schedule = schedule or dict(INHOUSE_SCHEDULE_DEFAULT)
    headers = INHOUSE_EXPORT_HEADERS
    data = [inhouse_export_row(row, schedule) for row in rows]
    def col(n):
        result=""
        while n:
            n,rem=divmod(n-1,26); result=chr(65+rem)+result
        return result
    def safe(value):
        return escape("".join(ch for ch in str(value or "") if ch in "\t\n\r" or ord(ch)>=32))
    sheet_rows=[]
    for rn,row in enumerate([headers,*data],1):
        cells="".join(f'<c r="{col(cn)}{rn}" t="inlineStr"><is><t xml:space="preserve">{safe(value)}</t></is></c>' for cn,value in enumerate(row,1))
        sheet_rows.append(f'<row r="{rn}">{cells}</row>')
    content={"[Content_Types].xml":'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Absensi In-house" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml":f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:{col(len(headers))}{max(1,len(data)+1)}"/><sheetData>{"".join(sheet_rows)}</sheetData><autoFilter ref="A1:{col(len(headers))}{max(1,len(data)+1)}"/></worksheet>'}
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,value in content.items(): archive.writestr(name,value)
    return output.getvalue()


def make_table_xlsx(headers: list[str], data: list[list], sheet_name: str) -> bytes:
    def col(n):
        result=""
        while n:
            n,rem=divmod(n-1,26); result=chr(65+rem)+result
        return result
    def safe(value):
        return escape("".join(ch for ch in str(value or "") if ch in "\t\n\r" or ord(ch)>=32))
    rows=[]
    for rn,row in enumerate([headers,*data],1):
        cells="".join(f'<c r="{col(cn)}{rn}" t="inlineStr"><is><t xml:space="preserve">{safe(value)}</t></is></c>' for cn,value in enumerate(row,1))
        rows.append(f'<row r="{rn}">{cells}</row>')
    last=col(len(headers)); end_row=max(1,len(data)+1)
    content={"[Content_Types].xml":'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":f'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{safe(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml":f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:{last}{end_row}"/><sheetData>{"".join(rows)}</sheetData><autoFilter ref="A1:{last}{end_row}"/></worksheet>'}
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,value in content.items(): archive.writestr(name,value)
    return output.getvalue()


def default_inhouse_payroll_period(today: date | None = None) -> tuple[str,str,str]:
    today=today or datetime.now(timezone(timedelta(hours=7))).date()
    first_this=today.replace(day=1); period_end=first_this-timedelta(days=1)
    return period_end.replace(day=1).isoformat(),period_end.isoformat(),today.replace(day=25).isoformat()


def inhouse_payroll_payload(conn: sqlite3.Connection, start: str, end: str) -> dict:
    start_date,end_exclusive=payroll_window(start,end); end_date=(date.fromisoformat(end_exclusive)-timedelta(days=1)).isoformat()
    batch=conn.execute("SELECT * FROM inhouse_payroll_batches WHERE period_start=? AND period_end=?",(start_date,end_date)).fetchone()
    attendance={row["user_id"]:dict(row) for row in conn.execute("""SELECT user_id,COUNT(*) attendance_days,
        SUM(CASE WHEN status='checked_out' THEN 1 ELSE 0 END) completed_days,
        SUM(CASE WHEN status='checked_in' THEN 1 ELSE 0 END) open_days FROM inhouse_attendance
        WHERE work_date>=? AND work_date<? GROUP BY user_id""",(start_date,end_exclusive)).fetchall()}
    accounts=[dict(row) for row in conn.execute("""SELECT u.id user_id,u.full_name,u.email,u.department,
        COALESCE(r.monthly_salary_rupiah,0) current_salary_rupiah FROM users u LEFT JOIN inhouse_salary_rates r ON r.user_id=u.id
        WHERE u.active=1 AND u.employment_type='inhouse' ORDER BY u.department,u.full_name""").fetchall()]
    for account in accounts:
        account['current_salary_rupiah'] = salary_at(conn, account['user_id'], datetime.now(WIB).date().isoformat())
        account['period_salary_rupiah'] = salary_at(conn, account['user_id'], start_date)
    if batch:
        rows=[dict(row) for row in conn.execute("""SELECT id payout_id,user_id,user_name_snapshot full_name,email_snapshot email,
            department_snapshot department,monthly_salary_rupiah,attendance_days,completed_days,open_days,
            allowance_rupiah,deduction_rupiah,total_rupiah,note,status,transferred_at,transfer_reference
            FROM inhouse_payroll_payouts WHERE batch_id=? ORDER BY department_snapshot,user_name_snapshot""",(batch["id"],)).fetchall()]
    else:
        rows=[]
        for account in accounts:
            stat=attendance.get(account["user_id"],{})
            rows.append({**account,"payout_id":None,"monthly_salary_rupiah":account["period_salary_rupiah"],
                "attendance_days":stat.get("attendance_days",0) or 0,"completed_days":stat.get("completed_days",0) or 0,
                "open_days":stat.get("open_days",0) or 0,"allowance_rupiah":0,"deduction_rupiah":0,
                "total_rupiah":account["period_salary_rupiah"],"note":"","status":"pending","transferred_at":None,"transfer_reference":""})
    _,_,default_pay_date=default_inhouse_payroll_period()
    return {"period_start":start_date,"period_end":end_date,"pay_date":batch["pay_date"] if batch else (default_pay_date if (start_date,end_date)==default_inhouse_payroll_period()[:2] else end_date),
        "batch_id":batch["id"] if batch else None,"batch_status":batch["status"] if batch else None,"rows":rows,"salary_accounts":accounts,
        "total_rupiah":sum(row["total_rupiah"] for row in rows)}


def inhouse_payroll_slips(conn: sqlite3.Connection,user_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute("""SELECT p.*,b.period_start,b.period_end,b.pay_date FROM inhouse_payroll_payouts p
        JOIN inhouse_payroll_batches b ON b.id=p.batch_id WHERE p.user_id=? AND p.status='transferred'
        ORDER BY b.period_end DESC,p.id DESC""",(user_id,)).fetchall()]


def freelancer_payroll_slips(conn: sqlite3.Connection, user_id: int, selected_year: str = "") -> dict:
    base = conn.execute("""SELECT DISTINCT substr(pb.period,1,4) year FROM payroll_batches pb
        JOIN payroll_batch_transfers bt ON bt.batch_id=pb.id
        JOIN payroll_lines pl ON pl.batch_id=pb.id JOIN event_assignments ea ON ea.id=pl.assignment_id
        WHERE ea.user_id=? ORDER BY year DESC""", (user_id,)).fetchall()
    years = [row["year"] for row in base]
    year = "all" if selected_year == "all" else (selected_year if selected_year else (years[0] if years else ""))
    rows = conn.execute("""SELECT pb.id batch_id,pb.period,btr.pay_date,btr.transfer_reference,btr.recorded_at,
        e.project_code,e.title event_title,e.starts_at,pl.base_fee_rupiah,pl.meal_rupiah,pl.skill_fee_rupiah,
        pl.adjustment_rupiah,pl.total_rupiah,
        COALESCE((SELECT GROUP_CONCAT(COALESCE(NULLIF(aks.skill_name_snapshot,''),s.name), ', ')
            FROM assignment_skills aks JOIN skills s ON s.id=aks.skill_id WHERE aks.assignment_id=ea.id),'') skill_names
        FROM payroll_lines pl JOIN payroll_batches pb ON pb.id=pl.batch_id
        JOIN payroll_batch_transfers btr ON btr.batch_id=pb.id
        JOIN event_assignments ea ON ea.id=pl.assignment_id JOIN events e ON e.id=ea.event_id
        WHERE ea.user_id=? AND (?='all' OR substr(pb.period,1,4)=?)
        ORDER BY pb.period DESC,e.starts_at""", (user_id, year, year)).fetchall() if year else []
    slips = {}
    for row in rows:
        item = dict(row); batch_id = item["batch_id"]
        slip = slips.setdefault(batch_id, {"batch_id":batch_id,"period":item["period"],"pay_date":item["pay_date"],
            "transfer_reference":item["transfer_reference"],"transferred_at":item["recorded_at"],"rows":[],"total_rupiah":0})
        slip["rows"].append(item); slip["total_rupiah"] += item["total_rupiah"]
    return {"years":years,"selected_year":year,"slips":list(slips.values())}


def create_payroll_export(conn: sqlite3.Connection, period: str, actor_id: int, end_date: str | None = None) -> tuple[list[dict], int]:
    period_key = f"{period}..{end_date}" if end_date else period
    batch = conn.execute("SELECT id,status FROM payroll_batches WHERE period=?", (period_key,)).fetchone()
    if batch and batch["status"] == "exported":
        existing = [dict(r) for r in conn.execute("""
            SELECT pl.assignment_id,pl.base_fee_rupiah AS base_fee,pl.meal_rupiah AS meal_fee,
                   pl.skill_fee_rupiah AS skill_fee,pl.total_rupiah AS total,
                   (pl.base_fee_rupiah+pl.skill_fee_rupiah) AS fee,
                   u.full_name,u.email,e.project_code,e.title AS event_title,e.starts_at,e.is_full_day,p.name AS position_name,
                   a.status AS attendance_status,a.check_in_at,a.check_out_at,
                   COALESCE((SELECT GROUP_CONCAT(COALESCE(NULLIF(aks.skill_name_snapshot,''),s.name), ', ') FROM assignment_skills aks JOIN skills s ON s.id=aks.skill_id WHERE aks.assignment_id=ea.id), '') AS skill_names
            FROM payroll_lines pl JOIN event_assignments ea ON ea.id=pl.assignment_id JOIN users u ON u.id=ea.user_id
            JOIN events e ON e.id=ea.event_id LEFT JOIN positions p ON p.id=ea.position_id
            JOIN attendance a ON a.assignment_id=ea.id
            WHERE pl.batch_id=? ORDER BY u.full_name,e.starts_at
        """, (batch["id"],)).fetchall()]
        return existing, batch["id"]
    if not batch:
        cur = conn.execute("INSERT INTO payroll_batches(period,status,created_by) VALUES(?,'draft',?)", (period_key, actor_id))
        batch_id = cur.lastrowid
    else:
        batch_id = batch["id"]
        conn.execute("DELETE FROM payroll_assignment_claims WHERE batch_id=?",(batch_id,))
    conn.execute("DELETE FROM payroll_lines WHERE batch_id=?", (batch_id,))
    rows = payroll_rows(conn, period, end_date)
    payable=[]
    for row in rows:
        claim=conn.execute("INSERT INTO payroll_assignment_claims(assignment_id,batch_id,claimed_at) VALUES(?,?,?) ON CONFLICT(assignment_id) DO NOTHING",
            (row["assignment_id"],batch_id,now_iso()))
        if claim.rowcount!=1:
            continue
        conn.execute("""INSERT INTO payroll_lines(batch_id,assignment_id,base_fee_rupiah,meal_rupiah,skill_fee_rupiah,adjustment_rupiah,total_rupiah)
            VALUES(?,?,?,?,?,?,?)""", (batch_id,row["assignment_id"],row["base_fee"],row["meal_fee"],row["skill_fee"],0,row["total"]))
        payable.append(row)
    conn.execute("UPDATE payroll_batches SET status='exported' WHERE id=?", (batch_id,))
    audit(conn, actor_id, "payroll_batch", batch_id, "exported", {"period": period_key, "lines": len(payable),"skipped_already_claimed":len(rows)-len(payable)})
    return payable, batch_id


def payroll_timestamp_wib(value: str | None) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S")


def make_payroll_xlsx(rows: list[dict], period_label: str) -> bytes:
    headers = [
        "No", "Akun (email)", "Nama", "Kode Project", "Event", "Hari", "Durasi", "Posisi",
        "Catatan Mulai (WIB)", "Catatan Selesai (WIB)", "Skill Khusus", "Fee Dasar (Rp)",
        "Fee Skill (Rp)", "Fee (Dasar + Skill) (Rp)", "UM (Rp)", "Total (Rp)",
    ]
    data = []
    for index, row in enumerate(rows, start=1):
        data.append([
            index, row.get("email", ""), row.get("full_name", ""), row.get("project_code", ""),
            row.get("event_title", ""), local_datetime(row["starts_at"]).date().isoformat() if row.get("starts_at") else "",
            "Full day" if row.get("is_full_day") else "Non-full day", row.get("position_name", ""),
            payroll_timestamp_wib(row.get("check_in_at")), payroll_timestamp_wib(row.get("check_out_at")),
            row.get("skill_names", ""), row.get("base_fee", row.get("base_fee_rupiah", 0)),
            row.get("skill_fee", row.get("skill_fee_rupiah", 0)),
            row.get("fee", row.get("base_fee_rupiah", 0) + row.get("skill_fee_rupiah", 0)),
            row.get("meal_fee", row.get("meal_rupiah", 0)), row.get("total", row.get("total_rupiah", 0)),
        ])
    totals = [0] * len(headers)
    for row in data:
        for index in (11, 12, 13, 14, 15):
            totals[index] += row[index]
    total_row = ["TOTAL MINGGUAN"] + [""] * 10 + [totals[11], totals[12], totals[13], totals[14], totals[15]]

    def xml_value(value: object) -> str:
        text_value = str(value)
        text_value = "".join(char for char in text_value if char in "\t\n\r" or ord(char) >= 32)
        return escape(text_value)

    def cell(reference: str, value: object, style: int = 0) -> str:
        style_attr = f' s="{style}"' if style else ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
        if value is None or value == "":
            return f'<c r="{reference}"{style_attr}/>' if style else ""
        return f'<c r="{reference}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{xml_value(value)}</t></is></c>'

    def row_xml(number: int, values: list[object], styles: list[int], height: int | None = None) -> str:
        height_attr = f' ht="{height}" customHeight="1"' if height else ""
        cells = "".join(cell(f"{chr(64 + index)}{number}", value, styles[index - 1]) for index, value in enumerate(values, start=1))
        return f'<row r="{number}"{height_attr}>{cells}</row>'

    worksheet_rows = [
        '<row r="1" ht="30" customHeight="1"><c r="A1" s="1" t="inlineStr"><is><t>Rekap Payroll Mingguan</t></is></c></row>',
        f'<row r="2" ht="22" customHeight="1"><c r="A2" s="2" t="inlineStr"><is><t>{xml_value("Periode: " + period_label)}</t></is></c></row>',
        '<row r="3" ht="21" customHeight="1"><c r="A3" s="3" t="inlineStr"><is><t>Sel kuning menandai komponen skill khusus dan uang makan. Total sudah dihitung otomatis.</t></is></c></row>',
    ]
    header_styles = [5 if index in {11, 13, 15} else 4 for index in range(1, len(headers) + 1)]
    worksheet_rows.append(row_xml(4, headers, header_styles, 36))
    body_end = 4 + len(data)
    for index, values in enumerate(data, start=5):
        styles = [7] * len(headers)
        styles[10] = 6
        for col in (11, 12, 13, 14, 15):
            styles[col] = 8 if col in {12, 14} else 9
        worksheet_rows.append(row_xml(index, values, styles))
    total_row_number = body_end + 1
    total_styles = [12] + [0] * 10 + [10, 11, 10, 11, 10]
    worksheet_rows.append(row_xml(total_row_number, total_row, total_styles, 23))

    column_widths = [7, 28, 24, 20, 30, 14, 16, 16, 21, 21, 26, 18, 17, 22, 15, 17]
    cols = "".join(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>' for index, width in enumerate(column_widths, start=1))
    xml_parts = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="0"/></bookViews><sheets><sheet name="Payroll Mingguan" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        "xl/styles.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;Rp&quot; #,##0;[Red]-&quot;Rp&quot; #,##0"/></numFmts><fonts count="5"><font><sz val="10"/><name val="Aptos"/><color rgb="FF443954"/></font><font><b/><sz val="16"/><name val="Aptos Display"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="11"/><name val="Aptos"/><color rgb="FF362653"/></font><font><b/><sz val="10"/><name val="Aptos"/><color rgb="FF4B386F"/></font><font><i/><sz val="9"/><name val="Aptos"/><color rgb="FF776C8A"/></font></fonts><fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF281545"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF1BF"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF4F0FC"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF7DB"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="thin"><color rgb="FFE6E1EE"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="13"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="4" fillId="5" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="164" fontId="3" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="164" fontId="3" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''',
    }
    last_data_row = max(4, body_end)
    last_row = total_row_number
    xml_parts["xl/worksheets/sheet1.xml"] = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:P{last_row}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>{''.join(worksheet_rows)}</sheetData><autoFilter ref="A4:P{last_data_row}"/><mergeCells count="3"><mergeCell ref="A1:P1"/><mergeCell ref="A2:P2"/><mergeCell ref="A3:P3"/></mergeCells><pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in xml_parts.items():
            archive.writestr(path, content)
    return output.getvalue()


def calendar_bounds(start_obj: dict, end_obj: dict) -> tuple[str | None, str | None]:
    """Convert Google start/end into WIB ISO timestamps.

    For all-day events Google's end.date is EXCLUSIVE (a 2-day event on 3-4 Oct ends on 5 Oct),
    so the last real day is end.date - 1. Timed events keep their own timezone offset.
    """
    if start_obj.get("dateTime") and end_obj.get("dateTime"):
        return start_obj["dateTime"], end_obj["dateTime"]
    start_date, end_date = start_obj.get("date"), end_obj.get("date")
    if not start_date:
        return None, None
    try:
        first = date.fromisoformat(start_date)
        last = date.fromisoformat(end_date) - timedelta(days=1) if end_date else first
    except ValueError:
        return None, None
    if last < first:
        last = first
    return f"{first.isoformat()}T00:00:00+07:00", f"{last.isoformat()}T23:59:00+07:00"


def google_error_message(exc: Exception) -> str:
    """Explain a failed Google call without leaking credentials."""
    if isinstance(exc, HTTPError):
        try:
            body = json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            body = {}
        err = body.get("error")
        detail = body.get("error_description") or (err.get("message") if isinstance(err, dict) else err) or ""
        code = err if isinstance(err, str) else (err.get("status") if isinstance(err, dict) else "")
        hint = ""
        if code == "invalid_grant":
            hint = ("Refresh token ditolak/kedaluwarsa. Jika OAuth consent screen berstatus Testing, token kedaluwarsa tiap 7 hari: "
                    "ubah ke In production lalu buat ulang GOOGLE_REFRESH_TOKEN.")
        elif code in {"invalid_client", "unauthorized_client"}:
            hint = "GOOGLE_CLIENT_ID atau GOOGLE_CLIENT_SECRET tidak cocok dengan refresh token."
        elif exc.code == 403:
            hint = "Google Calendar API belum diaktifkan di project Google Cloud, atau akun tidak punya akses ke kalender ini."
        elif exc.code == 404:
            hint = "GOOGLE_CALENDAR_ID tidak ditemukan atau tidak dapat diakses oleh akun tersebut."
        text = f"HTTP {exc.code}" + (f" {code}" if code else "") + (f": {str(detail)[:160]}" if detail else "")
        return f"{text}. {hint}".strip()
    return f"{type(exc).__name__}: {exc}"[:200]


def event_unresolved_attendance(conn: sqlite3.Connection, event_id: int) -> list[str]:
    """Multi-day events only: crew with a work day that has started but is still 'Belum absen' or has no check-out.

    Single-day events keep their existing clear flow.
    """
    event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event or not eventdays.is_multiday(event):
        return []
    today = eventdays.today_wib()
    names = []
    for row in conn.execute("""SELECT ea.id,u.full_name FROM event_assignments ea JOIN users u ON u.id=ea.user_id
            WHERE ea.event_id=? ORDER BY u.full_name""", (event_id,)).fetchall():
        if any(day["status"] in ("not_started", "checked_in") and day["work_date"] <= today
               for day in eventdays.day_attendance(conn, event, row["id"])):
            names.append(row["full_name"])
    return names


def reconcile_assignment_days(conn: sqlite3.Connection, event_id: int) -> None:
    """After Calendar changes an event's dates, drop day mappings that fell outside the new range."""
    event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        return
    valid = eventdays.event_dates(event)
    for row in conn.execute("SELECT id FROM event_assignments WHERE event_id=?", (event_id,)).fetchall():
        stored = [r[0] for r in conn.execute("SELECT work_date FROM event_assignment_days WHERE assignment_id=?", (row["id"],))]
        if not stored:
            continue
        keep = [d for d in stored if d in valid]
        if len(valid) == 1:
            conn.execute("DELETE FROM event_assignment_days WHERE assignment_id=?", (row["id"],))
        elif keep != stored:
            keep = keep or valid  # nothing left in range: cover the whole new range so the person is not dropped silently
            eventdays.save_days(conn, event, row["id"], keep)
            audit(conn, None, "event_assignment", row["id"], "days_adjusted_by_calendar", {"days": keep, "previous": stored})


def google_sync(conn: sqlite3.Connection) -> dict:
    required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        return {"ok": False, "status": 400, "message": "Integrasi Google Calendar belum dikonfigurasi. Isi kredensial read-only di file .env."}
    token_body = urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=token_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=20) as response:
            token = json.loads(response.read())["access_token"]
        calendar_id = os.environ["GOOGLE_CALENDAR_ID"]
        next_page = None
        queued = queue_updated = updated = skipped = 0
        while True:
            query = {
                "singleEvents": "true", "showDeleted": "true", "maxResults": "250",
                "orderBy": "startTime", "timeMin": datetime.now(timezone.utc).isoformat(),
                "timeMax": (datetime.now(timezone.utc) + timedelta(days=366)).isoformat(),
            }
            if next_page:
                query["pageToken"] = next_page
            url = "https://www.googleapis.com/calendar/v3/calendars/" + __import__("urllib.parse", fromlist=["quote"]).quote(calendar_id, safe="") + "/events?" + urlencode(query)
            request = Request(url, headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read())
            for item in payload.get("items", []):
                google_id = item.get("id")
                if not google_id:
                    skipped += 1
                    continue
                existing = conn.execute("SELECT * FROM events WHERE google_event_id=?", (google_id,)).fetchone()
                intake = conn.execute("SELECT id FROM calendar_code_queue WHERE google_event_id=?", (google_id,)).fetchone()
                if item.get('status') == 'cancelled' and existing:
                    # Calendar tombstones may contain only ID/status. Retain the last known schedule.
                    conn.execute("UPDATE events SET status='cancelled',calendar_updated_at=?,updated_at=? WHERE id=?", (item.get('updated'),now_iso(),existing['id']))
                    notices.event_changed(conn,existing,conn.execute('SELECT * FROM events WHERE id=?',(existing['id'],)).fetchone())
                    updated += 1
                    continue
                if item.get('status') == 'cancelled' and intake:
                    conn.execute("UPDATE calendar_code_queue SET status='cancelled',updated_at=? WHERE id=?", (now_iso(),intake['id']))
                    queue_updated += 1
                    continue
                description = item.get("description", "") or ""
                searchable = (item.get("summary", "") or "") + "\n" + description
                match = re.search(r"(?:PROJECT[_ ]?CODE|KODE[_ ]?PROJECT|KODE PROYEK)\s*[:=\-]\s*([A-Za-z0-9][A-Za-z0-9._-]{2,49})", searchable, re.I)
                suggested_code = match.group(1).upper() if match else None
                start_obj = item.get("start", {})
                end_obj = item.get("end", {})
                start_value, end_value = calendar_bounds(start_obj, end_obj)
                if not start_value or not end_value:
                    skipped += 1
                    continue
                try:
                    full_day = int("dateTime" not in start_obj or (parse_iso(end_value) - parse_iso(start_value)).total_seconds() >= 8 * 3600)
                except ValueError:
                    full_day = int("dateTime" not in start_obj)
                status = "cancelled" if item.get("status") == "cancelled" else "scheduled"
                if existing and existing["status"] == "completed" and status != "cancelled":
                    status = "completed"
                title = item.get("summary") or suggested_code or "Event tanpa judul"
                values = (title,item.get("eventType", "Photobooth"),start_value,end_value,
                          item.get("location", ""),full_day,status,item.get("updated"))
                if existing:
                    conn.execute("""UPDATE events SET title=?,event_type=?,starts_at=?,ends_at=?,location=?,
                        is_full_day=CASE WHEN is_full_day_manual=1 THEN is_full_day ELSE ? END,status=?,calendar_updated_at=?,updated_at=? WHERE google_event_id=?""",
                                 (values[0],values[1],values[2],values[3],values[4],values[5],values[6],values[7],now_iso(),google_id))
                    reconcile_assignment_days(conn, existing['id'])
                    notices.event_changed(conn, existing, conn.execute('SELECT * FROM events WHERE id=?', (existing['id'],)).fetchone())
                    updated += 1
                elif intake:
                    conn.execute("""UPDATE calendar_code_queue SET suggested_project_code=?,title=?,event_type=?,starts_at=?,ends_at=?,location=?,is_full_day=?,status=?,calendar_updated_at=?,updated_at=? WHERE google_event_id=?""",
                                 (suggested_code,values[0],values[1],values[2],values[3],values[4],values[5],values[6],values[7],now_iso(),google_id))
                    queue_updated += 1
                else:
                    if status == "cancelled":
                        skipped += 1
                        continue
                    try:
                        conn.execute("""INSERT INTO calendar_code_queue(google_event_id,suggested_project_code,title,event_type,starts_at,ends_at,location,is_full_day,status,calendar_updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", (google_id,suggested_code,*values))
                        queued += 1
                    except sqlite3.IntegrityError:
                        skipped += 1
            next_page = payload.get("nextPageToken")
            if not next_page:
                break
        return {"ok": True, "status": 200, "message": f"Sinkronisasi selesai: {queued} event menunggu kode CRM Admin, {queue_updated} antrian diperbarui, {updated} event operasional diperbarui, {skipped} dilewati.", "queued": queued, "queue_updated": queue_updated, "updated": updated, "skipped": skipped}
    except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        print(f"Google Calendar sync error: {google_error_message(exc)}", file=sys.stderr, flush=True)
        return {"ok": False, "status": 502, "message": f"Google Calendar belum dapat disinkronkan ({type(exc).__name__}). {google_error_message(exc)}"}


class Handler(SimpleHTTPRequestHandler):
    server_version = "CaptureItOps/0.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        if os.environ.get("LOG_REQUESTS", "0") == "1":
            super().log_message(fmt, *args)

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/push-config.js':
            data = ('self.CAPTUREIT_PUSH_CONFIG='+json.dumps(push_delivery.config())+';').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/favicon.ico":
            self.handle_branding_asset("favicon")
            return
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if self.headers.get_content_type() != 'application/json':
                self.json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'Gunakan Content-Type application/json.')
                return
            payload = self.read_json()
            if parsed.path == "/api/login":
                self.login(payload)
                return
            user = self.current_user()
            self.require_csrf(user)
            if parsed.path == "/api/logout":
                self.logout(user)
            elif parsed.path == "/api/notifications/read":
                self.handle_notification_read(user,payload)
            elif parsed.path in {'/api/notifications/preferences', '/api/push/register', '/api/push/unregister', '/api/push/test'}:
                self.handle_push_action(user, parsed.path, payload)
            elif parsed.path == "/api/vehicles":
                self.handle_vehicle_save(user,payload)
            elif re.fullmatch(r"/api/events/\d+/logistics",parsed.path):
                self.handle_logistics_save(user,int(parsed.path.split("/")[3]),payload)
            elif re.fullmatch(r"/api/events/\d+/closing",parsed.path):
                self.handle_closing_save(user,int(parsed.path.split("/")[3]),payload)
            elif parsed.path == "/api/google/sync":
                self.handle_google_sync(user)
            elif parsed.path == "/api/payroll/export":
                self.handle_payroll_export(user, payload)
            elif re.fullmatch(r"/api/payroll/batch/\d+/transfer", parsed.path):
                self.handle_freelancer_payroll_transfer(user, int(parsed.path.split("/")[4]), payload)
            elif parsed.path == "/api/inhouse-attendance":
                self.handle_inhouse_attendance(user, payload)
            elif parsed.path == "/api/inhouse-attendance/export":
                self.handle_inhouse_attendance_export(user, payload)
            elif parsed.path == "/api/inhouse-payroll":
                self.handle_inhouse_payroll_save(user, payload)
            elif parsed.path == "/api/inhouse-payroll/salaries":
                self.handle_inhouse_salary_update(user, payload)
            elif re.fullmatch(r"/api/inhouse-payroll/payout/\d+/transfer", parsed.path):
                self.handle_inhouse_payroll_transfer(user, int(parsed.path.split("/")[4]), payload)
            elif parsed.path == "/api/kpi":
                self.handle_kpi_create(user, payload)
            elif parsed.path == "/api/rates":
                self.handle_rate_update(user, payload)
            elif parsed.path == "/api/skills":
                self.handle_skill_update(user, payload)
            elif parsed.path == "/api/users":
                self.handle_user_create(user, payload)
            elif parsed.path == "/api/configure/appearance":
                self.handle_configure_appearance(user, payload)
            elif parsed.path == "/api/configure/asset":
                self.handle_configure_asset(user, payload)
            elif parsed.path == "/api/configure/roles":
                self.handle_configure_roles(user, payload)
            elif parsed.path == "/api/profile/password":
                self.handle_profile_password_change(user, payload)
            elif parsed.path == "/api/profile":
                self.handle_profile_update(user, payload)
            elif parsed.path == "/api/profile/ktp":
                self.handle_profile_ktp_upload(user, payload)
            elif re.fullmatch(r"/api/users/\d+/update", parsed.path):
                self.handle_user_update(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/calendar-code/\d+", parsed.path):
                self.handle_calendar_code_assign(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/calendar-queue/\d+/promote", parsed.path):
                self.handle_calendar_queue_promote(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/project-code", parsed.path):
                self.handle_event_project_code(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/temporary-code", parsed.path):
                self.handle_event_temporary_code(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/full-day", parsed.path):
                self.handle_event_full_day(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/assignment", parsed.path):
                self.handle_assignment(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/performance", parsed.path):
                self.handle_event_performance(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/group", parsed.path):
                self.handle_group(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/advance", parsed.path):
                self.handle_advance(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/advance/documents", parsed.path):
                self.handle_advance_document_upload(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/warehouse", parsed.path):
                self.handle_warehouse(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/events/\d+/attendance", parsed.path):
                self.handle_attendance(user, int(parsed.path.split("/")[3]), payload)
            elif parsed.path == "/api/settings/inhouse-schedule":
                self.handle_inhouse_schedule_update(user, payload)
            elif parsed.path == "/api/events/manual":
                self.handle_manual_event_create(user, payload)
            elif re.fullmatch(r"/api/events/\d+/delete-manual", parsed.path):
                self.handle_manual_event_delete(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/design/\d+/status", parsed.path):
                self.handle_design_status(user, int(parsed.path.split("/")[3]), payload)
            elif re.fullmatch(r"/api/design/\d+/brief", parsed.path):
                self.handle_design_brief(user, int(parsed.path.split("/")[3]), payload)
            else:
                self.json_error(HTTPStatus.NOT_FOUND, "Endpoint tidak ditemukan.")
        except PermissionError as exc:
            self.json_error(HTTPStatus.FORBIDDEN, str(exc))
        except ClosingVersionConflict as exc:
            self.json_response({'error': str(exc), 'code': 'closing_version_conflict'}, HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self.json_error(HTTPStatus.BAD_REQUEST, str(exc))
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            self.json_error(HTTPStatus.CONFLICT, f"Data tidak dapat disimpan: {exc}")
        except Exception as exc:
            if os.environ.get("DEBUG", "0") == "1":
                print(f"Request error: {exc!r}", file=sys.stderr)
            self.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Terjadi kesalahan saat memproses permintaan.")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 15*1024*1024:
            raise ValueError("Permintaan terlalu besar.")
        body = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Isi permintaan tidak valid.") from exc
        if not isinstance(value, dict):
            raise ValueError("Isi permintaan harus berupa object JSON.")
        return value

    def cookies(self) -> SimpleCookie:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        return cookie

    def current_user(self) -> dict:
        cookies = self.cookies()
        token = cookies["ops_session"].value if "ops_session" in cookies else ""
        if not token:
            raise PermissionError("Silakan masuk kembali.")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with get_db() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
            if not session or parse_iso(session["expires_at"]) <= datetime.now(timezone.utc):
                raise PermissionError("Sesi sudah berakhir. Silakan masuk kembali.")
            row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (session["user_id"],)).fetchone()
            if not row:
                raise PermissionError("Akun tidak aktif.")
            roles, permissions = role_data(conn, row["id"])
            return {**dict(row), "roles": roles, "permissions": permissions, "csrf_token": session["csrf_token"], 'session_hash': token_hash}

    def require_csrf(self, user: dict) -> None:
        cookies = self.cookies()
        cookie_token = cookies["ops_csrf"].value if "ops_csrf" in cookies else ""
        header_token = self.headers.get("X-CSRF-Token", "")
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token) or not hmac.compare_digest(header_token, user["csrf_token"]):
            raise PermissionError("Token keamanan tidak valid. Muat ulang halaman.")

    def require(self, user: dict, permission: str) -> None:
        if permission not in user["permissions"]:
            raise PermissionError("Akun ini tidak memiliki akses untuk tindakan tersebut.")

    def login(self, payload: dict) -> None:
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))
        if len(email) > 254 or len(password) > 1024:
            raise ValueError("Email atau password terlalu panjang.")
        ip = self.client_ip()
        with get_db() as conn:
            locked_for = login_lock_remaining(conn, email, ip)
            if locked_for:
                # Do not even check the password while locked, so a lock cannot be used to keep guessing.
                minutes = max(1, -(-locked_for // 60))
                self.json_response({"ok": False, "error": f"Terlalu banyak percobaan login yang gagal. Coba lagi dalam {minutes} menit.",
                                    "retry_after": locked_for}, HTTPStatus.TOO_MANY_REQUESTS, extra_headers={"Retry-After": str(locked_for)})
                return
            row = conn.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
            if not row or not check_password(password, row["password_hash"]):
                # Unknown emails are counted too, so the response never reveals which accounts exist.
                record_login_failure(conn, email, ip)
                time.sleep(0.25)
                self.json_error(HTTPStatus.UNAUTHORIZED, "Email atau password tidak cocok.")
                return
            conn.execute("DELETE FROM login_attempts WHERE email=?", (email,))
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)", (token_hash,row["id"],csrf,expires))
            audit(conn, row["id"], "session", None, "login")
            summary = user_summary(conn, row["id"])
        # Commit the session before the browser can start its next request.
        secure = "; Secure" if os.environ.get("COOKIE_SECURE", "0") == "1" else ""
        self.send_response(HTTPStatus.OK)
        self.send_header("Set-Cookie", f"ops_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600{secure}")
        self.send_header("Set-Cookie", f"ops_csrf={csrf}; Path=/; SameSite=Lax; Max-Age=1209600{secure}")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "user": summary, "csrf_token": csrf}, ensure_ascii=False).encode())

    def logout(self, user: dict) -> None:
        cookies = self.cookies()
        token = cookies["ops_session"].value if "ops_session" in cookies else ""
        with get_db() as conn:
            notices.revoke_devices(conn, user['id'], session_hash=user['session_hash'])
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            audit(conn, user["id"], "session", None, "logout")
        self.send_response(HTTPStatus.OK)
        self.send_header("Set-Cookie", "ops_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.send_header("Set-Cookie", "ops_csrf=; Path=/; SameSite=Lax; Max-Age=0")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def handle_branding_asset(self, kind: str) -> None:
        with get_db() as conn:
            row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (f"brand_{kind}_filename",)).fetchone()
            mime_row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (f"brand_{kind}_mime",)).fetchone()
            filename = row["setting_value"] if row else ""
            mime = mime_row["setting_value"] if mime_row else ""
        if filename and re.fullmatch(rf"{kind}-[a-f0-9]{{20}}\.(?:png|jpg|ico)", filename):
            path = branding_storage_dir() / filename
            allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/vnd.microsoft.icon": ".ico"}
            if path.is_file() and allowed.get(mime) == path.suffix:
                content = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
                return
        fallback = (STATIC / "logo.png").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(fallback)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(fallback)

    def handle_api_get(self, path: str, query: dict) -> None:
        if path == '/api/push/config':
            self.json_response(push_delivery.config())
            return
        if path == "/api/health":
            self.json_response({"ok": True, "app": "Capture It Operations"})
            return
        if path == "/api/branding":
            with get_db() as conn:
                self.json_response(branding_payload(conn))
            return
        branding_match = re.fullmatch(r"/api/branding/(logo|favicon)", path)
        if branding_match:
            self.handle_branding_asset(branding_match.group(1))
            return
        try:
            user = self.current_user()
        except PermissionError as exc:
            self.json_error(HTTPStatus.UNAUTHORIZED, str(exc))
            return
        photo_match = re.fullmatch(r"/api/attendance/(\d+)/photo/(check_in|check_out)", path)
        if photo_match:
            self.handle_attendance_photo(user, int(photo_match.group(1)), photo_match.group(2))
            return
        advance_document_match=re.fullmatch(r"/api/advance-documents/(\d+)/download",path)
        if advance_document_match:
            self.handle_advance_document_download(user,int(advance_document_match.group(1)))
            return
        inhouse_photo_match = re.fullmatch(r"/api/inhouse-attendance/(\d+)/photo/(check_in|check_out)", path)
        if inhouse_photo_match:
            self.handle_inhouse_attendance_photo(user, int(inhouse_photo_match.group(1)), inhouse_photo_match.group(2))
            return
        profile_file_match = re.fullmatch(r"/api/profile-file/(\d+)/(photo|ktp_front|ktp_back)", path)
        if profile_file_match:
            self.handle_profile_file(user, int(profile_file_match.group(1)), profile_file_match.group(2))
            return
        try:
            with get_db() as conn:
                if re.fullmatch(r"/api/closing-photos/\d+", path):
                    photo_id = int(path.rsplit('/', 1)[1])
                    photo = conn.execute('SELECT * FROM event_closing_photos WHERE id=?', (photo_id,)).fetchone()
                    if not photo:
                        self.json_error(HTTPStatus.NOT_FOUND, 'Foto tidak ditemukan.')
                    elif not can_read_closing(conn, photo['event_id'], user):
                        raise PermissionError('Foto ini bukan dokumentasi penugasan Anda.')
                    else:
                        self.send_response(HTTPStatus.OK)
                        self.send_header('Content-Type', photo['mime'])
                        self.send_header('Content-Length', str(len(photo['content'])))
                        self.end_headers()
                        self.wfile.write(photo['content'])
                elif path == "/api/server-time":
                    self.json_response({"timestamp":now_iso()})
                elif path == "/api/me":
                    self.json_response({"user": user_summary(conn, user["id"])})
                elif path == "/api/bootstrap":
                    self.json_response(bootstrap_payload(conn, user))
                elif path == "/api/notifications":
                    before = query.get('before', [''])[0]
                    if before and (not before.isdigit() or not 0<int(before)<=2**63-1):
                        raise ValueError('Halaman notifikasi tidak valid.')
                    self.json_response(notifications_payload(conn,user,before=int(before) if before else None))
                elif path == '/api/notifications/settings':
                    summary = {r['status']: r['n'] for r in conn.execute('''SELECT d.status,COUNT(*) n FROM push_deliveries d
                        JOIN notifications n ON n.id=d.notification_id WHERE n.user_id=? GROUP BY d.status''', (user['id'],))}
                    self.json_response({'preferences': notices.preferences(conn, user['id']), 'categories': notices.CATEGORIES,
                        'devices': notices.device_list(conn, user), 'push': push_delivery.config(), 'delivery_counts': summary})
                elif path in {'/api/notifications/item', '/api/push/context'}:
                    public_id = query.get('notice', [''])[0]
                    if not re.fullmatch(r'[a-f0-9]{32}', public_id):
                        raise ValueError('Notifikasi tidak valid.')
                    row = conn.execute('SELECT * FROM notifications WHERE public_id=? AND user_id=?', (public_id, user['id'])).fetchone()
                    live = {n['key'] for n in live_notifications(conn, user)}
                    if not row or not notices.allowed(conn, user, dict(row), live):
                        raise PermissionError('Notifikasi tidak tersedia untuk akun ini.')
                    if path == '/api/push/context':
                        if not conn.execute('SELECT 1 FROM push_devices WHERE user_id=? AND session_hash=? AND active=1', (user['id'], user['session_hash'])).fetchone():
                            raise PermissionError('Perangkat tidak terdaftar pada sesi ini.')
                        if row['read_at'] or datetime.fromisoformat(row['expires_at'])<=datetime.now(timezone.utc):
                            raise PermissionError('Notifikasi sudah dibaca atau kedaluwarsa.')
                        self.json_response({'title': 'Capture It Ops', 'body': 'Ada pembaruan '+notices.CATEGORIES.get(row['category'], 'akun').lower()+'. Buka aplikasi untuk melihatnya.',
                            'notice': public_id, 'url': '/?notice='+public_id})
                    else:
                        self.json_response({'item': notices.item(dict(row))})
                elif path == "/api/vehicles":
                    self.require(user,"events.assign")
                    self.json_response({"vehicles":fleet_rows(conn)})
                elif path == "/api/inhouse-attendance":
                    self.handle_inhouse_attendance_list(user, query)
                elif path == "/api/advance-documents":
                    self.require(user,"advances.documents.read_all")
                    self.json_response({"documents":advance_document_rows(conn)})
                elif path == "/api/inhouse-payroll":
                    self.require(user,"inhouse_payroll.view")
                    start,end,_=default_inhouse_payroll_period()
                    self.json_response(inhouse_payroll_payload(conn,query.get("start",[start])[0],query.get("end",[end])[0]))
                elif path == "/api/inhouse-payroll/slips":
                    self.require(user,"inhouse_payroll.read_own")
                    if user["employment_type"]!="inhouse":
                        raise PermissionError("Slip payroll In-house hanya tersedia untuk akun In-house.")
                    self.json_response({"slips":inhouse_payroll_slips(conn,user["id"])})
                elif path == "/api/payroll/slips":
                    self.require(user,"payroll.read_own")
                    if user["employment_type"]!="freelancer":
                        raise PermissionError("Slip honor event hanya tersedia untuk akun Crew/PIC.")
                    self.json_response(freelancer_payroll_slips(conn,user["id"],query.get("year",[""])[0]))
                elif path == "/api/events/export":
                    self.require(user,"events.export_own")
                    start=query.get("start",[""])[0]; end=query.get("end",[""])[0]
                    file_format=query.get("format",["xlsx"])[0].lower()
                    if file_format not in {"xlsx","csv"}: raise ValueError("Format ekspor harus Excel atau CSV.")
                    if not start or not end: raise ValueError("Pilih tanggal awal dan tanggal akhir jadwal.")
                    start_date,end_exclusive=payroll_window(start,end)
                    data=export_event_rows(conn,user["id"],start_date,end_exclusive)
                    headers=EXPORT_HEADERS
                    if file_format=="csv":
                        out=io.StringIO(); writer=csv.writer(out); writer.writerow(headers)
                        writer.writerows([[csv_value(value) for value in row] for row in data])
                        payload=out.getvalue().encode("utf-8-sig"); content_type="text/csv; charset=utf-8"
                    else:
                        payload=make_event_list_xlsx(data); content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    filename=f"captureit-jadwal-event-{start}-to-{end}.{file_format}"
                    self.send_response(HTTPStatus.OK); self.send_header("Content-Type",content_type)
                    self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
                    self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
                elif path == '/api/compensation-history':
                    kind = query.get('kind', [''])[0]
                    if kind not in {'fee', 'salary'}:
                        raise ValueError('Jenis riwayat tidak valid.')
                    self.require(user, 'fees.manage' if kind == 'fee' else 'inhouse_payroll.manage')
                    person_id = int(query.get('user_id', ['0'])[0])
                    before = int(query['before'][0]) if 'before' in query else None
                    if not 0 < person_id <= 2**63-1:
                        raise ValueError('Akun riwayat tidak valid.')
                    if before is not None and not 0 < before <= 2**63-1:
                        raise ValueError('Cursor riwayat tidak valid.')
                    self.json_response(history_payload(conn, person_id, kind, before))
                elif re.fullmatch(r"/api/events/\d+/staff-availability", path):
                    self.require(user, "events.assign")
                    event_id = int(path.split('/')[3])
                    event = conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
                    if not event:
                        raise ValueError('Event tidak ditemukan.')
                    staff_id = int(query.get('user_id', ['0'])[0])
                    if not conn.execute('SELECT 1 FROM users WHERE id=? AND active=1', (staff_id,)).fetchone():
                        raise ValueError('Pilih akun staff yang aktif.')
                    raw_days = [d for d in query.get('days', [''])[0].split(',') if d]
                    days = eventdays.normalize_days(event, raw_days) if raw_days and eventdays.is_multiday(event) else (
                        eventdays.event_dates(event) if eventdays.is_multiday(event) else None)
                    self.json_response(staff_availability(conn, event, staff_id, days=days))
                elif path.startswith("/api/events/"):
                    event_id = int(path.rsplit("/",1)[1])
                    detail = event_detail(conn, event_id, user["id"], "kpi.evaluate_crew" in user["permissions"],
                        "advances.documents.read_all" in user["permissions"])
                    if not detail:
                        self.json_error(HTTPStatus.NOT_FOUND,"Event tidak ditemukan.")
                    elif "events.read_all" not in user["permissions"] and ("events.read_own" not in user["permissions"] or not any(a["user_id"] == user["id"] for a in detail["assignments"])):
                        self.json_error(HTTPStatus.FORBIDDEN,"Event ini bukan penugasan Anda.")
                    else:
                        detail["can_manage_logistics"]=can_manage_logistics(user,detail)
                        detail["vehicle_conflicts"]=vehicle_conflicts(conn,detail,detail["logistics"]) if detail["can_manage_logistics"] else []
                        detail["closing_report"]=closing_detail(conn,detail,user)
                        detail["staff_conflicts"]=event_staff_conflicts(conn,detail,user)
                        self.json_response(detail)
                elif path == "/api/payroll":
                    self.require(user, "payroll.view")
                    start = query.get("start", [""])[0]
                    end = query.get("end", [""])[0]
                    if start or end:
                        if not start or not end:
                            raise ValueError("Pilih tanggal awal dan tanggal akhir payroll.")
                        rows = payroll_rows(conn, start, end)
                        period = f"{start} – {end}"
                        period_key = f"{start}..{end}"
                    else:
                        period = query.get("month", [datetime.now(WIB).strftime("%Y-%m")])[0]
                        rows = payroll_rows(conn, period)
                        period_key = period
                    batch=conn.execute("""SELECT b.id,b.status,t.pay_date,t.transfer_reference,t.recorded_at FROM payroll_batches b
                        LEFT JOIN payroll_batch_transfers t ON t.batch_id=b.id WHERE b.period=?""",(period_key,)).fetchone()
                    if batch and batch["status"]=="exported":
                        rows,_=create_payroll_export(conn,start if start and end else period,user["id"],end if start and end else None)
                    self.json_response({"period": period,"rows": rows,"total": sum(x["total"] for x in rows),"batch":dict(batch) if batch else None})
                else:
                    self.json_error(HTTPStatus.NOT_FOUND,"Endpoint tidak ditemukan.")
        except PermissionError as exc:
            self.json_error(HTTPStatus.FORBIDDEN,str(exc))
        except ValueError as exc:
            self.json_error(HTTPStatus.BAD_REQUEST,str(exc))

    def handle_inhouse_attendance_list(self, user: dict, query: dict) -> None:
        is_manager="attendance.inhouse.read_all" in user["permissions"]
        is_self=user["employment_type"]=="inhouse" and "attendance.inhouse.self" in user["permissions"]
        if not is_manager and not is_self: raise PermissionError("Akun ini tidak memiliki akses absensi In-house.")
        today=wib_now().date().isoformat()
        selected=query.get("date",[today])[0]
        try: date.fromisoformat(selected)
        except ValueError as exc: raise ValueError("Tanggal absensi tidak valid.") from exc
        with get_db() as conn:
            own=None; history=[]
            if is_self:
                own=conn.execute("SELECT * FROM inhouse_attendance WHERE user_id=? AND work_date=?",(user["id"],today)).fetchone()
                own=dict(own) if own else {"id":None,"user_id":user["id"],"work_date":today,"status":"not_started","check_in_at":None,"check_out_at":None,"has_check_in_photo":False,"has_check_out_photo":False}
                start=(date.fromisoformat(today)-timedelta(days=13)).isoformat()
                history=[dict(row) for row in conn.execute("""SELECT id,user_id,work_date,status,check_in_at,check_out_at,
                    check_in_photo_path IS NOT NULL AS has_check_in_photo,check_out_photo_path IS NOT NULL AS has_check_out_photo,
                    check_in_latitude,check_in_longitude,check_in_accuracy_m,check_out_latitude,check_out_longitude,check_out_accuracy_m,
                    scheduled_start,scheduled_end,late_minutes,early_leave_minutes
                    FROM inhouse_attendance WHERE user_id=? AND work_date BETWEEN ? AND ? ORDER BY work_date DESC""",(user["id"],start,today)).fetchall()]
            rows=[]; schedule=inhouse_schedule(conn)
            if is_manager:
                rows=[dict(row) for row in conn.execute("""SELECT a.id,u.id user_id,u.full_name,u.department,? work_date,
                    COALESCE(a.status,'not_started') status,a.check_in_at,a.check_out_at,
                    COALESCE(a.check_in_photo_path IS NOT NULL,0) has_check_in_photo,
                    COALESCE(a.check_out_photo_path IS NOT NULL,0) has_check_out_photo,
                    a.check_in_latitude,a.check_in_longitude,a.check_in_accuracy_m,a.check_out_latitude,a.check_out_longitude,a.check_out_accuracy_m,
                    a.scheduled_start,a.scheduled_end,a.late_minutes,a.early_leave_minutes
                    FROM users u LEFT JOIN inhouse_attendance a ON a.user_id=u.id AND a.work_date=?
                    WHERE u.active=1 AND u.employment_type='inhouse' ORDER BY u.department,u.full_name""",(selected,selected)).fetchall()]
        self.json_response({"today":today,"selected_date":selected,"own_today":own,"records":rows,"history":history,"is_manager":is_manager,
            "schedule":schedule})

    def handle_inhouse_attendance(self,user: dict,payload: dict) -> None:
        self.require(user,"attendance.inhouse.self")
        if user["employment_type"]!="inhouse": raise PermissionError("Hanya akun In-house yang dapat mengisi absensi kantor.")
        action=payload.get("action")
        if action not in {"check_in","check_out"}: raise ValueError("Tindakan absensi tidak dikenal.")
        photo=parse_attendance_photo(payload.get("photo")); location=parse_attendance_location(payload.get("location")); saved=None
        try:
            today=wib_now().date().isoformat()
            with DB_LOCK,get_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT OR IGNORE INTO inhouse_attendance(user_id,work_date) VALUES(?,?)",(user["id"],today))
                current=conn.execute("SELECT * FROM inhouse_attendance WHERE user_id=? AND work_date=?",(user["id"],today)).fetchone()
                stamp=now_iso();lat,lon,accuracy,_=location
                saved=store_inhouse_attendance_photo(user["id"],action,photo)
                schedule=inhouse_schedule(conn); now_wib=wib_now()
                late=early=None
                if action=="check_in":
                    if current["status"]!="not_started": raise ValueError("Absensi masuk hari ini sudah tercatat.")
                    late=max(0,int((now_wib-scheduled_moment(today,schedule["work_start"])).total_seconds()//60))
                    changed=conn.execute("""UPDATE inhouse_attendance SET status='checked_in',check_in_at=?,check_in_photo_path=?,
                        check_in_latitude=?,check_in_longitude=?,check_in_accuracy_m=?,scheduled_start=?,late_minutes=?,updated_at=?
                        WHERE id=? AND status='not_started'""",
                        (stamp,saved,lat,lon,accuracy,schedule["work_start"],late,stamp,current["id"]))
                else:
                    if current["status"]!="checked_in": raise ValueError("Lakukan check-in terlebih dahulu atau check-out sudah tercatat.")
                    early=max(0,int((scheduled_moment(today,schedule["work_end"])-now_wib).total_seconds()//60))
                    changed=conn.execute("""UPDATE inhouse_attendance SET status='checked_out',check_out_at=?,check_out_photo_path=?,
                        check_out_latitude=?,check_out_longitude=?,check_out_accuracy_m=?,scheduled_end=?,early_leave_minutes=?,updated_at=?
                        WHERE id=? AND status='checked_in'""",
                        (stamp,saved,lat,lon,accuracy,schedule["work_end"],early,stamp,current["id"]))
                if changed.rowcount!=1: raise ValueError("Absensi sudah diproses. Muat ulang halaman untuk melihat status terbaru.")
                audit(conn,user["id"],"inhouse_attendance",current["id"],action,{"work_date":today,"timestamp":stamp,
                    "latitude":lat,"longitude":lon,"accuracy_m":accuracy,"late_minutes":late,"early_leave_minutes":early})
                flagged=late is not None and late>schedule["late_grace_minutes"] or early is not None and early>schedule["early_grace_minutes"]
                if flagged:
                    overseers=[r["id"] for r in conn.execute("SELECT id FROM users WHERE active=1 AND id!=?",(user["id"],)).fetchall()
                               if "attendance.inhouse.read_all" in role_data(conn,r["id"])[1]]
                    notices.inhouse_punctuality(conn,overseers,user,current["id"],action,late if action=="check_in" else early,
                        schedule["work_start"] if action=="check_in" else schedule["work_end"],now_wib.strftime("%H:%M"),today)
        except Exception:
            if saved: (inhouse_attendance_storage_dir()/saved).unlink(missing_ok=True)
            raise
        notice=punctuality_note(late,early,schedule["late_grace_minutes"],schedule["early_grace_minutes"])
        self.json_response({"ok":True,"action":action,"work_date":today,"timestamp":stamp,"late_minutes":late,"early_leave_minutes":early,
            "punctuality":notice if notice!="Tepat waktu" else "","scheduled":schedule["work_start"] if action=="check_in" else schedule["work_end"],
            "location":{"latitude":location[0],"longitude":location[1],"accuracy_m":location[2]}})

    def handle_inhouse_attendance_photo(self, user: dict, record_id: int, kind: str) -> None:
        column="check_in_photo_path" if kind=="check_in" else "check_out_photo_path"
        with get_db() as conn: row=conn.execute(f"SELECT user_id,{column} photo_path FROM inhouse_attendance WHERE id=?",(record_id,)).fetchone()
        if not row or not row["photo_path"]: self.json_error(HTTPStatus.NOT_FOUND,"Foto absensi tidak ditemukan."); return
        if row["user_id"]==user["id"]:
            if user["employment_type"]!="inhouse" or "attendance.inhouse.self" not in user["permissions"]:
                self.json_error(HTTPStatus.FORBIDDEN,"Anda tidak diizinkan melihat foto absensi ini."); return
        elif "attendance.inhouse.read_all" not in user["permissions"]:
            self.json_error(HTTPStatus.FORBIDDEN,"Anda tidak diizinkan melihat foto absensi ini."); return
        root=inhouse_attendance_storage_dir().resolve(); path=(root/row["photo_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file(): self.json_error(HTTPStatus.NOT_FOUND,"File foto absensi tidak ditemukan."); return
        content=path.read_bytes(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type","image/jpeg")
        self.send_header("Content-Length",str(len(content))); self.send_header("Content-Disposition","inline; filename=inhouse-attendance.jpg")
        self.send_header("Cache-Control","private, no-store"); self.end_headers(); self.wfile.write(content)

    def handle_inhouse_attendance_export(self, user: dict, payload: dict) -> None:
        self.require(user,"attendance.inhouse.read_all")
        start=str(payload.get("start","")).strip(); end=str(payload.get("end","")).strip()
        fmt=str(payload.get("format","xlsx")).lower()
        if fmt not in {"xlsx","csv"}: raise ValueError("Format ekspor harus Excel atau CSV.")
        with get_db() as conn:
            rows=inhouse_attendance_rows(conn,start,end); schedule=inhouse_schedule(conn)
        if fmt=="xlsx":
            data=make_inhouse_attendance_xlsx(rows,schedule); content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            out=io.StringIO(); writer=csv.writer(out)
            writer.writerow(INHOUSE_EXPORT_HEADERS)
            for row in rows:
                writer.writerow([csv_value(v) for v in inhouse_export_row(row,schedule)])
            data=out.getvalue().encode("utf-8-sig"); content_type="text/csv; charset=utf-8"
        filename=f"captureit-inhouse-attendance-{start}-to-{end}.{fmt}"
        self.send_response(HTTPStatus.OK); self.send_header("Content-Type",content_type); self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)

    def handle_inhouse_salary_update(self,user: dict,payload: dict) -> None:
        self.require(user,"inhouse_payroll.manage"); entries=payload.get("entries")
        if not isinstance(entries,list) or len(entries)>500: raise ValueError("Daftar gaji pokok tidak valid.")
        applies = effective_date(payload.get('effective_from'))
        reason = change_reason(payload.get('reason', ''))
        changed = 0
        with DB_LOCK,get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            seen = set()
            for entry in entries:
                try: uid=int(entry.get("user_id",0)); amount=rupiah(entry.get("monthly_salary_rupiah"), 'Gaji pokok')
                except (AttributeError,TypeError,ValueError) as exc: raise ValueError("Masukkan gaji pokok dalam rupiah bulat.") from exc
                if uid<=0 or amount<0 or amount>2_000_000_000_000: raise ValueError("Nilai gaji pokok tidak valid.")
                if uid in seen: raise ValueError('Akun gaji tidak boleh dikirim lebih dari sekali.')
                seen.add(uid)
                if not conn.execute("SELECT id FROM users WHERE id=? AND active=1 AND employment_type='inhouse'",(uid,)).fetchone(): raise ValueError("Hanya akun In-house aktif yang dapat diberi gaji pokok.")
                history_id = append_change(conn, uid, 'salary', amount, applies, reason, user)
                if history_id:
                    changed += 1
                    audit(conn,user["id"],"inhouse_salary_rate",uid,"updated",{"monthly_salary_rupiah":amount,'effective_from':applies,'history_id':history_id})
        self.json_response({"ok":True,"updated":changed})

    def handle_inhouse_payroll_save(self,user: dict,payload: dict) -> None:
        self.require(user,"inhouse_payroll.manage")
        start=str(payload.get("start","")).strip(); end=str(payload.get("end","")).strip()
        start_date,end_exclusive=payroll_window(start,end); end_date=(date.fromisoformat(end_exclusive)-timedelta(days=1)).isoformat()
        pay_date=str(payload.get("pay_date","")).strip()
        try: pay_date=date.fromisoformat(pay_date).isoformat()
        except ValueError as exc: raise ValueError("Tanggal bayar tidak valid.") from exc
        inputs={}
        entries=payload.get('entries',[])
        if not isinstance(entries,list) or len(entries)>500:
            raise ValueError('Daftar penyesuaian payroll tidak valid.')
        for entry in entries:
            try: uid=int(entry.get("user_id",0)); allowance=rupiah(entry.get("allowance_rupiah",0),'Tunjangan'); deduction=rupiah(entry.get("deduction_rupiah",0),'Potongan'); note=str(entry.get("note","")).strip()
            except (AttributeError,TypeError,ValueError) as exc: raise ValueError("Tunjangan dan potongan harus berupa rupiah bulat.") from exc
            if uid<=0 or uid in inputs or allowance<0 or deduction<0 or len(note)>500: raise ValueError("Periksa akun, tunjangan, potongan, dan catatan payroll.")
            inputs[uid]={"allowance":allowance,"deduction":deduction,"note":note}
        with DB_LOCK,get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            eligible={r[0] for r in conn.execute("SELECT id FROM users WHERE active=1 AND employment_type='inhouse'")}
            if set(inputs)-eligible:
                raise ValueError('Penyesuaian payroll hanya untuk akun In-house yang aktif.')
            batch=conn.execute("SELECT * FROM inhouse_payroll_batches WHERE period_start=? AND period_end=?",(start_date,end_date)).fetchone()
            if batch and batch["status"]=="transferred": raise ValueError("Semua transfer periode ini sudah selesai; batch tidak dapat diubah.")
            if batch:
                paid=conn.execute("SELECT COUNT(*) n FROM inhouse_payroll_payouts WHERE batch_id=? AND status='transferred'",(batch["id"],)).fetchone()["n"]
                if paid and batch["pay_date"]!=pay_date: raise ValueError("Tanggal bayar tidak dapat diubah setelah ada transfer yang dicatat.")
                if not paid: conn.execute("UPDATE inhouse_payroll_batches SET pay_date=? WHERE id=?",(pay_date,batch["id"]))
                batch_id=batch["id"]
            else:
                batch_id=conn.execute("INSERT INTO inhouse_payroll_batches(period_start,period_end,pay_date,created_by) VALUES(?,?,?,?)",(start_date,end_date,pay_date,user["id"])).lastrowid
            people=conn.execute("""SELECT u.id,u.full_name,u.email,u.department,COALESCE(r.monthly_salary_rupiah,0) salary FROM users u
                LEFT JOIN inhouse_salary_rates r ON r.user_id=u.id WHERE u.active=1 AND u.employment_type='inhouse' ORDER BY u.full_name""").fetchall()
            stats={row["user_id"]:dict(row) for row in conn.execute("""SELECT user_id,COUNT(*) attendance_days,
                SUM(status='checked_out') completed_days,SUM(status='checked_in') open_days FROM inhouse_attendance
                WHERE work_date>=? AND work_date<? GROUP BY user_id""",(start_date,end_exclusive)).fetchall()}
            for person in people:
                person = dict(person)
                person['salary'] = salary_at(conn, person['id'], start_date)
                old=conn.execute("SELECT status FROM inhouse_payroll_payouts WHERE batch_id=? AND user_id=?",(batch_id,person["id"])).fetchone()
                if old and old["status"]=="transferred": continue
                adjustment=inputs.get(person["id"],{"allowance":0,"deduction":0,"note":""})
                total=person["salary"]+adjustment["allowance"]-adjustment["deduction"]
                if total<0: raise ValueError(f"Potongan untuk {person['full_name']} melebihi gaji dan tunjangan.")
                stat=stats.get(person["id"],{})
                conn.execute("""INSERT INTO inhouse_payroll_payouts(batch_id,user_id,user_name_snapshot,email_snapshot,department_snapshot,
                    monthly_salary_rupiah,attendance_days,completed_days,open_days,allowance_rupiah,deduction_rupiah,total_rupiah,note)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,user_id) DO UPDATE SET
                    monthly_salary_rupiah=excluded.monthly_salary_rupiah,attendance_days=excluded.attendance_days,
                    completed_days=excluded.completed_days,open_days=excluded.open_days,allowance_rupiah=excluded.allowance_rupiah,
                    deduction_rupiah=excluded.deduction_rupiah,total_rupiah=excluded.total_rupiah,note=excluded.note
                    WHERE inhouse_payroll_payouts.status='pending'""",
                    (batch_id,person["id"],person["full_name"],person["email"],person["department"],person["salary"],stat.get("attendance_days",0) or 0,
                     stat.get("completed_days",0) or 0,stat.get("open_days",0) or 0,adjustment["allowance"],adjustment["deduction"],total,adjustment["note"]))
            audit(conn,user["id"],"inhouse_payroll_batch",batch_id,"saved",{"period_start":start_date,"period_end":end_date,"pay_date":pay_date})
            result=inhouse_payroll_payload(conn,start_date,end_date)
        self.json_response(result)

    def handle_inhouse_payroll_transfer(self,user: dict,payout_id: int,payload: dict) -> None:
        self.require(user,"inhouse_payroll.manage"); reference=str(payload.get("transfer_reference","")).strip()
        if len(reference)>100: raise ValueError("Referensi transfer maksimal 100 karakter.")
        with DB_LOCK,get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payout=conn.execute("SELECT * FROM inhouse_payroll_payouts WHERE id=?",(payout_id,)).fetchone()
            if not payout: raise ValueError("Data payroll In-house tidak ditemukan.")
            if payout["status"]=="transferred": raise ValueError("Transfer akun ini sudah dicatat.")
            batch=conn.execute("SELECT period_start,period_end FROM inhouse_payroll_batches WHERE id=?",(payout["batch_id"],)).fetchone()
            for month_key in payroll_month_keys(batch["period_start"],batch["period_end"]):
                claimed=conn.execute("SELECT payout_id FROM inhouse_monthly_salary_claims WHERE user_id=? AND payroll_month=?",
                    (payout["user_id"],month_key)).fetchone()
                if claimed and claimed["payout_id"]!=payout_id:
                    raise ValueError(f"Gaji {month_key} untuk akun ini sudah tercatat di payroll lain.")
                conn.execute("INSERT INTO inhouse_monthly_salary_claims(user_id,payroll_month,batch_id,payout_id,claimed_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,payroll_month) DO NOTHING",
                    (payout["user_id"],month_key,payout["batch_id"],payout_id,now_iso()))
            stamp=now_iso(); changed=conn.execute("UPDATE inhouse_payroll_payouts SET status='transferred',transferred_at=?,transfer_reference=?,recorded_by=? WHERE id=? AND status='pending'",(stamp,reference,user["id"],payout_id))
            if changed.rowcount!=1: raise ValueError("Payout ini sudah diproses oleh transaksi lain.")
            remaining=conn.execute("SELECT COUNT(*) n FROM inhouse_payroll_payouts WHERE batch_id=? AND status!='transferred'",(payout["batch_id"],)).fetchone()["n"]
            if not remaining: conn.execute("UPDATE inhouse_payroll_batches SET status='transferred' WHERE id=?",(payout["batch_id"],))
            audit(conn,user["id"],"inhouse_payroll_payout",payout_id,"transferred",{"user_id":payout["user_id"],"amount_rupiah":payout["total_rupiah"],"reference":reference})
            notices.emit(conn, [payout['user_id']], key=f'payslip:inhouse:{payout_id}', category='payroll', kind='payslip',
                title='Slip gaji tersedia', body=f"Slip periode {batch['period_start']} – {batch['period_end']} sudah diterbitkan.",
                target_kind='payslip', target_id=payout_id, permission='inhouse_payroll.read_own')
        self.json_response({"ok":True,"status":"transferred","transferred_at":stamp})

    def handle_attendance_photo(self, user: dict, assignment_id: int, kind: str) -> None:
        column = "check_in_photo_path" if kind == "check_in" else "check_out_photo_path"
        work_date = parse_qs(urlparse(self.path).query).get("date", [""])[0]
        with get_db() as conn:
            if work_date:
                row = conn.execute(f"""SELECT ea.user_id,a.{column} AS photo_path
                    FROM event_assignments ea JOIN attendance_days a ON a.assignment_id=ea.id
                    WHERE ea.id=? AND a.work_date=?""", (assignment_id, work_date)).fetchone()
            else:
                row = conn.execute(f"""SELECT ea.user_id,a.{column} AS photo_path
                    FROM event_assignments ea JOIN attendance a ON a.assignment_id=ea.id
                    WHERE ea.id=?""", (assignment_id,)).fetchone()
        if not row or not row["photo_path"]:
            self.json_error(HTTPStatus.NOT_FOUND, "Foto absensi tidak ditemukan.")
            return
        if row["user_id"] == user["id"]:
            try:
                self.require(user, "attendance.self")
            except PermissionError as exc:
                self.json_error(HTTPStatus.FORBIDDEN, str(exc))
                return
        elif "attendance.manage" not in user["permissions"]:
            self.json_error(HTTPStatus.FORBIDDEN, "Anda tidak diizinkan melihat foto absensi ini.")
            return
        root = attendance_storage_dir().resolve()
        photo_path = (root / row["photo_path"]).resolve()
        if not photo_path.is_relative_to(root) or not photo_path.is_file():
            self.json_error(HTTPStatus.NOT_FOUND, "File foto absensi tidak ditemukan.")
            return
        try:
            content = photo_path.read_bytes()
        except OSError:
            self.json_error(HTTPStatus.NOT_FOUND, "File foto absensi tidak ditemukan.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", "inline; filename=attendance.jpg")
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(content)

    def handle_advance(self, user: dict, event_id: int, payload: dict) -> None:
        action = payload.get("action")
        transitions = {
            "submit": ("advances.request", {"not_submitted", "rejected"}, "submitted"),
            "approve": ("advances.approve", {"submitted"}, "approved"),
            "reject": ("advances.approve", {"submitted"}, "rejected"),
            "transfer": ("advances.transfer", {"approved"}, "transferred"),
        }
        if action not in transitions:
            raise ValueError("Tindakan uang jalan tidak dikenal.")
        permission, allowed, new_status = transitions[action]
        if action != "submit":
            self.require(user, permission)
        with get_db() as conn:
            event = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                raise ValueError("Event tidak ditemukan.")
            current = conn.execute("SELECT * FROM cash_advances WHERE event_id=?", (event_id,)).fetchone()
            if not current:
                conn.execute("INSERT INTO cash_advances(event_id,status) VALUES(?,'not_submitted')", (event_id,))
                current = conn.execute("SELECT * FROM cash_advances WHERE event_id=?", (event_id,)).fetchone()
            if current["status"] not in allowed:
                raise ValueError("Status uang jalan belum dapat diubah ke tahap tersebut.")
            stamp = now_iso()
            if action == "submit":
                assignment = conn.execute("SELECT id FROM event_assignments WHERE event_id=? AND user_id=? AND assignment_type='pic'", (event_id,user["id"])).fetchone()
                if not assignment:
                    raise PermissionError("Pengajuan uang jalan hanya dapat dilakukan PIC yang ditugaskan.")
                conn.execute("UPDATE cash_advances SET requested_by=?,requested_at=?,status=?,updated_at=? WHERE event_id=?", (user["id"],stamp,new_status,stamp,event_id))
            elif action in {"approve", "reject"}:
                conn.execute("UPDATE cash_advances SET reviewed_by=?,reviewed_at=?,status=?,updated_at=? WHERE event_id=?", (user["id"],stamp,new_status,stamp,event_id))
            else:
                conn.execute("UPDATE cash_advances SET transfer_recorded_by=?,transferred_at=?,status=?,updated_at=? WHERE event_id=?", (user["id"],stamp,new_status,stamp,event_id))
            audit(conn,user["id"],"cash_advance",event_id,action,{"from":current["status"],"to":new_status})
        self.json_response({"ok":True,"status":new_status})

    def handle_advance_document_upload(self,user: dict,event_id: int,payload: dict) -> None:
        content,mime,extension,filename=parse_cash_advance_document(payload.get("file"),payload.get("filename"))
        with get_db() as conn:
            pic=conn.execute("SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=? AND assignment_type='pic'",(event_id,user["id"])).fetchone()
            advance=conn.execute("SELECT status FROM cash_advances WHERE event_id=?",(event_id,)).fetchone()
        if not pic: raise PermissionError("Hanya PIC yang dijadwalkan pada event ini yang dapat mengunggah dokumen.")
        if not advance or advance["status"]!="transferred": raise ValueError("Dokumen pertanggungjawaban dapat diunggah setelah uang jalan ditransfer.")
        saved=store_cash_advance_document(event_id,content,extension)
        try:
            with DB_LOCK,get_db() as conn:
                cur=conn.execute("""INSERT INTO cash_advance_documents(event_id,uploaded_by,original_filename,storage_path,mime_type,file_size)
                    VALUES(?,?,?,?,?,?)""",(event_id,user["id"],filename,saved,mime,len(content)))
                doc_id=cur.lastrowid
                audit(conn,user["id"],"cash_advance_document",doc_id,"uploaded",{"event_id":event_id,"filename":filename,"file_size":len(content)})
        except Exception:
            (cash_advance_storage_dir()/saved).unlink(missing_ok=True)
            raise
        self.json_response({"ok":True,"document_id":doc_id,"filename":filename})

    def handle_advance_document_download(self,user: dict,document_id: int) -> None:
        with get_db() as conn:
            row=conn.execute("""SELECT d.*,ca.status advance_status FROM cash_advance_documents d
                LEFT JOIN cash_advances ca ON ca.event_id=d.event_id WHERE d.id=?""",(document_id,)).fetchone()
            if not row: self.json_error(HTTPStatus.NOT_FOUND,"Dokumen tidak ditemukan."); return
            allowed="advances.documents.read_all" in user["permissions"]
            if not allowed:
                allowed=bool(conn.execute("SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=? AND assignment_type='pic'",(row["event_id"],user["id"])).fetchone())
        if not allowed:
            self.json_error(HTTPStatus.FORBIDDEN,"Anda tidak dapat mengunduh dokumen event ini."); return
        root=cash_advance_storage_dir().resolve(); path=(root/row["storage_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file(): self.json_error(HTTPStatus.NOT_FOUND,"File dokumen tidak ditemukan."); return
        safe_filename=re.sub(r"[^A-Za-z0-9._ -]","_",row["original_filename"]).strip() or "dokumen"
        content=path.read_bytes(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type",row["mime_type"])
        self.send_header("Content-Length",str(len(content))); self.send_header("Content-Disposition",f'attachment; filename="{safe_filename}"')
        self.send_header("Cache-Control","private, no-store"); self.end_headers(); self.wfile.write(content)

    def handle_notification_read(self, user: dict, payload: dict) -> None:
        with DB_LOCK,get_db() as conn:
            notices.mark_read(conn, user, payload.get('keys'), live_notifications(conn, user), payload.get('all') is True)
            result=notifications_payload(conn,user,user_events(conn,user))
        self.json_response(result)

    def handle_push_action(self, user, path, payload):
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if path == '/api/notifications/preferences':
                result = {'preferences': notices.save_preferences(conn, user['id'], payload)}
            elif path == '/api/push/register':
                if not push_delivery.config()['ready']:
                    raise ValueError('Pengiriman push belum dikonfigurasi oleh pengelola.')
                result = {'device_id': notices.device_register(conn, user, payload)}
            else:
                device_id = payload.get('device_id')
                if type(device_id) is not int or not 0<device_id<=2**63-1:
                    raise ValueError('Pilih perangkat yang valid.')
                device = conn.execute('SELECT * FROM push_devices WHERE id=? AND user_id=?', (device_id, user['id'])).fetchone()
                if not device:
                    raise PermissionError('Perangkat tidak tersedia untuk akun ini.')
                if path == '/api/push/unregister':
                    notices.revoke_devices(conn, user['id'], device_id=device_id)
                    result = {'ok': True}
                else:
                    if not device['active'] or device['session_hash'] != user['session_hash'] or not push_delivery.config()['ready']:
                        raise ValueError('Aktifkan push pada perangkat dan sesi ini dahulu.')
                    recent = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(timespec='microseconds')
                    if conn.execute("SELECT 1 FROM notifications WHERE user_id=? AND kind='push_test' AND created_at>?", (user['id'], recent)).fetchone():
                        raise ValueError('Tunggu satu menit sebelum mengirim tes berikutnya.')
                    ids = notices.emit(conn, [user['id']], key='test:'+secrets.token_hex(16), category='operations', kind='push_test',
                        title='Tes notifikasi perangkat', body='Buka pengaturan notifikasi untuk memeriksa status pengiriman.', target_kind='inbox', urgent=True)
                    for nid in ids:
                        conn.execute('DELETE FROM push_deliveries WHERE notification_id=? AND device_id!=?', (nid, device_id))
                    result = {'ok': True, 'message': 'Tes masuk antrean pengiriman. Status dikirim berarti diterima layanan push, bukan bukti telah dibaca.'}
            audit(conn, user['id'], 'notifications', None, path.rsplit('/', 1)[1])
        self.json_response(result)

    def handle_vehicle_save(self, user: dict, payload: dict) -> None:
        self.require(user,"events.assign")
        values={}
        for field,limit in (("name",100),("plate_number",24),("vendor_name",160)):
            raw=payload.get(field,"")
            if not isinstance(raw,str) or len(raw.strip())>limit:
                raise ValueError("Nama, nomor polisi, atau vendor terlalu panjang/tidak valid.")
            values[field]=raw.strip()
        if not values["name"]:
            raise ValueError("Isi nama kendaraan.")
        values["plate_number"]=re.sub(r"\s+","",values["plate_number"]).upper()
        ownership=payload.get("ownership","internal")
        if ownership not in {"internal","rental"}:
            raise ValueError("Pilih jenis kendaraan internal atau sewa.")
        if ownership=="rental" and not values["vendor_name"]:
            raise ValueError("Isi vendor untuk kendaraan sewa.")
        if ownership=="internal": values["vendor_name"]=""
        active=payload.get("active",True)
        if not isinstance(active,bool): raise ValueError("Status kendaraan tidak valid.")
        raw_id=payload.get("id")
        if raw_id is not None and (isinstance(raw_id,bool) or not str(raw_id).isdigit()):
            raise ValueError("Kendaraan tidak valid.")
        with DB_LOCK,get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if raw_id is not None:
                vehicle_id=int(raw_id)
                if not conn.execute("SELECT id FROM vehicles WHERE id=?",(vehicle_id,)).fetchone():
                    raise ValueError("Kendaraan tidak ditemukan.")
            duplicate=conn.execute("SELECT id FROM vehicles WHERE plate_number=? AND id!=?",(values["plate_number"],raw_id or 0)).fetchone() if values["plate_number"] else None
            if duplicate: raise ValueError("Nomor polisi sudah terdaftar. Gunakan kendaraan yang ada.")
            args=(values["name"],values["plate_number"],ownership,values["vendor_name"],int(active),now_iso())
            if raw_id is not None:
                conn.execute("UPDATE vehicles SET name=?,plate_number=?,ownership=?,vendor_name=?,active=?,updated_at=? WHERE id=?",(*args,vehicle_id))
            else:
                vehicle_id=conn.execute("INSERT INTO vehicles(name,plate_number,ownership,vendor_name,active,updated_at) VALUES(?,?,?,?,?,?)",args).lastrowid
            audit(conn,user["id"],"vehicle",vehicle_id,"updated" if raw_id else "created",values)
        self.json_response({"ok":True,"id":vehicle_id})

    def handle_logistics_save(self, user: dict, event_id: int, payload: dict) -> None:
        self.require(user,"events.assign")
        with DB_LOCK,get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone()
            if not row: raise ValueError("Event tidak ditemukan.")
            event=dict(row)
            if not can_manage_logistics(user,event):
                raise PermissionError("Mapping hanya dapat diubah oleh coordinator event ini atau Head Operations/Admin.")
            if event["status"]=="cancelled": raise ValueError("Event dibatalkan; logistik tidak dapat diubah.")
            values=validate_logistics(conn,event,payload)
            previous = conn.execute('SELECT * FROM event_operations WHERE event_id=?', (event_id,)).fetchone()
            changed_logistics = previous is None or any(previous[k]!=v for k,v in values.items())
            values.update(updated_by=user["id"],updated_at=now_iso())
            columns=list(values)
            updates=",".join(f"{key}=excluded.{key}" for key in columns)
            conn.execute(f"INSERT INTO event_operations(event_id,{','.join(columns)}) VALUES({','.join('?' for _ in range(len(columns)+1))}) ON CONFLICT(event_id) DO UPDATE SET {updates}",
                [event_id,*values.values()])
            conn.execute("UPDATE events SET coordinator_id=COALESCE(coordinator_id,?) WHERE id=?",(user["id"],event_id))
            audit(conn,user["id"],"event_logistics",event_id,"updated",{"vehicle_id":values["vehicle_id"]})
            saved = logistics_detail(conn,event_id)
            if changed_logistics:
                recipients=[r[0] for r in conn.execute('SELECT DISTINCT user_id FROM event_assignments WHERE event_id=?',(event_id,))]
                notices.emit(conn,recipients,key='logistics:'+secrets.token_hex(16),category='schedule',kind='logistics_changed',
                    title='Rencana transportasi diperbarui',body='Buka detail event untuk melihat kendaraan dan perjalanan terbaru.',event=event,tab='logistics')
        # Return the committed values; the form need not refetch and overwrite its inputs.
        self.json_response({"ok":True,"logistics":saved})

    def handle_assignment(self, user: dict, event_id: int, payload: dict) -> None:
        self.require(user,"events.assign")
        action = payload.get("action", "add")
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                raise ValueError("Event tidak ditemukan.")
            if event["status"] == "completed":
                raise ValueError("Event yang sudah clear tidak dapat mengubah penugasan.")
            if event["status"] == "cancelled" and action not in {'remove', 'skills'}:
                raise ValueError('Event yang dibatalkan tidak dapat menerima penugasan baru.')
            report = conn.execute('SELECT status FROM event_closing_reports WHERE event_id=?', (event_id,)).fetchone()
            if action != 'skills' and report and report['status'] in {'submitted', 'accepted'}:
                raise ValueError('Tim terkunci karena laporan sudah dikirim atau diterima. Minta revisi saat review bila tim perlu dikoreksi.')
            if action == "skills":
                assignment_id = int(payload.get("assignment_id", 0))
                assignment = conn.execute("SELECT id FROM event_assignments WHERE id=? AND event_id=?", (assignment_id,event_id)).fetchone()
                if not assignment:
                    raise ValueError("Penugasan tidak ditemukan.")
                try:
                    skill_ids = sorted({int(value) for value in payload.get("skill_ids", [])})
                except (TypeError, ValueError) as exc:
                    raise ValueError("Pilih skill khusus yang valid.") from exc
                selected = []
                for skill_id in skill_ids:
                    row = conn.execute("SELECT id,name,extra_fee_rupiah FROM skills WHERE id=? AND active=1", (skill_id,)).fetchone()
                    if not row:
                        raise ValueError("Salah satu skill sudah tidak aktif.")
                    selected.append(row)
                conn.execute("DELETE FROM assignment_skills WHERE assignment_id=?", (assignment_id,))
                conn.executemany("INSERT INTO assignment_skills(assignment_id,skill_id,extra_fee_rupiah,skill_name_snapshot) VALUES(?,?,?,?)",
                                 [(assignment_id,row["id"],row["extra_fee_rupiah"],row["name"]) for row in selected])
                audit(conn,user["id"],"event_assignment",assignment_id,"skills_updated",{"skill_ids":skill_ids})
                self.json_response({"ok":True,"skill_ids":skill_ids})
                return
            if action == "days":
                assignment_id = int(payload.get("assignment_id", 0))
                assignment = conn.execute("SELECT * FROM event_assignments WHERE id=? AND event_id=?", (assignment_id,event_id)).fetchone()
                if not assignment:
                    raise ValueError("Penugasan tidak ditemukan.")
                if not eventdays.is_multiday(event):
                    raise ValueError("Pemetaan hari hanya untuk event lebih dari satu hari.")
                days = eventdays.normalize_days(event, payload.get("days"))
                old_days = eventdays.assignment_days(conn, event, assignment_id)
                locked = [r["work_date"] for r in conn.execute(
                    "SELECT work_date FROM attendance_days WHERE assignment_id=? AND status!='not_started'", (assignment_id,))]
                blocked = sorted(set(locked) - set(days))
                if blocked:
                    raise ValueError("Hari " + ", ".join(blocked) + " sudah memiliki absensi dan tidak dapat dilepas.")
                added = sorted(set(days) - set(old_days))
                availability = staff_availability(conn, event, assignment["user_id"], days=added) if added else None
                if availability and availability["needs_confirmation"]:
                    acknowledged = payload.get("schedule_confirmed") is True and payload.get("schedule_ack_signature") == availability["signature"]
                    if not acknowledged:
                        self.json_response({"ok": False, "error": "Ada event lain pada hari yang sama. Periksa peringatan, lalu konfirmasi atau ganti orang.",
                                            "availability": availability}, HTTPStatus.CONFLICT)
                        return
                eventdays.save_days(conn, event, assignment_id, days)
                audit(conn,user["id"],"event_assignment",assignment_id,"days_updated",{"event_id":event_id,"days":days,"previous":old_days})
                notices.assignment(conn, event, assignment["user_id"], assignment_id, kind=assignment["assignment_type"])
                conn.commit()
                self.json_response({"ok":True,"days":days})
                return
            if action == "remove":
                assignment_id = int(payload.get("assignment_id", 0))
                assignment = conn.execute("SELECT * FROM event_assignments WHERE id=? AND event_id=?", (assignment_id,event_id)).fetchone()
                if not assignment:
                    raise ValueError("Penugasan tidak ditemukan.")
                conn.execute("DELETE FROM event_assignments WHERE id=?", (assignment_id,))
                notices.assignment(conn, event, assignment['user_id'], assignment_id, removed=True)
                audit(conn,user["id"],"event_assignment",assignment_id,"removed",{"event_id":event_id,"user_id":assignment["user_id"]})
                self.json_response({"ok":True})
                return
            try:
                staff_id = int(payload.get("user_id", 0))
                position_id = int(payload.get("position_id", 0)) or None
            except (TypeError, ValueError) as exc:
                raise ValueError("Pilih staff yang akan ditugaskan.") from exc
            kind = payload.get("assignment_type")
            if kind not in {"crew","pic"}:
                raise ValueError("Jenis penugasan harus Crew atau PIC Event.")
            if position_id is None:
                position_name = "PIC Event" if kind == "pic" else "Crew"
                position = conn.execute("SELECT id FROM positions WHERE name=? AND active=1", (position_name,)).fetchone()
                position_id = position["id"] if position else None
            else:
                position = conn.execute("SELECT id,name FROM positions WHERE id=? AND active=1", (position_id,)).fetchone()
                if not position:
                    raise ValueError("Posisi event sudah tidak aktif.")
                if (kind == "pic" and position["name"] != "PIC Event") or (kind == "crew" and position["name"] == "PIC Event"):
                    raise ValueError("Posisi tim harus sesuai dengan peran Crew atau PIC Event.")
            target = conn.execute("SELECT id,full_name FROM users WHERE id=? AND active=1", (staff_id,)).fetchone()
            if not target:
                raise ValueError("Akun staff tidak ditemukan.")
            target_roles, _ = role_data(conn,staff_id)
            target_codes = {x["code"] for x in target_roles}
            if not target_codes & {"crew","pic_event"}:
                raise ValueError("Pilih akun dengan akses Crew atau PIC Event.")
            multi_day = eventdays.is_multiday(event)
            chosen_days = eventdays.normalize_days(event, payload.get("days")) if multi_day else None
            availability = staff_availability(conn, event, staff_id, days=chosen_days)
            acknowledged = payload.get('schedule_confirmed') is True and payload.get('schedule_ack_signature') == availability['signature']
            if availability['needs_confirmation'] and not acknowledged:
                message = 'Ada event lain pada hari yang sama. Periksa peringatan, lalu pilih Tetap Jadwalkan atau Ganti Orang.'
                self.json_response({'ok': False, 'error': message, 'availability': availability}, HTTPStatus.CONFLICT)
                return
            base_rate = conn.execute("SELECT base_fee_rupiah FROM user_fee_rates WHERE user_id=? AND effective_from<=wib_date(?) ORDER BY effective_from DESC LIMIT 1",
                                     (staff_id,event["starts_at"])).fetchone()
            base_fee_snapshot = base_rate["base_fee_rupiah"] if base_rate else 0
            ack = availability['signature'] if availability['needs_confirmation'] and acknowledged else ''
            cur = conn.execute("""INSERT INTO event_assignments(event_id,user_id,assignment_type,position_id,base_fee_snapshot_rupiah,
                schedule_ack_signature,schedule_acknowledged_at,schedule_acknowledged_by) VALUES(?,?,?,?,?,?,?,?)""",
                (event_id,staff_id,kind,position_id,base_fee_snapshot,ack,now_iso() if ack else None,user['id'] if ack else None))
            conn.execute("UPDATE events SET coordinator_id=COALESCE(coordinator_id,?) WHERE id=?",(user["id"],event_id))
            conn.execute("INSERT INTO attendance(assignment_id) VALUES(?)", (cur.lastrowid,))
            if multi_day:
                eventdays.save_days(conn, event, cur.lastrowid, chosen_days)
            try:
                skill_ids = sorted({int(value) for value in payload.get("skill_ids", [])})
            except (TypeError, ValueError) as exc:
                raise ValueError("Pilih skill khusus yang valid.") from exc
            for skill_id in skill_ids:
                skill = conn.execute("SELECT id,name,extra_fee_rupiah FROM skills WHERE id=? AND active=1", (skill_id,)).fetchone()
                if not skill:
                    raise ValueError("Salah satu skill sudah tidak aktif.")
                conn.execute("INSERT INTO assignment_skills(assignment_id,skill_id,extra_fee_rupiah,skill_name_snapshot) VALUES(?,?,?,?)",
                             (cur.lastrowid,skill["id"],skill["extra_fee_rupiah"],skill["name"]))
            audit(conn,user["id"],"event_assignment",cur.lastrowid,"assigned",{"event_id":event_id,"user_id":staff_id,"type":kind,"position_id":position_id,"skill_ids":skill_ids,"days":chosen_days,
                "schedule_ack_signature":ack,"schedule_warning":availability if ack else None})
            assignment_id = cur.lastrowid
            notices.assignment(conn, event, staff_id, assignment_id, kind=kind)
        self.json_response({"ok":True,"assignment_id":assignment_id})

    def handle_closing_save(self, user: dict, event_id: int, payload: dict) -> None:
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            event = conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
            if not event:
                raise ValueError('Event tidak ditemukan.')
            result = save_closing(conn, dict(event), user, payload, parse_profile_image)
            audit(conn, user['id'], 'event', event_id, 'closing_' + payload['action'], {'version': result['version']})
        self.json_response(result)

    def handle_event_performance(self, user: dict, event_id: int, payload: dict) -> None:
        action = payload.get("action", "submit")
        submissions = payload.get("reviews")
        if action == "clear":
            self.require(user, "events.clear")
            with DB_LOCK, get_db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                event = conn.execute("SELECT id,status,coordinator_id FROM events WHERE id=?", (event_id,)).fetchone()
                if not event:
                    raise ValueError("Event tidak ditemukan.")
                if event["status"] == "cancelled":
                    raise ValueError("Event yang dibatalkan tidak dapat di-clear.")
                if event["status"] != "completed":
                    if not can_review_closing(user, event):
                        raise PermissionError('Hanya coordinator event ini atau Administrator yang dapat menandai clear.')
                    report = conn.execute('SELECT status FROM event_closing_reports WHERE event_id=?', (event_id,)).fetchone()
                    if not report or report['status'] != 'accepted':
                        raise ValueError('Laporan penutupan PIC harus diterima sebelum event dapat di-clear.')
                    unresolved = event_unresolved_attendance(conn, event_id)
                    if unresolved:
                        raise ValueError('Masih ada absensi yang belum terselesaikan: ' + ', '.join(unresolved[:5])
                                         + (f' dan {len(unresolved) - 5} lainnya' if len(unresolved) > 5 else '')
                                         + '. Koreksi atau tandai tidak hadir terlebih dahulu.')
                    stamp = now_iso()
                    conn.execute("""UPDATE events SET status='completed',completed_at=?,completed_by=?,updated_at=?
                        WHERE id=?""", (stamp,user["id"],stamp,event_id))
                    audit(conn,user["id"],"event",event_id,"cleared",{})
            self.json_response({"ok":True,"status":"completed"})
            return
        if action != "submit":
            raise ValueError("Aksi performance event tidak valid.")
        self.require(user, "kpi.evaluate_crew")
        if not isinstance(submissions, list):
            raise ValueError("Data penilaian event tidak valid.")
        with DB_LOCK, get_db() as conn:
            event = conn.execute("SELECT id,status FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                raise ValueError("Event tidak ditemukan.")
            if event["status"] == "cancelled":
                raise ValueError("Event yang dibatalkan tidak dapat di-clear.")
            if event["status"] != "completed":
                raise ValueError("Tandai event clear sebelum mengisi penilaian Crew/PIC.")
            assignments = [dict(row) for row in conn.execute("""
                SELECT id,user_id,assignment_type FROM event_assignments
                WHERE event_id=? AND assignment_type IN ('crew','pic') ORDER BY id
            """, (event_id,)).fetchall()]
            existing_ids = {row["assignment_id"] for row in conn.execute("""
                SELECT epr.assignment_id FROM event_performance_reviews epr
                JOIN event_assignments ea ON ea.id=epr.assignment_id WHERE ea.event_id=?
            """, (event_id,)).fetchall()}
            pending = [row for row in assignments if row["id"] not in existing_ids]
            expected_ids = {row["id"] for row in pending}
            parsed = {}
            for review in submissions:
                if not isinstance(review, dict):
                    raise ValueError("Data penilaian crew/PIC tidak valid.")
                try:
                    assignment_id = int(review.get("assignment_id", 0))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Penugasan pada penilaian tidak valid.") from exc
                if assignment_id in parsed:
                    raise ValueError("Penugasan crew/PIC tidak boleh dinilai dua kali.")
                parsed[assignment_id] = review
            if set(parsed) != expected_ids:
                raise ValueError("Nilai semua crew dan PIC yang ditugaskan sebelum menandai event clear.")
            stamp = now_iso()
            for assignment in pending:
                review = parsed[assignment["id"]]
                scores = review.get("scores")
                if not isinstance(scores, dict):
                    raise ValueError("Pilih skor untuk setiap aspek penilaian.")
                note = review.get("note", "")
                if not isinstance(note, str):
                    raise ValueError("Catatan evaluasi tidak valid.")
                insert_event_performance_review(conn, assignment["id"], user["id"], scores, note.strip())
            conn.execute("""UPDATE events SET completed_at=COALESCE(completed_at,?),
                completed_by=COALESCE(completed_by,?),updated_at=? WHERE id=?""",
                (stamp,user["id"],stamp,event_id))
            audit(conn,user["id"],"event",event_id,"cleared_with_performance",{
                "reviews_created": len(pending),
                "assignment_ids": sorted(expected_ids),
            })
        self.json_response({"ok":True,"status":"completed","reviews_created":len(pending)})

    def handle_group(self, user: dict, event_id: int, payload: dict) -> None:
        self.require(user,"events.whatsapp.manage")
        status = payload.get("status")
        if status not in {"not_created","group_created","invites_sent"}:
            raise ValueError("Status grup WhatsApp tidak dikenal.")
        link = str(payload.get("group_link", "")).strip()
        if link and not link.startswith("https://"):
            raise ValueError("Tautan grup harus menggunakan HTTPS.")
        with get_db() as conn:
            if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
                raise ValueError("Event tidak ditemukan.")
            old = conn.execute("SELECT status FROM event_communications WHERE event_id=?", (event_id,)).fetchone()
            conn.execute("""INSERT INTO event_communications(event_id,status,group_link,updated_by,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,group_link=excluded.group_link,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                         (event_id,status,link,user["id"],now_iso()))
            conn.execute("UPDATE events SET coordinator_id=COALESCE(coordinator_id,?) WHERE id=?",(user["id"],event_id))
            audit(conn,user["id"],"event_communication",event_id,"status_changed",{"from":old["status"] if old else None,"to":status})
        self.json_response({"ok":True,"status":status})

    def handle_warehouse(self, user: dict, event_id: int, payload: dict) -> None:
        self.require(user,"warehouse.update")
        action = payload.get("action")
        transitions = {
            "start": ("needs_prep", "preparing"),
            "ready": ("preparing", "ready"),
            "dispatch": ("ready", "waiting_return"),
            "returned": ("waiting_return", "returned"),
            "issue": (None, "issue"),
            "resume": ("issue", "preparing"),
        }
        if action not in transitions:
            raise ValueError("Tindakan warehouse tidak dikenal.")
        with get_db() as conn:
            current = conn.execute("SELECT * FROM warehouse_checks WHERE event_id=?", (event_id,)).fetchone()
            if not current:
                conn.execute("INSERT INTO warehouse_checks(event_id,status) VALUES(?,'needs_prep')", (event_id,))
                current = conn.execute("SELECT * FROM warehouse_checks WHERE event_id=?", (event_id,)).fetchone()
            expected, new_status = transitions[action]
            if expected is not None and current["status"] != expected:
                raise ValueError("Status kesiapan alat sudah berubah. Muat ulang data.")
            stamp = now_iso()
            note = str(payload.get("note", "")).strip()[:500]
            if action == "ready":
                conn.execute("UPDATE warehouse_checks SET status=?,prepared_by=?,prepared_at=?,note=?,updated_at=? WHERE event_id=?", (new_status,user["id"],stamp,note,stamp,event_id))
            elif action == "returned":
                conn.execute("UPDATE warehouse_checks SET status=?,returned_by=?,returned_at=?,note=?,updated_at=? WHERE event_id=?", (new_status,user["id"],stamp,note,stamp,event_id))
            else:
                conn.execute("UPDATE warehouse_checks SET status=?,note=?,updated_at=? WHERE event_id=?", (new_status,note,stamp,event_id))
            audit(conn,user["id"],"warehouse_check",event_id,action,{"from":current["status"],"to":new_status,"note":note})
        self.json_response({"ok":True,"status":new_status})

    def handle_inhouse_schedule_update(self, actor: dict, payload: dict) -> None:
        self.require(actor, "app.configure")
        schedule = validate_inhouse_schedule(payload)
        stamp = now_iso()
        with DB_LOCK, get_db() as conn:
            previous = inhouse_schedule(conn)
            for key, value in schedule.items():
                conn.execute("""INSERT INTO app_settings(setting_key,setting_value,updated_by,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (f"inhouse_{key}", str(value), actor["id"], stamp))
            audit(conn, actor["id"], "app_settings", None, "inhouse_schedule_updated", {"previous": previous, "new": schedule})
        self.json_response({"ok": True, "schedule": schedule})

    def handle_manual_event_create(self, actor: dict, payload: dict) -> None:
        """Create an event by hand (no Google Calendar needed). Calendar sync never touches these."""
        self.require(actor, "events.assign")
        title = str(payload.get("title", "")).strip()
        if not 3 <= len(title) <= 120:
            raise ValueError("Judul event wajib diisi (3–120 karakter).")
        location = str(payload.get("location", "")).strip()[:200]
        wib = timezone(timedelta(hours=7))

        def moment(name: str, label: str) -> datetime:
            try:
                parsed = parse_iso(str(payload.get(name, "")))
            except ValueError:
                raise ValueError(f"{label} tidak valid.")
            parsed = parsed.replace(tzinfo=wib) if parsed.tzinfo is None else parsed.astimezone(wib)
            if not 2000 <= parsed.year <= 2100:
                raise ValueError(f"{label} tidak valid.")
            return parsed
        starts, ends = moment("starts_at", "Waktu mulai"), moment("ends_at", "Waktu selesai")
        if ends <= starts:
            raise ValueError("Waktu selesai harus setelah waktu mulai.")
        if ends - starts > timedelta(days=14):
            raise ValueError("Durasi event manual maksimal 14 hari.")
        requested_full_day = payload.get("is_full_day")
        if requested_full_day not in (None, True, False, 0, 1, "0", "1"):
            raise ValueError("Pilih Full day atau Non-full day.")
        is_full_day = int(requested_full_day in (True, 1, "1")) if requested_full_day is not None else int(ends - starts > timedelta(hours=24))
        requested_code = str(payload.get("project_code", "")).strip()
        if requested_code and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,49}", requested_code):
            raise ValueError("Kode project harus 3–50 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
        with DB_LOCK, get_db() as conn:
            if requested_code and conn.execute("SELECT 1 FROM events WHERE project_code=? COLLATE NOCASE", (requested_code,)).fetchone():
                raise ValueError(f"Kode {requested_code} sudah digunakan oleh event lain.")
            temporary = not requested_code
            operational = f"OPS-{datetime.now(WIB).strftime('%y%m%d')}-{secrets.token_hex(3).upper()}" if temporary else None
            code = requested_code or operational
            cur = conn.execute("""INSERT INTO events(project_code,operational_code,project_code_is_temporary,is_full_day_manual,is_manual,
                title,starts_at,ends_at,location,is_full_day,status,coordinator_id)
                VALUES(?,?,?,1,1,?,?,?,?,?,'scheduled',?)""",
                (code, operational, int(temporary), title, starts.isoformat(), ends.isoformat(), location, is_full_day, actor["id"]))
            event_id = cur.lastrowid
            conn.execute("INSERT INTO cash_advances(event_id,status) VALUES(?,'not_submitted')", (event_id,))
            conn.execute("INSERT INTO warehouse_checks(event_id,status) VALUES(?,'needs_prep')", (event_id,))
            conn.execute("INSERT INTO event_communications(event_id,status) VALUES(?,'not_created')", (event_id,))
            conn.execute("INSERT INTO design_tasks(event_id,title,status) VALUES(?,'Desain event','brief_needed')", (event_id,))
            audit(conn, actor["id"], "event", event_id, "manual_created", {"project_code": code, "starts_at": starts.isoformat(), "ends_at": ends.isoformat()})
        self.json_response({"ok": True, "event_id": event_id, "project_code": code, "temporary": temporary})

    def handle_manual_event_delete(self, actor: dict, event_id: int, payload: dict) -> None:
        """Remove a hand-made test event. Calendar events and anything already in a payroll batch are protected."""
        self.require(actor, "events.manual.delete")
        if payload.get("confirm") is not True:
            raise ValueError("Konfirmasi penghapusan diperlukan.")
        with DB_LOCK, get_db() as conn:
            event = conn.execute("SELECT id,project_code,is_manual FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                raise ValueError("Event tidak ditemukan.")
            if not event["is_manual"]:
                raise ValueError("Hanya event manual yang dapat dihapus. Event dari Calendar tidak dapat dihapus di sini.")
            if conn.execute("""SELECT 1 FROM payroll_assignment_claims c JOIN event_assignments ea ON ea.id=c.assignment_id
                    WHERE ea.event_id=?""", (event_id,)).fetchone():
                raise ValueError("Event ini sudah masuk batch payroll dan tidak dapat dihapus.")
            try:
                conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            except sqlite3.IntegrityError:
                raise ValueError("Event ini sudah memiliki data keuangan atau penutupan yang tidak dapat dihapus.")
            audit(conn, actor["id"], "event", event_id, "manual_deleted", {"project_code": event["project_code"]})
        self.json_response({"ok": True, "deleted": event_id})

    def handle_attendance_correction(self, user: dict, event_id: int, payload: dict) -> None:
        """Coordinator / Head Operations records attendance the crew forgot to submit.

        Only for days that are not yet checked out (self-recorded photo+GPS records are never rewritten),
        never for the corrector's own attendance, never for the future, and never once payroll has claimed it.
        """
        self.require(user, "attendance.correct")
        assignment_id = int(payload.get("assignment_id") or 0)
        note = str(payload.get("note", "")).strip()
        if len(note) < 5:
            raise ValueError("Alasan koreksi wajib diisi (minimal 5 karakter).")
        note = note[:300]
        with DB_LOCK, get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assignment = conn.execute("SELECT * FROM event_assignments WHERE id=? AND event_id=?", (assignment_id, event_id)).fetchone()
            event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not assignment or not event:
                raise ValueError("Penugasan tidak ditemukan.")
            if assignment["user_id"] == user["id"]:
                raise PermissionError("Anda tidak dapat mengoreksi absensi Anda sendiri. Minta Coordinator atau Head Operations lain.")
            if event["status"] in ("cancelled", "completed"):
                raise ValueError("Event yang dibatalkan atau sudah di-clear tidak dapat dikoreksi.")
            if conn.execute("SELECT 1 FROM payroll_assignment_claims WHERE assignment_id=?", (assignment_id,)).fetchone():
                raise ValueError("Absensi ini sudah masuk batch payroll dan tidak dapat dikoreksi.")
            multi = eventdays.is_multiday(event)
            today = eventdays.today_wib()
            if multi:
                work_date = str(payload.get("work_date") or "")
                if work_date not in eventdays.assignment_days(conn, event, assignment_id):
                    raise ValueError("Hari tersebut bukan jadwal kerja orang ini.")
                conn.execute("INSERT OR IGNORE INTO attendance_days(assignment_id,work_date) VALUES(?,?)", (assignment_id, work_date))
                table, where, key = "attendance_days", "assignment_id=? AND work_date=?", (assignment_id, work_date)
            else:
                work_date = eventdays.event_dates(event)[0]
                table, where, key = "attendance", "assignment_id=?", (assignment_id,)
            if work_date > today:
                raise ValueError("Hari yang belum berlangsung tidak dapat dikoreksi.")
            current = conn.execute(f"SELECT * FROM {table} WHERE {where}", key).fetchone()
            if not current:
                raise ValueError("Data absensi untuk penugasan ini tidak ditemukan.")
            if current["status"] == "checked_out":
                raise ValueError("Absensi yang sudah lengkap dengan foto dan lokasi tidak dapat diubah.")
            result = payload.get("result")
            if result not in ("present", "absent"):
                raise ValueError("Pilih hasil koreksi: hadir atau tidak hadir.")
            stamp = now_iso()
            previous = {"status": current["status"], "check_in_at": current["check_in_at"], "check_out_at": current["check_out_at"]}
            if result == "absent":
                if current["status"] == "checked_in":
                    raise ValueError("Orang ini sudah check-in. Isi jam pulang dengan hasil Hadir.")
                conn.execute(f"""UPDATE {table} SET status='absent',note=?,corrected_by=?,corrected_at=?,updated_by=?,updated_at=?
                    WHERE {where}""", (note, user["id"], stamp, user["id"], stamp, *key))
                new_values = {"status": "absent"}
            else:
                def parse_wib(name: str) -> datetime | None:
                    raw = payload.get(name)
                    if not raw:
                        return None
                    try:
                        parsed = parse_iso(str(raw))
                    except ValueError:
                        raise ValueError("Format jam tidak valid.")
                    return parsed.replace(tzinfo=timezone(timedelta(hours=7))) if parsed.tzinfo is None else parsed
                recorded_in = parse_iso(current["check_in_at"]) if current["check_in_at"] else None
                check_in = recorded_in or parse_wib("check_in_at")   # a self-recorded check-in is never overwritten
                check_out = parse_wib("check_out_at")
                if not check_in or not check_out:
                    raise ValueError("Jam masuk dan jam pulang wajib diisi.")
                if check_out <= check_in or check_out - check_in > timedelta(hours=24):
                    raise ValueError("Jam pulang harus setelah jam masuk dan tidak lebih dari 24 jam.")
                wib = timezone(timedelta(hours=7))
                if check_in.astimezone(wib).date().isoformat() != work_date:
                    raise ValueError("Jam masuk harus berada di tanggal hari kerja yang dikoreksi.")
                if check_out > datetime.now(timezone.utc):
                    raise ValueError("Jam pulang tidak boleh di masa depan.")
                conn.execute(f"""UPDATE {table} SET status='checked_out',check_in_at=?,check_out_at=?,note=?,corrected_by=?,corrected_at=?,
                    updated_by=?,updated_at=? WHERE {where}""",
                    (check_in.astimezone(wib).isoformat(), check_out.astimezone(wib).isoformat(), note, user["id"], stamp, user["id"], stamp, *key))
                new_values = {"status": "checked_out", "check_in_at": check_in.astimezone(wib).isoformat(), "check_out_at": check_out.astimezone(wib).isoformat()}
            if multi:
                eventdays.refresh_rollup(conn, event, assignment_id)
            audit(conn, user["id"], "attendance", assignment_id, "corrected",
                  {"work_date": work_date, "result": result, "note": note, "previous": previous, "new": new_values})
        self.json_response({"ok": True, "work_date": work_date, "result": result})

    def handle_attendance(self, user: dict, event_id: int, payload: dict) -> None:
        action=payload.get("action")
        if action=="correct":
            self.handle_attendance_correction(user,event_id,payload)
            return
        if action not in {"check_in","check_out","absent"}: raise ValueError("Tindakan absensi tidak dikenal.")
        assignment_id=payload.get("assignment_id"); saved_photo=None; location=None
        try:
            photo=None
            if action in {"check_in","check_out"}:
                photo=parse_attendance_photo(payload.get("photo"))
                location=parse_attendance_location(payload.get("location"))
            with DB_LOCK,get_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                assignment=conn.execute("SELECT * FROM event_assignments WHERE id=? AND event_id=?",(assignment_id,event_id)).fetchone()
                if not assignment: raise ValueError("Penugasan tidak ditemukan.")
                own=assignment["user_id"]==user["id"]
                if action=='absent': self.require(user,'attendance.manage')
                if own: self.require(user,"attendance.self")
                else:
                    self.require(user,"attendance.manage")
                    if action!="absent": raise PermissionError("Check-in dan check-out dengan foto serta lokasi harus dilakukan oleh akun yang ditugaskan.")
                current=conn.execute("SELECT * FROM attendance WHERE assignment_id=?",(assignment_id,)).fetchone()
                if not current: raise ValueError("Data absensi untuk penugasan ini tidak ditemukan.")
                event_row=conn.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone()
                work_date=None
                today=eventdays.today_wib(); yesterday=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
                if eventdays.is_multiday(event_row):
                    # Multi-day: attendance is recorded per mapped day. Check-in only on the day itself; check-out
                    # also the morning after, so a shift that ends past midnight can still be closed.
                    mine=eventdays.assignment_days(conn,event_row,int(assignment_id))
                    work_date=str(payload.get("work_date") or "")
                    if not work_date:
                        work_date=today
                        if action=="check_out":   # close the day that is actually open (may be yesterday's night shift)
                            open_days={r[0] for r in conn.execute("SELECT work_date FROM attendance_days WHERE assignment_id=? AND status='checked_in'",(assignment_id,))}
                            if today not in open_days and yesterday in open_days: work_date=yesterday
                    if work_date not in mine:
                        raise ValueError("Hari ini bukan jadwal kerja Anda pada event ini.")
                    if action=="check_in" and work_date!=today:
                        raise ValueError("Check-in hanya dapat dilakukan pada hari kerjanya.")
                    if action=="check_out" and work_date not in (today,yesterday):
                        raise ValueError("Check-out sudah lewat. Hubungi Coordinator untuk koreksi absensi.")
                    conn.execute("INSERT OR IGNORE INTO attendance_days(assignment_id,work_date) VALUES(?,?)",(assignment_id,work_date))
                    current=conn.execute("SELECT * FROM attendance_days WHERE assignment_id=? AND work_date=?",(assignment_id,work_date)).fetchone()
                elif action in {"check_in","check_out"}:
                    # Single-day events: crew can only check in on the event date (check-out also the day after, for
                    # shifts that end past midnight). Past that, the Coordinator corrects it.
                    event_dates=eventdays.event_dates(event_row)
                    span=event_dates[0] if len(event_dates)==1 else f"{event_dates[0]} s/d {event_dates[-1]}"
                    if action=="check_in" and today not in event_dates:
                        raise ValueError(f"Check-in hanya dapat dilakukan pada tanggal event ({span}).")
                    if action=="check_out" and today not in event_dates and yesterday not in event_dates:
                        raise ValueError(f"Check-out sudah lewat dari tanggal event ({span}). Hubungi Coordinator untuk koreksi absensi.")
                stamp=now_iso()
                if action in {"check_in","check_out"}:
                    saved_photo=store_attendance_photo(int(assignment_id),action,photo)
                    lat,lon,accuracy,_=location
                table="attendance_days" if work_date else "attendance"
                where="assignment_id=? AND work_date=?" if work_date else "assignment_id=?"
                key=(assignment_id,work_date) if work_date else (assignment_id,)
                if action=="check_in":
                    if current["status"]!="not_started": raise ValueError("Absensi masuk sudah tercatat.")
                    changed=conn.execute(f"""UPDATE {table} SET status='checked_in',check_in_at=?,check_in_photo_path=?,
                        check_in_latitude=?,check_in_longitude=?,check_in_accuracy_m=?,updated_by=?,updated_at=?
                        WHERE {where} AND status='not_started'""",(stamp,saved_photo,lat,lon,accuracy,user["id"],stamp,*key))
                elif action=="check_out":
                    if current["status"]!="checked_in": raise ValueError("Lakukan check-in terlebih dahulu atau check-out sudah tercatat.")
                    changed=conn.execute(f"""UPDATE {table} SET status='checked_out',check_out_at=?,check_out_photo_path=?,
                        check_out_latitude=?,check_out_longitude=?,check_out_accuracy_m=?,updated_by=?,updated_at=?
                        WHERE {where} AND status='checked_in'""",(stamp,saved_photo,lat,lon,accuracy,user["id"],stamp,*key))
                else:
                    note=str(payload.get("note","")).strip()[:300]
                    if current["status"]!="not_started": raise ValueError("Absensi yang sudah dimulai tidak dapat ditandai tidak hadir.")
                    changed=conn.execute(f"UPDATE {table} SET status='absent',note=?,updated_by=?,updated_at=? WHERE {where} AND status='not_started'",(note,user["id"],stamp,*key))
                if changed.rowcount!=1: raise ValueError("Absensi sudah diproses. Muat ulang jadwal untuk melihat status terbaru.")
                if work_date: eventdays.refresh_rollup(conn,event_row,int(assignment_id))
                audit(conn,user["id"],"attendance",assignment_id,action,{"work_date":work_date,"timestamp":stamp,"photo_saved":bool(saved_photo),
                    "latitude":location[0] if location else None,"longitude":location[1] if location else None,"accuracy_m":location[2] if location else None})
        except Exception:
            if saved_photo:
                try: (attendance_storage_dir()/saved_photo).unlink(missing_ok=True)
                except OSError: pass
            raise
        self.json_response({"ok":True,"action":action,"timestamp":stamp,
            "location":{"latitude":location[0],"longitude":location[1],"accuracy_m":location[2]} if location else None})

    def handle_design_status(self, user: dict, task_id: int, payload: dict) -> None:
        self.require(user,"design.update")
        statuses = {"brief_needed","in_progress","client_review","revision","approved"}
        new_status = payload.get("status")
        if new_status not in statuses:
            raise ValueError("Tahap desain tidak dikenal.")
        note = payload.get('note', '')
        if not isinstance(note, str) or len(note)>2000:
            raise ValueError('Catatan desain maksimal 2000 karakter.')
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = conn.execute("SELECT * FROM design_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("Tugas desain tidak ditemukan.")
            event = conn.execute('SELECT * FROM events WHERE id=?', (task['event_id'],)).fetchone()
            if not notices.can_event(conn, user, event['id']):
                raise PermissionError('Anda tidak memiliki akses desain event ini.')
            if 'design.read_all' not in user['permissions'] and task['assignee_id'] != user['id']:
                raise PermissionError('Tugas desain ini ditugaskan ke designer lain.')
            if event['status'] in {'completed', 'cancelled'}:
                raise ValueError('Desain event yang sudah clear atau dibatalkan tidak dapat diubah.')
            final_url = payload.get('final_url', task['final_url'])
            if not isinstance(final_url, str) or len(final_url)>2048:
                raise ValueError('Tautan desain tidak valid.')
            final_url = final_url.strip()
            parsed_url = urlparse(final_url)
            if final_url and (parsed_url.scheme != 'https' or not parsed_url.netloc or parsed_url.username or parsed_url.password):
                raise ValueError('Gunakan tautan desain HTTPS tanpa username/password.')
            old = task["status"]
            changed = old != new_status or final_url != task['final_url']
            previous_note = conn.execute('SELECT body FROM design_comments WHERE task_id=? ORDER BY id DESC LIMIT 1', (task_id,)).fetchone()
            new_note = bool(note.strip() and (not previous_note or previous_note['body'] != note.strip()))
            if changed or new_note:
                conn.execute("UPDATE design_tasks SET status=?,final_url=?,updated_at=? WHERE id=?", (new_status,final_url,now_iso(),task_id))
                if new_note:
                    conn.execute('INSERT INTO design_comments(task_id,author_id,body,created_at) VALUES(?,?,?,?)', (task_id,user['id'],note.strip(),now_iso()))
                recipients = []
                if new_status == 'revision':
                    recipients = designers_for(conn, task, event)
                elif new_status in {'client_review','approved'}:
                    if event['coordinator_id']:
                        recipients.append(event['coordinator_id'])
                    if new_status == 'approved':
                        recipients += [r[0] for r in conn.execute("SELECT user_id FROM event_assignments WHERE event_id=? AND assignment_type='pic'", (event['id'],))]
                title = {'revision': 'Revisi desain diperlukan', 'client_review': 'Desain menunggu review', 'approved': 'Desain disetujui'}.get(new_status, 'Desain diperbarui')
                for uid in set(recipients):
                    target = notification_user(conn, uid)
                    if not target or not notices.can_event(conn, target, event['id']):
                        continue
                    notices.emit(conn, [uid], key='design-status:'+secrets.token_hex(16), category='design', kind='design_status',
                        title=title, body='Buka event untuk melihat status dan catatan terbaru.', event=event,
                        permission='design.update' if new_status=='revision' else '',
                        tab='design' if 'design.read' in target['permissions'] else 'overview')
                audit(conn,user["id"],"design_task",task_id,"status_changed",{"from":old,"to":new_status})
        self.json_response({"ok":True,"status":new_status})

    def handle_design_brief(self, user, task_id, payload):
        brief = payload.get('brief_text')
        if not isinstance(brief, str) or not 1<=len(brief.strip())<=10000:
            raise ValueError('Isi brief desain, maksimal 10000 karakter.')
        assignee = payload.get('assignee_id') or None
        if assignee is not None and (type(assignee) is not int or not 0<assignee<=2**63-1):
            raise ValueError('Designer tidak valid.')
        due = payload.get('due_at') or None
        if due:
            if not isinstance(due, str):
                raise ValueError('Tenggat desain tidak valid.')
            due = local_datetime(due).isoformat()
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = conn.execute('SELECT * FROM design_tasks WHERE id=?', (task_id,)).fetchone()
            if not task:
                raise ValueError('Tugas desain tidak ditemukan.')
            event = conn.execute('SELECT * FROM events WHERE id=?', (task['event_id'],)).fetchone()
            may_brief = 'design.assign' in user['permissions'] and ('design.update' in user['permissions'] or can_manage_logistics(user, dict(event)))
            if not notices.can_event(conn, user, event['id']) or not may_brief:
                raise PermissionError('Brief dan pemilihan designer hanya dapat dilakukan Head Design, Administrator, Head Operations, atau coordinator penanggung jawab.')
            if event['status'] != 'scheduled':
                raise ValueError('Brief hanya dapat diubah untuk event aktif.')
            target = notification_user(conn, assignee) if assignee else None
            if assignee and (not target or not {'design.read','design.update'} <= set(target['permissions']) or not notices.can_event(conn,target,event['id'])):
                raise ValueError('Pilih designer aktif yang memiliki akses event ini.')
            unchanged = task['brief_text']==brief.strip() and task['assignee_id']==assignee and task['due_at']==due and task['brief_sent_at']
            if not unchanged and (type(payload.get('version')) is not int or payload['version']!=task['brief_version']):
                self.json_error(HTTPStatus.CONFLICT, 'Brief sudah diperbarui pengguna lain. Muat ulang sebelum mengirim.'); return
            if not unchanged:
                version = task['brief_version']+1
                conn.execute('''UPDATE design_tasks SET brief_text=?,brief_version=?,brief_sent_at=?,brief_sent_by=?,assignee_id=?,due_at=?,updated_at=? WHERE id=?''',
                    (brief.strip(),version,now_iso(),user['id'],assignee,due,now_iso(),task_id))
                saved = dict(conn.execute('SELECT * FROM design_tasks WHERE id=?', (task_id,)).fetchone())
                notices.emit(conn, designers_for(conn,saved,event), key=f'design-brief:{task_id}:{version}', category='design', kind='design_brief',
                    title='Brief desain baru' if not task['brief_sent_at'] else 'Brief desain diperbarui',
                    body='Brief sudah dikirim. Buka rincian event untuk mulai mengerjakan.', event=event, tab='design', permission='design.update')
                audit(conn,user['id'],'design_task',task_id,'brief_sent',{'version':version,'assignee_id':assignee})
        self.json_response({'ok':True,'changed':not unchanged})

    def handle_rate_update(self, user: dict, payload: dict) -> None:
        self.require(user,"fees.manage")
        try:
            user_id = int(payload.get("user_id"))
            amount = rupiah(payload.get("base_fee_rupiah"), 'Fee dasar', 100_000_000)
        except (TypeError, ValueError) as exc:
            raise ValueError("Pilih akun dan masukkan fee dalam rupiah.") from exc
        if amount < 0 or amount > 100_000_000:
            raise ValueError("Fee harus berada di antara Rp0 dan Rp100.000.000.")
        effective_from = effective_date(payload.get('effective_from'))
        reason = change_reason(payload.get('reason', ''))
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            account = conn.execute("SELECT full_name FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
            if not account:
                raise ValueError("Akun tidak ditemukan.")
            history_id = append_change(conn, user_id, 'fee', amount, effective_from, reason, user)
            if history_id:
                audit(conn,user["id"],"user_fee_rate",user_id,"updated",{"account":account["full_name"],"new":amount,"effective_from":effective_from,'history_id':history_id})
        self.json_response({"ok":True,"user_id":user_id,"base_fee_rupiah":amount,"effective_from":effective_from})

    def handle_skill_update(self, user: dict, payload: dict) -> None:
        self.require(user,"skills.manage")
        name = str(payload.get("name", "")).strip()
        if len(name) < 2 or len(name) > 60:
            raise ValueError("Nama skill harus terdiri dari 2–60 karakter.")
        try:
            amount = rupiah(payload.get("extra_fee_rupiah", 0), 'Fee skill', 100_000_000)
        except (TypeError, ValueError) as exc:
            raise ValueError("Masukkan fee tambahan dalam rupiah.") from exc
        if amount < 0 or amount > 100_000_000:
            raise ValueError("Fee skill harus berada di antara Rp0 dan Rp100.000.000.")
        skill_id = payload.get("id")
        with get_db() as conn:
            if skill_id:
                skill_id = int(skill_id)
                old = conn.execute("SELECT name,extra_fee_rupiah FROM skills WHERE id=?", (skill_id,)).fetchone()
                if not old:
                    raise ValueError("Skill tidak ditemukan.")
                conn.execute("UPDATE skills SET name=?,extra_fee_rupiah=? WHERE id=?", (name,amount,skill_id))
                audit(conn,user["id"],"skill",skill_id,"updated",{"old_name":old["name"],"name":name,"old_fee":old["extra_fee_rupiah"],"fee":amount})
            else:
                cur = conn.execute("INSERT INTO skills(name,extra_fee_rupiah,active) VALUES(?,?,1)", (name,amount))
                skill_id = cur.lastrowid
                audit(conn,user["id"],"skill",skill_id,"created",{"name":name,"fee":amount})
        self.json_response({"ok":True,"id":skill_id,"name":name,"extra_fee_rupiah":amount})

    def handle_configure_appearance(self, actor: dict, payload: dict) -> None:
        self.require(actor, "app.configure")
        colors = payload.get("colors")
        if not isinstance(colors, dict) or set(colors) != set(BRAND_DEFAULTS):
            raise ValueError("Lengkapi semua warna tema aplikasi.")
        for key, value in colors.items():
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("Warna harus berupa kode HEX enam digit.")
        stamp = now_iso()
        cache_version = secrets.token_hex(8)
        with DB_LOCK, get_db() as conn:
            for key, value in colors.items():
                conn.execute("""INSERT INTO app_settings(setting_key,setting_value,updated_by,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (f"brand_{key}", value.lower(), actor["id"], stamp))
            conn.execute("""INSERT INTO app_settings(setting_key,setting_value,updated_by,updated_at) VALUES('brand_updated_at',?,?,?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (cache_version, actor["id"], stamp))
            audit(conn, actor["id"], "app_branding", None, "colors_updated", colors)
            result = branding_payload(conn)
        self.json_response({"ok": True, "branding": result})

    def handle_configure_asset(self, actor: dict, payload: dict) -> None:
        self.require(actor, "app.configure")
        kind = str(payload.get("kind", ""))
        if kind not in {"logo", "favicon"}:
            raise ValueError("Pilih aset logo atau favicon.")
        content, mime, extension = parse_brand_asset(payload.get("image"), kind)
        root = branding_storage_dir()
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        root.chmod(0o750)
        filename = f"{kind}-{secrets.token_hex(10)}.{extension}"
        target = root / filename
        temporary = root / f".{filename}.tmp"
        temporary.write_bytes(content)
        temporary.chmod(0o640)
        os.replace(temporary, target)
        stamp = now_iso()
        cache_version = secrets.token_hex(8)
        with DB_LOCK, get_db() as conn:
            for key, value in ((f"brand_{kind}_filename", filename), (f"brand_{kind}_mime", mime), ("brand_updated_at", cache_version)):
                conn.execute("""INSERT INTO app_settings(setting_key,setting_value,updated_by,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (key, value, actor["id"], stamp))
            audit(conn, actor["id"], "app_branding", None, f"{kind}_updated", {"mime": mime, "bytes": len(content)})
            result = branding_payload(conn)
        self.json_response({"ok": True, "branding": result})

    def handle_configure_roles(self, actor: dict, payload: dict) -> None:
        self.require(actor, "app.configure")
        role_code = str(payload.get("role", "")).strip()
        if role_code not in dict(ROLE_LIST) or role_code == "administrator":
            raise ValueError("Role Administrator dikelola tetap untuk menjaga akses pemulihan.")
        reset_default = payload.get("reset_default") is True
        submitted = payload.get("permissions", [])
        if not reset_default:
            if not isinstance(submitted, list) or any(not isinstance(code, str) for code in submitted):
                raise ValueError("Daftar hak akses role tidak valid.")
            requested = set(submitted)
            unknown = requested - set(PERMISSION_DESCRIPTIONS)
            protected = requested & {"users.manage", "app.configure"}
            if unknown or protected:
                raise ValueError("Hak akses tersebut tidak dikenal atau hanya tersedia untuk Administrator.")
            if role_code in FEE_RESTRICTED_ROLES and requested & FEE_RESTRICTED_PERMISSIONS:
                raise ValueError("Role ini tidak boleh melihat daftar fee/gaji (freelance maupun In-house).")
        with DB_LOCK, get_db() as conn:
            role = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()
            if not role:
                raise ValueError("Role tidak ditemukan.")
            conn.execute("DELETE FROM role_permissions WHERE role_id=?", (role["id"],))
            if reset_default:
                requested = set(ROLE_PERMISSIONS.get(role_code, set()))
                conn.execute("UPDATE role_acl_settings SET customized=0,updated_by=?,updated_at=? WHERE role_id=?",
                             (actor["id"], now_iso(), role["id"]))
            else:
                conn.execute("UPDATE role_acl_settings SET customized=1,updated_by=?,updated_at=? WHERE role_id=?",
                             (actor["id"], now_iso(), role["id"]))
            for code in requested:
                permission_id = conn.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()["id"]
                conn.execute("INSERT INTO role_permissions(role_id,permission_id) VALUES(?,?)", (role["id"], permission_id))
            audit(conn, actor["id"], "role_acl", role["id"], "reset_to_default" if reset_default else "updated",
                  {"role": role_code, "permissions": sorted(requested)})
            result = configure_role_payload(conn)
        self.json_response({"ok": True, "configure": result})

    def handle_user_create(self, actor: dict, payload: dict) -> None:
        self.require(actor, "users.manage")
        full_name = str(payload.get("full_name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        role_code = str(payload.get("role", "")).strip()
        password = str(payload.get("password", ""))
        if len(full_name) < 2 or len(full_name) > 100:
            raise ValueError("Nama harus terdiri dari 2–100 karakter.")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("Masukkan alamat email yang valid.")
        if role_code not in dict(ROLE_LIST):
            raise ValueError("Pilih role akun yang valid.")
        validate_password(password)
        employment_type = "freelancer" if role_code in {"crew","pic_event"} else "inhouse"
        department = {"administrator":"Management","head_operations":"Operations","event_coordinator":"Operations",
            "head_finance":"Finance","finance":"Finance","admin_finance":"Finance","warehouse_head":"Warehouse",
            "warehouse_staff":"Warehouse","design_team":"Design","design_head":"Design","sales_staff":"Sales","content_team":"Content"}.get(role_code,"Other")
        with get_db() as conn:
            cur = conn.execute("INSERT INTO users(full_name,email,password_hash,active,employment_type,department) VALUES(?,?,?,1,?,?)",
                               (full_name,email,password_hash(password),employment_type,department))
            role = conn.execute("SELECT id,name FROM roles WHERE code=?", (role_code,)).fetchone()
            conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (cur.lastrowid,role["id"]))
            audit(conn,actor["id"],"user",cur.lastrowid,"created",{"email":email,"role":role_code})
        self.json_response({"ok":True,"id":cur.lastrowid,"email":email,"role":role_code})

    def handle_profile_update(self, actor: dict, payload: dict) -> None:
        self.require(actor, "profile.update_own")
        full_name = str(payload.get("full_name", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        if not 2 <= len(full_name) <= 100:
            raise ValueError("Nama lengkap harus terdiri dari 2–100 karakter.")
        if phone and (not re.fullmatch(r"[0-9+().\-\s]{8,30}", phone) or not 8 <= sum(ch.isdigit() for ch in phone) <= 15):
            raise ValueError("Nomor WhatsApp harus berisi 8–15 angka.")
        photo_data = payload.get("photo")
        new_photo = None
        old_photo = None
        if photo_data:
            content, mime, extension = parse_profile_image(photo_data, 300_000, "foto profil")
            new_photo = (store_profile_file(actor["id"], "profile-photo", content, extension), mime)
        try:
            with DB_LOCK, get_db() as conn:
                old_user = conn.execute("SELECT full_name FROM users WHERE id=? AND active=1", (actor["id"],)).fetchone()
                if not old_user:
                    raise PermissionError("Akun tidak aktif.")
                current = conn.execute("SELECT profile_photo_path FROM user_profiles WHERE user_id=?", (actor["id"],)).fetchone()
                old_photo = current["profile_photo_path"] if current else None
                conn.execute("INSERT OR IGNORE INTO user_profiles(user_id) VALUES(?)", (actor["id"],))
                if new_photo:
                    conn.execute("""UPDATE user_profiles SET phone=?,profile_photo_path=?,profile_photo_mime=?,updated_by=?,updated_at=?
                        WHERE user_id=?""", (phone,new_photo[0],new_photo[1],actor["id"],now_iso(),actor["id"]))
                else:
                    conn.execute("UPDATE user_profiles SET phone=?,updated_by=?,updated_at=? WHERE user_id=?",
                                 (phone,actor["id"],now_iso(),actor["id"]))
                conn.execute("UPDATE users SET full_name=? WHERE id=?", (full_name,actor["id"]))
                audit(conn,actor["id"],"user_profile",actor["id"],"updated",{
                    "name_changed": old_user["full_name"] != full_name,
                    "phone_changed": True,
                    "photo_changed": bool(new_photo),
                })
        except Exception:
            if new_photo:
                remove_profile_file(new_photo[0])
            raise
        if new_photo and old_photo and old_photo != new_photo[0]:
            remove_profile_file(old_photo)
        with get_db() as conn:
            profile = user_profile_summary(conn,actor["id"])
        self.json_response({"ok":True,"profile":profile})

    def handle_profile_password_change(self, actor: dict, payload: dict) -> None:
        self.require(actor, "profile.update_own")
        current_password = payload.get("current_password")
        new_password = payload.get("new_password")
        confirmation = payload.get("password_confirmation")
        if not all(isinstance(value, str) for value in (current_password, new_password, confirmation)):
            raise ValueError("Masukkan password saat ini, password baru, dan konfirmasinya.")
        if not current_password or len(current_password) > 1024:
            raise ValueError("Password saat ini tidak valid.")
        if new_password != confirmation:
            raise ValueError("Konfirmasi password baru tidak sama.")
        validate_password(new_password)
        if current_password == new_password:
            raise ValueError("Password baru harus berbeda dari password saat ini.")
        with DB_LOCK, get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute("SELECT password_hash,active FROM users WHERE id=?", (actor["id"],)).fetchone()
            if not target or not target["active"]:
                raise PermissionError("Akun tidak aktif.")
            if not check_password(current_password, target["password_hash"]):
                raise ValueError("Password saat ini salah.")
            if check_password(new_password, target["password_hash"]):
                raise ValueError("Password baru harus berbeda dari password saat ini.")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(new_password), actor["id"]))
            revoked = conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash!=?",
                                   (actor["id"], actor["session_hash"])).rowcount
            audit(conn, actor["id"], "user", actor["id"], "password_changed",
                  {"sessions_revoked": revoked, "current_session_preserved": True})
        self.json_response({"ok":True,"sessions_revoked":revoked,"current_session_preserved":True})

    def handle_profile_ktp_upload(self, actor: dict, payload: dict) -> None:
        self.require(actor, "profile.update_own")
        side = payload.get("side")
        if side not in {"front", "back"}:
            raise ValueError("Pilih sisi KTP depan atau belakang.")
        content, mime, extension = parse_profile_image(payload.get("image"), 650_000, "foto KTP")
        field = f"ktp_{side}"
        new_file = store_profile_file(actor["id"], field, content, extension)
        old_file = None
        try:
            with DB_LOCK, get_db() as conn:
                current = conn.execute(f"SELECT {field}_path FROM user_profiles WHERE user_id=?", (actor["id"],)).fetchone()
                old_file = current[f"{field}_path"] if current else None
                conn.execute("INSERT OR IGNORE INTO user_profiles(user_id) VALUES(?)", (actor["id"],))
                conn.execute(f"UPDATE user_profiles SET {field}_path=?,{field}_mime=?,updated_by=?,updated_at=? WHERE user_id=?",
                             (new_file,mime,actor["id"],now_iso(),actor["id"]))
                audit(conn,actor["id"],"user_profile",actor["id"],"ktp_uploaded",{"side":side})
        except Exception:
            remove_profile_file(new_file)
            raise
        if old_file and old_file != new_file:
            remove_profile_file(old_file)
        self.json_response({"ok":True,"side":side})

    def handle_profile_file(self, actor: dict, target_id: int, kind: str) -> None:
        if kind == "photo":
            if target_id != actor["id"] and "staff.directory.read" not in actor["permissions"]:
                self.json_error(HTTPStatus.FORBIDDEN,"Foto profil staff hanya dapat dilihat oleh direktori operasional.")
                return
        elif target_id != actor["id"] and "staff.documents.read" not in actor["permissions"]:
            self.json_error(HTTPStatus.FORBIDDEN,"Dokumen KTP hanya dapat dilihat pemilik dan pengelola operasional berwenang.")
            return
        columns = {
            "photo": ("profile_photo_path", "profile_photo_mime", "profile.jpg"),
            "ktp_front": ("ktp_front_path", "ktp_front_mime", "ktp-depan.jpg"),
            "ktp_back": ("ktp_back_path", "ktp_back_mime", "ktp-belakang.jpg"),
        }
        path_column, mime_column, filename = columns[kind]
        with get_db() as conn:
            row = conn.execute(f"SELECT {path_column} AS file_path,{mime_column} AS mime_type FROM user_profiles WHERE user_id=?",
                               (target_id,)).fetchone()
        if not row or not row["file_path"]:
            self.json_error(HTTPStatus.NOT_FOUND,"File profil belum diunggah.")
            return
        root = profile_storage_dir().resolve()
        file_path = (root / row["file_path"]).resolve()
        if not file_path.is_relative_to(root) or not file_path.is_file():
            self.json_error(HTTPStatus.NOT_FOUND,"File profil tidak ditemukan.")
            return
        try:
            content = file_path.read_bytes()
        except OSError:
            self.json_error(HTTPStatus.NOT_FOUND,"File profil tidak ditemukan.")
            return
        mime = row["mime_type"] if row["mime_type"] in {"image/jpeg","image/png"} else "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(content)

    def handle_user_update(self, actor: dict, target_id: int, payload: dict) -> None:
        self.require(actor, "users.manage")
        action = payload.get("action")
        deleted_profile_paths = []
        password_reset_result = None
        with DB_LOCK, get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            target = conn.execute("SELECT id,full_name,email,active FROM users WHERE id=?", (target_id,)).fetchone()
            if not target:
                raise ValueError("Akun tidak ditemukan.")
            old_roles, _ = role_data(conn,target_id)
            is_admin = any(role["code"] == "administrator" for role in old_roles)
            if action == "role":
                role_code = str(payload.get("role", "")).strip()
                if role_code not in dict(ROLE_LIST):
                    raise ValueError("Pilih role akun yang valid.")
                if is_admin and role_code != "administrator":
                    active_admins = conn.execute("""
                        SELECT COUNT(DISTINCT u.id) FROM users u JOIN user_roles ur ON ur.user_id=u.id
                        JOIN roles r ON r.id=ur.role_id WHERE u.active=1 AND r.code='administrator'
                    """).fetchone()[0]
                    if active_admins <= 1:
                        raise ValueError("Role Administrator harus tetap dimiliki minimal satu akun aktif.")
                role = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()
                conn.execute("DELETE FROM user_roles WHERE user_id=?", (target_id,))
                conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (target_id,role["id"]))
                employment, department = account_classification(role_code)
                conn.execute('UPDATE users SET employment_type=?,department=? WHERE id=?', (employment,department,target_id))
                audit(conn,actor["id"],"user",target_id,"role_changed",{"from":[r["code"] for r in old_roles],"to":role_code})
            elif action == "active":
                active = payload.get("active")
                if not isinstance(active, bool):
                    raise ValueError("Status akun tidak valid.")
                if target_id == actor["id"] and not active:
                    raise ValueError("Anda tidak dapat menonaktifkan akun yang sedang digunakan.")
                if is_admin and not active and target["active"]:
                    active_admins = conn.execute("""
                        SELECT COUNT(DISTINCT u.id) FROM users u JOIN user_roles ur ON ur.user_id=u.id
                        JOIN roles r ON r.id=ur.role_id WHERE u.active=1 AND r.code='administrator'
                    """).fetchone()[0]
                    if active_admins <= 1:
                        raise ValueError("Minimal satu akun Administrator harus tetap aktif.")
                conn.execute("UPDATE users SET active=? WHERE id=?", (int(active),target_id))
                if not active:
                    conn.execute('DELETE FROM sessions WHERE user_id=?', (target_id,))
                audit(conn,actor["id"],"user",target_id,"status_changed",{"from":bool(target["active"]),"to":active})
            elif action == "password":
                password = payload.get("password")
                confirmation = payload.get("password_confirmation")
                if not isinstance(password, str) or not isinstance(confirmation, str):
                    raise ValueError("Masukkan password baru dan ulangi password tersebut.")
                if password != confirmation:
                    raise ValueError("Konfirmasi password tidak sama.")
                validate_password(password)
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password), target_id))
                revoked = conn.execute("DELETE FROM sessions WHERE user_id=?", (target_id,)).rowcount
                audit(conn, actor["id"], "user", target_id, "password_reset",
                      {"email": target["email"], "sessions_revoked": revoked})
                password_reset_result = {"ok": True, "id": target_id, "action": action,
                                         "current_session_revoked": target_id == actor["id"]}
            elif action == "delete":
                if payload.get("confirm") != "HAPUS":
                    raise ValueError("Ketik HAPUS untuk mengonfirmasi penghapusan akun.")
                if target_id == actor["id"]:
                    raise ValueError("Akun yang sedang digunakan tidak dapat dihapus.")
                if target["active"]:
                    raise ValueError("Nonaktifkan akun terlebih dahulu sebelum menghapusnya permanen.")
                if is_admin:
                    active_admins = conn.execute("""
                        SELECT COUNT(DISTINCT u.id) FROM users u JOIN user_roles ur ON ur.user_id=u.id
                        JOIN roles r ON r.id=ur.role_id
                        WHERE u.active=1 AND r.code='administrator' AND u.id!=?
                    """, (target_id,)).fetchone()[0]
                    if active_admins <= 0:
                        raise ValueError("Minimal satu akun Administrator aktif harus tetap tersedia.")
                blockers = []
                checks = [
                    ("SELECT 1 FROM compensation_history WHERE user_id=? OR created_by=? LIMIT 1", (target_id, target_id), "riwayat kompensasi"),
                    ("SELECT 1 FROM event_assignments WHERE user_id=? LIMIT 1", (target_id,), "penugasan event"),
                    ("SELECT 1 FROM inhouse_attendance WHERE user_id=? LIMIT 1", (target_id,), "absensi In-house"),
                    ("SELECT 1 FROM inhouse_salary_rates WHERE user_id=? LIMIT 1", (target_id,), "riwayat gaji In-house"),
                    ("SELECT 1 FROM inhouse_payroll_payouts WHERE user_id=? LIMIT 1", (target_id,), "riwayat payroll In-house"),
                    ("SELECT 1 FROM inhouse_monthly_salary_claims WHERE user_id=? LIMIT 1", (target_id,), "klaim payroll In-house"),
                    ("SELECT 1 FROM cash_advance_documents WHERE uploaded_by=? LIMIT 1", (target_id,), "dokumen uang jalan"),
                    ("SELECT 1 FROM kpi_reviews WHERE reviewer_id=? OR subject_id=? LIMIT 1", (target_id, target_id), "riwayat KPI"),
                    ("SELECT 1 FROM event_performance_reviews WHERE reviewer_id=? LIMIT 1", (target_id,), "penilaian performance"),
                ]
                for query, params, label in checks:
                    if conn.execute(query, params).fetchone():
                        blockers.append(label)
                if blockers:
                    raise ValueError("Akun memiliki " + ", ".join(blockers) + ". Gunakan Nonaktifkan agar riwayat tetap aman.")
                profile = conn.execute("SELECT profile_photo_path,ktp_front_path,ktp_back_path FROM user_profiles WHERE user_id=?", (target_id,)).fetchone()
                deleted_profile_paths = [profile[column] for column in ("profile_photo_path", "ktp_front_path", "ktp_back_path") if profile and profile[column]]
                audit(conn, actor["id"], "user", target_id, "deleted",
                      {"email": target["email"], "full_name": target["full_name"]})
                conn.execute("DELETE FROM users WHERE id=?", (target_id,))
            else:
                raise ValueError("Tindakan akun tidak dikenal.")
        for profile_path in deleted_profile_paths:
            try:
                remove_profile_file(profile_path)
            except OSError:
                pass
        if password_reset_result is not None:
            self.json_response(password_reset_result)
            return
        self.json_response({"ok":True,"id":target_id,"action":action})

    def handle_calendar_code_assign(self, actor: dict, intake_id: int, payload: dict) -> None:
        self.require(actor, "events.project_code.manage")
        project_code = str(payload.get("project_code", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,49}", project_code):
            raise ValueError("Kode project harus 3–50 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
        with get_db() as conn:
            intake = conn.execute("SELECT * FROM calendar_code_queue WHERE id=?", (intake_id,)).fetchone()
            if not intake:
                raise ValueError("Event Calendar tidak ditemukan atau sudah dipasangkan.")
            if intake["status"] != "scheduled":
                raise ValueError("Event yang dibatalkan tidak dapat dipasangkan ke kode project.")
            duplicate = conn.execute("SELECT title FROM events WHERE project_code=? COLLATE NOCASE", (project_code,)).fetchone()
            if duplicate:
                raise ValueError(f"Kode {project_code} sudah digunakan oleh event {duplicate['title']}.")
            cur = conn.execute("""INSERT INTO events(project_code,google_event_id,title,event_type,starts_at,ends_at,location,is_full_day,status,calendar_updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (project_code,intake["google_event_id"],intake["title"],intake["event_type"],
                intake["starts_at"],intake["ends_at"],intake["location"],intake["is_full_day"],intake["status"],intake["calendar_updated_at"]))
            event_id = cur.lastrowid
            conn.execute("INSERT INTO cash_advances(event_id,status) VALUES(?,'not_submitted')", (event_id,))
            conn.execute("INSERT INTO warehouse_checks(event_id,status) VALUES(?,'needs_prep')", (event_id,))
            conn.execute("INSERT INTO event_communications(event_id,status) VALUES(?,'not_created')", (event_id,))
            conn.execute("INSERT INTO design_tasks(event_id,title,status) VALUES(?,'Desain event','brief_needed')", (event_id,))
            conn.execute("DELETE FROM calendar_code_queue WHERE id=?", (intake_id,))
            audit(conn,actor["id"],"event",event_id,"crm_project_code_assigned",{"project_code":project_code,"google_event_id":intake["google_event_id"]})
        self.json_response({"ok":True,"event_id":event_id,"project_code":project_code})

    def handle_calendar_queue_promote(self, actor: dict, intake_id: int, payload: dict) -> None:
        self.require(actor,"events.assign")
        requested_full_day=payload.get("is_full_day")
        if requested_full_day is not None and requested_full_day not in (True,False,0,1,"0","1"):
            raise ValueError("Pilih Full day atau Non-full day.")
        requested_project_code=str(payload.get("project_code","")).strip()
        if requested_project_code and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,49}",requested_project_code):
            raise ValueError("Kode project harus 3–50 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
        with DB_LOCK,get_db() as conn:
            intake=conn.execute("SELECT * FROM calendar_code_queue WHERE id=?",(intake_id,)).fetchone()
            if not intake: raise ValueError("Event Calendar tidak ditemukan atau sudah dijadwalkan.")
            if intake["status"]!="scheduled": raise ValueError("Event yang dibatalkan tidak dapat dijadwalkan.")
            duplicate=conn.execute("SELECT title FROM events WHERE project_code=? COLLATE NOCASE",(requested_project_code,)).fetchone() if requested_project_code else None
            if duplicate: raise ValueError(f"Kode {requested_project_code} sudah digunakan oleh event {duplicate['title']}.")
            is_full_day=int(intake["is_full_day"] if requested_full_day is None else requested_full_day in (True,1,"1"))
            manual_override=int(requested_full_day is not None)
            is_temporary=not bool(requested_project_code)
            temporary_code=f"OPS-{datetime.now(WIB).strftime('%y%m%d')}-{secrets.token_hex(3).upper()}" if is_temporary else None
            project_code=requested_project_code or temporary_code
            cur=conn.execute("""INSERT INTO events(project_code,operational_code,project_code_is_temporary,google_event_id,title,event_type,
                starts_at,ends_at,location,is_full_day,is_full_day_manual,status,calendar_updated_at,coordinator_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(project_code,temporary_code,int(is_temporary),intake["google_event_id"],intake["title"],intake["event_type"],
                intake["starts_at"],intake["ends_at"],intake["location"],is_full_day,manual_override,intake["status"],intake["calendar_updated_at"],actor["id"]))
            event_id=cur.lastrowid
            conn.execute("INSERT INTO cash_advances(event_id,status) VALUES(?,'not_submitted')",(event_id,))
            conn.execute("INSERT INTO warehouse_checks(event_id,status) VALUES(?,'needs_prep')",(event_id,))
            conn.execute("INSERT INTO event_communications(event_id,status) VALUES(?,'not_created')",(event_id,))
            conn.execute("INSERT INTO design_tasks(event_id,title,status) VALUES(?,'Desain event','brief_needed')",(event_id,))
            conn.execute("DELETE FROM calendar_code_queue WHERE id=?",(intake_id,))
            audit(conn,actor["id"],"event",event_id,"scheduled_from_calendar_queue",{"project_code":project_code,"temporary":bool(is_temporary),"operational_code":temporary_code,"google_event_id":intake["google_event_id"],"is_full_day":bool(is_full_day),"full_day_manual_override":bool(manual_override)})
        self.json_response({"ok":True,"event_id":event_id,"project_code":project_code,"temporary":bool(is_temporary),"is_full_day":bool(is_full_day)})

    def handle_event_full_day(self, actor: dict, event_id: int, payload: dict) -> None:
        self.require(actor,"events.assign")
        value=payload.get("is_full_day")
        if value not in (True,False,0,1,"0","1"):
            raise ValueError("Pilih Full day atau Non-full day.")
        is_full_day=int(value in (True,1,"1"))
        with get_db() as conn:
            event=conn.execute("SELECT is_full_day,status FROM events WHERE id=?",(event_id,)).fetchone()
            if not event: raise ValueError("Event tidak ditemukan.")
            if event["status"]=="completed": raise ValueError("Event yang sudah clear tidak dapat mengubah durasi payroll.")
            conn.execute("UPDATE events SET is_full_day=?,is_full_day_manual=1,updated_at=? WHERE id=?",(is_full_day,now_iso(),event_id))
            audit(conn,actor["id"],"event",event_id,"full_day_setting_changed",{"from":bool(event["is_full_day"]),"to":bool(is_full_day)})
        self.json_response({"ok":True,"event_id":event_id,"is_full_day":bool(is_full_day)})

    def handle_event_project_code(self, actor: dict, event_id: int, payload: dict) -> None:
        self.require(actor,"events.project_code.manage")
        project_code=str(payload.get("project_code","")).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,49}",project_code):
            raise ValueError("Kode project harus 3–50 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
        with get_db() as conn:
            event=conn.execute("SELECT project_code,project_code_is_temporary,operational_code FROM events WHERE id=?",(event_id,)).fetchone()
            if not event: raise ValueError("Event tidak ditemukan.")
            if not event["project_code_is_temporary"]: raise ValueError("Kode CRM hanya dapat dipasangkan pada event berkode sementara.")
            duplicate=conn.execute("SELECT title FROM events WHERE project_code=? COLLATE NOCASE AND id<>?",(project_code,event_id)).fetchone()
            if duplicate: raise ValueError(f"Kode {project_code} sudah digunakan oleh event {duplicate['title']}.")
            conn.execute("UPDATE events SET project_code=?,project_code_is_temporary=0,updated_at=? WHERE id=?",(project_code,now_iso(),event_id))
            audit(conn,actor["id"],"event",event_id,"temporary_code_replaced",{"from":event["project_code"],"to":project_code,"operational_code":event["operational_code"]})
        self.json_response({"ok":True,"event_id":event_id,"project_code":project_code})

    def handle_event_temporary_code(self, actor: dict, event_id: int, payload: dict) -> None:
        self.require(actor,"events.assign")
        code=str(payload.get("project_code","")).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,49}",code):
            raise ValueError("Kode operasional harus 3–50 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
        with get_db() as conn:
            event=conn.execute("SELECT project_code,operational_code,project_code_is_temporary FROM events WHERE id=?",(event_id,)).fetchone()
            if not event: raise ValueError("Event tidak ditemukan.")
            if not event["project_code_is_temporary"]: raise ValueError("Kode ini bukan kode operasional sementara.")
            duplicate=conn.execute("SELECT title FROM events WHERE project_code=? COLLATE NOCASE AND id<>?",(code,event_id)).fetchone()
            if duplicate: raise ValueError(f"Kode {code} sudah digunakan oleh event {duplicate['title']}.")
            conn.execute("UPDATE events SET project_code=?,operational_code=?,updated_at=? WHERE id=?",(code,code,now_iso(),event_id))
            audit(conn,actor["id"],"event",event_id,"temporary_operational_code_changed",{"from":event["project_code"],"to":code})
        self.json_response({"ok":True,"event_id":event_id,"project_code":code,"temporary":True})

    def handle_payroll_export(self, user: dict, payload: dict) -> None:
        self.require(user,"payroll.export")
        file_format = str(payload.get("format", "xlsx")).lower()
        if file_format not in {"xlsx", "csv"}:
            raise ValueError("Format ekspor harus Excel atau CSV.")
        start = str(payload.get("start", "")).strip()
        end = str(payload.get("end", "")).strip()
        if start or end:
            if not start or not end:
                raise ValueError("Pilih tanggal awal dan tanggal akhir payroll.")
            period_label = f"{start}-to-{end}"
            period = f"{start} – {end}"
        else:
            period_label = str(payload.get("month", ""))
            period = period_label
        with DB_LOCK,get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows, _ = create_payroll_export(conn,start if start and end else period_label,user["id"],end if start and end else None)
        if file_format == "xlsx":
            data = make_payroll_xlsx(rows, period)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"captureit-payroll-{period_label}.xlsx"
        else:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Nama", "Kode Project", "Event", "Tanggal", "Posisi", "Skill Khusus", "Check-in (WIB)", "Check-out (WIB)", "Fee Dasar (Rp)", "Fee Skill (Rp)", "Fee (Dasar + Skill) (Rp)", "Uang Makan (Rp)", "Total (Rp)"])
            for row in rows:
                writer.writerow([csv_value(v) for v in [
                    row.get("full_name", ""), row["project_code"], row["event_title"], local_datetime(row["starts_at"]).date().isoformat(),
                    row.get("position_name", ""), row.get("skill_names", ""),
                    payroll_timestamp_wib(row.get("check_in_at")), payroll_timestamp_wib(row.get("check_out_at")),
                    row.get("base_fee", row.get("base_fee_rupiah", 0)), row.get("skill_fee", row.get("skill_fee_rupiah", 0)),
                    row.get("fee", row.get("base_fee_rupiah", 0) + row.get("skill_fee_rupiah", 0)),
                    row.get("meal_fee", row.get("meal_rupiah", 0)), row.get("total", row.get("total_rupiah", 0)),
                ]])
            data = output.getvalue().encode("utf-8-sig")
            content_type = "text/csv; charset=utf-8"
            filename = f"captureit-payroll-{period_label}.csv"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_freelancer_payroll_transfer(self,user: dict,batch_id: int,payload: dict) -> None:
        self.require(user,"payroll.export")
        reference=str(payload.get("transfer_reference","")).strip()
        pay_date=str(payload.get("pay_date","")).strip()
        if len(reference)>100: raise ValueError("Referensi transfer maksimal 100 karakter.")
        if not pay_date: pay_date=datetime.now(timezone(timedelta(hours=7))).date().isoformat()
        try: pay_date=date.fromisoformat(pay_date).isoformat()
        except ValueError as exc: raise ValueError("Tanggal transfer tidak valid.") from exc
        with DB_LOCK,get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch=conn.execute("SELECT * FROM payroll_batches WHERE id=?",(batch_id,)).fetchone()
            if not batch or batch["status"]!="exported": raise ValueError("Export payroll Crew/PIC belum tersedia.")
            if not conn.execute("SELECT 1 FROM payroll_lines WHERE batch_id=? LIMIT 1",(batch_id,)).fetchone():
                raise ValueError("Batch payroll ini tidak memiliki rincian pembayaran.")
            if conn.execute("SELECT 1 FROM payroll_batch_transfers WHERE batch_id=?",(batch_id,)).fetchone():
                raise ValueError("Transfer periode ini sudah dicatat.")
            conn.execute("INSERT INTO payroll_batch_transfers(batch_id,pay_date,transfer_reference,recorded_by,recorded_at) VALUES(?,?,?,?,?)",
                (batch_id,pay_date,reference,user["id"],now_iso()))
            audit(conn,user["id"],"payroll_batch",batch_id,"transfer_recorded",{"pay_date":pay_date,"reference":reference})
            recipients = [r[0] for r in conn.execute('''SELECT DISTINCT a.user_id FROM payroll_lines l
                JOIN event_assignments a ON a.id=l.assignment_id WHERE l.batch_id=?''', (batch_id,))]
            notices.emit(conn, recipients, key=f'payslip:freelancer:{batch_id}', category='payroll', kind='payslip',
                title='Slip honor event tersedia', body=f"Slip periode {batch['period']} sudah diterbitkan.",
                target_kind='payslip', target_id=batch_id, permission='payroll.read_own')
        self.json_response({"ok":True,"status":"transferred","pay_date":pay_date})

    def handle_kpi_create(self, user: dict, payload: dict) -> None:
        subject_id = int(payload.get("subject_id", 0))
        score = int(payload.get("score", 0))
        category = str(payload.get("category", "")).strip()[:100]
        note = str(payload.get("note", "")).strip()[:1000]
        period = str(payload.get("review_period", datetime.now(WIB).strftime("%Y-%m")))
        if score < 1 or score > 5 or not category or not re.fullmatch(r"\d{4}-\d{2}",period):
            raise ValueError("Lengkapi kategori, skor 1–5, dan periode penilaian.")
        with get_db() as conn:
            target = conn.execute("SELECT id FROM users WHERE id=? AND active=1", (subject_id,)).fetchone()
            if not target:
                raise ValueError("Staff yang dinilai tidak ditemukan.")
            target_roles, _ = role_data(conn,subject_id)
            target_codes = {x["code"] for x in target_roles}
            if target_codes & {"crew", "pic_event"}:
                raise ValueError("Penilaian Crew/PIC harus terkait dengan penugasan event dan dikirim saat event clear.")
            allowed = False
            if "kpi.evaluate_crew" in user["permissions"] and target_codes & {"crew","pic_event"}:
                allowed = True
            if "kpi.evaluate_operations" in user["permissions"] and target_codes & {"event_coordinator","warehouse_head","warehouse_staff"}:
                allowed = True
            if "kpi.evaluate_finance" in user["permissions"] and "admin_finance" in target_codes:
                allowed = True
            if not allowed:
                raise PermissionError("Role Anda tidak dapat menilai staff tersebut.")
            event_id = payload.get("event_id")
            if event_id:
                event_id = int(event_id)
                if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
                    raise ValueError("Event tidak ditemukan.")
            cur = conn.execute("INSERT INTO kpi_reviews(reviewer_id,subject_id,event_id,category,score,note,review_period) VALUES(?,?,?,?,?,?,?)",
                               (user["id"],subject_id,event_id,category,score,note,period))
            audit(conn,user["id"],"kpi_review",cur.lastrowid,"created",{"subject_id":subject_id,"score":score,"category":category})
        self.json_response({"ok":True,"id":cur.lastrowid})

    def handle_google_sync(self, user: dict) -> None:
        self.require(user,"google.sync")
        result = run_google_sync("manual", user["id"])
        self.json_response(result, status=result["status"])

    def json_response(self, value: dict, status: HTTPStatus = HTTPStatus.OK, extra_headers: dict | None = None) -> None:
        data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, header_value in (extra_headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(data)

    def client_ip(self) -> str:
        """Peer address. Behind a reverse proxy set TRUST_PROXY=1 to use the address the proxy appended."""
        if os.environ.get("TRUST_PROXY", "0") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[-1].strip()[:64]
        return str(self.client_address[0])[:64]

    def json_error(self, status: HTTPStatus, message: str) -> None:
        self.json_response({"ok":False,"error":message},status)


def configure_runtime() -> None:
    """Load the same configuration for the local CLI and production entry point."""
    global DB_PATH, DEMO_MODE, HOST, PORT
    load_dotenv()
    DB_PATH = Path(os.environ.get("OPS_DB_PATH", str(ROOT / "ops.sqlite3")))
    DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes"}
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "8000"))


def start_background_tasks() -> None:
    """Start one pair of schedulers per process; deploy only one app process."""
    global BACKGROUND_TASKS_STARTED
    with BACKGROUND_TASKS_LOCK:
        if BACKGROUND_TASKS_STARTED:
            return
        threading.Thread(target=google_sync_scheduler, name="google-calendar-auto-sync", daemon=True).start()
        threading.Thread(target=notification_scheduler, name="notifications-and-push", daemon=True).start()
        BACKGROUND_TASKS_STARTED = True


def main() -> None:
    configure_runtime()
    parser = argparse.ArgumentParser(description="Capture It Operations MVP")
    sub = parser.add_subparsers(dest="command")
    create = sub.add_parser("create-user", help="Buat akun dan assign role")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[x[0] for x in ROLE_LIST])
    create.add_argument("--password")
    reset = sub.add_parser("reset-password", help="Ganti password akun")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password")
    sub.add_parser("init", help="Siapkan database dan data referensi")
    args = parser.parse_args()
    if args.command == "create-user":
        initialize(seed=False)
        password = args.password or getpass.getpass("Password baru: ")
        if len(password) < 12:
            parser.error("Password minimal 12 karakter.")
        add_user(args.email,args.name,args.role,password)
        print(f"Akun dibuat: {args.email} ({args.role})")
        return
    if args.command == "reset-password":
        initialize(seed=False)
        password = args.password or getpass.getpass("Password baru: ")
        if len(password) < 12:
            parser.error("Password minimal 12 karakter.")
        reset_user_password(args.email,password)
        print(f"Password diperbarui: {args.email}")
        return
    if args.command == "init":
        initialize()
        print(f"Database siap: {DB_PATH}")
        return
    initialize()
    if DEMO_MODE:
        print("DEMO MODE aktif — contoh akun tersedia; ganti DEMO_MODE=false untuk penggunaan VPS.")
    start_background_tasks()
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()


if __name__ == "__main__":
    main()

