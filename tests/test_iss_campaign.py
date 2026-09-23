from skatai.evaluation.iss_campaign import (
    ARMS,
    DEFAULT_OPPONENTS,
    SEATS,
    next_targets,
    quota_status,
    target_quotas,
)


def test_target_quotas_are_balanced_and_sum_per_arm():
    for n in (300, 1000, 3000):
        q = target_quotas(n)
        for arm in ARMS:
            values = [
                q[s]
                for s in q
                if s.arm == arm
            ]
            assert sum(values) == n
            assert max(values) - min(values) <= 1


def test_quota_status_starts_incomplete():
    s = quota_status([], per_arm=300)
    assert s["complete"] is False
    assert s["arm_counts"] == {"B0": 0, "B1": 0}
    assert sum(x["target"] for x in s["strata"] if x["arm"] == "B0") == 300


def test_next_targets_prioritize_arm_with_fewer_games():
    rows = [
        {"game_id": f"b0-{i}", "arm": "B0", "opponent": "kermit", "seat": 0}
        for i in range(5)
    ]
    nxt = next_targets(rows, per_arm=300)
    assert nxt
    assert nxt[0]["arm"] == "B1"


def test_complete_quota_is_complete_and_has_no_next_targets():
    rows = []
    q = target_quotas(300)
    i = 0
    for s, target in q.items():
        for _ in range(target):
            rows.append({
                "game_id": f"g-{i}",
                "arm": s.arm,
                "opponent": s.opponent,
                "seat": s.seat,
            })
            i += 1
    assert quota_status(rows, per_arm=300)["complete"] is True
    assert next_targets(rows, per_arm=300) == []


def test_all_named_opponent_seat_strata_are_present():
    q = target_quotas(300)
    got = {(s.opponent, s.seat) for s in q}
    assert got == {(o, seat) for o in DEFAULT_OPPONENTS for seat in SEATS}
