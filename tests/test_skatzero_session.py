from pathlib import Path
import sys

import pytest

from skatai.runtime.skatzero_session import SkatZeroSessionRunner


def test_session_reuses_import_and_recovers_after_child_loss(tmp_path: Path):
    (tmp_path / "api.py").write_text(
        "calls = 0\n"
        "def cardplay(args):\n"
        "    global calls\n"
        "    calls += 1\n"
        "    print(args[1], calls)\n"
        "def bid(args, accuracy, threshold):\n"
        "    print(accuracy, threshold)\n"
        "def declare(args):\n"
        "    print(args[1])\n"
    )
    with SkatZeroSessionRunner(tmp_path, Path(sys.executable), timeout_s=5) as runner:
        assert runner.run(["CARDPLAY", "DJ"]) == ["DJ 1"]
        assert runner.run(["CARDPLAY", "CJ"]) == ["CJ 2"]
        assert runner.run(["BID"]) == ["231 -5"]
        assert runner.run(["DISCARD_AND_DECL", "D7"]) == ["D7"]
        with pytest.raises(RuntimeError, match="SKATZERO_SESSION_API_ERROR:ValueError"):
            runner.run(["UNKNOWN"])
        runner._child.kill()
        runner._child.wait()
        assert runner.run(["CARDPLAY", "SA"]) == ["SA 1"]


def test_session_timeout_terminates_child_before_retry(tmp_path: Path):
    (tmp_path / "api.py").write_text(
        "import time\n"
        "def cardplay(args):\n"
        "    if args[1] == 'SLOW': time.sleep(1)\n"
        "    print(args[1])\n"
    )
    with SkatZeroSessionRunner(tmp_path, Path(sys.executable), timeout_s=0.2) as runner:
        with pytest.raises(TimeoutError, match="SKATZERO_SESSION_TIMEOUT"):
            runner.run(["CARDPLAY", "SLOW"])
        assert runner.run(["CARDPLAY", "FAST"]) == ["FAST"]
