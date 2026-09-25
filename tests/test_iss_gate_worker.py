from skatai.iss.gate_worker import (
    PRIMARY_STACKS,
    canonical_opponent_stack,
    choose_arm_for_stratum,
    current_target,
    next_underfilled_stack,
    stack_complete,
)


def test_pinned_repo_cannot_fall_back_to_default_evidence_root(tmp_path, monkeypatch):
    import pytest
    from skatai.iss.gate_worker import GatePaths, ISSGateWorkerError

    monkeypatch.setenv("SKATAI_V2_ROOT", str(tmp_path / "pinned-r9"))
    monkeypatch.delenv("ISS_GATE_RUNTIME_ROOT", raising=False)
    with pytest.raises(ISSGateWorkerError, match="EXPLICIT_GATE_RUNTIME_ROOT"):
        GatePaths.defaults()
    evidence_root = tmp_path / "external-gate-r9"
    monkeypatch.setenv("ISS_GATE_RUNTIME_ROOT", str(evidence_root))
    assert GatePaths.defaults().runtime_root == evidence_root


def test_evidence_mirror_recovers_persisted_remote_root(tmp_path, monkeypatch):
    import json
    from skatai.iss.gate_worker import HetznerEvidenceMirror

    monkeypatch.delenv("ISS_GATE_S3_PREFIX", raising=False)
    expected = ":s3:skatai-v2/evidence/V2-B1-bidding-linearish-full-v1/external-iss-gate-r3"
    (tmp_path / "object-storage-readiness.json").write_text(
        json.dumps({"ok": True, "remote_root": expected, "returncode": 0}),
        encoding="utf-8",
    )

    assert HetznerEvidenceMirror(local_root=tmp_path).remote_root == expected


def test_evidence_mirror_readiness_probe_has_bounded_timeout(tmp_path, monkeypatch):
    import subprocess
    from skatai.iss.gate_worker import HetznerEvidenceMirror

    def stalled(*args, **kwargs):
        assert args[0][:3] == ["rclone", "lsd", ":s3:skatai-v2"]
        assert kwargs["timeout"] == 30.0
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", stalled)
    outcome = HetznerEvidenceMirror(local_root=tmp_path).probe()
    assert outcome["ok"] is False
    assert outcome["reason"] == "RCLONE_READINESS_TIMEOUT"


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
            self.binding = None
        def bind_game(self, table_id, game_sequence, arm):
            self.binding = (table_id, game_sequence, arm)

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
    assert w.switch.binding == ("T", 7, "B1")
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
    w.assignment_by_game = {}
    w._mirror_pause_requested = False
    w._restore_active_game_authority = lambda: None
    w.mirror_writebehind = type(
        "MirrorWriteBehindStub",
        (),
        {
            "start": lambda self: None,
            "stop": lambda self, *, flush: None,
            "backpressure_required": lambda self: False,
        },
    )()

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


def test_run_waits_for_background_mirror_after_confirmed_table_destroy(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker

    pending = [False, True, True, False]
    events = []
    class Evidence:
        mirror = SimpleNamespace(probe=lambda: {"ok": True})
        def _write_mirror_status(self, **kwargs):
            events.append(kwargs["state"])

    w = object.__new__(ExternalGateWorker)
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.evidence = Evidence()
    w._transport_failure_streak = 0
    w.assignment_by_game = {}
    w._mirror_pause_requested = False
    w._restore_active_game_authority = lambda: None
    w.mirror_policy = SimpleNamespace(poll_interval_s=0.1)
    w.mirror_writebehind = SimpleNamespace(
        start=lambda: events.append("start"),
        stop=lambda *, flush: events.append(("stop", flush)),
        backpressure_required=lambda: pending.pop(0),
    )
    sessions = []
    def connected_session(*, client_policy):
        sessions.append(len(sessions))
        events.append("session")
        if len(sessions) == 1:
            return {"mirror_paused": True}
        return {"finished": True}
    w._run_connected_session = connected_session
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})
    monkeypatch.setattr(gw.time, "sleep", lambda seconds: events.append("wait"))

    assert w.run() == {"finished": True}
    assert events == ["start", "session", "BACKPRESSURE", "wait", "BACKPRESSURE", "wait", "session", ("stop", True), ("stop", False)]


def test_restart_does_not_admit_new_table_until_existing_backlog_drains(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker

    events = []
    pending = [True, True, False]
    class Evidence:
        mirror = SimpleNamespace(probe=lambda: {"ok": True})
        def _write_mirror_status(self, **kwargs):
            events.append(kwargs["state"])

    w = object.__new__(ExternalGateWorker)
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.evidence = Evidence()
    w.assignment_by_game = {}
    w._transport_failure_streak = 0
    w._restore_active_game_authority = lambda: None
    w.mirror_policy = SimpleNamespace(poll_interval_s=0.1)
    w.mirror_writebehind = SimpleNamespace(
        start=lambda: events.append("start"),
        stop=lambda *, flush: events.append(("stop", flush)),
        backpressure_required=lambda: pending.pop(0),
    )
    w._run_connected_session = lambda *, client_policy: events.append("session") or {"finished": True}
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})
    monkeypatch.setattr(gw.time, "sleep", lambda seconds: events.append("wait"))

    assert w.run() == {"finished": True}
    assert events == ["start", "BACKPRESSURE", "wait", "BACKPRESSURE", "wait", "session", ("stop", True), ("stop", False)]


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
    w.assignment_by_game = {}
    w._mirror_pause_requested = False
    w._restore_active_game_authority = lambda: None
    w.mirror_writebehind = type(
        "MirrorWriteBehindStub",
        (),
        {
            "start": lambda self: None,
            "stop": lambda self, *, flush: None,
            "backpressure_required": lambda self: False,
        },
    )()
    w._run_connected_session = lambda **kwargs: (_ for _ in ()).throw(
        ISSTransportError("LOGIN_EXPECTED_WELCOME")
    )
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})

    import pytest
    with pytest.raises(ISSTransportError, match="LOGIN_EXPECTED_WELCOME"):
        w.run()


def test_fresh_epoch_create_ignores_replayed_foreign_table():
    from skatai.iss.gate_worker import ExternalGateWorker
    from skatai.iss.service import parse_service_line

    class Client:
        def __init__(self):
            self.sent = []
        def send_service_command(self, line):
            self.sent.append(line)

    w = object.__new__(ExternalGateWorker)
    w.client = Client()
    w.desired_stack = "kermit+zoot"
    w.table_id = None
    w._expected_new_table_id = "NEW"
    w.assignment_by_game = {}

    w._on_create(parse_service_line("create OLD SkatAI 3"))
    assert w.table_id is None
    assert w._expected_new_table_id == "NEW"
    assert w.client.sent == []

    w._on_create(parse_service_line("create NEW SkatAI 3"))
    assert w.table_id == "NEW"
    assert w._expected_new_table_id is None
    assert w.client.sent == [
        "table NEW SkatAI invite kermit",
        "table NEW SkatAI invite zoot",
        "table NEW SkatAI ready",
    ]


def test_table_event_admission_is_epoch_scoped():
    from skatai.iss.gate_worker import ActiveGame, ExternalGateWorker, GameAssignment
    from skatai.iss.service import parse_service_line

    w = object.__new__(ExternalGateWorker)
    w.table_id = "CURRENT"
    w._expected_new_table_id = "EXPECTED"
    w.assignment_by_game = {
        ("RESTORED", 7): ActiveGame(
            GameAssignment("B1", "kermit+zoot", 0, 300, True),
            protocol_offset=10,
            effect_offset=20,
        )
    }

    assert w._event_is_admitted(parse_service_line("create CURRENT SkatAI 3"))
    assert w._event_is_admitted(parse_service_line("create EXPECTED SkatAI 3"))
    assert w._event_is_admitted(
        parse_service_line("table RESTORED SkatAI start 7 SkatAI 100 kermit 100 zoot 100")
    )
    assert not w._event_is_admitted(parse_service_line("create FOREIGN SkatAI 3"))
    assert not w._event_is_admitted(
        parse_service_line("table FOREIGN SkatAI start 1 SkatAI 100 kermit 100 zoot 100")
    )


def test_terminal_less_failure_is_append_only_and_idempotent(tmp_path):
    import json
    from pathlib import Path
    from skatai.iss.gate_worker import GateEvidence, GatePaths, GameAssignment

    paths = GatePaths(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        skatzero_root=tmp_path / "b0",
        skatzero_python=tmp_path / "python",
        b1_model=tmp_path / "b1.pt",
    )
    identities = {
        "B0": {
            "deployment_identity_sha256": "a" * 64,
            "release_id": "B0-test",
        },
        "B1": {
            "deployment_identity_sha256": "b" * 64,
            "release_id": "B1-test",
        },
    }
    ev = GateEvidence(paths=paths, identities=identities, source_commit="abc1234")
    ev.protocol_journal.parent.mkdir(parents=True, exist_ok=True)
    ev.protocol_journal.write_text('{"direction":"in","line":"table T SkatAI start 1"}\n')
    ev.effect_journal.write_text("")
    assignment = GameAssignment("B0", "kermit+zoot", 1, 300, True)

    first = ev.append_failure_without_terminal(
        assignment=assignment,
        table_id="T",
        game_sequence=1,
        protocol_offset=0,
        effect_offset=0,
        status="PROTOCOL_FAILURE",
        failure_reason="RECOVERY_NO_TERMINAL",
    )
    second = ev.append_failure_without_terminal(
        assignment=assignment,
        table_id="T",
        game_sequence=1,
        protocol_offset=0,
        effect_offset=0,
        status="PROTOCOL_FAILURE",
        failure_reason="RECOVERY_NO_TERMINAL",
    )

    assert first["game_id"] == second["game_id"]
    assert first["duplicate_reused"] is False
    assert second["duplicate_reused"] is True
    rows = ev.ledger.records()
    assert len(rows) == 1
    assert rows[0].status == "PROTOCOL_FAILURE"
    assert rows[0].score is None
    assert rows[0].raw_sgf_sha256 is None
    evidence = json.loads(
        (ev.games_dir / f"{first['game_id']}.evidence.json").read_text()
    )
    assert evidence["terminal_sgf_present"] is False
    assert evidence["artifacts"]["service_slice"]["bytes"] > 0
    assert not (ev.games_dir / f"{first['game_id']}.sgf").exists()


def test_terminal_less_failure_mirror_does_not_require_sgf(tmp_path):
    from skatai.iss.gate_worker import GateEvidence, GatePaths, GameAssignment

    paths = GatePaths(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        skatzero_root=tmp_path / "b0",
        skatzero_python=tmp_path / "python",
        b1_model=tmp_path / "b1.pt",
    )
    identities = {
        "B0": {"deployment_identity_sha256": "a" * 64, "release_id": "B0-test"},
        "B1": {"deployment_identity_sha256": "b" * 64, "release_id": "B1-test"},
    }
    ev = GateEvidence(paths=paths, identities=identities, source_commit="abc1234")
    ev.protocol_journal.parent.mkdir(parents=True, exist_ok=True)
    ev.protocol_journal.write_text("{}\n")
    ev.effect_journal.write_text("")
    result = ev.append_failure_without_terminal(
        assignment=GameAssignment("B0", "kermit+zoot", 1, 300, True),
        table_id="T",
        game_sequence=1,
        protocol_offset=0,
        effect_offset=0,
        status="PROTOCOL_FAILURE",
        failure_reason="RECOVERY_NO_TERMINAL",
    )
    uploads = []
    ev.mirror.upload_verified = lambda local, remote: uploads.append((local, remote)) or {
        "local": str(local), "remote": remote, "sha256": "0" * 64, "bytes": local.stat().st_size
    }
    ev.mirror_game(result["game_id"])
    assert uploads
    assert all(local.exists() for local, _ in uploads)
    assert not any(remote.endswith(".sgf") for _, remote in uploads)



def test_upload_verified_uses_immutable_snapshot_for_mutable_source(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    from pathlib import Path

    import skatai.iss.gate_worker as worker

    source = tmp_path / "service.jsonl"
    original = b'{"n":1}\n'
    source.write_bytes(original)
    copied = {}

    def fake_run(args, **kwargs):
        if args[0:2] == ["rclone", "copyto"]:
            copied["path"] = Path(args[2])
            copied["bytes"] = copied["path"].read_bytes()
            source.write_bytes(original + b'{"n":2}\n')
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        if args[0:2] == ["rclone", "cat"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=copied["bytes"], stderr=b""
            )
        raise AssertionError(args)

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    mirror = worker.HetznerEvidenceMirror(local_root=tmp_path)
    result = mirror.upload_verified(source, "current/service.jsonl")

    assert copied["path"] != source
    assert copied["bytes"] == original
    assert source.read_bytes() != original
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["bytes"] == len(original)



def test_active_table_error_is_persisted_as_protocol_failure_and_stops_worker(tmp_path):
    import pytest
    from types import SimpleNamespace

    from skatai.iss.effects import ISSAuthorityGuard, ISSEffectJournal
    from skatai.iss.gate_worker import (
        ActiveGame,
        ExternalGateWorker,
        GameAssignment,
        ISSGateWorkerError,
    )
    from tests.test_iss_effects import request_result

    calls = []
    sent = []

    class Evidence:
        def append_failure_without_terminal(self, **kwargs):
            calls.append(("append", kwargs))
            return {"game_id": "failure-game"}

        def persist_active_games(self, games, *, source_commit):
            calls.append(("persist", dict(games), source_commit))

        def mirror_game(self, game_id):
            calls.append(("mirror", game_id))
            return {"game_id": game_id}

    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    req, result = request_result()
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash="a" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    journal.mark_send_returned(effect.effect_id)

    worker = object.__new__(ExternalGateWorker)
    worker.source_commit = "abc1234"
    worker.evidence = Evidence()
    worker.effect_guard = ISSAuthorityGuard(journal)
    worker.assignment_by_game = {
        ("T", 1): ActiveGame(
            assignment=GameAssignment("B0", "kermit+zoot", 0, 300, True),
            protocol_offset=11,
            effect_offset=22,
        )
    }
    worker.client = SimpleNamespace(send_service_command=sent.append)
    worker.table_id = "T"
    worker.desired_stack = "kermit+zoot"
    worker._expected_new_table_id = None
    worker._table_password = "ephemeral"

    event = SimpleNamespace(fields={"text": "play : _you_do_not_have_card HT."})
    table = SimpleNamespace(table_id="T", game_sequence=1, viewer_name="SkatAI")

    with pytest.raises(ISSGateWorkerError, match="ISS_TABLE_ERROR_DURING_ACTIVE_GAME"):
        worker._on_table_error(event, table)

    append = next(row for row in calls if row[0] == "append")
    assert append[1]["status"] == "PROTOCOL_FAILURE"
    assert append[1]["failure_reason"].startswith("ISS_TABLE_ERROR:")
    assert append[1]["protocol_offset"] == 11
    assert append[1]["effect_offset"] == 22
    assert journal.pending_for_game("T", 1) == []
    assert worker.assignment_by_game == {}
    assert any(row[0] == "persist" and row[1] == {} for row in calls)
    assert ("mirror", "failure-game") in calls
    assert sent == ["table T SkatAI leave"]
    assert worker.table_id is None



def test_stack_rotation_keeps_departing_table_admitted_until_destroy(monkeypatch):
    from types import SimpleNamespace

    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ActiveGame, ExternalGateWorker, GameAssignment
    from skatai.iss.service import parse_service_line

    sent = []
    queued = []

    class Evidence:
        def append_game(self, **kwargs):
            return {"result": {"game_id": "g"}}

        def enqueue_mirror_game(self, game_id):
            queued.append(game_id)

        def scored_rows(self):
            return []

    class Switch:
        def latency_summary(self, game_id):
            return 1.0, 2.0

    worker = object.__new__(ExternalGateWorker)
    worker.client = SimpleNamespace(send_service_command=sent.append)
    worker.switch = Switch()
    worker.evidence = Evidence()
    worker.assignment_by_game = {
        ("T", 6): ActiveGame(
            assignment=GameAssignment("B1", "kermit+zoot", 0, 300, True),
            protocol_offset=11,
            effect_offset=22,
        )
    }
    worker.table_id = "T"
    worker.desired_stack = "kermit+zoot"
    worker._expected_new_table_id = None
    worker._table_password = "ephemeral"
    worker._transport_failure_streak = 0
    worker._persist_active_game_authority = lambda: None
    worker.mirror_writebehind = SimpleNamespace(backpressure_required=lambda: False)
    worker.campaign_status = lambda: {
        "next_per_arm_target": 300,
        "next_stack": "kermit+theCount",
    }
    monkeypatch.setattr(
        gw,
        "next_underfilled_stack",
        lambda rows, *, per_arm: "kermit+theCount",
    )

    table = SimpleNamespace(
        table_id="T",
        game_sequence=6,
        game_sgf="(;GM[Skat])",
        viewer_name="SkatAI",
    )

    assert worker._on_end(SimpleNamespace(), table) is True
    assert queued == ["g"]
    assert sent == ["table T SkatAI leave"]
    assert worker.desired_stack is None
    assert worker.table_id == "T"
    assert worker._event_is_admitted(parse_service_line("destroy T SkatAI"))
    worker._mirror_pause_requested = False
    worker.mirror_writebehind = SimpleNamespace(backpressure_required=lambda: True)
    worker.evidence._write_mirror_status = lambda **kwargs: None
    worker._create_next_table = lambda: (_ for _ in ()).throw(AssertionError("new game admitted under pressure"))
    assert worker._on_destroy("T") is True
    assert worker.table_id is None


def test_mirror_pressure_leaves_only_after_closed_game_and_pauses_on_destroy(tmp_path):
    from types import SimpleNamespace
    from skatai.iss.gate_worker import ActiveGame, ExternalGateWorker, GameAssignment

    sent = []
    events = []
    class Evidence:
        protocol_journal = tmp_path / "service.jsonl"
        def _file_size(self, path):
            return path.stat().st_size if path.exists() else 0
        def append_game(self, **kwargs):
            events.append("closed")
            return {"result": {"game_id": "g"}}
        def enqueue_mirror_game(self, game_id):
            events.append("queued")
        def _write_mirror_status(self, **kwargs):
            events.append(kwargs["state"])

    w = object.__new__(ExternalGateWorker)
    w.client = SimpleNamespace(send_service_command=sent.append)
    w.switch = SimpleNamespace(latency_summary=lambda game_id: (1.0, 2.0))
    w.evidence = Evidence()
    w.assignment_by_game = {
        ("T", 1): ActiveGame(GameAssignment("B0", "kermit+zoot", 0, 300, True), 0, 0)
    }
    w.table_id = "T"
    w.desired_stack = "kermit+zoot"
    w._expected_new_table_id = None
    w._table_password = "ephemeral"
    w._transport_failure_streak = 0
    w._mirror_pause_requested = False
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.source_commit = "test-source"
    w._persist_active_game_authority = lambda: events.append("authority")
    w.campaign_status = lambda: {"next_per_arm_target": 300}
    w.mirror_writebehind = SimpleNamespace(backpressure_required=lambda: True)
    table = SimpleNamespace(table_id="T", game_sequence=1, game_sgf="(;GM[Skat])", viewer_name="SkatAI")

    assert w._on_end(SimpleNamespace(), table) is True
    assert events == ["closed", "queued", "authority", "BACKPRESSURE"]
    assert sent == ["table T SkatAI leave"]
    assert w.assignment_by_game == {}
    assert w._mirror_pause_requested is True
    marker = tmp_path / "mirror-departure-pending.json"
    assert marker.is_file()
    import json
    assert json.loads(marker.read_text())["protocol_offset"] == 0
    assert json.loads(marker.read_text())["viewer_name"] == "SkatAI"
    assert w._on_destroy("T") is True
    assert not marker.exists()
    assert w.table_id is None
    assert w._mirror_pause_requested is False


def test_unconfirmed_mirror_departure_fails_closed_on_restart(monkeypatch, tmp_path):
    import json
    import pytest
    from types import SimpleNamespace
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker, ISSGateWorkerError

    marker = tmp_path / "mirror-departure-pending.json"
    marker.write_text(json.dumps({"table_id": "T", "game_id": "g"}))
    w = object.__new__(ExternalGateWorker)
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.source_commit = "test-source"
    monkeypatch.setattr(gw, "readiness", lambda paths: (_ for _ in ()).throw(AssertionError("must not prepare ISS")))
    with pytest.raises(ISSGateWorkerError, match="MIRROR_DEPARTURE_OUTCOME_UNKNOWN"):
        w.run()
    assert marker.exists()


def test_mirror_departure_reconciles_only_later_matching_leave_and_destroy(tmp_path):
    import json
    from skatai.iss.gate_worker import reconcile_mirror_departure

    journal = tmp_path / "service.jsonl"
    old = {"direction": "in", "line": "destroy T SkatAI"}
    journal.write_text(json.dumps(old) + "\n")
    offset = journal.stat().st_size
    marker = tmp_path / "mirror-departure-pending.json"
    marker.write_text(json.dumps({
        "schema": "skatai.v2.iss-mirror-departure-pending.v2",
        "source_commit": "commit-a", "table_id": "T", "viewer_name": "SkatAI",
        "game_id": "game-a", "protocol_offset": offset,
    }))
    assert not reconcile_mirror_departure(marker, journal, source_commit="commit-a")
    with journal.open("a") as stream:
        stream.write(json.dumps({"direction": "out", "line": "table T SkatAI leave"}) + "\n")
        stream.write(json.dumps({"direction": "in", "line": "destroy U SkatAI"}) + "\n")
    assert not reconcile_mirror_departure(marker, journal, source_commit="commit-a")
    assert not reconcile_mirror_departure(marker, journal, source_commit="commit-b")
    with journal.open("a") as stream:
        stream.write(json.dumps({"direction": "in", "line": "destroy T SkatAI"}) + "\n")
    assert reconcile_mirror_departure(marker, journal, source_commit="commit-a")
    assert not marker.exists()
    evidence = json.loads((tmp_path / "mirror-departure-reconciliation.json").read_text())
    assert evidence["outcome"] == "CONFIRMED_DEPARTURE_NO_ACTION_REPLAY"
    assert evidence["leave_offset"] >= offset
    assert evidence["destroy_offset"] > evidence["leave_offset"]


def test_mirror_departure_rejects_unjournaled_and_ambiguous_effects(tmp_path):
    import json
    from skatai.iss.gate_worker import reconcile_mirror_departure

    journal = tmp_path / "service.jsonl"
    marker = tmp_path / "mirror-departure-pending.json"
    marker.write_text(json.dumps({
        "schema": "skatai.v2.iss-mirror-departure-pending.v2",
        "source_commit": "commit-a", "table_id": "T", "viewer_name": "SkatAI",
        "game_id": "game-a", "protocol_offset": 0,
    }))
    lines = [
        {"direction": "in", "line": "destroy T SkatAI"},
        {"direction": "out", "line": "table T SkatAI leave"},
        {"direction": "out", "line": "table T SkatAI leave"},
        {"direction": "in", "line": "destroy T SkatAI"},
    ]
    journal.write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert not reconcile_mirror_departure(marker, journal, source_commit="commit-a")
    assert marker.exists()
    journal.write_text(json.dumps(lines[0]) + "\n")
    assert not reconcile_mirror_departure(marker, journal, source_commit="commit-a")


def test_restart_resumes_after_durable_mirror_departure_reconciliation(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker

    (tmp_path / "mirror-departure-pending.json").write_text(json.dumps({
        "schema": "skatai.v2.iss-mirror-departure-pending.v2",
        "source_commit": "commit-a", "table_id": "T", "viewer_name": "SkatAI",
        "game_id": "game-a", "protocol_offset": 0,
    }))
    (tmp_path / "service.jsonl").write_text(
        json.dumps({"direction": "out", "line": "table T SkatAI leave"}) + "\n"
        + json.dumps({"direction": "in", "line": "destroy T SkatAI"}) + "\n"
    )
    calls = []
    w = object.__new__(ExternalGateWorker)
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.source_commit = "commit-a"
    w.evidence = SimpleNamespace(mirror=SimpleNamespace(probe=lambda: {"ok": True}))
    w.assignment_by_game = {}
    w._restore_active_game_authority = lambda: calls.append("restore")
    w.mirror_writebehind = SimpleNamespace(
        start=lambda: calls.append("start"),
        stop=lambda *, flush: calls.append(("stop", flush)),
        backpressure_required=lambda: False,
    )
    w._run_connected_session = lambda *, client_policy: calls.append("session") or {"finished": True}
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})
    assert w.run() == {"finished": True}
    assert calls == ["restore", "start", "session", ("stop", True), ("stop", False)]
    assert not (tmp_path / "mirror-departure-pending.json").exists()


def test_transport_loss_before_departure_confirmation_does_not_reconnect(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import pytest
    import skatai.iss.gate_worker as gw
    from skatai.iss.gate_worker import ExternalGateWorker, ISSGateWorkerError
    from skatai.iss.transport import ISSTransportError

    calls = []
    w = object.__new__(ExternalGateWorker)
    w.paths = SimpleNamespace(runtime_root=tmp_path)
    w.evidence = SimpleNamespace(mirror=SimpleNamespace(probe=lambda: {"ok": True}))
    w.assignment_by_game = {}
    w._mirror_pause_requested = True
    w._transport_failure_streak = 0
    w._restore_active_game_authority = lambda: None
    w.mirror_writebehind = SimpleNamespace(
        start=lambda: None,
        stop=lambda *, flush: calls.append(("stop", flush)),
        backpressure_required=lambda: False,
    )
    def interrupted_session(*, client_policy):
        calls.append("session")
        raise ISSTransportError("REMOTE_EOF")
    w._run_connected_session = interrupted_session
    monkeypatch.setattr(gw, "readiness", lambda paths: {"ready": True, "blockers": []})

    with pytest.raises(ISSGateWorkerError, match="MIRROR_DEPARTURE_OUTCOME_UNKNOWN"):
        w.run()
    assert calls == ["session", ("stop", False)]

def _terminal_recovery_fixture(tmp_path):
    import json
    from dataclasses import asdict

    from skatai.evaluation.iss_ledger import ISSGateLedgerRecord, sha256_file
    from skatai.iss.gate_worker import ActiveGame, GateEvidence, GatePaths, GameAssignment

    paths = GatePaths(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        skatzero_root=tmp_path / "b0",
        skatzero_python=tmp_path / "python",
        b1_model=tmp_path / "b1.pt",
    )
    identities = {
        "B0": {"deployment_identity_sha256": "a" * 64, "release_id": "B0-test"},
        "B1": {"deployment_identity_sha256": "b" * 64, "release_id": "B1-test"},
    }
    source_commit = "abc1234"
    ev = GateEvidence(paths=paths, identities=identities, source_commit=source_commit)
    assignment = GameAssignment("B0", "kermit+zoot", 2, 300, True)
    active = ActiveGame(assignment, protocol_offset=11, effect_offset=22)
    table_id, game_sequence = "T-terminal", 9
    game_id = "f" * 64

    sgf = ev.games_dir / f"{game_id}.sgf"
    service = ev.games_dir / f"{game_id}.service.jsonl"
    effects = ev.games_dir / f"{game_id}.effects.jsonl"
    evidence_path = ev.games_dir / f"{game_id}.evidence.json"
    sgf.write_text("(;GM[Skat])", encoding="utf-8")
    service.write_text('{"direction":"in"}\n', encoding="utf-8")
    effects.write_text('{"event":"CONFIRMED"}\n', encoding="utf-8")
    evidence = {
        "schema": "skatai.v2.external-iss-game-evidence.v1",
        "game_id": game_id,
        "table_id": table_id,
        "server_game_num": game_sequence,
        "source_commit": source_commit,
        "assignment": asdict(assignment),
        "actual_stack": assignment.stack,
        "deployment_identity_sha256": "a" * 64,
        "release_id": "B0-test",
        "status": "SCORED",
        "failure_reason": None,
        "result": {
            "game_id": game_id,
            "seat": 2,
            "score": 0.0,
            "raw_sgf_sha256": sha256_file(sgf),
        },
        "artifacts": {
            "terminal_sgf": {"sha256": sha256_file(sgf), "bytes": sgf.stat().st_size},
            "service_slice": {"sha256": sha256_file(service), "bytes": service.stat().st_size},
            "effect_slice": {"sha256": sha256_file(effects), "bytes": effects.stat().st_size},
        },
        "effects": [{"effect_id": "e1", "status": "CONFIRMED"}],
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rec = ISSGateLedgerRecord.create(
        game_id=game_id,
        arm="B0",
        opponent="kermit+zoot",
        seat=2,
        status="SCORED",
        score=0.0,
        model_sha256="a" * 64,
        source_commit=source_commit,
        raw_sgf_sha256=sha256_file(sgf),
        journal_sha256=sha256_file(evidence_path),
    )
    ev.ledger.append(rec)
    return ev, {(table_id, game_sequence): active}, game_id, evidence_path


def test_terminal_active_recovery_mirrors_before_clearing_authority(tmp_path):
    ev, active, game_id, _ = _terminal_recovery_fixture(tmp_path)
    calls = []
    ev.mirror_game = (
        lambda gid, **kwargs:
        calls.append(("mirror", gid, kwargs)) or {"game_id": gid}
    )
    ev.persist_active_games = (
        lambda games, *, source_commit:
        calls.append(("persist", dict(games), source_commit)) or {"ok": True}
    )
    ev.mirror_current_state = lambda: calls.append(("current",)) or []

    remaining, recovered = ev.reconcile_terminal_active_games(
        active, source_commit="abc1234"
    )

    assert remaining == {}
    assert recovered == [game_id]
    assert calls[0] == (
        "mirror",
        game_id,
        {"include_current": False},
    )
    assert calls[1][0] == "persist"
    assert calls[1][1] == {}
    assert calls[2] == ("current",)


def test_terminal_active_recovery_keeps_authority_if_mirror_fails(tmp_path):
    import pytest

    ev, active, _, _ = _terminal_recovery_fixture(tmp_path)
    persisted = []
    def fail_mirror(game_id, **kwargs):
        raise RuntimeError("mirror interrupted")
    ev.mirror_game = fail_mirror
    ev.persist_active_games = lambda *args, **kwargs: persisted.append(True)

    with pytest.raises(RuntimeError, match="mirror interrupted"):
        ev.reconcile_terminal_active_games(active, source_commit="abc1234")

    assert persisted == []
    assert active


def test_terminal_active_recovery_fails_closed_on_nonterminal_evidence(tmp_path):
    import json
    import pytest

    ev, active, _, evidence_path = _terminal_recovery_fixture(tmp_path)
    payload = json.loads(evidence_path.read_text())
    payload["effects"][0]["status"] = "SEND_RETURNED"
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    # Update the ledger's journal hash would require rewriting immutable ledger
    # evidence, so the recovery must reject this altered terminal package.
    ev.mirror_game = lambda game_id: (_ for _ in ()).throw(
        AssertionError("must not mirror invalid evidence")
    )

    with pytest.raises(Exception):
        ev.reconcile_terminal_active_games(active, source_commit="abc1234")

def test_terminal_active_recovery_preserves_midgame_authority_without_terminal_evidence(tmp_path):
    from skatai.iss.gate_worker import ActiveGame, GateEvidence, GatePaths, GameAssignment

    paths = GatePaths(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        skatzero_root=tmp_path / "b0",
        skatzero_python=tmp_path / "python",
        b1_model=tmp_path / "b1.pt",
    )
    identities = {
        "B0": {"deployment_identity_sha256": "a" * 64, "release_id": "B0-test"},
        "B1": {"deployment_identity_sha256": "b" * 64, "release_id": "B1-test"},
    }
    ev = GateEvidence(paths=paths, identities=identities, source_commit="abc1234")
    active = {
        ("T-midgame", 3): ActiveGame(
            GameAssignment("B1", "kermit+theCount", 1, 300, True),
            protocol_offset=10,
            effect_offset=20,
        )
    }
    ev.mirror_game = lambda game_id: (_ for _ in ()).throw(
        AssertionError("mid-game authority must not be mirrored")
    )
    ev.persist_active_games = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("mid-game authority must not be rewritten")
    )

    remaining, recovered = ev.reconcile_terminal_active_games(
        active, source_commit="abc1234"
    )

    assert remaining == active
    assert recovered == []


def _writebehind_evidence(tmp_path):
    from skatai.iss.gate_worker import GateEvidence, GatePaths

    paths = GatePaths(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        skatzero_root=tmp_path / "b0",
        skatzero_python=tmp_path / "python",
        b1_model=tmp_path / "b1.pt",
    )
    identities = {
        "B0": {
            "deployment_identity_sha256": "a" * 64,
            "release_id": "B0-test",
        },
        "B1": {
            "deployment_identity_sha256": "b" * 64,
            "release_id": "B1-test",
        },
    }
    return GateEvidence(
        paths=paths,
        identities=identities,
        source_commit="abc1234",
    )


def _writebehind_closed_game(ev, game_id):
    import json

    for suffix in ("service.jsonl", "effects.jsonl"):
        (ev.games_dir / f"{game_id}.{suffix}").write_text(
            f"{game_id}:{suffix}\n"
        )
    (ev.games_dir / f"{game_id}.evidence.json").write_text(
        json.dumps({"game_id": game_id, "source_commit": ev.source_commit}) + "\n"
    )


def test_active_authority_persist_is_local_and_marks_remote_dirty(tmp_path):
    from skatai.iss.gate_worker import ActiveGame, GameAssignment

    ev = _writebehind_evidence(tmp_path)
    ev.mirror.upload_verified = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("hot path must not perform remote upload")
    )
    active = {
        ("T", 1): ActiveGame(
            GameAssignment("B0", "kermit+zoot", 0, 300, True),
            protocol_offset=11,
            effect_offset=22,
        )
    }

    payload = ev.persist_active_games(active, source_commit="abc1234")

    assert payload["games"][0]["table_id"] == "T"
    assert ev.active_games_path.is_file()
    assert ev.mirror_current_dirty_path.is_file()


def test_mirror_batch_combines_games_and_current_state_in_one_transfer(tmp_path):
    import json

    ev = _writebehind_evidence(tmp_path)
    ev.active_games_path.write_text(
        json.dumps(
            {
                "schema": "skatai.v2.external-iss-active-games.v1",
                "source_commit": "abc1234",
                "games": [],
            }
        )
        + "\n"
    )
    ev.mark_current_mirror_dirty()

    for game_id in ("g1", "g2"):
        _writebehind_closed_game(ev, game_id)
        ev.enqueue_mirror_game(game_id)

    calls = []

    def batch(files):
        items = list(files)
        calls.append(items)
        return [
            ev._mirror_file_metadata(local, remote)
            for local, remote in items
        ]

    ev.mirror.upload_batch_verified = batch
    result = ev.mirror_batch(limit=8)

    assert len(calls) == 1
    remotes = [remote for _, remote in calls[0]]
    assert "manifests/g1.json" in remotes
    assert "manifests/g2.json" in remotes
    assert remotes.count("current/active-games.json") == 1
    assert result["mirrored_games"] == ["g1", "g2"]
    assert result["pending_games"] == 0
    assert not ev.mirror_current_dirty_path.exists()
    assert not list(ev.mirror_queue_dir.glob("*.json"))
    receipts = [json.loads((ev.mirror_receipts_dir / f"{game_id}.json").read_text())
                for game_id in ("g1", "g2")]
    assert all(x["lag_s"] >= 0 and x["verified_unix_ns"] >= x["enqueued_unix_ns"]
               for x in receipts)
    ev.enqueue_mirror_game("g1")
    ev.mirror_batch(limit=1)
    assert json.loads((ev.mirror_receipts_dir / "g1.json").read_text()) == receipts[0]


def test_mirror_batch_failure_preserves_durable_queue(tmp_path):
    import pytest

    ev = _writebehind_evidence(tmp_path)
    game_id = "g-fail"
    _writebehind_closed_game(ev, game_id)
    marker = ev.enqueue_mirror_game(game_id)
    ev.mark_current_mirror_dirty()

    def fail_batch(files):
        list(files)
        raise RuntimeError("hetzner unavailable")

    ev.mirror.upload_batch_verified = fail_batch

    with pytest.raises(RuntimeError, match="hetzner unavailable"):
        ev.mirror_batch(limit=8)

    assert marker.is_file()
    assert not (ev.mirror_receipts_dir / f"{game_id}.json").exists()
    assert ev.mirror_current_dirty_path.is_file()
    assert ev.mirror_backlog_status()["pending_games"] == 1


def test_upload_batch_verified_protects_games_and_scopes_current_snapshot(
    tmp_path, monkeypatch
):
    import subprocess

    import skatai.iss.gate_worker as gw

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\n")
    b.write_text("beta\n")
    calls = []

    def fake_run(args, **kwargs):
        selected = __import__("pathlib").Path(
            args[args.index("--files-from") + 1]
        ).read_text().splitlines()
        calls.append((list(args), selected))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)
    mirror = gw.HetznerEvidenceMirror(local_root=tmp_path)
    result = mirror.upload_batch_verified(
        [(a, "games/a.txt"), (b, "current/b.txt")]
    )

    assert len(result) == 2
    assert [args[:2] for args, _ in calls] == [
        ["rclone", "copy"], ["rclone", "check"],
        ["rclone", "copy"], ["rclone", "check"],
    ]
    assert [selected for _, selected in calls] == [
        ["games/a.txt"], ["games/a.txt"],
        ["current/b.txt"], ["current/b.txt"],
    ]
    assert "--immutable" in calls[0][0]
    assert "--immutable" not in calls[2][0]
    assert all("--no-traverse" in calls[i][0] for i in (0, 2))
    assert all("--download" in calls[i][0] for i in (1, 3))
    assert all("--one-way" in calls[i][0] for i in (1, 3))


def test_immutable_remote_conflict_stops_before_current_write(tmp_path, monkeypatch):
    import subprocess
    import pytest
    import skatai.iss.gate_worker as gw

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\n")
    b.write_text("beta\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 9, stdout="", stderr="conflict")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)
    mirror = gw.HetznerEvidenceMirror(local_root=tmp_path)
    with pytest.raises(gw.ISSGateWorkerError, match="MIRROR_BATCH_IMMUTABLE_COPY_FAILED:9"):
        mirror.upload_batch_verified(
            [(a, "games/a.txt"), (b, "current/b.txt")]
        )
    assert len(calls) == 1
    assert "--immutable" in calls[0]


def test_mirror_manifest_published_only_after_game_readback(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    import pytest
    import skatai.iss.gate_worker as gw

    game = tmp_path / "game.txt"
    manifest = tmp_path / "manifest.json"
    current = tmp_path / "current.json"
    for path in (game, manifest, current):
        path.write_text(path.name)
    files = [(game, "games/g.txt"), (manifest, "manifests/g.json"),
             (current, "current/state.json")]
    calls = []
    fail_game_check = True

    def fake_run(args, **kwargs):
        nonlocal fail_game_check
        selected = Path(args[args.index("--files-from") + 1]).read_text().splitlines()
        calls.append((args[1], selected))
        code = 9 if fail_game_check and args[1] == "check" else 0
        return subprocess.CompletedProcess(args, code, stdout="", stderr="failed" if code else "")

    monkeypatch.setattr(gw.subprocess, "run", fake_run)
    mirror = gw.HetznerEvidenceMirror(local_root=tmp_path)
    with pytest.raises(gw.ISSGateWorkerError, match="MIRROR_BATCH_IMMUTABLE_VERIFY_FAILED:9"):
        mirror.upload_batch_verified(files)
    assert calls == [("copy", ["games/g.txt"]), ("check", ["games/g.txt"])]

    calls.clear()
    fail_game_check = False
    mirror.upload_batch_verified(files)
    assert calls == [
        ("copy", ["games/g.txt"]), ("check", ["games/g.txt"]),
        ("copy", ["manifests/g.json"]), ("check", ["manifests/g.json"]),
        ("copy", ["current/state.json"]), ("check", ["current/state.json"]),
    ]


def test_mirror_policy_environment_is_bounded():
    from skatai.iss.gate_worker import mirror_policy_from_environment

    policy = mirror_policy_from_environment(
        {
            "ISS_GATE_MIRROR_BATCH_GAMES": "12",
            "ISS_GATE_MIRROR_MAX_DELAY_S": "240",
            "ISS_GATE_MIRROR_RETRY_DELAY_S": "15",
            "ISS_GATE_MIRROR_POLL_INTERVAL_S": "1",
            "ISS_GATE_MIRROR_MAX_PENDING_GAMES": "12",
            "ISS_GATE_MIRROR_MAX_BACKLOG_AGE_S": "240",
        }
    )

    assert policy.batch_games == 12
    assert policy.max_delay_s == 240.0
    assert policy.retry_delay_s == 15.0
    assert policy.poll_interval_s == 1.0


def test_mirror_queue_duplicate_enqueue_is_idempotent_and_hash_bound(tmp_path):
    ev = _writebehind_evidence(tmp_path)
    game_id = "g-duplicate"
    _writebehind_closed_game(ev, game_id)

    first = ev.enqueue_mirror_game(game_id)
    first_bytes = first.read_bytes()
    second = ev.enqueue_mirror_game(game_id)

    assert second == first
    assert second.read_bytes() == first_bytes
    payload = __import__("json").loads(first_bytes)
    assert payload["schema"] == "skatai.v2.iss-mirror-queue.v2"
    assert len(payload["artifacts"]) == 3
    assert all(len(item["sha256"]) == 64 for item in payload["artifacts"])


def test_mirror_queue_rejects_false_game_source_before_enqueue_or_retry(tmp_path):
    import json
    import pytest
    from skatai.iss.gate_worker import ISSGateWorkerError

    ev = _writebehind_evidence(tmp_path)
    game_id = "g-false-source"
    _writebehind_closed_game(ev, game_id)
    evidence_path = ev.games_dir / f"{game_id}.evidence.json"
    evidence_path.write_text(
        json.dumps({"game_id": game_id, "source_commit": "different"}) + "\n"
    )
    with pytest.raises(ISSGateWorkerError, match="MIRROR_QUEUE_SOURCE_UNVERIFIED"):
        ev.enqueue_mirror_game(game_id)
    assert not (ev.mirror_queue_dir / f"{game_id}.json").exists()

    _writebehind_closed_game(ev, game_id)
    marker = ev.enqueue_mirror_game(game_id)
    payload = json.loads(marker.read_text())
    payload["source_commit"] = "different"
    marker.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ISSGateWorkerError, match="MIRROR_QUEUE_SOURCE_UNVERIFIED"):
        ev.pending_mirror_entries()
    assert marker.exists()


def test_mirror_queue_rejects_local_artifact_mutation_before_upload(tmp_path):
    import pytest
    from skatai.iss.gate_worker import ISSGateWorkerError

    ev = _writebehind_evidence(tmp_path)
    game_id = "g-mutated"
    _writebehind_closed_game(ev, game_id)
    marker = ev.enqueue_mirror_game(game_id)
    (ev.games_dir / f"{game_id}.effects.jsonl").write_text("tampered\n")

    ev.mirror.upload_batch_verified = lambda files: (_ for _ in ()).throw(
        AssertionError("remote upload must not start for mutated outbox")
    )
    with pytest.raises(
        ISSGateWorkerError,
        match="MIRROR_QUEUE_ARTIFACT_BINDING_MISMATCH",
    ):
        ev.mirror_batch(limit=2)

    assert marker.is_file()
    assert ev.mirror_backlog_status()["pending_games"] == 1


def test_pending_game_survives_source_update_with_original_lineage(tmp_path):
    import json
    from skatai.iss.gate_worker import GateEvidence, MirrorPolicy, MirrorWriteBehind

    old = _writebehind_evidence(tmp_path)
    game_id = "g-before-update"
    for suffix in ("service.jsonl", "effects.jsonl"):
        (old.games_dir / f"{game_id}.{suffix}").write_text(
            f"{game_id}:{suffix}\n"
        )
    (old.games_dir / f"{game_id}.evidence.json").write_text(
        json.dumps({"game_id": game_id, "source_commit": "abc1234"}) + "\n"
    )
    marker = old.enqueue_mirror_game(game_id)
    old.mark_current_mirror_dirty()
    old_dirty = json.loads(old.mirror_current_dirty_path.read_text())

    updated = GateEvidence(
        paths=old.paths,
        identities=old.identities,
        source_commit="newcommit",
    )
    assert updated.enqueue_mirror_game(game_id) == marker
    updated.mark_current_mirror_dirty()
    dirty = json.loads(updated.mirror_current_dirty_path.read_text())
    assert dirty["source_commit"] == "newcommit"
    assert dirty["first_dirty_unix_ns"] == old_dirty["first_dirty_unix_ns"]
    policy = MirrorPolicy(
        batch_games=1,
        max_delay_s=1,
        retry_delay_s=1,
        poll_interval_s=1,
        max_pending_games=2,
        max_pending_bytes=1024,
        max_backlog_age_s=60,
    )
    assert MirrorWriteBehind(updated, policy=policy)._due()

    copied = []
    updated.mirror.upload_batch_verified = lambda files: copied.extend(files) or []
    result = updated.mirror_batch(limit=1)
    manifest = json.loads(
        (updated.games_dir / f"{game_id}.mirror.json").read_text()
    )
    assert manifest["source_commit"] == "abc1234"
    assert result["pending_games"] == 0
    assert not marker.exists()
    assert not updated.mirror_current_dirty_path.exists()
    assert any(remote == f"manifests/{game_id}.json" for _, remote in copied)


def test_pending_game_source_update_rejects_unverified_lineage(tmp_path):
    import json
    import pytest
    from skatai.iss.gate_worker import GateEvidence, ISSGateWorkerError

    old = _writebehind_evidence(tmp_path)
    game_id = "g-unverified-update"
    for suffix in ("service.jsonl", "effects.jsonl"):
        (old.games_dir / f"{game_id}.{suffix}").write_text("evidence\n")
    (old.games_dir / f"{game_id}.evidence.json").write_text(
        json.dumps({"game_id": game_id, "source_commit": "abc1234"}) + "\n"
    )
    marker = old.enqueue_mirror_game(game_id)
    updated = GateEvidence(
        paths=old.paths,
        identities=old.identities,
        source_commit="newcommit",
    )
    (old.games_dir / f"{game_id}.evidence.json").write_text(
        json.dumps({"game_id": game_id, "source_commit": "different"}) + "\n"
    )
    with pytest.raises(ISSGateWorkerError, match="MIRROR_QUEUE_SOURCE_UNVERIFIED"):
        updated.pending_mirror_entries()
    assert marker.exists()


def test_mirror_batch_preserves_newer_current_dirty_generation(tmp_path):
    import json

    ev = _writebehind_evidence(tmp_path)
    game_id = "g-current-race"
    _writebehind_closed_game(ev, game_id)
    ev.enqueue_mirror_game(game_id)
    ev.active_games_path.write_text(
        json.dumps(
            {
                "schema": "skatai.v2.external-iss-active-games.v1",
                "source_commit": "abc1234",
                "games": [],
            }
        )
        + "\n"
    )
    ev.mark_current_mirror_dirty()
    before = ev.mirror_current_dirty_path.read_bytes()

    def batch(files):
        items = list(files)
        ev.mark_current_mirror_dirty()
        return [
            ev._mirror_file_metadata(local, remote)
            for local, remote in items
        ]

    ev.mirror.upload_batch_verified = batch
    ev.mirror_batch(limit=2)

    assert ev.mirror_current_dirty_path.is_file()
    assert ev.mirror_current_dirty_path.read_bytes() != before
    assert ev.mirror_backlog_status()["pending_games"] == 0
    assert ev.mirror_backlog_status()["current_dirty"] is True


def test_mirror_backpressure_reports_bounded_backlog(tmp_path):
    from skatai.iss.gate_worker import MirrorPolicy, MirrorWriteBehind

    ev = _writebehind_evidence(tmp_path)
    for game_id in ("g1", "g2", "g3", "g4"):
        _writebehind_closed_game(ev, game_id)
        ev.enqueue_mirror_game(game_id)

    wb = MirrorWriteBehind(
        ev,
        policy=MirrorPolicy(
            batch_games=2,
            max_delay_s=60,
            retry_delay_s=10,
            poll_interval_s=1,
            max_pending_games=4,
            max_pending_bytes=64 * 1024 * 1024,
            max_backlog_age_s=120,
        ),
    )

    assert wb.backpressure_required() is True
    for marker, _ in ev.pending_mirror_entries()[:2]:
        marker.unlink()
    assert wb.backpressure_required() is False


def test_writebehind_storage_outage_retains_games_then_drains_without_duplicates(tmp_path):
    import threading
    import time
    from skatai.iss.gate_worker import MirrorPolicy, MirrorWriteBehind

    ev = _writebehind_evidence(tmp_path)
    for game_id in ("g1", "g2"):
        _writebehind_closed_game(ev, game_id)
        ev.enqueue_mirror_game(game_id)

    storage_up = threading.Event()
    uploaded = []

    def upload(files):
        if not storage_up.is_set():
            raise RuntimeError("storage unavailable")
        items = list(files)
        uploaded.extend(remote for _, remote in items)
        return [ev._mirror_file_metadata(local, remote) for local, remote in items]

    ev.mirror.upload_batch_verified = upload
    wb = MirrorWriteBehind(
        ev,
        policy=MirrorPolicy(
            batch_games=2,
            max_delay_s=1,
            retry_delay_s=0.01,
            poll_interval_s=0.01,
            max_pending_games=2,
            max_pending_bytes=64 * 1024 * 1024,
            max_backlog_age_s=2,
        ),
    )
    wb.start()
    try:
        deadline = time.monotonic() + 2
        while not ev.mirror_status_path.exists() or 'DEGRADED' not in ev.mirror_status_path.read_text():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert wb.backpressure_required()
        assert ev.mirror_backlog_status()["pending_games"] == 2
        assert not uploaded

        storage_up.set()
        while ev.mirror_backlog_status()["pending_games"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not wb.backpressure_required()
        assert uploaded.count("manifests/g1.json") == 1
        assert uploaded.count("manifests/g2.json") == 1
    finally:
        wb.stop(flush=False)


def test_active_game_payload_supports_multiple_concurrent_tables():
    from skatai.iss.gate_worker import (
        ActiveGame,
        GameAssignment,
        active_games_payload,
        parse_active_games_payload,
    )

    games = {
        ("T1", 11): ActiveGame(
            GameAssignment("B0", "kermit+zoot", 0, 300, True), 10, 20
        ),
        ("T2", 22): ActiveGame(
            GameAssignment("B1", "kermit+theCount", 2, 300, True), 30, 40
        ),
    }
    payload = active_games_payload(games, source_commit="abc1234")
    restored = parse_active_games_payload(
        payload, expected_source_commit="abc1234"
    )
    assert restored == games


def test_recording_switch_routes_concurrent_games_to_bound_arms():
    from types import SimpleNamespace
    from skatai.iss.gate_worker import RecordingSwitchProvider

    class Provider:
        def __init__(self, arm):
            self.arm = arm
        def next_decision(self, table):
            return SimpleNamespace(
                request=SimpleNamespace(
                    game_id=f"iss:{table.table_id}:{table.game_sequence}"
                ),
                result=SimpleNamespace(
                    decision_id=f"d-{self.arm}",
                    latency_ms=1.0,
                ),
                wire_action=self.arm,
            )

    switch = RecordingSwitchProvider(
        {"B0": Provider("B0"), "B1": Provider("B1")}
    )
    switch.bind_game("T1", 1, "B0")
    switch.bind_game("T2", 2, "B1")
    t1 = SimpleNamespace(table_id="T1", game_sequence=1)
    t2 = SimpleNamespace(table_id="T2", game_sequence=2)
    assert switch.next_decision(t1).wire_action == "B0"
    assert switch.next_decision(t2).wire_action == "B1"


def test_throughput_policy_defaults_preserve_single_table_cold_sync():
    from skatai.iss.gate_worker import throughput_policy_from_environment

    p = throughput_policy_from_environment({})
    assert p.tables == 1
    assert p.inference_workers == 1
    assert p.warm_skatzero is False
    assert p.async_decisions is False


def test_multitable_policy_requires_async_and_bounds_workers():
    import pytest
    from skatai.iss.gate_worker import throughput_policy_from_environment

    with pytest.raises(ValueError, match="MULTITABLE_REQUIRES_ASYNC"):
        throughput_policy_from_environment(
            {"ISS_GATE_TABLES": "2", "ISS_GATE_INFERENCE_WORKERS": "1"}
        )

    p = throughput_policy_from_environment(
        {
            "ISS_GATE_TABLES": "4",
            "ISS_GATE_INFERENCE_WORKERS": "2",
            "ISS_GATE_WARM_SKATZERO": "true",
            "ISS_GATE_ASYNC_DECISIONS": "1",
        }
    )
    assert (p.tables, p.inference_workers) == (4, 2)
    assert p.warm_skatzero is True
    assert p.async_decisions is True
