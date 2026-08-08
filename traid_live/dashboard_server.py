from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class TraidDashboardHandler(SimpleHTTPRequestHandler):
    """Static dashboard server with the same clean /chart route as Hosting."""

    server_version = "TraidDashboard/1.0"

    def _request_parts(self):
        return urlsplit(self.path)

    def _redirect_clean_chart(self) -> bool:
        parts = self._request_parts()
        if parts.path not in {"/chart/", "/chart.html"}:
            return False
        destination = urlunsplit(("", "", "/chart", parts.query, parts.fragment))
        self.send_response(308)
        self.send_header("Location", destination)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _serve_clean_chart(self):
        parts = self._request_parts()
        original_path = self.path
        try:
            # Rewrite only inside the server. The browser continues to display
            # /chart, so relative dashboard assets resolve from the site root.
            self.path = urlunsplit(("", "", "/chart.html", parts.query, ""))
            return super().send_head()
        finally:
            self.path = original_path

    def send_head(self):
        parts = self._request_parts()
        if parts.path == "/chart":
            return self._serve_clean_chart()
        return super().send_head()

    def do_GET(self) -> None:
        if self._redirect_clean_chart():
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._redirect_clean_chart():
            return
        super().do_HEAD()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Traid dashboard locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--directory", default="dashboard")
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        raise SystemExit(f"Dashboard directory not found: {directory}")

    handler = partial(TraidDashboardHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Traid dashboard serving http://{args.host}:{args.port}/")
    print(f"Chart terminal: http://{args.host}:{args.port}/chart")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
