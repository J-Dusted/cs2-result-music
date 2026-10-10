"""Real child-process crash test with a FILE sink, never user32.SendInput."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cs2music.ptt import watchdog
from cs2music.hotkeys import parse_hotkey

if sys.argv[1] == "parent":
    state, output = map(Path, sys.argv[2:4])
    payload = {"vk": 124, "held": True, "heartbeat": time.time()}
    if len(sys.argv) > 4:
        payload["keys"] = parse_hotkey(sys.argv[4])[1]
    state.write_text(json.dumps(payload))
    subprocess.Popen([sys.executable, __file__, "child", str(os.getpid()), str(state), str(output)],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    print("ready", flush=True)
    time.sleep(300)
else:
    parent, state, output = int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    def record(vk, down):
        entry = {"vk": vk, "down": down}
        if "keys" in json.loads(state.read_text()):
            previous = json.loads(output.read_text()) if output.exists() else []
            output.write_text(json.dumps(previous + [entry]))
        else:
            output.write_text(json.dumps(entry))
    watchdog(parent, state, release=record)
