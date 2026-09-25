package org.skatai.v2.jskat;

import org.jskat.ai.AbstractAIPlayer;
import org.jskat.data.GameContract;
import org.jskat.data.Trick;
import org.jskat.util.Card;
import org.jskat.util.CardList;
import org.jskat.util.Player;

import java.util.*;
import java.util.concurrent.atomic.AtomicLong;

public final class SkatAIJSkatPlayer extends AbstractAIPlayer implements AutoCloseable {
    private static final List<Integer> BID_VALUES = List.of(
            18, 20, 22, 23, 24, 27, 30, 33, 35, 36, 40, 44, 45, 46, 48, 50,
            54, 55, 59, 60, 63, 66, 70, 72, 77, 80, 81, 84, 88, 90, 96, 99,
            100, 108, 110, 117, 120, 121, 126, 130, 132, 135, 140, 143,
            144, 150, 153, 154, 156, 160, 162, 165, 168, 170, 176, 180,
            187, 192, 198, 204, 216, 240, 264
    );
    private static final AtomicLong GAME_COUNTER = new AtomicLong();

    private final HostClient host;
    private String gameId;
    private long sequenceNo;
    private int soloForehandAcceptedBid;
    private String cachedHandContract;
    private HostClient.PickupPlan cachedPickupPlan;

    public SkatAIJSkatPlayer() {
        this(JsonLineHostClient.fromEnvironment());
    }

    SkatAIJSkatPlayer(HostClient host) {
        this.host = Objects.requireNonNull(host, "host");
        resetAdapterState();
    }

    private void resetAdapterState() {
        gameId = "jskat:" + Long.toUnsignedString(GAME_COUNTER.incrementAndGet()) + ":" + UUID.randomUUID();
        sequenceNo = 0;
        soloForehandAcceptedBid = 0;
        cachedHandContract = null;
        cachedPickupPlan = null;
    }

    @Override
    public void prepareForNewGame() {
        resetAdapterState();
    }

    @Override
    public void finalizeGame() {
        // Keep the host process warm across a series; per-game state resets in prepareForNewGame.
    }

    @Override
    public int bidMore(int nextBidValue) {
        int seat = seat();
        int bidIndex = bidIndex(nextBidValue);

        if (nextBidValue == 264 && seat != 0) {
            int answerer = seat == 1 ? 0 : firstDuelWinner();
            if (highestBid(seat) == 264 && highestBid(answerer) == 264) {
                // JSkat repeats getNextBidValue(264) after a legal hold at the ceiling.
                // V2 treats the hold as terminal, so never create a duplicate host decision.
                return 0;
            }
        }

        int bidder;
        int answerer;
        if (seat == 0) {
            if (nextBidValue != 18 || maxAcceptedBid() != 0) {
                throw new IllegalStateException("UNEXPECTED_FOREHAND_BID_CALLBACK:" + nextBidValue);
            }
            bidder = 0;
            answerer = 0;
        } else if (seat == 1) {
            bidder = 1;
            answerer = 0;
        } else {
            bidder = 2;
            answerer = firstDuelWinner();
        }

        HostClient.Decision result = host.decide(new HostClient.DecisionRequest(
                gameId,
                nextSequence(),
                "BID",
                biddingObservation(seat, bidder, answerer, bidIndex, "BIDDER"),
                List.of(),
                "bidMore"
        ));
        return switch (result.action()) {
            case "CONTINUE" -> {
                if (seat == 0) soloForehandAcceptedBid = 18;
                yield nextBidValue;
            }
            case "PASS" -> 0;
            default -> throw new IllegalStateException("HOST_BAD_BID_ACTION:" + result.action());
        };
    }

    @Override
    public boolean holdBid(int currBidValue) {
        int seat = seat();
        if (seat == 2) throw new IllegalStateException("REARHAND_CANNOT_BE_BID_ANSWERER");
        int bidder = highestBid(2) >= currBidValue ? 2 : 1;
        if (bidder == seat) throw new IllegalStateException("BIDDER_ANSWERER_COLLISION");

        HostClient.Decision result = host.decide(new HostClient.DecisionRequest(
                gameId,
                nextSequence(),
                "BID",
                biddingObservation(seat, bidder, seat, bidIndex(currBidValue), "ANSWERER"),
                List.of(),
                "holdBid"
        ));
        return switch (result.action()) {
            case "CONTINUE" -> true;
            case "PASS" -> false;
            default -> throw new IllegalStateException("HOST_BAD_BID_ACTION:" + result.action());
        };
    }

    @Override
    public boolean pickUpSkat() {
        int winningBid = winningBid();
        List<String> legal = new ArrayList<>();
        legal.add("PICKUP");
        legal.addAll(handContracts(winningBid));

        Map<String, Object> observation = new LinkedHashMap<>();
        observation.put("cards", ownCards());
        observation.put("seat", seat());
        observation.put("winning_bid", winningBid);
        observation.put("picked_up_skat", false);
        observation.put("legal_contracts", legal);
        observation.put("max_accepted_bids_by_seat", maxAcceptedBids());

        HostClient.Decision result = host.decide(new HostClient.DecisionRequest(
                gameId,
                nextSequence(),
                "DECLARATION",
                observation,
                List.of(),
                "pickUpSkat"
        ));
        if ("PICKUP".equals(result.action())) {
            cachedHandContract = null;
            return true;
        }
        if (!legal.contains(result.action())) {
            throw new IllegalStateException("HOST_BAD_DECLARATION_ACTION:" + result.action());
        }
        cachedHandContract = result.action();
        return false;
    }

    @Override
    protected CardList getCardsToDiscard() {
        List<String> hand12 = ownCards();
        if (hand12.size() != 12) throw new IllegalStateException("PICKUP_HAND_NOT_12:" + hand12.size());

        long planSequence = sequenceNo;
        sequenceNo += 2; // The pickup-plan host operation commits DECLARATION at N and DISCARD at N+1.
        HostClient.PickupPlan plan = host.planPickup(new HostClient.PickupPlanRequest(
                gameId,
                planSequence,
                hand12,
                seat(),
                winningBid(),
                maxAcceptedBids(),
                pickupContracts(winningBid())
        ));
        validatePickupPlan(plan, hand12);
        cachedPickupPlan = plan;
        return new CardList(plan.discard().stream().map(Card::valueOf).toList());
    }

    @Override
    public GameContract announceGame() {
        List<String> hand = ownCards();
        if (hand.size() != 10) throw new IllegalStateException("ANNOUNCE_HAND_NOT_10:" + hand.size());

        if (cachedPickupPlan != null) {
            if (!new HashSet<>(hand).equals(new HashSet<>(cachedPickupPlan.finalHand()))) {
                throw new IllegalStateException("PICKUP_PLAN_FINAL_HAND_MISMATCH");
            }
            return ContractMapper.toGameContract(cachedPickupPlan.contract(), hand);
        }
        if (cachedHandContract != null) {
            return ContractMapper.toGameContract(cachedHandContract, hand);
        }
        throw new IllegalStateException("NO_CACHED_DECLARATION");
    }

    @Override
    public Card playCard() {
        GameContract contract = knowledge.getGameContract();
        Player declarer = knowledge.getDeclarer();
        if (contract == null || declarer == null) {
            throw new IllegalStateException("CARDPLAY_WITHOUT_CONTRACT");
        }

        List<String> hand = ownCards();
        List<List<Object>> currentTrick = trickEntries(knowledge.getCurrentTrick());
        List<List<Object>> played = new ArrayList<>();
        for (Trick trick : knowledge.getCompletedTricks()) {
            played.addAll(trickEntries(trick));
        }
        played.addAll(currentTrick);

        List<String> legal = cardNames(getPlayableCards(knowledge.getTrickCards()));
        if (legal.isEmpty()) throw new IllegalStateException("JSKAT_NO_LEGAL_CARDS");

        Map<String, Object> observation = new LinkedHashMap<>();
        observation.put("hand", hand);
        observation.put("seat", seat());
        observation.put("declarer", declarer.getOrder());
        observation.put("contract", ContractMapper.toToken(contract));
        observation.put("winning_bid", winningBid());
        observation.put("current_trick", currentTrick);
        observation.put("played_cards", played);
        observation.put("legal_cards", legal);
        observation.put("max_accepted_bids_by_seat", maxAcceptedBids());
        observation.put("blind_hand", contract.hand());
        int[] publicPoints = publicPoints(declarer, contract);
        observation.put("points_self", publicPoints[0]);
        observation.put("points_other", publicPoints[1]);

        if (seat() == declarer.getOrder() && !contract.hand()) {
            if (cachedPickupPlan == null) {
                throw new IllegalStateException("DECLARER_PICKUP_PLAN_MISSING_AT_CARDPLAY");
            }
            observation.put("skat_cards", cachedPickupPlan.discard());
        }
        if (contract.ouvert()) {
            observation.put(
                    "open_hand_cards",
                    openHandRemaining(declarer.getOrder(), played)
            );
        }

        HostClient.Decision result = host.decide(new HostClient.DecisionRequest(
                gameId,
                nextSequence(),
                "PLAY_CARD",
                observation,
                legal,
                "playCard"
        ));
        if (!legal.contains(result.action())) {
            throw new IllegalStateException("HOST_ILLEGAL_CARD_ACTION:" + result.action());
        }
        return Card.valueOf(result.action());
    }

    @Override
    public void startGame() {
        // Cardplay integration is gated separately; declaration mapping has completed here.
    }

    @Override
    public boolean callContra() {
        return false;
    }

    @Override
    public boolean callRe() {
        return false;
    }

    @Override
    public boolean playGrandHand() {
        return false;
    }

    private Map<String, Object> biddingObservation(
            int actor, int bidder, int answerer, int bidIndex, String role) {
        Map<String, Object> observation = new LinkedHashMap<>();
        observation.put("hand", ownCards());
        observation.put("actor", actor);
        observation.put("bidder", bidder);
        observation.put("answerer", answerer);
        observation.put("bid_index", bidIndex);
        observation.put("decision_role", role);
        return observation;
    }

    private void validatePickupPlan(HostClient.PickupPlan plan, List<String> hand12) {
        if (!gameId.equals(plan.gameId())) throw new IllegalStateException("PICKUP_PLAN_GAME_MISMATCH");
        if (plan.discard().size() != 2 || new HashSet<>(plan.discard()).size() != 2) {
            throw new IllegalStateException("PICKUP_PLAN_BAD_DISCARD");
        }
        if (!hand12.containsAll(plan.discard())) throw new IllegalStateException("PICKUP_PLAN_DISCARD_NOT_OWNED");
        if (!pickupContracts(winningBid()).contains(plan.contract())) {
            throw new IllegalStateException("PICKUP_PLAN_BAD_CONTRACT");
        }
        Set<String> expected = new HashSet<>(hand12);
        expected.removeAll(plan.discard());
        if (expected.size() != 10 || !expected.equals(new HashSet<>(plan.finalHand()))) {
            throw new IllegalStateException("PICKUP_PLAN_BAD_FINAL_HAND");
        }
    }

    private int firstDuelWinner() {
        return highestBid(1) > highestBid(0) ? 1 : 0;
    }

    private int winningBid() {
        int value = Math.max(maxAcceptedBid(), soloForehandAcceptedBid);
        if (value < 18 && inferredSoloForehand18()) value = 18;
        if (value < 18) throw new IllegalStateException("NO_WINNING_BID");
        return value;
    }

    private int maxAcceptedBid() {
        return Math.max(highestBid(0), Math.max(highestBid(1), highestBid(2)));
    }

    private boolean inferredSoloForehand18() {
        return soloForehandAcceptedBid == 18
                || (maxAcceptedBid() == 0
                && knowledge.getDeclarer() == Player.FOREHAND
                && knowledge.getGameContract() != null);
    }

    private List<Integer> maxAcceptedBids() {
        List<Integer> out = new ArrayList<>(List.of(highestBid(0), highestBid(1), highestBid(2)));
        if (soloForehandAcceptedBid > out.get(0)) out.set(0, soloForehandAcceptedBid);
        if (out.equals(List.of(0, 0, 0)) && inferredSoloForehand18()) out.set(0, 18);
        return List.copyOf(out);
    }

    private int highestBid(int seat) {
        Integer value = knowledge.getHighestBid(Player.values()[seat]);
        return value == null ? 0 : value;
    }

    private int seat() {
        Player position = knowledge.getPlayerPosition();
        if (position == null) throw new IllegalStateException("PLAYER_POSITION_UNKNOWN");
        return position.getOrder();
    }

    private List<String> ownCards() {
        return cardNames(knowledge.getOwnCards());
    }

    private static List<String> cardNames(CardList cardList) {
        List<String> cards = new ArrayList<>();
        for (Card card : cardList) {
            cards.add(card.name());
        }
        cards.sort(String::compareTo);
        return List.copyOf(cards);
    }

    private static List<List<Object>> trickEntries(Trick trick) {
        if (trick == null) throw new IllegalStateException("CURRENT_TRICK_MISSING");
        List<List<Object>> out = new ArrayList<>();
        Player actor = trick.getForeHand();
        for (Card card : trick.getCardList()) {
            List<Object> entry = new ArrayList<>(2);
            entry.add(actor.getOrder());
            entry.add(card.name());
            out.add(List.copyOf(entry));
            actor = actor.getLeftNeighbor();
        }
        return List.copyOf(out);
    }

    private List<String> openHandRemaining(int declarerSeat, List<List<Object>> played) {
        if (seat() == declarerSeat) return ownCards();

        List<String> visible = new ArrayList<>(cardNames(knowledge.getDeclarerPlayerCards()));
        Set<String> playedByDeclarer = new HashSet<>();
        for (List<Object> entry : played) {
            if (((Number) entry.get(0)).intValue() == declarerSeat) {
                playedByDeclarer.add(String.valueOf(entry.get(1)));
            }
        }
        visible.removeIf(playedByDeclarer::contains);
        visible.sort(String::compareTo);
        return List.copyOf(visible);
    }

    private int[] publicPoints(Player declarer, GameContract contract) {
        int declarerPoints = 0;
        int defenderPoints = 0;
        for (Trick trick : knowledge.getCompletedTricks()) {
            if (!trick.isTrickFinished() || trick.getTrickWinner() == null) {
                throw new IllegalStateException("INCOMPLETE_COMPLETED_TRICK");
            }
            int trickPoints = 0;
            for (Card card : trick.getCardList()) {
                trickPoints += card.getPoints();
            }
            if (trick.getTrickWinner() == declarer) {
                declarerPoints += trickPoints;
            } else {
                defenderPoints += trickPoints;
            }
        }

        if (seat() == declarer.getOrder() && !contract.hand()) {
            if (cachedPickupPlan == null) {
                throw new IllegalStateException("DECLARER_PICKUP_PLAN_MISSING_AT_CARDPLAY");
            }
            for (String card : cachedPickupPlan.discard()) {
                declarerPoints += Card.valueOf(card).getPoints();
            }
        }
        return new int[]{declarerPoints, defenderPoints};
    }

    private long nextSequence() {
        return sequenceNo++;
    }

    private static int bidIndex(int bidValue) {
        int index = BID_VALUES.indexOf(bidValue);
        if (index < 0) throw new IllegalArgumentException("UNSUPPORTED_BID_VALUE:" + bidValue);
        return index;
    }

    private static List<String> handContracts(int winningBid) {
        List<String> out = new ArrayList<>(ContractMapper.INITIAL_DECLARATION_ACTIONS);
        out.remove("PICKUP");
        if (winningBid > 35) out.remove("NH");
        if (winningBid > 59) out.remove("NHO");
        return List.copyOf(out);
    }

    private static List<String> pickupContracts(int winningBid) {
        List<String> out = new ArrayList<>(ContractMapper.PICKUP_CONTRACTS);
        if (winningBid > 23) out.remove("N");
        if (winningBid > 46) out.remove("NO");
        return List.copyOf(out);
    }

    @Override
    public void close() {
        host.close();
    }
}
