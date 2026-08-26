#!/usr/bin/env python3

import argparse
import http.server
import socketserver
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Large Cap P/L calendar locally.")
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to try first (default: 8000). If unavailable, will try the next ports.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host/interface to bind (default: 127.0.0.1).",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]

    class Handler(http.server.SimpleHTTPRequestHandler):
        # Serve from repo root so large_cap_pnl.html can fetch ./gainsandlosses_enriched.csv
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    print("Serving Large Cap P/L calendar from:", root)

    last_err: Exception | None = None
    for port in range(args.port, args.port + 20):
        try:
            with Server((args.host, port), Handler) as httpd:
                print(f"Open: http://localhost:{port}/large_cap_pnl.html")
                httpd.serve_forever()
                return
        except Exception as e:  # noqa: BLE001 - want to try multiple ports
            last_err = e
            continue

    print("Could not bind a local port to serve the calendar.")
    if last_err:
        print("Last error:", repr(last_err))
    print("Fallback: from repo root run:")
    print("  python3 -m http.server 8000 --bind 127.0.0.1")


if __name__ == "__main__":
    main()

