package org.skatai.v2.jskat;

import org.jskat.data.GameContract;
import org.jskat.util.Card;
import org.jskat.util.CardList;
import org.jskat.util.GameType;

import java.util.List;
import java.util.Map;

public final class ContractMapper {
    private static final Map<Character, GameType> GAME_TYPES = Map.of(
            'C', GameType.CLUBS,
            'S', GameType.SPADES,
            'H', GameType.HEARTS,
            'D', GameType.DIAMONDS,
            'G', GameType.GRAND,
            'N', GameType.NULL
    );

    public static final List<String> INITIAL_DECLARATION_ACTIONS = List.of(
            "PICKUP",
            "CH", "CHS", "CHZ", "CHO",
            "SH", "SHS", "SHZ", "SHO",
            "HH", "HHS", "HHZ", "HHO",
            "DH", "DHS", "DHZ", "DHO",
            "GH", "GHS", "GHZ", "GHO",
            "NH", "NHO"
    );

    public static final List<String> PICKUP_CONTRACTS =
            List.of("C", "S", "H", "D", "G", "N", "NO");

    private ContractMapper() {
    }

    public static GameContract toGameContract(String token, List<String> finalHand) {
        if (token == null || token.isBlank() || token.equals("PICKUP")) {
            throw new IllegalArgumentException("NOT_A_GAME_CONTRACT:" + token);
        }
        char base = token.charAt(0);
        GameType gameType = GAME_TYPES.get(base);
        if (gameType == null) throw new IllegalArgumentException("BAD_CONTRACT:" + token);

        CardList hand = new CardList(finalHand.stream().map(Card::valueOf).toList());

        if (token.length() == 1) {
            return new GameContract(gameType);
        }
        if (token.equals("NO")) {
            return new GameContract(GameType.NULL, false, false, false, true, hand);
        }
        if (token.equals("NH")) {
            return new GameContract(GameType.NULL, true, false, false, false, new CardList());
        }
        if (token.equals("NHO")) {
            return new GameContract(GameType.NULL, true, false, false, true, hand);
        }

        String suffix = token.substring(1);
        if (!List.of("H", "HS", "HZ", "HO").contains(suffix)
                || gameType == GameType.NULL) {
            throw new IllegalArgumentException("BAD_CONTRACT:" + token);
        }
        boolean schneider = suffix.length() >= 2;
        boolean schwarz = suffix.equals("HZ") || suffix.equals("HO");
        boolean ouvert = suffix.equals("HO");
        return new GameContract(
                gameType,
                true,
                schneider,
                schwarz,
                ouvert,
                ouvert ? hand : new CardList()
        );
    }

    public static String toToken(GameContract contract) {
        GameType type = contract.gameType();
        String base = switch (type) {
            case CLUBS -> "C";
            case SPADES -> "S";
            case HEARTS -> "H";
            case DIAMONDS -> "D";
            case GRAND -> "G";
            case NULL -> "N";
            default -> throw new IllegalArgumentException("UNSUPPORTED_GAME_TYPE:" + type);
        };

        if (type == GameType.NULL) {
            if (contract.schneider() || contract.schwarz()) {
                throw new IllegalArgumentException("BAD_NULL_MODIFIERS");
            }
            if (contract.hand() && contract.ouvert()) return "NHO";
            if (contract.hand()) return "NH";
            if (contract.ouvert()) return "NO";
            return "N";
        }

        if (!contract.hand()) {
            if (contract.schneider() || contract.schwarz() || contract.ouvert()) {
                throw new IllegalArgumentException("BAD_PICKUP_MODIFIERS");
            }
            return base;
        }
        if (contract.ouvert()) return base + "HO";
        if (contract.schwarz()) return base + "HZ";
        if (contract.schneider()) return base + "HS";
        return base + "H";
    }
}
