"""Local dev server for the frontend.

    python serve_frontend.py            # http://127.0.0.1:5500

Use this instead of `python -m http.server 5500`. Two reasons, and the first one
costs real debugging time:

1. NO CACHING. `http.server` sends Last-Modified and no Cache-Control, which leaves
   the browser to guess a freshness window (the HTTP spec's heuristic: roughly 10%
   of the document's age). It then serves the page from cache WITHOUT asking the
   server. The result is that an edit shows up on one navigation and not the next,
   which reads as "the new section is sometimes there and sometimes isn't" rather
   than as a caching problem. Cache-Control: no-store settles it.

2. PORT 5500. The API's CORS list (backend/main.py DEFAULT_ORIGINS) allows 5500 and
   nothing else, so a frontend served anywhere else has every fetch blocked by the
   browser while curl against the same API succeeds — which looks like the backend
   is down when it is fine.

This file lives at the repo root ON PURPOSE. Anything inside frontend/ is published
by wrangler, which ignores .gitignore.
"""

import functools
import http.server
import socketserver
from pathlib import Path

PORT = 5500
ROOT = Path(__file__).resolve().parent / "frontend"


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        # One line per request, without the date noise SimpleHTTPRequestHandler adds.
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main():
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
        print(f"frontend  http://127.0.0.1:{PORT}   (serving {ROOT}, caching off)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
