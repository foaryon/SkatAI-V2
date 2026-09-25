package org.skatai.v2.jskat;

import org.jskat.data.GameContract;
import org.jskat.data.Trick;
import org.jskat.util.Card;
import org.jskat.util.CardList;
import org.jskat.util.GameType;
import org.jskat.util.Player;
import org.junit.jupiter.api.Test;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

final class SkatAIJSkatPlayerTest {
    private static final CardList HAND10 = CardList.of(
            Card.CJ, Card.DJ, Card.DA, Card.DK, Card.DQ,
            Card.D7, Card.C9, Card.HA, Card.HT, Card.HK
    );

    private static final class FakeHost implements HostClient {
        final List<DecisionRequest> decisions = new ArrayList<>();
        final List<PickupPlanRequest> plans = new ArrayList<>();
        final Deque<Decision> decisionReplies = new ArrayDeque<>();
        PickupPlan pickupReply;

        @Override
        public Decision decide(DecisionRequest request) {
            decisions.add(request);
            Decision reply = decisionReplies.pollFirst();
            if (reply == null) throw new AssertionError("UNEXPECTED_DECISION_REQUEST:" + request);
            return reply;
        }

        @Override
        public PickupPlan planPickup(PickupPlanRequest request) {
            plans.add(request);
            if (pickupReply == null) throw new AssertionError("UNEXPECTED_PICKUP_PLAN_REQUEST:" + request);
            return new PickupPlan(
                    pickupReply.releaseId(),
                    request.gameId(),
                    request.sequenceNo(),
                    pickupReply.planId(),
                    pickupReply.hand12Sha256(),
                    pickupReply.discard(),
                    pickupReply.contract(),
                    pickupReply.finalHand()
            );
        }

        void reply(String action, String decisionType) {
            decisionReplies.add(new Decision(
                    action, "R-TEST", "REQ-" + (decisionReplies.size() + 1),
                    "POS", decisionType
            ));
        }
    }

    @Test
    void forehandSolo18UsesLegalSelfOfferAndPreservesWinningBidForHandDeclaration() {
        FakeHost host = new FakeHost();
        host.reply("CONTINUE", "BID");
        host.reply("GH", "DECLARATION");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.FOREHAND);
        player.takeCards(new CardList(HAND10));
        player.setUpBidding();

        assertEquals(18, player.bidMore(18));
        HostClient.DecisionRequest bid = host.decisions.get(0);
        assertEquals("BID", bid.decisionType());
        assertEquals(0, bid.observation().get("actor"));
        assertEquals(0, bid.observation().get("bidder"));
        assertEquals(0, bid.observation().get("answerer"));
        assertEquals(0, bid.observation().get("bid_index"));
        assertEquals("BIDDER", bid.observation().get("decision_role"));

        assertFalse(player.pickUpSkat());
        HostClient.DecisionRequest declaration = host.decisions.get(1);
        assertEquals(18, declaration.observation().get("winning_bid"));
        assertEquals(List.of(18, 0, 0), declaration.observation().get("max_accepted_bids_by_seat"));

        GameContract contract = player.announceGame();
        assertEquals(GameType.GRAND, contract.gameType());
        assertTrue(contract.hand());
    }

    @Test
    void rearhandBidUsesWinnerOfFirstDuelAsAnswerer() {
        FakeHost host = new FakeHost();
        host.reply("CONTINUE", "BID");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.REARHAND);
        player.takeCards(new CardList(HAND10));
        player.setUpBidding();
        player.bidByPlayer(Player.MIDDLEHAND, 18);
        player.bidByPlayer(Player.FOREHAND, 18);

        assertEquals(20, player.bidMore(20));
        HostClient.DecisionRequest req = host.decisions.get(0);
        assertEquals(2, req.observation().get("bidder"));
        assertEquals(0, req.observation().get("answerer"));
        assertEquals(1, req.observation().get("bid_index"));
    }

    @Test
    void repeated264CallbackPassesWithoutDuplicateHostDecision() {
        FakeHost host = new FakeHost();
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.MIDDLEHAND);
        player.takeCards(new CardList(HAND10));
        player.setUpBidding();
        player.bidByPlayer(Player.MIDDLEHAND, 264);
        player.bidByPlayer(Player.FOREHAND, 264);

        assertEquals(0, player.bidMore(264));
        assertTrue(host.decisions.isEmpty());
    }

    @Test
    void pickupPlanIsAtomicAcrossDiscardAndAnnouncement() {
        FakeHost host = new FakeHost();
        host.reply("PICKUP", "DECLARATION");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.MIDDLEHAND);
        player.takeCards(new CardList(HAND10));
        player.setUpBidding();
        player.bidByPlayer(Player.MIDDLEHAND, 18);

        assertTrue(player.pickUpSkat());
        player.takeSkat(CardList.of(Card.CT, Card.ST));

        List<String> hand12 = List.of(
                "C9", "CJ", "CT", "D7", "DA", "DJ",
                "DK", "DQ", "HA", "HK", "HT", "ST"
        );
        List<String> discard = List.of("CT", "ST");
        List<String> finalHand = List.of("C9", "CJ", "D7", "DA", "DJ", "DK", "DQ", "HA", "HK", "HT");
        host.pickupReply = new HostClient.PickupPlan(
                "R-TEST", "placeholder", 0, "PLAN-1", "HASH",
                discard, "C", finalHand
        );

        CardList discarded = player.discardSkat();
        assertEquals(2, discarded.size());
        assertTrue(discarded.contains(Card.CT));
        assertTrue(discarded.contains(Card.ST));

        assertEquals(1, host.plans.size());
        HostClient.PickupPlanRequest plan = host.plans.get(0);
        assertEquals(hand12, plan.hand12());
        assertEquals(18, plan.winningBid());
        assertEquals(List.of(0, 18, 0), plan.maxAcceptedBidsBySeat());
        assertTrue(plan.legalContracts().contains("C"));

        GameContract contract = player.announceGame();
        assertEquals(GameType.CLUBS, contract.gameType());
        assertFalse(contract.hand());
    }

    @Test
    void defenderCardplayInfersUnreportedSoloForehand18AndUsesJSkatLegalSet() {
        FakeHost host = new FakeHost();
        host.reply("C9", "PLAY_CARD");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.MIDDLEHAND);
        player.takeCards(new CardList(HAND10));
        GameContract contract = new GameContract(GameType.CLUBS, true);
        player.startGame(Player.FOREHAND, contract);
        player.newTrick(0, Player.FOREHAND);
        player.cardPlayed(Player.FOREHAND, Card.C7);

        assertEquals(Card.C9, player.playCard());
        HostClient.DecisionRequest req = host.decisions.get(0);
        assertEquals("PLAY_CARD", req.decisionType());
        assertEquals(18, req.observation().get("winning_bid"));
        assertEquals(List.of(18, 0, 0), req.observation().get("max_accepted_bids_by_seat"));
        assertEquals("CH", req.observation().get("contract"));
        assertEquals(true, req.observation().get("blind_hand"));
        assertEquals(0, req.observation().get("points_self"));
        assertEquals(0, req.observation().get("points_other"));
        assertEquals(List.of(List.of(0, "C7")), req.observation().get("current_trick"));
        assertEquals(List.of(List.of(0, "C7")), req.observation().get("played_cards"));
        assertTrue(req.legalActions().contains("C9"));
        assertTrue(req.legalActions().contains("CJ"));
    }

    @Test
    void pickupDeclarerCardplayExposesOnlyBuriedCardsAsSkat() {
        FakeHost host = new FakeHost();
        host.reply("PICKUP", "DECLARATION");
        host.reply("C9", "PLAY_CARD");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.MIDDLEHAND);
        player.takeCards(new CardList(HAND10));
        player.bidByPlayer(Player.MIDDLEHAND, 18);
        assertTrue(player.pickUpSkat());
        player.takeSkat(CardList.of(Card.CT, Card.ST));
        host.pickupReply = new HostClient.PickupPlan(
                "R-TEST", "placeholder", 0, "PLAN-2", "HASH",
                List.of("CT", "ST"), "C",
                List.of("C9", "CJ", "D7", "DA", "DJ", "DK", "DQ", "HA", "HK", "HT")
        );
        player.discardSkat();
        GameContract contract = player.announceGame();
        player.startGame(Player.MIDDLEHAND, contract);
        player.newTrick(0, Player.FOREHAND);
        player.cardPlayed(Player.FOREHAND, Card.C7);

        assertEquals(Card.C9, player.playCard());
        HostClient.DecisionRequest req = host.decisions.get(1);
        assertEquals(List.of("CT", "ST"), req.observation().get("skat_cards"));
        assertEquals(false, req.observation().get("blind_hand"));
        assertEquals(20, req.observation().get("points_self"));
        assertEquals(0, req.observation().get("points_other"));
        assertEquals("C", req.observation().get("contract"));
        assertFalse(req.observation().containsKey("known_private_cards"));
    }

    @Test
    void completedTrickPointsAreAttributedFromJSkatWinnerState() {
        FakeHost host = new FakeHost();
        host.reply("C9", "PLAY_CARD");
        SkatAIJSkatPlayer player = new SkatAIJSkatPlayer(host);

        player.newGame(Player.MIDDLEHAND);
        player.takeCards(new CardList(HAND10));
        GameContract contract = new GameContract(GameType.CLUBS, true);
        player.startGame(Player.FOREHAND, contract);

        player.newTrick(0, Player.FOREHAND);
        player.cardPlayed(Player.FOREHAND, Card.D8);
        player.cardPlayed(Player.MIDDLEHAND, Card.DJ);
        player.cardPlayed(Player.REARHAND, Card.S7);
        Trick completed = new Trick(0, Player.FOREHAND);
        completed.addCard(Card.D8);
        completed.addCard(Card.DJ);
        completed.addCard(Card.S7);
        completed.setTrickWinner(Player.MIDDLEHAND);
        player.showTrick(completed);
        player.newTrick(1, Player.MIDDLEHAND);

        assertEquals(Card.C9, player.playCard());
        HostClient.DecisionRequest req = host.decisions.get(0);
        assertEquals(0, req.observation().get("points_self"));
        assertEquals(2, req.observation().get("points_other"));
        assertEquals(9, ((List<?>) req.observation().get("hand")).size());
        assertEquals(
                List.of(
                        List.of(0, "D8"),
                        List.of(1, "DJ"),
                        List.of(2, "S7")
                ),
                req.observation().get("played_cards")
        );
    }
}
