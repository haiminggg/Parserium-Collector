"""Bounded fixture-only HTTP harness. Authentication happens at the Worker."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != '/run':
            self.send_error(404)
            return
        try:
            run = subprocess.run([sys.executable, '/trial/measure.py'],
                                 capture_output=True, text=True, timeout=60)
            data = json.loads(run.stdout) if run.returncode == 0 else {'error': 'Parser failed', 'detail': run.stderr[-3000:]}
            status = 200 if run.returncode == 0 else 500
        except (subprocess.TimeoutExpired, ValueError):
            status, data = 500, {'error': 'Trial timed out or returned invalid output'}
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    watchdog = threading.Timer(120, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
