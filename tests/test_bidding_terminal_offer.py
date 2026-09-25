from skatai.game.bidding import BID_VALUES, BiddingState, replay


def _reach_final_offer(second_duel: bool) -> tuple[BiddingState, list[str]]:
    state = BiddingState()
    tokens: list[str] = []
    if second_duel:
        tokens.extend(("1", "18", "0", "p"))
        state.apply(1, "18")
        state.apply(0, "p")
    while state.offer < BID_VALUES[-1]:
        bidder = state.expected_actor
        offer = str(state.offer)
        state.apply(bidder, offer)
        tokens.extend((str(bidder), offer))
        answerer = state.expected_actor
        state.apply(answerer, "y")
        tokens.extend((str(answerer), "y"))
    bidder = state.expected_actor
    state.apply(bidder, str(BID_VALUES[-1]))
    tokens.extend((str(bidder), str(BID_VALUES[-1])))
    return state, tokens


def test_answerer_holding_final_offer_wins_without_overflow() -> None:
    for second_duel, winner in ((False, 0), (True, 1)):
        state, tokens = _reach_final_offer(second_duel)
        answerer = state.expected_actor
        state.apply(answerer, "y")
        tokens.extend((str(answerer), "y"))
        assert (state.finished, state.winner, state.winning_bid) == (True, winner, 264)
        result = replay(tokens)
        assert (result.ok, result.winner, result.winning_bid) == (True, winner, 264)


def test_first_duel_bidder_wins_when_hearer_passes_final_offer() -> None:
    state, tokens = _reach_final_offer(second_duel=False)
    answerer = state.expected_actor
    state.apply(answerer, "p")
    tokens.extend((str(answerer), "p"))
    assert (state.finished, state.winner, state.winning_bid) == (True, 1, 264)
    result = replay(tokens)
    assert (result.ok, result.winner, result.winning_bid) == (True, 1, 264)
