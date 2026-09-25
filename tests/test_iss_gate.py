from skatai.evaluation.iss_gate import ISSGameOutcome, decide_external_gate


def games(delta: float, n_per_arm: int):
    out = []
    opponents = ["kermit", "zoot", "theCount"]
    for arm in ("B0", "B1"):
        for i in range(n_per_arm):
            seat = i % 3
            opponent = opponents[(i // 3) % len(opponents)]
            base = float((i % 11) - 5)
            score = base + (delta if arm == "B1" else 0.0)
            out.append(
                ISSGameOutcome(
                    arm=arm,
                    opponent=opponent,
                    seat=seat,
                    score=score,
                    game_id=f"{arm}-{i}",
                )
            )
    return out


def test_external_gate_is_incomplete_before_first_look():
    r = decide_external_gate(games(10.0, 299), replicates=1000)
    assert r["status"] == "INCOMPLETE"
    assert r["decision"] is None
    assert r["accept_authorized"] is False
    assert r["next_look_per_arm"] == 300


def test_external_gate_accepts_clear_positive_at_frozen_look():
    r = decide_external_gate(games(20.0, 300), replicates=2000)
    assert r["status"] == "COMPLETE"
    assert r["decision"] == "ACCEPT"
    assert r["interval"]["low"] > 0
    assert r["accept_authorized"] is True


def test_external_gate_rejects_clear_negative_at_frozen_look():
    r = decide_external_gate(games(-20.0, 300), replicates=2000)
    assert r["status"] == "COMPLETE"
    assert r["decision"] == "REJECT"
    assert r["interval"]["high"] < 0
    assert r["accept_authorized"] is False


def test_external_gate_continues_when_first_look_interval_crosses_zero():
    r = decide_external_gate(games(0.0, 300), replicates=2000)
    assert r["status"] == "CONTINUE"
    assert r["decision"] is None
    assert r["next_look_per_arm"] == 1000


def test_external_gate_final_look_can_be_inconclusive():
    r = decide_external_gate(games(0.0, 3000), replicates=2000)
    assert r["status"] == "COMPLETE"
    assert r["decision"] == "INCONCLUSIVE"
    assert r["accept_authorized"] is False


def test_duplicate_game_identity_is_rejected():
    xs = games(1.0, 300)
    xs.append(xs[0])
    try:
        decide_external_gate(xs, replicates=1000)
    except ValueError as exc:
        assert "DUPLICATE_GAME_IDS" in str(exc)
    else:
        raise AssertionError("duplicate game ids must be rejected")


def test_frozen_look_reuses_exact_prefix_without_changing_result(monkeypatch):
    import skatai.evaluation.iss_gate as gate

    gate._frozen_prefix_interval.cache_clear()
    original = gate.stratified_bootstrap_interval
    calls = []

    def counted(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(gate, "stratified_bootstrap_interval", counted)
    first = games(0.0, 300)
    r1 = gate.decide_external_gate(first, replicates=1000)
    # Later scores cannot alter the chronological 300-per-arm selected look.
    extra = [ISSGameOutcome("B0", "kermit", 0, 100.0, "B0-extra"),
             ISSGameOutcome("B1", "kermit", 0, -100.0, "B1-extra")]
    r2 = gate.decide_external_gate(first + extra, replicates=1000)
    assert calls == [600]
    assert r1["interval"] == original(first, replicates=1000, seed=20260923,
                                      confidence=gate.CONFIDENCE)
    assert r1["interval"] == r2["interval"]
    assert r1["estimate"] == r2["estimate"]
    assert r1["counts"] == {"B0": 300, "B1": 300}
    assert r2["counts"] == {"B0": 301, "B1": 301}
    gate._frozen_prefix_interval.cache_clear()


def test_frozen_look_cache_invalidates_for_changed_prefix_or_seed(monkeypatch):
    import skatai.evaluation.iss_gate as gate

    gate._frozen_prefix_interval.cache_clear()
    original = gate.stratified_bootstrap_interval
    calls = []

    def counted(*args, **kwargs):
        calls.append((len(args[0]), kwargs["seed"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(gate, "stratified_bootstrap_interval", counted)
    first = games(0.0, 300)
    gate.decide_external_gate(first, replicates=1000, seed=1)
    changed = list(first)
    changed[0] = ISSGameOutcome("B0", "kermit", 0, 30.0, "B0-0")
    gate.decide_external_gate(changed, replicates=1000, seed=1)
    gate.decide_external_gate(changed, replicates=1000, seed=2)
    assert calls == [(600, 1), (600, 1), (600, 2)]
    gate._frozen_prefix_interval.cache_clear()
