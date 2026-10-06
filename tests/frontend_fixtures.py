"""Synthetic HTTP fixtures for Node rendering tests; never uses the real app DB."""
import contextlib
import json
import sys
import tempfile
import threading
from pathlib import Path

from test_workflows import Client, ops


def fixtures():
    with tempfile.TemporaryDirectory() as folder:
        ops.DB_PATH = Path(folder) / 'ui-qa.sqlite3'
        ops.initialize(seed=True)
        server = ops.ThreadingHTTPServer(('127.0.0.1', 0), ops.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        results = []
        try:
            for role, _ in ops.ROLE_LIST:
                with ops.get_db() as conn:
                    person = conn.execute('''SELECT u.email FROM users u JOIN user_roles ur ON ur.user_id=u.id
                        JOIN roles r ON r.id=ur.role_id WHERE r.code=? LIMIT 1''', (role,)).fetchone()
                email = person['email'] if person else f'{role}@qa.local'
                password = 'demo1234' if person else 'RenderFixture!2026'
                if not person:
                    with contextlib.redirect_stdout(sys.stderr):
                        ops.add_user(email, 'QA ' + role, role, password)
                client = Client(f'http://127.0.0.1:{server.server_port}')
                client.login(email, password)
                status, _, body = client.request('/api/bootstrap')
                assert status == 200, body
                bootstrap = json.loads(body)
                details = []
                for event in bootstrap['events'][:2]:
                    status, _, body = client.request(f"/api/events/{event['id']}")
                    assert status == 200, body
                    details.append(json.loads(body))
                extra = {}
                for route in ['/api/payroll?start=2026-09-26&end=2026-10-02',
                              '/api/inhouse-attendance?date=2026-09-30',
                              '/api/inhouse-payroll?start=2026-08-01&end=2026-08-31',
                              '/api/inhouse-payroll/slips', '/api/payroll/slips']:
                    status, _, body = client.request(route)
                    if status == 200:
                        extra[route.split('?')[0]] = json.loads(body)
                results.append({'role': role, 'bootstrap': bootstrap, 'details': details, 'extra': extra})
        finally:
            server.shutdown(); server.server_close(); thread.join()
        return results


if __name__ == '__main__':
    print(json.dumps(fixtures(), ensure_ascii=False))
