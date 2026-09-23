import json

from skatai.evaluation.bidding_gameplay_gate import DEAL_SET_SCHEMA
from skatai.evaluation.promote_pregate_checkpoint import (
    promote_screen_to_confirmation,
)


def _deal(i):
    cards = [s + r for s in "CSHD" for r in "789TJQKA"]
    d = cards[i % 32 :] + cards[: i % 32]
    return {
        "game_identity": f"{i:064x}",
        "hands": [d[:10], d[10:20], d[20:30]],
        "skat": d[30:32],
    }


def test_promote_screen_checkpoint_reuses_prefix(tmp_path):
    screen_set = tmp_path / "screen.json"
    confirm_set = tmp_path / "confirm.json"
    screen_set.write_text(
        json.dumps({"schema": DEAL_SET_SCHEMA, "deals": [_deal(i) for i in range(2)]})
    )
    confirm_set.write_text(
        json.dumps({"schema": DEAL_SET_SCHEMA, "deals": [_deal(i) for i in range(5)]})
    )

    result = tmp_path / "screen-result.json"
    result.write_text(
        json.dumps(
            {
                "configuration": {"deal_count": 2, "paired_seat_observations": 6},
                "summary": {"elapsed_s": 12.5},
                "records": [
                    {
                        "deal_identity": f"{i:064x}",
                        "paired": [{"delta": 0}, {"delta": 1}, {"delta": -1}],
                    }
                    for i in range(2)
                ],
            }
        )
    )
    b1 = tmp_path / "model.pt"
    b1.write_bytes(b"candidate")
    skz = tmp_path / "skz"
    models = tmp_path / "models"
    skz.mkdir()
    models.mkdir()

    out = tmp_path / "checkpoint.json"
    x = promote_screen_to_confirmation(
        screen_result_path=result,
        screen_deal_set_path=screen_set,
        confirmation_deal_set_path=confirm_set,
        skatzero_root=skz,
        model_root=models,
        b1_model=b1,
        output_checkpoint=out,
        expected_screen_deals=2,
        expected_confirmation_deals=5,
    )
    assert x["completed_deals"] == 2
    assert x["complete"] is False
    assert len(x["selected_deal_identities"]) == 5
    assert len(x["records"]) == 2
    assert x["promotion"]["reused_deals"] == 2


def test_promote_rejects_nonprefix(tmp_path):
    screen_set = tmp_path / "screen.json"
    confirm_set = tmp_path / "confirm.json"
    screen_set.write_text(
        json.dumps({"schema": DEAL_SET_SCHEMA, "deals": [_deal(1), _deal(2)]})
    )
    confirm_set.write_text(
        json.dumps({"schema": DEAL_SET_SCHEMA, "deals": [_deal(0), _deal(1), _deal(2)]})
    )
    result = tmp_path / "screen-result.json"
    result.write_text(
        json.dumps(
            {
                "configuration": {"deal_count": 2, "paired_seat_observations": 6},
                "summary": {"elapsed_s": 1.0},
                "records": [
                    {"deal_identity": f"{i:064x}", "paired": [{}, {}, {}]}
                    for i in (1, 2)
                ],
            }
        )
    )
    b1 = tmp_path / "model.pt"
    b1.write_bytes(b"x")
    skz = tmp_path / "skz"
    models = tmp_path / "models"
    skz.mkdir()
    models.mkdir()

    try:
        promote_screen_to_confirmation(
            screen_result_path=result,
            screen_deal_set_path=screen_set,
            confirmation_deal_set_path=confirm_set,
            skatzero_root=skz,
            model_root=models,
            b1_model=b1,
            output_checkpoint=tmp_path / "out.json",
            expected_screen_deals=2,
            expected_confirmation_deals=3,
        )
    except ValueError as exc:
        assert str(exc) == "SCREEN_NOT_CONFIRMATION_PREFIX"
    else:
        raise AssertionError("non-prefix promotion must fail")
