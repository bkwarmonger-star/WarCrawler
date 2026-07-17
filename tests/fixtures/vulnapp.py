"""
Deliberately-vulnerable local test app — the offline target for aegis/active + aegis/discover
self-tests. Stdlib-only (wsgiref). Serves on 127.0.0.1 so the scope guard permits it.

Intentional flaws (so probes have something true to find):
  GET /echo?q=      -> reflects q UNENCODED         (reflected XSS)
  GET /item?id=     -> id with a quote -> SQL error  (error-based SQLi)
  GET /go?url=      -> 302 Location: <url>           (open redirect)
  GET /.env         -> leaks a fake secret           (exposed file)
  GET /api/openapi.json -> tiny spec                 (API discovery)
  GET /             -> benign (must yield NO findings)

This exists ONLY as a test target. It is not part of the shipped product.
"""
import threading
from wsgiref.simple_server import WSGIServer, make_server
from wsgiref.util import setup_testing_defaults
from urllib.parse import parse_qs


def app(environ, start_response):
    setup_testing_defaults(environ)
    path = environ.get("PATH_INFO", "/")
    qs = parse_qs(environ.get("QUERY_STRING", ""))

    def send(status, body, headers=None):
        h = headers or [("Content-Type", "text/html; charset=utf-8")]
        start_response(status, h)
        return [body.encode() if isinstance(body, str) else body]

    if path == "/echo":
        q = (qs.get("q") or [""])[0]
        return send("200 OK", "<html><body>you said: %s</body></html>" % q)  # UNENCODED reflect
    if path == "/item":
        i = (qs.get("id") or [""])[0]
        if "'" in i or '"' in i:
            return send("500 Internal Server Error",
                        "SQL syntax error near '%s': You have an error in your SQL syntax" % i)
        return send("200 OK", "<html><body>item %s</body></html>" % i)
    if path == "/go":
        url = (qs.get("url") or ["/"])[0]
        return send("302 Found", "", [("Location", url), ("Content-Type", "text/html")])
    if path == "/.env":
        return send("200 OK", "DB_PASSWORD=hunter2hunter2\nAPI_KEY=AKIAEXAMPLEEXAMPLE12\n",
                    [("Content-Type", "text/plain")])
    if path == "/api/openapi.json":
        return send("200 OK",
                    '{"openapi":"3.0.0","paths":{"/item":{"get":{"parameters":['
                    '{"name":"id","in":"query"}]}}}}',
                    [("Content-Type", "application/json")])
    return send("200 OK", "<html><body>welcome — nothing to see here</body></html>")


class _Server(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.httpd: WSGIServer = make_server("127.0.0.1", 0, app)
        self.port = self.httpd.server_address[1]

    def run(self):
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()


def serve():
    """Start the app on an ephemeral 127.0.0.1 port. Returns (base_url, stop_fn)."""
    s = _Server()
    s.start()
    return "http://127.0.0.1:%d" % s.port, s.stop


if __name__ == "__main__":
    base, stop = serve()
    print("vulnapp serving at", base)
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
