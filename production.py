"""WSGI transport for Capture It Ops; HTTP parsing belongs to Waitress.

Reuses the existing API handlers without constructing a socket handler or running
http.server's request parser/static server. Importing this module does not create
a database or start schedulers. Run `python3 production.py` for the single-process
deployment; `python3 server.py` is retained for local development and admin CLI.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import signal
import tempfile
from email.message import Message
from email.utils import formatdate
from http import HTTPStatus
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

from waitress import serve

import server as ops

MAX_BODY_SIZE = 15 * 1024 * 1024
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "transfer-encoding", "upgrade"}
LOG = logging.getLogger(__name__)


class WSGIHandler(ops.Handler):
    """Adapt business handlers to WSGI, preserving duplicate Set-Cookie headers."""

    def __init__(self, environ):
        # Deliberately do not call BaseHTTPRequestHandler.__init__: no sockets here.
        self.command = environ["REQUEST_METHOD"]
        self.decoded_path = environ.get("PATH_INFO", "/").encode("latin-1").decode("utf-8", "replace")
        self.path = "/" + quote(self.decoded_path, safe="/:").lstrip("/")
        if environ.get("QUERY_STRING"):
            self.path += "?" + environ["QUERY_STRING"]
        self.client_address = (environ.get("REMOTE_ADDR", ""), 0)
        self.requestline = f"{self.command} {self.path}"
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[5:].replace("_", "-")] = value
        for key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if key in environ:
                self.headers[key.replace("_", "-")] = environ[key]
        self.rfile = environ["wsgi.input"]
        # Large responses spill to disk, so downloads don't accumulate another
        # unbounded in-memory copy while waiting for the client.
        self.wfile = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        self.status = 200
        self.response_headers = []

    def send_response(self, code, message=None):
        self.status = int(code)
        self.response_headers = []
        self.wfile.seek(0)
        self.wfile.truncate()

    def send_header(self, keyword, value):
        keyword, value = str(keyword), str(value)
        if "\r" in keyword + value or "\n" in keyword + value:
            raise ValueError("Invalid response header")
        if keyword.lower() in HOP_BY_HOP:
            raise ValueError("Hop-by-hop headers belong to the WSGI server")
        self.response_headers.append((keyword, value))

    def end_headers(self):
        for name, value in ops.SECURITY_HEADERS:
            self.send_header(name, value)

    def send_error(self, code, message=None, explain=None):
        self.json_error(code, message or HTTPStatus(code).phrase)

    def client_ip(self):
        # Waitress has already validated the proxy chain and set REMOTE_ADDR.
        # Never interpret an untrusted X-Forwarded-For again in application code.
        return str(self.client_address[0])[:64]

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/") or path in {"/push-config.js", "/favicon.ico"}:
            return ops.Handler.do_GET(self)
        self.serve_static()

    def serve_static(self):
        relative = self.decoded_path.lstrip("/") or "index.html"
        parts = PurePosixPath(relative).parts
        if "\x00" in relative or "\\" in relative or any(p.startswith(".") for p in parts):
            self.send_error(404)
            return
        root = ops.STATIC.resolve()
        path = (root / relative).resolve()
        # No directory listings, dotfiles, or symlinks escaping the static root.
        if not path.is_relative_to(root) or not path.is_file():
            self.send_error(404)
            return
        try:
            with path.open("rb") as source:
                self.send_response(200)
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                self.send_header("Last-Modified", formatdate(os.fstat(source.fileno()).st_mtime, usegmt=True))
                self.end_headers()
                if self.command != "HEAD":
                    shutil.copyfileobj(source, self.wfile)
        except OSError:
            self.send_error(404)


def _response_body(stream):
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


def application(environ, start_response):
    handler = WSGIHandler(environ)
    try:
        if handler.command in {"GET", "HEAD"}:
            handler.do_GET()
        elif handler.command == "POST":
            handler.do_POST()
        else:
            handler.json_response({"ok": False, "error": "Metode tidak diizinkan."},
                                  405, {"Allow": "GET, HEAD, POST"})
    except Exception:
        LOG.exception("Request failed")
        handler.json_error(500, "Terjadi kesalahan saat memproses permintaan.")
    try:
        if not any(name.lower() == "content-length" for name, _ in handler.response_headers):
            handler.send_header("Content-Length", str(handler.wfile.tell()))
        start_response(f"{handler.status} {HTTPStatus(handler.status).phrase}", handler.response_headers)
        if handler.command == "HEAD" or handler.status in {204, 304}:
            handler.wfile.close()
            return []
        handler.wfile.seek(0)
        return _response_body(handler.wfile)
    except BaseException:
        handler.wfile.close()
        raise


def server_options():
    options = dict(host=ops.HOST, port=ops.PORT, threads=4, connection_limit=64,
                   backlog=64, channel_timeout=60, cleanup_interval=10,
                   max_request_body_size=MAX_BODY_SIZE, max_request_header_size=32768,
                   inbuf_overflow=128 * 1024, outbuf_overflow=128 * 1024,
                   outbuf_high_watermark=1024 * 1024, expose_tracebacks=False,
                   clear_untrusted_proxy_headers=True)
    # Docker publishes this port only on 127.0.0.1 and uses a dedicated bridge.
    # Other deployments must set an exact trusted peer or leave this unset.
    proxy = os.environ.get("OPS_TRUSTED_PROXY", "").strip()
    if proxy:
        options.update(trusted_proxy=proxy, trusted_proxy_count=1,
                       trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"})
    return options


def main():
    logging.basicConfig(level=logging.INFO)
    ops.configure_runtime()
    if ops.DEMO_MODE:
        raise SystemExit("Production server requires DEMO_MODE=false.")
    ops.initialize()
    ops.start_background_tasks()

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    serve(application, **server_options())


if __name__ == "__main__":
    main()
