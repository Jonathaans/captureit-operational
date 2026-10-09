"""Replay the existing backend regression suite using real Waitress sockets.

    python3 tests/run_wsgi_suite.py
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server
from wsgi_support import WSGITestServer

if __name__ == "__main__":
    with patch.object(server, "ThreadingHTTPServer", WSGITestServer):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
