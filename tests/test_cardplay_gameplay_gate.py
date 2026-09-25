import pytest

from skatai.evaluation.cardplay_gameplay_gate import CardplayPosition, evaluate_cardplay_position
from skatai.selfplay.cardplay import make_deal


class FirstLegal:
    def play_card(self, observation):
        return observation.legal_cards[0]


class LastLegal:
    def play_card(self, observation):
        return observation.legal_cards[-1]


@pytest.mark.parametrize("contract", ("CH", "GH", "NH"))
def test_identical_deterministic_cardplay_has_zero_paired_delta(contract):
    result = evaluate_cardplay_position(
        CardplayPosition(17, declarer=1, contract=contract, winning_bid=18),
        control_factory=lambda _seat: FirstLegal(),
        candidate_factory=lambda _seat: FirstLegal(),
    )
    assert len(result["rows"]) == 3
    assert {row["role"] for row in result["rows"]} == {"DECLARER", "DEFENDER"}
    assert all(row["candidate_delta"] == 0 for row in result["rows"])
    assert all(row["control_play_count"] == row["treatment_play_count"] == 30
               for row in result["rows"])
    assert all(row["first_divergence_play_index"] is None and
               row["control_play_sha256"] == row["treatment_play_sha256"]
               for row in result["rows"])


@pytest.mark.parametrize("contract", ("C", "G", "N"))
def test_identical_pickup_policy_scores_supported_families(contract):
    deal = make_deal(4)
    hand12 = (*deal.hands[2], *deal.skat)
    result = evaluate_cardplay_position(
        CardplayPosition(4, 2, contract, 18, tuple(hand12[2:]), (hand12[0], hand12[1])),
        control_factory=lambda _seat: FirstLegal(),
        candidate_factory=lambda _seat: FirstLegal(),
    )
    assert all(row["candidate_delta"] == 0 for row in result["rows"])


def test_paired_cardplay_scores_pickup_declarer_and_defenders():
    deal = make_deal(1)
    hand12 = (*deal.hands[0], *deal.skat)
    position = CardplayPosition(
        1, declarer=0, contract="G", winning_bid=18,
        final_hand=tuple(hand12[2:]), final_skat=(hand12[0], hand12[1]),
    )
    result = evaluate_cardplay_position(
        position,
        control_factory=lambda _seat: FirstLegal(),
        candidate_factory=lambda _seat: LastLegal(),
    )
    assert result["picked_up_skat"] is True
    assert result["deal_sha256"] == deal.identity_sha256
    assert any(row["candidate_delta"] != 0 for row in result["rows"])
    assert any(row["first_divergence_play_index"] is not None and
               row["control_play_sha256"] != row["treatment_play_sha256"]
               for row in result["rows"])
    for row in result["rows"]:
        signed_change = (
            row["treatment_signed_declarer_score"]
            - row["control_signed_declarer_score"]
        )
        assert row["candidate_delta"] == (
            signed_change if row["role"] == "DECLARER" else -signed_change
        )


def test_evaluation_rejects_contract_mode_mismatch_before_policy_calls():
    def should_not_start(_seat):
        pytest.fail("started gameplay with invalid declaration mode")

    with pytest.raises(ValueError, match="CARDPLAY_EVALUATION_CONTRACT_MODE_MISMATCH"):
        evaluate_cardplay_position(
            CardplayPosition(1, declarer=0, contract="G", winning_bid=18),
            control_factory=should_not_start,
            candidate_factory=should_not_start,
        )
