package org.skatai.v2.jskat;

import org.jskat.util.Card;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

final class JsonLineHostClientIntegrationTest {
    @Test
    void validatedReleaseRoundTripsThroughRealPythonHostWhenConfigured() {
        assumeTrue(System.getenv("SKATAI_V2_PACKAGE") != null, "release smoke not configured");
        assumeTrue(System.getenv("SKATAI_V2_PYTHON") != null, "release smoke not configured");
        assumeTrue(System.getenv("SKATAI_V2_MATERIALIZE_TO") != null, "release smoke not configured");

        try (JsonLineHostClient client = JsonLineHostClient.fromEnvironment()) {
            List<String> hand = List.of(
                    "CJ", "DJ", "DA", "DK", "DQ",
                    "D7", "C9", "HA", "HT", "HK"
            );
            Map<String, Object> first = Map.of(
                    "hand", hand,
                    "actor", 1,
                    "bidder", 1,
                    "answerer", 0,
                    "bid_index", 0,
                    "decision_role", "BIDDER"
            );
            HostClient.Decision d1 = client.decide(new HostClient.DecisionRequest(
                    "jskat-java-release-smoke", 0, "BID", first, List.of(), "bidMore"
            ));
            assertEquals("CONTINUE", d1.action());
            assertEquals("BID", d1.decisionType());
            String expectedRelease = System.getenv("SKATAI_V2_RELEASE_ID");
            if (expectedRelease != null && !expectedRelease.isBlank()) {
                assertEquals(expectedRelease, d1.releaseId());
            }

            Map<String, Object> second = Map.of(
                    "hand", hand,
                    "actor", 1,
                    "bidder", 1,
                    "answerer", 0,
                    "bid_index", 1,
                    "decision_role", "BIDDER"
            );
            HostClient.Decision d2 = client.decide(new HostClient.DecisionRequest(
                    "jskat-java-release-smoke", 1, "BID", second, List.of(), "bidMore"
            ));
            assertEquals("CONTINUE", d2.action());
            assertEquals(d1.releaseId(), d2.releaseId());
            assertNotEquals(d1.requestId(), d2.requestId());

            List<String> hand12 = List.of(
                    "CJ", "DJ", "DA", "DK", "DQ", "D7",
                    "C9", "HA", "HT", "HK", "CT", "ST"
            );
            HostClient.PickupPlan plan = client.planPickup(new HostClient.PickupPlanRequest(
                    "jskat-java-allphase", 0, hand12, 0, 18,
                    List.of(18, 0, 0),
                    List.of("C", "S", "H", "D", "G", "N", "NO")
            ));
            assertEquals(2, plan.discard().size());
            assertEquals(10, plan.finalHand().size());
            assertTrue(List.of("C", "S", "H", "D", "G", "N", "NO").contains(plan.contract()));
            assertEquals(d1.releaseId(), plan.releaseId());

            Map<String, Object> cardplay = new java.util.LinkedHashMap<>();
            cardplay.put("hand", plan.finalHand());
            cardplay.put("seat", 0);
            cardplay.put("declarer", 0);
            cardplay.put("contract", plan.contract());
            cardplay.put("winning_bid", 18);
            cardplay.put("current_trick", List.of());
            cardplay.put("played_cards", List.of());
            cardplay.put("legal_cards", plan.finalHand());
            cardplay.put("max_accepted_bids_by_seat", List.of(18, 0, 0));
            cardplay.put("skat_cards", plan.discard());
            cardplay.put("blind_hand", false);
            int buriedPoints = plan.discard().stream()
                    .map(Card::valueOf)
                    .mapToInt(Card::getPoints)
                    .sum();
            cardplay.put("points_self", buriedPoints);
            cardplay.put("points_other", 0);
            if (plan.contract().contains("O")) {
                cardplay.put("open_hand_cards", plan.finalHand());
            }
            HostClient.Decision play = client.decide(new HostClient.DecisionRequest(
                    "jskat-java-allphase", 2, "PLAY_CARD", cardplay,
                    plan.finalHand(), "playCard"
            ));
            assertTrue(plan.finalHand().contains(play.action()));
            assertEquals("PLAY_CARD", play.decisionType());
            assertEquals(plan.releaseId(), play.releaseId());
        }
    }
}
