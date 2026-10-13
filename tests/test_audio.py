import time

import numpy as np
import pytest
import soundfile as sf

from cs2music.audio import AudioEngine, AudioRing, RNNoise, VoiceGate, load_track, Device
from cs2music.config import Settings


def test_ring_wrap_overflow_and_resampling():
    ring = AudioRing(1, sample_rate=48000, capacity=20)
    ring.write(np.arange(15, dtype=np.float32)[:, None])
    first = ring.read(10, target=15, correct_drift=False)
    np.testing.assert_allclose(first[:, 0], np.arange(10))
    ring.write(np.arange(15, 30, dtype=np.float32)[:, None])
    result = ring.read(9, output_rate=24000, correct_drift=False)
    np.testing.assert_allclose(result[:, 0], np.arange(10, 28, 2))
    ring.write(np.arange(40, dtype=np.float32)[:, None])
    assert ring.overflows == 1 and ring.available == 20
    assert np.isfinite(ring.read(80)).all()
    assert ring.underflows == 1


def test_voice_gate_hangover_and_mute_tail():
    gate = VoiceGate()
    speech = np.full(480, .1, np.float32)
    output, active, _ = gate.process(speech, -42, .9, .55)
    assert active and output[-1] == pytest.approx(.1)
    for _ in range(25):
        output, active, _ = gate.process(np.zeros(480, np.float32), -42, 0, .55)
    assert not active and not output.any()
    _, active, _ = gate.process(speech, -42, .1, .55)
    assert not active  # loud non-speech is not automatically speech


def test_rnnoise_real_dll_silence_noise_finite_and_suppressed():
    noise = RNNoise()
    random = np.random.default_rng(42)
    levels = []
    probabilities = []
    for _ in range(200):
        raw = random.normal(0, .01, 480).astype(np.float32)
        output, probability = noise.process(raw)
        assert np.isfinite(output).all()
        levels.append(np.sqrt(np.mean(output ** 2)))
        probabilities.append(probability)
    assert np.mean(levels[-50:]) < .01
    assert all(0 <= p <= 1 for p in probabilities)
    noise.close()


@pytest.mark.parametrize("extension", ["wav", "flac", "ogg", "mp3"])
def test_decode_formats_resample_stereo_and_fade(tmp_path, extension):
    source = np.sin(np.arange(4410) * .08).astype(np.float32) * .5
    path = tmp_path / f"音效.{extension}"
    sf.write(path, source, 44100)
    output = load_track(str(path))
    assert output.shape[1] == 2 and 4700 <= len(output) <= 4900
    assert np.isfinite(output).all()
    assert not output[0].any() and not output[-1].any()


def test_preview_tail_never_leaks_into_transmitted_track():
    engine = AudioEngine(Settings())
    engine.running = True
    engine._pcm = np.full((4800, 2), .7, np.float32)
    engine.music_active = True
    engine.send_music = False
    engine.stop_music()
    engine._pcm = np.full((4800, 2), .1, np.float32)
    engine.music_active = True
    engine.send_music = True
    heard, shared = engine._render_music()
    assert np.max(heard) > .5
    np.testing.assert_allclose(shared, .1)
    engine.running = False
    engine.close()


def test_cancelled_decode_does_not_restart_music(tmp_path, monkeypatch):
    engine = AudioEngine(Settings())
    engine.running = True
    path = tmp_path / "test.wav"
    sf.write(path, np.ones(100, np.float32), 48000)
    def slow(_path):
        time.sleep(.1)
        return np.ones((100, 2), np.float32)
    monkeypatch.setattr("cs2music.audio.load_track", slow)
    engine.play(str(path), "victory")
    engine.stop_music()
    time.sleep(.15)
    assert engine._pcm is None and not engine.music_active
    engine.running = False
    engine.close()


def test_production_mixer_ducks_only_transmitted_music_and_mute_preserves_music(monkeypatch):
    streams = []
    class FakeStream:
        def __init__(self, **kwargs):
            self.callback, self.capture = kwargs["callback"], kwargs["device"] == 1
            self.active = False
            streams.append(self)
        def start(self): self.active = True
        def abort(self): self.active = False
        def close(self): self.active = False
    def device(label, capture):
        return Device({"mic": 1, "head": 2, "cable": 3}[label], "CABLE Input" if label == "cable" else label, 1 if capture else 0,
                      0 if capture else 2, 48000)
    monkeypatch.setattr("cs2music.audio.resolve_device", device)
    monkeypatch.setattr("cs2music.audio.sd.InputStream", FakeStream)
    monkeypatch.setattr("cs2music.audio.sd.OutputStream", FakeStream)
    settings = Settings(mic_device="mic", headphone_device="head", cable_device="cable",
                        denoise=False, headphone_gain=1, music_gain=1, mic_gain=1)
    settings.tracks["victory"].gain = .5
    engine = AudioEngine(settings)
    engine.start()
    engine.event = "victory"
    engine._pcm = np.full((96000, 2), .4, np.float32)
    engine.send_music = engine.music_active = True
    outputs = {"head": [], "cable": []}
    try:
        for _ in range(50):
            streams[0].callback(np.full((480, 1), .1, np.float32), 480, None, False)
            for stream, name in ((streams[1], "cable"), (streams[2], "head")):
                output = np.empty((480, 2), np.float32)
                stream.callback(output, 480, None, False)
                outputs[name].append(output)
            time.sleep(.01)
        assert engine.speaking and engine.healthy()
        assert np.mean(outputs["head"][-1]) == pytest.approx(.2, abs=.01)
        # ~0.1 voice + 0.2 * 10^(-12/20), while headphones retain 0.2 music.
        assert np.mean(outputs["cable"][-1]) == pytest.approx(.15, abs=.015)
        engine.muted = True
        for _ in range(60):
            streams[0].callback(np.full((480, 1), .1, np.float32), 480, None, False)
            out = np.empty((480, 2), np.float32)
            streams[1].callback(out, 480, None, False)
            streams[2].callback(np.empty((480, 2), np.float32), 480, None, False)
            time.sleep(.01)
        assert not engine.speaking and engine.transmitting
        assert np.mean(out) > .18  # music continues without voice, ducking has recovered
    finally:
        engine.close()
    assert not any(s.active for s in streams)
