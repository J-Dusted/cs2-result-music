#!/usr/bin/env python3
"""CS2 result music controller.

The process listens for CS2 Game State Integration, decides whether the local
player won or lost, selects a track, and publishes an atomic command for the
x64 VST2 microphone injector.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


LOG = logging.getLogger("cs2_result_music")
DEFAULT_COMMAND_FILE = Path(r"C:\ProgramData\CS2ResultAudio\command.txt")
DEFAULT_SEQUENCE_FILE = Path(r"C:\ProgramData\CS2ResultAudio\sequence.json")
DEFAULT_STATUS_FILE = Path(r"C:\ProgramData\CS2ResultAudio\status.txt")
DEFAULT_STAGING_DIR = Path(r"C:\ProgramData\CS2ResultAudio\staging")
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
TEAM_MODES = {
    "competitive",
    "scrimcomp2v2",
    "scrimcomp5v5",
    "premier",
    "wingman",
    "casual",
}
SOLO_MODES = {"deathmatch", "armsrace"}
EQUALIZER_APO_PRE_MIX_GUID = "{EACD2258-FCAC-4FF4-B36D-419E924A6D79}"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PlaybackRule:
    mode: str
    specific: str = ""

    def validate(self) -> None:
        normalized = self.mode.strip().lower()
        if normalized not in {"random", "sequential", "specific"}:
            raise ConfigError(
                f"Unsupported playback mode {self.mode!r}; "
                "expected random, sequential, or specific."
            )
        if normalized == "specific" and not self.specific.strip():
            raise ConfigError("The specific playback mode requires a file name.")


@dataclass(frozen=True)
class AppConfig:
    path: Path
    bind_host: str
    port: int
    token: str
    music_root: Path
    win_dir: Path
    lose_dir: Path
    win_rule: PlaybackRule
    lose_rule: PlaybackRule
    trigger_mode: str
    mic_gain_db: float
    local_enabled: bool
    local_player: Path
    local_gain_db: float
    local_output_device: str
    command_file: Path
    status_file: Path
    sequence_file: Path
    staging_dir: Path
    equalizer_apo_dir: Path
    plugin_name: str

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        path = path.expanduser().resolve()
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Configuration file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise ConfigError("The top-level configuration value must be an object.")

        base = path.parent
        gsi = _object(raw.get("gsi"), "gsi")
        library = _object(raw.get("music"), "music")
        playback = _object(raw.get("playback"), "playback")
        audio = _object(raw.get("audio"), "audio")
        local = _object(raw.get("local"), "local")
        apo = _object(raw.get("equalizer_apo", {}), "equalizer_apo")

        music_root = _resolve_path(base, str(library.get("root", "music")))
        win_dir = _resolve_path(music_root, str(library.get("win_dir", "win")))
        lose_dir = _resolve_path(music_root, str(library.get("lose_dir", "lose")))
        win_rule = _rule(playback.get("win"), "playback.win")
        lose_rule = _rule(playback.get("lose"), "playback.lose")
        win_rule.validate()
        lose_rule.validate()
        trigger = _object(raw.get("trigger", {}), "trigger")
        trigger_mode = str(trigger.get("mode", "match")).strip().lower()
        if trigger_mode not in {"match", "round"}:
            raise ConfigError(
                "trigger.mode must be either 'match' or 'round'."
            )

        command_file = _resolve_path(
            base, str(audio.get("command_file", DEFAULT_COMMAND_FILE))
        )
        status_file = _resolve_path(
            base, str(audio.get("status_file", command_file.with_name("status.txt")))
        )
        sequence_file = _resolve_path(
            base, str(audio.get("sequence_file", DEFAULT_SEQUENCE_FILE))
        )
        staging_dir = _resolve_path(
            base, str(audio.get("staging_dir", DEFAULT_STAGING_DIR))
        )

        local_player_value = local.get("player", "bin\\CS2LocalPlayback.exe")
        local_player = _resolve_path(base, str(local_player_value))

        bind_host = str(gsi.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(gsi.get("port", 3000))
        except (TypeError, ValueError) as exc:
            raise ConfigError("gsi.port must be an integer.") from exc
        if port < 1 or port > 65535:
            raise ConfigError("gsi.port must be between 1 and 65535.")

        return cls(
            path=path,
            bind_host=bind_host,
            port=port,
            token=str(gsi.get("token", "")).strip(),
            music_root=music_root,
            win_dir=win_dir,
            lose_dir=lose_dir,
            win_rule=win_rule,
            lose_rule=lose_rule,
            trigger_mode=trigger_mode,
            mic_gain_db=_finite_float(audio.get("mic_gain_db", -6.0), "audio.mic_gain_db"),
            local_enabled=bool(local.get("enabled", False)),
            local_player=local_player,
            local_gain_db=_finite_float(local.get("gain_db", -6.0), "local.gain_db"),
            local_output_device=str(local.get("output_device", "")).strip(),
            command_file=command_file,
            status_file=status_file,
            sequence_file=sequence_file,
            staging_dir=staging_dir,
            equalizer_apo_dir=Path(
                str(apo.get("install_dir", r"C:\Program Files\EqualizerAPO"))
            ).expanduser(),
            plugin_name=str(apo.get("plugin_name", "CS2MicInjector.dll")).strip()
            or "CS2MicInjector.dll",
        )


@dataclass(frozen=True)
class MatchResult:
    outcome: str
    team: str
    score_ct: int
    score_t: int
    map_name: str
    mode: str


class GameState:
    """Turns GSI snapshots into one result event per map."""

    def __init__(self, trigger_mode: str = "match") -> None:
        if trigger_mode not in {"match", "round"}:
            raise ValueError("trigger_mode must be match or round")
        self.trigger_mode = trigger_mode
        self.last_phase: str | None = None
        self.last_timestamp: int | None = None
        self.map_identity: tuple[str, str] | None = None
        self.handled_gameover = False
        self.handled_round_key: tuple[Any, ...] | None = None

    def observe(self, payload: dict[str, Any]) -> MatchResult | None:
        provider = _safe_object(payload.get("provider"))
        timestamp = _optional_int(provider.get("timestamp"))
        if (
            timestamp is not None
            and self.last_timestamp is not None
            and timestamp < self.last_timestamp
        ):
            return None
        if timestamp is not None:
            self.last_timestamp = timestamp

        map_data = _safe_object(payload.get("map"))
        phase = str(map_data.get("phase", "")).strip().lower()
        identity = (
            str(map_data.get("name", "")).strip(),
            str(map_data.get("mode", "")).strip().lower(),
        )
        if self.map_identity is not None and identity != self.map_identity:
            self._reset_for_new_map()
        self.map_identity = identity

        if self.trigger_mode == "round":
            return self._observe_round(payload, map_data)

        if phase != "gameover":
            if phase:
                self.last_phase = phase
                self.handled_gameover = False
            return None

        previous_phase = self.last_phase
        self.last_phase = phase
        if previous_phase is None or previous_phase == "gameover":
            return None
        if self.handled_gameover:
            return None

        result = determine_match_result(payload)
        self.handled_gameover = True
        return result

    def _observe_round(
        self, payload: dict[str, Any], map_data: dict[str, Any]
    ) -> MatchResult | None:
        round_data = _safe_object(payload.get("round"))
        phase = str(round_data.get("phase", "")).strip().lower()
        winner = str(round_data.get("win_team", "")).strip().upper()
        if phase != "over" or winner not in {"CT", "T"}:
            return None

        round_number = _optional_int(map_data.get("round"))
        if round_number is None:
            score_ct = _score(map_data.get("team_ct"))
            score_t = _score(map_data.get("team_t"))
            key: tuple[Any, ...] = (winner, score_ct, score_t)
        else:
            key = (round_number, winner)
        key = (self.map_identity, *key)
        if self.handled_round_key == key:
            return None

        result = determine_round_result(payload)
        if result is None:
            return None
        self.handled_round_key = key
        return result

    def _reset_for_new_map(self) -> None:
        self.last_phase = None
        self.last_timestamp = None
        self.handled_gameover = False
        self.handled_round_key = None


def determine_match_result(payload: dict[str, Any]) -> MatchResult | None:
    map_data = _safe_object(payload.get("map"))
    mode = str(map_data.get("mode", "")).strip().lower()
    if mode in SOLO_MODES:
        return None

    player_id = _safe_object(payload.get("player"))
    team = str(player_id.get("team", "")).strip().upper()
    if team not in {"CT", "T"}:
        return None

    score_ct = _score(map_data.get("team_ct"))
    score_t = _score(map_data.get("team_t"))
    if score_ct is None or score_t is None or score_ct == score_t:
        return None

    if team == "CT":
        outcome = "win" if score_ct > score_t else "lose"
    else:
        outcome = "win" if score_t > score_ct else "lose"

    return MatchResult(
        outcome=outcome,
        team=team,
        score_ct=score_ct,
        score_t=score_t,
        map_name=str(map_data.get("name", "")),
        mode=mode,
    )


def determine_round_result(payload: dict[str, Any]) -> MatchResult | None:
    map_data = _safe_object(payload.get("map"))
    mode = str(map_data.get("mode", "")).strip().lower()
    if mode in SOLO_MODES:
        return None

    player = _safe_object(payload.get("player"))
    team = str(player.get("team", "")).strip().upper()
    if team not in {"CT", "T"}:
        return None

    round_data = _safe_object(payload.get("round"))
    winner = str(round_data.get("win_team", "")).strip().upper()
    if winner not in {"CT", "T"}:
        return None

    score_ct = _score(map_data.get("team_ct"))
    score_t = _score(map_data.get("team_t"))
    if score_ct is None or score_t is None:
        return None

    return MatchResult(
        outcome="win" if winner == team else "lose",
        team=team,
        score_ct=score_ct,
        score_t=score_t,
        map_name=str(map_data.get("name", "")),
        mode=mode,
    )


class SequenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, int] = {}
        self._load()

    def next_index(self, key: str, item_count: int) -> int:
        if item_count <= 0:
            raise FileNotFoundError("No playable audio files were found.")
        with self._lock:
            index = int(self._data.get(key, 0))
            if index < 0 or index >= item_count:
                index = 0
            self._data[key] = (index + 1) % item_count
            self._save_locked()
            return index

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {
                    str(key): int(value)
                    for key, value in raw.items()
                    if isinstance(value, (int, float))
                }
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            self._data = {}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.path, self._data)


class MusicLibrary:
    def __init__(
        self,
        win_dir: Path,
        lose_dir: Path,
        win_rule: PlaybackRule,
        lose_rule: PlaybackRule,
        sequence_store: SequenceStore,
        rng: random.Random | None = None,
    ) -> None:
        self.directories = {"win": win_dir, "lose": lose_dir}
        self.rules = {"win": win_rule, "lose": lose_rule}
        self.sequence_store = sequence_store
        self.rng = rng or random.Random()
        self._last_random: dict[str, Path | None] = {"win": None, "lose": None}
        self._lock = threading.Lock()

    def list_tracks(self, outcome: str) -> list[Path]:
        directory = self.directories[outcome]
        if not directory.is_dir():
            return []
        tracks = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return sorted(tracks, key=lambda path: path.relative_to(directory).as_posix().casefold())

    def choose(self, outcome: str) -> Path:
        if outcome not in self.rules:
            raise ValueError(f"Unknown outcome: {outcome}")
        rule = self.rules[outcome]
        mode = rule.mode.strip().lower()
        tracks = self.list_tracks(outcome)
        if not tracks:
            raise FileNotFoundError(
                f"No supported audio files in {self.directories[outcome]}"
            )

        with self._lock:
            if mode == "specific":
                directory = self.directories[outcome]
                candidate = (directory / rule.specific).resolve()
                try:
                    candidate.relative_to(directory.resolve())
                except ValueError as exc:
                    raise FileNotFoundError(
                        "The specific track must stay inside its outcome directory."
                    ) from exc
                if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise FileNotFoundError(f"Configured track not found: {candidate}")
                return candidate

            if mode == "sequential":
                index = self.sequence_store.next_index(outcome, len(tracks))
                return tracks[index]

            index = self.rng.randrange(len(tracks))
            if len(tracks) > 1 and tracks[index] == self._last_random[outcome]:
                index = (index + 1) % len(tracks)
            self._last_random[outcome] = tracks[index]
            return tracks[index]


class CommandPublisher:
    def __init__(self, command_file: Path) -> None:
        self.command_file = command_file
        self._lock = threading.Lock()
        self._next_id = int(time.time_ns() // 1_000_000)

    def publish_play(self, audio_file: Path, gain_db: float) -> int:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            payload = {
                "request": request_id,
                "op": "play",
                "file": str(audio_file.resolve()),
                "gain_db": round(float(gain_db), 3),
            }
            _atomic_write_json(self.command_file, payload)
            return request_id

    def publish_stop(self) -> int:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            _atomic_write_json(
                self.command_file,
                {"request": request_id, "op": "stop"},
            )
            return request_id


class GsiCapture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())


class LocalPlayer:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[Any] | None = None
        self._lock = threading.Lock()

    def start(self, audio_file: Path, request_id: int) -> None:
        if not self.config.local_enabled:
            return
        player = self.config.local_player
        if not player.is_file():
            LOG.warning("Local playback is enabled but player was not found: %s", player)
            return

        creative_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        arguments = [
            str(player),
            "--file",
            str(audio_file.resolve()),
            "--gain-db",
            f"{self.config.local_gain_db:.3f}",
            "--watch-command",
            str(self.config.command_file),
            "--request-id",
            str(request_id),
        ]
        if self.config.local_output_device:
            arguments.extend(["--output-device", self.config.local_output_device])

        with self._lock:
            self._stop_locked()
            try:
                self._process = subprocess.Popen(
                    arguments,
                    cwd=str(self.config.path.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creative_flags,
                )
                LOG.info("Started local playback process (pid=%s)", self._process.pid)
            except OSError as exc:
                LOG.warning("Could not start local player: %s", exc)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


class ResultMusicController:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.game_state = GameState(config.trigger_mode)
        self.library = MusicLibrary(
            config.win_dir,
            config.lose_dir,
            config.win_rule,
            config.lose_rule,
            SequenceStore(config.sequence_file),
        )
        self.publisher = CommandPublisher(config.command_file)
        self.local_player = LocalPlayer(config)
        self._event_lock = threading.Lock()

    def handle_payload(self, payload: dict[str, Any]) -> MatchResult | None:
        result = self.game_state.observe(payload)
        if result is None:
            return None
        with self._event_lock:
            self._play_outcome(result.outcome)
        return result

    def trigger_test(self, outcome: str) -> int:
        if outcome not in {"win", "lose"}:
            raise ValueError("outcome must be win or lose")
        with self._event_lock:
            return self._play_outcome(outcome)

    def _play_outcome(self, outcome: str) -> int:
        track = self.library.choose(outcome)
        staged_track = _stage_track(track, self.config.staging_dir, outcome)
        request_id = self.publisher.publish_play(
            staged_track, self.config.mic_gain_db
        )
        self.local_player.start(staged_track, request_id)
        LOG.info(
            "Queued %s music: %s (request=%s, mic_gain=%.1f dB)",
            outcome,
            staged_track,
            request_id,
            self.config.mic_gain_db,
        )
        return request_id


def run_server(config: AppConfig, capture_file: Path | None = None) -> int:
    if not config.token or config.token == "TOKEN":
        LOG.error(
            "The GSI token is still empty or TOKEN. Edit config.json and the CS2 "
            "configuration with the same non-placeholder value."
        )
        return 2
    controller = ResultMusicController(config)
    capture = GsiCapture(capture_file) if capture_file is not None else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "CS2ResultMusic/1.0"

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, b"Invalid Content-Length")
                return
            if length <= 0 or length > 2 * 1024 * 1024:
                self._reply(413, b"Invalid payload size")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._reply(400, b"Invalid JSON")
                return
            if not isinstance(payload, dict):
                self._reply(400, b"Payload must be an object")
                return

            supplied = _extract_gsi_token(payload, query)
            if not hmac.compare_digest(supplied, config.token):
                self._reply(403, b"Forbidden")
                return

            if capture is not None:
                try:
                    captured = dict(payload)
                    captured.pop("auth", None)
                    capture.append(captured)
                except OSError as exc:
                    LOG.warning("Could not capture GSI payload: %s", exc)

            try:
                result = controller.handle_payload(payload)
            except OSError as exc:
                LOG.error("%s", exc)
                self._reply(503, b"Music library is not ready")
                return
            if result is None:
                self._reply(204, b"")
            else:
                self._reply(
                    200,
                    json.dumps(
                        {
                            "outcome": result.outcome,
                            "team": result.team,
                            "score_ct": result.score_ct,
                            "score_t": result.score_t,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )

        def do_GET(self) -> None:  # noqa: N802
            self._reply(405, b"POST only")

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.debug("GSI HTTP: " + fmt, *args)

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    server = ThreadingHTTPServer((config.bind_host, config.port), Handler)
    server.daemon_threads = True
    LOG.info(
        "Listening on http://%s:%s and watching %s",
        config.bind_host,
        config.port,
        ", ".join((str(config.win_dir), str(config.lose_dir))),
    )
    if capture is not None:
        LOG.info("Capturing raw GSI payloads to %s", capture.path)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        LOG.info("Stopping.")
    finally:
        server.server_close()
        controller.local_player.stop()
    return 0


def check_audio(config: AppConfig) -> int:
    ok = True
    controller = ResultMusicController(config)
    print(f"GSI listener: http://{config.bind_host}:{config.port}")
    print(f"trigger mode: {config.trigger_mode}")
    print(f"staging directory: {config.staging_dir}")
    for outcome in ("win", "lose"):
        tracks = controller.library.list_tracks(outcome)
        if tracks:
            print(f"{outcome}: {len(tracks)} track(s)")
            for track in tracks[:8]:
                print(f"  {track}")
            if len(tracks) > 8:
                print(f"  ... and {len(tracks) - 8} more")
        else:
            ok = False
            print(f"{outcome}: no playable files in {controller.library.directories[outcome]}")
    local_player = config.local_player
    if config.local_enabled:
        if local_player.is_file():
            print(f"local player: {local_player}")
        else:
            ok = False
            print(f"local player missing: {local_player}")
    return 0 if ok else 1


def check_apo(config: AppConfig) -> int:
    ok = True
    installed_plugin = (
        config.equalizer_apo_dir / "VSTPlugins" / config.plugin_name
    )
    local_plugin = config.path.parent / "bin" / config.plugin_name
    if installed_plugin.is_file():
        print(f"plugin installed: {installed_plugin}")
    elif local_plugin.is_file():
        print(f"plugin built: {local_plugin}")
        print(f"not installed yet: {installed_plugin}")
        ok = False
    else:
        print("plugin missing: build CS2MicInjector.dll first")
        ok = False

    config_txt = config.equalizer_apo_dir / "config" / "config.txt"
    include_name = "cs2_result_music.txt"
    if config_txt.is_file():
        text = config_txt.read_text(encoding="utf-8", errors="replace")
        if include_name in text or "CS2 Result Music" in text:
            print(f"APO config include: {config_txt}")
        else:
            print(f"APO config has no CS2 result music include: {config_txt}")
            ok = False
    else:
        print(f"Equalizer APO config not found: {config_txt}")
        ok = False

    registered_endpoints = find_registered_capture_endpoints()
    if registered_endpoints:
        for endpoint_guid in registered_endpoints:
            print(f"capture APO registration: {endpoint_guid}")
    else:
        print("capture APO registration not found")
        ok = False

    if config.status_file.is_file():
        age = time.time() - config.status_file.stat().st_mtime
        if age <= 20:
            print(f"plugin heartbeat: fresh ({age:.1f}s)")
            try:
                status = json.loads(config.status_file.read_text(encoding="utf-8"))
                print(
                    "  state={state} request={request} sample_rate={sample_rate}".format(
                        state=status.get("state", "?"),
                        request=status.get("request", "?"),
                        sample_rate=status.get("sample_rate", "?"),
                    )
                )
            except (OSError, json.JSONDecodeError):
                pass
        else:
            print(f"plugin heartbeat is stale: {age:.1f}s")
            ok = False
    else:
        print(f"plugin heartbeat not found: {config.status_file}")
        if registered_endpoints:
            print(
                "  reboot Windows, then open or record from the HECATE microphone "
                "to load audiodg."
            )
        ok = False
    return 0 if ok else 1


def find_registered_capture_endpoints() -> list[str]:
    root_path = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
    )
    value_names = (
        r"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},1",
        r"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},5",
    )
    result: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path) as root:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                endpoint_guid = winreg.EnumKey(root, index)
                try:
                    with winreg.OpenKey(
                        root,
                        rf"{endpoint_guid}\FxProperties",
                    ) as fx:
                        for value_name in value_names:
                            try:
                                value, _ = winreg.QueryValueEx(fx, value_name)
                            except OSError:
                                continue
                            if str(value).upper() == EQUALIZER_APO_PRE_MIX_GUID:
                                result.append(endpoint_guid)
                                break
                except OSError:
                    continue
    except OSError:
        return []
    return result


def install_gsi_config(config: AppConfig, destination: Path | None) -> int:
    if destination is None:
        destination = Path(
            r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\Counter-Strike Global Offensive\game\csgo\cfg"
        )
    destination = destination.expanduser().resolve()
    if not destination.is_dir():
        print(f"CS2 cfg directory not found: {destination}")
        return 1
    output = destination / "gamestate_integration_cs2_result_music.cfg"
    text = _gsi_config_text(config.port, config.token)
    output.write_text(text, encoding="utf-8", newline="\r\n")
    print(f"Installed GSI config: {output}")
    print("Restart CS2 so it reloads the integration configuration.")
    return 0


def _gsi_config_text(port: int, token: str) -> str:
    return f'''"CS2 Result Music"
{{
    "uri" "http://127.0.0.1:{port}"
    "timeout" "1.0"
    "buffer" "0.0"
    "throttle" "0.1"
    "heartbeat" "10.0"
    "auth"
    {{
        "token" "{_escape_cfg(token)}"
    }}
    "output"
    {{
        "precision_time" "3"
        "precision_position" "1"
        "precision_vector" "3"
    }}
    "data"
    {{
        "map" "1"
        "map_round_wins" "1"
        "player_id" "1"
        "player_state" "1"
        "provider" "1"
        "round" "1"
    }}
}}
'''


def _escape_cfg(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _extract_gsi_token(
    payload: dict[str, Any], query: dict[str, list[str]]
) -> str:
    query_token = query.get("auth", [""])[0]
    auth = _safe_object(payload.get("auth"))
    body_token = str(auth.get("token", "")).strip()
    return str(query_token).strip() or body_token


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _stage_track(track: Path, staging_dir: Path, outcome: str) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    suffix = track.suffix.lower()
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{outcome}-",
        suffix=suffix,
        dir=str(staging_dir),
    )
    temp_path = Path(temp_name)
    try:
        with track.open("rb") as source, os.fdopen(handle, "wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        return temp_path
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _object(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be an object.")
    return value


def _safe_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rule(value: Any, name: str) -> PlaybackRule:
    obj = _object(value, name)
    return PlaybackRule(
        mode=str(obj.get("mode", "random")),
        specific=str(obj.get("specific", "")),
    )


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _finite_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ConfigError(f"{name} must be finite.")
    return number


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _score(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    return _optional_int(value.get("score"))


def _default_config_path() -> Path:
    return Path(__file__).with_name("config.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help="Path to config.json.",
    )
    parser.add_argument("--check-audio", action="store_true", help="Validate music folders.")
    parser.add_argument("--check-apo", action="store_true", help="Validate APO installation.")
    parser.add_argument("--test", choices=("win", "lose"), help="Trigger one outcome.")
    parser.add_argument(
        "--install-gsi",
        action="store_true",
        help="Write the generated CS2 integration config into the CS2 cfg folder.",
    )
    parser.add_argument(
        "--csgo-cfg",
        type=Path,
        help="Override the CS2 cfg directory used by --install-gsi.",
    )
    parser.add_argument(
        "--capture-gsi",
        nargs="?",
        const=Path(__file__).with_name("work") / "gsi-capture.jsonl",
        type=Path,
        help=(
            "Run the GSI listener and append raw payloads to this JSONL file. "
            "The default file is work/gsi-capture.jsonl."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        config = AppConfig.load(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check_audio:
        return check_audio(config)
    if args.check_apo:
        return check_apo(config)
    if args.install_gsi:
        return install_gsi_config(config, args.csgo_cfg)
    if args.capture_gsi is not None:
        capture_file = _resolve_path(config.path.parent, str(args.capture_gsi))
        return run_server(config, capture_file)
    if args.test:
        controller = ResultMusicController(config)
        try:
            controller.trigger_test(args.test)
        except OSError as exc:
            print(f"Test failed: {exc}", file=sys.stderr)
            return 1
        return 0

    return run_server(config)


if __name__ == "__main__":
    raise SystemExit(main())
