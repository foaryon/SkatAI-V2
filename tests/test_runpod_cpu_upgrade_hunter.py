from __future__ import annotations

import argparse

import pytest

from scripts.runpod_cpu_upgrade_hunter import (
    Config,
    Target,
    build_create_body,
    candidate_cpu_types,
    config_from_args,
    normalize_env,
    parse_targets,
    source_create_fields,
)


def _cfg(tmp_path) -> Config:
    return Config(
        source_pod_id="old123",
        data_center_id="EU-NL-1",
        network_volume_id="vol123",
        mount_path="/workspace",
        upgrade_entrypoint="/workspace/sentinelx-host/runpod-upgrade-entrypoint.sh",
        poll_seconds=30,
        hold_seconds=10800,
        targets=(Target(16, 32), Target(8, 16)),
        state_dir=tmp_path,
        log_path=tmp_path / "hunter.log",
    )


def test_parse_targets_preserves_preference_order():
    assert parse_targets("16/32,8/16") == (Target(16, 32), Target(8, 16))


def test_normalize_env_accepts_v2_dict_and_legacy_lists():
    assert normalize_env({"A": 1}) == {"A": "1"}
    assert normalize_env([{"key": "A", "value": "x"}, "B=y"]) == {
        "A": "x",
        "B": "y",
    }


def test_candidate_cpu_types_requires_exact_ram_ratio_and_dc():
    cpus = [
        {
            "id": "cpu-fast",
            "vcpu": {"min": 2, "max": 32},
            "ramGbPerVcpu": 2,
            "dataCenters": [{"id": "EU-NL-1", "availability": "LOW"}],
        },
        {
            "id": "cpu-too-much-ram",
            "vcpu": {"min": 2, "max": 32},
            "ramGbPerVcpu": 4,
            "dataCenters": [{"id": "EU-NL-1", "availability": "HIGH"}],
        },
        {
            "id": "cpu-wrong-dc",
            "vcpu": {"min": 2, "max": 32},
            "ramGbPerVcpu": 2,
            "dataCenters": [{"id": "US-KS-2", "availability": "HIGH"}],
        },
    ]
    result = candidate_cpu_types(cpus, Target(16, 32), "EU-NL-1")
    assert [item["id"] for item in result] == ["cpu-fast"]


def test_candidate_cpu_types_prefers_higher_reported_availability():
    cpus = [
        {
            "id": "cpu-low",
            "ramGbPerVcpu": 2,
            "dataCenters": [{"id": "EU-NL-1", "availability": "LOW"}],
        },
        {
            "id": "cpu-high",
            "ramGbPerVcpu": 2,
            "dataCenters": [{"id": "EU-NL-1", "availability": "HIGH"}],
        },
    ]
    result = candidate_cpu_types(cpus, Target(8, 16), "EU-NL-1")
    assert [item["id"] for item in result] == ["cpu-high", "cpu-low"]


def test_source_create_fields_accepts_legacy_shape():
    source = {
        "imageName": "runpod/base:tag",
        "containerDiskInGb": 20,
        "ports": "22/tcp,8888/http",
        "env": [{"key": "FOO", "value": "bar"}],
    }
    assert source_create_fields(source) == {
        "image": "runpod/base:tag",
        "disk": 20,
        "ports": ["22/tcp", "8888/http"],
        "env": {"FOO": "bar"},
        "startSsh": True,
    }


def test_build_create_body_pins_volume_dc_and_handoff(tmp_path):
    source = {
        "image": "runpod/base:tag",
        "disk": 20,
        "ports": ["22/tcp"],
        "env": {"KEEP": "yes"},
        "startSsh": True,
    }
    body = build_create_body(
        source,
        cfg=_cfg(tmp_path),
        target=Target(16, 32),
        cpu_flavor_id="cpu3c",
    )
    assert body["cpu"] == {"id": "cpu3c", "vcpuCount": 16}
    assert body["dataCenterIds"] == ["EU-NL-1"]
    assert body["mounts"] == {
        "network": [{"volumeId": "vol123", "path": "/workspace"}]
    }
    assert body["args"].endswith("runpod-upgrade-entrypoint.sh")
    assert body["env"]["KEEP"] == "yes"
    assert body["env"]["SKATAI_UPGRADE_SOURCE_POD_ID"] == "old123"
    assert body["env"]["SKATAI_UPGRADE_WAIT_SECONDS"] == "10800"


def test_config_uses_runtime_runpod_identity(tmp_path):
    args = argparse.Namespace(
        source_pod_id=None,
        data_center_id=None,
        network_volume_id=None,
        mount_path="/workspace",
        upgrade_entrypoint="/entry.sh",
        poll_seconds=30,
        hold_minutes=180,
        targets="16/32,8/16",
        state_dir=str(tmp_path),
        log_path=str(tmp_path / "x.log"),
    )
    cfg = config_from_args(
        args,
        {
            "RUNPOD_POD_ID": "pod-a",
            "RUNPOD_DC_ID": "EU-NL-1",
            "RUNPOD_VOLUME_ID": "vol-a",
        },
    )
    assert cfg.source_pod_id == "pod-a"
    assert cfg.data_center_id == "EU-NL-1"
    assert cfg.network_volume_id == "vol-a"


def test_source_requires_image_or_template():
    with pytest.raises(RuntimeError):
        source_create_fields({"disk": 20})
