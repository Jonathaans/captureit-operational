"""Sales pipeline, delivery order, and inventory helpers for Capture It Ops."""

from __future__ import annotations

import re
import json
import sqlite3
from html import escape
from datetime import date, datetime, timedelta, timezone


SALES_STAGE_DEFAULTS = (
    ("lead", "Leads"),
    ("quote", "Quote"),
    ("proforma", "Proforma Invoice"),
    ("invoice", "Invoice"),
    ("spk", "Surat Perintah Kerja"),
)

DOC_KINDS = {
    "quote": "Quote",
    "proforma": "Proforma Invoice",
    "invoice": "Invoice",
    "spk": "Surat Perintah Kerja",
}

DOC_STATUSES = {"draft", "sent", "approved", "paid", "cancelled"}
LEAD_STATUSES = {"open", "won", "lost"}
ITEM_KINDS = {"inventory", "asset"}
MOVE_DIRECTIONS = {
    "in": 1,
    "return": 1,
    "adjustment_in": 1,
    "out": -1,
    "adjustment_out": -1,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: object, label: str, limit: int, required: bool = False) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    cleaned = re.sub(r"\s+", " ", value).strip()
    if required and not cleaned:
        raise ValueError(f"{label} wajib diisi.")
    if len(cleaned) > limit:
        raise ValueError(f"{label} maksimal {limit} karakter.")
    return cleaned


def clean_code(value: object, label: str, limit: int = 64, required: bool = False) -> str:
    cleaned = clean_text(value, label, limit, required).upper()
    if cleaned and not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]*", cleaned):
        raise ValueError(f"{label} hanya boleh huruf, angka, titik, garis miring, minus, atau underscore.")
    return cleaned


def positive_int(value: object, label: str, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{label} harus berupa bilangan bulat.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} harus berupa angka.") from exc
    if not 0 <= number <= maximum:
        raise ValueError(f"{label} harus di antara 0 dan {maximum}.")
    return number


def optional_date(value: object, label: str) -> str:
    text = clean_text(value, label, 10)
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} tidak valid.") from exc


def money(value: object, label: str = "Nominal") -> int:
    if value in (None, ""):
        return 0
    return positive_int(value, label, maximum=9_999_999_999_999)


def seed_reference_data(conn: sqlite3.Connection) -> None:
    migrate(conn)
    conn.execute(
        "INSERT OR IGNORE INTO sales_pipelines(name,is_default,active,updated_at) VALUES('Sales Utama',1,1,?)",
        (now_iso(),),
    )
    pipeline = conn.execute("SELECT id FROM sales_pipelines WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    if pipeline:
        for index, (code, name) in enumerate(SALES_STAGE_DEFAULTS, start=1):
            conn.execute(
                """INSERT OR IGNORE INTO sales_stages(pipeline_id,code,name,position,locked)
                   VALUES(?,?,?,?,1)""",
                (pipeline["id"], code, name, index),
            )
    for name in ("Gudang Utama", "Event", "Service/Repair"):
        conn.execute("INSERT OR IGNORE INTO inventory_locations(name,active) VALUES(?,1)", (name,))


def migrate(conn: sqlite3.Connection) -> None:
    additions = {
        "sales_leads": {"event_id": "INTEGER REFERENCES events(id) ON DELETE SET NULL"},
        "sales_documents": {
            "source_document_id": "INTEGER REFERENCES sales_documents(id) ON DELETE RESTRICT",
            "due_at": "TEXT NOT NULL DEFAULT ''", "bill_to": "TEXT NOT NULL DEFAULT ''",
            "terms": "TEXT NOT NULL DEFAULT ''", "discount_rupiah": "INTEGER NOT NULL DEFAULT 0",
            "tax_rupiah": "INTEGER NOT NULL DEFAULT 0", "context_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "inventory_movements": {
            "asset_id": "INTEGER REFERENCES inventory_assets(id) ON DELETE RESTRICT",
            "request_id": "TEXT",
        },
    }
    for table, definitions in additions.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, definition in definitions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_scan_request ON inventory_movements(request_id) WHERE request_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_lead_event ON sales_leads(event_id)")


def has_any(user: dict, *permissions: str) -> bool:
    current = set(user.get("permissions") or [])
    return bool(current & set(permissions))


def can_sales_all(user: dict) -> bool:
    return has_any(user, "sales.read_all", "sales.manage_all", "sales.dashboard")


def can_sales_read(user: dict) -> bool:
    return has_any(user, "sales.read_own", "sales.read_all", "sales.manage_own", "sales.manage_all", "sales.dashboard")


def can_sales_manage_any(user: dict) -> bool:
    return has_any(user, "sales.manage_own", "sales.manage_all")


def ensure_lead_access(conn: sqlite3.Connection, user: dict, lead_id: int, write: bool = False) -> sqlite3.Row:
    lead = conn.execute("SELECT * FROM sales_leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        raise ValueError("Lead tidak ditemukan.")
    if "sales.manage_all" in user["permissions"] or (not write and can_sales_all(user)):
        return lead
    own = lead["owner_id"] == user["id"]
    if write:
        if "sales.manage_own" not in user["permissions"] or not own:
            raise PermissionError("Lead ini bukan milik akun sales Anda.")
    elif not has_any(user, "sales.read_own", "sales.manage_own") or not own:
        raise PermissionError("Lead ini bukan milik akun sales Anda.")
    return lead


def sales_people(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT DISTINCT u.id,u.full_name,u.email
           FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id
           WHERE u.active=1 AND r.code IN ('sales_staff','admin_sales','administrator')
           ORDER BY u.full_name"""
    ).fetchall()
    return [dict(row) for row in rows]


def pipeline_rows(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    pipelines = [dict(row) for row in conn.execute(
        "SELECT id,name,is_default,active FROM sales_pipelines WHERE active=1 ORDER BY is_default DESC,name"
    ).fetchall()]
    stages = [dict(row) for row in conn.execute(
        "SELECT id,pipeline_id,code,name,position,locked FROM sales_stages ORDER BY pipeline_id,position"
    ).fetchall()]
    return pipelines, stages


def sales_lead_rows(conn: sqlite3.Connection, user: dict) -> list[dict]:
    where = ""
    args: tuple = ()
    if not can_sales_all(user):
        where = "WHERE l.owner_id=?"
        args = (user["id"],)
    return [dict(row) for row in conn.execute(f"""
        SELECT l.*,p.name AS pipeline_name,s.code AS stage_code,s.name AS stage_name,s.position AS stage_position,
               owner.full_name AS owner_name,
               COALESCE((SELECT COUNT(*) FROM sales_documents d WHERE d.lead_id=l.id),0) AS document_count,
               COALESCE((SELECT document_no FROM sales_documents d WHERE d.lead_id=l.id AND d.kind='spk'
                         ORDER BY d.created_at DESC,d.id DESC LIMIT 1),'') AS spk_number
        FROM sales_leads l
        JOIN sales_pipelines p ON p.id=l.pipeline_id
        JOIN sales_stages s ON s.id=l.stage_id
        LEFT JOIN users owner ON owner.id=l.owner_id
        {where}
        ORDER BY l.updated_at DESC,l.id DESC
    """, args).fetchall()]


def sales_documents(conn: sqlite3.Connection, user: dict) -> list[dict]:
    if not can_sales_read(user):
        return []
    where = "" if can_sales_all(user) else "WHERE l.owner_id=?"
    args: tuple = () if can_sales_all(user) else (user["id"],)
    documents = [dict(row) for row in conn.execute(f"""
        SELECT d.*,l.client_name,l.event_name,l.owner_id,l.event_id,l.event_date,l.venue,
               src.document_no AS source_number
        FROM sales_documents d JOIN sales_leads l ON l.id=d.lead_id
        LEFT JOIN sales_documents src ON src.id=d.source_document_id
        {where}
        ORDER BY d.created_at DESC,d.id DESC
    """, args).fetchall()]
    by_id = {doc["id"]: doc for doc in documents}
    for doc in documents:
        doc["items"] = []
    for row in conn.execute(f"""SELECT i.* FROM sales_document_items i
        JOIN sales_documents d ON d.id=i.document_id JOIN sales_leads l ON l.id=d.lead_id
        {where} ORDER BY i.document_id,i.position""", args):
        by_id[row["document_id"]]["items"].append(dict(row))
    return documents


def sales_dashboard(conn: sqlite3.Connection, user: dict, leads: list[dict]) -> dict:
    open_leads = [lead for lead in leads if lead["status"] == "open"]
    won = [lead for lead in leads if lead["status"] == "won"]
    ids = {lead["id"] for lead in leads}
    invoices = [row for row in conn.execute("SELECT lead_id,amount_rupiah,status,due_at FROM sales_documents WHERE kind='invoice' AND status NOT IN ('draft','cancelled')") if row["lead_id"] in ids]
    return {
        "invoiced_value": sum(row["amount_rupiah"] for row in invoices),
        "paid_value": sum(row["amount_rupiah"] for row in invoices if row["status"] == "paid"),
        "unpaid_value": sum(row["amount_rupiah"] for row in invoices if row["status"] != "paid"),
        "overdue_count": sum(1 for row in invoices if row["status"] != "paid" and row["due_at"] and row["due_at"] < date.today().isoformat()),
        "open_leads": len(open_leads),
        "won_leads": len(won),
        "estimated_value": sum(int(lead["estimated_value_rupiah"] or 0) for lead in open_leads),
        "won_value": sum(int(lead["estimated_value_rupiah"] or 0) for lead in won),
        "by_stage": [
            dict(row) for row in conn.execute("""
                SELECT s.id AS stage_id,s.name AS stage_name,s.code AS stage_code,p.name AS pipeline_name,COUNT(l.id) AS total,
                       COALESCE(SUM(l.estimated_value_rupiah),0) AS value_rupiah
                FROM sales_stages s
                JOIN sales_pipelines p ON p.id=s.pipeline_id
                LEFT JOIN sales_leads l ON l.stage_id=s.id
                GROUP BY s.id
                ORDER BY s.pipeline_id,s.position
            """).fetchall()
        ] if can_sales_all(user) else [],
    }


def sales_payload(conn: sqlite3.Connection, user: dict) -> dict | None:
    if not can_sales_read(user):
        return None
    pipelines, stages = pipeline_rows(conn)
    leads = sales_lead_rows(conn, user)
    documents = sales_documents(conn, user)
    dashboard = sales_dashboard(conn, user, leads)
    invoices = [doc for doc in documents if doc["kind"] == "invoice" and doc["status"] not in {"draft", "cancelled"}]
    dashboard["invoiced_value"] = sum(doc["amount_rupiah"] for doc in invoices)
    dashboard["paid_value"] = sum(doc["amount_rupiah"] for doc in invoices if doc["status"] == "paid")
    dashboard["unpaid_value"] = dashboard["invoiced_value"] - dashboard["paid_value"]
    dashboard["overdue_count"] = sum(1 for doc in invoices if doc["status"] != "paid" and doc["due_at"] and doc["due_at"] < date.today().isoformat())
    return {
        "pipelines": pipelines,
        "stages": stages,
        "leads": leads,
        "documents": documents,
        "sales_people": sales_people(conn) if "sales.manage_all" in user["permissions"] else [],
        "dashboard": dashboard,
    }


def save_pipeline(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "sales.pipeline.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses mengatur pipeline sales.")
    name = clean_text(payload.get("name"), "Nama pipeline", 120, required=True)
    pipeline_id = payload.get("id")
    if pipeline_id is not None:
        pipeline_id = positive_int(pipeline_id, "Pipeline")
        if not conn.execute("SELECT id FROM sales_pipelines WHERE id=?", (pipeline_id,)).fetchone():
            raise ValueError("Pipeline tidak ditemukan.")
        conn.execute("UPDATE sales_pipelines SET name=?,updated_at=? WHERE id=?", (name, now_iso(), pipeline_id))
    else:
        pipeline_id = conn.execute(
            "INSERT INTO sales_pipelines(name,is_default,active,created_by,updated_at) VALUES(?,0,1,?,?)",
            (name, user["id"], now_iso()),
        ).lastrowid
        for index, (code, stage_name) in enumerate(SALES_STAGE_DEFAULTS, start=1):
            conn.execute(
                "INSERT INTO sales_stages(pipeline_id,code,name,position,locked) VALUES(?,?,?,?,1)",
                (pipeline_id, code, stage_name, index),
            )
    return {"ok": True, "id": pipeline_id}


def save_lead(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if not can_sales_manage_any(user):
        raise PermissionError("Akun ini tidak memiliki akses menyimpan lead.")
    lead_id = payload.get("id")
    existing = ensure_lead_access(conn, user, positive_int(lead_id, "Lead"), write=True) if lead_id else None
    pipeline_id = positive_int(payload.get("pipeline_id") or 0, "Pipeline")
    if not pipeline_id:
        pipeline = conn.execute("SELECT id FROM sales_pipelines WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
        pipeline_id = pipeline["id"] if pipeline else 0
    pipeline = conn.execute("SELECT id FROM sales_pipelines WHERE id=? AND active=1", (pipeline_id,)).fetchone()
    if not pipeline:
        raise ValueError("Pipeline tidak ditemukan.")
    stage_id = positive_int(payload.get("stage_id") or 0, "Tahap")
    if not stage_id:
        stage = conn.execute("SELECT id FROM sales_stages WHERE pipeline_id=? ORDER BY position LIMIT 1", (pipeline_id,)).fetchone()
        stage_id = stage["id"] if stage else 0
    if not conn.execute("SELECT id FROM sales_stages WHERE id=? AND pipeline_id=?", (stage_id, pipeline_id)).fetchone():
        raise ValueError("Tahap pipeline tidak valid.")
    if "sales.manage_all" in user["permissions"]:
        owner_id = positive_int(payload.get("owner_id") or user["id"], "Sales owner")
        if not conn.execute("SELECT id FROM users WHERE id=? AND active=1", (owner_id,)).fetchone():
            raise ValueError("Sales owner tidak ditemukan.")
    else:
        owner_id = user["id"]
    values = {
        "pipeline_id": pipeline_id,
        "stage_id": stage_id,
        "owner_id": owner_id,
        "client_name": clean_text(payload.get("client_name"), "Nama client", 160, required=True),
        "contact_name": clean_text(payload.get("contact_name"), "Nama contact", 160),
        "contact_phone": clean_text(payload.get("contact_phone"), "Nomor contact", 60),
        "contact_email": clean_text(payload.get("contact_email"), "Email contact", 160),
        "event_name": clean_text(payload.get("event_name"), "Nama event", 180, required=True),
        "event_date": optional_date(payload.get("event_date"), "Tanggal event"),
        "venue": clean_text(payload.get("venue"), "Venue", 220),
        "source": clean_text(payload.get("source"), "Source", 80),
        "estimated_value_rupiah": money(payload.get("estimated_value_rupiah"), "Estimasi nilai"),
        "notes": clean_text(payload.get("notes"), "Catatan", 2000),
        "status": payload.get("status", existing["status"] if existing else "open"),
        "updated_at": now_iso(),
    }
    if values["status"] not in LEAD_STATUSES:
        raise ValueError("Status lead tidak valid.")
    if lead_id:
        lead_id = positive_int(lead_id, "Lead")
        ensure_lead_access(conn, user, lead_id, write=True)
        columns = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE sales_leads SET {columns} WHERE id=?", (*values.values(), lead_id))
    else:
        values["created_by"] = user["id"]
        lead_id = conn.execute(
            f"INSERT INTO sales_leads({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
    return {"ok": True, "id": lead_id}


def move_lead(conn: sqlite3.Connection, user: dict, lead_id: int, payload: dict) -> dict:
    lead = ensure_lead_access(conn, user, lead_id, write=True)
    stage_id = positive_int(payload.get("stage_id"), "Tahap")
    stage = conn.execute("SELECT * FROM sales_stages WHERE id=? AND pipeline_id=?", (stage_id, lead["pipeline_id"])).fetchone()
    if not stage:
        raise ValueError("Tahap pipeline tidak valid.")
    status = payload.get("status")
    if status not in LEAD_STATUSES:
        status = lead["status"]
    conn.execute("UPDATE sales_leads SET stage_id=?,status=?,updated_at=? WHERE id=?", (stage_id, status, now_iso(), lead_id))
    return {"ok": True, "id": lead_id, "stage_id": stage_id, "status": status}


def save_document(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if not can_sales_manage_any(user):
        raise PermissionError("Akun ini tidak memiliki akses menyimpan dokumen sales.")
    lead_id = positive_int(payload.get("lead_id"), "Lead")
    lead = ensure_lead_access(conn, user, lead_id, write=True)
    kind = payload.get("kind")
    if kind not in DOC_KINDS:
        raise ValueError("Jenis dokumen tidak valid.")
    status = payload.get("status", "draft")
    if status not in DOC_STATUSES or (status == "paid" and kind not in {"invoice", "proforma"}):
        raise ValueError("Status dokumen tidak valid.")
    doc_id = payload.get("id")
    current = None
    if doc_id:
        doc_id = positive_int(doc_id, "Dokumen")
        current = conn.execute("SELECT * FROM sales_documents WHERE id=? AND lead_id=?", (doc_id, lead_id)).fetchone()
        if not current or current["kind"] != kind:
            raise ValueError("Dokumen tidak ditemukan.")
        if current["status"] == "cancelled":
            raise ValueError("Dokumen yang dibatalkan tidak dapat diubah.")
        if current["status"] != "draft":
            if set(payload) - {"id", "lead_id", "kind", "status"}:
                raise ValueError("Isi dokumen terbit tidak dapat diubah. Buat dokumen baru.")
            transitions = {"sent": {"approved", "paid", "cancelled"}, "approved": {"paid", "cancelled"}, "paid": set()}
            if status != current["status"] and status not in transitions.get(current["status"], set()):
                raise ValueError("Perubahan status dokumen tidak diizinkan.")
            if status == "cancelled" and (conn.execute("SELECT 1 FROM sales_documents WHERE source_document_id=? AND status!='cancelled'", (doc_id,)).fetchone()
                    or conn.execute("SELECT 1 FROM delivery_orders WHERE spk_document_id=? AND status!='cancelled'", (doc_id,)).fetchone()):
                raise ValueError("Dokumen masih digunakan dokumen lanjutan atau surat jalan.")
            conn.execute("UPDATE sales_documents SET status=?,updated_at=? WHERE id=?", (status, now_iso(), doc_id))
            advance_document_stage(conn, lead, kind, status)
            return {"ok": True, "id": doc_id}
    previous = {"proforma": "quote", "invoice": "proforma", "spk": "invoice"}.get(kind)
    source_id = positive_int(payload.get("source_document_id") or 0, "Dokumen sumber")
    source = conn.execute("SELECT * FROM sales_documents WHERE id=?", (source_id,)).fetchone() if source_id else None
    if previous and (not source or source["lead_id"] != lead_id or source["kind"] != previous or source["status"] in {"draft", "cancelled"}):
        raise ValueError(f"Pilih {DOC_KINDS[previous]} milik lead ini sebagai dokumen sumber.")
    if not previous and source_id:
        raise ValueError("Quote tidak memiliki dokumen sumber.")
    raw_items = payload.get("items")
    if raw_items is None and source:
        raw_items = [dict(row) for row in conn.execute("SELECT * FROM sales_document_items WHERE document_id=? ORDER BY position", (source_id,))]
        if not raw_items:
            raw_items = [{"description": lead["event_name"], "quantity": 1, "days": 1, "unit_price_rupiah": source["amount_rupiah"]}]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 100:
        raise ValueError("Isi 1 sampai 100 item dokumen.")
    items = []
    for row in raw_items:
        if not isinstance(row, dict):
            raise ValueError("Item dokumen tidak valid.")
        qty = positive_int(row.get("quantity"), "Qty", 10000)
        days = positive_int(row.get("days", 1), "Hari", 3660)
        if not qty or not days:
            raise ValueError("Qty dan hari minimal 1.")
        items.append((clean_text(row.get("description"), "Deskripsi item", 500, True), qty, days, money(row.get("unit_price_rupiah"))))
    subtotal = sum(qty * days * price for _, qty, days, price in items)
    discount = money(payload.get("discount_rupiah", source["discount_rupiah"] if source else 0))
    tax = money(payload.get("tax_rupiah", source["tax_rupiah"] if source else 0))
    if discount > subtotal:
        raise ValueError("Diskon melebihi subtotal.")
    amount = money(subtotal - discount + tax)
    number = clean_text(payload.get("document_no") or (current["document_no"] if current else next_number(conn, "sales_documents", "document_no", {"quote":"QT", "proforma":"PI", "invoice":"INV", "spk":"SPK"}[kind])), "Nomor dokumen", 80, True)
    if conn.execute("SELECT 1 FROM sales_documents WHERE document_no=? AND id!=?", (number, doc_id or 0)).fetchone():
        raise ValueError("Nomor dokumen sudah digunakan.")
    values = {
        "lead_id": lead_id,
        "kind": kind,
        "document_no": number,
        "source_document_id": source_id or None,
        "context_json": json.dumps({key: lead[key] for key in ('client_name','event_name','event_date','venue')}, ensure_ascii=False),
        "amount_rupiah": amount,
        "discount_rupiah": discount,
        "tax_rupiah": tax,
        "due_at": optional_date(payload.get("due_at"), "Jatuh tempo"),
        "bill_to": clean_text(payload.get("bill_to", source["bill_to"] if source else lead["client_name"]), "Ditagihkan kepada", 1000),
        "terms": clean_text(payload.get("terms", source["terms"] if source else ""), "Ketentuan", 4000),
        "status": status,
        "issued_at": optional_date(payload.get("issued_at"), "Tanggal dokumen"),
        "notes": clean_text(payload.get("notes"), "Catatan dokumen", 1200),
        "updated_at": now_iso(),
    }
    if values["due_at"] and values["issued_at"] and values["due_at"] < values["issued_at"]:
        raise ValueError("Jatuh tempo tidak boleh sebelum tanggal dokumen.")
    if doc_id:
        doc_id = positive_int(doc_id, "Dokumen")
        if not conn.execute("SELECT id FROM sales_documents WHERE id=? AND lead_id=?", (doc_id, lead_id)).fetchone():
            raise ValueError("Dokumen tidak ditemukan.")
        columns = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE sales_documents SET {columns} WHERE id=?", (*values.values(), doc_id))
    else:
        values["created_by"] = user["id"]
        doc_id = conn.execute(
            f"INSERT INTO sales_documents({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
    conn.execute("DELETE FROM sales_document_items WHERE document_id=?", (doc_id,))
    conn.executemany("INSERT INTO sales_document_items(document_id,description,quantity,days,unit_price_rupiah,position) VALUES(?,?,?,?,?,?)",
                     [(doc_id, *item, index) for index, item in enumerate(items)])
    advance_document_stage(conn, lead, kind, status)
    return {"ok": True, "id": doc_id, "document_no": number, "amount_rupiah": amount}


def advance_document_stage(conn: sqlite3.Connection, lead: sqlite3.Row, kind: str, status: str) -> None:
    if status in {"draft", "cancelled"}:
        return
    stage = conn.execute("SELECT * FROM sales_stages WHERE pipeline_id=? AND code=?", (lead["pipeline_id"], kind)).fetchone()
    current = conn.execute("SELECT position FROM sales_stages WHERE id=?", (lead["stage_id"],)).fetchone()
    if stage and stage["position"] >= current["position"]:
        conn.execute("UPDATE sales_leads SET stage_id=?,status=?,updated_at=? WHERE id=?",
                     (stage["id"], "won" if kind == "spk" else lead["status"], now_iso(), lead["id"]))


def next_number(conn: sqlite3.Connection, table: str, column: str, kind: str) -> str:
    prefix = f"{kind}-{datetime.now().strftime('%y%m')}-"
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",)).fetchall()
    suffixes = [row[0][len(prefix):] for row in rows]
    number = max((int(s) for s in suffixes if s.isdigit()), default=0) + 1
    return f"{prefix}{number:04d}"


def create_spk_event(conn: sqlite3.Connection, user: dict, doc_id: int, payload: dict) -> dict:
    doc = conn.execute("SELECT * FROM sales_documents WHERE id=? AND kind='spk'", (doc_id,)).fetchone()
    if not doc:
        raise ValueError("SPK tidak ditemukan.")
    lead = ensure_lead_access(conn, user, doc["lead_id"], write=True)
    if doc["status"] in {"draft", "cancelled"}:
        raise ValueError("Terbitkan SPK sebelum menjadwalkan event.")
    if lead["event_id"]:
        return {"ok": True, "event_id": lead["event_id"]}
    wib = timezone(timedelta(hours=7))
    moments = []
    for key in ("starts_at", "ends_at"):
        try:
            value = datetime.fromisoformat(str(payload.get(key, "")))
            value = value.replace(tzinfo=wib) if value.tzinfo is None else value.astimezone(wib)
        except ValueError as exc:
            raise ValueError("Isi waktu mulai dan selesai event yang valid (WIB).") from exc
        if not 2000 <= value.year <= 2100:
            raise ValueError("Tahun event tidak valid.")
        moments.append(value)
    starts, ends = moments
    if not timedelta(0) < ends - starts <= timedelta(days=14):
        raise ValueError("Durasi event harus positif dan maksimal 14 hari.")
    code = f"CI-SPK-{doc_id:06d}"
    event_id = conn.execute("""INSERT INTO events(project_code,title,starts_at,ends_at,location,is_manual,is_full_day_manual,is_full_day)
        VALUES(?,?,?,?,?,1,1,?)""", (code, lead["event_name"], starts.isoformat(), ends.isoformat(), lead["venue"], int(payload.get("is_full_day") is True))).lastrowid
    for table, status in (("cash_advances", "not_submitted"), ("warehouse_checks", "needs_prep"), ("event_communications", "not_created")):
        conn.execute(f"INSERT INTO {table}(event_id,status) VALUES(?,?)", (event_id, status))
    conn.execute("INSERT INTO design_tasks(event_id,title,status) VALUES(?,'Desain event','brief_needed')", (event_id,))
    owner = conn.execute("SELECT full_name FROM users WHERE id=?", (lead["owner_id"],)).fetchone()
    conn.execute("""INSERT INTO event_operations(event_id,client_name,client_phone,sales_name,notes,updated_by)
        VALUES(?,?,?,?,?,?)""", (event_id, lead["client_name"], lead["contact_phone"], owner[0], f"SPK {doc['document_no']}", user["id"]))
    conn.execute("UPDATE sales_leads SET event_id=?,updated_at=? WHERE id=?", (event_id, now_iso(), lead["id"]))
    conn.execute("UPDATE delivery_orders SET event_id=?,updated_at=? WHERE lead_id=? AND event_id IS NULL", (event_id, now_iso(), lead["id"]))
    return {"ok": True, "event_id": event_id, "project_code": code}


def print_page(title: str, body: str) -> str:
    return f'''<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{escape(title)}</title><link rel="stylesheet" href="/sales-print.css">
      <script src="/vendor/qrcode.min.js" defer></script><script src="/sales-print.js" defer></script></head>
      <body><nav><button id="print-document" type="button">Cetak / Simpan PDF</button></nav><main>{body}</main></body></html>'''


def document_print(conn: sqlite3.Connection, user: dict, doc_id: int) -> str:
    doc = conn.execute("SELECT * FROM sales_documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        raise ValueError("Dokumen tidak ditemukan.")
    lead = ensure_lead_access(conn, user, doc["lead_id"])
    lead = {**dict(lead), **json.loads(doc['context_json'])}
    items = conn.execute("SELECT * FROM sales_document_items WHERE document_id=? ORDER BY position", (doc_id,)).fetchall()
    source = conn.execute("SELECT document_no FROM sales_documents WHERE id=?", (doc["source_document_id"],)).fetchone()
    rows = "".join(f"<tr><td>{escape(row['description'])}</td><td>{row['quantity']}</td><td>{row['days']}</td><td>{format_money(row['unit_price_rupiah'])}</td><td>{format_money(row['quantity'] * row['days'] * row['unit_price_rupiah'])}</td></tr>" for row in items)
    body = f'''<header><b>CAPTURE IT</b><h1>{DOC_KINDS[doc['kind']]}</h1><p>{escape(doc['document_no'])} | {escape(doc['status'].upper())}</p></header>
      <section><p><b>Kepada:</b> {escape(doc['bill_to'] or lead['client_name'])}</p>
      <p><b>Event:</b> {escape(lead['event_name'])} | {escape(lead['event_date'])} | {escape(lead['venue'])}</p>
      <p><b>Tanggal:</b> {escape(doc['issued_at'])} | <b>Jatuh tempo:</b> {escape(doc['due_at'] or '-')}</p>
      <p><b>Dokumen sumber:</b> {escape(source[0] if source else '-')}</p></section>
      <table><thead><tr><th>Deskripsi</th><th>Qty</th><th>Hari</th><th>Harga satuan</th><th>Subtotal</th></tr></thead><tbody>{rows}</tbody></table>
      <section class="totals"><p>Diskon: {format_money(doc['discount_rupiah'])}</p><p>Pajak: {format_money(doc['tax_rupiah'])}</p><h2>Total: {format_money(doc['amount_rupiah'])}</h2></section>
      <section><h3>Ketentuan</h3><p>{escape(doc['terms'])}</p><p>{escape(doc['notes'])}</p></section>
      <footer><div>Disiapkan oleh<br><br><br>____________________</div><div>Disetujui oleh<br><br><br>____________________</div></footer>'''
    return print_page(doc["document_no"], body)


def format_money(value: int) -> str:
    return "Rp " + f"{value:,}".replace(",", ".")


def delivery_print(conn: sqlite3.Connection, user: dict, order_id: int) -> str:
    if not has_any(user, "delivery_orders.read", "delivery_orders.manage"):
        raise PermissionError("Akun ini tidak memiliki akses surat jalan.")
    data = delivery_payload(conn, user)
    order = next((row for row in data["orders"] if row["id"] == order_id), None)
    if not order:
        raise PermissionError("Surat jalan tidak tersedia untuk akun ini.")
    rows = "".join(f"<tr><td>{escape(row['sku'])}</td><td>{escape(row['item_name'])}</td><td>{row['planned_quantity']} {escape(row['unit'])}</td><td>{row['out_quantity']}</td><td>{row['returned_quantity']}</td></tr>" for row in data["items"] if row["delivery_order_id"] == order_id)
    body = f'''<header><b>CAPTURE IT</b><h1>Surat Jalan</h1><p>{escape(order['number'])} | {escape(order['status'].upper())}</p></header>
      <p><b>SPK:</b> {escape(order['spk_number'] or '-')}</p><p><b>Event:</b> {escape(order['event_title'] or order['event_name'] or '-')} | {escape(order['project_code'] or '-')}</p>
      <p><b>Penerima:</b> {escape(order['recipient_name'])}</p><p><b>Tujuan:</b> {escape(order['destination'])}</p>
      <table><thead><tr><th>SKU</th><th>Alat / barang</th><th>Rencana</th><th>Keluar</th><th>Kembali</th></tr></thead><tbody>{rows}</tbody></table>
      <p>{escape(order['notes'])}</p><p>Dirilis oleh: {escape(order['released_by_name'] or '-')} | {escape(order['released_at'] or '-')}</p>
      <footer><div>Gudang<br><br><br>____________________</div><div>Penerima<br><br><br>____________________</div></footer>'''
    return print_page(order["number"], body)


def inventory_labels(conn: sqlite3.Connection, user: dict, item_id: int) -> str:
    if not has_any(user, "inventory.read", "inventory.manage", "inventory.scan"):
        raise PermissionError("Akun ini tidak memiliki akses inventory.")
    item = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise ValueError("Item tidak ditemukan.")
    codes = [row[0] for row in conn.execute("SELECT asset_code FROM inventory_assets WHERE item_id=? AND status!='retired' ORDER BY asset_code", (item_id,))] if item["kind"] == "asset" else [item["barcode"] or item["sku"]]
    labels = "".join(f'<article class="qr-label"><div data-qr="{escape(code, quote=True)}"></div><b>{escape(code)}</b><p>{escape(item["name"])}</p></article>' for code in codes)
    return print_page("Label " + item["name"], f'<h1>Label barang</h1><div class="qr-labels">{labels or "Belum ada unit asset."}</div>')


def item_stock_expression(alias: str = "m") -> str:
    return f"COALESCE(SUM(CASE {alias}.direction WHEN 'in' THEN {alias}.quantity WHEN 'return' THEN {alias}.quantity WHEN 'adjustment_in' THEN {alias}.quantity WHEN 'out' THEN -{alias}.quantity WHEN 'adjustment_out' THEN -{alias}.quantity ELSE 0 END),0)"


def inventory_items(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(f"""
        SELECT i.*,{item_stock_expression()} AS stock_on_hand
        FROM inventory_items i
        LEFT JOIN inventory_movements m ON m.item_id=i.id
        WHERE i.active=1
        GROUP BY i.id
        ORDER BY i.kind,i.name
    """).fetchall()]


def inventory_payload(conn: sqlite3.Connection, user: dict) -> dict | None:
    if not has_any(user, "inventory.read", "inventory.manage", "inventory.scan"):
        return None
    items = inventory_items(conn)
    recent = [dict(row) for row in conn.execute("""
        SELECT m.*,i.name AS item_name,i.sku,u.full_name AS actor_name,e.project_code,d.number AS delivery_number,a.asset_code
        FROM inventory_movements m
        JOIN inventory_items i ON i.id=m.item_id
        LEFT JOIN users u ON u.id=m.created_by
        LEFT JOIN events e ON e.id=m.event_id
        LEFT JOIN delivery_orders d ON d.id=m.delivery_order_id
        LEFT JOIN inventory_assets a ON a.id=m.asset_id
        ORDER BY m.created_at DESC,m.id DESC LIMIT 80
    """).fetchall()]
    locations = [dict(row) for row in conn.execute("SELECT id,name,active FROM inventory_locations WHERE active=1 ORDER BY name").fetchall()]
    low_stock = [item for item in items if item["kind"] == "inventory" and int(item["stock_on_hand"] or 0) <= int(item["min_quantity"] or 0)]
    assets = [dict(row) for row in conn.execute("""SELECT a.*,i.name AS item_name,i.sku,d.number AS delivery_number
        FROM inventory_assets a JOIN inventory_items i ON i.id=a.item_id
        LEFT JOIN delivery_orders d ON d.id=a.delivery_order_id ORDER BY i.name,a.asset_code""")]
    outstanding = [dict(row) for row in conn.execute("""SELECT d.id,d.number,d.destination,
        SUM(CASE m.direction WHEN 'out' THEN m.quantity WHEN 'return' THEN -m.quantity ELSE 0 END) AS quantity
        FROM delivery_orders d JOIN inventory_movements m ON m.delivery_order_id=d.id
        GROUP BY d.id HAVING quantity>0 ORDER BY d.created_at""")]
    return {"items": items, "assets": assets, "movements": recent, "locations": locations, "low_stock": low_stock, "outstanding": outstanding}


def ensure_unique_scan_code(conn: sqlite3.Connection, code: str, item_id: int = 0) -> None:
    if not code:
        return
    if conn.execute("SELECT 1 FROM inventory_items WHERE (sku=? OR barcode=?) AND id!=?", (code, code, item_id)).fetchone() or conn.execute("SELECT 1 FROM inventory_assets WHERE asset_code=?", (code,)).fetchone():
        raise ValueError("Kode scan sudah digunakan barang atau asset lain.")


def save_item(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "inventory.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses mengatur inventory.")
    item_id = payload.get("id")
    kind = payload.get("kind") if payload.get("kind") in ITEM_KINDS else "inventory"
    barcode = clean_code(payload.get("barcode"), "Barcode", 64)
    values = {
        "sku": clean_code(payload.get("sku"), "SKU/barcode", 64, required=True),
        "barcode": barcode or None,
        "name": clean_text(payload.get("name"), "Nama item", 160, required=True),
        "category": clean_text(payload.get("category"), "Kategori", 100),
        "kind": kind,
        "unit": clean_text(payload.get("unit") or "pcs", "Satuan", 24, required=True),
        "min_quantity": positive_int(payload.get("min_quantity") or 0, "Stok minimum"),
        "active": 1 if payload.get("active", True) is not False else 0,
        "updated_at": now_iso(),
    }
    ensure_unique_scan_code(conn, values["sku"], positive_int(item_id, "Item") if item_id else 0)
    ensure_unique_scan_code(conn, barcode, positive_int(item_id, "Item") if item_id else 0)
    if item_id:
        item_id = positive_int(item_id, "Item")
        old = conn.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)).fetchone()
        if not old:
            raise ValueError("Item tidak ditemukan.")
        if kind != old["kind"]:
            raise ValueError("Jenis item tidak dapat diubah setelah dibuat.")
        if not values["active"] and (item_stock(conn, item_id) or conn.execute("SELECT 1 FROM delivery_order_items di JOIN delivery_orders d ON d.id=di.delivery_order_id WHERE di.item_id=? AND d.status NOT IN ('cancelled','returned')", (item_id,)).fetchone()):
            raise ValueError("Barang masih memiliki stok atau surat jalan aktif.")
        columns = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE inventory_items SET {columns} WHERE id=?", (*values.values(), item_id))
    else:
        values["created_by"] = user["id"]
        item_id = conn.execute(
            f"INSERT INTO inventory_items({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
        initial_qty = positive_int(payload.get("initial_quantity") or 0, "Stok awal")
        if kind == "asset" and initial_qty:
            if initial_qty > 500:
                raise ValueError("Pembuatan asset maksimal 500 unit per transaksi.")
            for index in range(initial_qty):
                save_asset(conn, user, {"item_id": item_id, "asset_code": f"{values['sku']}-{index + 1:04d}"})
        elif initial_qty:
            default_location = conn.execute("SELECT id FROM inventory_locations ORDER BY id LIMIT 1").fetchone()
            conn.execute(
                """INSERT INTO inventory_movements(item_id,location_id,direction,quantity,scan_code,notes,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (item_id, default_location["id"] if default_location else None, "adjustment_in", initial_qty,
                 values["sku"], "Stok awal", user["id"], now_iso()),
            )
    return {"ok": True, "id": item_id}


def save_asset(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "inventory.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses mengatur asset.")
    item_id = positive_int(payload.get("item_id"), "Item")
    item = conn.execute("SELECT * FROM inventory_items WHERE id=? AND kind='asset' AND active=1", (item_id,)).fetchone()
    if not item:
        raise ValueError("Pilih master item berjenis asset.")
    code = clean_code(payload.get("asset_code"), "Kode asset", 64, True)
    ensure_unique_scan_code(conn, code)
    existing_stock = payload.get("existing_stock") is True
    if existing_stock:
        labelled = conn.execute("SELECT COUNT(*) FROM inventory_assets WHERE item_id=? AND status='available'", (item_id,)).fetchone()[0]
        if labelled >= item_stock(conn, item_id):
            raise ValueError("Tidak ada stok lama yang belum diberi kode asset.")
    asset_id = conn.execute("INSERT INTO inventory_assets(item_id,asset_code,serial_number,created_by,updated_at) VALUES(?,?,?,?,?)",
                            (item_id, code, clean_text(payload.get("serial_number"), "Serial number", 120), user["id"], now_iso())).lastrowid
    if not existing_stock:
        conn.execute("""INSERT INTO inventory_movements(item_id,asset_id,direction,quantity,scan_code,notes,created_by,created_at)
            VALUES(?,?,'in',1,?,'Registrasi asset',?,?)""", (item_id, asset_id, code, user["id"], now_iso()))
    return {"ok": True, "id": asset_id}


def item_stock(conn: sqlite3.Connection, item_id: int) -> int:
    row = conn.execute(f"SELECT {item_stock_expression()} AS qty FROM inventory_movements m WHERE m.item_id=?", (item_id,)).fetchone()
    return int(row["qty"] if row else 0)


def scan_inventory(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "inventory.scan" not in user["permissions"] and "inventory.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses scan inventory.")
    scan_code = clean_code(payload.get("scan_code"), "Kode scan", 64, required=True)
    direction = payload.get("direction")
    if direction not in MOVE_DIRECTIONS:
        raise ValueError("Arah scan tidak valid.")
    if direction in {"in", "adjustment_in", "adjustment_out"} and "inventory.manage" not in user["permissions"]:
        raise PermissionError("Penerimaan dan adjustment stok hanya untuk pengelola inventory.")
    quantity = positive_int(payload.get("quantity", 1), "Jumlah")
    if quantity < 1:
        raise ValueError("Jumlah minimal 1.")
    request_id = clean_text(payload.get("request_id"), "ID scan", 80)
    if request_id:
        prior = conn.execute("SELECT * FROM inventory_movements WHERE request_id=?", (request_id,)).fetchone()
        if prior:
            if (prior["created_by"], prior["scan_code"], prior["direction"], prior["quantity"], prior["delivery_order_id"] or 0) != (user["id"], scan_code, direction, quantity, positive_int(payload.get("delivery_order_id") or 0, "Surat jalan")):
                raise ValueError("ID scan sudah dipakai untuk transaksi lain.")
            return {"ok": True, "id": prior["id"], "item_id": prior["item_id"], "stock_on_hand": item_stock(conn, prior["item_id"]), "duplicate": True}
    asset = conn.execute("SELECT * FROM inventory_assets WHERE asset_code=?", (scan_code,)).fetchone()
    item = conn.execute("SELECT * FROM inventory_items WHERE id=? AND active=1", (asset["item_id"],)).fetchone() if asset else conn.execute("SELECT * FROM inventory_items WHERE active=1 AND (sku=? OR barcode=?)", (scan_code, scan_code)).fetchone()
    if not item:
        raise ValueError("Kode barang tidak ditemukan di master inventory.")
    if item["kind"] == "asset" and not asset:
        raise ValueError("Scan kode asset per unit, bukan SKU master. Daftarkan unit asset lebih dahulu.")
    if asset and (quantity != 1 or direction not in {"out", "return"}):
        raise ValueError("Asset discan satu per satu untuk keluar atau kembali.")
    current = item_stock(conn, item["id"])
    if MOVE_DIRECTIONS[direction] < 0 and current - quantity < 0:
        raise ValueError(f"Stok {item['name']} tidak cukup. Sisa saat ini {current}.")
    location_id = positive_int(payload.get("location_id") or 0, "Lokasi")
    if location_id and not conn.execute("SELECT id FROM inventory_locations WHERE id=? AND active=1", (location_id,)).fetchone():
        raise ValueError("Lokasi tidak valid.")
    event_id = positive_int(payload.get("event_id") or 0, "Event")
    if event_id and not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError("Event tidak ditemukan.")
    delivery_order_id = positive_int(payload.get("delivery_order_id") or 0, "Surat jalan")
    order = conn.execute("SELECT * FROM delivery_orders WHERE id=?", (delivery_order_id,)).fetchone() if delivery_order_id else None
    if direction in {"out", "return"}:
        if not order or order["status"] not in {"released", "out"}:
            raise ValueError("Scan keluar/kembali memerlukan surat jalan yang sudah dirilis dan belum selesai.")
        ensure_delivery_access(conn, user, order)
        if event_id and event_id != order["event_id"]:
            raise ValueError("Event scan tidak sesuai surat jalan.")
        event_id = order["event_id"] or 0
        planned = conn.execute("SELECT planned_quantity FROM delivery_order_items WHERE delivery_order_id=? AND item_id=?", (delivery_order_id, item["id"])).fetchone()
        if not planned:
            raise ValueError("Barang tidak tercantum pada surat jalan ini.")
        counts = conn.execute("""SELECT COALESCE(SUM(CASE WHEN direction='out' THEN quantity ELSE 0 END),0) AS sent,
            COALESCE(SUM(CASE WHEN direction='return' THEN quantity ELSE 0 END),0) AS received
            FROM inventory_movements WHERE delivery_order_id=? AND item_id=?""", (delivery_order_id, item["id"])).fetchone()
        if direction == "out" and counts["sent"] + quantity > planned[0]:
            raise ValueError("Jumlah keluar melebihi rencana surat jalan.")
        if direction == "return" and counts["received"] + quantity > counts["sent"]:
            raise ValueError("Jumlah kembali melebihi barang yang keluar.")
        if asset:
            if direction == "out" and asset["status"] != "available":
                raise ValueError("Asset tidak tersedia atau sudah keluar.")
            if direction == "return" and (asset["status"] != "out" or asset["delivery_order_id"] != delivery_order_id):
                raise ValueError("Asset ini tidak keluar dengan surat jalan yang dipilih.")
    elif delivery_order_id:
        raise ValueError("Surat jalan hanya untuk scan keluar/kembali.")
    notes = clean_text(payload.get("notes"), "Catatan", 800)
    movement_id = conn.execute(
        """INSERT INTO inventory_movements(item_id,location_id,event_id,delivery_order_id,direction,quantity,scan_code,notes,created_by,created_at,asset_id,request_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item["id"], location_id or None, event_id or None, delivery_order_id or None,
         direction, quantity, scan_code, notes, user["id"], now_iso(), asset["id"] if asset else None, request_id or None),
    ).lastrowid
    if asset:
        conn.execute("UPDATE inventory_assets SET status=?,delivery_order_id=?,updated_at=? WHERE id=?",
                     ("out" if direction == "out" else "available", delivery_order_id if direction == "out" else None, now_iso(), asset["id"]))
    if order:
        counts = conn.execute("""SELECT COALESCE(SUM(CASE WHEN direction='out' THEN quantity ELSE 0 END),0) AS sent,
            COALESCE(SUM(CASE WHEN direction='return' THEN quantity ELSE 0 END),0) AS received
            FROM inventory_movements WHERE delivery_order_id=?""", (delivery_order_id,)).fetchone()
        total = conn.execute("SELECT SUM(planned_quantity) FROM delivery_order_items WHERE delivery_order_id=?", (delivery_order_id,)).fetchone()[0]
        status = "returned" if counts["sent"] == counts["received"] == total else "out"
        conn.execute("UPDATE delivery_orders SET status=?,updated_at=? WHERE id=?", (status, now_iso(), delivery_order_id))
    return {"ok": True, "id": movement_id, "item_id": item["id"], "stock_on_hand": item_stock(conn, item["id"])}


def own_sales_only(user: dict) -> bool:
    return has_any(user, "sales.read_own", "sales.manage_own") and not can_sales_all(user)


def ensure_delivery_access(conn: sqlite3.Connection, user: dict, order: sqlite3.Row) -> None:
    if not has_any(user, "delivery_orders.read", "delivery_orders.manage", "inventory.scan", "inventory.manage"):
        raise PermissionError("Akun ini tidak memiliki akses surat jalan.")
    if own_sales_only(user):
        lead_id = order["lead_id"]
        if not lead_id and order["spk_document_id"]:
            doc = conn.execute("SELECT lead_id FROM sales_documents WHERE id=?", (order["spk_document_id"],)).fetchone()
            lead_id = doc[0] if doc else None
        if not lead_id:
            raise PermissionError("Surat jalan ini bukan milik akun sales Anda.")
        ensure_lead_access(conn, user, lead_id)


def delivery_payload(conn: sqlite3.Connection, user: dict) -> dict | None:
    if not has_any(user, "delivery_orders.read", "delivery_orders.manage"):
        return None
    restricted = own_sales_only(user)
    args = (user["id"],) if restricted else ()
    scope = "WHERE l.owner_id=?" if restricted else ""
    orders = [dict(row) for row in conn.execute(f"""
        SELECT d.*,e.project_code,e.title AS event_title,e.starts_at,e.location,l.client_name,l.event_name,
               spk.document_no AS spk_number,u.full_name AS released_by_name,
               COALESCE((SELECT COUNT(*) FROM delivery_order_items di WHERE di.delivery_order_id=d.id),0) AS item_count
        FROM delivery_orders d
        LEFT JOIN events e ON e.id=d.event_id
        LEFT JOIN sales_documents spk ON spk.id=d.spk_document_id
        LEFT JOIN sales_leads l ON l.id=COALESCE(d.lead_id,spk.lead_id)
        LEFT JOIN users u ON u.id=d.released_by
        {scope}
        ORDER BY d.created_at DESC,d.id DESC
    """, args).fetchall()]
    order_ids = [row["id"] for row in orders]
    items: list[dict] = []
    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        items = [dict(row) for row in conn.execute(f"""
            SELECT di.*,i.name AS item_name,i.sku,i.unit,
                   COALESCE((SELECT SUM(quantity) FROM inventory_movements m WHERE m.delivery_order_id=di.delivery_order_id AND m.item_id=di.item_id AND m.direction='out'),0) AS out_quantity,
                   COALESCE((SELECT SUM(quantity) FROM inventory_movements m WHERE m.delivery_order_id=di.delivery_order_id AND m.item_id=di.item_id AND m.direction='return'),0) AS returned_quantity
            FROM delivery_order_items di JOIN inventory_items i ON i.id=di.item_id
            WHERE di.delivery_order_id IN ({placeholders})
            ORDER BY di.delivery_order_id,i.name
        """, tuple(order_ids)).fetchall()]
    return {
        "orders": orders,
        "items": items,
        "events": [dict(row) for row in conn.execute(f"""
            SELECT id,project_code,title,starts_at,location FROM events WHERE status!='cancelled'
            {"AND id IN (SELECT event_id FROM sales_leads WHERE owner_id=?)" if restricted else ""}
            ORDER BY starts_at DESC LIMIT 200""", args).fetchall()],
        "spk_documents": [dict(row) for row in conn.execute(f"""
            SELECT d.id,d.lead_id,d.document_no,l.client_name,l.event_name,l.event_date,l.venue,l.event_id
            FROM sales_documents d JOIN sales_leads l ON l.id=d.lead_id
            WHERE d.kind='spk' AND d.status NOT IN ('draft','cancelled') {"AND l.owner_id=?" if restricted else ""}
            ORDER BY d.created_at DESC,d.id DESC LIMIT 200
        """, args).fetchall()],
        "catalog": [{key: item[key] for key in ("id", "name", "sku", "unit", "kind", "stock_on_hand")} for item in inventory_items(conn)] if "delivery_orders.manage" in user["permissions"] else [],
    }


def next_delivery_number(conn: sqlite3.Connection) -> str:
    return next_number(conn, "delivery_orders", "number", "SJ")


def save_delivery_order(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "delivery_orders.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses membuat surat jalan.")
    order_id = payload.get("id")
    current = None
    if order_id:
        order_id = positive_int(order_id, "Surat jalan")
        current = conn.execute("SELECT * FROM delivery_orders WHERE id=?", (order_id,)).fetchone()
        if not current:
            raise ValueError("Surat jalan tidak ditemukan.")
        ensure_delivery_access(conn, user, current)
        if current["status"] != "draft":
            if set(payload) <= {"id", "status"} and payload.get("status") == "cancelled" and current["status"] == "released":
                if conn.execute("SELECT 1 FROM inventory_movements WHERE delivery_order_id=?", (order_id,)).fetchone():
                    raise ValueError("Surat jalan sudah memiliki scan dan tidak dapat dibatalkan.")
                conn.execute("UPDATE delivery_orders SET status='cancelled',updated_at=? WHERE id=?", (now_iso(), order_id))
                return {"ok": True, "id": order_id, "number": current["number"], "status": "cancelled"}
            raise ValueError("Surat jalan yang sudah dirilis tidak dapat diedit.")
        payload = {**dict(current), **payload}
    event_id = positive_int(payload.get("event_id") or 0, "Event")
    lead_id = positive_int(payload.get("lead_id") or 0, "Lead")
    spk_id = positive_int(payload.get("spk_document_id") or 0, "SPK")
    if not spk_id and not (current and current["event_id"]):
        raise ValueError("Pilih SPK sebagai sumber surat jalan.")
    if event_id and not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError("Event tidak ditemukan.")
    if spk_id:
        spk = conn.execute("SELECT * FROM sales_documents WHERE id=? AND kind='spk'", (spk_id,)).fetchone()
        if not spk or spk["status"] in {"draft", "cancelled"}:
            raise ValueError("Pilih SPK yang sudah terbit dan belum dibatalkan.")
        if lead_id and lead_id != spk["lead_id"]:
            raise ValueError("Lead tidak sesuai dengan SPK.")
        lead_id = lead_id or spk["lead_id"]
    if lead_id and not conn.execute("SELECT id FROM sales_leads WHERE id=?", (lead_id,)).fetchone():
        raise ValueError("Lead tidak ditemukan.")
    lead = conn.execute("SELECT * FROM sales_leads WHERE id=?", (lead_id,)).fetchone() if lead_id else None
    if lead:
        if own_sales_only(user):
            ensure_lead_access(conn, user, lead_id)
        if event_id and lead["event_id"] and event_id != lead["event_id"]:
            raise ValueError("Event tidak sesuai dengan SPK/lead.")
        event_id = event_id or lead["event_id"] or 0
        if event_id:
            conn.execute("UPDATE sales_leads SET event_id=? WHERE id=?", (event_id, lead_id))
    status = payload.get("status", "draft")
    if status not in {"draft", "released", "cancelled"}:
        raise ValueError("Status keluar/kembali hanya berubah melalui scan.")
    release = payload.get("release") is True
    if release:
        status = "released"
    if not current and status == "cancelled":
        raise ValueError("Buat surat jalan sebagai draft terlebih dahulu.")
    raw_items = payload.get("items")
    if raw_items is None and current:
        raw_items = [dict(row) for row in conn.execute("SELECT * FROM delivery_order_items WHERE delivery_order_id=?", (order_id,))]
    if not isinstance(raw_items, list) or len(raw_items) > 300 or (status == "released" and not raw_items):
        raise ValueError("Surat jalan yang dirilis wajib berisi daftar alat (maksimal 300 item).")
    prepared = []
    seen = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValueError("Daftar item surat jalan tidak valid.")
        item_id = positive_int(entry.get("item_id"), "Item")
        if item_id in seen:
            raise ValueError("Item surat jalan tidak boleh dobel.")
        seen.add(item_id)
        if not conn.execute("SELECT 1 FROM inventory_items WHERE id=? AND active=1", (item_id,)).fetchone():
            raise ValueError("Item inventory tidak ditemukan.")
        planned = positive_int(entry.get("planned_quantity"), "Qty rencana")
        if not planned:
            raise ValueError("Qty rencana minimal 1.")
        if status == "released":
            reserved = conn.execute("""SELECT COALESCE(SUM(MAX(0,di.planned_quantity-
                COALESCE((SELECT SUM(m.quantity) FROM inventory_movements m WHERE m.delivery_order_id=d.id AND m.item_id=di.item_id AND m.direction='out'),0))),0)
                FROM delivery_order_items di JOIN delivery_orders d ON d.id=di.delivery_order_id
                WHERE di.item_id=? AND d.id!=? AND d.status IN ('released','out')""", (item_id, order_id or 0)).fetchone()[0]
            if planned > item_stock(conn, item_id) - reserved:
                raise ValueError("Stok tersedia tidak cukup; sebagian stok mungkin dialokasikan ke surat jalan lain.")
        prepared.append((item_id, planned, clean_text(entry.get("notes"), "Catatan item", 500)))
    values = {
        "event_id": event_id or None,
        "lead_id": lead_id or None,
        "spk_document_id": spk_id or None,
        "number": clean_code(payload.get("number") or next_delivery_number(conn), "Nomor surat jalan", 80, required=True),
        "recipient_name": clean_text(payload.get("recipient_name") or (lead["contact_name"] if lead else ""), "Penerima", 160),
        "destination": clean_text(payload.get("destination") or (lead["venue"] if lead else ""), "Tujuan", 240),
        "status": status,
        "notes": clean_text(payload.get("notes"), "Catatan", 1200),
        "updated_at": now_iso(),
    }
    if status == "released":
        values["released_by"] = user["id"]
        values["released_at"] = now_iso()
    if order_id:
        order_id = positive_int(order_id, "Surat jalan")
        current = conn.execute("SELECT * FROM delivery_orders WHERE id=?", (order_id,)).fetchone()
        if not current:
            raise ValueError("Surat jalan tidak ditemukan.")
        if current["status"] in {"out", "returned", "cancelled"} and status == "draft":
            raise ValueError("Surat jalan yang sudah berjalan tidak dapat dikembalikan ke draft.")
        update_keys = list(values)
        conn.execute(f"UPDATE delivery_orders SET {','.join(f'{key}=?' for key in update_keys)} WHERE id=?",
                     (*[values[key] for key in update_keys], order_id))
    else:
        values["created_by"] = user["id"]
        values["created_at"] = now_iso()
        order_id = conn.execute(
            f"INSERT INTO delivery_orders({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
    conn.execute("DELETE FROM delivery_order_items WHERE delivery_order_id=?", (order_id,))
    conn.executemany("INSERT INTO delivery_order_items(delivery_order_id,item_id,planned_quantity,notes) VALUES(?,?,?,?)",
                     [(order_id, *item) for item in prepared])
    return {"ok": True, "id": order_id, "number": values["number"], "status": status}

