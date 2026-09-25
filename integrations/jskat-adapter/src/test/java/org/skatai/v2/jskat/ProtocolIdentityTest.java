package org.skatai.v2.jskat;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class ProtocolIdentityTest {
    @Test
    void matchesPythonCanonicalDecisionIdentityReference() {
        List<String> hand = List.of(
                "CJ", "DJ", "DA", "DK", "DQ",
                "D7", "C9", "HA", "HT", "HK"
        );
        HostClient.DecisionRequest request = new HostClient.DecisionRequest(
                "jskat-java-release-smoke",
                0,
                "BID",
                Map.of(
                        "hand", hand,
                        "actor", 1,
                        "bidder", 1,
                        "answerer", 0,
                        "bid_index", 0,
                        "decision_role", "BIDDER"
                ),
                List.of(),
                "bidMore"
        );
        ProtocolIdentity.DecisionIdentity identity =
                ProtocolIdentity.decisionIdentity(request);
        assertEquals(
                "5e64d86f036887cca08464f6084e5ecccf2f2e3e60739756335f2a8fb25d3033",
                identity.positionHash()
        );
        assertEquals(
                "e9d5839cda54c2b907b86d0c6812fd838eed4753aec2572e07b8b168688c83e1",
                identity.requestId()
        );
        assertEquals(List.of("PASS", "CONTINUE"), identity.legalActions());
    }

    @Test
    void matchesPythonCanonicalCardplayIdentityWithDefaultFields() {
        List<String> hand = List.of(
                "C9", "CJ", "D7", "DA", "DJ",
                "DK", "DQ", "HA", "HK", "HT"
        );
        Map<String, Object> observation = new java.util.LinkedHashMap<>();
        observation.put("hand", hand);
        observation.put("seat", 0);
        observation.put("declarer", 0);
        observation.put("contract", "C");
        observation.put("winning_bid", 18);
        observation.put("current_trick", List.of());
        observation.put("played_cards", List.of());
        observation.put("legal_cards", hand);
        observation.put("max_accepted_bids_by_seat", List.of(18, 0, 0));
        observation.put("skat_cards", List.of("CT", "ST"));
        observation.put("blind_hand", false);

        ProtocolIdentity.DecisionIdentity identity =
                ProtocolIdentity.decisionIdentity(new HostClient.DecisionRequest(
                        "card-id", 2, "PLAY_CARD", observation, hand, "playCard"
                ));
        assertEquals(
                "b09c227d29f76e66d706c5df40f773e3050a48b386d38d214513c662ac808f23",
                identity.positionHash()
        );
        assertEquals(
                "bdbc00019a01bfb3f9938229d50ad367857ad39e84b87cbd77bc4280c86ffa1d",
                identity.requestId()
        );
    }

    @Test
    void matchesPythonCanonicalPickupHandHashReference() {
        assertEquals(
                "fa7168edcd073449699202b92a1b1606c4a8a0020e6bf1b041654d1bdd10b711",
                ProtocolIdentity.sha256Json(List.of(
                        "CJ", "DJ", "DA", "DK", "DQ", "D7",
                        "C9", "HA", "HT", "HK", "CT", "ST"
                ))
        );
    }
}
