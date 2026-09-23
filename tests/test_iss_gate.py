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
