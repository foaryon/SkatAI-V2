#!/usr/bin/env bash
set -euo pipefail

V2_ROOT=/workspace/skatai-v2
PY=/tmp/skatai-v2-dev/bin/python
JOURNAL=${ISS_PREFLIGHT_JOURNAL:-/workspace/skatai-v2-runtime/iss/preflight/service.jsonl}
RESULT=${ISS_PREFLIGHT_RESULT:-/workspace/skatai-v2-runtime/iss/preflight/result.json}

mkdir -p "$(dirname "$RESULT")"
mkdir -p "$(dirname "$JOURNAL")"

export PYTHONPATH="$V2_ROOT/src"

"$PY" - "$RESULT" "$JOURNAL" <<'PY'
import json, sys, time
from pathlib import Path
from skatai.iss.client import client_from_environment

out = Path(sys.argv[1])
journal = Path(sys.argv[2])
client, password = client_from_environment(journal_path=journal)
start = time.monotonic()
result = {
    "schema": "skatai.v2.iss-login-preflight.v1",
    "host": client.transport.config.host,
    "port": client.transport.config.port,
    "requested_client_id": client.transport.config.client_id,
    "journal_path": str(journal),
    "scored_game": False,
    "joined_table": False,
}
try:
    actual = client.connect_and_login(password)
    result.update({
        "status": "AUTHENTICATED",
        "authenticated_client_id": actual,
        "elapsed_ms": round((time.monotonic() - start) * 1000.0, 1),
    })
finally:
    client.close()
    password = None

tmp = out.with_suffix(out.suffix + ".tmp")
tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
tmp.replace(out)
print(json.dumps(result, sort_keys=True))
PY
