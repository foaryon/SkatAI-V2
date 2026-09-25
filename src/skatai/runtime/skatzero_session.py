"""Experimental persistent transport for the frozen SkatZero API.

The upstream API and its model files stay unchanged. This module is a bounded
runtime treatment, not the default B0 transport or an accepted release.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import selectors
import subprocess
import sys
import threading


def _serve(root: Path) -> None:
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("skatzero_frozen_api", root / "api.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("SKATZERO_API_IMPORT_FAILED")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            args = request["args"]
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise ValueError("BAD_ARGS")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                if args[0] in {"BID", "SKAT_OR_HAND_DECL"}:
                    api.bid(args, 231, -5)
                elif args[0] == "DISCARD_AND_DECL":
                    api.declare(args)
                elif args[0] == "CARDPLAY":
                    api.cardplay(args)
                else:
                    raise ValueError("UNSUPPORTED_COMMAND")
            response = {"id": request["id"], "lines": out.getvalue().splitlines()}
        except Exception as exc:
            response = {"id": request.get("id") if isinstance(request, dict) else None,
                        "error": type(exc).__name__}
        print(json.dumps(response, separators=(",", ":")), flush=True)


class SkatZeroSessionRunner:
    """One serialized child reuses imports; each upstream call resets game state."""

    def __init__(self, root: Path, python_executable: Path, *, timeout_s: float = 120.0):
        self.root = Path(root)
        self.python_executable = Path(python_executable)
        self.timeout_s = float(timeout_s)
        self._lock = threading.Lock()
        self._sequence = 0
        self._child: subprocess.Popen[str] | None = None

    def _read(self) -> dict:
        assert self._child is not None and self._child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(self._child.stdout, selectors.EVENT_READ)
            if not selector.select(self.timeout_s):
                self.close()
                raise TimeoutError("SKATZERO_SESSION_TIMEOUT")
        line = self._child.stdout.readline()
        if not line:
            self.close()
            raise RuntimeError("SKATZERO_SESSION_CLOSED")
        return json.loads(line)

    def _start(self) -> None:
        if self._child is not None and self._child.poll() is not None:
            self.close()
        if self._child is not None:
            return
        if not (self.root / "api.py").is_file() or not self.python_executable.is_file():
            raise FileNotFoundError("SKATZERO_SESSION_ASSET_MISSING")
        self._child = subprocess.Popen(
            [str(self.python_executable), "-u", str(Path(__file__).resolve()),
             "--server", str(self.root)],
            cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        if self._read() != {"ready": True}:
            self.close()
            raise RuntimeError("SKATZERO_SESSION_NOT_READY")

    def run(self, args: list[str]) -> list[str]:
        with self._lock:
            self._start()
            assert self._child is not None and self._child.stdin is not None
            self._sequence += 1
            try:
                self._child.stdin.write(json.dumps({"id": self._sequence, "args": args}) + "\n")
                self._child.stdin.flush()
            except OSError:
                self.close()
                raise
            response = self._read()
            if response.get("id") != self._sequence:
                self.close()
                raise RuntimeError("SKATZERO_SESSION_RESPONSE_ID_MISMATCH")
            if "error" in response:
                raise RuntimeError("SKATZERO_SESSION_API_ERROR:" + response["error"])
            lines = response.get("lines")
            if not isinstance(lines, list) or not lines or not all(isinstance(x, str) for x in lines):
                raise RuntimeError("SKATZERO_SESSION_EMPTY_OUTPUT")
            return [x.strip() for x in lines if x.strip()]

    def close(self) -> None:
        child, self._child = self._child, None
        if child is None:
            return
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()

    def __enter__(self) -> "SkatZeroSessionRunner":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--server":
        raise SystemExit(2)
    _serve(Path(sys.argv[2]).resolve())
