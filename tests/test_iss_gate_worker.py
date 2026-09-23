from skatai.iss.gate_worker import (
    PRIMARY_STACKS,
    canonical_opponent_stack,
    choose_arm_for_stratum,
    current_target,
    next_underfilled_stack,
    stack_complete,
)


def row(game_id, arm, stack, seat, score=0.0):
    return {
        "game_id": game_id,
        "arm": arm,
        "opponent": stack,
        "seat": seat,
        "score": score,
    }


def test_canonical_stack_uses_both_other_players():
    assert canonical_opponent_stack(
        ["SkatAI", "zoot", "kermit"], skatai_seat=0
    ) == "kermit+zoot"
    assert canonical_opponent_stack(
        ["theCount", "SkatAI", "kermit"], skatai_seat=1
    ) == "kermit+theCount"


def test_arm_assignment_balances_same_stack_and_seat():
    stack = "kermit+zoot"
    rows = [row("a", "B0", stack, 0)]
    x = choose_arm_for_stratum(rows, per_arm=18, stack=stack, seat=0)
    assert x.arm == "B1"
    assert x.primary is True


def test_nonprimary_stack_is_diagnostic():
    x = choose_arm_for_stratum([], per_arm=300, stack="human+zoot", seat=1)
    assert x.primary is False


def test_stack_completion_requires_both_arms_all_three_seats():
    stack = PRIMARY_STACKS[0]
    # per_arm=3 means one game per seat for this stack at this artificial look.
    rows = []
    for arm in ("B0", "B1"):
        for seat in (0, 1, 2):
            rows.append(row(f"{arm}-{seat}", arm, stack, seat))
    assert stack_complete(rows, per_arm=9, stack=stack) is True
    # 27 / 9 primary strata = 3 per stack/seat, so one each is insufficient.
    assert stack_complete(rows, per_arm=27, stack=stack) is False


def test_next_underfilled_stack_is_deterministic():
    assert next_underfilled_stack([], per_arm=300) == PRIMARY_STACKS[0]


def test_current_target_starts_at_first_frozen_look_without_strength_peek():
    target, gate = current_target([])
    assert target == 300
    assert gate is None


def test_active_game_offsets_are_explicit():
    from skatai.iss.gate_worker import ActiveGame, GameAssignment

    a = GameAssignment("B1", "kermit+zoot", 2, 300, True)
    g = ActiveGame(a, protocol_offset=123, effect_offset=45)
    assert g.assignment is a
    assert g.protocol_offset == 123
    assert g.effect_offset == 45


def test_next_stack_follows_global_frozen_priority_not_first_incomplete_only():
    # Make kermit+zoot slightly less deficient than kermit+theCount.
    rows = []
    for arm in ("B0", "B1"):
        for seat in (0, 1, 2):
            rows.append(row(f"kz-{arm}-{seat}", arm, "kermit+zoot", seat))
    assert next_underfilled_stack(rows, per_arm=300) == "kermit+theCount"


def test_evidence_manifest_binding_requires_both_protocol_and_effect_artifacts():
    import inspect
    from skatai.iss.gate_worker import GateEvidence

    source = inspect.getsource(GateEvidence.append_game)
    assert '"service_slice"' in source
    assert '"effect_slice"' in source
    assert "sha256_file(evidence_manifest)" in source
    assert "UNRESOLVED_EXTERNAL_EFFECTS_AT_TERMINAL" in source
    assert "STALE_EXTERNAL_EFFECTS_AT_TERMINAL" in source


def test_active_game_payload_roundtrip_and_commit_binding():
    from skatai.iss.gate_worker import (
        ActiveGame,
        GameAssignment,
        active_games_payload,
        parse_active_games_payload,
        ISSGateWorkerError,
    )

    games = {
        ("T7", 42): ActiveGame(
            GameAssignment("B1", "kermit+zoot", 2, 300, True),
            protocol_offset=123,
            effect_offset=456,
        )
    }
    payload = active_games_payload(games, source_commit="abc1234")
    restored = parse_active_games_payload(
        payload,
        expected_source_commit="abc1234",
    )
    assert restored == games

    try:
        parse_active_games_payload(payload, expected_source_commit="fffffff")
    except ISSGateWorkerError as exc:
        assert "SOURCE_COMMIT_MISMATCH" in str(exc)
    else:
        raise AssertionError("active game cannot cross source commits silently")


def test_empty_active_state_can_cross_commit():
    from skatai.iss.gate_worker import active_games_payload, parse_active_games_payload

    payload = active_games_payload({}, source_commit="old")
    assert parse_active_games_payload(payload, expected_source_commit="new") == {}


def test_reconnect_policy_has_bounded_exponential_delay_and_cycles():
    from skatai.iss.gate_worker import (
        ReconnectPolicy,
        reconnect_delay_s,
        reconnect_policy_from_environment,
    )

    p = ReconnectPolicy(base_delay_s=2, max_delay_s=10, attempts_per_cycle=3, cooldown_s=30)
    assert reconnect_delay_s(p, 1) == 2
    assert reconnect_delay_s(p, 2) == 4
    assert reconnect_delay_s(p, 5) == 10

    env = {
        "ISS_RECONNECT_BASE_DELAY_S": "1",
        "ISS_RECONNECT_MAX_DELAY_S": "8",
        "ISS_RECONNECT_ATTEMPTS_PER_CYCLE": "4",
        "ISS_RECONNECT_COOLDOWN_S": "60",
    }
    q = reconnect_policy_from_environment(env)
    assert q.base_delay_s == 1
    assert q.max_delay_s == 8
    assert q.attempts_per_cycle == 4
    assert q.cooldown_s == 60


def test_transport_error_classifier_does_not_retry_bad_login():
    from skatai.iss.gate_worker import recoverable_transport_error
    from skatai.iss.transport import ISSTransportError

    assert recoverable_transport_error(ISSTransportError("REMOTE_EOF"))
    assert recoverable_transport_error(ISSTransportError("READ_FAILED:TimeoutError"))
    assert not recoverable_transport_error(
        ISSTransportError("LOGIN_EXPECTED_WELCOME")
    )


def test_reconnect_start_reuses_frozen_active_assignment_and_offsets():
    from skatai.iss.gate_worker import ActiveGame, ExternalGateWorker, GameAssignment

    class Switch:
        def __init__(self):
            self.arm = None
        def set_arm(self, arm):
            self.arm = arm

    class Evidence:
        def scored_rows(self):
            raise AssertionError("must not reselect arm on reconnect")
        def _file_size(self, path):
            raise AssertionError("must not reset offsets on reconnect")

    w = object.__new__(ExternalGateWorker)
    w.assignment_by_game = {
        ("T", 7): ActiveGame(
            GameAssignment("B1", "kermit+zoot", 0, 300, True),
            protocol_offset=10,
            effect_offset=20,
        )
    }
    w.switch = Switch()
    w.evidence = Evidence()
    w._persist_active_game_authority = lambda: (_ for _ in ()).throw(
        AssertionError("existing reconnect state must not be rewritten")
    )

    line = "table T SkatAI start 7 SkatAI 100 kermit 100 zoot 100"
    w._on_start_preapply(line)
    assert w.switch.arm == "B1"
    active = w.assignment_by_game[("T", 7)]
    assert active.protocol_offset == 10
    assert active.effect_offset == 20


def test_reconnect_create_for_active_game_does_not_reinvite_or_ready():
    from skatai.iss.gate_worker import ActiveGame, ExternalGateWorker, GameAssignment
    from skatai.iss.service import parse_service_line

    class Client:
        def __init__(self):
            self.sent = []
        def send_service_command(self, line):
            self.sent.append(line)

    w = object.__new__(ExternalGateWorker)
    w.client = Client()
    w.desired_stack = "kermit+zoot"
    w.table_id = "T"
    w.assignment_by_game = {
        ("T", 7): ActiveGame(
            GameAssignment("B1", "kermit+zoot", 0, 300, True),
            protocol_offset=10,
            effect_offset=20,
        )
    }
    w._on_create(parse_service_line("create T SkatAI 3"))
    assert w.client.sent == []


def test_run_retries_recoverable_transport_failure_then_resumes(monkeypatch, tmp_path):
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker, ReconnectPolicy
    from skatai.iss.transport import ISSTransportError

    class Mirror:
        def probe(self):
            return {"ok": True}

    class Evidence:
        def __init__(self):
            self.mirror = Mirror()
            self.events = []
        def append_connection_event(self, event, **fields):
            self.events.append((event, fields))

    w = object.__new__(ExternalGateWorker)
    w.paths = type("P", (), {"runtime_root": tmp_path})()
    w.evidence = Evidence()
    w._transport_failure_streak = 0
    w._restore_active_game_authority = lambda: None

    calls = []
    def connected_session(*, client_policy):
        calls.append(1)
        if len(calls) == 1:
            raise ISSTransportError("REMOTE_EOF")
        return {"finished": True}

    w._run_connected_session = connected_session
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})
    monkeypatch.setattr(
        gw,
        "reconnect_policy_from_environment",
        lambda: ReconnectPolicy(
            base_delay_s=1,
            max_delay_s=4,
            attempts_per_cycle=3,
            cooldown_s=30,
        ),
    )
    sleeps = []
    monkeypatch.setattr(gw.time, "sleep", sleeps.append)

    assert w.run() == {"finished": True}
    assert len(calls) == 2
    assert sleeps == [1]
    assert w._transport_failure_streak == 1
    assert w.evidence.events[0][0] == "RECONNECT_WAIT"


def test_run_does_not_retry_authentication_protocol_failure(monkeypatch, tmp_path):
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker
    from skatai.iss.transport import ISSTransportError

    class Mirror:
        def probe(self):
            return {"ok": True}

    class Evidence:
        mirror = Mirror()

    w = object.__new__(ExternalGateWorker)
    w.paths = type("P", (), {"runtime_root": tmp_path})()
    w.evidence = Evidence()
    w._transport_failure_streak = 0
    w._restore_active_game_authority = lambda: None
    w._run_connected_session = lambda **kwargs: (_ for _ in ()).throw(
        ISSTransportError("LOGIN_EXPECTED_WELCOME")
    )
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})

    import pytest
    with pytest.raises(ISSTransportError, match="LOGIN_EXPECTED_WELCOME"):
        w.run()
