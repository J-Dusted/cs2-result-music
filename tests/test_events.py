from copy import deepcopy

import pytest

from cs2music.config import Settings
from cs2music.events import EventEngine


def state(phase="live", number=0, team="CT", mvps=0, map_phase="live", score=None, **extras):
    score = number if score is None else score
    payload = {
        "provider": {"appid": 730, "steamid": "self"},
        "map": {"name": "de_test", "phase": map_phase, "round": number,
                "team_ct": {"score": score}, "team_t": {"score": 0}},
        "round": {"phase": phase},
        "player": {"steamid": "self", "team": team, "match_stats": {"mvps": mvps}},
    }
    for key, value in extras.items():
        if isinstance(value, dict):
            payload.setdefault(key, {}).update(value)
        else:
            payload[key] = value
    return payload


def plays(actions):
    return [a.event for a in actions if a.kind == "play"]


def test_baseline_does_not_replay_result_or_plant():
    engine = EventEngine(Settings())
    assert not plays(engine.ingest(state("over", round={"win_team": "CT"}), 0))
    assert not plays(engine.ingest(state("over", round={"win_team": "CT"}), 1))
    engine = EventEngine(Settings())
    engine.ingest(state(round={"bomb": "planted"}), 0)
    for now in range(1, 40):
        assert not plays(engine.ingest(state(round={"bomb": "planted"}), now))
    assert engine.bomb_deadline is None


def test_victory_waits_mvp_overrides_and_duplicates_do_not_replay():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    over = state("over", round={"win_team": "CT"})
    assert not plays(engine.ingest(over, 1))
    assert not plays(engine.tick(2.49))
    assert plays(engine.ingest(state("over", mvps=1, round={"win_team": "CT"}), 2.5)) == ["mvp"]
    assert not plays(engine.ingest(state("over", mvps=1, round={"win_team": "CT"}), 3))
    assert not plays(engine.tick(4))


def test_victory_fallback_late_mvp_next_freeze_and_live_cutoff():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    engine.ingest(state("over", round={"win_team": "CT"}), 1)
    assert plays(engine.tick(2.5)) == ["victory"]
    assert plays(engine.ingest(state("freezetime", number=1, mvps=1), 3)) == ["mvp"]
    assert any(a.kind == "stop" for a in engine.ingest(state("live", number=1, mvps=1), 4))
    assert not plays(engine.ingest(state("live", number=1, mvps=2), 4.5))


def test_late_win_team_and_final_round_gameover():
    engine = EventEngine(Settings())
    engine.ingest(state(number=20), 0)
    engine.ingest(state("over", number=20, map_phase="gameover"), 1)
    engine.ingest(state("over", number=20, map_phase="gameover", round={"win_team": "CT"}), 1.2)
    assert plays(engine.ingest(state("over", number=20, map_phase="gameover", round={"win_team": "CT"}), 2.8)) == ["victory"]


def test_dead_spectator_identity_and_original_team():
    engine = EventEngine(Settings())
    engine.ingest(state(team="CT", mvps=2), 0)
    over = state("over", team="T", mvps=9, round={"win_team": "T"})
    over["player"]["steamid"] = "teammate"
    assert plays(engine.ingest(over, 1)) == ["defeat"]
    assert engine.mvps == 2 and engine.own_team == "CT"
    over["allplayers"] = {"self": {"team": "CT", "match_stats": {"mvps": 2}}}
    assert not plays(engine.ingest(over, 2))


def test_missing_own_identity_does_not_guess_result():
    engine = EventEngine(Settings())
    initial = state()
    initial["player"]["steamid"] = "someone_else"
    engine.ingest(initial, 0)
    over = deepcopy(initial)
    over["round"] = {"phase": "over", "win_team": "CT"}
    assert not plays(engine.ingest(over, 1))
    assert not plays(engine.tick(3))


def test_warmup_only_first_freeze_and_stop_at_live():
    engine = EventEngine(Settings())
    engine.ingest(state(map_phase="warmup"), 0)
    assert plays(engine.ingest(state("freezetime"), 1)) == ["start"]
    assert not plays(engine.ingest(state("freezetime"), 2))
    assert any(a.kind == "stop" for a in engine.ingest(state("live"), 3))
    assert not plays(engine.ingest(state("freezetime", number=1), 4))


@pytest.mark.parametrize("rounds", [12, 24, 27, 30, 33])
def test_side_change_boundaries_and_late_local_identity(rounds):
    engine = EventEngine(Settings())
    engine.ingest(state(number=rounds - 1), 0)
    engine.ingest(state("over", number=rounds - 1, score=rounds, round={"win_team": "CT"}), 1)
    engine.ingest(state("freezetime", number=rounds, team="CT"), 2)
    assert plays(engine.ingest(state("freezetime", number=rounds, team="T"), 2.2)) == ["side"]
    assert not plays(engine.ingest(state("freezetime", number=rounds, team="T"), 3))


def test_regular_team_change_is_not_halftime():
    engine = EventEngine(Settings())
    engine.ingest(state(number=3), 0)
    assert not plays(engine.ingest(state("freezetime", number=4, team="T"), 1))


def test_estimated_bomb_threshold_cancellation_and_compensation():
    settings = Settings(bomb_compensation=2)
    engine = EventEngine(settings)
    engine.ingest(state(), 0)
    planted = state(round={"bomb": "planted"})
    engine.ingest(planted, 1)
    assert engine.bomb_deadline == 39
    for now in range(2, 29):
        assert not plays(engine.ingest(planted, now))
    assert plays(engine.ingest(planted, 29)) == ["bomb"]
    assert not plays(engine.ingest(planted, 30))
    assert any(a.kind == "stop" for a in engine.ingest(state(round={"bomb": "defused"}), 31))


def test_exact_countdown_supersedes_estimate_and_defuse_time_is_not_explosion():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    engine.ingest(state(round={"bomb": "planted"}, phase_countdowns={"phase": "defuse", "phase_ends_in": "4"}), 1)
    assert "估算" in engine.bomb_source
    assert not plays(engine.tick(2))
    assert plays(engine.ingest(state(round={"bomb": "planted"}, bomb={"state": "defusing", "countdown": "9.8"}), 3)) == ["bomb"]
    assert "精确" in engine.bomb_source


def test_bomb_round_end_stale_and_reconnect_reset():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    engine.ingest(state(round={"bomb": "planted"}), 1)
    assert any(a.kind == "stop" for a in engine.tick(6.1))
    assert not engine.connected and engine.bomb_deadline is None
    engine.ingest(state(round={"bomb": "planted"}), 7)
    assert engine.bomb_deadline is None
    assert not plays(engine.ingest(state("over", round={"win_team": "T"}), 8)) == ["bomb"]


def test_map_reset_and_restarted_match_clear_mvp_baseline():
    engine = EventEngine(Settings())
    engine.ingest(state(number=9, mvps=4), 0)
    assert not plays(engine.ingest(state(number=0, mvps=0), 1))
    changed = state("over", round={"win_team": "CT"})
    changed["map"]["name"] = "de_other"
    assert not plays(engine.ingest(changed, 2))
    assert not plays(engine.tick(4))


def test_mvp_statistics_arrive_before_round_over_field():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    engine.ingest(state(mvps=1), 1)
    assert plays(engine.ingest(state("over", mvps=1, round={"win_team": "CT"}), 1.1)) == ["mvp"]


def test_missing_bomb_field_does_not_restart_mid_round_estimate():
    engine = EventEngine(Settings())
    engine.ingest(state(round={"bomb": "planted"}), 0)
    engine.ingest(state(), 1)
    engine.ingest(state(round={"bomb": "planted"}), 2)
    assert engine.bomb_deadline is None


def test_missed_freeze_new_round_cancels_previous_bomb():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    engine.ingest(state(round={"bomb": "planted"}), 1)
    engine.ingest(state(number=1), 2)
    assert engine.bomb_deadline is None
    engine.ingest(state(number=1, round={"bomb": "planted"}), 3)
    assert engine.bomb_deadline == 43


def test_stable_warmup_does_not_interrupt_manual_preview_every_heartbeat():
    engine = EventEngine(Settings())
    engine.ingest(state(map_phase="warmup"), 0)
    assert engine.ingest(state(map_phase="warmup"), 1) == []
