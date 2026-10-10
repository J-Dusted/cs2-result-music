from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class GSIServer:
    def __init__(self, port: int = 8765):
        self.messages: queue.Queue[tuple[float, dict]] = queue.Queue(maxsize=256)
        self.dropped = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/gsi":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024 * 1024:
                        self.send_error(413)
                        return
                    self.connection.settimeout(2)
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("expected object")
                    # Validate the nested structures used by the state machine.
                    for key in ("provider", "map", "round", "player", "bomb", "allplayers", "phase_countdowns"):
                        if key in payload and not isinstance(payload[key], dict):
                            raise ValueError(key)
                    for key in ("team_ct", "team_t"):
                        if key in payload.get("map", {}) and not isinstance(payload["map"][key], dict):
                            raise ValueError(key)
                    for player in [payload.get("player", {})] + list(payload.get("allplayers", {}).values()):
                        if not isinstance(player, dict) or not isinstance(player.get("match_stats", {}), dict):
                            raise ValueError("invalid player stats")
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                try:
                    owner.messages.put_nowait((time.monotonic(), payload))
                except queue.Full:
                    # Never block CS2 behind playback or decode work.
                    owner.dropped += 1
                    self.send_error(503, "State queue full; retry with fresh state")
                    return
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="gsi-http", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
