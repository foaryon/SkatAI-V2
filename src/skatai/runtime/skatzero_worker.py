from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import traceback


def _load_api(root: Path):
    root = root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))

    # Bound per-process parallelism before model construction. Multi-table
    # throughput comes from multiple warm workers; letting every worker fan
    # out across all host CPUs causes severe oversubscription.
    import torch
    torch_threads = int(os.environ.get("SKATZERO_TORCH_THREADS", "1"))
    interop_threads = int(
        os.environ.get("SKATZERO_TORCH_INTEROP_THREADS", "1")
    )
    if torch_threads < 1 or interop_threads < 1:
        raise ValueError("BAD_SKATZERO_TORCH_THREAD_CONFIG")
    torch.set_num_threads(torch_threads)
    torch.set_num_interop_threads(interop_threads)

    import api as skatzero_api  # type: ignore

    # Load the nine frozen cardplay models once. Every request still receives
    # a fresh SkatEnv/raw state so game state and RNG are never shared.
    agents, _, _ = skatzero_api.prepare_env()

    def warm_prepare_env():
        env = skatzero_api.SkatEnv()
        env.set_agents(agents)
        raw_state, _ = env.game.init_game()
        env.game.round.blind_hand = True
        env.game.round.open_hand = False
        raw_state["blind_hand"] = True
        raw_state["open_hand"] = False
        raw_state["points"] = [0, 0]
        raw_state["drueck"] = False
        return agents, env, raw_state

    skatzero_api.prepare_env = warm_prepare_env
    return skatzero_api


def _dispatch(api, args: list[str]) -> list[str]:
    if not args:
        raise ValueError("EMPTY_SKATZERO_ARGS")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        if args[0] in {"BID", "SKAT_OR_HAND_DECL"}:
            api.bid(args, 231, -5)
        elif args[0] == "DISCARD_AND_DECL":
            api.declare(args)
        elif args[0] == "CARDPLAY":
            if len(args) < 2 or args[1] not in {"D", "H", "S", "C", "G", "N"}:
                raise ValueError("BAD_CARDPLAY_MODE")
            api.cardplay(args)
        else:
            raise ValueError(f"UNKNOWN_SKATZERO_COMMAND:{args[0]}")
    return [line.strip() for line in out.getvalue().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    api = _load_api(args.root)

    sys.stdout.write(json.dumps({"ready": True}, separators=(",", ":")) + "\n")
    sys.stdout.flush()

    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            req_id = str(request["id"])
            argv = request["args"]
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                raise ValueError("ARGS_NOT_STRING_LIST")
            lines = _dispatch(api, argv)
            response = {"id": req_id, "ok": True, "lines": lines}
        except BaseException as exc:
            response = {
                "id": str(request.get("id", "")) if isinstance(locals().get("request"), dict) else "",
                "ok": False,
                "error": f"{type(exc).__name__}:{exc}",
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
