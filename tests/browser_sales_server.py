"""Disposable browser QA fixture, served through the production transport."""
import contextlib
from datetime import datetime,timedelta
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as ops
from operations import WIB
from wsgi_support import WSGITestServer

with tempfile.TemporaryDirectory(prefix='captureit-sales-browser-') as folder:
    ops.DB_PATH=Path(folder)/'qa.sqlite3';ops.DEMO_MODE=True;ops.initialize(seed=True)
    with contextlib.redirect_stdout(sys.stderr):ops.add_user('sales-browser@qa.local','Sales Browser','sales_staff','BrowserQA!2026')
    today=datetime.now(WIB).date().isoformat();tomorrow=(datetime.now(WIB)+timedelta(days=1)).date().isoformat()
    with ops.get_db() as conn:
        conn.execute("INSERT INTO calendar_code_queue(google_event_id,title,event_type,starts_at,ends_at,location,is_full_day,status) VALUES('browser-manual-time','QA Calendar manual','Corporate',?,?,'Jakarta',0,'scheduled')",(today+'T00:00:00+07:00',tomorrow+'T00:00:00+07:00'))
    httpd=WSGITestServer(('127.0.0.1',0))
    print(f'BROWSER_QA_URL=http://127.0.0.1:{httpd.server_port}',flush=True)
    try:httpd.serve_forever()
    finally:httpd.shutdown()
