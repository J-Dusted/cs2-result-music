"""Deterministic state machine. No HTTP, audio or wall-clock dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class Action:
    kind: str  # play / stop
    event: str = ""
    reason: str = ""
    when_live: bool = False


class EventEngine:
    STALE_SECONDS = 5.0
    RESULT_WAIT = 1.5

    def __init__(self, settings: Settings):
        self.s = settings
        self.identity = ""
        self.connected = False
        self.last_received = 0.0
        self.map_name = ""
        self.map_phase = ""
        self.round_phase = ""
        self.round_number = -1
        self.completed = 0
        self.own_team = ""
        self.round_team = ""
        self.mvps: int | None = None
        self.current_mvps: int | None = None
        self.own_stats_source = "missing"
        self.player_is_self = False
        self.allplayers_self_present = False
        self.round_mvp_baseline: int | None = None
        self.result_mvp_baseline: int | None = None
        self.seen: set[tuple[int, str]] = set()
        self.pending_result: tuple[int, str, float | None] | None = None
        self.result_status = "idle"
        self.result_reason = ""
        self.result_round: int | None = None
        self.results_handled: set[int] = set()
        self.active_event = ""
        self.bomb_state = ""
        self.bomb_deadline: float | None = None
        self.bomb_source = "无"
        self.plant_seen = False
        self.opening_armed = False
        self.side_armed = False
        self.actions: list[Action] = []

    def _play(self, event: str, number: int | None = None, reason: str = "事件触发"):
        key = (self.round_number if number is None else number, event)
        if key in self.seen:
            return
        self.seen.add(key)
        if event in ("start", "side"):
            self.cancel_results("新的开局／换边事件")
        if event in ("victory", "defeat", "mvp"):
            self.result_status, self.result_reason = event, reason
        self.active_event = event
        self.actions.append(Action("play", event, reason))

    def cancel_results(self, reason: str):
        waiting = self.pending_result is not None
        if self.result_round is not None:
            self.results_handled.add(self.result_round)
        self.pending_result = None
        self.result_round = None
        self.result_mvp_baseline = None
        self.result_status = "skipped" if waiting else "idle"
        self.result_reason = reason

    @staticmethod
    def _mvp_count(player: dict) -> int | None:
        stats = player.get("match_stats") or {}
        value = stats.get("mvps") if isinstance(stats, dict) else None
        return value if type(value) is int and value >= 0 else None

    def _refresh_result_wait(self, now: float):
        if self.pending_result is None:
            return
        number, event, ready_at = self.pending_result
        if self.result_mvp_baseline is None:
            ready_at = None
            self.result_status, self.result_reason = "waiting_baseline", "缺少该回合 MVP 基线，暂不判断"
        elif self.current_mvps is None or self.current_mvps < self.result_mvp_baseline:
            ready_at = None
            self.result_status, self.result_reason = "waiting_self", "本人统计不可用，等待恢复"
        else:
            if ready_at is None:
                ready_at = now + self.RESULT_WAIT
            self.result_status, self.result_reason = "confirming", "本人统计可用，等待 MVP 确认"
        self.pending_result = (number, event, ready_at)

    def result_snapshot(self) -> dict:
        return {
            "connected": self.connected, "map_phase": self.map_phase,
            "round_phase": self.round_phase, "round": self.round_number,
            "own_stats_source": self.own_stats_source,
            "player_is_self": self.player_is_self,
            "allplayers_self_present": self.allplayers_self_present,
            "own_stats_available": self.current_mvps is not None,
            "current_mvps": self.current_mvps, "remembered_mvps": self.mvps,
            "round_mvp_baseline": self.round_mvp_baseline,
            "result_mvp_baseline": self.result_mvp_baseline,
            "result_round": self.result_round, "status": self.result_status,
            "reason": self.result_reason,
            "pending_round": self.pending_result[0] if self.pending_result else None,
            "confirm_at_monotonic": self.pending_result[2] if self.pending_result else None,
        }

    def _stop(self, reason: str, when_live: bool = False):
        self.active_event = ""
        self.actions.append(Action("stop", reason=reason, when_live=when_live))

    def _cancel_bomb(self):
        self.bomb_deadline = None
        self.bomb_source = "无"
        if self.active_event == "bomb":
            self._stop("炸弹计时取消")

    def _boundary(self, n: int) -> bool:
        h, o = self.s.half_rounds, self.s.overtime_half_rounds
        return n == h or (n >= 2 * h and (n - 2 * h) % o == 0)

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def ingest(self, payload: dict, now: float) -> list[Action]:
        self.actions = []
        provider = payload.get("provider") or {}
        game_map = payload.get("map") or {}
        rnd = payload.get("round") or {}
        if not isinstance(provider, dict) or not isinstance(game_map, dict) or not isinstance(rnd, dict):
            return []
        steamid = str(provider.get("steamid", ""))
        if provider.get("appid") not in (None, 730) or not steamid:
            return []
        map_name = str(game_map.get("name", ""))
        map_phase = str(game_map.get("phase", ""))
        phase = str(rnd.get("phase", ""))
        number = self._integer(game_map.get("round"), -1)
        completed = sum(self._integer((game_map.get(k) or {}).get("score")) for k in ("team_ct", "team_t"))
        local = payload.get("player") or {}
        self.player_is_self = isinstance(local, dict) and str(local.get("steamid", "")) == steamid
        if not self.player_is_self:
            local = {}
        alternate = (payload.get("allplayers") or {}).get(steamid, {})
        self.allplayers_self_present = isinstance(alternate, dict) and bool(alternate)
        if not isinstance(alternate, dict):
            alternate = {}
        team = local.get("team") or alternate.get("team")
        mvp = self._mvp_count(local)
        self.own_stats_source = "player" if mvp is not None else "missing"
        if mvp is None:
            mvp = self._mvp_count(alternate)
            if mvp is not None:
                self.own_stats_source = "allplayers"
        self.current_mvps = mvp
        reset = (not self.connected or now - self.last_received > self.STALE_SECONDS
                 or steamid != self.identity or map_name != self.map_name
                 or (number < self.round_number and completed < self.completed)
                 or (map_phase == "warmup" and self.map_phase != "warmup"))
        self.connected = True
        self.last_received = now
        if reset:
            self._stop("建立新的对局状态基线")
            self.identity = steamid
            self.map_name, self.map_phase = map_name, map_phase
            self.round_phase, self.round_number, self.completed = phase, number, completed
            self.own_team = team if team in ("T", "CT") else ""
            self.round_team = self.own_team
            self.mvps = mvp
            self.round_mvp_baseline = mvp
            self.result_mvp_baseline = None
            self.seen.clear()
            self.pending_result = None
            self.result_status, self.result_reason = "idle", "建立新的对局状态基线"
            self.result_round = None
            self.results_handled = {number} if phase == "over" else set()
            self.side_armed = False
            self.opening_armed = map_phase == "warmup"
            self.bomb_state = self._bomb_state(payload)
            self.plant_seen = self.bomb_state == "planted"
            self.bomb_deadline = None
            self.bomb_source = "无"
            return self.actions

        previous_phase = self.round_phase
        previous_map_phase = self.map_phase
        previous_team = self.own_team
        previous_number = self.round_number
        if mvp is not None:
            self.mvps = mvp
        if team in ("T", "CT"):
            self.own_team = team
        self.map_phase = map_phase
        self.round_phase, self.round_number, self.completed = phase, number, completed

        if (previous_team and self.own_team != previous_team and self._boundary(completed)
                and phase != "live"):
            self.side_armed = True

        if not map_name or map_phase in ("warmup", "intermission", "gameover"):
            if map_phase != "gameover":
                self.cancel_results("离开正式回合")
            self._cancel_bomb()
            if map_phase != "gameover" and map_phase != previous_map_phase:
                self._stop("离开正式回合")
            if map_phase == "warmup":
                self.opening_armed = True
            # Final-round results remain eligible in gameover.
            if map_phase != "gameover":
                return self.actions

        if (phase == "freezetime" and previous_team and self.own_team != previous_team
                and self._boundary(completed)):
            self.side_armed = True
        if phase == "freezetime" and (previous_phase != phase or number != previous_number):
            self.round_team = self.own_team
            self.round_mvp_baseline = self.current_mvps
            self.bomb_state = ""
            self.plant_seen = False
            self._cancel_bomb()
        if phase == "freezetime" and map_phase == "live":
            # The previous round's result baseline is already frozen separately.
            # Stats recovered during buying become the upcoming round's baseline.
            if self.current_mvps is not None:
                self.round_mvp_baseline = self.current_mvps
            if self.opening_armed and completed == 0:
                self.opening_armed = False
                self._play("start")
            elif self.side_armed:
                self.side_armed = False
                self._play("side")
        if phase == "live" and (previous_phase != "live" or number != previous_number):
            self.cancel_results("正式交战开始，取消未确认结算")
            self.round_team = self.own_team
            if self.current_mvps is not None or previous_phase != "freezetime":
                self.round_mvp_baseline = self.current_mvps
            self._cancel_bomb()
            self.bomb_state = self._bomb_state(payload)
            self.plant_seen = self.bomb_state == "planted"
            self._stop("正式交战开始", when_live=True)

        if phase == "over" and (previous_phase != "over" or number != previous_number):
            self._cancel_bomb()
            self.result_round = number
            self.result_mvp_baseline = self.round_mvp_baseline
        if phase == "over" and number not in self.results_handled:
            win_team = rnd.get("win_team")
            if win_team in ("T", "CT") and self.round_team in ("T", "CT"):
                self.results_handled.add(number)
                event = "victory" if win_team == self.round_team else "defeat"
                if event == "defeat":
                    self._play("defeat", reason="回合获胜队伍与本人回合队伍不同")
                elif (number, "mvp") not in self.seen:
                    self.pending_result = (number, event, None)
        self._refresh_result_wait(now)
        has_mvp = (self.current_mvps is not None and self.result_mvp_baseline is not None
                   and self.current_mvps > self.result_mvp_baseline)
        if has_mvp and self.result_round is not None and phase in ("over", "freezetime"):
            self.pending_result = None
            # A new-phase start/side track has precedence over the previous round's result.
            if self.active_event not in ("start", "side"):
                self._play("mvp", self.result_round, "本人 MVP 计数超过该回合基线")

        if phase == "live" and map_phase == "live":
            self._update_bomb(payload, now)
        else:
            self._cancel_bomb()
        self.actions.extend(self._tick(now))
        return self.actions

    @staticmethod
    def _bomb_state(payload: dict) -> str:
        state = (payload.get("round") or {}).get("bomb", "")
        detailed = (payload.get("bomb") or {}).get("state", "")
        if state in ("defused", "exploded") or detailed in ("defused", "exploded"):
            return state if state in ("defused", "exploded") else detailed
        return "planted" if state == "planted" or detailed in ("planted", "defusing") else str(state or detailed)

    def _update_bomb(self, payload: dict, now: float):
        state = self._bomb_state(payload)
        if state != "planted":
            self.bomb_state = state
            self._cancel_bomb()
            return
        remaining = None
        bomb = payload.get("bomb") or {}
        countdown = payload.get("phase_countdowns") or {}
        candidates = [bomb.get("countdown")]
        if countdown.get("phase") == "bomb":
            candidates.append(countdown.get("phase_ends_in"))
        for value in candidates:
            try:
                value = float(value)
                if 0 <= value <= 120:
                    remaining = value
                    break
            except (ValueError, TypeError):
                pass
        if remaining is not None:
            self.bomb_deadline = now + remaining
            self.bomb_source = "GSI 精确倒计时"
        elif self.bomb_state != "planted" and not self.plant_seen:
            self.bomb_deadline = now + self.s.bomb_seconds - self.s.bomb_compensation
            self.bomb_source = "估算（可能受 GSI 延迟影响）"
        self.bomb_state = state
        self.plant_seen = True

    def _tick(self, now: float) -> list[Action]:
        actions = []
        if (self.pending_result and self.pending_result[2] is not None
                and now >= self.pending_result[2]):
            number, event, _ = self.pending_result
            self.pending_result = None
            if self.active_event not in ("start", "side", "mvp"):
                before = len(self.actions)
                self._play(event, number, "本人统计持续可用，确认窗口内 MVP 未增加")
                actions.extend(self.actions[before:])
                del self.actions[before:]
        if self.bomb_deadline is not None:
            remaining = self.bomb_deadline - now
            if remaining <= 0:
                self.bomb_deadline = None
                if self.active_event == "bomb":
                    self.active_event = ""
                    actions.append(Action("stop", reason="炸弹计时到期"))
            elif remaining <= 10:
                before = len(self.actions)
                self._play("bomb")
                actions.extend(self.actions[before:])
                del self.actions[before:]
        return actions

    def tick(self, now: float) -> list[Action]:
        self.actions = []
        if self.connected and now - self.last_received > self.STALE_SECONDS:
            self.connected = False
            self.current_mvps = None
            self.own_stats_source = "missing"
            self.cancel_results("GSI 超时，取消未确认结算")
            self._cancel_bomb()
            self._stop("GSI 超时，等待重新建立基线")
            return self.actions
        return self._tick(now)
