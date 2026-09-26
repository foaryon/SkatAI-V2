import json
from pathlib import Path

import pytest

from skatai.orchestration import control_plane as cp


def base_task():
    return {
        "task_id": "t-test-001",
        "task_family": "qa",
        "assigned_agent": "qa-validation",
        "priority": "P1",
        "global_goal_reference": ["strong_integrated_skat_ai"],
        "work_prompt_capability_reference": ["controlled_scientific_evaluation"],
        "current_gap": "Need a focused verification.",
        "objective": "Run the bounded verification.",
        "rationale": "Evidence is needed before integration.",
        "scope": ["tests/test_game_rules.py"],
        "authority": {
            "read": ["tests/**", "src/**"],
            "write": ["tests/test_game_rules.py"],
            "execute": [["python3", "-m", "pytest", "-q", "tests/test_game_rules.py"]],
            "forbidden": ["secrets", "global reprioritization"],
        },
        "evidence_requirements": ["pytest output"],
        "success_criteria": ["test command passes"],
        "execution_profile": {
            "preferred_execution_mode": "very_low_cost_model",
            "reasoning_effort": "minimal",
            "max_cost_class": "very_low",
            "model_escalation_requires_orchestrator": True,
        },
    }


def test_runtime_policy_has_one_expensive_brain_and_cheap_workers():
    cfg = json.loads((cp.REPO / "configs/orchestration/ORCHESTRATOR_RUNTIME_POLICY.json").read_text())
    assert cfg["superbrain"]["model"] == "gpt-6-sol"
    assert cfg["superbrain"]["reasoning_effort"] == "medium"
    assert cfg["workers"]["default_model"] == "gpt-6-luna"
    assert cfg["workers"]["default_reasoning_effort"] == "low"
    assert cfg["workers"]["may_spawn_subworkers"] is False
    assert cfg["workers"]["may_self_escalate"] is False


def test_architecture_has_fourteen_bounded_worker_roles():
    roles = cp.roles()
    assert len(roles) == 14
    assert "planner-tasking" in roles
    assert "training" in roles
    assert "qa-validation" in roles
    assert "release-packaging" in roles


def test_task_contract_normalizes_to_ready():
    task = cp.validate_task_contract(base_task())
    assert task["state"] == "READY"
    assert task["assigned_agent"] == "qa-validation"
    assert task["result_path"].endswith("t-test-001.json")


def test_worker_control_plane_paths_are_protected():
    task = base_task()
    task["authority"]["write"] = ["src/skatai/orchestration/control_plane.py"]
    with pytest.raises(RuntimeError, match="CONTROL_PLANE_WRITE"):
        cp.validate_task_contract(task)


def test_mid_cost_worker_requires_explicit_escalation_reason():
    task = base_task()
    task["execution_profile"]["preferred_execution_mode"] = "mid_cost_model"
    task["execution_profile"]["max_cost_class"] = "mid"
    with pytest.raises(RuntimeError, match="MID_COST_REQUIRES_ESCALATION_REASON"):
        cp.validate_task_contract(task)


def test_task_model_defaults_to_nano_and_cannot_silently_upgrade():
    task = cp.validate_task_contract(base_task())
    model, reasoning = cp.task_model(task)
    assert model == "gpt-6-luna"
    assert reasoning == "low"

    escalated = base_task()
    escalated["execution_profile"].update({
        "preferred_execution_mode": "mid_cost_model",
        "max_cost_class": "mid",
        "reasoning_effort": "medium",
        "escalation_reason": "Bounded local debugging exceeded nano capability.",
    })
    escalated = cp.validate_task_contract(escalated)
    model, reasoning = cp.task_model(escalated)
    assert model == "gpt-6-luna"
    assert reasoning == "medium"


def test_worker_authority_matches_only_explicit_scope():
    task = cp.validate_task_contract(base_task())
    assert cp.task_scope_allows(task, "read", "repo:tests/test_game_rules.py")
    assert not cp.task_scope_allows(task, "read", "repo:provenance/secret.json")
    assert cp.task_scope_allows(task, "write", "repo:tests/test_game_rules.py")
    assert not cp.task_scope_allows(task, "write", "repo:src/skatai/game/rules.py")


def test_worker_tools_do_not_include_agent_creation_or_priority_controls():
    names = {tool["name"] for tool in cp.worker_tools()}
    assert "submit_worker_result" in names
    assert "run_command" in names
    assert "create_task" not in names
    assert "dispatch_task" not in names
    assert "accept_worker_result" not in names


def test_superbrain_tools_have_tasking_but_no_arbitrary_shell():
    names = {tool["name"] for tool in cp.orchestration_tools()}
    assert {"create_task", "dispatch_task", "accept_worker_result"} <= names
    assert "run_command" not in names


def test_dependencies_must_be_complete_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    monkeypatch.setattr(cp, 'CONTROLLER_STATE', tmp_path / 'controller.json')
    cp.atomic_json(cp.TASKS, {'tasks': {'prereq': {'state': 'READY'}}})
    task = base_task()
    task['dependencies'] = ['prereq']
    cp.create_task(task)
    with pytest.raises(RuntimeError, match='DEPENDENCIES_NOT_COMPLETE'):
        cp.dispatch_task(task['task_id'])


def test_worker_cannot_expand_task_scope_with_glob_or_control_paths():
    task = base_task()
    task['authority']['write'] = ['**/*']
    with pytest.raises(RuntimeError, match='WRITE_SCOPE'):
        cp.validate_task_contract(task)


def test_worker_result_requires_evidence_and_matching_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(cp, 'REPO', tmp_path)
    monkeypatch.setattr(cp, 'EVIDENCE', tmp_path / 'evidence.jsonl')
    task = cp.validate_task_contract(base_task())
    with pytest.raises(RuntimeError, match='WORKER_RESULT'):
        cp.submit_worker_result(task, 'different-worker', 's1', {
            'status': 'COMPLETE', 'observations': [], 'changes': [],
            'evidence': [], 'verification': [], 'unresolved': []})


def test_deterministic_mode_is_not_routed_to_a_model():
    task = base_task()
    task['execution_profile'].update({'preferred_execution_mode': 'deterministic', 'max_cost_class': 'deterministic'})
    task = cp.validate_task_contract(task)
    assert cp.task_model(task) == (None, None)


def test_read_text_bounds_context(tmp_path):
    p = tmp_path / 'large.txt'
    p.write_text('large-line\n' * 10000)
    assert len(cp.bounded_text(p).encode()) < 4200


def test_exact_worker_command_runs_without_root_credentials(monkeypatch):
    import subprocess
    seen = {}
    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, 'ok', '')
    monkeypatch.setattr(cp.subprocess, 'run', fake_run)
    task = base_task()
    cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
    assert seen['user'] == 'sentinelx'
    assert 'OPENAI_API_KEY' not in seen['env']
    assert 'RUNPOD_SECRET_openai_agents_api_key' not in seen['env']


def test_repo_prefixed_protected_write_is_rejected():
    task = base_task()
    task['authority']['write'] = ['repo:src/skatai/orchestration/control_plane.py']
    with pytest.raises(RuntimeError, match='CONTROL_PLANE_WRITE'):
        cp.validate_task_contract(task)


def test_directory_write_scope_is_rejected():
    task = base_task()
    task['authority']['write'] = ['repo:integrations/jskat-adapter/']
    with pytest.raises(RuntimeError, match='WRITE_SCOPE'):
        cp.validate_task_contract(task)
