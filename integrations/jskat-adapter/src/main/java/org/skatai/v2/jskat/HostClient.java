package org.skatai.v2.jskat;

import java.util.List;
import java.util.Map;

public interface HostClient extends AutoCloseable {

    Decision decide(DecisionRequest request);

    PickupPlan planPickup(PickupPlanRequest request);

    @Override
    default void close() {
    }

    record DecisionRequest(
            String gameId,
            long sequenceNo,
            String decisionType,
            Map<String, Object> observation,
            List<String> legalActions,
            String callback) {
        public DecisionRequest {
            if (gameId == null || gameId.isBlank()) throw new IllegalArgumentException("EMPTY_GAME_ID");
            if (sequenceNo < 0) throw new IllegalArgumentException("NEGATIVE_SEQUENCE");
            if (decisionType == null || decisionType.isBlank()) throw new IllegalArgumentException("EMPTY_DECISION_TYPE");
            observation = Map.copyOf(observation);
            legalActions = legalActions == null ? List.of() : List.copyOf(legalActions);
            callback = callback == null ? "" : callback;
        }
    }

    record Decision(
            String action,
            String releaseId,
            String requestId,
            String positionHash,
            String decisionType) {
        public Decision {
            if (action == null || action.isBlank()) throw new IllegalArgumentException("EMPTY_ACTION");
            if (releaseId == null || releaseId.isBlank()) throw new IllegalArgumentException("EMPTY_RELEASE_ID");
        }
    }

    record PickupPlanRequest(
            String gameId,
            long sequenceNo,
            List<String> hand12,
            int seat,
            int winningBid,
            List<Integer> maxAcceptedBidsBySeat,
            List<String> legalContracts) {
        public PickupPlanRequest {
            hand12 = List.copyOf(hand12);
            maxAcceptedBidsBySeat = List.copyOf(maxAcceptedBidsBySeat);
            legalContracts = List.copyOf(legalContracts);
        }
    }

    record PickupPlan(
            String releaseId,
            String gameId,
            long sequenceNo,
            String planId,
            String hand12Sha256,
            List<String> discard,
            String contract,
            List<String> finalHand) {
        public PickupPlan {
            discard = List.copyOf(discard);
            finalHand = List.copyOf(finalHand);
        }
    }
}
