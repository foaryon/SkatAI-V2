#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

CONTROL_ROOT = Path("/var/lib/skatai-main-controller")
INBOX = CONTROL_ROOT / "inbox"


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("MAIN_USER_INPUT_REQUIRES_ROOT")
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path)
    p.add_argument("--label", default="operator")
    args = p.parse_args()
    data = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
    if not data.strip():
        raise SystemExit("EMPTY_MAIN_USER_INPUT")
    INBOX.mkdir(parents=True, exist_ok=True)
    os.chmod(INBOX, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in args.label)[:48]
    path = INBOX / f"{stamp}-{safe}.msg"
    tmp = path.with_suffix(".msg.tmp")
    tmp.write_text(data.rstrip() + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
