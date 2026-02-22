#!/usr/bin/env python3
"""
Local search API server for the website.

Serves:
  GET /api/search?q=QUERY[&source=discord|openclaw|all][&top_k=N][&min_score=F][&channel=X]
  → JSON: [{score, source, channel, author, timestamp, text, link, message_id?, session_id?, turn_idx?}]

Also serves static files from website/ so you can open http://localhost:8765 directly.

Usage:
    python3 scripts/serve_search.py [--port 8765]
"""
import argparse
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

ROOT       = pathlib.Path(__file__).resolve().parent.parent
WEBSITE    = ROOT / "website"
SCRIPTS    = ROOT / "scripts"

# lazy-loaded semantic search function
_search_fn = None


def get_search_fn():
    global _search_fn
    if _search_fn is None:
        sys.path.insert(0, str(SCRIPTS))
        from search_semantic import search as _fn
        _search_fn = _fn
    return _search_fn


def do_search(q, source="all", top_k=10, min_score=0.3, channel=None):
    fn = get_search_fn()
    results = fn(q, source=source, top_k=top_k, min_score=min_score, channel=channel)
    out = []
    for score, meta in results:
        src = meta["source"]
        entry = {
            "score": round(score, 4),
            "source": src,
            "timestamp": meta.get("timestamp", "")[:16],
            "text": meta.get("text", ""),
        }
        if src == "discord":
            entry["channel"] = meta.get("channel", "")
            entry["author"] = meta.get("author", "")
            entry["message_id"] = meta.get("message_id", "")
            entry["link"] = f"logs.html#msg-{meta.get('message_id','')}"
        else:
            entry["session_id"] = meta.get("session_id", "")
            entry["turn_idx"] = meta.get("turn_idx", "")
            entry["role"] = meta.get("role", "")
            entry["link"] = f"sessions.html#sess-{meta.get('session_id','')}"
        out.append(entry)
    return out


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css",
    ".js":   "application/javascript",
    ".json": "application/json",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access log

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ── API ─────────────────────────────────────────────
        if path == "/api/search":
            qs = parse_qs(parsed.query)
            q       = (qs.get("q", [""])[0]).strip()
            source  = qs.get("source", ["all"])[0]
            top_k   = int(qs.get("top_k", ["10"])[0])
            min_score = float(qs.get("min_score", ["0.3"])[0])
            channel = qs.get("channel", [None])[0]

            if not q:
                self.send_json({"error": "missing q parameter"}, 400)
                return

            try:
                results = do_search(q, source=source, top_k=top_k,
                                    min_score=min_score, channel=channel)
                self.send_json(results)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # ── Static files ─────────────────────────────────────
        if path == "/" or path == "":
            path = "/index.html"

        file_path = WEBSITE / path.lstrip("/")
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext  = file_path.suffix.lower()
        mime = MIME.get(ext, "application/octet-stream")
        body = file_path.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="Local search API + static file server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    print(f"Pre-loading embedding model…", flush=True)
    get_search_fn()   # warm up model + indexes
    print(f"✓ Model ready.")
    print(f"Open: http://localhost:{args.port}")
    print(f"Search API: http://localhost:{args.port}/api/search?q=your+query")
    print("Press Ctrl+C to stop.")

    server = HTTPServer(("", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
