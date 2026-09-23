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
