import json
from pathlib import Path

import pytest

from skatai.orchestration import control_plane as cp


@pytest.fixture(autouse=True)
def isolate_orchestrator_state(tmp_path, monkeypatch):
    """No unit test may write to live network-volume control or project state."""
    for name in ('CONTROL', 'STATE_DIR'):
        monkeypatch.setattr(cp, name, tmp_path)
    for name, filename in (
        ('CONTROLLER_STATE', 'controller_state.json'), ('PROJECT_STATE', 'project_state.json'),
        ('TASKS', 'tasks.json'), ('WORKERS', 'workers.json'), ('CAPABILITIES', 'capabilities.json'),
        ('DECISIONS', 'decisions.jsonl'), ('EVIDENCE', 'evidence.jsonl'),
        ('EXPERIMENTS', 'experiments.json'), ('RELEASES', 'releases.json'),
        ('COST', 'cost.jsonl'), ('LOG', 'controller.log'), ('PID', 'controller.pid'),
    ):
        monkeypatch.setattr(cp, name, tmp_path / filename)


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
            "execute": [["git", "-C", str(cp.REPO), "rev-parse", "HEAD"]],
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
    task['authority']['execute'] = [['python3', '-m', 'pytest', '-q', 'tests/test_game_rules.py']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
    task['authority']['execute'] = [['bash', 'scripts/audit_data_split_leakage.py']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)
    task['authority']['execute'] = [['rg', '-n', 'needle', '--pre', 'evil', 'src/skatai']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.run_exact_worker_command(task, task['authority']['execute'][0], 10)


def test_capability_map_covers_all_work_prompt_phases_and_preserves_assessment(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'CAPABILITIES', tmp_path / 'capabilities.json')
    cp.atomic_json(cp.CAPABILITIES, {'capabilities': {'controlled_scientific_evaluation': 'PARTIAL'},
                                      'work_prompt_phases': {'phase_00': {'status': 'BLOCKED', 'evidence': ['prior']}}})
    data = cp.reconcile_capability_map()
    assert len(data['work_prompt_phases']) == 29
    assert data['work_prompt_phases']['phase_00']['status'] == 'BLOCKED'
    assert data['work_prompt_phases']['phase_28']['status'] == 'NOT_STARTED'
    assert data['capabilities']['controlled_scientific_evaluation'] == 'PARTIAL'
    assert data['authority_hashes']['work_prompt'] == cp.sha256(cp.WORK_PROMPT)


def test_capability_assessment_requires_existing_hashed_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'CAPABILITIES', tmp_path / 'capabilities.json')
    cp.atomic_json(cp.TASKS, {'tasks': {}})
    cp.reconcile_capability_map()
    with pytest.raises(RuntimeError, match='CAPABILITY_TASK_EVIDENCE_NOT_ACCEPTED'):
        cp.assess_capability('phase_16', 'VERIFIED', ['task:invented'], 'Controlled promotion evidence was independently verified.')
    with pytest.raises(RuntimeError, match='CAPABILITY_ASSESSMENT_INSUFFICIENT'):
        cp.assess_capability('phase_16', 'VERIFIED', [], 'Controlled promotion evidence was independently verified.')


def test_rolling_budget_adapts_to_accepted_outcomes_and_requires_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'LOG', tmp_path / 'controller.log')
    monkeypatch.setattr(cp, 'COST', tmp_path / 'usage.jsonl')
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    cp.atomic_json(cp.TASKS, {'tasks': {str(i): {'state': 'COMPLETE', 'integration_decision': {'accepted': True}}
                                        for i in range(2)}})
    now = cp.time.time()
    stamp = cp.utc_now()
    cp.LOG.write_text(''.join(f'{stamp} superbrain_session_created id=sess_{i} model=gpt-6-sol\n' for i in range(15)))
    usage = {'input_tokens': 10000, 'output_tokens': 100, 'total_tokens': 10100}
    for i in range(15):
        cp.append_jsonl(cp.COST, {'session_id': f'sess_{i}', 'role': 'orchestrator-superbrain', 'usage': usage})
    status = cp.superbrain_budget_status(now)
    assert status['sessions'] == 15
    assert status['limits']['sessions'] == 24
    assert status['limits']['tokens'] == 8000000
    assert not status['exhausted']
    for i in range(15, 24):
        with cp.LOG.open('a') as f:
            f.write(f'{stamp} superbrain_session_created id=sess_{i} model=gpt-6-sol\n')
        cp.append_jsonl(cp.COST, {'session_id': f'sess_{i}', 'role': 'orchestrator-superbrain', 'usage': usage})
    assert 'session_allowance' in cp.superbrain_budget_status(now)['reasons']
    cp.LOG.write_text(''.join(f'{stamp} superbrain_session_created id=sess_{i} model=gpt-6-sol\n' for i in range(15)))
    cp.COST.write_text('')
    assert 'missing_usage' in cp.superbrain_budget_status(now)['reasons']


def test_budget_prevents_creating_another_superbrain_session(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'CONTROLLER_STATE', tmp_path / 'state.json')
    monkeypatch.setattr(cp, 'superbrain_budget_status', lambda: {'exhausted': True})
    monkeypatch.setattr(cp, 'create_session', lambda *args, **kwargs: pytest.fail('model session created'))
    state = cp.ensure_superbrain_session({'superbrain_agent_id': 'a', 'event_seq': 1})
    assert state['superbrain_paused_reason'] == 'adaptive superbrain budget exhausted'


def test_task_listing_is_compact_and_full_contract_requires_single_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    task = base_task()
    task['state'] = 'READY'
    cp.atomic_json(cp.TASKS, {'tasks': {'one': task}})
    listing = cp.dispatch_tool('list_tasks', {}, kind='orchestrator')['tasks']['one']
    assert 'authority' not in listing
    assert listing['objective'] == task['objective']
    assert cp.dispatch_tool('get_task', {'task_id': 'one'}, kind='orchestrator')['authority'] == task['authority']


def test_cost_estimate_deduplicates_delayed_usage_and_counts_accepted_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'COST', tmp_path / 'usage.jsonl')
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    usage = {'input_tokens': 1000000, 'input_tokens_details': {'cached_tokens': 900000},
             'output_tokens': 10000, 'total_tokens': 1010000}
    assert cp.estimate_usage_usd(usage, 'gpt-6-sol', 'flex') == 0.24
    cp.append_jsonl(cp.COST, {'session_id': 's1', 'role': 'orchestrator-superbrain', 'usage': usage})
    cp.append_jsonl(cp.COST, {'session_id': 's1', 'role': 'orchestrator-superbrain', 'usage': usage, 'outcome': 'correction'})
    cp.atomic_json(cp.TASKS, {'tasks': {'t': {'state': 'COMPLETE', 'integration_decision': {'accepted': True}}}})
    x = cp.model_cost_summary()
    assert x['sessions_with_usage'] == 1
    assert x['total_tokens'] == 1010000
    assert x['estimated_usd_per_accepted_worker_outcome'] == 0.24


def test_unsafe_command_rejected_before_worker_session_creation():
    task = base_task()
    task['authority']['execute'] = [['python3', '-m', 'pytest', '-q', 'tests/test_game_rules.py']]
    with pytest.raises(RuntimeError, match='COMMAND_UNSAFE_SHARED_CHECKOUT'):
        cp.validate_task_contract(task)


def test_exact_target_status_probe_allowed():
    cp.validate_worker_command(['git', '-C', str(cp.REPO), 'status', '--porcelain=v1', '--',
                                'src/skatai/evaluation/bidding_gameplay_gate.py'])


def test_unchanged_negative_result_is_fail_closed_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    monkeypatch.setattr(cp, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(cp, 'DECISIONS', tmp_path / 'decisions.jsonl')
    monkeypatch.setattr(cp, 'CONTROLLER_STATE', tmp_path / 'controller.json')
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 0})
    monkeypatch.setattr(cp, 'refresh_project_state', lambda: None)
    task = base_task()
    task['state'] = 'VERIFYING'
    cp.atomic_json(cp.TASKS, {'tasks': {task['task_id']: task}})
    rp = cp.result_file(task['task_id'])
    cp.atomic_json(rp, {'task_id': task['task_id'], 'worker_id': task['assigned_agent'],
                        'status': 'BLOCKED', 'changes': [], 'artifacts': []})
    assert cp.reconcile_negative_results() == [task['task_id']]
    assert cp.strict_json(cp.TASKS)['tasks'][task['task_id']]['state'] == 'BLOCKED'
    assert cp.reconcile_negative_results() == []
    assert len(cp.DECISIONS.read_text().splitlines()) == 1
    assert cp.strict_json(cp.CONTROLLER_STATE)['event_seq'] == 1


def test_negative_result_with_side_effects_stays_for_review(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    monkeypatch.setattr(cp, 'STATE_DIR', tmp_path)
    task = base_task()
    task['state'] = 'VERIFYING'
    cp.atomic_json(cp.TASKS, {'tasks': {task['task_id']: task}})
    cp.atomic_json(cp.result_file(task['task_id']), {'task_id': task['task_id'],
                   'worker_id': task['assigned_agent'], 'status': 'BLOCKED',
                   'changes': ['repo:source.py'], 'artifacts': []})
    assert cp.reconcile_negative_results() == []


def test_tool_failure_is_persisted_without_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(cp, 'CONTROL', tmp_path)
    monkeypatch.setattr(cp, 'LOG', tmp_path / 'controller.log')
    monkeypatch.setattr(cp, 'dispatch_tool', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('INVALID_CONTRACT')))
    monkeypatch.setattr(cp, 'api', lambda *args, **kwargs: {})
    cp.resolve_required_actions({'id': 'sess_test', 'required_actions': [{
        'type': 'function_call', 'name': 'create_task', 'arguments': {'contract': {'secret': 'dont-log'}},
        'turn_id': 'turn', 'call_id': 'call'}]}, kind='orchestrator')
    line = (tmp_path / 'tool_failure_registry.jsonl').read_text()
    assert 'INVALID_CONTRACT' in line
    assert 'dont-log' not in line


def test_only_new_external_evidence_or_revision_can_resume_paused_reasoning():
    state = {'event_seq': 7, 'last_superbrain_event_seq': 6,
             'last_event': {'kind': 'controller_revision'}}
    assert cp.has_actionable_reasoning_event(state)
    state['last_superbrain_event_seq'] = 7
    assert not cp.has_actionable_reasoning_event(state)
    state['last_superbrain_event_seq'] = 6
    state['last_event']['kind'] = 'task_dispatch_requested'
    assert not cp.has_actionable_reasoning_event(state)


def test_readonly_worker_gets_distinct_minimal_contract_and_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'TASKS', tmp_path / 'tasks.json')
    monkeypatch.setattr(cp, 'CONTROLLER_STATE', tmp_path / 'controller.json')
    monkeypatch.setattr(cp, 'REPO', tmp_path)
    cp.atomic_json(cp.TASKS, {'tasks': {}})
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 0})
    (tmp_path / 'acceptance.json').write_text('{"status":"PENDING"}')
    result = cp.create_and_dispatch_readonly_task({
        'task_id': 'audit-acceptance', 'assigned_agent': 'scientific-governance',
        'priority': 'P1', 'objective': 'Read the bounded acceptance evidence.',
        'rationale': 'A pending gate needs a precise finding.', 'current_gap': 'Acceptance pending.',
        'read_paths': ['acceptance.json'], 'work_prompt_capability_reference': ['phase_16'],
        'evidence_requirements': ['Exact path and status'], 'success_criteria': ['Precise finding']})
    task = cp.strict_json(cp.TASKS)['tasks']['audit-acceptance']
    assert result['state'] == 'DISPATCH_REQUESTED'
    assert task['authority'] == {'read': ['repo:acceptance.json'], 'write': [], 'execute': [],
                                  'forbidden': ['No commands, writes, secrets, promotion, or project-wide decisions.']}
    assert task['execution_profile']['max_cost_class'] == 'very_low'


def test_idle_dispatch_event_does_not_call_superbrain(monkeypatch):
    monkeypatch.setattr(cp, 'superbrain_budget_status', lambda: {'exhausted': False})
    monkeypatch.setattr(cp, 'api', lambda *args, **kwargs: {'status': 'idle'})
    monkeypatch.setattr(cp, 'send_message', lambda *args, **kwargs: pytest.fail('idle dispatch caused model call'))
    state = {'superbrain_session_id': 's1', 'event_seq': 2, 'last_superbrain_event_seq': 1,
             'last_event': {'kind': 'task_dispatch_requested'}}
    assert cp.poll_superbrain(state) == state


def test_idle_superbrain_makes_no_api_poll_without_actionable_event(monkeypatch):
    monkeypatch.setattr(cp, 'superbrain_budget_status', lambda: {'exhausted': False})
    monkeypatch.setattr(cp, 'api', lambda *args, **kwargs: pytest.fail('idle session was polled'))
    state = {'superbrain_session_id': 's1', 'superbrain_idle': True, 'event_seq': 9,
             'last_superbrain_event_seq': 8, 'last_event': {'kind': 'task_dispatch_requested'}}
    assert cp.poll_superbrain(state) == state


def test_repeated_orchestrator_rejection_is_session_scoped(tmp_path):
    path = cp.STATE_DIR / 'tool_failure_registry.jsonl'
    for sid in ('other', 'current', 'current'):
        cp.append_jsonl(path, {'session_id': sid, 'kind': 'orchestrator',
                               'name': 'create_task', 'message': 'COMMAND_UNSAFE_SHARED_CHECKOUT'})
    assert cp.repeated_orchestrator_rejection('current')
    assert not cp.repeated_orchestrator_rejection('other')
    assert not cp.repeated_orchestrator_rejection('unseen')


def test_readonly_worker_package_excludes_global_authority_documents():
    with pytest.raises(RuntimeError, match='GLOBAL_AUTHORITY_IS_ORCHESTRATOR_CONTEXT'):
        cp.create_and_dispatch_readonly_task({'read_paths': ['repo:SKATAI_V2_WORK_PROMPT.md']})


def test_canonical_scoped_paths_roundtrip_into_worker_read(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'REPO', tmp_path)
    source = tmp_path / 'evidence.txt'
    source.write_text('verified evidence\n')
    task = base_task()
    task['authority']['read'] = ['repo:evidence.txt']
    assert cp.safe_path('repo:evidence.txt') == (source, 'repo:evidence.txt')
    result = cp.read_text_tool({'path': 'repo:evidence.txt'}, task)
    assert 'verified evidence' in result['text']
    assert result['sha256'] == cp.sha256(source)
    with pytest.raises(RuntimeError, match='PATH_OUTSIDE_PROJECT'):
        cp.safe_path('repo:../other')
    with pytest.raises(RuntimeError, match='PATH_OUTSIDE_PROJECT'):
        cp.safe_path('runtime:somefile', allow_runtime=False)


def test_unchanged_partial_result_closes_fail_closed_without_claiming_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'refresh_project_state', lambda: None)
    task = base_task()
    task['state'] = 'VERIFYING'
    cp.atomic_json(cp.TASKS, {'tasks': {task['task_id']: task}})
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 0})
    cp.atomic_json(cp.result_file(task['task_id']), {
        'task_id': task['task_id'], 'worker_id': task['assigned_agent'],
        'status': 'PARTIAL', 'changes': [], 'artifacts': [],
        'observations': ['One file has a PENDING field'], 'evidence': [],
        'verification': [], 'unresolved': ['Acceptance unproven']})
    assert cp.reconcile_negative_results() == [task['task_id']]
    after = cp.strict_json(cp.TASKS)['tasks'][task['task_id']]
    assert after['state'] == 'BLOCKED'
    assert after['integration_decision']['accepted'] is False
    assert cp.reconcile_negative_results() == []


def test_partial_readonly_result_may_reference_unchanged_input(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'refresh_project_state', lambda: None)
    task = base_task()
    task['task_family'] = 'bounded_readonly_audit'
    task['authority']['execute'] = []
    task['authority']['write'] = []
    task['authority']['read'] = ['repo:input.json']
    task['state'] = 'VERIFYING'
    cp.atomic_json(cp.TASKS, {'tasks': {task['task_id']: task}})
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 0})
    cp.atomic_json(cp.result_file(task['task_id']), {
        'task_id': task['task_id'], 'worker_id': task['assigned_agent'],
        'status': 'PARTIAL', 'changes': [], 'artifacts': ['repo:input.json'],
        'observations': [], 'evidence': [], 'verification': [], 'unresolved': ['PENDING']})
    assert cp.reconcile_negative_results() == [task['task_id']]
    assert cp.strict_json(cp.TASKS)['tasks'][task['task_id']]['state'] == 'BLOCKED'


def test_delayed_usage_recovery_skips_live_session_and_deduplicates(tmp_path, monkeypatch):
    cp.LOG.write_text('2026-09-26T12:00:00Z superbrain_session_created id=sess_old model=gpt-6-sol\n'
                      '2026-09-26T12:01:00Z superbrain_session_created id=sess_live model=gpt-6-sol\n')
    seen = []
    def fake_api(method, path):
        seen.append(path)
        return {'id': 'sess_old', 'status': 'idle', 'usage': {'input_tokens': 5,
                'input_tokens_details': {'cached_tokens': 0}, 'output_tokens': 1, 'total_tokens': 6}}
    monkeypatch.setattr(cp, 'api', fake_api)
    assert cp.reconcile_delayed_superbrain_usage({'superbrain_session_id': 'sess_live'}) == ['sess_old']
    assert cp.reconcile_delayed_superbrain_usage({'superbrain_session_id': 'sess_live'}) == []
    assert seen == ['/agents/sessions/sess_old']


def test_controller_iteration_sees_result_event_after_deterministic_reconcile(monkeypatch):
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 1})
    seen = []
    def reconcile():
        cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 2, 'last_event': {'kind': 'negative_worker_result_integrated'}})
    def poll(state):
        seen.append(state['event_seq'])
        return state
    monkeypatch.setattr(cp, 'reconcile_negative_results', reconcile)
    monkeypatch.setattr(cp, 'reconcile_repository_revision', lambda: False)
    monkeypatch.setattr(cp, 'poll_superbrain', poll)
    monkeypatch.setattr(cp, 'launch_ready_workers', lambda state: None)
    monkeypatch.setattr(cp, 'poll_workers', lambda state: None)
    cp.controller_iteration()
    assert seen == [2]
    assert cp.strict_json(cp.CONTROLLER_STATE)['event_seq'] == 2


def test_repeated_partial_audits_stop_unproductive_reasoning(tmp_path, monkeypatch):
    for i in range(2):
        task = base_task()
        task['task_id'] = f'placeholder_{i}'
        task['task_family'] = 'bounded_readonly_audit'
        task['state'] = 'BLOCKED'
        task['created_at'] = f'2026-09-26T12:00:0{i}Z'
        task['integration_decision'] = {'accepted': False}
        if i == 0:
            tasks = {}
        tasks[task['task_id']] = task
        cp.atomic_json(cp.result_file(task['task_id']), {'status': 'PARTIAL'})
    cp.atomic_json(cp.TASKS, {'tasks': tasks})
    assert cp.repeated_placeholder_audit_streak() == 2
    state = {'event_seq': 2, 'last_superbrain_event_seq': 1,
             'last_event': {'kind': 'negative_worker_result_integrated'}}
    monkeypatch.setattr(cp, 'api', lambda *args, **kwargs: pytest.fail('idle must not call model API'))
    assert 'repeated placeholder audits' in cp.poll_superbrain(state)['superbrain_paused_reason']
    assert cp.strict_json(cp.CONTROLLER_STATE)['last_superbrain_event_seq'] == 2


def test_stale_controller_write_preserves_newer_external_event():
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 1, 'last_superbrain_event_seq': 1})
    stale = cp.strict_json(cp.CONTROLLER_STATE)
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 2, 'last_superbrain_event_seq': 1,
                                        'last_event': {'kind': 'worker_result', 'subject': 'real'}})
    stale['superbrain_idle'] = True
    cp.atomic_json(cp.CONTROLLER_STATE, stale)
    after = cp.strict_json(cp.CONTROLLER_STATE)
    assert after['event_seq'] == 2
    assert after['last_event'] == {'kind': 'worker_result', 'subject': 'real'}
    assert after['superbrain_idle'] is True


def test_repository_revision_wakes_only_for_relevant_new_work(monkeypatch):
    heads = iter(['a', 'b', 'c'])
    def fake_git(*args):
        if args == ('rev-parse', 'HEAD'):
            return next(heads)
        if args == ('diff', '--name-only', 'a', 'b'):
            return 'provenance/status.json'
        if args == ('diff', '--name-only', 'b', 'c'):
            return 'src/skatai/runtime/host_service.py'
        raise AssertionError(args)
    monkeypatch.setattr(cp, 'git', fake_git)
    cp.atomic_json(cp.CONTROLLER_STATE, {'event_seq': 0, 'last_superbrain_event_seq': 0})
    assert cp.reconcile_repository_revision() is False
    assert cp.reconcile_repository_revision() is False
    assert cp.reconcile_repository_revision() is True
    state = cp.strict_json(cp.CONTROLLER_STATE)
    assert state['event_seq'] == 1
    assert state['last_event']['kind'] == 'controller_revision'
    assert state['observed_repository_revision'] == 'c'
