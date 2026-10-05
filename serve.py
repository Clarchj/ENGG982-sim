"""Local web service: POST a JSON request to /api, get the JSON response. GET / serves the web page. Standard library only.
    python serve.py [port]        then open http://localhost:8080/
Binds to 127.0.0.1 only. No authentication: do not expose it beyond your own machine. Advice only: no write path to equipment."""
import http.server
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'engine'))
import twin_api as T  # noqa: E402

MAX_BODY = 200_000


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=os.path.join(HERE, 'web'), **k)

    def do_POST(self):
        if self.path != '/api':
            self.send_error(404); return
        n = int(self.headers.get('Content-Length', 0))
        if n > MAX_BODY:
            self.send_error(413); return
        out = json.dumps(T.run(self.rfile.read(n).decode('utf-8')), default=float).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f'Haul Twin service on http://127.0.0.1:{port}/  (POST /api)')
    http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
