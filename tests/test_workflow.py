"""业务工作流：提案、审批、事务执行、幂等与并发预留。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from workbench.workflow import (
    WorkflowError,
    approve_proposal,
    is_actionable,
    reject_proposal,
)

from .support import (
    audit_result,
    build_env,
    business_findings,
    plan,
    policy_findings,
    tool_call,
)


def full_task(env, request: str = "新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑") -> str:
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue("policy", tool_call("search_knowledge", {"query": "研发岗 电脑 领用"}), policy_findings())
    env.fake.queue("auditor", audit_result("pass"))
    return env.system.run(request, owner="alice", department="研发中心")


def ticket_count(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    finally:
        connection.close()


def test_actionable_detection() -> None:
    assert is_actionable("新员工 EMP-1012 申请一台笔记本")
    assert not is_actionable("现在库存多少")


def test_standard_task_creates_pending_proposal_without_writing(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    before = ticket_count(env.settings.db_path)
    task_id = full_task(env)
    task = env.repository.get_task(task_id)
    assert task["status"] == "needs_approval"
    proposal = env.repository.get_proposal_by_task(task_id)
    assert proposal and proposal["status"] == "pending"
    final = json.loads(task["final_json"])
    assert final["requires_approval"] is True
    assert final["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert ticket_count(env.settings.db_path) == before
    connection = sqlite3.connect(env.settings.db_path)
    try:
        status = connection.execute("SELECT status FROM assets WHERE asset_id = 'ASSET-1002'").fetchone()[0]
    finally:
        connection.close()
    assert status == "in_stock"


def test_approval_executes_once_and_writes_ticket_and_reservation(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    before = ticket_count(env.settings.db_path)
    result = approve_proposal(
        env.repository,
        proposal_id=proposal["proposal_id"],
        approver="bob",
        approver_department="研发中心",
        is_admin=False,
        expected_hash=proposal["params_hash"],
    )
    assert result["ticket_id"].startswith("INC-2026-")
    assert ticket_count(env.settings.db_path) == before + 1
    task = env.repository.get_task(task_id)
    assert task["status"] == "completed"
    final = json.loads(task["final_json"])
    assert final["execution"]["asset_id"] == "ASSET-1002"
    reservations = env.repository.list_reservations()
    assert len(reservations) == 1
    connection = sqlite3.connect(env.settings.db_path)
    try:
        status = connection.execute("SELECT status FROM assets WHERE asset_id = 'ASSET-1002'").fetchone()[0]
    finally:
        connection.close()
    assert status == "reserved"

    # 重复批准：返回原结果，不重复写入
    again = approve_proposal(
        env.repository,
        proposal_id=proposal["proposal_id"],
        approver="bob",
        approver_department="研发中心",
        is_admin=False,
        expected_hash=proposal["params_hash"],
    )
    assert again == result
    assert ticket_count(env.settings.db_path) == before + 1
    assert len(env.repository.list_reservations()) == 1


def test_requester_cannot_approve_own_request(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="alice",
            approver_department="研发中心",
            is_admin=False,
            expected_hash=proposal["params_hash"],
        )
    assert excinfo.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_wrong_department_cannot_approve(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    # 手工把提案部门改为研发中心，模拟跨部门审批
    connection = sqlite3.connect(env.settings.db_path)
    try:
        connection.execute(
            "UPDATE wb_proposals SET department = '研发中心' WHERE proposal_id = ?", (proposal["proposal_id"],)
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="carol",
            approver_department="财务部",
            is_admin=False,
            expected_hash=proposal["params_hash"],
        )
    assert excinfo.value.code == "FORBIDDEN_DEPARTMENT"


def test_params_hash_mismatch_rejected(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    with pytest.raises(WorkflowError) as excinfo:
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="bob",
            approver_department="研发中心",
            is_admin=False,
            expected_hash="deadbeef",
        )
    assert excinfo.value.code == "PARAMS_CHANGED"
    assert env.repository.get_task(task_id)["status"] == "needs_approval"


def test_reject_prevents_execution(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    task_id = full_task(env)
    proposal = env.repository.get_proposal_by_task(task_id)
    before = ticket_count(env.settings.db_path)
    reject_proposal(
        env.repository,
        proposal_id=proposal["proposal_id"],
        approver="bob",
        approver_department="研发中心",
        is_admin=False,
        reason="材料不全",
    )
    assert env.repository.get_task(task_id)["status"] == "rejected"
    assert ticket_count(env.settings.db_path) == before
    with pytest.raises(WorkflowError):
        approve_proposal(
            env.repository,
            proposal_id=proposal["proposal_id"],
            approver="bob",
            approver_department="研发中心",
            is_admin=False,
            expected_hash=proposal["params_hash"],
        )


def test_no_proposal_when_audit_not_pass(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue("policy", tool_call("search_knowledge", {"query": "领用"}), policy_findings())
    env.fake.queue("auditor", audit_result("needs_evidence", summary="证据不足"))
    task_id = env.system.run("新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑")
    assert env.repository.get_task(task_id)["status"] == "partial"
    assert env.repository.get_proposal_by_task(task_id) is None


def test_no_proposal_when_policy_is_draft(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue(
        "policy",
        tool_call("search_knowledge", {"query": "显示器"}),
        policy_findings(status="草稿"),
    )
    env.fake.queue("auditor", audit_result("pass"))
    task_id = env.system.run("新员工 EMP-1012 申请一台 ThinkPad T14 Gen5 笔记本电脑")
    assert env.repository.get_proposal_by_task(task_id) is None


def test_no_proposal_when_stock_shortage(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue("policy", tool_call("search_knowledge", {"query": "领用"}), policy_findings())
    env.fake.queue("auditor", audit_result("pass"))
    task_id = env.system.run("新员工 EMP-1013 申请一台 ThinkPad L14 笔记本电脑")
    assert env.repository.get_proposal_by_task(task_id) is None


def test_no_proposal_when_employee_missing(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
    env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
    env.fake.queue("policy", tool_call("search_knowledge", {"query": "领用"}), policy_findings())
    env.fake.queue("auditor", audit_result("pass"))
    task_id = env.system.run("新员工 EMP-9999 申请一台 ThinkPad T14 Gen5 笔记本电脑")
    assert env.repository.get_proposal_by_task(task_id) is None


def test_concurrent_approval_only_one_reserves_single_asset(tmp_path: Path) -> None:
    env = build_env(tmp_path)
    # 两个任务都申请只有 1 台库存的 X1 Carbon
    task_ids = []
    for _ in range(2):
        env.fake.queue("planner", plan([("business", "查库存"), ("policy", "查制度")], audit=True))
        env.fake.queue("business", tool_call("query_inventory", {}), business_findings())
        env.fake.queue("policy", tool_call("search_knowledge", {"query": "领用"}), policy_findings())
        env.fake.queue("auditor", audit_result("pass"))
        task_ids.append(
            env.system.run("新员工 EMP-1012 申请一台 MacBook Air 13 笔记本电脑", owner="alice", department="研发中心")
        )
    proposals = [env.repository.get_proposal_by_task(task_id) for task_id in task_ids]
    assert all(proposal is not None for proposal in proposals)
    before = ticket_count(env.settings.db_path)
    results = []
    errors = []

    def approve(proposal):
        try:
            results.append(
                approve_proposal(
                    env.repository,
                    proposal_id=proposal["proposal_id"],
                    approver="bob",
                    approver_department="研发中心",
                    is_admin=False,
                    expected_hash=proposal["params_hash"],
                )
            )
        except WorkflowError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=approve, args=(proposal,)) for proposal in proposals]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 1
    assert len(errors) == 1
    assert len(env.repository.list_reservations()) == 1
    assert ticket_count(env.settings.db_path) == before + 1
