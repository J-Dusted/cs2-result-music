from __future__ import annotations

import ctypes
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly

from .config import Settings, resource_path

RATE = 48000
FRAME = 480


class AudioRing:
    """Bounded, single-reader ring with fractional resampling and clock correction."""
    def __init__(self, channels: int, sample_rate: float = RATE, capacity: int = RATE * 2):
        self.data = np.zeros((capacity, channels), dtype=np.float32)
        self.capacity, self.channels, self.sample_rate = capacity, channels, sample_rate
        self.start = 0
        self.count = 0
        self.fraction = 0.0
        self.overflows = 0
        self.underflows = 0
        self.lock = threading.Lock()

    @property
    def available(self) -> int:
        with self.lock:
            return self.count

    def clear(self):
        with self.lock:
            self.start = self.count = 0
            self.fraction = 0.0

    def write(self, samples: np.ndarray):
        samples = np.asarray(samples, dtype=np.float32).reshape(-1, self.channels)
        with self.lock:
            if len(samples) >= self.capacity:
                samples = samples[-self.capacity:]
                self.start = self.count = 0
                self.fraction = 0.0
                self.overflows += 1
            excess = max(0, self.count + len(samples) - self.capacity)
            if excess:
                self.start = (self.start + excess) % self.capacity
                self.count -= excess
                self.overflows += 1
            end = (self.start + self.count) % self.capacity
            split = min(len(samples), self.capacity - end)
            self.data[end:end + split] = samples[:split]
            if split < len(samples):
                self.data[:len(samples) - split] = samples[split:]
            self.count += len(samples)

    def read(self, frames: int, output_rate: float = RATE, target: int = 2400,
             correct_drift: bool = True) -> np.ndarray:
        with self.lock:
            output = np.zeros((frames, self.channels), np.float32)
            correction = np.clip((self.count - target) / max(target, 1) * 0.002, -0.005, 0.005) if correct_drift else 0
            ratio = self.sample_rate / output_rate * (1 + correction)
            positions = self.fraction + np.arange(frames) * ratio
            valid = positions < max(0, self.count - 1)
            if not valid.all():
                self.underflows += 1
            if valid.any():
                p = positions[valid]
                lo = np.floor(p).astype(np.int64)
                weight = (p - lo).astype(np.float32)[:, None]
                a = self.data[(self.start + lo) % self.capacity]
                b = self.data[(self.start + lo + 1) % self.capacity]
                output[valid] = a + (b - a) * weight
            consumed = min(self.count, int(self.fraction + frames * ratio))
            self.start = (self.start + consumed) % self.capacity
            self.count -= consumed
            self.fraction = self.fraction + frames * ratio - consumed if valid.all() else 0.0
            return output


class RNNoise:
    def __init__(self, path: Path | None = None):
        path = path or resource_path("assets/rnnoise/rnnoise.dll")
        if not path.exists():
            raise FileNotFoundError("缺少 RNNoise DLL。请运行 scripts/vendor_rnnoise.py 或使用完整发行包。")
        self.dll = ctypes.CDLL(str(path))
        self.dll.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.dll.rnnoise_create.restype = ctypes.c_void_p
        self.dll.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.dll.rnnoise_destroy.restype = None
        self.dll.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self.dll.rnnoise_process_frame.restype = ctypes.c_float
        self.state = self.dll.rnnoise_create(None)
        if not self.state:
            raise RuntimeError("RNNoise 初始化失败")

    def process(self, samples: np.ndarray) -> tuple[np.ndarray, float]:
        source = np.ascontiguousarray(samples.reshape(FRAME) * 32768, dtype=np.float32)
        output = np.empty(FRAME, np.float32)
        probability = self.dll.rnnoise_process_frame(self.state, output.ctypes.data, source.ctypes.data)
        return output / 32768, float(probability)

    def close(self):
        if self.state:
            self.dll.rnnoise_destroy(self.state)
            self.state = None


class VoiceGate:
    def __init__(self):
        self.remaining = 0
        self.gain = 0.0

    def process(self, samples: np.ndarray, threshold_db: float, probability: float | None,
                vad_threshold: float) -> tuple[np.ndarray, bool, float]:
        rms = float(np.sqrt(np.mean(samples * samples) + 1e-15))
        level = 20 * math.log10(max(rms, 1e-9))
        voice = level >= threshold_db and (probability is None or probability >= vad_threshold)
        if voice:
            self.remaining = 20  # 200 ms speech hangover
        else:
            self.remaining = max(0, self.remaining - 1)
        active = self.remaining > 0
        target = 1.0 if active else 0.0
        envelope = np.linspace(self.gain, target, len(samples), dtype=np.float32)
        self.gain = target
        return samples * envelope, active, level


def load_track(path: str) -> np.ndarray:
    with sf.SoundFile(path) as file:
        if file.frames / file.samplerate > 15 * 60:
            raise ValueError("首版支持最长 15 分钟的单个音乐文件；请先裁剪更长的文件")
        data = file.read(dtype="float32", always_2d=True)
        sample_rate = file.samplerate
    if not len(data):
        raise ValueError("音乐文件为空")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = np.repeat(data.mean(axis=1, keepdims=True), 2, axis=1)
    if sample_rate != RATE:
        divisor = math.gcd(sample_rate, RATE)
        data = resample_poly(data, RATE // divisor, sample_rate // divisor).astype(np.float32)
    data = np.nan_to_num(data, copy=False)
    peak = float(np.max(np.abs(data)))
    if peak > 0.95:
        data *= 0.95 / peak
    fade = min(int(RATE * 0.05), len(data) // 2)
    if fade:
        data[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        data[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    return np.ascontiguousarray(data)


@dataclass
class Device:
    index: int
    label: str
    inputs: int
    outputs: int
    rate: float


def devices() -> list[Device]:
    apis = sd.query_hostapis()
    # WASAPI shared mode supports coexistence with CS2 and TS.
    all_devices = sd.query_devices()
    preferred = [i for i, d in enumerate(all_devices) if "WASAPI" in apis[d["hostapi"]]["name"]]
    indexes = preferred or list(range(len(all_devices)))
    return [Device(i, f'{all_devices[i]["name"]} [{apis[all_devices[i]["hostapi"]]["name"]}]',
                   all_devices[i]["max_input_channels"], all_devices[i]["max_output_channels"],
                   all_devices[i]["default_samplerate"]) for i in indexes]


def resolve_device(label: str, capture: bool) -> Device:
    for device in devices():
        if device.label == label and (device.inputs if capture else device.outputs):
            return device
    raise ValueError(f"设备不可用，请重新选择：{label or '未选择设备'}")


class AudioEngine:
    """Decode on a worker; mix on a DSP thread; device callbacks only move PCM."""
    def __init__(self, settings: Settings):
        self.s = settings
        self.running = False
        self.muted = False
        self.speaking = False
        self.level_db = -90.0
        self.event = ""
        self.error = ""
        self.stream_errors = 0
        self.music_active = False
        self.send_music = False
        self.duck_gain = 1.0
        self._gate = VoiceGate()
        self._noise: RNNoise | None = None
        self._streams = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._decode = ThreadPoolExecutor(max_workers=1, thread_name_prefix="music-decode")
        self._track_lock = threading.Lock()
        self._generation = 0
        self._pcm: np.ndarray | None = None
        self._position = 0
        self._event_gain = 1.0
        self._tail: np.ndarray | None = None
        self._tail_pos = 0
        self._tail_transmit = False
        self._head: AudioRing | None = None
        self._cable: AudioRing | None = None
        self._mic: AudioRing | None = None
        self._cached_path = ""
        self._cached_pcm: np.ndarray | None = None

    def start(self):
        if self.running:
            return
        mic = resolve_device(self.s.mic_device, True)
        head = resolve_device(self.s.headphone_device, False)
        cable = resolve_device(self.s.cable_device, False)
        if head.index == cable.index:
            raise ValueError("耳机与虚拟输出必须是不同设备")
        if "CABLE INPUT" not in cable.label.upper():
            raise ValueError("虚拟输出请选择 VB-CABLE 的 CABLE Input。未安装时请先安装驱动并重启。")
        if "CABLE" in mic.label.upper() or "VOICEMEETER" in mic.label.upper():
            raise ValueError("真实麦克风不能选择混音回路的虚拟输出，请选择物理麦克风")
        if self.s.denoise:
            self._noise = RNNoise()
        self._mic = AudioRing(1, mic.rate, capacity=int(mic.rate * 2))
        self._head, self._cable = AudioRing(2), AudioRing(2)
        self._stop.clear()
        self.error = ""
        self._gate = VoiceGate()
        self.duck_gain = 1.0

        def capture(indata, _frames, _time, status):
            if status:
                self.stream_errors += 1
            self._mic.write(indata[:, :1])

        def output(ring, rate, channels, master=False):
            def callback(outdata, frames, _time, status):
                if status:
                    self.stream_errors += 1
                # Cable is master: mixer is paced by its actual sample consumption.
                data = ring.read(frames, rate, target=2400, correct_drift=not master)
                outdata[:] = data if channels == 2 else data.mean(axis=1, keepdims=True)
            return callback

        try:
            self._streams.append(sd.InputStream(device=mic.index, samplerate=mic.rate,
                                                channels=1, dtype="float32", latency=0.04, callback=capture))
            for device, ring, master in ((cable, self._cable, True), (head, self._head, False)):
                channels = min(2, device.outputs)
                self._streams.append(sd.OutputStream(device=device.index, samplerate=device.rate,
                                                     channels=channels, dtype="float32", latency=0.04,
                                                     callback=output(ring, device.rate, channels, master)))
            self._streams[0].start()
            # Prime output buffers and let the capture stream fill its own latency buffer.
            for ring in (self._head, self._cable):
                ring.write(np.zeros((2400, 2), np.float32))
            self.running = True
            self._thread = threading.Thread(target=self._mix_loop, daemon=True, name="audio-mixer")
            self._thread.start()
            for stream in self._streams[1:]:
                stream.start()
        except Exception:
            self.stop()
            raise

    def play(self, path: str, event: str, transmit: bool = True):
        if not self.running:
            raise RuntimeError("请先启动音频")
        if not Path(path).is_file():
            raise FileNotFoundError(f"音乐文件不存在：{path}")
        self.stop_music()
        with self._track_lock:
            generation = self._generation
        self._decode.submit(self._prepare, path, event, transmit, generation)

    def _prepare(self, path, event, transmit, generation):
        try:
            with self._track_lock:
                if generation != self._generation or not self.running:
                    return
            if path == self._cached_path and self._cached_pcm is not None:
                pcm = self._cached_pcm
            else:
                pcm = load_track(path)
                self._cached_pcm, self._cached_path = pcm, path
            with self._track_lock:
                if generation != self._generation or not self.running:
                    return
                self._pcm, self._position = pcm, 0
                self.event, self.send_music, self.music_active = event, transmit, True
                track = self.s.tracks.get(event)
                self._event_gain = track.gain if track else 1.0
        except Exception as exc:
            with self._track_lock:
                if generation == self._generation:
                    self.error = f"音乐解码失败：{exc}"

    def stop_music(self):
        with self._track_lock:
            self._generation += 1
            # Fade the departing track for 50 ms instead of abruptly zeroing PCM.
            if self._pcm is not None and self._position < len(self._pcm):
                self._tail = self._pcm[self._position:self._position + 2400].copy()
                self._tail *= self._event_gain
                if len(self._tail):
                    self._tail *= np.linspace(1, 0, len(self._tail), dtype=np.float32)[:, None]
                self._tail_pos = 0
                self._tail_transmit = self.send_music
            self._pcm = None
            self._position = 0
            self.event = ""
            # Keep transmit intent until the tail has been consumed.
            self.music_active = self._tail is not None and len(self._tail) > 0

    def _render_music(self) -> tuple[np.ndarray, np.ndarray]:
        output = np.zeros((FRAME, 2), np.float32)
        shared = np.zeros((FRAME, 2), np.float32)
        with self._track_lock:
            if self._tail is not None:
                size = min(FRAME, len(self._tail) - self._tail_pos)
                output[:size] += self._tail[self._tail_pos:self._tail_pos + size]
                if self._tail_transmit:
                    shared[:size] += self._tail[self._tail_pos:self._tail_pos + size]
                self._tail_pos += size
                if self._tail_pos >= len(self._tail):
                    self._tail = None
            if self._pcm is not None:
                size = min(FRAME, len(self._pcm) - self._position)
                track = self.s.tracks.get(self.event)
                target = track.gain if track else 1.0
                # Ramp live volume edits over one block to avoid a sample discontinuity.
                gain = np.linspace(self._event_gain, target, FRAME, dtype=np.float32)[:, None]
                chunk = self._pcm[self._position:self._position + size] * gain[:size]
                self._event_gain = target
                output[:size] += chunk
                if self.send_music:
                    shared[:size] += chunk
                self._position += size
                if self._position >= len(self._pcm):
                    self._pcm = None
            self.music_active = self._pcm is not None or self._tail is not None
            if not self.music_active:
                self.event = ""
        return output, shared

    def _mix_loop(self):
        try:
            while not self._stop.is_set():
                if self._cable.available >= 2400:
                    self._stop.wait(0.002)
                    continue
                raw = self._mic.read(FRAME, RATE, target=int(self._mic.sample_rate * 0.06)).reshape(FRAME)
                probability = None
                if self._noise:
                    raw, probability = self._noise.process(raw)
                voice, speaking, level = self._gate.process(raw, self.s.voice_threshold_db,
                                                          probability, self.s.vad_threshold)
                self.level_db = level
                self.speaking = speaking and not self.muted
                if self.muted:
                    voice.fill(0)
                music, shared_music = self._render_music()
                target = 10 ** (self.s.duck_db / 20) if self.speaking else 1.0
                end = self.duck_gain + (target - self.duck_gain) * (0.4 if self.speaking else 0.05)
                duck = np.linspace(self.duck_gain, end, FRAME, dtype=np.float32)[:, None]
                self.duck_gain = end
                mixed = voice[:, None] * self.s.mic_gain
                mixed = np.repeat(mixed, 2, axis=1)
                mixed += shared_music * self.s.music_gain * duck
                self._cable.write(np.clip(mixed, -0.98, 0.98))
                self._head.write(np.clip(music * self.s.headphone_gain, -0.98, 0.98))
        except Exception as exc:
            self.error = f"音频处理停止：{exc}"
            self._stop.set()
            self.speaking = False

    @property
    def transmitting(self) -> bool:
        # Include buffered playback after the last source frame.
        return self.speaking or (self._pcm is not None and self.send_music) or (self._tail is not None and self._tail_transmit)

    def healthy(self) -> bool:
        return (self.running and self._thread is not None and self._thread.is_alive()
                and all(stream.active for stream in self._streams))

    def stats(self) -> dict:
        return {"device_flags": self.stream_errors,
                "underflows": sum(r.underflows for r in (self._head, self._cable, self._mic) if r),
                "overflows": sum(r.overflows for r in (self._head, self._cable, self._mic) if r)}

    def stop(self):
        self._stop.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        for stream in self._streams:
            try:
                stream.abort()
                stream.close()
            except sd.PortAudioError:
                pass
        self._streams.clear()
        if self._noise:
            self._noise.close()
            self._noise = None
        self.speaking = False
        self.stop_music()
        self._tail = None
        self.music_active = self.send_music = False

    def close(self):
        self.stop()
        self._decode.shutdown(wait=False, cancel_futures=True)
