"""Local lifecycle fixture. It has no model loader or generation endpoint."""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/version":
            if getattr(self.server, "delay_first_version", False):
                self.server.delay_first_version = False
                print("fixture-delaying-version", flush=True)
                time.sleep(22)
            print(
                f'time={datetime.now(timezone.utc).isoformat()} level=INFO msg="inference compute"',
                file=sys.stderr,
                flush=True,
            )
            body = {"version": "0.34.4"}
        elif self.path == "/api/ps":
            body = {"models": []}
        elif self.path == "/spawn-child":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                close_fds=True,
            )
            body = {"pid": child.pid}
        else:
            self.send_error(404)
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_POST(self):
        self.send_error(405, "generation disabled")

    def log_message(self, format_string, *args):
        print(format_string % args, flush=True)


if __name__ == "__main__":
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], close_fds=True
    )
    print("fixture-starting", flush=True)
    print(
        f'time={datetime.now(timezone.utc).isoformat()} level=INFO msg="discovering available GPUs..."',
        file=sys.stderr,
        flush=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    server.delay_first_version = "--delay-first-version" in sys.argv[2:]
    print("fixture-ready", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
