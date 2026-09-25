package org.skatai.v2.jskat;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

final class ProtocolIdentity {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String DECISION_SCHEMA = "skatai.v2.decision-envelope.v1";

    record DecisionIdentity(String positionHash, String requestId, List<String> legalActions) {}

    private ProtocolIdentity() {
    }

    static DecisionIdentity decisionIdentity(HostClient.DecisionRequest request) {
        List<String> legalActions = legalActions(request);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("schema", DECISION_SCHEMA);
        body.put("game_id", request.gameId());
        body.put("sequence_no", request.sequenceNo());
        body.put("decision_type", request.decisionType());
        body.put("observation", normalizedObservation(request));
        body.put("legal_actions", legalActions);
        body.put("source", "HOST");
        body.put(
                "source_context",
                request.callback().isBlank()
                        ? Map.of()
                        : Map.of("callback", request.callback())
        );
        String positionHash = sha256Json(body);
        String requestId = sha256Text("skatai.v2.request\0" + positionHash);
        return new DecisionIdentity(positionHash, requestId, legalActions);
    }

    static List<String> legalActions(HostClient.DecisionRequest request) {
        if (!request.legalActions().isEmpty()) return request.legalActions();
        Map<String, Object> obs = request.observation();
        return switch (request.decisionType()) {
            case "BID" -> List.of("PASS", "CONTINUE");
            case "DECLARATION" -> stringList(obs.get("legal_contracts"), "legal_contracts");
            case "DISCARD" -> discardActions(stringList(obs.get("hand12"), "hand12"));
            case "PLAY_CARD" -> stringList(obs.get("legal_cards"), "legal_cards");
            default -> throw new IllegalArgumentException("UNKNOWN_DECISION_TYPE:" + request.decisionType());
        };
    }

    private static Map<String, Object> normalizedObservation(
            HostClient.DecisionRequest request) {
        Map<String, Object> out = new LinkedHashMap<>(request.observation());
        switch (request.decisionType()) {
            case "BID" -> {
                // No defaulted fields.
            }
            case "DECLARATION", "DISCARD" ->
                    out.putIfAbsent("max_accepted_bids_by_seat", null);
            case "PLAY_CARD" -> {
                out.putIfAbsent("known_private_cards", List.of());
                out.putIfAbsent("points_self", null);
                out.putIfAbsent("points_other", null);
                out.putIfAbsent("max_accepted_bids_by_seat", null);
                out.putIfAbsent("skat_cards", List.of());
                out.putIfAbsent("blind_hand", false);
                out.putIfAbsent("open_hand_cards", List.of());
            }
            default -> throw new IllegalArgumentException(
                    "UNKNOWN_DECISION_TYPE:" + request.decisionType()
            );
        }
        return Collections.unmodifiableMap(out);
    }

    static String sha256Json(Object value) {
        try {
            return sha256Text(MAPPER.writeValueAsString(canonical(value)));
        } catch (JsonProcessingException exc) {
            throw new IllegalStateException("CANONICAL_JSON_FAILED", exc);
        }
    }

    private static Object canonical(Object value) {
        if (value == null
                || value instanceof String
                || value instanceof Boolean
                || value instanceof Number) {
            return value;
        }
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> out = new TreeMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                out.put(String.valueOf(entry.getKey()), canonical(entry.getValue()));
            }
            return out;
        }
        if (value instanceof Iterable<?> iterable) {
            List<Object> out = new ArrayList<>();
            for (Object item : iterable) out.add(canonical(item));
            return out;
        }
        if (value.getClass().isArray()) {
            List<Object> out = new ArrayList<>();
            int length = java.lang.reflect.Array.getLength(value);
            for (int i = 0; i < length; i++) {
                out.add(canonical(java.lang.reflect.Array.get(value, i)));
            }
            return out;
        }
        throw new IllegalArgumentException("UNSUPPORTED_CANONICAL_VALUE:" + value.getClass().getName());
    }

    private static List<String> discardActions(List<String> cards) {
        List<String> out = new ArrayList<>();
        for (int i = 0; i < cards.size(); i++) {
            for (int j = i + 1; j < cards.size(); j++) {
                out.add(cards.get(i) + "." + cards.get(j));
            }
        }
        return List.copyOf(out);
    }

    private static List<String> stringList(Object value, String field) {
        if (!(value instanceof List<?> list) || list.stream().anyMatch(x -> !(x instanceof String))) {
            throw new IllegalArgumentException("BAD_STRING_LIST:" + field);
        }
        return list.stream().map(String.class::cast).toList();
    }

    private static String sha256Text(String value) {
        final MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException("SHA256_UNAVAILABLE", exc);
        }
        return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
    }
}
