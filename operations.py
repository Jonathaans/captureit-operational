"""Event logistics, role-scoped reminders and the supplied Events export format.

Only the Python standard library is needed at runtime. The packaged workbook
contains the reference layout without client data; export fills its data rows.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from closing_reports import approved_export, can_review_closing

WIB = timezone(timedelta(hours=7))
EXPORT_HEADERS = ["Event Name", "Client Name", "Client Phone", "Service Type", "Equipment Setup", "Sales", "Petugas",
                  "Event Date", "Start Time", "End Time", "Loading Date", "Loading Time", "Location", "Status",
                  "Vehicle Name", "Driver Name", "Vendor Sewa", "Ribbon Awal", "Ribbon Akhir", "Total Penggunaan", "Notes"]
TEXT_FIELDS = {"driver_name":120,"loading_date":10,"loading_time":5,"vehicle_return_at":32,
               "client_name":160,"client_phone":40,"service_type":100,"equipment_setup":200,"sales_name":120,"notes":4000,
               "courier_name":100,"courier_note":300,"other_transport":160,"transport_note":1000}
TRANSPORT_MODES = ('fleet', 'motorcycles', 'courier', 'other')


def transport_modes(value, vehicle_id=None):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) and value else (['fleet'] if vehicle_id else [])


def transport_label(logistics):
    modes = transport_modes(logistics.get('transport_modes'), logistics.get('vehicle_id'))
    labels = []
    if 'fleet' in modes:
        labels.append(logistics.get('vehicle_name') or 'Kendaraan operasional')
    if 'motorcycles' in modes:
        labels.append('Motor masing-masing')
    if 'courier' in modes:
        labels.append(logistics.get('courier_name') or 'Lalamove')
    if 'other' in modes:
        labels.append(logistics.get('other_transport') or 'Transportasi lainnya')
    return ' + '.join(labels)


def local_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=WIB) if result.tzinfo is None else result.astimezone(WIB)


def can_manage_logistics(user: dict, event: dict) -> bool:
    permissions = set(user["permissions"])
    return "events.assign" in permissions and (not event.get("coordinator_id") or
        event["coordinator_id"] == user["id"] or bool(permissions & {"users.manage", "kpi.evaluate_operations"}))


def fleet_rows(conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM vehicles ORDER BY active DESC,name,plate_number")]


def resolve_vehicle_text(conn, value, current_vehicle_id: int | None = None) -> int | None:
    """Accept a typed name/plate and reuse the fleet identity for conflict checks.

    Called inside the logistics write transaction: an invalid form never leaves
    behind a newly created vehicle. Existing ID-based clients remain supported.
    """
    if not isinstance(value,str) or len(value)>140:
        raise ValueError("Isian kendaraan tidak valid atau terlalu panjang.")
    text=" ".join(value.split())
    if not text:
        return None
    key=text.casefold()
    compact=re.sub(r"\s+","",text).casefold()
    matches=[]
    for row in conn.execute("SELECT id,name,plate_number,active FROM vehicles ORDER BY id"):
        name=" ".join(row["name"].split()).casefold()
        label=" ".join(f'{row["name"]} · {row["plate_number"]}'.split()).casefold()
        plate=re.sub(r"\s+","",row["plate_number"]).casefold()
        if key==name or (plate and (key==label or compact==plate)):
            matches.append(dict(row))
    current=next((v for v in matches if v["id"]==current_vehicle_id),None)
    if current:
        return current["id"]
    active=[v for v in matches if v["active"]]
    if len(active)>1:
        raise ValueError("Ada beberapa kendaraan dengan nama yang sama. Ketik nomor polisi untuk menentukan unitnya.")
    if active:
        return active[0]["id"]
    if matches:
        raise ValueError("Kendaraan ini nonaktif. Gunakan kendaraan lain atau aktifkan kembali pada daftar kendaraan.")
    if len(text)>100:
        raise ValueError("Nama kendaraan baru maksimal 100 karakter.")
    return conn.execute("INSERT INTO vehicles(name) VALUES(?)",(text,)).lastrowid


def logistics_detail(conn, event_id: int) -> dict:
    row = conn.execute("""SELECT o.*,v.name vehicle_name,v.plate_number,v.ownership,v.vendor_name
        FROM event_operations o LEFT JOIN vehicles v ON v.id=o.vehicle_id WHERE o.event_id=?""", (event_id,)).fetchone()
    result = dict(row) if row else {"event_id":event_id}
    result['transport_modes'] = transport_modes(result.get('transport_modes'), result.get('vehicle_id'))
    result['transport_label'] = transport_label(result)
    a,b = result.get("ribbon_start"),result.get("ribbon_end")
    result["ribbon_used"] = a-b if a is not None and b is not None else None
    return result


def transport_window(event: dict, logistics: dict) -> tuple[datetime, datetime]:
    start,end = local_datetime(event["starts_at"]),local_datetime(event["ends_at"])
    if logistics.get("loading_date"):
        start = min(start,local_datetime(f'{logistics["loading_date"]}T{logistics.get("loading_time") or "00:00"}'))
    if logistics.get("vehicle_return_at"):
        end = max(end,local_datetime(logistics["vehicle_return_at"]))
    return start,end


def vehicle_conflicts(conn, event: dict, logistics: dict) -> list[dict]:
    if not logistics.get("vehicle_id") or event["status"] == "cancelled":
        return []
    start,end = transport_window(event,logistics)
    conflicts = []
    for row in conn.execute("""SELECT e.*,o.loading_date,o.loading_time,o.vehicle_return_at
        FROM events e JOIN event_operations o ON o.event_id=e.id
        WHERE o.vehicle_id=? AND e.id!=? AND e.status!='cancelled'""", (logistics["vehicle_id"],event["id"])):
        other = dict(row)
        other_start,other_end = transport_window(other,other)
        if start < other_end and other_start < end:
            conflicts.append({"id":other["id"],"title":other["title"],"starts_at":other["starts_at"]})
    return conflicts


def validate_logistics(conn, event: dict, payload: dict) -> dict:
    old = logistics_detail(conn,event["id"])
    if 'ribbon_start' in payload or 'ribbon_end' in payload:
        raise ValueError('Pemakaian bahan diisi pada tab Penutupan. Muat ulang halaman untuk memakai form terbaru.')
    result = {}
    for field,limit in TEXT_FIELDS.items():
        value = payload.get(field,old.get(field,""))
        if not isinstance(value,str) or len(value.strip()) > limit:
            raise ValueError(f"Isian {field} tidak valid atau terlalu panjang (maksimal {limit} karakter).")
        result[field] = value.strip()
    modes = payload.get('transport_modes', old['transport_modes'])
    if not isinstance(modes, list) or any(not isinstance(m, str) or m not in TRANSPORT_MODES for m in modes) or len(set(modes)) != len(modes):
        raise ValueError('Pilihan transportasi tidak valid.')
    modes = list(modes)
    if 'transport_modes' not in payload and ('vehicle_name' in payload or 'vehicle_id' in payload):
        value = payload.get('vehicle_name') if 'vehicle_name' in payload else payload.get('vehicle_id')
        if value is not None and str(value).strip():
            if 'fleet' not in modes: modes.append('fleet')
        elif 'fleet' in modes:
            modes.remove('fleet')
    result['transport_modes'] = json.dumps([m for m in TRANSPORT_MODES if m in modes])
    raw = ((resolve_vehicle_text(conn,payload["vehicle_name"],old.get("vehicle_id"))
            if "vehicle_name" in payload else payload.get("vehicle_id",old.get("vehicle_id"))) if 'fleet' in modes else None)
    if raw in (None,""):
        result["vehicle_id"] = None
    elif isinstance(raw,bool) or not str(raw).isdigit():
        raise ValueError("Pilih kendaraan yang tersedia.")
    else:
        result["vehicle_id"] = int(raw)
        vehicle = conn.execute("SELECT active FROM vehicles WHERE id=?",(int(raw),)).fetchone()
        if not vehicle or (not vehicle["active"] and old.get("vehicle_id") != int(raw)):
            raise ValueError("Kendaraan tidak tersedia atau sudah nonaktif.")
    if 'fleet' in modes and result['vehicle_id'] is None:
        raise ValueError('Ketik nama kendaraan operasional, atau lepas pilihan kendaraan operasional.')
    if 'fleet' not in modes:
        result['driver_name'] = result['vehicle_return_at'] = ''
    if 'courier' in modes:
        result['courier_name'] = result['courier_name'] or 'Lalamove'
    else:
        result['courier_name'] = result['courier_note'] = ''
    if 'other' in modes and not result['other_transport']:
        raise ValueError('Isi keterangan transportasi lainnya.')
    if 'other' not in modes:
        result['other_transport'] = ''
    if bool(result["loading_date"]) != bool(result["loading_time"]):
        raise ValueError("Isi tanggal dan waktu loading sekaligus.")
    if result["loading_date"]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",result["loading_date"]) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",result["loading_time"]):
            raise ValueError("Tanggal atau waktu loading tidak valid.")
        try:
            loading = local_datetime(f'{result["loading_date"]}T{result["loading_time"]}')
        except ValueError as exc:
            raise ValueError("Tanggal loading tidak valid.") from exc
        if loading > local_datetime(event["starts_at"]):
            raise ValueError("Loading tidak boleh melewati waktu mulai event.")
    if result["vehicle_return_at"]:
        try:
            returned = local_datetime(result["vehicle_return_at"])
        except ValueError as exc:
            raise ValueError("Waktu kendaraan kembali tidak valid.") from exc
        if returned < local_datetime(event["ends_at"]):
            raise ValueError("Kendaraan kembali tidak boleh sebelum event selesai.")
        result["vehicle_return_at"] = returned.isoformat(timespec="minutes")
    conflicts = vehicle_conflicts(conn,event,result)
    if conflicts:
        raise ValueError("Jadwal kendaraan bentrok dengan: " + ", ".join(row["title"] for row in conflicts) + ". Pilih kendaraan atau jadwal loading lain.")
    return result


def export_event_rows(conn, user_id: int, start: str, end_exclusive: str) -> list[list]:
    # Date filtering uses WIB, including events supplied by Calendar in UTC.
    rows = conn.execute("SELECT * FROM events WHERE coordinator_id=? ORDER BY julianday(starts_at),id",(user_id,))
    data = []
    for row in rows:
        e = dict(row)
        begins,ends = local_datetime(e["starts_at"]),local_datetime(e["ends_at"])
        if not start <= begins.date().isoformat() < end_exclusive:
            continue
        o = logistics_detail(conn,e["id"])
        closing = approved_export(conn,e['id'])
        if closing:
            o['ribbon_start'], o['ribbon_end'], o['ribbon_used'], closing_note = closing
            o['notes'] = '\n'.join(filter(None, [o.get('notes', ''), closing_note]))
        transport_notes = '\n'.join(filter(None, [o.get('courier_note'), o.get('transport_note')]))
        if transport_notes:
            o['notes'] = '\n'.join(filter(None, [o.get('notes'), 'Transportasi: ' + transport_notes]))
        assignments = [dict(a) for a in conn.execute("""SELECT a.id,a.assignment_type,u.full_name,
            EXISTS(SELECT 1 FROM event_performance_reviews r WHERE r.assignment_id=a.id) reviewed
            FROM event_assignments a JOIN users u ON u.id=a.user_id WHERE a.event_id=?
            ORDER BY CASE a.assignment_type WHEN 'pic' THEN 0 ELSE 1 END,u.full_name,a.id""",(e["id"],))]
        status = e["status"].upper()
        if e["status"] == "completed" and any(not a["reviewed"] for a in assignments):
            status = "PENDING_EVALUATION"
        data.append([e["title"],o.get("client_name",""),o.get("client_phone",""),o.get("service_type",""),
            o.get("equipment_setup",""),o.get("sales_name",""),
            ", ".join(f'{a["full_name"]} ({"PIC" if a["assignment_type"]=="pic" else "CREW"})' for a in assignments),
            begins.date(),begins.strftime("%H:%M"),ends.strftime("%H:%M"),
            date.fromisoformat(o["loading_date"]) if o.get("loading_date") else None,o.get("loading_time",""),
            e["location"],status,transport_label(o),o.get("driver_name",""),o.get("vendor_name") or "",
            o.get("ribbon_start"),o.get("ribbon_end"),o.get("ribbon_used"),o.get("notes","")])
    return data


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value,date):
        return value.isoformat()
    if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@","\t","\r")):
        return "'"+value
    return value


def make_event_list_xlsx(data: list[list]) -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    q = lambda name: f"{{{ns}}}{name}"
    template = Path(__file__).parent / "templates" / "event-list.xlsx"
    with zipfile.ZipFile(template) as source:
        root = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
        sheet_data = root.find(q("sheetData"))
        style_row = sheet_data.find(f'{q("row")}[@r="2"]')
        styles = {re.sub(r"\d", "",c.get("r")):c.get("s","0") for c in style_row} if style_row is not None else {}
        for row in list(sheet_data)[1:]:
            sheet_data.remove(row)
        for index,values in enumerate(data,2):
            # Expand tall rows when the reference's narrow wrapped columns need it.
            widths = [35,13,14,14,17,12,25,12,12,12,14,14,12,20,14,13,13,13,14,18,12]
            lines = max([1]+[sum(max(1,math.ceil(len(part)/max(5,width-2))) for part in str(value or "").split("\n")) for value,width in zip(values,widths)])
            row = ET.SubElement(sheet_data,q("row"),{"r":str(index),"ht":str(min(409, max(48,lines*15))),"customHeight":"1"})
            for col,value in enumerate(values):
                letter = chr(65+col)
                cell = ET.SubElement(row,q("c"),{"r":f"{letter}{index}","s":styles.get(letter,"0")})
                if value is None or value == "":
                    continue
                if isinstance(value,date):
                    ET.SubElement(cell,q("v")).text = str((value-date(1899,12,30)).days)
                elif isinstance(value,(int,float)):
                    if col == 19:
                        ET.SubElement(cell,q("f")).text = f"R{index}-S{index}"
                    ET.SubElement(cell,q("v")).text = str(value)
                else:
                    cell.set("t","inlineStr")
                    text = ET.SubElement(ET.SubElement(cell,q("is")),q("t"),{"{http://www.w3.org/XML/1998/namespace}space":"preserve"})
                    text.text = "".join(ch for ch in str(value) if ch in "\t\n\r" or 32<=ord(ch)<=0xD7FF or 0xE000<=ord(ch)<=0xFFFD or 0x10000<=ord(ch)<=0x10FFFF)
        last = max(1,len(data)+1)
        dimension = root.find(q("dimension"))
        if dimension is not None:
            dimension.set("ref",f"A1:U{last}")
        auto_filter = root.find(q("autoFilter"))
        if auto_filter is None:
            auto_filter = ET.Element(q("autoFilter"))
            root.insert(list(root).index(sheet_data)+1,auto_filter)
        auto_filter.set("ref",f"A1:U{last}")
        output = io.BytesIO()
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                target.writestr(name,ET.tostring(root,encoding="utf-8",xml_declaration=True) if name=="xl/worksheets/sheet1.xml" else source.read(name))
    return output.getvalue()


def notifications_payload(conn, user: dict, events: list[dict]) -> dict:
    from staffing import event_staff_conflicts
    permissions = set(user["permissions"])
    if not permissions & {"events.read_all","events.read_own"}:
        return {"items":[],"unread_count":0}
    now = datetime.now(WIB)
    reads = {r["notification_key"] for r in conn.execute("SELECT notification_key FROM notification_reads WHERE user_id=?",(user["id"],))}
    mine = {r["event_id"]:dict(r) for r in conn.execute("SELECT event_id,id,assignment_type FROM event_assignments WHERE user_id=?",(user["id"],))}
    items = []
    def add(e,kind,title,body,tab="overview",tone="gold",revision=""):
        signature = hashlib.sha256(str(revision).encode()).hexdigest()[:16]
        key = f'event:{e["id"]}:{kind}:{signature}'
        items.append({"key":key,"title":title,"body":body,"event_id":e["id"],"event_title":e["title"],
            "tab":tab,"tone":tone,"event_at":e["starts_at"],"read":key in reads})
    for e in events:
        begins,ends = local_datetime(e["starts_at"]),local_datetime(e["ends_at"])
        closing = conn.execute('SELECT status,version,submitted_by FROM event_closing_reports WHERE event_id=?', (e['id'],)).fetchone()
        is_pic = bool(conn.execute("SELECT 1 FROM event_assignments WHERE event_id=? AND user_id=? AND assignment_type='pic'", (e['id'], user['id'])).fetchone())
        # Unresolved reports remain actionable even for events older than 30 days.
        if e['status'] == 'scheduled':
            if closing and closing['status'] == 'submitted' and can_review_closing(user, e):
                add(e, 'closing-review', 'Laporan penutupan menunggu review', 'Periksa laporan PIC sebelum Clear & Evaluasi.', 'closing', revision=closing['version'])
            if is_pic and closing and closing['status'] == 'revision':
                add(e, 'closing-revision', 'Revisi laporan penutupan', 'Coordinator meminta perbaikan. Buka catatan review.', 'closing', 'red', closing['version'])
            elif is_pic and ends < now and (not closing or closing['status'] == 'draft'):
                add(e, 'closing-due', 'Lengkapi laporan penutupan', 'Simpan hasil event dan kirim kepada coordinator.', 'closing')
            if closing and closing['status'] == 'accepted' and can_review_closing(user, e):
                add(e, 'closing-clear', 'Laporan diterima · siap clear', 'Lanjutkan Clear & Evaluasi crew.', 'closing', 'blue', closing['version'])
        if ends < now-timedelta(days=30) or begins > now+timedelta(days=30):
            continue
        own = mine.get(e["id"])
        manages = can_manage_logistics(user,e)
        if e["status"] == "cancelled":
            if own or manages:
                add(e,"cancelled","Event dibatalkan","Periksa kembali jadwal tim.",tone="red",revision=e["starts_at"])
            continue
        if own and e["status"] == "scheduled":
            add(e,"assignment","Penugasan event","Anda ditugaskan sebagai "+("PIC." if own["assignment_type"]=="pic" else "Crew."),"team","blue",f'{own["id"]}:{e["starts_at"]}:{e["ends_at"]}')
            if begins.date() == (now+timedelta(days=1)).date():
                add(e,'event-tomorrow','Pengingat event besok','Periksa jam, lokasi, PIC, dan transportasi terbaru sebelum berangkat.',
                    'overview','blue',f'{e["starts_at"]}:{e["ends_at"]}:{e["location"]}')
        if e['status'] == 'scheduled' and ends >= now and (manages or own):
            staff_conflicts = event_staff_conflicts(conn, e, user)
            if staff_conflicts:
                overlaps = any(p['severity'] == 'red' for p in staff_conflicts)
                add(e, 'staff-conflict', 'Jam event Crew/PIC beririsan' if overlaps else 'Crew/PIC memiliki event pada hari yang sama',
                    'Tinjau pembagian tugas dengan coordinator. Penugasan tetap dapat dilanjutkan setelah konfirmasi.',
                    'team', 'red' if overlaps else 'gold', repr(staff_conflicts))
        if e["status"] == "scheduled" and ends>=now and begins<=now+timedelta(days=7):
            if manages:
                if not e["pic_name"] or not e["crew_count"]:
                    add(e,"team","Lengkapi tim event","PIC atau crew belum ditugaskan.","team",revision=f'{e["pic_name"]}:{e["crew_count"]}')
                modes = transport_modes(e.get('transport_modes'), e.get('vehicle_id'))
                if not modes or ('fleet' in modes and not e.get('vehicle_id')):
                    add(e,"transport","Atur transportasi event","Pilih kendaraan operasional, motor tim, atau layanan kurir.","logistics",revision=e.get('logistics_updated_at'))
            if "warehouse.update" in permissions and e["warehouse_status"] in {"needs_prep","preparing","issue"}:
                add(e,"warehouse","Kesiapan alat perlu ditindaklanjuti","Periksa checklist dan kendala alat.","warehouse",revision=e["warehouse_status"])
            if ("design.update" in permissions and e["design_status"] not in {"approved","ready","done","completed"}
                    and ("design.read_all" in permissions or e.get("design_assignee_id") == user["id"])):
                add(e,"design","Desain event belum selesai","Periksa brief dan tahap desain.","design",revision=e["design_status"])
        if "advances.approve" in permissions and e["advance_status"] == "submitted":
            add(e,"advance-review","Pengajuan uang jalan","Pengajuan menunggu persetujuan.","advance",revision=e.get("advance_updated_at"))
        if "advances.transfer" in permissions and e["advance_status"] == "approved":
            add(e,"advance-transfer","Uang jalan siap ditransfer","Catat transfer setelah pembayaran selesai.","advance",revision=e.get("advance_updated_at"))
        if (manages or own and own["assignment_type"]=="pic") and e["advance_status"] in {"rejected","transferred"}:
            add(e,"advance-status","Status uang jalan diperbarui","Pengajuan ditolak." if e["advance_status"]=="rejected" else "Transfer telah dicatat.","advance","blue",f'{e["advance_status"]}:{e.get("advance_updated_at")}')
        if manages and "kpi.evaluate_crew" in permissions and e["status"] == "completed":
            pending = conn.execute("""SELECT COUNT(*) FROM event_assignments a WHERE a.event_id=?
                AND NOT EXISTS(SELECT 1 FROM event_performance_reviews r WHERE r.assignment_id=a.id)""",(e["id"],)).fetchone()[0]
            if pending:
                add(e,"evaluation","Evaluasi crew belum lengkap",f"{pending} anggota tim menunggu penilaian.","performance")
        if manages and e.get("vehicle_id"):
            o = logistics_detail(conn,e["id"])
            conflicts = vehicle_conflicts(conn,e,o)
            if conflicts:
                add(e,"vehicle-conflict","Jadwal kendaraan bentrok","Jadwal berubah. Periksa kembali mapping kendaraan.","logistics","red",repr(conflicts))
    items.sort(key=lambda item:(item["read"],item["tone"]!="red",item["event_at"],item["key"]))
    return {"items":items,"unread_count":sum(not i["read"] for i in items)}
