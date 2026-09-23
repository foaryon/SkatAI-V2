from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pyarrow.parquet as pq
import torch

from skatai.gameplay.bidding import NeuralBiddingPolicy
from skatai.models.bidding import dense_features

SCHEMA = "skatai.v2.bidding-stratified-diagnostic.v1"
COLUMNS = (
    "game_identity",
    "hand_mask",
    "actor",
    "bidder",
    "answerer",
    "bid_index",
    "decision_role",
    "current_offer",
    "target_continue",
    "game_type",
)


class Accumulator:
    __slots__ = ("n", "sum_p", "sum_t", "sum_brier", "correct")

    def __init__(self) -> None:
        self.n = 0
        self.sum_p = 0.0
        self.sum_t = 0.0
        self.sum_brier = 0.0
        self.correct = 0

    def update(self, p: np.ndarray, t: np.ndarray) -> None:
        if p.size == 0:
            return
        p = p.astype(np.float64, copy=False)
        t = t.astype(np.float64, copy=False)
        self.n += int(p.size)
        self.sum_p += float(p.sum())
        self.sum_t += float(t.sum())
        self.sum_brier += float(np.square(p - t).sum())
        self.correct += int(((p >= 0.5) == (t >= 0.5)).sum())

    def result(self) -> dict[str, float | int | None]:
        if self.n == 0:
            return {
                "rows": 0,
                "mean_predicted_continue": None,
                "observed_continue_rate": None,
                "calibration_gap_predicted_minus_observed": None,
                "brier": None,
                "accuracy_at_0_5": None,
            }
        mean_p = self.sum_p / self.n
        mean_t = self.sum_t / self.n
        return {
            "rows": self.n,
            "mean_predicted_continue": mean_p,
            "observed_continue_rate": mean_t,
            "calibration_gap_predicted_minus_observed": mean_p - mean_t,
            "brier": self.sum_brier / self.n,
            "accuracy_at_0_5": self.correct / self.n,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=8 * 1024 * 1024) as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _sample_mask(
    identities: list[bytes],
    *,
    numerator: int,
    denominator: int,
) -> np.ndarray:
    if numerator <= 0 or denominator <= 0 or numerator > denominator:
        raise ValueError("BAD_SAMPLE_FRACTION")
    if numerator == denominator:
        return np.ones(len(identities), dtype=bool)
    # semantic game identities are SHA-256 bytes. Sampling on the first 32 bits
    # keeps all decision rows from the same game together.
    cutoff = (1 << 32) * numerator // denominator
    return np.fromiter(
        (int.from_bytes(x[:4], "big") < cutoff for x in identities),
        dtype=bool,
        count=len(identities),
    )


def _band(offers: np.ndarray) -> np.ndarray:
    out = np.empty(offers.size, dtype=object)
    out[offers <= 24] = "18-24"
    out[(offers >= 27) & (offers <= 48)] = "27-48"
    out[offers >= 50] = "50+"
    return out


def _update_groups(
    groups: dict[str, dict[str, Accumulator]],
    axis: str,
    values: np.ndarray,
    p: np.ndarray,
    t: np.ndarray,
) -> None:
    for value in np.unique(values):
        mask = values == value
        groups[axis][str(value)].update(p[mask], t[mask])


@torch.no_grad()
def scan_split(
    policy: NeuralBiddingPolicy,
    split_dir: Path,
    *,
    sample_numerator: int,
    sample_denominator: int,
    batch_size: int = 65536,
) -> dict[str, Any]:
    files = sorted(split_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"NO_PARQUET_FILES:{split_dir}")

    groups: dict[str, dict[str, Accumulator]] = defaultdict(
        lambda: defaultdict(Accumulator)
    )
    overall = Accumulator()
    rows_scanned = 0
    rows_selected = 0
    selected_identity_prefixes: set[bytes] = set()

    for path in files:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=batch_size, columns=list(COLUMNS)):
            d = batch.to_pydict()
            identities = d["game_identity"]
            rows_scanned += len(identities)
            mask = _sample_mask(
                identities,
                numerator=sample_numerator,
                denominator=sample_denominator,
            )
            if not mask.any():
                continue
            idx = np.flatnonzero(mask)
            rows_selected += int(idx.size)
            # Count unique selected games exactly within the scanned split.
            selected_identity_prefixes.update(identities[i] for i in idx)

            def arr(name: str, dtype=np.int64) -> np.ndarray:
                return np.asarray(d[name], dtype=dtype)[idx]

            hand_mask = arr("hand_mask")
            actor = arr("actor")
            bidder = arr("bidder")
            answerer = arr("answerer")
            bid_index = arr("bid_index")
            role = arr("decision_role")
            offer = arr("current_offer")
            target = arr("target_continue", dtype=np.float32)
            game_type = np.asarray(d["game_type"], dtype=object)[idx]

            dev = policy.device
            x = dense_features(
                torch.as_tensor(hand_mask, dtype=torch.int64, device=dev),
                torch.as_tensor(actor, dtype=torch.int64, device=dev),
                torch.as_tensor(bidder, dtype=torch.int64, device=dev),
                torch.as_tensor(answerer, dtype=torch.int64, device=dev),
                torch.as_tensor(bid_index, dtype=torch.int64, device=dev),
                torch.as_tensor(role, dtype=torch.int64, device=dev),
            )
            probs = torch.sigmoid(policy.model(x)).cpu().numpy().astype(np.float64)
            target64 = target.astype(np.float64)

            overall.update(probs, target64)
            _update_groups(groups, "actor", actor, probs, target64)
            _update_groups(groups, "decision_role", role, probs, target64)
            _update_groups(groups, "current_offer", offer, probs, target64)
            _update_groups(groups, "bid_band", _band(offer), probs, target64)
            _update_groups(groups, "game_type", game_type, probs, target64)
            actor_role = np.asarray(
                [f"{a}:{'BIDDER' if r == 0 else 'ANSWERER'}" for a, r in zip(actor, role)],
                dtype=object,
            )
            _update_groups(groups, "actor_role", actor_role, probs, target64)

    role_names = {"0": "BIDDER", "1": "ANSWERER"}
    rendered = {
        axis: {
            (role_names.get(key, key) if axis == "decision_role" else key): acc.result()
            for key, acc in sorted(values.items())
        }
        for axis, values in sorted(groups.items())
    }
    return {
        "files": len(files),
        "rows_scanned": rows_scanned,
        "rows_selected": rows_selected,
        "selected_unique_games": len(selected_identity_prefixes),
        "sample": {
            "numerator": sample_numerator,
            "denominator": sample_denominator,
            "method": "semantic_game_identity_sha256_first_32_bits_threshold",
            "game_cluster_preserving": True,
        },
        "overall": overall.result(),
        "groups": rendered,
    }


def domain_drift(
    test: Mapping[str, Any],
    external: Mapping[str, Any],
    *,
    min_rows_each: int = 5000,
) -> dict[str, Any]:
    rows = []
    axes = sorted(set(test["groups"]) & set(external["groups"]))
    for axis in axes:
        keys = sorted(
            set(test["groups"][axis]) & set(external["groups"][axis])
        )
        for key in keys:
            a = test["groups"][axis][key]
            b = external["groups"][axis][key]
            if int(a["rows"]) < min_rows_each or int(b["rows"]) < min_rows_each:
                continue
            rows.append(
                {
                    "axis": axis,
                    "key": key,
                    "test_rows": int(a["rows"]),
                    "external_rows": int(b["rows"]),
                    "test_calibration_gap": float(
                        a["calibration_gap_predicted_minus_observed"]
                    ),
                    "external_calibration_gap": float(
                        b["calibration_gap_predicted_minus_observed"]
                    ),
                    "calibration_gap_shift_external_minus_test": float(
                        b["calibration_gap_predicted_minus_observed"]
                        - a["calibration_gap_predicted_minus_observed"]
                    ),
                    "predicted_continue_shift_external_minus_test": float(
                        b["mean_predicted_continue"] - a["mean_predicted_continue"]
                    ),
                    "observed_continue_shift_external_minus_test": float(
                        b["observed_continue_rate"] - a["observed_continue_rate"]
                    ),
                }
            )
    rows.sort(
        key=lambda x: abs(x["calibration_gap_shift_external_minus_test"]),
        reverse=True,
    )
    return {
        "min_rows_each": min_rows_each,
        "shared_strata": len(rows),
        "largest_absolute_calibration_gap_shifts": rows[:30],
    }


def analyze(
    dataset_root: Path,
    model_path: Path,
    *,
    external_sample_numerator: int = 1,
    external_sample_denominator: int = 10,
    device: str = "cpu",
) -> dict[str, Any]:
    policy = NeuralBiddingPolicy.load(model_path, device=device)
    test = scan_split(
        policy,
        dataset_root / "test",
        sample_numerator=1,
        sample_denominator=1,
    )
    external = scan_split(
        policy,
        dataset_root / "external_bot_holdout",
        sample_numerator=external_sample_numerator,
        sample_denominator=external_sample_denominator,
    )
    return {
        "schema": SCHEMA,
        "model": {
            "path": str(model_path),
            "sha256": sha256_file(model_path),
        },
        "dataset_manifest": {
            "path": str(dataset_root / "manifest.json"),
            "sha256": sha256_file(dataset_root / "manifest.json"),
        },
        "domains": {
            "test": test,
            "external_bot_holdout_sample": external,
        },
        "drift": domain_drift(test, external),
        "interpretation_constraints": [
            "Offline imitation and calibration are diagnostic only.",
            "External-bot holdout remains excluded from training and hyperparameter fitting.",
            "The external-bot result is a deterministic game-level sample unless numerator equals denominator.",
            "No offline stratum may ACCEPT or REJECT B1 playing strength.",
            "This analysis must not alter the frozen ISS external-gate decision rule.",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset_root", type=Path)
    p.add_argument("model", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--external-sample-numerator", type=int, default=1)
    p.add_argument("--external-sample-denominator", type=int, default=10)
    p.add_argument("--expected-model-sha256")
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--device", default="cpu")
    args = p.parse_args()

    if args.expected_model_sha256:
        actual = sha256_file(args.model)
        if actual != args.expected_model_sha256:
            raise ValueError(
                f"MODEL_SHA256_MISMATCH:{actual}!={args.expected_model_sha256}"
            )
    manifest = args.dataset_root / "manifest.json"
    if args.expected_manifest_sha256:
        actual = sha256_file(manifest)
        if actual != args.expected_manifest_sha256:
            raise ValueError(
                f"MANIFEST_SHA256_MISMATCH:{actual}!={args.expected_manifest_sha256}"
            )

    result = analyze(
        args.dataset_root,
        args.model,
        external_sample_numerator=args.external_sample_numerator,
        external_sample_denominator=args.external_sample_denominator,
        device=args.device,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(args.output)
    print(
        json.dumps(
            {
                "schema": result["schema"],
                "test": result["domains"]["test"]["overall"],
                "external_sample": result["domains"][
                    "external_bot_holdout_sample"
                ]["overall"],
                "external_rows_selected": result["domains"][
                    "external_bot_holdout_sample"
                ]["rows_selected"],
                "top_drift": result["drift"][
                    "largest_absolute_calibration_gap_shifts"
                ][:10],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
