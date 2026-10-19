"""审批授权、参数哈希、权威制度状态与重复设备防护。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workbench.agents import BusinessOutput, PolicyOutput, PolicyRule
from workbench.auth import User
from workbench.config import ApiKeyConfig
from workbench.errors import ConfigError
from workbench.ingest import import_document
from workbench.service import ServiceError, TaskService
from workbench.workflow import WorkflowError, approve_proposal, maybe_create_proposal

from .support import audit_result, build_env, business_findings, plan, policy_findings, tool_call


def full_task(env, request: str = "新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑") -> str:
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue("policy", tool_call("search_knowledge", {"query": "研发岗 电脑 领用"}), policy_findings())
    env.fake.queue("auditor", audit_result("pass"))
    return env.system.run(request, owner="alice", department="研发中心")


def ticket_count(db: Path) -> int:
    connection = sqlite3.connect(db)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    finally:
        connection.close()


def test_approver_without_department_is_rejected_by_config() -> None:
    with pytest.raises(ConfigError):
        ApiKeyConfig("approver-key-123", "carol", "approver", "")
    with pytest.raises(ConfigError):
        ApiKeyConfig("approver-key-123", "carol", "approver", "  ")


def test_empty_department_approver_cannot_list_or_read_or_decide(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    service = TaskService(env.settings, system=env.system, repository=env.repository)
    user = User("carol", "approver", "")
    with pytest.raises(ServiceError) as pending:
        service.pending_approvals(user)
    assert pending.value.status_code == 403
    with pytest.raises(ServiceError) as read:
        service.get_task(user, task_id)
    assert read.value.status_code == 404
    with pytest.raises(ServiceError) as decide:
        service.decide(user, proposal["proposal_id"], "approve", proposal["params_hash"])
    assert decide.value.status_code == 403


def test_approve_without_hash_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    before = ticket_count(env.settings.db_path)
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="bob",
            approver_department="研发中心",
            is_admin=False,
            expected_hash=None,
        )
    assert excinfo.value.code == "PARAMS_HASH_REQUIRED"
    assert ticket_count(env.settings.db_path) == before
    assert env.repository.get_task(task_id)["status"] == "needs_approval"


def test_tampered_actions_are_rejected_by_hash(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    connection = sqlite3.connect(env.settings.db_path)
    try:
        actions = json.loads(proposal["actions_json"])
        actions[0]["asset_id"] = "ASSET-1003"
        connection.execute(
            "UPDATE wb_proposals SET actions_json = ? WHERE proposal_id = ?",
            (json.dumps(actions, ensure_ascii=False), proposal["proposal_id"]),
        )
        connection.commit()
    finally:
        connection.close()
    before = ticket_count(env.settings.db_path)
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="bob",
            approver_department="研发中心",
            is_admin=False,
            expected_hash=proposal["params_hash"],
        )
    assert excinfo.value.code == "PROPOSAL_TAMPERED"
    assert ticket_count(env.settings.db_path) == before
    assert env.repository.get_task(task_id)["status"] == "needs_approval"


def test_department_mismatch_is_rejected_even_with_empty_proposal_department(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    connection = sqlite3.connect(env.settings.db_path)
    try:
        connection.execute("UPDATE wb_proposals SET department = '' WHERE proposal_id = ?", (proposal["proposal_id"],))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="bob",
            approver_department="研发中心",
            is_admin=False,
            expected_hash=proposal["params_hash"],
        )
    assert excinfo.value.code == "FORBIDDEN_DEPARTMENT"


def test_model_lies_about_draft_status_blocks_proposal(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    knowledge = env.system.tools.knowledge
    result = import_document(
        knowledge,
        filename="draft.md",
        data=b"# Draft Policy\n\n## Rule\nOnly valid for testing.",
        title="Draft Policy",
        doc_id="POL-DRAFT-001",
        version="v0.1",
        status="draft",
        doc_type="md",
        source="test",
        doc_group="POL-DRAFT-001",
    )
    assert result["chunks"] >= 1
    connection = sqlite3.connect(env.settings.kb_path)
    try:
        row = connection.execute(
            "SELECT evidence_id FROM kb_chunks WHERE doc_pk = ? LIMIT 1", (result["doc_pk"],)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    evidence_id = row[0]
    task_id = env.repository.create_task(
        "新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑",
        owner="alice",
        department="研发中心",
    )
    policy = PolicyOutput(
        rules=[
            PolicyRule(
                doc_id="POL-DRAFT-001",
                title="Draft Policy",
                section="Rule",
                version="v0.1",
                status="current",  # 模型故意谎报为现行
                condition="Draft rule",
                citation="Draft Policy · Rule",
                evidence_ids=[evidence_id],
            )
        ],
        conflicts=[],
        gaps=[],
        confidence=0.9,
    )
    business = BusinessOutput(summary="库存可用", claims=[], data_gaps=[], confidence=0.9)
    proposal = maybe_create_proposal(
        repository=env.repository,
        store=env.system.tools.store,
        knowledge=knowledge,
        task=env.repository.get_task(task_id),
        business_output=business,
        policy_output=policy,
        audit_verdict="pass",
        request="新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑",
    )
    assert proposal is None
    events = env.repository.list_events(task_id)
    blocked = [json.loads(event["payload_json"]) for event in events if event["type"] == "proposal_blocked"]
    assert blocked and "非现行" in blocked[-1]["reason"]


def test_employee_with_existing_asset_blocks_proposal(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    before = ticket_count(env.settings.db_path)
    # EMP-1001 已持有 ASSET-1001
    task_id = full_task(env, "新员工 EMP-1001 申请一台 ThinkPad T14 Gen5 笔记本电脑")
    assert env.repository.get_proposal_by_task(task_id) is None
    final = json.loads(env.repository.get_task(task_id)["final_json"])
    assert final["proposal_blocked"]["reason"]
    assert "ASSET-1001" in final["proposal_blocked"]["evidence"]
    assert ticket_count(env.settings.db_path) == before


def test_employee_with_active_reservation_blocks_second_proposal(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    first = full_task(env)
    first_proposal = env.repository.get_proposal_by_task(first)
    approve_proposal(
        env.repository,
        proposal_id=first_proposal["proposal_id"],
        approver="bob",
        approver_department="研发中心",
        is_admin=False,
        expected_hash=first_proposal["params_hash"],
    )
    second = full_task(env)
    assert env.repository.get_proposal_by_task(second) is None
    final = json.loads(env.repository.get_task(second)["final_json"])
    assert final["proposal_blocked"]["reason"]
    reservations = env.repository.list_reservations()
    assert len(reservations) == 1
    assert reservations[0]["employee_id"] == "EMP-1012"


def test_cross_department_target_employee_blocks_proposal(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    before_tickets = ticket_count(env.settings.db_path)
    before_reservations = len(env.repository.list_reservations())
    # 申请人是研发中心，但目标员工 EMP-1013 属于财务部
    task_id = full_task(env, "新员工 EMP-1013 申请一台 ThinkPad T14 Gen5 笔记本电脑")
    assert env.repository.get_proposal_by_task(task_id) is None
    final = json.loads(env.repository.get_task(task_id)["final_json"])
    blocked = final["proposal_blocked"]
    assert "部门不一致" in blocked["reason"]
    assert "财务部" in blocked["reason"]
    assert blocked["evidence"][0] == "EMP-1013"
    assert ticket_count(env.settings.db_path) == before_tickets
    assert len(env.repository.list_reservations()) == before_reservations
