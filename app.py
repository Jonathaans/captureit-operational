
from __future__ import annotations

import json
import io
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import cv2
import numpy as np
import qrcode
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE / "templates"
DATA_DIR = BASE / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
OUTPUTS_DIR = DATA_DIR / "outputs"
PHOTOS_DIR = OUTPUTS_DIR / "photos"
VIDEOS_DIR = OUTPUTS_DIR / "videos"
CUSTOM_QR_PATH = DATA_DIR / "custom_qr.png"
PRINT_LOG_PATH = DATA_DIR / "print_jobs.log"
PRINT_SCRIPT_PATH = BASE / "scripts" / "print_photo.ps1"
SETTINGS_PATH = BASE / "settings.json"

for p in (TEMPLATES_DIR, SESSIONS_DIR, OUTPUTS_DIR, PHOTOS_DIR, VIDEOS_DIR):
    p.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")

_state_lock = threading.RLock()
_print_lock = threading.Lock()
_current_process: subprocess.Popen | None = None
_cancel_requested = False
_browser_session: dict[str, Any] | None = None
_session_state: dict[str, Any] = {
    "phase": "idle",
    "message": "Ready",
    "templateId": None,
    "sessionId": None,
    "take": 0,
    "takeCount": 0,
    "phaseStartedAt": None,
    "phaseDuration": 0,
    "outputVideo": None,
    "outputPhoto": None,
    "desktopSync": None,
    "captureStats": None,
    "cameraTransform": None,
    "cameraBackendRotationOffset": None,
    "sessionPreviewTransform": None,
    "error": None,
}

ACTIVE_SESSION_PHASES = {
    "starting", "warmup", "prepare", "recording", "capture", "between", "extracting", "rendering"
}

DEFAULT_CAPTURE_FPS = 30
SESSION_PREVIEW_FPS = 20
PRINT_FIT_MODES = {"full4r", "strip2up", "strip1left"}

VIDEO_QUALITY_PROFILES = {
    "standard": {"scale": 1.0, "preset": "fast", "crf": 20},
    "hd": {"scale": 1.5, "preset": "medium", "crf": 17},
    "hd_plus": {"scale": 2.0, "preset": "slow", "crf": 15},
}

# The same FFmpeg process that records the session also publishes small JPEG
# frames here. This keeps the live preview active without opening the camera a
# second time (EOS Webcam Utility commonly allows only one reader).
_preview_condition = threading.Condition()
_preview_frame: bytes | None = None
_preview_sequence = 0
_preview_running = False


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    return load_json(SETTINGS_PATH)


def save_settings(data: dict) -> None:
    save_json(SETTINGS_PATH, data)


def normalize_qr_settings(value: Any) -> dict:
    cfg = value if isinstance(value, dict) else {}
    mode = str(cfg.get("mode") or "output").strip().lower()
    if mode not in {"output", "fixed", "custom"}:
        mode = "output"
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "mode": mode,
        "publicBaseUrl": str(cfg.get("publicBaseUrl") or "").strip().rstrip("/"),
        "fixedUrl": str(cfg.get("fixedUrl") or "").strip(),
        "label": str(cfg.get("label") or "Scan untuk mengambil foto").strip()[:80],
    }


def normalize_printer_settings(value: Any) -> dict:
    cfg = value if isinstance(value, dict) else {}
    fit_mode = str(cfg.get("fitMode") or "strip1left").strip().lower()
    # A normal 4x6 print, double strip, and single left strip now follow an
    # explicit operator choice. This avoids accidental zoom/fill on DNP media.
    if fit_mode in {"auto", "cover", "contain"}:
        fit_mode = "full4r"
    if fit_mode not in PRINT_FIT_MODES:
        fit_mode = "full4r"
    try:
        copies = max(1, min(10, int(cfg.get("copies", 1))))
    except (TypeError, ValueError):
        copies = 1
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "name": str(cfg.get("name") or "").strip(),
        "copies": copies,
        "fitMode": fit_mode,
        "modeVersion": 4,
    }


def normalize_kiosk_settings(value: Any) -> dict:
    cfg = value if isinstance(value, dict) else {}
    try:
        timeout = max(15, min(300, int(cfg.get("resultTimeoutSeconds", 60))))
    except (TypeError, ValueError):
        timeout = 60
    return {"resultTimeoutSeconds": timeout}


def normalize_desktop_sync_settings(value: Any) -> dict:
    cfg = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "photoFolder": str(cfg.get("photoFolder") or "").strip(),
        "videoFolder": str(cfg.get("videoFolder") or "").strip(),
        "syncVideos": bool(cfg.get("syncVideos", True)),
    }


def output_asset_path(value: str, *, extensions: set[str], must_exist: bool = True) -> Path:
    raw = str(value or "").split("?", 1)[0].replace("\\", "/").strip()
    if raw.startswith("/outputs/"):
        raw = raw[len("/outputs/"):]
    raw = raw.lstrip("/")
    if not raw or Path(raw).suffix.lower() not in extensions:
        raise ValueError("File hasil tidak valid.")
    path = (OUTPUTS_DIR / raw).resolve()
    if not path.is_relative_to(OUTPUTS_DIR.resolve()):
        raise ValueError("Lokasi file hasil tidak valid.")
    if "/" not in raw:
        preferred = PHOTOS_DIR / raw if Path(raw).suffix.lower() in {".jpg", ".jpeg", ".png"} else VIDEOS_DIR / raw
        if preferred.is_file():
            path = preferred.resolve()
    if must_exist and not path.is_file():
        raise FileNotFoundError("File hasil tidak ditemukan.")
    return path


def output_photo_path(value: str, *, must_exist: bool = True) -> Path:
    path = output_asset_path(value, extensions={".jpg", ".jpeg", ".png"}, must_exist=must_exist)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("File foto hasil tidak valid.")
    return path


def output_relative_name(path: Path) -> str:
    return path.resolve().relative_to(OUTPUTS_DIR.resolve()).as_posix()


def guess_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        value = str(sock.getsockname()[0])
        if value and not value.startswith("127."):
            return value
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for value in socket.gethostbyname_ex(socket.gethostname())[2]:
            if value and not value.startswith("127."):
                return value
    except OSError:
        pass
    return "127.0.0.1"


def qr_target_url(photo_name: str) -> str:
    settings = load_settings()
    cfg = normalize_qr_settings(settings.get("qr"))
    if cfg["mode"] == "fixed":
        return cfg["fixedUrl"]

    base_url = cfg["publicBaseUrl"]
    if not base_url:
        base_url = request.url_root.rstrip("/")
        host = str(settings.get("server", {}).get("host") or "127.0.0.1")
        if host in {"0.0.0.0", "::"} and request.host.split(":", 1)[0] in {"127.0.0.1", "localhost"}:
            port = int(settings.get("server", {}).get("port", 5050))
            base_url = f"http://{guess_lan_ip()}:{port}"
    return f"{base_url}/outputs/{quote(photo_name, safe='/')}"


def append_print_log(message: str) -> None:
    try:
        PRINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with PRINT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {message}\n")
    except Exception:
        pass


def powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _powershell_printer_names() -> list[str]:
    executable = powershell_executable()
    if not executable:
        return []
    command = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Printer | ForEach-Object {$_.Name}) "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        payload = json.loads(completed.stdout.strip() or "[]")
        if isinstance(payload, str):
            payload = [payload]
        return [str(name).strip() for name in payload if str(name).strip()]
    except Exception as exc:
        append_print_log(f"Printer fallback discovery gagal: {exc}")
        return []


def installed_windows_printers() -> list[str]:
    if os.name != "nt":
        return []
    names: set[str] = set()
    try:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        for item in win32print.EnumPrinters(flags, None, 1):
            if len(item) > 2 and item[2]:
                names.add(str(item[2]).strip())
        try:
            default = str(win32print.GetDefaultPrinter() or "").strip()
            if default:
                names.add(default)
        except Exception:
            pass
    except Exception as exc:
        append_print_log(f"EnumPrinters gagal: {exc}")

    if not names:
        names.update(_powershell_printer_names())
    return sorted((name for name in names if name), key=str.lower)


def default_windows_printer() -> str:
    if os.name != "nt":
        return ""
    try:
        import win32print

        return str(win32print.GetDefaultPrinter() or "").strip()
    except Exception:
        return ""


def _normalized_printer_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def resolve_windows_printer(requested: str = "") -> tuple[str, list[str]]:
    available = installed_windows_printers()
    if not available:
        raise RuntimeError(
            "Windows tidak mengembalikan daftar printer. Pastikan driver printer terpasang, "
            "printer berstatus siap, lalu restart RecordCountdown."
        )

    requested_key = _normalized_printer_name(requested)
    if requested_key:
        for name in available:
            if _normalized_printer_name(name) == requested_key:
                return name, available
        fuzzy = [
            name for name in available
            if requested_key in _normalized_printer_name(name)
            or _normalized_printer_name(name) in requested_key
        ]
        if len(fuzzy) == 1:
            return fuzzy[0], available

    # Prefer a DNP queue when it is present, otherwise use the Windows default
    # or the explicitly selected queue. Epson and other Windows printers are
    # handled by the same PrintDocument/PyWin32 path below.
    dnp_tokens = ("dnp", "dsrx", "rx1", "ds620", "ds820", "qw410")
    dnp = [name for name in available if any(token in _normalized_printer_name(name) for token in dnp_tokens)]
    if len(dnp) == 1:
        return dnp[0], available

    default = default_windows_printer()
    for name in available:
        if default and name.casefold() == default.casefold():
            return name, available
    if len(available) == 1:
        return available[0], available
    if requested:
        raise RuntimeError(
            f"Printer '{requested}' tidak ditemukan. Terdeteksi: {', '.join(available)}"
        )
    raise RuntimeError("Pilih printer foto di Settings. Printer terdeteksi: " + ", ".join(available))


def print_photo_powershell(photo_path: Path, printer_name: str, copies: int, fit_mode: str) -> dict:
    executable = powershell_executable()
    if not executable:
        raise RuntimeError("Windows PowerShell tidak ditemukan.")
    if not PRINT_SCRIPT_PATH.is_file():
        raise RuntimeError(f"Script print tidak ditemukan: {PRINT_SCRIPT_PATH}")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(PRINT_SCRIPT_PATH),
            "-ImagePath", str(photo_path),
            "-PrinterName", printer_name,
            "-Copies", str(copies),
            "-FitMode", fit_mode,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "PrintDocument gagal.").strip()
        raise RuntimeError(detail)
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError((completed.stdout or "PowerShell tidak mengembalikan status print.").strip())
    return {
        "method": "Windows PrintDocument",
        "jobs": [],
        "details": payload,
    }


def _print_ratio(width: int, height: int) -> float:
    short = max(1, min(int(width), int(height)))
    return max(int(width), int(height)) / short


def resolve_print_fit_mode(image: Image.Image, size: tuple[int, int], fit_mode: str) -> str:
    """Resolve legacy values while keeping the operator's format explicit."""
    del image, size
    mode = str(fit_mode or "full4r").strip().lower()
    if mode in {"auto", "cover", "contain"}:
        return "full4r"
    return mode if mode in PRINT_FIT_MODES else "strip1left"


def prepare_print_image(
    source: Image.Image,
    size: tuple[int, int],
    fit_mode: str,
    dpi: tuple[float, float] = (300.0, 300.0),
) -> tuple[Image.Image, str]:
    """Build a page-sized RGB bitmap so the printer receives exact pixels."""
    width, height = (int(size[0]), int(size[1]))
    if width <= 0 or height <= 0:
        raise RuntimeError("Ukuran media printer tidak valid.")
    original = ImageOps.exif_transpose(source).convert("RGB")
    resolved = resolve_print_fit_mode(original, (width, height), fit_mode)

    if resolved == "strip1left":
        if _print_ratio(original.width, original.height) < 2.25:
            raise ValueError("Mode 1 strip membutuhkan template 2x6, misalnya 600x1800.")
        # Compose one physical 4x6 sheet first. Only the left 2x6 region is
        # filled; rotate/scale the complete sheet to the driver's coordinates.
        sheet = Image.new("RGB", (1200, 1800), "white")
        strip = original if original.height >= original.width else original.transpose(Image.Transpose.ROTATE_90)
        fitted = ImageOps.contain(strip, (600, 1800), Image.Resampling.LANCZOS)
        sheet.paste(fitted, (0, 0))
        if width / max(1.0, dpi[0]) > height / max(1.0, dpi[1]):
            sheet = sheet.transpose(Image.Transpose.ROTATE_270)
        return sheet.resize((width, height), Image.Resampling.LANCZOS), resolved

    if resolved == "strip2up":
        if _print_ratio(original.width, original.height) < 2.25:
            raise RuntimeError(
                "Mode Strip membutuhkan template rasio 2x6, misalnya 600x1800. "
                "Untuk hasil 4x6 gunakan mode Full 4R."
            )
        page = Image.new("RGB", (width, height), "white")
        if height >= width:
            # Portrait 4x6 page: two portrait 2x6 strips side by side.
            first_width = width // 2
            regions = (
                ((first_width, height), (0, 0)),
                ((width - first_width, height), (first_width, 0)),
            )
            target_is_portrait = True
        else:
            # Landscape 6x4 page: two landscape 6x2 strips top and bottom.
            first_height = height // 2
            regions = (
                ((width, first_height), (0, 0)),
                ((width, height - first_height), (0, first_height)),
            )
            target_is_portrait = False

        strip = original
        source_is_portrait = strip.height >= strip.width
        if source_is_portrait != target_is_portrait:
            strip = strip.transpose(Image.Transpose.ROTATE_90)
        for region_size, offset in regions:
            # The RX1 printable pixel ratio includes a small overprint area.
            # Resize the complete strip into that area: no crop and no white.
            fitted = strip.resize(region_size, Image.Resampling.LANCZOS)
            page.paste(fitted, offset)
        return page, resolved

    if _print_ratio(original.width, original.height) >= 2.25:
        raise RuntimeError(
            "Hasil saat ini berformat strip 2x6. Pilih mode Strip atau gunakan "
            "template 4x6 untuk mencetak Full 4R."
        )
    oriented = original
    if (oriented.height >= oriented.width) != (height >= width):
        oriented = oriented.transpose(Image.Transpose.ROTATE_90)
    # Full 4R keeps every source pixel. The tiny aspect difference between a
    # 3:2 file and the RX1 overprint area is absorbed by resize, not crop.
    page = oriented.resize((width, height), Image.Resampling.LANCZOS)
    return page, "full4r"


def print_photo_pywin32(photo_path: Path, printer_name: str, copies: int, fit_mode: str) -> dict:
    try:
        import win32con
        import win32ui
        from PIL import ImageWin
    except ImportError as exc:
        raise RuntimeError("pywin32 belum terpasang. Jalankan kembali 00_INSTALL.bat.") from exc

    jobs: list[int] = []
    resolved_mode = ""
    media_size = (0, 0)
    with Image.open(photo_path) as source:
        original = ImageOps.exif_transpose(source).convert("RGB")
        for copy_number in range(1, copies + 1):
            dc = win32ui.CreateDC()
            document_started = False
            prepared: Image.Image | None = None
            try:
                dc.CreatePrinterDC(printer_name)
                width = int(dc.GetDeviceCaps(win32con.HORZRES))
                height = int(dc.GetDeviceCaps(win32con.VERTRES))
                if width <= 0 or height <= 0:
                    raise RuntimeError("Ukuran media printer tidak dapat dibaca. Periksa Printing Preferences printer.")
                dpi = (float(dc.GetDeviceCaps(win32con.LOGPIXELSX)), float(dc.GetDeviceCaps(win32con.LOGPIXELSY)))
                if fit_mode == "strip1left":
                    short, long = sorted((width / max(1.0, dpi[0]), height / max(1.0, dpi[1])))
                    if abs(short - 4.0) > 0.35 or abs(long - 6.0) > 0.35:
                        raise ValueError("Mode 1 strip memerlukan kertas 4x6. Pilih Paper Size (6x4) di Printing Preferences.")
                prepared, resolved_mode = prepare_print_image(original, (width, height), fit_mode, dpi)
                media_size = (width, height)

                job_name = f"RecordCountdown {photo_path.stem} ({copy_number}/{copies})"
                start_result = dc.StartDoc(job_name)
                document_started = True
                dc.StartPage()
                ImageWin.Dib(prepared).draw(dc.GetHandleOutput(), (0, 0, width, height))
                dc.EndPage()
                dc.EndDoc()
                document_started = False
                # win32ui.CDC.StartDoc returns None on some driver versions
                # even though the job has been accepted successfully.
                if start_result is not None:
                    try:
                        jobs.append(int(start_result))
                    except (TypeError, ValueError):
                        pass
            except Exception:
                if document_started:
                    try:
                        dc.AbortDoc()
                    except Exception:
                        pass
                raise
            finally:
                if prepared is not None:
                    prepared.close()
                dc.DeleteDC()
    return {
        "method": "PyWin32 GDI pixel-perfect",
        "jobs": jobs,
        "details": {
            "requestedMode": fit_mode,
            "resolvedMode": resolved_mode,
            "printableWidthPx": media_size[0],
            "printableHeightPx": media_size[1],
        },
    }


def print_photo_windows(photo_path: Path, printer_name: str, copies: int, fit_mode: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("Print DNP hanya tersedia saat aplikasi dijalankan di Windows.")
    selected, available = resolve_windows_printer(printer_name)
    append_print_log(
        f"Mulai print file={photo_path.name!r} printer={selected!r} copies={copies} "
        f"fit={fit_mode} available={available!r}"
    )
    errors: list[str] = []
    # PyWin32 works directly in the driver's printable pixels and therefore
    # avoids the Display-unit scaling bug seen with PrintDocument on DS-RX1.
    for method in (print_photo_pywin32, print_photo_powershell):
        try:
            result = method(photo_path, selected, copies, fit_mode)
            result["printer"] = selected
            append_print_log(f"Print dikirim method={result['method']} printer={selected!r}")
            return result
        except Exception as exc:
            errors.append(f"{method.__name__}: {exc}")
            append_print_log(errors[-1])
    raise RuntimeError("Semua metode print gagal. " + " | ".join(errors))


def create_print_test_image(printer_name: str, fit_mode: str = "full4r") -> Path:
    path = DATA_DIR / "print_test.jpg"
    width, height = ((600, 1800) if fit_mode in {"strip2up", "strip1left"} else (1200, 1800))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, width - 35, height - 35), outline="#111827", width=18)
    draw.rectangle((70, 70, width - 70, 430), fill="#111827")
    try:
        title_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 82)
        body_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    draw.text((120, 145), "TEST PRINT", fill="white", font=title_font)
    text_x = min(120, max(35, width // 12))
    draw.text((text_x, 500), "RecordCountdown", fill="#111827", font=title_font)
    draw.text((text_x, 650), f"Printer: {printer_name}", fill="#111827", font=body_font)
    draw.text((text_x, 735), datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"), fill="#111827", font=body_font)
    colors = ("#22d3ee", "#818cf8", "#c084fc", "#f472b6")
    block_width = max(1, (width - (text_x * 2)) // len(colors))
    for index, color in enumerate(colors):
        x1 = text_x + index * block_width
        draw.rectangle((x1, 940, x1 + block_width, 1240), fill=color)
    label = {"strip1left": "1 STRIP - LEFT / CUT ON", "strip2up": "2 STRIPS - CUT ON"}.get(fit_mode, "FULL 4R - CUT OFF")
    draw.text((text_x, 1400), label, fill="#111827", font=body_font)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path


def template_dir(template_id: str) -> Path:
    return TEMPLATES_DIR / template_id


def load_template(template_id: str) -> dict:
    p = template_dir(template_id) / "template.json"
    if not p.exists():
        raise FileNotFoundError(f"Template not found: {template_id}")
    return load_json(p)


def list_templates() -> list[dict]:
    items = []
    for folder in sorted(TEMPLATES_DIR.iterdir() if TEMPLATES_DIR.exists() else []):
        p = folder / "template.json"
        if not p.exists():
            continue
        try:
            t = load_json(p)
            t["takeCount"] = max((int(s.get("sourceTake", 1)) for s in t.get("slots", [])), default=1)
            items.append(t)
        except Exception:
            pass
    return items


def ffmpeg_executable() -> str:
    value = str(load_settings().get("ffmpegPath") or "ffmpeg")
    if Path(value).is_absolute():
        if not Path(value).exists():
            raise RuntimeError(f"FFmpeg tidak ditemukan: {value}")
        return value
    resolved = shutil.which(value)
    if not resolved:
        raise RuntimeError(
            "FFmpeg tidak ditemukan di PATH. Pastikan `ffmpeg -version` bekerja dari Command Prompt."
        )
    return resolved


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-").lower()
    return value or f"template-{uuid.uuid4().hex[:8]}"


def video_quality_profile(video_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return a bounded render profile for the final composed MP4."""
    cfg = video_cfg if isinstance(video_cfg, dict) else {}
    quality = str(cfg.get("quality") or "hd").strip().lower()
    if quality not in VIDEO_QUALITY_PROFILES:
        quality = "hd"
    return {"quality": quality, **VIDEO_QUALITY_PROFILES[quality]}


def scaled_video_pixels(value: int, scale: float) -> int:
    """Keep scaled video dimensions even for yuv420p compatibility."""
    return max(2, int(round(float(value) * float(scale) / 2.0) * 2))


def video_render_geometry(template: dict) -> dict:
    cfg = template.get("video", {})
    cw, ch = int(template["canvas"]["width"]), int(template["canvas"]["height"])
    layout = cfg.get("outputLayout", "template")
    scale = min(1080 / cw, 1920 / ch) if layout == "portrait_9_16" else video_quality_profile(cfg)["scale"]
    width, height = scaled_video_pixels(cw, scale), scaled_video_pixels(ch, scale)
    return {
        "scale": scale, "contentWidth": width, "contentHeight": height,
        "width": 1080 if layout == "portrait_9_16" else width,
        "height": 1920 if layout == "portrait_9_16" else height,
    }


def update_state(**kwargs) -> None:
    global _session_state
    with _state_lock:
        _session_state.update(kwargs)


def get_state() -> dict:
    with _state_lock:
        state = dict(_session_state)
    if state.get("phaseStartedAt") and state.get("phaseDuration"):
        elapsed = max(0.0, time.time() - float(state["phaseStartedAt"]))
        state["remaining"] = max(0.0, float(state["phaseDuration"]) - elapsed)
    else:
        state["remaining"] = 0.0
    return state


def set_live_preview_running(running: bool, clear_frame: bool = False) -> None:
    global _preview_frame, _preview_sequence, _preview_running
    with _preview_condition:
        _preview_running = running
        if clear_frame:
            _preview_frame = None
        _preview_sequence += 1
        _preview_condition.notify_all()


def publish_live_preview(frame: bytes) -> None:
    global _preview_frame, _preview_sequence
    with _preview_condition:
        _preview_frame = frame
        _preview_sequence += 1
        _preview_condition.notify_all()


def run_process(args: list[str], log_path: Path | None = None) -> None:
    global _current_process
    stderr_target = subprocess.PIPE
    with _state_lock:
        if _cancel_requested:
            raise RuntimeError("Session dibatalkan.")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=stderr_target,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    with _state_lock:
        _current_process = proc

    _, stderr = proc.communicate()

    with _state_lock:
        _current_process = None
        cancelled = _cancel_requested

    if log_path:
        log_path.write_text(stderr or "", encoding="utf-8", errors="replace")

    if cancelled:
        raise RuntimeError("Session dibatalkan.")

    if proc.returncode != 0:
        tail = "\n".join((stderr or "").splitlines()[-15:])
        raise RuntimeError(f"FFmpeg gagal (exit {proc.returncode}).\n{tail}")


def detect_slots(image_path: Path) -> tuple[int, int, list[dict]]:
    """
    Transparent connected components become slot bounding boxes.
    The PNG remains the actual shape mask, so irregular holes also work:
    the slot rectangle only determines where video is placed behind the overlay.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError("Overlay tidak dapat dibaca.")
    if image.ndim != 3 or image.shape[2] < 4:
        raise RuntimeError("Overlay harus PNG dengan alpha/transparency.")

    h, w = image.shape[:2]
    alpha = image[:, :, 3]
    mask = (alpha <= 12).astype(np.uint8) * 255

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area = max(2500, int(w * h * 0.003))
    min_w = max(40, int(w * 0.04))
    min_h = max(40, int(h * 0.04))

    rects = []
    for i in range(1, count):
        x, y, rw, rh, area = [int(v) for v in stats[i]]
        if area < min_area or rw < min_w or rh < min_h:
            continue
        rects.append({
            "x": x, "y": y, "width": rw, "height": rh,
            "_cx": x + rw / 2, "_cy": y + rh / 2,
        })

    rects.sort(key=lambda r: (r["y"], r["x"]))

    # Auto-map same visual row to the same take.
    if rects:
        median_h = float(np.median([r["height"] for r in rects]))
        tolerance = max(12.0, median_h * 0.35)
        rows: list[list[dict]] = []
        for r in rects:
            matched = None
            for row in rows:
                row_cy = sum(v["_cy"] for v in row) / len(row)
                if abs(r["_cy"] - row_cy) <= tolerance:
                    matched = row
                    break
            if matched is None:
                rows.append([r])
            else:
                matched.append(r)

        rows.sort(key=lambda row: min(v["y"] for v in row))
        for take_no, row in enumerate(rows, 1):
            row.sort(key=lambda v: v["x"])
            for r in row:
                r["sourceTake"] = take_no
    else:
        rows = []

    slots = []
    for idx, r in enumerate(rects, 1):
        slots.append({
            "index": idx,
            "x": int(r["x"]),
            "y": int(r["y"]),
            "width": int(r["width"]),
            "height": int(r["height"]),
            "sourceTake": int(r.get("sourceTake", idx)),
        })
    return w, h, slots


def parse_directshow_video_devices(text: str) -> list[str]:
    """Extract DirectShow video names from FFmpeg's stderr output.

    FFmpeg prints this diagnostic on stderr and the exact prefix changed
    between builds.  Only the first quoted value on a video-device line is a
    display name; the following quoted value is often an ``Alternative name``
    and must not become a second camera option.
    """
    devices: list[str] = []
    seen: set[str] = set()
    in_video = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if "directshow video devices" in lower:
            in_video = True
            continue
        if "directshow audio devices" in lower:
            in_video = False
            continue
        if not in_video or "alternative name" in lower:
            continue
        quoted = re.findall(r'"([^"]+)"', line)
        if not quoted:
            continue
        name = quoted[0].strip()
        if name and name not in seen:
            seen.add(name)
            devices.append(name)
    return devices


def dshow_devices() -> list[str]:
    if os.name != "nt":
        return []
    ffmpeg = ffmpeg_executable()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return parse_directshow_video_devices(proc.stderr or "")



VALID_CAMERA_ROTATIONS = {0, 90, 180, 270}


def _setting_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_camera_transform(value: Any) -> dict[str, Any]:
    """Validate and normalize the transform stored by the browser UI."""
    cfg = value if isinstance(value, dict) else {}
    try:
        rotation = int(cfg.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Rotation camera harus 0, 90, 180, atau 270 derajat.") from exc
    if rotation not in VALID_CAMERA_ROTATIONS:
        raise ValueError("Rotation camera harus 0, 90, 180, atau 270 derajat.")
    return {
        "rotation": rotation,
        "mirrorHorizontal": _setting_bool(cfg.get("mirrorHorizontal", False)),
        "mirrorVertical": _setting_bool(cfg.get("mirrorVertical", False)),
    }


def default_camera_backend_rotation(settings: dict) -> int:
    """Default for the observed EOS browser/DirectShow orientation mismatch."""
    device = str(settings.get("cameraDevice") or "").strip().lower()
    return 180 if "eos webcam utility" in device else 0


def normalize_camera_backend_rotation(value: Any, settings: dict) -> int:
    if value is None or value == "":
        return default_camera_backend_rotation(settings)
    try:
        rotation = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Koreksi FFmpeg harus 0, 90, 180, atau 270 derajat.") from exc
    if rotation not in VALID_CAMERA_ROTATIONS:
        raise ValueError("Koreksi FFmpeg harus 0, 90, 180, atau 270 derajat.")
    return rotation


def effective_camera_transform(settings: dict) -> dict[str, Any]:
    """Transform for the DirectShow/FFmpeg source, including its base offset."""
    user = normalize_camera_transform(settings.get("cameraTransform"))
    offset = normalize_camera_backend_rotation(settings.get("cameraBackendRotationOffset"), settings)
    return {
        "rotation": (int(user["rotation"]) + offset) % 360,
        "mirrorHorizontal": bool(user["mirrorHorizontal"]),
        "mirrorVertical": bool(user["mirrorVertical"]),
    }


def camera_transform_filter(settings: dict) -> str:
    """
    Transform for the DirectShow source (capture test or take extraction).
    This includes the backend offset; browser getUserMedia gets only the user
    Camera Transform, while raw session MJPEG gets the full transform in CSS.

    Rotation values:
      0   = no rotation
      90  = clockwise
      180 = upside-down correction
      270 = counter-clockwise

    Mirrors are applied after rotation in output space.
    """
    cfg = effective_camera_transform(settings)
    rotation = int(cfg["rotation"])

    filters: list[str] = []

    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.extend(["hflip", "vflip"])
    elif rotation == 270:
        filters.append("transpose=2")

    if bool(cfg.get("mirrorHorizontal", False)):
        filters.append("hflip")
    if bool(cfg.get("mirrorVertical", False)):
        filters.append("vflip")

    return ",".join(filters)



def resolve_dshow_device(configured: str) -> str:
    """Match the configured camera against what DirectShow lists on THIS PC.

    The same camera can be listed slightly differently on another computer
    (e.g. "EOS Webcam Utility Pro", "EOS Webcam Utility (2)"), so an exact
    name is tried first, then a fuzzy match, then EOS Webcam Utility, then
    the first non-virtual camera.
    """
    configured = (configured or "").strip()
    try:
        devices = dshow_devices()
    except Exception:
        devices = []
    if not devices:
        return configured
    lowered = {d.lower(): d for d in devices}
    if configured.lower() in lowered:
        return lowered[configured.lower()]
    if configured:
        for d in devices:
            dl = d.lower()
            if configured.lower() in dl or dl in configured.lower():
                return d
    for d in devices:
        if "eos webcam" in d.lower():
            return d
    for d in devices:
        if not re.search(r"obs|virtual|snap camera|manycam|xsplit|droidcam|iriun|ndi", d, re.I):
            return d
    return devices[0]


def dshow_input_attempts(settings: dict) -> list[dict]:
    """Input option sets to try in order, from strict to most permissive.

    Webcams/virtual cameras often refuse the exact resolution/FPS combination
    on some PCs ("Could not set video options" / "Could not run graph").
    Each fallback removes one constraint until the driver accepts it.
    """
    resolution = str(settings.get("cameraResolution") or "").strip()
    try:
        fps = float(settings.get("cameraInputFps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    attempts: list[dict] = []

    def add(res: str, rate: float, rtbuf: str = "512M", vcodec: str = "") -> None:
        item = {"resolution": res, "fps": rate, "rtbufsize": rtbuf, "vcodec": vcodec}
        if item not in attempts:
            attempts.append(item)

    add(resolution, fps, "1024M")
    add(resolution, 0.0)
    add("", fps)
    add("", 0.0)
    add("", 0.0, "256M")
    add("1280x720", 0.0, "256M")
    return attempts


def dshow_input_args(device: str, opts: dict) -> list[str]:
    args = ["-f", "dshow", "-rtbufsize", opts.get("rtbufsize") or "512M"]
    if opts.get("resolution"):
        args += ["-video_size", str(opts["resolution"])]
    if float(opts.get("fps") or 0) > 0:
        args += ["-framerate", f"{float(opts['fps']):g}"]
    if opts.get("vcodec"):
        args += ["-vcodec", str(opts["vcodec"])]
    args += ["-thread_queue_size", "1024", "-i", f"video={device}"]
    return args


def record_take(output: Path, duration: float, settings_override: dict | None = None) -> None:
    settings = settings_override if settings_override is not None else load_settings()
    configured = str(settings.get("cameraDevice") or "").strip()
    if not configured:
        raise RuntimeError("Camera Device belum dipilih di Settings.")
    device = resolve_dshow_device(configured)

    ffmpeg = ffmpeg_executable()
    transform = camera_transform_filter(settings)
    last_error: Exception | None = None
    for opts in dshow_input_attempts(settings):
        args = [ffmpeg, "-hide_banner", "-y"] + dshow_input_args(device, opts)
        if transform:
            args += ["-vf", transform]
        args += [
            "-t", f"{duration:.3f}",
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output),
        ]
        try:
            run_process(args, output.with_suffix(".record.log"))
            if output.is_file() and output.stat().st_size > 1024:
                return
        except Exception as exc:  # try the next, more permissive option set
            last_error = exc
    raise last_error or RuntimeError(
        f"Kamera '{device}' tidak dapat dibuka oleh FFmpeg. Tutup aplikasi lain yang memakai kamera "
        "(Chrome, Zoom, OBS, EOS Utility) atau gunakan Sumber sesi = Browser."
    )


def apply_capture_progress_line(progress: dict, line: str) -> dict | None:
    """Update FFmpeg capture counters and return UI stats per progress block."""
    if line.startswith(("out_time_us=", "out_time_ms=")):
        try:
            sec = int(line.split("=", 1)[1]) / 1_000_000.0
            progress["seconds"] = max(float(progress.get("seconds", 0.0)), sec)
            if float(progress["seconds"]) >= 0.20:
                progress["ready"] = True
        except Exception:
            pass
    elif line.startswith("frame="):
        try:
            progress["frame"] = max(0, int(line.split("=", 1)[1]))
        except Exception:
            pass
    elif line.startswith("dup_frames="):
        try:
            progress["dupFrames"] = max(0, int(line.split("=", 1)[1]))
        except Exception:
            pass
    elif line.startswith("drop_frames="):
        try:
            progress["dropFrames"] = max(0, int(line.split("=", 1)[1]))
        except Exception:
            pass
    elif line.startswith("progress="):
        seconds = float(progress.get("seconds", 0.0))
        encoded = int(progress.get("frame", 0))
        duplicated = int(progress.get("dupFrames", 0))
        dropped = int(progress.get("dropFrames", 0))
        source_frames = max(0, encoded - duplicated + dropped)
        effective = source_frames / seconds if seconds > 0.25 else 0.0
        progress["effectiveSourceFps"] = effective
        return {
            "inputFpsRequested": float(progress.get("inputFpsRequested", 0.0)),
            "outputFps": int(progress.get("outputFps", DEFAULT_CAPTURE_FPS)),
            "previewFps": SESSION_PREVIEW_FPS,
            "encodedFrames": encoded,
            "duplicatedFrames": duplicated,
            "droppedFrames": dropped,
            "effectiveSourceFps": round(effective, 1),
            "duplicatePercent": round((duplicated / encoded * 100.0) if encoded else 0.0, 1),
        }
    return None



def _start_continuous_capture_once(
    output: Path,
    output_fps: int,
    settings: dict,
    device: str,
    opts: dict,
) -> tuple[subprocess.Popen, Any, dict]:
    """
    Open DirectShow only ONCE for the whole session.
    FFmpeg progress reports media timeline, so each countdown can be cut
    from the continuous recording with a common clock.
    """
    global _current_process

    input_fps = float(opts.get("fps") or 0)
    ffmpeg = ffmpeg_executable()
    args = [ffmpeg, "-hide_banner", "-y"] + dshow_input_args(device, opts)

    # Keep the continuous source and MJPEG session preview RAW. The browser
    # applies user rotation to getUserMedia, and user + backend correction to
    # FFmpeg MJPEG. The same corrected transform is applied once to extracted
    # takes, before snapshots and final compositions are generated.
    filter_complex = (
        "[0:v]setsar=1,split=2[record][preview];"
        f"[preview]fps={SESSION_PREVIEW_FPS},scale=960:-2[preview_out]"
    )

    # Progress uses stderr (pipe:2); stdout remains a clean MJPEG stream.
    args += [
        "-filter_complex", filter_complex,
        "-stats_period", "0.10",
        "-progress", "pipe:2",
        "-nostats",

        "-map", "[record]",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "17",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-r", str(output_fps),
        "-f", "matroska",
        str(output),

        "-map", "[preview_out]",
        "-an",
        "-c:v", "mjpeg",
        "-q:v", "5",
        "-f", "image2pipe",
        "pipe:1",
    ]

    stderr_handle = open(output.with_suffix(".camera.log"), "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        stderr_handle.close()
        raise

    with _state_lock:
        _current_process = proc

    progress = {
        "seconds": 0.0,
        "ready": False,
        "frame": 0,
        "dupFrames": 0,
        "dropFrames": 0,
        "outputFps": int(output_fps),
        "inputFpsRequested": float(input_fps),
        "effectiveSourceFps": 0.0,
    }
    set_live_preview_running(True, clear_frame=True)

    def progress_reader() -> None:
        if proc.stderr is None:
            return
        for raw_line in iter(proc.stderr.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            try:
                stderr_handle.write(line + "\n")
                stderr_handle.flush()
            except Exception:
                pass

            stats = apply_capture_progress_line(progress, line)
            if stats is not None:
                update_state(captureStats=stats)

    def preview_reader() -> None:
        if proc.stdout is None:
            return
        buffer = bytearray()
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            buffer.extend(chunk)

            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    if len(buffer) > 2:
                        del buffer[:-2]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                publish_live_preview(frame)

            # Defensive cap in case a damaged stream never emits an EOI marker.
            if len(buffer) > 12 * 1024 * 1024:
                last_start = buffer.rfind(b"\xff\xd8")
                if last_start >= 0:
                    del buffer[:last_start]
                else:
                    buffer.clear()

    readers = [
        threading.Thread(target=progress_reader, daemon=True),
        threading.Thread(target=preview_reader, daemon=True),
    ]
    for thread in readers:
        thread.start()
    setattr(proc, "_recordcountdown_readers", readers)
    return proc, stderr_handle, progress


def start_continuous_capture(
    output: Path,
    output_fps: int,
    settings_override: dict | None = None,
) -> tuple[subprocess.Popen, Any, dict]:
    """Open the camera once for the whole session, with automatic fallbacks.

    A session owns one immutable settings snapshot. The camera name is
    resolved against the DirectShow list of the current PC, and if FFmpeg
    exits immediately (driver rejects size/FPS, buffer too large, ...) the
    next more permissive option set is tried automatically.
    """
    settings = settings_override if settings_override is not None else load_settings()
    configured = str(settings.get("cameraDevice") or "").strip()
    if not configured:
        raise RuntimeError("Camera Device belum dipilih di Settings.")
    device = resolve_dshow_device(configured)

    last_error = ""
    for index, opts in enumerate(dshow_input_attempts(settings)):
        proc, stderr_handle, progress = _start_continuous_capture_once(output, output_fps, settings, device, opts)
        deadline = time.time() + 6.0
        started = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if float(progress.get("seconds", 0.0)) > 0 or progress.get("frame", 0):
                started = True
                break
            time.sleep(0.05)
        else:
            started = proc.poll() is None  # still running but slow to report: accept
        if started or proc.poll() is None:
            return proc, stderr_handle, progress
        last_error = f"FFmpeg berhenti saat membuka '{device}' (opsi {opts})"
        try:
            stop_continuous_capture(proc, stderr_handle)
        except Exception:
            pass
    raise RuntimeError(
        f"{last_error}. Kamera tidak dapat dibuka dengan semua opsi. Tutup aplikasi lain yang memakai kamera "
        "atau ubah Sumber sesi ke Browser."
    )


def stop_continuous_capture(proc: subprocess.Popen, stderr_handle: Any) -> None:
    global _current_process
    try:
        if proc.poll() is None and proc.stdin:
            proc.stdin.write(b"q\n")
            proc.stdin.flush()
            proc.wait(timeout=12)
    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        for thread in getattr(proc, "_recordcountdown_readers", []):
            try:
                thread.join(timeout=1.5)
            except Exception:
                pass
        try:
            stderr_handle.close()
        except Exception:
            pass
        with _state_lock:
            if _current_process is proc:
                _current_process = None
        set_live_preview_running(False)


def wait_for_media_time(proc: subprocess.Popen, progress: dict, target: float, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _state_lock:
            if _cancel_requested:
                raise RuntimeError("Session dibatalkan.")
        if proc.poll() is not None:
            raise RuntimeError("Camera capture berhenti sebelum waktunya. Cek file .camera.log di folder session.")
        if float(progress.get("seconds", 0.0)) >= target:
            return
        time.sleep(0.03)
    raise RuntimeError(
        f"Camera stream tidak mencapai media time {target:.2f}s. "
        f"Terakhir {float(progress.get('seconds', 0.0)):.2f}s."
    )


def extract_take_from_session(
    source_video: Path,
    output_video: Path,
    start_time: float,
    duration: float,
    fps: int,
    settings: dict,
) -> None:
    ffmpeg = ffmpeg_executable()
    filters: list[str] = []
    transform = camera_transform_filter(settings)
    if transform:
        filters.append(transform)
    filters.extend([f"fps={fps}", "setsar=1", "setpts=PTS-STARTPTS"])
    args = [
        ffmpeg, "-hide_banner", "-y",
        "-i", str(source_video),
        "-ss", f"{start_time:.3f}",
        "-t", f"{duration:.3f}",
        "-an",
        "-vf", ",".join(filters),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "17",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_video),
    ]
    run_process(args, output_video.with_suffix(".extract.log"))


def _image_is_black(path: Path, threshold: float = 6.0) -> bool:
    try:
        with Image.open(path) as im:
            gray = im.convert("L").resize((64, 64))
            data = list(gray.getdata())
        return (sum(data) / max(1, len(data))) < threshold
    except Exception:
        return True


def extract_snapshot(video_path: Path, output_jpg: Path, snapshot_lead: float) -> None:
    """Extract the photo; if the frame is black, step back until a real frame is found."""
    ffmpeg = ffmpeg_executable()
    base = max(0.05, float(snapshot_lead))
    leads = [base, base + 0.15, base + 0.35, base + 0.7, base + 1.2, base + 2.0]
    for lead in leads:
        args = [
            ffmpeg, "-hide_banner", "-y",
            "-sseof", f"-{lead:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(output_jpg),
        ]
        try:
            run_process(args, output_jpg.with_suffix(".snapshot.log"))
        except Exception:
            continue
        if output_jpg.is_file() and not _image_is_black(output_jpg):
            return
    # Last resort: the middle of the take (never leave a missing/black photo).
    args = [
        ffmpeg, "-hide_banner", "-y", "-i", str(video_path),
        "-vf", "select='gte(n,5)'", "-frames:v", "1", "-q:v", "2", str(output_jpg),
    ]
    run_process(args, output_jpg.with_suffix(".snapshot.log"))


def extract_snapshot_at(source_video: Path, output_jpg: Path, timestamp: float) -> None:
    """Extract the first complete frame at/after an explicit media timestamp."""
    ffmpeg = ffmpeg_executable()
    args = [
        ffmpeg, "-hide_banner", "-y",
        "-i", str(source_video),
        "-ss", f"{max(0.0, timestamp):.3f}",
        "-frames:v", "1",
        "-q:v", "2",
        str(output_jpg),
    ]
    run_process(args, output_jpg.with_suffix(".snapshot.log"))


def fit_pil(source: Image.Image, width: int, height: int, mode: str) -> Image.Image:
    src = source.convert("RGB")
    if mode == "contain":
        result = Image.new("RGB", (width, height), "black")
        fitted = ImageOps.contain(src, (width, height), Image.Resampling.LANCZOS)
        x = (width - fitted.width) // 2
        y = (height - fitted.height) // 2
        result.paste(fitted, (x, y))
        return result
    return ImageOps.fit(src, (width, height), Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def compose_photo(template: dict, template_folder: Path, session_dir: Path, output_path: Path) -> None:
    cw = int(template["canvas"]["width"])
    ch = int(template["canvas"]["height"])
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))

    video_cfg = template.get("video", {})
    default_fit = str(video_cfg.get("fitMode", "cover"))

    snapshots = {}
    take_count = max(int(s.get("sourceTake", 1)) for s in template["slots"])
    for take in range(1, take_count + 1):
        p = session_dir / f"snapshot_{take:02d}.jpg"
        snapshots[take] = Image.open(p).convert("RGB")

    for slot in template["slots"]:
        take = int(slot["sourceTake"])
        x, y = int(slot["x"]), int(slot["y"])
        sw, sh = int(slot["width"]), int(slot["height"])
        fit_mode = str(slot.get("fitMode") or default_fit)
        panel = fit_pil(snapshots[take], sw, sh, fit_mode).convert("RGBA")
        canvas.alpha_composite(panel, (x, y))

    overlay = Image.open(template_folder / template["overlay"]).convert("RGBA")
    if overlay.size != (cw, ch):
        overlay = overlay.resize((cw, ch), Image.Resampling.LANCZOS)
    canvas.alpha_composite(overlay)
    canvas.convert("RGB").save(output_path, quality=95, dpi=(300, 300))


def compose_video(template: dict, template_folder: Path, session_dir: Path, output_path: Path) -> None:
    ffmpeg = ffmpeg_executable()
    slots = template["slots"]
    take_count = max(int(s.get("sourceTake", 1)) for s in slots)
    capture_cfg = template.get("capture", {})
    video_cfg = template.get("video", {})
    fps = int(video_cfg.get("fps", DEFAULT_CAPTURE_FPS))
    # A take is exactly the countdown recording. The short capture indicator
    # shown after number 1 is UI feedback only and must never extend the clip.
    take_duration = max(0.1, float(capture_cfg.get("countdown", 5)))
    duration = max(0.1, float(video_cfg.get("outputDuration") or take_duration))
    loop_mode = str(video_cfg.get("loopMode", "none")).lower()
    default_fit = str(video_cfg.get("fitMode", "cover")).lower()
    cw = int(template["canvas"]["width"])
    ch = int(template["canvas"]["height"])
    quality = video_quality_profile(video_cfg)
    geometry = video_render_geometry(template)
    render_scale = geometry["scale"]
    output_width, output_height = geometry["contentWidth"], geometry["contentHeight"]

    args = [ffmpeg, "-hide_banner", "-y"]
    for take in range(1, take_count + 1):
        if loop_mode == "repeat":
            args += ["-stream_loop", "-1"]
        args += ["-i", str(session_dir / f"take_{take:02d}.mp4")]

    overlay_index = take_count
    args += ["-loop", "1", "-framerate", str(fps), "-i", str(template_folder / template["overlay"])]

    filters = []

    # One raw timeline per take, then split according to how often it is used.
    for take in range(1, take_count + 1):
        uses = sum(1 for s in slots if int(s.get("sourceTake", 1)) == take)
        pad = "" if loop_mode == "repeat" else f",tpad=stop_mode=clone:stop_duration={duration:.3f}"
        filters.append(
            f"[{take-1}:v]setpts=PTS-STARTPTS,fps={fps}{pad},trim=duration={duration:.3f}[raw{take}]"
        )
        labels = "".join(f"[t{take}_{i}]" for i in range(1, uses + 1))
        if uses == 1:
            filters.append(f"[raw{take}]null[t{take}_1]")
        else:
            filters.append(f"[raw{take}]split={uses}{labels}")

    copy_counter = {take: 0 for take in range(1, take_count + 1)}
    for slot_idx, slot in enumerate(slots, 1):
        take = int(slot["sourceTake"])
        copy_counter[take] += 1
        source_label = f"t{take}_{copy_counter[take]}"
        sw = scaled_video_pixels(int(slot["width"]), render_scale)
        sh = scaled_video_pixels(int(slot["height"]), render_scale)
        fit_mode = str(slot.get("fitMode") or default_fit).lower()

        if fit_mode == "contain":
            filters.append(
                f"[{source_label}]scale={sw}:{sh}:flags=lanczos:force_original_aspect_ratio=decrease,"
                f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[slot{slot_idx}]"
            )
        else:
            filters.append(
                f"[{source_label}]scale={sw}:{sh}:flags=lanczos:force_original_aspect_ratio=increase,"
                f"crop={sw}:{sh},setsar=1[slot{slot_idx}]"
            )

    filters.append(f"color=c=black:s={output_width}x{output_height}:r={fps}:d={duration:.3f}[base]")
    current = "base"
    for slot_idx, slot in enumerate(slots, 1):
        x = int(round(int(slot["x"]) * render_scale))
        y = int(round(int(slot["y"]) * render_scale))
        nxt = f"panel{slot_idx}"
        filters.append(f"[{current}][slot{slot_idx}]overlay=x={x}:y={y}:eof_action=pass[{nxt}]")
        current = nxt

    filters.append(f"[{overlay_index}:v]scale={output_width}:{output_height}:flags=lanczos,format=rgba,setsar=1[frame]")
    filters.append(f"[{current}][frame]overlay=x=0:y=0:eof_action=repeat[composed]")
    if video_cfg.get("outputLayout") == "portrait_9_16":
        filters.append(
            f"[composed]pad={geometry['width']}:{geometry['height']}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[outv]"
        )
    else:
        filters.append("[composed]null[outv]")
    filter_complex = ";".join(filters)

    args += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-an",
        "-t", f"{duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", str(quality["preset"]),
        "-crf", str(quality["crf"]),
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run_process(args, session_dir / "compose_video.log")


def copy_to_hot_folder(snapshot: Path, session_id: str, take: int) -> None:
    settings = load_settings()
    hot = settings.get("hotFolder", {})
    if not hot.get("enabled"):
        return
    target = str(hot.get("path") or "").strip()
    if not target:
        return
    folder = Path(target)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"RC_{session_id}_take_{take:02d}.jpg"
    shutil.copy2(snapshot, dst)


def copy_output_to_sync_folder(source: Path, folder_value: str) -> str:
    folder = Path(folder_value).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / source.name
    if destination.resolve() != source.resolve():
        shutil.copy2(source, destination)
    return str(destination)


def sync_outputs_to_drive_desktop(photo: Path, video: Path) -> dict:
    cfg = normalize_desktop_sync_settings(load_settings().get("desktopSync"))
    result: dict[str, Any] = {"enabled": cfg["enabled"], "photo": None, "video": None, "errors": []}
    if not cfg["enabled"]:
        return result
    if cfg["photoFolder"]:
        try:
            result["photo"] = copy_output_to_sync_folder(photo, cfg["photoFolder"])
        except Exception as exc:
            result["errors"].append(f"Foto: {exc}")
    else:
        result["errors"].append("Folder sync foto belum diisi.")
    if cfg["syncVideos"]:
        if cfg["videoFolder"]:
            try:
                result["video"] = copy_output_to_sync_folder(video, cfg["videoFolder"])
            except Exception as exc:
                result["errors"].append(f"Video: {exc}")
        else:
            result["errors"].append("Folder sync video belum diisi.")
    return result


def session_worker(template_id: str, settings_snapshot: dict | None = None) -> None:
    """
    V1.1 SyncStart:
    The camera is opened once for the WHOLE session.

    Why:
    EOS Webcam Utility / DirectShow can output frozen or stale frames for a
    moment when a new FFmpeg process opens the camera. Opening it separately
    for Take 1/2/3 made the visual start of each clip inconsistent.

    Now:
    - one continuous session_source.mkv
    - warm up the camera before countdown
    - mark each countdown using FFmpeg's own media clock
    - extract every take afterwards
    - every extracted take starts at PTS 0 in the final composition
    """
    global _cancel_requested
    camera_proc = None
    camera_log = None

    try:
        template = load_template(template_id)
        folder = template_dir(template_id)
        slots = template.get("slots", [])
        take_count = max((int(s.get("sourceTake", 1)) for s in slots), default=0)
        if take_count < 1:
            raise RuntimeError("Template belum memiliki slot/take.")

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        session_dir = SESSIONS_DIR / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)

        capture_cfg = template.get("capture", {})
        video_cfg = template.get("video", {})
        countdown = float(capture_cfg.get("countdown", 5))
        between = float(capture_cfg.get("betweenTakes", 1))
        pre_delay = float(capture_cfg.get("preSessionDelay", 3))
        capture_delay = max(0.05, float(capture_cfg.get("captureDelay", 0.25)))
        snapshot_lead = max(0.01, float(capture_cfg.get("snapshotLead", 0.05)))
        fps = int(video_cfg.get("fps", DEFAULT_CAPTURE_FPS))

        # Clone the snapshot so every take, preview and exported result uses
        # the exact transform that was active when Start Session was pressed.
        settings = json.loads(json.dumps(settings_snapshot)) if settings_snapshot is not None else load_settings()
        settings["cameraTransform"] = normalize_camera_transform(settings.get("cameraTransform"))
        settings["cameraBackendRotationOffset"] = normalize_camera_backend_rotation(
            settings.get("cameraBackendRotationOffset"), settings
        )
        session_preview_transform = effective_camera_transform(settings)
        warmup = max(0.0, float(settings.get("cameraWarmupSeconds", 3.0)))

        source_video = session_dir / "session_source.mkv"

        update_state(
            phase="warmup",
            message="Opening EOS Webcam / warming camera",
            templateId=template_id,
            sessionId=session_id,
            take=0,
            takeCount=take_count,
            phaseStartedAt=time.time(),
            phaseDuration=warmup,
            outputVideo=None,
            outputPhoto=None,
            desktopSync=None,
            captureStats={
                "inputFpsRequested": float(settings.get("cameraInputFps") or 0),
                "outputFps": fps,
                "previewFps": SESSION_PREVIEW_FPS,
                "encodedFrames": 0,
                "duplicatedFrames": 0,
                "droppedFrames": 0,
                "effectiveSourceFps": 0.0,
                "duplicatePercent": 0.0,
            },
            cameraTransform=settings["cameraTransform"],
            cameraBackendRotationOffset=settings["cameraBackendRotationOffset"],
            sessionPreviewTransform=session_preview_transform,
            error=None,
        )

        camera_proc, camera_log, progress = start_continuous_capture(source_video, fps, settings)

        # Wait until FFmpeg is actually producing recorded media, then warm up
        # in MEDIA time. Startup/frozen DirectShow frames are kept outside takes.
        wait_for_media_time(camera_proc, progress, 0.20, timeout=20.0)
        warmup_start = float(progress["seconds"])
        wait_for_media_time(camera_proc, progress, warmup_start + warmup, timeout=max(20.0, warmup + 10.0))

        if pre_delay > 0:
            update_state(
                phase="prepare",
                message="Countdown will start soon",
                take=0,
                phaseStartedAt=time.time(),
                phaseDuration=pre_delay,
            )
            end = time.time() + pre_delay
            while time.time() < end:
                with _state_lock:
                    if _cancel_requested:
                        raise RuntimeError("Session dibatalkan.")
                time.sleep(0.05)

        take_ranges: list[tuple[float, float]] = []

        for take in range(1, take_count + 1):
            # Use FFmpeg's output media clock as the source of truth.
            take_start = float(progress["seconds"])

            update_state(
                phase="recording",
                message=f"Sesi {take}/{take_count} — merekam {countdown:g} detik",
                take=take,
                phaseStartedAt=time.time(),
                phaseDuration=countdown,
            )

            # Wait for the continuous recorded media to advance exactly by the
            # requested countdown duration. This makes all take lengths equal.
            countdown_end = take_start + countdown
            wait_for_media_time(
                camera_proc,
                progress,
                countdown_end,
                timeout=max(20.0, countdown + 12.0),
            )

            # The last complete frame INSIDE this exact countdown recording is
            # the still-photo result. The following short phase only keeps the
            # flash visible; it is deliberately outside the 5-second clip.
            take_ranges.append((take_start, countdown))
            update_state(
                phase="capture",
                message=f"Foto sesi {take}/{take_count} = frame terakhir video",
                take=take,
                phaseStartedAt=time.time(),
                phaseDuration=capture_delay,
            )
            wait_for_media_time(
                camera_proc,
                progress,
                countdown_end + capture_delay,
                timeout=max(10.0, capture_delay + 5.0),
            )

            if take < take_count and between > 0:
                update_state(
                    phase="between",
                    message="Siapkan pose berikutnya",
                    take=take,
                    phaseStartedAt=time.time(),
                    phaseDuration=between,
                )
                end = time.time() + between
                while time.time() < end:
                    with _state_lock:
                        if _cancel_requested:
                            raise RuntimeError("Session dibatalkan.")
                    time.sleep(0.05)

        # A tiny post-roll gives the container enough complete frames before
        # graceful shutdown.
        last_target = float(progress["seconds"]) + 0.20
        try:
            wait_for_media_time(camera_proc, progress, last_target, timeout=5.0)
        except Exception:
            pass

        stop_continuous_capture(camera_proc, camera_log)
        camera_proc = None
        camera_log = None

        update_state(
            phase="extracting",
            message="Preparing takes",
            take=take_count,
            phaseStartedAt=None,
            phaseDuration=0,
        )

        # Extract clean, independent clips. Every one gets a fresh 00:00 PTS.
        for take, (start_time, duration) in enumerate(take_ranges, 1):
            video_path = session_dir / f"take_{take:02d}.mp4"
            extract_take_from_session(source_video, video_path, start_time, duration, fps, settings)

            snapshot = session_dir / f"snapshot_{take:02d}.jpg"
            extract_snapshot(video_path, snapshot, snapshot_lead)
            copy_to_hot_folder(snapshot, session_id, take)

        update_state(
            phase="rendering",
            message="Rendering",
            take=take_count,
            phaseStartedAt=time.time(),
            phaseDuration=0,
        )

        out_video = VIDEOS_DIR / f"recordcountdown_{session_id}.mp4"
        out_photo = PHOTOS_DIR / f"recordcountdown_{session_id}.jpg"
        compose_photo(template, folder, session_dir, out_photo)
        compose_video(template, folder, session_dir, out_video)
        desktop_sync = sync_outputs_to_drive_desktop(out_photo, out_video)

        update_state(
            phase="done",
            message="Done" if not desktop_sync["errors"] else "Done • Drive sync copy perlu diperiksa",
            outputVideo=f"/outputs/{output_relative_name(out_video)}",
            outputPhoto=f"/outputs/{output_relative_name(out_photo)}",
            desktopSync=desktop_sync,
            phaseStartedAt=None,
            phaseDuration=0,
        )

    except Exception as exc:
        if camera_proc is not None:
            try:
                stop_continuous_capture(camera_proc, camera_log)
            except Exception:
                pass

        if _cancel_requested:
            update_state(
                phase="idle",
                message="Cancelled",
                error=None,
                phaseStartedAt=None,
                phaseDuration=0,
            )
        else:
            update_state(
                phase="error",
                message="Error",
                error=str(exc),
                phaseStartedAt=None,
                phaseDuration=0,
            )


def browser_session_worker(
    template_id: str,
    settings_snapshot: dict,
    session_id: str,
    session_dir: Path,
    source_video: Path,
    take_ranges: list[tuple[float, float]],
) -> None:
    """Finish a session recorded by the browser MediaRecorder.

    This is the flexible-camera fallback used when a webcam is available to
    Chrome/Edge but is not exposed by FFmpeg's DirectShow enumeration. The
    browser sends one continuous WebM file and the exact countdown ranges;
    the normal FFmpeg extraction and template renderer then produce the same
    JPG/MP4 outputs as a DirectShow session.
    """
    global _browser_session
    try:
        template = load_template(template_id)
        folder = template_dir(template_id)
        slots = template.get("slots", [])
        take_count = max((int(s.get("sourceTake", 1)) for s in slots), default=0)
        if take_count < 1:
            raise RuntimeError("Template belum memiliki slot/take.")
        if len(take_ranges) != take_count:
            raise RuntimeError(f"Jumlah take browser ({len(take_ranges)}) tidak sama dengan template ({take_count}).")
        if not source_video.is_file() or source_video.stat().st_size < 1024:
            raise RuntimeError("Video browser kosong atau tidak lengkap.")

        settings = json.loads(json.dumps(settings_snapshot))
        settings["cameraTransform"] = normalize_camera_transform(settings.get("cameraTransform"))
        settings["cameraBackendRotationOffset"] = normalize_camera_backend_rotation(
            settings.get("cameraBackendRotationOffset"), settings
        )
        session_preview_transform = effective_camera_transform(settings)
        capture_cfg = template.get("capture", {})
        video_cfg = template.get("video", {})
        snapshot_lead = max(0.01, float(capture_cfg.get("snapshotLead", 0.05)))
        fps = int(video_cfg.get("fps", DEFAULT_CAPTURE_FPS))

        update_state(
            phase="extracting",
            message="Preparing browser takes",
            templateId=template_id,
            sessionId=session_id,
            take=take_count,
            takeCount=take_count,
            phaseStartedAt=time.time(),
            phaseDuration=0,
            outputVideo=None,
            outputPhoto=None,
            desktopSync=None,
            captureStats={
                "inputFpsRequested": float(settings.get("cameraInputFps") or 0),
                "outputFps": fps,
                "previewFps": SESSION_PREVIEW_FPS,
                "encodedFrames": 0,
                "duplicatedFrames": 0,
                "droppedFrames": 0,
                "effectiveSourceFps": 0.0,
                "duplicatePercent": 0.0,
                "source": "browser",
            },
            cameraTransform=settings["cameraTransform"],
            cameraBackendRotationOffset=settings["cameraBackendRotationOffset"],
            sessionPreviewTransform=session_preview_transform,
            error=None,
        )

        for take, (start_time, duration) in enumerate(take_ranges, 1):
            with _state_lock:
                if _cancel_requested:
                    raise RuntimeError("Session dibatalkan.")
            video_path = session_dir / f"take_{take:02d}.mp4"
            extract_take_from_session(source_video, video_path, float(start_time), float(duration), fps, settings)
            snapshot = session_dir / f"snapshot_{take:02d}.jpg"
            extract_snapshot(video_path, snapshot, snapshot_lead)
            copy_to_hot_folder(snapshot, session_id, take)
            update_state(
                phase="extracting",
                message=f"Menyiapkan foto browser {take}/{take_count}",
                take=take,
                takeCount=take_count,
                phaseStartedAt=time.time(),
                phaseDuration=0,
            )

        update_state(
            phase="rendering",
            message="Rendering",
            take=take_count,
            takeCount=take_count,
            phaseStartedAt=time.time(),
            phaseDuration=0,
        )
        out_video = VIDEOS_DIR / f"recordcountdown_{session_id}.mp4"
        out_photo = PHOTOS_DIR / f"recordcountdown_{session_id}.jpg"
        compose_photo(template, folder, session_dir, out_photo)
        compose_video(template, folder, session_dir, out_video)
        desktop_sync = sync_outputs_to_drive_desktop(out_photo, out_video)
        update_state(
            phase="done",
            message="Done" if not desktop_sync["errors"] else "Done • Drive sync copy perlu diperiksa",
            outputVideo=f"/outputs/{output_relative_name(out_video)}",
            outputPhoto=f"/outputs/{output_relative_name(out_photo)}",
            desktopSync=desktop_sync,
            phaseStartedAt=None,
            phaseDuration=0,
            error=None,
        )
    except Exception as exc:
        if _cancel_requested:
            update_state(phase="idle", message="Cancelled", error=None, phaseStartedAt=None, phaseDuration=0)
        else:
            update_state(phase="error", message="Error", error=str(exc), phaseStartedAt=None, phaseDuration=0)
    finally:
        with _state_lock:
            if _browser_session and _browser_session.get("sessionId") == session_id:
                _browser_session = None


@app.route("/")
def index():
    page = send_from_directory(BASE / "static", "index.html")
    page.headers["Cache-Control"] = "no-store, must-revalidate"
    return page


@app.route("/kiosk")
def kiosk():
    return index()


@app.get("/api/health")
def health():
    try:
        ffmpeg = ffmpeg_executable()
        ffmpeg_ok = True
    except Exception as exc:
        ffmpeg = str(exc)
        ffmpeg_ok = False
    return jsonify({
        "ok": True,
        "ffmpegOk": ffmpeg_ok,
        "ffmpeg": ffmpeg,
        "platform": os.name,
    })


@app.get("/api/settings")
def api_get_settings():
    data = load_settings()
    data["cameraTransform"] = normalize_camera_transform(data.get("cameraTransform"))
    data["cameraBackendRotationOffset"] = normalize_camera_backend_rotation(
        data.get("cameraBackendRotationOffset"), data
    )
    data["sessionPreviewTransform"] = effective_camera_transform(data)
    data["cameraTransformFilter"] = camera_transform_filter(data) or "none"
    data["qr"] = normalize_qr_settings(data.get("qr"))
    data["printer"] = normalize_printer_settings(data.get("printer"))
    data["kiosk"] = normalize_kiosk_settings(data.get("kiosk"))
    data["desktopSync"] = normalize_desktop_sync_settings(data.get("desktopSync"))
    data["customQrUploaded"] = CUSTOM_QR_PATH.is_file()
    return jsonify(data)


@app.put("/api/settings")
def api_put_settings():
    data = request.get_json(force=True)
    try:
        data["cameraTransform"] = normalize_camera_transform(data.get("cameraTransform"))
        data["cameraBackendRotationOffset"] = normalize_camera_backend_rotation(
            data.get("cameraBackendRotationOffset"), data
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data["qr"] = normalize_qr_settings(data.get("qr"))
    data["printer"] = normalize_printer_settings(data.get("printer"))
    data["kiosk"] = normalize_kiosk_settings(data.get("kiosk"))
    data["desktopSync"] = normalize_desktop_sync_settings(data.get("desktopSync"))
    data.pop("customQrUploaded", None)
    data.pop("cameraTransformFilter", None)
    data.pop("sessionPreviewTransform", None)
    save_settings(data)
    return jsonify({
        "ok": True,
        "cameraTransform": data["cameraTransform"],
        "cameraBackendRotationOffset": data["cameraBackendRotationOffset"],
        "sessionPreviewTransform": effective_camera_transform(data),
        "cameraTransformFilter": camera_transform_filter(data) or "none",
        "qr": data["qr"],
        "printer": data["printer"],
        "kiosk": data["kiosk"],
        "desktopSync": data["desktopSync"],
        "customQrUploaded": CUSTOM_QR_PATH.is_file(),
    })


@app.put("/api/settings/camera-transform")
def api_put_camera_transform():
    payload = request.get_json(force=True)
    data = load_settings()
    try:
        if isinstance(payload, dict) and "cameraTransform" in payload:
            transform = normalize_camera_transform(payload.get("cameraTransform"))
            offset_value = payload.get("cameraBackendRotationOffset", data.get("cameraBackendRotationOffset"))
        else:
            transform = normalize_camera_transform(payload)
            offset_value = data.get("cameraBackendRotationOffset")
        offset = normalize_camera_backend_rotation(offset_value, data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data["cameraTransform"] = transform
    data["cameraBackendRotationOffset"] = offset
    save_settings(data)
    return jsonify({
        "ok": True,
        "cameraTransform": transform,
        "cameraBackendRotationOffset": offset,
        "sessionPreviewTransform": effective_camera_transform(data),
        "cameraTransformFilter": camera_transform_filter(data) or "none",
    })


@app.get("/api/devices")
def api_devices():
    try:
        return jsonify({"devices": dshow_devices()})
    except Exception as exc:
        return jsonify({"devices": [], "error": str(exc)}), 500


@app.get("/api/printers")
def api_printers():
    try:
        printers = installed_windows_printers()
        default = default_windows_printer()
        recommended = ""
        if printers:
            try:
                recommended, _ = resolve_windows_printer("")
            except Exception:
                recommended = default if default in printers else printers[0]
        try:
            import win32print  # noqa: F401
            pywin32_ready = True
        except ImportError:
            pywin32_ready = False
        return jsonify({
            "printers": printers,
            "supported": os.name == "nt",
            "defaultPrinter": default,
            "recommendedPrinter": recommended,
            "powerShellReady": bool(powershell_executable()),
            "pywin32Ready": pywin32_ready,
            "printLog": str(PRINT_LOG_PATH),
        })
    except Exception as exc:
        return jsonify({"printers": [], "supported": False, "error": str(exc)}), 500


@app.post("/api/printers/preferences")
def api_open_printer_preferences():
    if os.name != "nt":
        return jsonify({"error": "Printing Preferences hanya tersedia di Windows."}), 400
    body = request.get_json(silent=True) or {}
    try:
        selected, _ = resolve_windows_printer(str(body.get("printerName") or "").strip())
        subprocess.Popen(
            ["rundll32.exe", "printui.dll,PrintUIEntry", "/e", "/n", selected],
            close_fds=False,
        )
        return jsonify({"ok": True, "printer": selected})
    except Exception as exc:
        return jsonify({"error": f"Tidak dapat membuka Printing Preferences: {exc}"}), 500


@app.post("/api/qr/custom")
def api_upload_custom_qr():
    if "qrImage" not in request.files:
        return jsonify({"error": "Pilih file gambar QR/barcode."}), 400
    uploaded = request.files["qrImage"]
    raw = uploaded.read()
    if not raw:
        return jsonify({"error": "File QR/barcode kosong."}), 400
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({"error": "Ukuran gambar QR maksimal 10 MB."}), 400
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            temporary = CUSTOM_QR_PATH.with_suffix(".tmp.png")
            image.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, CUSTOM_QR_PATH)
        return jsonify({"ok": True, "url": f"/api/qr/custom-image?t={time.time()}"})
    except Exception as exc:
        return jsonify({"error": f"Gambar QR/barcode tidak valid: {exc}"}), 400


@app.get("/api/qr/custom-image")
def api_custom_qr_image():
    if not CUSTOM_QR_PATH.is_file():
        return jsonify({"error": "QR/barcode custom belum di-upload."}), 404
    return send_file(CUSTOM_QR_PATH, mimetype="image/png", max_age=0)


@app.post("/api/desktop-sync/test")
def api_desktop_sync_test():
    body = request.get_json(force=True)
    sync_videos = bool(body.get("syncVideos", True))
    folders = {
        "photo": str(body.get("photoFolder") or "").strip(),
        "video": str(body.get("videoFolder") or "").strip(),
    }
    result: dict[str, Any] = {}
    for kind, value in folders.items():
        if kind == "video" and not sync_videos:
            result[kind] = {"ok": True, "skipped": True, "path": ""}
            continue
        if not value:
            result[kind] = {"ok": False, "error": "Folder belum diisi."}
            continue
        try:
            folder = Path(value).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".recordcountdown_test_{uuid.uuid4().hex}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            result[kind] = {"ok": True, "path": str(folder)}
        except Exception as exc:
            result[kind] = {"ok": False, "error": str(exc)}
    return jsonify({"ok": all(item["ok"] for item in result.values()), "folders": result})


@app.get("/api/share")
def api_share():
    settings = load_settings()
    cfg = normalize_qr_settings(settings.get("qr"))
    preview = request.args.get("preview") == "1"
    try:
        photo = output_photo_path(request.args.get("photo", "hasil-foto.jpg"), must_exist=not preview)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 404

    if cfg["mode"] == "custom":
        available = CUSTOM_QR_PATH.is_file()
        return jsonify({
            "enabled": bool(cfg["enabled"] and available),
            "label": cfg["label"],
            "targetUrl": "",
            "qrImageUrl": f"/api/qr/custom-image?t={time.time()}",
            "mode": "custom",
            "status": "ready" if available else "missing",
            "message": "" if available else "Upload gambar QR/barcode di Settings.",
        })

    relative = output_relative_name(photo)
    target = qr_target_url(relative)
    enabled = bool(cfg["enabled"] and target)
    return jsonify({
        "enabled": enabled,
        "label": cfg["label"],
        "targetUrl": target,
        "qrImageUrl": f"/api/share/qr?photo={quote(relative, safe='')}{'&preview=1' if preview else ''}",
        "mode": cfg["mode"],
        "status": "ready" if enabled else "missing",
        "message": "" if enabled else "Tujuan QR belum tersedia.",
    })


@app.get("/api/share/qr")
def api_share_qr():
    cfg = normalize_qr_settings(load_settings().get("qr"))
    if cfg["mode"] == "custom":
        return api_custom_qr_image()
    preview = request.args.get("preview") == "1"
    try:
        photo = output_photo_path(request.args.get("photo", "hasil-foto.jpg"), must_exist=not preview)
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 404
    target = qr_target_url(output_relative_name(photo))
    if not target:
        return jsonify({"error": "Tujuan QR belum diisi di Settings."}), 400

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(target)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="image/png",
        max_age=0,
        download_name="recordcountdown-qr.png",
    )


@app.post("/api/print")
def api_print_photo():
    body = request.get_json(force=True)
    settings = load_settings()
    cfg = normalize_printer_settings(settings.get("printer"))
    if not cfg["enabled"]:
        return jsonify({"error": "Fitur print belum diaktifkan di Settings."}), 400
    try:
        photo = output_photo_path(str(body.get("photo") or ""))
        requested_printer = str(body.get("printerName") or cfg["name"] or "").strip()
        with _print_lock:
            result = print_photo_windows(photo, requested_printer, cfg["copies"], cfg["fitMode"])
        return jsonify({
            "ok": True,
            "printer": result["printer"],
            "copies": cfg["copies"],
            "method": result["method"],
            "jobs": result["jobs"],
            "details": result["details"],
        })
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        append_print_log(f"Print final gagal: {exc}")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/print/preview")
def api_print_preview():
    """Preview the same 4x6 composition used by the printer, without a job."""
    mode = request.args.get("fitMode", "strip1left")
    if mode not in PRINT_FIT_MODES:
        return jsonify({"error": "Format cetak tidak valid."}), 400
    try:
        photo = request.args.get("photo", "")
        if photo:
            source_path = output_photo_path(photo)
        else:
            template_id = request.args.get("templateId", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", template_id):
                raise ValueError("Pilih template di Capture terlebih dahulu.")
            template = load_template(template_id)
            source_path = template_dir(template_id) / template["overlay"]
        with Image.open(source_path) as source:
            rgba = source.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, "white")
            flat.alpha_composite(rgba)
            page, _ = prepare_print_image(flat, (1200, 1800), mode)
            page.thumbnail((400, 600), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            page.save(buffer, format="PNG")
        buffer.seek(0)
        return send_file(buffer, mimetype="image/png", max_age=0)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/print/test")
def api_print_test():
    body = request.get_json(force=True)
    requested = str(body.get("printerName") or "").strip()
    fit_mode = str(body.get("fitMode") or "strip1left").strip().lower()
    if fit_mode not in PRINT_FIT_MODES:
        fit_mode = "strip1left"
    try:
        selected, _ = resolve_windows_printer(requested)
        test_image = create_print_test_image(selected, fit_mode)
        with _print_lock:
            result = print_photo_windows(test_image, selected, 1, fit_mode)
        return jsonify({
            "ok": True,
            "printer": result["printer"],
            "copies": 1,
            "method": result["method"],
            "jobs": result["jobs"],
            "details": result["details"],
            "message": "Test print sudah dikirim ke antrean Windows.",
        })
    except Exception as exc:
        append_print_log(f"Test print gagal: {exc}")
        return jsonify({
            "error": str(exc),
            "printLog": str(PRINT_LOG_PATH),
        }), 500


@app.get("/api/templates")
def api_templates():
    return jsonify(list_templates())


@app.get("/api/templates/<template_id>")
def api_template(template_id: str):
    t = load_template(template_id)
    t["overlayUrl"] = f"/template-assets/{template_id}/{t['overlay']}"
    t["takeCount"] = max((int(s.get("sourceTake", 1)) for s in t.get("slots", [])), default=1)
    return jsonify(t)


@app.put("/api/templates/<template_id>")
def api_update_template(template_id: str):
    folder = template_dir(template_id)
    if not folder.exists():
        return jsonify({"error": "Template not found"}), 404
    data = request.get_json(force=True)
    data["id"] = template_id
    save_json(folder / "template.json", data)
    return jsonify({"ok": True})


@app.post("/api/templates/import")
def api_import_template():
    if "overlay" not in request.files:
        return jsonify({"error": "overlay PNG wajib di-upload"}), 400

    file = request.files["overlay"]
    name = (request.form.get("name") or Path(file.filename or "Template").stem).strip()
    template_id = safe_slug(name)
    original = template_id
    counter = 2
    while template_dir(template_id).exists():
        template_id = f"{original}-{counter}"
        counter += 1

    folder = template_dir(template_id)
    folder.mkdir(parents=True)
    overlay_path = folder / "overlay.png"
    file.save(overlay_path)

    try:
        w, h, slots = detect_slots(overlay_path)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    if not slots:
        # Still allow template creation. User can draw slots manually.
        slots = []

    take_count = max((s["sourceTake"] for s in slots), default=1)
    data = {
        "id": template_id,
        "name": name,
        "overlay": "overlay.png",
        "canvas": {"width": w, "height": h},
        "capture": {
            "preSessionDelay": 0.0,
            "countdown": 5.0,
            "betweenTakes": 1.0,
            "captureDelay": 0.25,
            "snapshotLead": 0.05
        },
        "video": {
            "fps": DEFAULT_CAPTURE_FPS,
            "outputDuration": 5.0,
            "fitMode": "cover",
            "loopMode": "none",
            "quality": "hd"
        },
        "slots": slots,
    }
    save_json(folder / "template.json", data)
    return jsonify({
        "ok": True,
        "templateId": template_id,
        "slotCount": len(slots),
        "takeCount": take_count,
    })


@app.delete("/api/templates/<template_id>")
def api_delete_template(template_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", template_id):
        return jsonify({"error": "ID template tidak valid."}), 400
    if template_id == "video-classic":
        return jsonify({"error": "Sample template tidak dapat dihapus."}), 400
    with _state_lock:
        if _session_state.get("phase") in ACTIVE_SESSION_PHASES and _session_state.get("templateId") == template_id:
            return jsonify({"error": "Template sedang dipakai oleh sesi aktif."}), 409
    folder = template_dir(template_id)
    if not folder.exists():
        return jsonify({"error": "Template tidak ditemukan."}), 404
    shutil.rmtree(folder)
    return jsonify({"ok": True})


@app.get("/template-assets/<template_id>/<filename>")
def template_asset(template_id: str, filename: str):
    return send_from_directory(template_dir(template_id), filename)


@app.post("/api/camera/test")
def api_camera_test():
    global _cancel_requested
    with _state_lock:
        if _session_state.get("phase") in ACTIVE_SESSION_PHASES:
            return jsonify({"error": "Session sedang berjalan."}), 409
        _cancel_requested = False

    try:
        settings = load_settings()
        transform = normalize_camera_transform(settings.get("cameraTransform"))
        settings["cameraTransform"] = transform
        settings["cameraBackendRotationOffset"] = normalize_camera_backend_rotation(
            settings.get("cameraBackendRotationOffset"), settings
        )
        test_file = DATA_DIR / "camera_test.mp4"
        record_take(test_file, 3.0, settings)
        return jsonify({
            "ok": True,
            "url": f"/data-files/{test_file.name}?t={time.time()}",
            "cameraTransform": transform,
            "cameraBackendRotationOffset": settings["cameraBackendRotationOffset"],
            "sessionPreviewTransform": effective_camera_transform(settings),
            "cameraTransformFilter": camera_transform_filter(settings) or "none",
            "inputFpsRequested": float(settings.get("cameraInputFps") or 0),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/session/browser/start")
def api_browser_session_start():
    """Reserve a session that will be recorded by browser MediaRecorder."""
    global _cancel_requested, _browser_session
    body = request.get_json(force=True) or {}
    template_id = str(body.get("templateId") or "")
    if not template_id:
        return jsonify({"error": "templateId wajib"}), 400
    try:
        template = load_template(template_id)
        slots = template.get("slots", [])
        take_count = max((int(s.get("sourceTake", 1)) for s in slots), default=0)
        if take_count < 1:
            raise RuntimeError("Template belum memiliki slot/take.")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    with _state_lock:
        if _session_state.get("phase") in ACTIVE_SESSION_PHASES or _browser_session:
            return jsonify({"error": "Session sedang berjalan."}), 409
        try:
            settings_snapshot = load_settings()
            settings_snapshot["cameraSource"] = "browser"
            requested_transform = body.get("cameraTransform", settings_snapshot.get("cameraTransform"))
            transform = normalize_camera_transform(requested_transform)
            settings_snapshot["cameraTransform"] = transform
            # Browser MediaRecorder stores the untransformed stream. The user
            # rotation is applied exactly once during FFmpeg extraction.
            settings_snapshot["cameraBackendRotationOffset"] = 0
            session_preview_transform = effective_camera_transform(settings_snapshot)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        _cancel_requested = False
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        session_dir = SESSIONS_DIR / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        _browser_session = {
            "sessionId": session_id,
            "templateId": template_id,
            "sessionDir": str(session_dir),
            "status": "recording",
            "progressSequence": 0,
        }
        update_state(
            phase="starting",
            message="Menyiapkan kamera browser",
            templateId=template_id,
            sessionId=session_id,
            take=0,
            takeCount=take_count,
            phaseStartedAt=time.time(),
            phaseDuration=0,
            outputVideo=None,
            outputPhoto=None,
            desktopSync=None,
            captureStats={"source": "browser", "outputFps": int(template.get("video", {}).get("fps", DEFAULT_CAPTURE_FPS))},
            cameraTransform=transform,
            cameraBackendRotationOffset=0,
            sessionPreviewTransform=session_preview_transform,
            error=None,
        )

    return jsonify({
        "ok": True,
        "browser": True,
        "sessionId": session_id,
        "takeCount": take_count,
        "cameraTransform": transform,
        "cameraBackendRotationOffset": 0,
        "sessionPreviewTransform": session_preview_transform,
    })


@app.post("/api/session/browser/progress")
def api_browser_session_progress():
    body = request.get_json(force=True) or {}
    session_id = str(body.get("sessionId") or "")
    try:
        sequence = max(0, int(body.get("sequence", 0)))
    except (TypeError, ValueError):
        sequence = 0
    with _state_lock:
        pending = _browser_session
        if not pending or pending.get("sessionId") != session_id:
            return jsonify({"error": "Sesi browser tidak ditemukan atau sudah selesai."}), 409
        if sequence and sequence <= int(pending.get("progressSequence", 0)):
            return jsonify({"ok": True, "stale": True})
        if sequence:
            pending["progressSequence"] = sequence
    phase = str(body.get("phase") or "starting").strip().lower()
    if phase not in {"starting", "warmup", "prepare", "recording", "capture", "between"}:
        return jsonify({"error": "Phase browser tidak valid."}), 400
    try:
        remaining = max(0.0, float(body.get("remaining", 0)))
        take = max(0, int(body.get("take", 0)))
        take_count = max(0, int(body.get("takeCount", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "Progress browser tidak valid."}), 400
    message = str(body.get("message") or "")[:160]
    update_state(
        phase=phase,
        message=message or phase,
        take=take,
        takeCount=take_count or _session_state.get("takeCount", 0),
        phaseStartedAt=time.time(),
        phaseDuration=remaining,
        error=None,
    )
    return jsonify({"ok": True})


@app.post("/api/session/browser/upload")
def api_browser_session_upload():
    global _browser_session
    session_id = str(request.form.get("sessionId") or "")
    ranges_raw = request.form.get("ranges") or "[]"
    upload = request.files.get("video")
    if not session_id or upload is None:
        return jsonify({"error": "sessionId dan video wajib dikirim."}), 400
    try:
        raw_ranges = json.loads(ranges_raw)
        if not isinstance(raw_ranges, list):
            raise ValueError
        take_ranges = []
        for item in raw_ranges:
            if not isinstance(item, dict):
                raise ValueError
            start = max(0.0, float(item.get("start", 0)))
            duration = max(0.1, float(item.get("duration", 0)))
            take_ranges.append((start, duration))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return jsonify({"error": "Rentang take browser tidak valid."}), 400

    with _state_lock:
        pending = _browser_session
        if not pending or pending.get("sessionId") != session_id:
            return jsonify({"error": "Sesi browser tidak ditemukan atau sudah dibatalkan."}), 409
        if _cancel_requested:
            return jsonify({"error": "Session dibatalkan."}), 409
        template_id = str(pending.get("templateId") or "")
        session_dir = Path(str(pending.get("sessionDir")))
        settings_snapshot = load_settings()
        settings_snapshot["cameraSource"] = "browser"
        settings_snapshot["cameraBackendRotationOffset"] = 0
        source_video = session_dir / "session_source.webm"

    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        upload.save(source_video)
        if not source_video.is_file() or source_video.stat().st_size < 1024:
            raise RuntimeError("Video browser kosong setelah upload.")
    except Exception as exc:
        update_state(phase="error", message="Upload browser gagal", error=str(exc), phaseStartedAt=None, phaseDuration=0)
        with _state_lock:
            _browser_session = None
        return jsonify({"error": str(exc)}), 500

    with _state_lock:
        if _browser_session and _browser_session.get("sessionId") == session_id:
            _browser_session["status"] = "processing"
    update_state(phase="extracting", message="Mengunggah video selesai, menyiapkan hasil", phaseStartedAt=time.time(), phaseDuration=0)
    thread = threading.Thread(
        target=browser_session_worker,
        args=(template_id, settings_snapshot, session_id, session_dir, source_video, take_ranges),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "sessionId": session_id, "processing": True})


@app.post("/api/session/start")
def api_session_start():
    global _cancel_requested
    body = request.get_json(force=True)
    template_id = str(body.get("templateId") or "")
    if not template_id:
        return jsonify({"error": "templateId wajib"}), 400

    # Lock the displayed rotation and the backend correction together at
    # session start so the worker does not pick up a delayed settings write.
    with _state_lock:
        if _session_state.get("phase") in ACTIVE_SESSION_PHASES:
            return jsonify({"error": "Session sedang berjalan."}), 409
        try:
            settings_snapshot = load_settings()
            requested_transform = body.get("cameraTransform", settings_snapshot.get("cameraTransform"))
            transform = normalize_camera_transform(requested_transform)
            settings_snapshot["cameraTransform"] = transform
            requested_offset = body.get(
                "cameraBackendRotationOffset", settings_snapshot.get("cameraBackendRotationOffset")
            )
            offset = normalize_camera_backend_rotation(requested_offset, settings_snapshot)
            settings_snapshot["cameraBackendRotationOffset"] = offset
            session_preview_transform = effective_camera_transform(settings_snapshot)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        save_settings(settings_snapshot)
        _cancel_requested = False
        update_state(
            phase="starting",
            message="Menyiapkan kamera",
            templateId=template_id,
            phaseStartedAt=time.time(),
            phaseDuration=0,
            outputVideo=None,
            outputPhoto=None,
            desktopSync=None,
            captureStats=None,
            cameraTransform=transform,
            cameraBackendRotationOffset=offset,
            sessionPreviewTransform=session_preview_transform,
            error=None,
        )

    set_live_preview_running(False, clear_frame=True)

    thread = threading.Thread(target=session_worker, args=(template_id, settings_snapshot), daemon=True)
    thread.start()
    return jsonify({
        "ok": True,
        "cameraTransform": transform,
        "cameraBackendRotationOffset": offset,
        "sessionPreviewTransform": session_preview_transform,
    })


@app.post("/api/session/cancel")
def api_session_cancel():
    global _cancel_requested, _current_process, _browser_session
    with _state_lock:
        _cancel_requested = True
        proc = _current_process
        pending_browser = _browser_session
        if pending_browser and pending_browser.get("status") == "recording":
            _browser_session = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    update_state(phase="idle", message="Cancelled", phaseStartedAt=None, phaseDuration=0)
    return jsonify({"ok": True})


@app.get("/api/session/workflow/<template_id>")
def api_session_workflow(template_id: str):
    template = load_template(template_id)
    capture_cfg = template.get("capture", {})
    take_count = max((int(s.get("sourceTake", 1)) for s in template.get("slots", [])), default=1)
    settings = load_settings()
    warmup = max(0.0, float(settings.get("cameraWarmupSeconds", 3.0)))
    return jsonify({
        "warmupSeconds": warmup,
        "countdownSeconds": float(capture_cfg.get("countdown", 5.0)),
        "preSessionDelay": float(capture_cfg.get("preSessionDelay", 3.0)),
        "captureDelay": max(0.05, float(capture_cfg.get("captureDelay", 0.25))),
        "betweenTakes": float(capture_cfg.get("betweenTakes", 1.0)),
        "snapshotLead": max(0.01, float(capture_cfg.get("snapshotLead", 0.05))),
        "snapshotMode": "last_frame_of_take",
        "inputFpsRequested": float(settings.get("cameraInputFps") or 0),
        "outputFps": int(template.get("video", {}).get("fps", DEFAULT_CAPTURE_FPS)),
        "previewFps": SESSION_PREVIEW_FPS,
        "takeCount": take_count
    })


@app.get("/api/session/live-frame")
def api_session_live_frame():
    """Return one fresh preview JPEG; used by a short-polling browser client.

    Some Windows/Chrome combinations show a broken image for a long-lived
    multipart MJPEG response. Individual JPEG responses are much more robust
    while still coming from the exact FFmpeg stream that owns the camera.
    """
    try:
        after = int(request.args.get("after", "-1"))
    except (TypeError, ValueError):
        after = -1

    with _preview_condition:
        if _preview_sequence <= after and _preview_running:
            _preview_condition.wait_for(
                lambda: _preview_sequence > after or not _preview_running,
                timeout=0.45,
            )
        frame = _preview_frame
        sequence = _preview_sequence
        running = _preview_running

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-Preview-Sequence": str(sequence),
    }
    if not running or frame is None or sequence <= after:
        return Response(status=204, headers=headers)
    return Response(frame, content_type="image/jpeg", headers=headers)


@app.get("/api/session/live-preview")
def api_session_live_preview():
    def frames():
        last_sequence = -1
        started = False
        start_deadline = time.monotonic() + 20.0
        while True:
            with _preview_condition:
                _preview_condition.wait_for(
                    lambda: _preview_sequence != last_sequence or (started and not _preview_running),
                    timeout=1.0,
                )
                frame = _preview_frame
                sequence = _preview_sequence
                running = _preview_running

            changed = sequence != last_sequence
            last_sequence = sequence
            if running:
                started = True

            if running and frame is not None and changed:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )

            if started and not running:
                break
            if not started and time.monotonic() >= start_deadline:
                break

    return Response(
        frames(),
        content_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )

@app.get("/api/session/status")
def api_session_status():
    return jsonify(get_state())


@app.get("/api/outputs")
def api_outputs():
    items = []
    candidates = [p for p in OUTPUTS_DIR.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".jpg", ".png"}]
    for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
        if p.suffix.lower() not in {".mp4", ".jpg", ".png"}:
            continue
        relative = output_relative_name(p)
        items.append({
            "name": p.name,
            "category": "Photos" if p.suffix.lower() in {".jpg", ".png"} else "Videos",
            "url": f"/outputs/{relative}",
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return jsonify(items)


@app.get("/outputs/<path:filename>")
def output_file(filename: str):
    return send_from_directory(OUTPUTS_DIR, filename)


@app.get("/data-files/<filename>")
def data_file(filename: str):
    return send_from_directory(DATA_DIR, filename)


if __name__ == "__main__":
    settings = load_settings()
    server = settings.get("server", {})
    host = str(server.get("host", "127.0.0.1"))
    port = int(server.get("port", 5050))
    app.run(host=host, port=port, debug=False, threaded=True)
