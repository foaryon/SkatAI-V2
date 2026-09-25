package org.skatai.v2.jskat;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.*;

public final class JsonLineHostClient implements HostClient {
    public static final String REQUEST_SCHEMA = "skatai.v2.host-request.v1";
    public static final String RESPONSE_SCHEMA = "skatai.v2.host-response.v1";
    public static final String PICKUP_REQUEST_SCHEMA = "skatai.v2.host-pickup-plan-request.v1";
    public static final String PICKUP_RESPONSE_SCHEMA = "skatai.v2.host-pickup-plan-response.v1";

    private final ObjectMapper mapper = new ObjectMapper();
    private final Process process;
    private final BufferedWriter writer;
    private final BufferedReader reader;
    private final ExecutorService readerExecutor;
    private final Duration timeout;
    private final String expectedReleaseId;

    public JsonLineHostClient(List<String> command, Duration timeout, String expectedReleaseId) {
        if (command == null || command.isEmpty()) throw new IllegalArgumentException("EMPTY_HOST_COMMAND");
        this.timeout = Objects.requireNonNull(timeout, "timeout");
        if (timeout.isZero() || timeout.isNegative()) throw new IllegalArgumentException("BAD_HOST_TIMEOUT");
        this.expectedReleaseId = expectedReleaseId == null ? "" : expectedReleaseId.trim();
        try {
            ProcessBuilder pb = new ProcessBuilder(command);
            String explicitPythonPath = System.getenv("SKATAI_V2_PYTHONPATH");
            if (explicitPythonPath != null && !explicitPythonPath.isBlank()) {
                String inherited = pb.environment().getOrDefault("PYTHONPATH", "");
                pb.environment().put(
                        "PYTHONPATH",
                        explicitPythonPath + (inherited.isBlank()
                                ? ""
                                : java.io.File.pathSeparator + inherited)
                );
            }
            pb.redirectError(ProcessBuilder.Redirect.INHERIT);
            process = pb.start();
            writer = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
            reader = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
        } catch (IOException exc) {
            throw new IllegalStateException("HOST_PROCESS_START_FAILED", exc);
        }
        readerExecutor = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "skatai-host-jsonl-reader");
            t.setDaemon(true);
            return t;
        });
    }

    public static JsonLineHostClient fromEnvironment() {
        String python = requiredEnv("SKATAI_V2_PYTHON");
        String packagePath = requiredEnv("SKATAI_V2_PACKAGE");
        String materialize = requiredEnv("SKATAI_V2_MATERIALIZE_TO");
        String expectedRelease = System.getenv().getOrDefault("SKATAI_V2_RELEASE_ID", "");
        long timeoutMs = parsePositiveLong(
                System.getenv().getOrDefault("SKATAI_V2_HOST_TIMEOUT_MS", "30000"),
                "SKATAI_V2_HOST_TIMEOUT_MS"
        );
        List<String> command = List.of(
                python,
                "-m", "skatai.runtime.host_service",
                "--package", Path.of(packagePath).toAbsolutePath().normalize().toString(),
                "--materialize-to", Path.of(materialize).toAbsolutePath().normalize().toString(),
                "--python", Path.of(python).toAbsolutePath().normalize().toString()
        );
        return new JsonLineHostClient(command, Duration.ofMillis(timeoutMs), expectedRelease);
    }

    private static String requiredEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) throw new IllegalStateException("MISSING_ENV:" + name);
        return value;
    }

    private static long parsePositiveLong(String value, String field) {
        try {
            long out = Long.parseLong(value);
            if (out <= 0) throw new NumberFormatException();
            return out;
        } catch (NumberFormatException exc) {
            throw new IllegalStateException("BAD_ENV:" + field, exc);
        }
    }

    @Override
    public synchronized Decision decide(DecisionRequest request) {
        ProtocolIdentity.DecisionIdentity expectedIdentity =
                ProtocolIdentity.decisionIdentity(request);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schema", REQUEST_SCHEMA);
        payload.put("game_id", request.gameId());
        payload.put("sequence_no", request.sequenceNo());
        payload.put("decision_type", request.decisionType());
        payload.put("observation", request.observation());
        payload.put("source", "HOST");
        if (!request.callback().isBlank()) {
            payload.put("source_context", Map.of("callback", request.callback()));
        }
        if (!request.legalActions().isEmpty()) {
            payload.put("legal_actions", request.legalActions());
        }
        Map<String, Object> response = exchange(payload);
        requireSchema(response, RESPONSE_SCHEMA);
        requireOk(response);
        Map<String, Object> result = object(response.get("result"), "HOST_RESULT_NOT_OBJECT");
        Decision decision = new Decision(
                text(result, "action"),
                text(result, "release_id"),
                text(result, "request_id"),
                text(result, "position_hash"),
                text(result, "decision_type")
        );
        if (!decision.decisionType().equals(request.decisionType())) {
            failAndStop("HOST_DECISION_TYPE_MISMATCH");
        }
        if (!expectedIdentity.requestId().equals(decision.requestId())) {
            failAndStop("HOST_REQUEST_ID_MISMATCH");
        }
        if (!expectedIdentity.positionHash().equals(decision.positionHash())) {
            failAndStop("HOST_POSITION_HASH_MISMATCH");
        }
        if (!expectedIdentity.legalActions().contains(decision.action())) {
            failAndStop("HOST_ACTION_OUTSIDE_LEGAL_SET");
        }
        validateRelease(decision.releaseId());
        return decision;
    }

    @Override
    public synchronized PickupPlan planPickup(PickupPlanRequest request) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schema", PICKUP_REQUEST_SCHEMA);
        payload.put("game_id", request.gameId());
        payload.put("sequence_no", request.sequenceNo());
        payload.put("hand12", request.hand12());
        payload.put("seat", request.seat());
        payload.put("winning_bid", request.winningBid());
        payload.put("max_accepted_bids_by_seat", request.maxAcceptedBidsBySeat());
        payload.put("legal_contracts", request.legalContracts());
        Map<String, Object> response = exchange(payload);
        requireSchema(response, PICKUP_RESPONSE_SCHEMA);
        requireOk(response);
        PickupPlan plan = new PickupPlan(
                text(response, "release_id"),
                text(response, "game_id"),
                number(response, "sequence_no").longValue(),
                text(response, "plan_id"),
                text(response, "hand12_sha256"),
                strings(response, "discard"),
                text(response, "contract"),
                strings(response, "final_hand")
        );
        if (!plan.gameId().equals(request.gameId()) || plan.sequenceNo() != request.sequenceNo()) {
            failAndStop("HOST_PICKUP_PLAN_IDENTITY_MISMATCH");
        }
        if (!ProtocolIdentity.sha256Json(request.hand12()).equals(plan.hand12Sha256())) {
            failAndStop("HOST_PICKUP_PLAN_HAND_HASH_MISMATCH");
        }
        if (!request.legalContracts().contains(plan.contract())) {
            failAndStop("HOST_PICKUP_PLAN_CONTRACT_OUTSIDE_LEGAL_SET");
        }
        if (plan.discard().size() != 2
                || new java.util.HashSet<>(plan.discard()).size() != 2
                || !request.hand12().containsAll(plan.discard())) {
            failAndStop("HOST_PICKUP_PLAN_BAD_DISCARD");
        }
        List<String> expectedFinal = request.hand12().stream()
                .filter(card -> !plan.discard().contains(card))
                .toList();
        if (!expectedFinal.equals(plan.finalHand())) {
            failAndStop("HOST_PICKUP_PLAN_FINAL_HAND_MISMATCH");
        }
        validateRelease(plan.releaseId());
        return plan;
    }

    private Map<String, Object> exchange(Map<String, Object> payload) {
        if (!process.isAlive()) throw new IllegalStateException("HOST_PROCESS_NOT_ALIVE");
        try {
            writer.write(mapper.writeValueAsString(payload));
            writer.newLine();
            writer.flush();
        } catch (IOException exc) {
            failAndStop("HOST_REQUEST_WRITE_FAILED", exc);
        }

        Future<String> future = readerExecutor.submit(reader::readLine);
        final String line;
        try {
            line = future.get(timeout.toMillis(), TimeUnit.MILLISECONDS);
        } catch (TimeoutException exc) {
            future.cancel(true);
            failAndStop("HOST_RESPONSE_TIMEOUT", exc);
            return Map.of();
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            failAndStop("HOST_RESPONSE_INTERRUPTED", exc);
            return Map.of();
        } catch (ExecutionException exc) {
            failAndStop("HOST_RESPONSE_READ_FAILED", exc.getCause());
            return Map.of();
        }
        if (line == null) {
            failAndStop("HOST_RESPONSE_EOF");
        }
        try {
            return mapper.readValue(line, new TypeReference<>() {});
        } catch (IOException exc) {
            failAndStop("HOST_RESPONSE_BAD_JSON", exc);
            return Map.of();
        }
    }

    private void requireSchema(Map<String, Object> response, String expected) {
        if (!expected.equals(response.get("schema"))) failAndStop("HOST_RESPONSE_SCHEMA_MISMATCH");
    }

    private void requireOk(Map<String, Object> response) {
        if (!Boolean.TRUE.equals(response.get("ok"))) {
            String code = String.valueOf(response.getOrDefault("error_code", "UNKNOWN"));
            failAndStop("HOST_REJECTED:" + code);
        }
    }

    private void validateRelease(String releaseId) {
        if (!expectedReleaseId.isBlank() && !expectedReleaseId.equals(releaseId)) {
            failAndStop("HOST_RELEASE_ID_MISMATCH");
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String error) {
        if (!(value instanceof Map<?, ?> map)) throw new IllegalStateException(error);
        return (Map<String, Object>) map;
    }

    private static String text(Map<String, Object> value, String key) {
        Object x = value.get(key);
        if (!(x instanceof String s) || s.isBlank()) throw new IllegalStateException("HOST_MISSING_TEXT:" + key);
        return s;
    }

    private static Number number(Map<String, Object> value, String key) {
        Object x = value.get(key);
        if (!(x instanceof Number n)) throw new IllegalStateException("HOST_MISSING_NUMBER:" + key);
        return n;
    }

    private static List<String> strings(Map<String, Object> value, String key) {
        Object x = value.get(key);
        if (!(x instanceof List<?> list) || list.stream().anyMatch(y -> !(y instanceof String))) {
            throw new IllegalStateException("HOST_MISSING_STRING_LIST:" + key);
        }
        return list.stream().map(String.class::cast).toList();
    }

    private void failAndStop(String message) {
        failAndStop(message, null);
    }

    private void failAndStop(String message, Throwable cause) {
        process.destroyForcibly();
        if (cause == null) throw new IllegalStateException(message);
        throw new IllegalStateException(message, cause);
    }

    @Override
    public synchronized void close() {
        try {
            writer.close();
        } catch (IOException ignored) {
        }
        process.destroy();
        try {
            if (!process.waitFor(500, TimeUnit.MILLISECONDS)) process.destroyForcibly();
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            process.destroyForcibly();
        }
        readerExecutor.shutdownNow();
    }
}
