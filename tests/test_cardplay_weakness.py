from types import SimpleNamespace

from skatai.evaluation.cardplay_weakness import (
    contract_family,
    deterministic_balanced_sample,
    event_stratum,
)


def event(game_id, ordinal, contract, seat, declarer):
    return SimpleNamespace(
        game_id=game_id,
        play_ordinal=ordinal,
        observation=SimpleNamespace(
            contract=contract,
            seat=seat,
            declarer=declarer,
        ),
    )


def test_cardplay_diagnostic_strata_match_product_dimensions():
    assert contract_family("CH") == "SUIT"
    assert contract_family("G") == "GRAND"
    assert contract_family("NHO") == "NULL"

    assert event_stratum(event("a", 0, "C", 0, 0)) == (
        "SUIT",
        "DECLARER",
        "EARLY",
    )
    assert event_stratum(event("b", 10, "G", 2, 0)) == (
        "GRAND",
        "DEFENDER",
        "MIDDLE",
    )
    assert event_stratum(event("c", 20, "N", 1, 1)) == (
        "NULL",
        "DECLARER",
        "END",
    )


def test_balanced_sample_is_deterministic_and_caps_each_stratum():
    events = [
        event(f"s{i}", i % 10, "C", i % 3, 0)
        for i in range(8)
    ] + [
        event(f"g{i}", 10 + i % 10, "G", (i + 1) % 3, 0)
        for i in range(7)
    ]
    a = deterministic_balanced_sample(events, per_stratum=3, seed=17)
    b = deterministic_balanced_sample(list(reversed(events)), per_stratum=3, seed=17)

    assert [(x.game_id, x.play_ordinal) for x in a] == [
        (x.game_id, x.play_ordinal) for x in b
    ]
    counts = {}
    for x in a:
        counts[event_stratum(x)] = counts.get(event_stratum(x), 0) + 1
    assert counts
    assert max(counts.values()) <= 3



def test_agreement_summary_reports_overall_and_dimensions():
    from skatai.evaluation.cardplay_weakness import summarize_agreement

    rows = [
        {
            "family": "SUIT",
            "role": "DECLARER",
            "phase": "EARLY",
            "seat": 0,
            "agreement": True,
            "legal_count": 3,
            "latency_ms": 10.0,
        },
        {
            "family": "SUIT",
            "role": "DEFENDER",
            "phase": "EARLY",
            "seat": 1,
            "agreement": False,
            "legal_count": 2,
            "latency_ms": 20.0,
        },
        {
            "family": "GRAND",
            "role": "DEFENDER",
            "phase": "MIDDLE",
            "seat": 2,
            "agreement": True,
            "legal_count": 4,
            "latency_ms": 30.0,
        },
    ]

    summary = summarize_agreement(rows)
    assert summary["overall"]["n"] == 3
    assert summary["overall"]["agreement_count"] == 2
    assert summary["overall"]["agreement_rate"] == 2 / 3
    assert summary["overall"]["latency_ms_p50"] == 20.0
    assert summary["by_family"]["SUIT"]["n"] == 2
    assert summary["by_role"]["DEFENDER"]["agreement_count"] == 1
    assert summary["by_phase"]["EARLY"]["n"] == 2
    assert summary["by_seat"]["2"]["agreement_rate"] == 1.0



def test_decision_context_tracks_lead_follow_and_forced_choice():
    from skatai.evaluation.cardplay_weakness import choice_type, lead_position

    lead_choice = SimpleNamespace(
        observation=SimpleNamespace(current_trick=(), legal_cards=("C7", "C8")),
    )
    follow_forced = SimpleNamespace(
        observation=SimpleNamespace(current_trick=((0, "C7"),), legal_cards=("C8",)),
    )

    assert lead_position(lead_choice) == "LEAD"
    assert choice_type(lead_choice) == "CHOICE"
    assert lead_position(follow_forced) == "FOLLOW"
    assert choice_type(follow_forced) == "FORCED"


def test_agreement_summary_includes_context_and_actor_class():
    from skatai.evaluation.cardplay_weakness import summarize_agreement

    rows = [
        {
            "family": "SUIT",
            "role": "DECLARER",
            "phase": "EARLY",
            "seat": 0,
            "lead_position": "LEAD",
            "choice_type": "CHOICE",
            "actor_class": "strong_human",
            "agreement": True,
            "legal_count": 3,
            "latency_ms": 10.0,
        },
        {
            "family": "SUIT",
            "role": "DEFENDER",
            "phase": "EARLY",
            "seat": 1,
            "lead_position": "FOLLOW",
            "choice_type": "FORCED",
            "actor_class": "kermit",
            "agreement": False,
            "legal_count": 1,
            "latency_ms": 20.0,
        },
    ]

    summary = summarize_agreement(rows)
    assert summary["by_lead_position"]["LEAD"]["agreement_rate"] == 1.0
    assert summary["by_choice_type"]["FORCED"]["n"] == 1
    assert summary["by_actor_class"]["strong_human"]["n"] == 1
