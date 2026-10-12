"""Sales pipeline, delivery order, and inventory helpers for Capture It Ops."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timezone


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
    if isinstance(value, bool):
        raise ValueError(f"{label} harus berupa angka.")
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
    cur = conn.execute(
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
    elif "sales.read_own" not in user["permissions"] or not own:
        raise PermissionError("Lead ini bukan milik akun sales Anda.")
    return lead


def sales_people(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT DISTINCT u.id,u.full_name,u.email
           FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id
           WHERE u.active=1 AND r.code IN ('sales_staff','administrator')
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
    return [dict(row) for row in conn.execute(f"""
        SELECT d.*,l.client_name,l.event_name,l.owner_id
        FROM sales_documents d JOIN sales_leads l ON l.id=d.lead_id
        {where}
        ORDER BY d.created_at DESC,d.id DESC
    """, args).fetchall()]


def sales_dashboard(conn: sqlite3.Connection, user: dict, leads: list[dict]) -> dict:
    open_leads = [lead for lead in leads if lead["status"] == "open"]
    won = [lead for lead in leads if lead["status"] == "won"]
    return {
        "open_leads": len(open_leads),
        "won_leads": len(won),
        "estimated_value": sum(int(lead["estimated_value_rupiah"] or 0) for lead in open_leads),
        "won_value": sum(int(lead["estimated_value_rupiah"] or 0) for lead in won),
        "by_stage": [
            dict(row) for row in conn.execute("""
                SELECT s.id AS stage_id,s.name AS stage_name,s.code AS stage_code,COUNT(l.id) AS total,
                       COALESCE(SUM(l.estimated_value_rupiah),0) AS value_rupiah
                FROM sales_stages s
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
    return {
        "pipelines": pipelines,
        "stages": stages,
        "leads": leads,
        "documents": sales_documents(conn, user),
        "sales_people": sales_people(conn) if "sales.manage_all" in user["permissions"] else [],
        "dashboard": sales_dashboard(conn, user, leads),
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
        "status": payload.get("status") if payload.get("status") in LEAD_STATUSES else "open",
        "updated_at": now_iso(),
    }
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
        status = "won" if stage["code"] == "spk" else "open"
    conn.execute("UPDATE sales_leads SET stage_id=?,status=?,updated_at=? WHERE id=?", (stage_id, status, now_iso(), lead_id))
    return {"ok": True, "id": lead_id, "stage_id": stage_id, "status": status}


def save_document(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if not can_sales_manage_any(user):
        raise PermissionError("Akun ini tidak memiliki akses menyimpan dokumen sales.")
    lead_id = positive_int(payload.get("lead_id"), "Lead")
    ensure_lead_access(conn, user, lead_id, write=True)
    kind = payload.get("kind")
    if kind not in DOC_KINDS:
        raise ValueError("Jenis dokumen tidak valid.")
    status = payload.get("status") if payload.get("status") in DOC_STATUSES else "draft"
    doc_id = payload.get("id")
    values = {
        "lead_id": lead_id,
        "kind": kind,
        "document_no": clean_text(payload.get("document_no"), "Nomor dokumen", 80, required=True),
        "amount_rupiah": money(payload.get("amount_rupiah"), "Nominal dokumen"),
        "status": status,
        "issued_at": optional_date(payload.get("issued_at"), "Tanggal dokumen"),
        "notes": clean_text(payload.get("notes"), "Catatan dokumen", 1200),
        "updated_at": now_iso(),
    }
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
    if kind == "spk":
        stage = conn.execute(
            "SELECT s.id FROM sales_stages s JOIN sales_leads l ON l.pipeline_id=s.pipeline_id WHERE l.id=? AND s.code='spk'",
            (lead_id,),
        ).fetchone()
        if stage:
            conn.execute("UPDATE sales_leads SET stage_id=?,status='won',updated_at=? WHERE id=?", (stage["id"], now_iso(), lead_id))
    return {"ok": True, "id": doc_id}


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
        SELECT m.*,i.name AS item_name,i.sku,u.full_name AS actor_name,e.project_code,d.number AS delivery_number
        FROM inventory_movements m
        JOIN inventory_items i ON i.id=m.item_id
        LEFT JOIN users u ON u.id=m.created_by
        LEFT JOIN events e ON e.id=m.event_id
        LEFT JOIN delivery_orders d ON d.id=m.delivery_order_id
        ORDER BY m.created_at DESC,m.id DESC LIMIT 80
    """).fetchall()]
    locations = [dict(row) for row in conn.execute("SELECT id,name,active FROM inventory_locations WHERE active=1 ORDER BY name").fetchall()]
    low_stock = [item for item in items if item["kind"] == "inventory" and int(item["stock_on_hand"] or 0) <= int(item["min_quantity"] or 0)]
    return {"items": items, "movements": recent, "locations": locations, "low_stock": low_stock}


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
    if item_id:
        item_id = positive_int(item_id, "Item")
        if not conn.execute("SELECT id FROM inventory_items WHERE id=?", (item_id,)).fetchone():
            raise ValueError("Item tidak ditemukan.")
        columns = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE inventory_items SET {columns} WHERE id=?", (*values.values(), item_id))
    else:
        values["created_by"] = user["id"]
        item_id = conn.execute(
            f"INSERT INTO inventory_items({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        ).lastrowid
        initial_qty = positive_int(payload.get("initial_quantity") or 0, "Stok awal")
        if initial_qty:
            default_location = conn.execute("SELECT id FROM inventory_locations ORDER BY id LIMIT 1").fetchone()
            conn.execute(
                """INSERT INTO inventory_movements(item_id,location_id,direction,quantity,scan_code,notes,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (item_id, default_location["id"] if default_location else None, "adjustment_in", initial_qty,
                 values["sku"], "Stok awal", user["id"], now_iso()),
            )
    return {"ok": True, "id": item_id}


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
    quantity = positive_int(payload.get("quantity") or 1, "Jumlah")
    if quantity < 1:
        raise ValueError("Jumlah minimal 1.")
    item = conn.execute("SELECT * FROM inventory_items WHERE active=1 AND (sku=? OR barcode=?)", (scan_code, scan_code)).fetchone()
    if not item:
        raise ValueError("Kode barang tidak ditemukan di master inventory.")
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
    if delivery_order_id and not conn.execute("SELECT id FROM delivery_orders WHERE id=?", (delivery_order_id,)).fetchone():
        raise ValueError("Surat jalan tidak ditemukan.")
    notes = clean_text(payload.get("notes"), "Catatan", 800)
    movement_id = conn.execute(
        """INSERT INTO inventory_movements(item_id,location_id,event_id,delivery_order_id,direction,quantity,scan_code,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (item["id"], location_id or None, event_id or None, delivery_order_id or None,
         direction, quantity, scan_code, notes, user["id"], now_iso()),
    ).lastrowid
    return {"ok": True, "id": movement_id, "item_id": item["id"], "stock_on_hand": item_stock(conn, item["id"])}


def delivery_payload(conn: sqlite3.Connection, user: dict) -> dict | None:
    if not has_any(user, "delivery_orders.read", "delivery_orders.manage"):
        return None
    orders = [dict(row) for row in conn.execute("""
        SELECT d.*,e.project_code,e.title AS event_title,e.starts_at,e.location,l.client_name,l.event_name,
               spk.document_no AS spk_number,u.full_name AS released_by_name,
               COALESCE((SELECT COUNT(*) FROM delivery_order_items di WHERE di.delivery_order_id=d.id),0) AS item_count
        FROM delivery_orders d
        LEFT JOIN events e ON e.id=d.event_id
        LEFT JOIN sales_leads l ON l.id=d.lead_id
        LEFT JOIN sales_documents spk ON spk.id=d.spk_document_id
        LEFT JOIN users u ON u.id=d.released_by
        ORDER BY d.created_at DESC,d.id DESC
    """).fetchall()]
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
        "events": [dict(row) for row in conn.execute(
            "SELECT id,project_code,title,starts_at,location FROM events WHERE status!='cancelled' ORDER BY starts_at DESC LIMIT 200"
        ).fetchall()],
        "spk_documents": [dict(row) for row in conn.execute("""
            SELECT d.id,d.lead_id,d.document_no,l.client_name,l.event_name,l.event_date,l.venue
            FROM sales_documents d JOIN sales_leads l ON l.id=d.lead_id
            WHERE d.kind='spk' ORDER BY d.created_at DESC,d.id DESC LIMIT 200
        """).fetchall()],
    }


def next_delivery_number(conn: sqlite3.Connection) -> str:
    stamp = datetime.now().strftime("%y%m")
    prefix = f"SJ-{stamp}-"
    count = conn.execute("SELECT COUNT(*) FROM delivery_orders WHERE number LIKE ?", (prefix + "%",)).fetchone()[0]
    return f"{prefix}{count + 1:04d}"


def save_delivery_order(conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
    if "delivery_orders.manage" not in user["permissions"]:
        raise PermissionError("Akun ini tidak memiliki akses membuat surat jalan.")
    order_id = payload.get("id")
    event_id = positive_int(payload.get("event_id") or 0, "Event")
    lead_id = positive_int(payload.get("lead_id") or 0, "Lead")
    spk_id = positive_int(payload.get("spk_document_id") or 0, "SPK")
    if not event_id and not lead_id and not spk_id:
        raise ValueError("Pilih event atau SPK sebagai sumber surat jalan.")
    if event_id and not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError("Event tidak ditemukan.")
    if spk_id:
        spk = conn.execute("SELECT * FROM sales_documents WHERE id=? AND kind='spk'", (spk_id,)).fetchone()
        if not spk:
            raise ValueError("SPK tidak ditemukan.")
        lead_id = lead_id or spk["lead_id"]
    if lead_id and not conn.execute("SELECT id FROM sales_leads WHERE id=?", (lead_id,)).fetchone():
        raise ValueError("Lead tidak ditemukan.")
    status = payload.get("status") if payload.get("status") in {"draft", "released", "out", "returned", "cancelled"} else "draft"
    release = payload.get("release") is True
    if release:
        status = "released"
    values = {
        "event_id": event_id or None,
        "lead_id": lead_id or None,
        "spk_document_id": spk_id or None,
        "number": clean_code(payload.get("number") or next_delivery_number(conn), "Nomor surat jalan", 80, required=True),
        "recipient_name": clean_text(payload.get("recipient_name"), "Penerima", 160),
        "destination": clean_text(payload.get("destination"), "Tujuan", 240),
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
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list) or len(raw_items) > 300:
        raise ValueError("Daftar item surat jalan tidak valid.")
    if raw_items:
        conn.execute("DELETE FROM delivery_order_items WHERE delivery_order_id=?", (order_id,))
        seen: set[int] = set()
        for entry in raw_items:
            if not isinstance(entry, dict):
                raise ValueError("Daftar item surat jalan tidak valid.")
            item_id = positive_int(entry.get("item_id"), "Item")
            if item_id in seen:
                raise ValueError("Item surat jalan tidak boleh dobel.")
            seen.add(item_id)
            if not conn.execute("SELECT id FROM inventory_items WHERE id=? AND active=1", (item_id,)).fetchone():
                raise ValueError("Salah satu item inventory tidak ditemukan.")
            planned = positive_int(entry.get("planned_quantity") or 0, "Qty rencana")
            if planned < 1:
                raise ValueError("Qty rencana minimal 1.")
            conn.execute(
                "INSERT INTO delivery_order_items(delivery_order_id,item_id,planned_quantity,notes) VALUES(?,?,?,?)",
                (order_id, item_id, planned, clean_text(entry.get("notes"), "Catatan item", 500)),
            )
    return {"ok": True, "id": order_id, "number": values["number"], "status": status}

