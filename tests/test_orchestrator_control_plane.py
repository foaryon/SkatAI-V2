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
    task['authority']['execute'] = [['git', '-C', str(cp.REPO), 'rev-parse', 'HEAD']]
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


def test_bootstrap_has_bounded_dispatch_instruction():
    prompt = cp.superbrain_bootstrap_text()
    assert 'list_tasks' in prompt
    assert 'within at most six tool calls' in prompt
    assert 'do not restart it' in prompt


def test_session_rotation_requires_persisted_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'DECISIONS', tmp_path / 'decisions.jsonl')
    prior = {'session_start_decision_bytes': 0}
    assert not cp.integrated_since_session_start(prior)
    cp.append_jsonl(cp.DECISIONS, {'type': 'worker_result_integration'})
    assert cp.integrated_since_session_start(prior)
    prior['session_start_decision_bytes'] = cp.decision_file_size()
    assert not cp.integrated_since_session_start(prior)


def test_repeated_blocked_command_set_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    prior = base_task()
    prior['task_id'] = 'prior-blocked'
    prior['state'] = 'BLOCKED'
    cp.atomic_json(cp.TASKS, {'tasks': {'prior-blocked': prior}})
    with pytest.raises(RuntimeError, match='DUPLICATE_BLOCKED_WORK'):
        cp.create_task(base_task())


def test_new_worker_evidence_allows_one_bounded_session_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'EVIDENCE', tmp_path / 'evidence.jsonl')
    state = {'session_start_evidence_bytes': 0}
    assert not cp.evidence_since_session_start(state)
    cp.append_jsonl(cp.EVIDENCE, {'type': 'worker_result'})
    assert cp.evidence_since_session_start(state)
    state['session_start_evidence_bytes'] = cp.EVIDENCE.stat().st_size
    assert not cp.evidence_since_session_start(state)


def test_command_input_hashes_track_newly_available_tool(tmp_path, monkeypatch):
    import shutil
    task = base_task()
    task['authority']['execute'] = [['rg', 'pattern', 'provenance']]
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    missing = cp.command_input_hashes(task)
    binary = tmp_path / 'rg'
    binary.write_bytes(b'new-tool')
    monkeypatch.setattr(shutil, 'which', lambda name: str(binary))
    present = cp.command_input_hashes(task)
    assert 'tool:rg' not in missing
    assert present['tool:rg'] == cp.sha256(binary)


def test_nonconflicting_workers_launch_while_shared_write_waits(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    monkeypatch.setattr(cp, 'WORKERS', tmp_path / 'workers.json')
    monkeypatch.setattr(cp, 'refresh_project_state', lambda: None)
    monkeypatch.setattr(cp, 'log', lambda message: None)
    cfg = json.loads((cp.REPO / 'configs/orchestration/ORCHESTRATOR_RUNTIME_POLICY.json').read_text())
    monkeypatch.setattr(cp, 'config', lambda: cfg)
    launched = []
    def create_session(agent_id, model, tier, prompt, **kwargs):
        launched.append(agent_id)
        return {'id': 's' + str(len(launched))}
    monkeypatch.setattr(cp, 'create_session', create_session)
    tasks = {}
    for i, path in enumerate(('provenance/shared.json', 'provenance/shared.json', 'provenance/independent.json')):
        task = base_task()
        task['task_id'] = 'bounded-' + str(i)
        task['authority']['write'] = [path]
        task = cp.validate_task_contract(task)
        task['state'] = 'DISPATCH_REQUESTED'
        task['created_at'] = '2026-09-26T00:00:0' + str(i) + 'Z'
        tasks[task['task_id']] = task
    cp.atomic_json(cp.TASKS, {'tasks': tasks})
    cp.atomic_json(cp.WORKERS, {'workers': {}})
    cp.launch_ready_workers({'worker_agent_ids': {'qa-validation': 'agent-qa'}})
    states = {k: v['state'] for k,v in cp.strict_json(cp.TASKS)['tasks'].items()}
    assert states == {'bounded-0': 'RUNNING', 'bounded-1': 'DISPATCH_REQUESTED', 'bounded-2': 'RUNNING'}
    assert len(launched) == 2


def test_worker_command_cannot_run_script_or_tests_with_indirect_effects(monkeypatch):
    import subprocess
    monkeypatch.setattr(cp.subprocess, 'run', lambda *args, **kwargs: pytest.fail('unsafe subprocess ran'))
    task = base_task()
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
    task['authority']['execute'] = [['bash', 'scripts/audit_data_split_leakage.py']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
    task['authority']['execute'] = [['rg', '-n', 'needle', '--pre', 'evil', 'src/skatai']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
