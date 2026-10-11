#!/usr/bin/env python3
"""PHASE 2 — Run the Chhaon Lambda handler locally, no AWS needed.

Wraps `src/api/app.py:handler` in a tiny HTTP server so you can hit the same
endpoints with curl during development. Uses the local sample (or real) graph
via GRAPH_LOCAL_PATH.

Usage:
    python scripts/run_local.py                       # http://127.0.0.1:8000
    python scripts/run_local.py --port 9000 --graph data/graph.json.gz
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "api"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Local dev server for the Chhaon API.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--graph", default=os.path.join(ROOT, "data", "graph.json.gz"))
    args = ap.parse_args()

    os.environ.setdefault("GRAPH_LOCAL_PATH", os.path.abspath(args.graph))
    os.environ.setdefault("ALLOWED_ORIGIN", "*")
    # Leave BEDROCK_MODEL_ID unset locally -> template advice (no AWS call).

    import app as chhaon  # noqa: E402

    class Handler(BaseHTTPRequestHandler):
        def _run(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            event = {
                "requestContext": {"http": {"method": method}},
                "httpMethod": method,
                "rawPath": self.path.split("?")[0],
                "path": self.path.split("?")[0],
                "body": raw,
            }
            result = chhaon.handler(event)
            body = result["body"].encode("utf-8")
            self.send_response(result["statusCode"])
            for k, v in result.get("headers", {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            self._run("GET")

        def do_POST(self):
            self._run("POST")

        def do_OPTIONS(self):
            self._run("OPTIONS")

        def log_message(self, fmt, *a):  # quieter logs
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Chhaon local API on http://127.0.0.1:{args.port}")
    print(f"  graph: {os.environ['GRAPH_LOCAL_PATH']}")
    print("  try:  curl -s http://127.0.0.1:%d/health" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
