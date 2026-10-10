"""Real child-process crash test with a FILE sink, never user32.SendInput."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cs2music.ptt import watchdog

if sys.argv[1] == "parent":
    state, output = map(Path, sys.argv[2:4])
    state.write_text(json.dumps({"vk": 124, "held": True, "heartbeat": time.time()}))
    subprocess.Popen([sys.executable, __file__, "child", str(os.getpid()), str(state), str(output)],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    print("ready", flush=True)
    time.sleep(300)
else:
    parent, state, output = int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    def record(vk, down):
        output.write_text(json.dumps({"vk": vk, "down": down}))
    watchdog(parent, state, release=record)
