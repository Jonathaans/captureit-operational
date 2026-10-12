"""Disposable Waitress server for the browser attendance smoke test; no production data."""
import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as ops
from wsgi_support import WSGITestServer

with tempfile.TemporaryDirectory(prefix='captureit-browser-') as folder:
    ops.DB_PATH = Path(folder) / 'qa.sqlite3'
    ops.DEMO_MODE = True
    ops.initialize(seed=True)
    for kind in ('desktop','mobile'):
        with contextlib.redirect_stdout(sys.stderr):
            ops.add_user(f'browser-{kind}@qa.local', f'Browser {kind}', 'design_team', 'BrowserQA!2026')
    httpd = WSGITestServer(('127.0.0.1',0))
    print(f'BROWSER_QA_URL=http://127.0.0.1:{httpd.server_port}', flush=True)
    try: httpd.serve_forever()
    finally: httpd.shutdown()
