"""Exercise real DSP/RNNoise with simulated independent device clocks.

By default 30 minutes of frames are accelerated; --realtime uses wall time.
This is explicitly NOT a physical hardware/TeamSpeak/network acceptance test.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from cs2music.audio import AudioRing, RNNoise, VoiceGate, FRAME, RATE


def run(minutes: float, realtime: bool):
    frame_count = int(minutes * 60 * RATE / FRAME)
    mic = AudioRing(1, 44100)
    head, cable = AudioRing(2), AudioRing(2)
    mic.write(np.zeros((2646, 1), np.float32))
    for ring in (head, cable):
        ring.write(np.zeros((2400, 2), np.float32))
    noise, gate = RNNoise(), VoiceGate()
    random = np.random.default_rng(17)
    raw = random.normal(0, .006, (442, 1)).astype(np.float32)
    music = np.repeat((.08 * np.sin(np.arange(FRAME) * .06)).astype(np.float32)[:, None], 2, axis=1)
    head_clock = mic_clock = 0.0
    peak = 0.0
    max_occupancy = [0, 0, 0]
    begin = time.perf_counter()
    for index in range(frame_count):
        # +150 ppm microphone at 44.1 kHz, +200 ppm headphones at 48 kHz.
        mic_clock += 441 * 1.00015
        capture_frames = int(mic_clock)
        mic_clock -= capture_frames
        mic.write(raw[:capture_frames])
        captured = mic.read(FRAME, RATE, target=2646).reshape(FRAME)
        denoised, probability = noise.process(captured)
        voice, speaking, _ = gate.process(denoised, -42, probability, .55)
        mixed = np.clip(voice[:, None] + music * (.25 if speaking else 1), -.98, .98)
        cable.write(mixed)
        head.write(music)
        sent = cable.read(FRAME, RATE, correct_drift=False)
        head_clock += FRAME * 1.0002
        headphone_frames = int(head_clock)
        head_clock -= headphone_frames
        heard = head.read(headphone_frames, RATE)
        if not np.isfinite(sent).all() or not np.isfinite(heard).all():
            raise RuntimeError("non-finite output")
        peak = max(peak, float(np.max(np.abs(sent))))
        max_occupancy = [max(v, ring.available) for v, ring in zip(max_occupancy, (mic, head, cable))]
        if realtime:
            delay = begin + (index + 1) * .01 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        if index and index % 30000 == 0:
            print(f"Simulated {index * .01 / 60:.0f} minutes", flush=True)
    noise.close()
    report = {
        "mode": "real-time simulated devices" if realtime else "accelerated simulated devices",
        "simulated_minutes": minutes, "frames": frame_count,
        "elapsed_seconds": round(time.perf_counter() - begin, 3),
        "mic_clock_ppm": 150, "headphone_clock_ppm": 200,
        "mic_sample_rate": 44100, "internal_sample_rate": RATE,
        "peak": peak, "max_buffer_samples": max_occupancy,
        "underflows": sum(r.underflows for r in (mic, head, cable)),
        "overflows": sum(r.overflows for r in (mic, head, cable)),
        "physical_audio_tested": False, "teamspeak_tested": False,
    }
    destination = ROOT / "runtime/soak-report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["underflows"] or report["overflows"]:
        raise RuntimeError("clock drift compensation did not keep buffers stable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--realtime", action="store_true")
    args = parser.parse_args()
    if not 0 < args.minutes <= 240:
        parser.error("minutes must be between 0 and 240")
    run(args.minutes, args.realtime)
