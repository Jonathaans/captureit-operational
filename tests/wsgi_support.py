"""Run the existing HTTP workflow tests against the deployed WSGI transport."""
from waitress.server import create_server
from waitress import wasyncore

import production


class WSGITestServer:
    def __init__(self, address, handler=None, **overrides):
        options = production.server_options()
        options.update(host=address[0], port=address[1], asyncore_loop_timeout=0.05)
        options.update(overrides)
        self.channels = {}
        self.server = create_server(production.application, map=self.channels, **options)
        self.server_port = int(self.server.effective_port)

    def serve_forever(self):
        self.server.run()

    def shutdown(self):
        self.server.task_dispatcher.shutdown()
        wasyncore.close_all(map=self.channels)

    def server_close(self):
        pass
