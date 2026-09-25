from copy import deepcopy

from skatai.data.cardplay import reconstruct_cardplay_events
from skatai.data.sgf import parse_sgf_line, semantic_identity
from tests.test_sgf import LIVE_SPLIT_PICKUP, PLAYED


def test_hand_game_reconstructs_all_legal_decisions():
    game = parse_sgf_line("fixture", PLAYED)
    events = reconstruct_cardplay_events(game)

    assert len(events) == 30
    assert [e.play_ordinal for e in events] == list(range(30))
    for event in events:
        assert event.target_card in event.observation.hand
        assert event.target_card in event.observation.legal_cards
        assert len(event.observation.played_cards) == event.play_ordinal
        assert event.observation.skat_cards == ()
        assert event.observation.blind_hand is True
        assert event.observation.known_private_cards == ()

    assert events[0].observation.max_accepted_bids_by_seat == (0, 18, 20)
    assert len(events[-1].observation.hand) == 1


def test_pickup_game_matches_runtime_visibility_and_point_semantics():
    game = parse_sgf_line("fixture", LIVE_SPLIT_PICKUP)
    events = reconstruct_cardplay_events(game)

    assert len(events) == 30
    first = events[0]
    assert first.observation.seat == game["declarer"] == 0
    assert first.observation.skat_cards == ("SK", "S7")
    assert first.observation.blind_hand is False
    # Runtime parity: a pickup declarer sees buried-card points in its
    # declarer-point total. SK=4 and S7=0.
    assert first.observation.points_self == 4
    assert first.observation.points_other == 0
    assert first.observation.max_accepted_bids_by_seat == (48, 0, 48)

    first_defender = next(
        event for event in events if event.observation.seat != game["declarer"]
    )
    assert first_defender.observation.skat_cards == ()
    assert first_defender.observation.known_private_cards == ()


def test_defender_observation_is_invariant_to_hidden_opponent_reallocation():
    game_a = parse_sgf_line("fixture", LIVE_SPLIT_PICKUP)
    game_a["plays"] = game_a["plays"][:2]
    game_a["play_count"] = 2
    game_a["play_complete"] = False

    game_b = deepcopy(game_a)
    # Swap two cards that are hidden from seat 1 at its first decision.
    # The public prefix, seat-1 hand, skat visibility, and current legal state
    # remain unchanged.
    card_a = "H8"
    card_b = "DQ"
    assert card_a in game_b["initial_hands"][0]
    assert card_b in game_b["initial_hands"][2]
    game_b["initial_hands"][0].remove(card_a)
    game_b["initial_hands"][0].append(card_b)
    game_b["initial_hands"][2].remove(card_b)
    game_b["initial_hands"][2].append(card_a)

    events_a = reconstruct_cardplay_events(game_a)
    events_b = reconstruct_cardplay_events(game_b)

    assert events_a[1].observation.seat == 1
    assert events_b[1].observation.seat == 1
    assert events_a[1].observation == events_b[1].observation
    assert events_a[1].target_card == events_b[1].target_card


def test_target_is_label_not_part_of_observation_mapping():
    game = parse_sgf_line("fixture", LIVE_SPLIT_PICKUP)
    event = reconstruct_cardplay_events(game)[0]
    payload = event.to_mapping()

    assert payload["target_card"] == event.target_card
    assert "target_card" not in payload["observation"]



def test_cardplay_event_keeps_player_metadata_outside_observation():
    game = parse_sgf_line("fixture", LIVE_SPLIT_PICKUP)
    game["players"] = ["p0", "p1", "p2"]
    game["ratings"] = [701.0, 812.5, 923.0]
    game["actor_classes"] = ["human", "kermit", "strong_human"]

    events = reconstruct_cardplay_events(game)
    event = events[0]
    actor = event.observation.seat

    assert event.actor_name == game["players"][actor]
    assert event.actor_rating == game["ratings"][actor]
    assert event.actor_class == game["actor_classes"][actor]

    payload = event.to_mapping()
    assert payload["actor_name"] == event.actor_name
    assert payload["actor_rating"] == event.actor_rating
    assert payload["actor_class"] == event.actor_class
    assert "actor_name" not in payload["observation"]
    assert "actor_rating" not in payload["observation"]
    assert "actor_class" not in payload["observation"]


def test_reconstructed_game_id_uses_v2_identity_and_preserves_source_identity():
    game = parse_sgf_line("fixture", LIVE_SPLIT_PICKUP)
    game["semantic_sha256"] = "0" * 64  # Simulates a migrated Legacy identity.

    event = reconstruct_cardplay_events(game)[0]
    assert event.game_id == semantic_identity(game)
    assert event.game_id != game["semantic_sha256"]
    assert event.source_semantic_sha256 == game["semantic_sha256"]
    assert event.raw_sha256 == game["raw_sha256"]
    assert event.to_mapping()["source_semantic_sha256"] == game["semantic_sha256"]
    assert "source_semantic_sha256" not in event.to_mapping()["observation"]
