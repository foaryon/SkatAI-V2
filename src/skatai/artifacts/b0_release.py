"""Build a staged B0 release from frozen V2-authoritative inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from skatai.artifacts.release import (
    ReleaseComponent,
    ReleasePackageError,
    build_release_package,
    sha256_file,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def build_b0_release(
    *,
    repo: Path,
    upstream_source: Path,
    pretrained_dir: Path,
    output: Path,
) -> dict:
    repo = Path(repo)
    upstream_source = Path(upstream_source)
    pretrained_dir = Path(pretrained_dir)
    output = Path(output)
    provenance = repo / "provenance"
    baseline_path = provenance / "B0_SKATZERO_BASELINE.json"
    reproduction_path = provenance / "B0_REPRODUCTION.json"
    benchmark_path = provenance / "B0_CARDPLAY_BENCHMARK.json"
    repeatability_path = provenance / "B0_BIDDING_REPEATABILITY.json"
    environment_path = provenance / "B0_ENVIRONMENT.lock.txt"

    baseline = json.loads(baseline_path.read_text())
    reproduction = json.loads(reproduction_path.read_text())
    benchmark = json.loads(benchmark_path.read_text())
    repeatability = json.loads(repeatability_path.read_text())
    if baseline["baseline_id"] != "V2-B0" or baseline["status"] != "FROZEN_PUBLISHED_RUNTIME_REPRODUCED":
        raise ReleasePackageError("B0_BASELINE_NOT_FROZEN")
    if reproduction["baseline_id"] != "V2-B0" or benchmark["baseline_id"] != "V2-B0":
        raise ReleasePackageError("B0_EVIDENCE_IDENTITY_MISMATCH")
    if reproduction["status"] != "B0_RUNTIME_AND_DETERMINISTIC_CARDPLAY_BENCHMARK_REPRODUCED":
        raise ReleasePackageError("B0_REPRODUCTION_NOT_VERIFIED")
    if benchmark["status"] != "VERIFIED_DETERMINISTIC_BASELINE_BENCHMARK":
        raise ReleasePackageError("B0_BENCHMARK_NOT_VERIFIED")
    if sha256_file(upstream_source) != baseline["git_archive_sha256"]:
        raise ReleasePackageError("B0_SOURCE_HASH_MISMATCH")
    models = baseline["pretrained_models"]
    for name, expected in models.items():
        if sha256_file(pretrained_dir / name) != expected:
            raise ReleasePackageError(f"B0_MODEL_HASH_MISMATCH:{name}")

    commit = _git(repo, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise ReleasePackageError("BAD_V2_SOURCE_COMMIT")

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="b0-release-build-", dir=output.parent) as scratch:
        v2_source = Path(scratch) / "v2-source.tar"
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", "HEAD", "-o", str(v2_source)],
            check=True,
        )
        metadata = {
            "release_id": "V2-B0-package-v1",
            "release_status": "BASELINE_PACKAGE_STAGED",
            "source_commit": commit,
            "parent_lineage": {
                "kind": "ORIGINAL_BASELINE",
                "upstream_repository": baseline["upstream_repository"],
                "upstream_commit": baseline["upstream_commit"],
            },
            "model_hashes": models,
            "bidding_identity": {
                "implementation": "SkatZero.api.BID",
                "accuracy": repeatability["first"]["accuracy"],
                "bid_threshold": repeatability["first"]["bid_threshold"],
                "repeatability_sha256": sha256_file(repeatability_path),
            },
            "cardplay_identity": {
                "inference_api_sha256": baseline["implementation_hashes"]["inference_api_py"],
                "pretrained_models": models,
            },
            "belief_value_identity": None,
            "search_config": None,
            "rule_engine_identity": {
                "v2_source_commit": commit,
                "upstream_source_sha256": baseline["git_archive_sha256"],
            },
            "runtime_version": reproduction["runtime"],
            "deployment_config": {
                "device": "cpu",
                "backend": "skatai.runtime.skatzero_backend",
                "host_interface": "skatai.runtime.interface.SkatAI",
            },
            "acceptance_evidence": [
                {"kind": "baseline_reproduction", "sha256": sha256_file(reproduction_path)},
                {"kind": "deterministic_cardplay_benchmark", "sha256": sha256_file(benchmark_path)},
            ],
            "benchmark_identity": benchmark["result"]["identity_sha256"],
            "build_inputs": {
                "v2_source_archive_sha256": sha256_file(v2_source),
                "upstream_source_archive_sha256": baseline["git_archive_sha256"],
            },
        }
        components = [
            ReleaseComponent(v2_source, "source/v2-source.tar", "source"),
            ReleaseComponent(upstream_source, "source/skatzero-source.tar", "source"),
        ]
        components.extend(
            ReleaseComponent(pretrained_dir / name, f"models/{name}", "model")
            for name in sorted(models)
        )
        components.extend(
            ReleaseComponent(path, f"provenance/{path.name}", "provenance")
            for path in (
                baseline_path,
                reproduction_path,
                benchmark_path,
                repeatability_path,
                environment_path,
            )
        )
        return build_release_package(output, release=metadata, components=components)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--upstream-source", type=Path, required=True)
    parser.add_argument("--pretrained-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_b0_release(
        repo=args.repo,
        upstream_source=args.upstream_source,
        pretrained_dir=args.pretrained_dir,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
