package org.skatai.v2.jskat;

import org.jskat.data.GameContract;
import org.jskat.util.GameType;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

final class ContractMapperTest {
    private static final List<String> HAND10 = List.of(
            "C9", "CJ", "D7", "DA", "DJ", "DK", "DQ", "HA", "HK", "HT"
    );

    @Test
    void allReviewedContractTokensInstantiateWithExpectedModifiers() {
        for (String token : ContractMapper.PICKUP_CONTRACTS) {
            GameContract c = ContractMapper.toGameContract(token, HAND10);
            assertEquals(token, ContractMapper.toToken(c), token);
            assertFalse(c.hand(), token);
            assertEquals(token.equals("NO"), c.ouvert(), token);
            assertFalse(c.schneider(), token);
            assertFalse(c.schwarz(), token);
            assertEquals(token.equals("NO") ? 10 : 0, c.ouvertCards().size(), token);
        }

        for (String token : ContractMapper.INITIAL_DECLARATION_ACTIONS) {
            if (token.equals("PICKUP")) continue;
            GameContract c = ContractMapper.toGameContract(token, HAND10);
            assertEquals(token, ContractMapper.toToken(c), token);
            assertTrue(c.hand(), token);
            boolean nullGame = token.startsWith("N");
            assertEquals(nullGame ? GameType.NULL : gameType(token.charAt(0)), c.gameType(), token);
            assertEquals(token.endsWith("O"), c.ouvert(), token);
            if (nullGame) {
                assertFalse(c.schneider(), token);
                assertFalse(c.schwarz(), token);
            } else {
                assertEquals(token.length() >= 3, c.schneider(), token);
                assertEquals(token.endsWith("Z") || token.endsWith("O"), c.schwarz(), token);
            }
            assertEquals(token.endsWith("O") ? 10 : 0, c.ouvertCards().size(), token);
        }
    }

    @Test
    void rejectsPickupAndUnknownTokens() {
        assertThrows(IllegalArgumentException.class,
                () -> ContractMapper.toGameContract("PICKUP", HAND10));
        assertThrows(IllegalArgumentException.class,
                () -> ContractMapper.toGameContract("X", HAND10));
        assertThrows(IllegalArgumentException.class,
                () -> ContractMapper.toGameContract("NS", HAND10));
    }

    private static GameType gameType(char token) {
        return switch (token) {
            case 'C' -> GameType.CLUBS;
            case 'S' -> GameType.SPADES;
            case 'H' -> GameType.HEARTS;
            case 'D' -> GameType.DIAMONDS;
            case 'G' -> GameType.GRAND;
            default -> throw new AssertionError(token);
        };
    }
}
