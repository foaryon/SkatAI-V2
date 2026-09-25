from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import pyarrow.parquet as pq
import torch
from torch.nn import functional as F

from skatai.data.bidding_features import FEATURE_SCHEMA
from skatai.data.bidding_parquet import DATASET_SCHEMA, SPLITS
from skatai.models.bidding import BiddingMLP, BiddingModelConfig, dense_features

TRAINING_SCHEMA = "skatai.v2.bidding-training.v1"
COLUMNS = [
    "hand_mask",
    "actor",
    "bidder",
    "answerer",
    "bid_index",
    "decision_role",
    "target_continue",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=8 * 1024 * 1024) as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def dataset_manifest_identity(dataset_root: Path) -> str:
    manifest = dataset_root / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"MISSING_DATASET_MANIFEST:{manifest}")
    return sha256_file(manifest)


def verified_split_shards(
    dataset_root: Path, split: str, *, expected_manifest_sha256: str | None = None,
) -> list[Path]:
    """Bind a read split to the manifest's exact shard set and content."""
    if split not in SPLITS:
        raise ValueError(f"UNKNOWN_DATASET_SPLIT:{split}")
    manifest_path = dataset_root / "manifest.json"
    raw = manifest_path.read_bytes()
    if (expected_manifest_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != expected_manifest_sha256):
        raise ValueError("DATASET_MANIFEST_CHANGED_DURING_PREFLIGHT")
    manifest = json.loads(raw)
    if (manifest.get("dataset_schema") != DATASET_SCHEMA
            or manifest.get("feature_schema") != FEATURE_SCHEMA
            or not isinstance(manifest.get("shards"), list)):
        raise ValueError("DATASET_MANIFEST_SCHEMA_INVALID")
    entries = [entry for entry in manifest["shards"] if entry.get("split") == split]
    if not entries:
        raise FileNotFoundError(f"NO_PARQUET_SHARDS:{dataset_root / split}")
    listed: dict[Path, dict] = {}
    for entry in entries:
        relative = Path(entry["path"])
        if (len(relative.parts) != 2 or relative.parts[0] != split
                or relative.suffix != ".parquet" or relative in listed):
            raise ValueError("DATASET_SHARD_PATH_INVALID_OR_DUPLICATE")
        listed[relative] = entry
    physical = {path.relative_to(dataset_root) for path in (dataset_root / split).glob("*.parquet")}
    if physical != set(listed):
        raise ValueError("DATASET_SHARD_SET_MISMATCH")
    files = []
    for relative, entry in sorted(listed.items()):
        path = dataset_root / relative
        if path.stat().st_size != entry["bytes"]:
            raise ValueError(f"DATASET_SHARD_SIZE_MISMATCH:{relative}")
        if sha256_file(path) != entry["sha256"]:
            raise ValueError(f"DATASET_SHARD_HASH_MISMATCH:{relative}")
        files.append(path)
    return files


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def parquet_batches(
    dataset_root: Path,
    split: str,
    *,
    batch_size: int,
    seed: int,
    shuffle_files: bool,
    verified_files: Sequence[Path] | None = None,
) -> Iterator[dict[str, torch.Tensor]]:
    files = list(verified_files) if verified_files is not None else verified_split_shards(dataset_root, split)
    if shuffle_files:
        rng = random.Random(seed)
        rng.shuffle(files)
    for path in files:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=batch_size, columns=COLUMNS):
            d = batch.to_pydict()
            yield {
                k: torch.as_tensor(d[k], dtype=(
                    torch.float32 if k == "target_continue" else torch.int64
                ))
                for k in COLUMNS
            }


def prepare_batch(batch: dict[str, torch.Tensor], device: torch.device):
    x = dense_features(
        batch["hand_mask"].to(device),
        batch["actor"].to(device),
        batch["bidder"].to(device),
        batch["answerer"].to(device),
        batch["bid_index"].to(device),
        batch["decision_role"].to(device),
    )
    y = batch["target_continue"].to(device)
    return x, y


@torch.no_grad()
def evaluate(
    model: BiddingMLP,
    dataset_root: Path,
    split: str,
    *,
    batch_size: int = 16384,
    device: str = "cpu",
    max_batches: int | None = None,
    ece_bins: int = 20,
) -> dict:
    dev = torch.device(device)
    model.eval()
    total = correct = 0
    loss_sum = brier_sum = prob_sum = target_sum = 0.0
    bin_count = np.zeros(ece_bins, dtype=np.int64)
    bin_prob = np.zeros(ece_bins, dtype=np.float64)
    bin_target = np.zeros(ece_bins, dtype=np.float64)

    for batch_i, batch in enumerate(
        parquet_batches(dataset_root, split, batch_size=batch_size, seed=0, shuffle_files=False)
    ):
        if max_batches is not None and batch_i >= max_batches:
            break
        x, y = prepare_batch(batch, dev)
        logits = model(x)
        probs = torch.sigmoid(logits)
        loss_sum += F.binary_cross_entropy_with_logits(logits, y, reduction="sum").item()
        brier_sum += torch.sum((probs - y) ** 2).item()
        correct += torch.sum((probs >= 0.5) == (y >= 0.5)).item()
        n = y.numel()
        total += n
        prob_sum += probs.sum().item()
        target_sum += y.sum().item()

        p = probs.detach().cpu().numpy()
        t = y.detach().cpu().numpy()
        bins = np.minimum((p * ece_bins).astype(np.int64), ece_bins - 1)
        for b in range(ece_bins):
            m = bins == b
            if m.any():
                bin_count[b] += int(m.sum())
                bin_prob[b] += float(p[m].sum())
                bin_target[b] += float(t[m].sum())

    if total == 0:
        raise ValueError("EMPTY_EVALUATION")

    ece = 0.0
    for b in range(ece_bins):
        if bin_count[b]:
            confidence = bin_prob[b] / bin_count[b]
            frequency = bin_target[b] / bin_count[b]
            ece += (bin_count[b] / total) * abs(confidence - frequency)

    return {
        "split": split,
        "rows": total,
        "log_loss": loss_sum / total,
        "brier": brier_sum / total,
        "accuracy_at_0_5": correct / total,
        "mean_predicted_continue": prob_sum / total,
        "observed_continue_rate": target_sum / total,
        "ece_20": ece,
    }


def train(
    dataset_root: Path,
    output_dir: Path,
    *,
    config: BiddingModelConfig = BiddingModelConfig(),
    epochs: int = 1,
    batch_size: int = 8192,
    learning_rate: float = 3e-4,
    weight_decay: float = 1e-4,
    seed: int = 20260923,
    device: str = "cpu",
    max_batches: int | None = None,
    resume_from: Path | None = None,
) -> dict:
    dataset_id = dataset_manifest_identity(dataset_root)
    train_files = verified_split_shards(
        dataset_root, "train", expected_manifest_sha256=dataset_id,
    )
    seed_everything(seed)
    dev = torch.device(device)
    model = BiddingMLP(config).to(dev)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    steps = 0
    start_epoch = 0
    epoch_metrics = []

    expected_hparams = {
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "max_batches": max_batches,
    }

    if resume_from is not None:
        checkpoint = torch.load(resume_from, map_location=dev)
        if checkpoint.get("training_schema") != TRAINING_SCHEMA:
            raise ValueError("RESUME_TRAINING_SCHEMA_MISMATCH")
        if checkpoint.get("feature_schema") != FEATURE_SCHEMA:
            raise ValueError("RESUME_FEATURE_SCHEMA_MISMATCH")
        if checkpoint.get("dataset_manifest_sha256") != dataset_id:
            raise ValueError("RESUME_DATASET_IDENTITY_MISMATCH")
        if checkpoint.get("model") != model.artifact_config():
            raise ValueError("RESUME_MODEL_CONFIG_MISMATCH")
        if checkpoint.get("seed") != seed:
            raise ValueError("RESUME_SEED_MISMATCH")
        if checkpoint.get("hyperparameters") != expected_hparams:
            raise ValueError("RESUME_HYPERPARAMETER_MISMATCH")

        model.load_state_dict(checkpoint["state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        start_epoch = int(checkpoint["epoch"])
        steps = int(checkpoint["steps"])
        epoch_metrics = list(checkpoint.get("epoch_metrics", []))

        torch.set_rng_state(checkpoint["torch_rng_state"])
        np.random.set_state(checkpoint["numpy_random_state"])
        random.setstate(checkpoint["python_random_state"])

        if start_epoch >= epochs:
            raise ValueError(f"RESUME_ALREADY_AT_OR_BEYOND_TARGET:{start_epoch}>={epochs}")

    for epoch in range(start_epoch, epochs):
        model.train()
        rows = 0
        loss_sum = 0.0
        for batch_i, batch in enumerate(
            parquet_batches(
                dataset_root,
                "train",
                batch_size=batch_size,
                seed=seed + epoch,
                shuffle_files=True,
                verified_files=train_files,
            )
        ):
            if max_batches is not None and batch_i >= max_batches:
                break
            x, y = prepare_batch(batch, dev)
            optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = F.binary_cross_entropy_with_logits(logits, y)
            loss.backward()
            optimizer.step()
            n = y.numel()
            rows += n
            loss_sum += loss.item() * n
            steps += 1

        epoch_metrics.append(
            {"epoch": epoch + 1, "rows": rows, "train_log_loss": loss_sum / max(rows, 1)}
        )
        ckpt = {
            "training_schema": TRAINING_SCHEMA,
            "feature_schema": FEATURE_SCHEMA,
            "model": model.artifact_config(),
            "state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "epoch": epoch + 1,
            "steps": steps,
            "seed": seed,
            "dataset_manifest_sha256": dataset_id,
            "hyperparameters": expected_hparams,
            "epoch_metrics": list(epoch_metrics),
            "torch_rng_state": torch.get_rng_state(),
            "numpy_random_state": np.random.get_state(),
            "python_random_state": random.getstate(),
        }
        torch.save(ckpt, output_dir / f"checkpoint-epoch-{epoch + 1:03d}.pt")

    final_path = output_dir / "model.pt"
    torch.save(
        {
            "training_schema": TRAINING_SCHEMA,
            "feature_schema": FEATURE_SCHEMA,
            "model": model.artifact_config(),
            "state_dict": model.state_dict(),
            "seed": seed,
            "dataset_manifest_sha256": dataset_id,
        },
        final_path,
    )

    result = {
        "training_schema": TRAINING_SCHEMA,
        "dataset_root": str(dataset_root),
        "dataset_manifest_sha256": dataset_id,
        "model": model.artifact_config(),
        "epochs": epochs,
        "seed": seed,
        "steps": steps,
        "resumed_from": str(resume_from) if resume_from is not None else None,
        "epoch_metrics": epoch_metrics,
        "model_sha256": sha256_file(final_path),
    }
    (output_dir / "training-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset_root", type=Path)
    p.add_argument("output_dir", type=Path)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--batch-size", type=int, default=8192)
    p.add_argument("--learning-rate", type=float, default=3e-4)
    p.add_argument("--weight-decay", type=float, default=1e-4)
    p.add_argument("--seed", type=int, default=20260923)
    p.add_argument("--device", default="cpu")
    p.add_argument("--hidden-dim", type=int, default=192)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--dropout", type=float, default=0.05)
    p.add_argument("--max-batches", type=int)
    p.add_argument("--resume-from", type=Path)
    args = p.parse_args()
    config = BiddingModelConfig(
        hidden_dim=args.hidden_dim, depth=args.depth, dropout=args.dropout
    )
    result = train(
        args.dataset_root,
        args.output_dir,
        config=config,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        device=args.device,
        max_batches=args.max_batches,
        resume_from=args.resume_from,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
