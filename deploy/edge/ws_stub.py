"""Tiny WebSocket identity endpoint used only by the access-plane proof."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socketserver


MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request = self.rfile.readline().decode("ascii", "replace")
        if not request:
            return
        headers: dict[str, str] = {}
        while line := self.rfile.readline().decode("ascii", "replace"):
            if line in ("\r\n", "\n"):
                break
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()

        key = headers.get("sec-websocket-key")
        if headers.get("upgrade", "").lower() != "websocket" or not key:
            self.wfile.write(b"HTTP/1.1 426 Upgrade Required\r\nContent-Length: 0\r\n\r\n")
            return

        accept = base64.b64encode(hashlib.sha1((key + MAGIC).encode()).digest()).decode()
        self.wfile.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        )
        payload = json.dumps({"seat": os.environ["SEAT_ID"]}).encode()
        if len(payload) >= 126:
            raise ValueError("stub identity payload must remain a short WebSocket frame")
        self.wfile.write(bytes((0x81, len(payload))) + payload)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("0.0.0.0", 7681), Handler) as server:
        server.serve_forever()
