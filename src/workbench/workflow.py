"""受控业务工作流：提案生成、人工审批、事务执行。

模型只产出结论与建议；提案参数与业务写入都由本模块的确定性代码完成。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .knowledge import KnowledgeStore
from .repository import Repository
from .store import DeskStore

ACTION_KEYWORDS = ("申请", "领用", "预留", "配一台", "配电脑", "发放", "新员工")
DEVICE_KEYWORDS = ("电脑", "笔记本", "设备", "ThinkPad", "MacBook")
EMPLOYEE_RE = re.compile(r"EMP-\d{4}")
STANDARD_MODEL = {"研发中心": "ThinkPad T14 Gen5", "IT 运维": "ThinkPad T14 Gen5"}
DEFAULT_MODEL = "ThinkPad L14"
ALLOWED_MODELS = {
    "研发中心": {"ThinkPad T14 Gen5", "MacBook Air 13"},
    "产品部": {"MacBook Air 13", "ThinkPad X1 Carbon"},
    "设计部": {"MacBook Air 13", "ThinkPad X1 Carbon"},
    "IT 运维": {"ThinkPad T14 Gen5", "ThinkPad L14"},
}


class WorkflowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def is_actionable(request: str) -> bool:
    return any(word in request for word in ACTION_KEYWORDS) and any(word in request for word in DEVICE_KEYWORDS)


def _employee(connection: sqlite3.Connection, employee_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()


def _available_assets(connection: sqlite3.Connection, model: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM assets WHERE model = ? AND status = 'in_stock' ORDER BY asset_id", (model,)
    ).fetchall()


def choose_model(request: str, department: str) -> str:
    for model in ("ThinkPad T14 Gen5", "MacBook Air 13", "ThinkPad X1 Carbon", "ThinkPad L14"):
        if model in request:
            return model
    return STANDARD_MODEL.get(department, DEFAULT_MODEL)


def _block_proposal(repository: Repository, task_id: str, reason: str, evidence: list[str] | None = None) -> None:
    repository.add_event(
        task_id,
        "proposal_blocked",
        {"reason": reason, "evidence": evidence or []},
    )


def maybe_create_proposal(
    *,
    repository: Repository,
    store: DeskStore,
    knowledge: KnowledgeStore,
    task: dict[str, Any],
    business_output: Any,
    policy_output: Any,
    audit_verdict: str,
    request: str,
) -> dict[str, Any] | None:
    """只有 audit=pass、制度现行、员工无设备、库存可满足时才生成提案。

    制度状态以知识库元数据为权威，模型输出的 status 只作为待核对字段。
    """
    if audit_verdict != "pass" or not is_actionable(request):
        return None
    if business_output is None or policy_output is None or not policy_output.rules:
        _block_proposal(repository, task["id"], "缺少业务或制度结论")
        return None

    evidence_ids = [eid for rule in policy_output.rules for eid in getattr(rule, "evidence_ids", [])]
    metadata = knowledge.document_metadata(evidence_ids)
    for rule in policy_output.rules:
        model_status = getattr(rule, "status", "") or ""
        if any(token in model_status for token in ("废止", "草稿", "未审核", "superseded", "draft")):
            _block_proposal(repository, task["id"], f"模型标注非现行制度：{rule.citation}")
            return None
        if not rule.evidence_ids:
            _block_proposal(repository, task["id"], f"制度条款未绑定证据：{rule.citation}")
            return None
        for evidence_id in rule.evidence_ids:
            meta = metadata.get(evidence_id)
            if meta is None:
                _block_proposal(repository, task["id"], f"制度证据不存在：{evidence_id}", [evidence_id])
                return None
            if meta["doc_id"] != rule.doc_id or meta["version"] != rule.version:
                _block_proposal(
                    repository,
                    task["id"],
                    f"模型制度元数据与知识库不一致：{rule.doc_id} {rule.version}",
                    [evidence_id],
                )
                return None
            if not meta["is_current"]:
                _block_proposal(
                    repository,
                    task["id"],
                    f"制度证据非现行：{meta['doc_id']} {meta['status']}",
                    [evidence_id],
                )
                return None

    employee_match = EMPLOYEE_RE.search(request)
    if not employee_match:
        _block_proposal(repository, task["id"], "缺少有效员工编号")
        return None
    employee_id = employee_match.group(0)

    connection = _connect(repository.db_path)
    try:
        employee = _employee(connection, employee_id)
        if employee is None:
            _block_proposal(repository, task["id"], f"员工不存在：{employee_id}", [employee_id])
            return None
        department = employee["department"]
        task_department = (task.get("department") or "").strip()
        if not task_department or department != task_department:
            _block_proposal(
                repository,
                task["id"],
                "目标员工部门与申请部门不一致："
                f"{employee_id} 属于 {department}，申请部门为 {task_department or '未绑定'}",
                [employee_id, department],
            )
            return None
        model = choose_model(request, department)
        allowed = ALLOWED_MODELS.get(department, {"ThinkPad L14"})
        if model not in allowed:
            _block_proposal(repository, task["id"], f"机型不属于部门标准配置：{department} {model}", [employee_id])
            return None
        held = connection.execute(
            "SELECT asset_id FROM assets WHERE assignee_id = ? AND status IN ('in_use', 'reserved')",
            (employee_id,),
        ).fetchall()
        reserved = connection.execute(
            "SELECT asset_id FROM wb_reservations WHERE employee_id = ? AND released_at IS NULL",
            (employee_id,),
        ).fetchall()
        if held or reserved:
            assets = sorted({row["asset_id"] for row in held} | {row["asset_id"] for row in reserved})
            _block_proposal(
                repository,
                task["id"],
                f"员工已持有设备或存在有效预留：{employee_id}",
                assets,
            )
            return None
        assets = _available_assets(connection, model)
        if not assets:
            _block_proposal(repository, task["id"], f"库存不足：{model}", [employee_id])
            return None
        asset = assets[0]
        params = {
            "action": "provision_device",
            "task_id": task["id"],
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "department": department,
            "category": "笔记本电脑",
            "model": model,
            "asset_id": asset["asset_id"],
            "priority": "P3",
            "title": f"新员工设备申请 - {employee['name']}",
            "description": f"{department} {employee['name']}（{employee_id}）申请 {model}，由入职工作流生成。",
        }
        digest = params_hash(params)
        proposal_id = repository.create_proposal(
            task_id=task["id"],
            actions=[params],
            params_hash=digest,
            requested_by=task["owner"],
            department=department,
        )
    finally:
        connection.close()

    repository.add_event(
        task["id"],
        "proposal_ready",
        {"proposal_id": proposal_id, "asset_id": params["asset_id"], "model": model},
    )
    return {
        "proposal_id": proposal_id,
        "status": "pending",
        "version": 1,
        "params_hash": digest,
        "actions": [params],
    }


def _next_ticket_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT ticket_id FROM tickets WHERE ticket_id LIKE 'INC-2026-%' ORDER BY ticket_id DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            number = int(row["ticket_id"].rsplit("-", 1)[1]) + 1
        except ValueError:
            number = 1
    else:
        number = 1
    return f"INC-2026-{number:04d}"


def execute_proposal(repository: Repository, proposal: dict[str, Any]) -> dict[str, Any]:
    """事务执行：预留资产 + 创建工单；失败整体回滚，不产生半成品。"""
    actions = json.loads(proposal["actions_json"])
    if len(actions) != 1 or actions[0].get("action") != "provision_device":
        raise WorkflowError("UNSUPPORTED_ACTION", "提案动作不受支持")
    params = actions[0]
    if params_hash(params) != proposal["params_hash"]:
        raise WorkflowError("PROPOSAL_TAMPERED", "提案参数与哈希不一致")
    asset_id = params["asset_id"]
    task_id = proposal["task_id"]
    connection = _connect(repository.db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        guard = connection.execute(
            "UPDATE wb_proposals SET status = 'executing' WHERE proposal_id = ? AND status = 'approved'",
            (proposal["proposal_id"],),
        )
        if guard.rowcount != 1:
            connection.rollback()
            raise WorkflowError("PROPOSAL_NOT_APPROVED", "提案不存在或未批准")
        existing = connection.execute(
            "SELECT asset_id FROM assets WHERE assignee_id = ? AND status IN ('in_use', 'reserved')",
            (params["employee_id"],),
        ).fetchall()
        reserved = connection.execute(
            "SELECT asset_id FROM wb_reservations WHERE employee_id = ? AND released_at IS NULL",
            (params["employee_id"],),
        ).fetchall()
        if existing or reserved:
            connection.rollback()
            raise WorkflowError("EMPLOYEE_ALREADY_HAS_DEVICE", "该员工已持有设备或存在有效预留")
        asset = connection.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
        if asset is None or asset["status"] != "in_stock":
            connection.rollback()
            raise WorkflowError("ASSET_UNAVAILABLE", "资产已被占用或不存在")
        updated = connection.execute(
            "UPDATE assets SET status = 'reserved', assignee_id = ? WHERE asset_id = ? AND status = 'in_stock'",
            (params["employee_id"], asset_id),
        )
        if updated.rowcount != 1:
            connection.rollback()
            raise WorkflowError("ASSET_CONFLICT", "资产已被其他申请预留")
        connection.execute(
            "INSERT INTO wb_reservations (asset_id, task_id, employee_id, created_at) VALUES (?, ?, ?, ?)",
            (asset_id, task_id, params["employee_id"], datetime.now(UTC).isoformat(timespec="seconds")),
        )
        ticket_id = _next_ticket_id(connection)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO tickets (ticket_id, title, description, category, priority, status, requester_id, "
            "assignee_id, created_at, updated_at, comments_json) "
            "VALUES (?, ?, ?, 'hardware', 'P3', 'open', ?, NULL, ?, ?, '[]')",
            (ticket_id, params["title"], params["description"], params["employee_id"], now, now),
        )
        result = {"ticket_id": ticket_id, "asset_id": asset_id, "employee_id": params["employee_id"]}
        connection.execute(
            "UPDATE wb_proposals SET status = 'executed', executed_at = ?, result_json = ?, error = NULL "
            "WHERE proposal_id = ?",
            (now, json.dumps(result, ensure_ascii=False), proposal["proposal_id"]),
        )
        connection.execute("UPDATE collab_tasks SET status = 'completed', updated_at = ? WHERE id = ?", (now, task_id))
        connection.commit()
    except WorkflowError:
        raise
    except Exception as exc:
        connection.rollback()
        raise WorkflowError("EXECUTION_FAILED", f"执行失败：{exc.__class__.__name__}") from exc
    finally:
        connection.close()

    task = repository.get_task(task_id)
    final = json.loads(task["final_json"]) if task.get("final_json") else {}
    final["execution"] = result
    final["status"] = "completed"
    repository.update_task(task_id, status="completed", final_json=json.dumps(final, ensure_ascii=False))
    repository.add_event(task_id, "proposal_executed", result)
    return result


def approve_proposal(
    repository: Repository,
    *,
    proposal_id: str,
    approver: str,
    approver_department: str,
    is_admin: bool,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    proposal = repository.get_proposal(proposal_id)
    task = repository.get_task(proposal["task_id"])
    if proposal["requested_by"] == approver:
        raise WorkflowError("SELF_APPROVAL_FORBIDDEN", "请求人不能批准自己的申请")
    if not is_admin and (not approver_department.strip() or proposal["department"] != approver_department):
        raise WorkflowError("FORBIDDEN_DEPARTMENT", "无权审批其他部门的申请")
    if not expected_hash:
        raise WorkflowError("PARAMS_HASH_REQUIRED", "批准必须提供参数哈希")
    if expected_hash != proposal["params_hash"]:
        raise WorkflowError("PARAMS_CHANGED", "提案参数已变化，请重新审核")
    actions = json.loads(proposal["actions_json"])
    if not actions or params_hash(actions[0]) != proposal["params_hash"]:
        raise WorkflowError("PROPOSAL_TAMPERED", "提案参数与哈希不一致")
    if proposal["status"] == "executed":
        return json.loads(proposal["result_json"] or "{}")
    if proposal["status"] != "pending":
        raise WorkflowError("PROPOSAL_NOT_PENDING", f"提案状态为 {proposal['status']}，不能批准")
    if task["status"] != "needs_approval":
        raise WorkflowError("TASK_NOT_PENDING_APPROVAL", "任务不在待审批状态")

    now = datetime.now(UTC).isoformat(timespec="seconds")
    connection = repository._connect()
    try:
        cursor = connection.execute(
            "UPDATE wb_proposals SET status = 'approved', decided_by = ?, decided_at = ? "
            "WHERE proposal_id = ? AND status = 'pending'",
            (approver, now, proposal_id),
        )
        connection.commit()
    finally:
        connection.close()
    if cursor.rowcount != 1:
        raise WorkflowError("PROPOSAL_ALREADY_DECIDED", "提案已被处理")
    repository.update_task(proposal["task_id"], status="approved")
    repository.add_event(proposal["task_id"], "proposal_approved", {"proposal_id": proposal_id, "by": approver})
    try:
        return execute_proposal(repository, repository.get_proposal(proposal_id))
    except WorkflowError as exc:
        repository.update_proposal(proposal_id, status="failed", error=exc.message)
        repository.update_task(proposal["task_id"], status="failed", error=f"{exc.code}: 执行失败")
        repository.add_event(proposal["task_id"], "proposal_failed", {"proposal_id": proposal_id, "code": exc.code})
        raise


def reject_proposal(
    repository: Repository,
    *,
    proposal_id: str,
    approver: str,
    approver_department: str,
    is_admin: bool,
    reason: str = "",
) -> None:
    proposal = repository.get_proposal(proposal_id)
    if proposal["requested_by"] == approver:
        raise WorkflowError("SELF_APPROVAL_FORBIDDEN", "请求人不能拒绝自己的申请")
    if not is_admin and (not approver_department.strip() or proposal["department"] != approver_department):
        raise WorkflowError("FORBIDDEN_DEPARTMENT", "无权审批其他部门的申请")
    if proposal["status"] == "executed":
        raise WorkflowError("ALREADY_EXECUTED", "提案已执行，不能拒绝")
    if proposal["status"] != "pending":
        raise WorkflowError("PROPOSAL_NOT_PENDING", "提案不在待审批状态")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    connection = repository._connect()
    try:
        cursor = connection.execute(
            "UPDATE wb_proposals SET status = 'rejected', decided_by = ?, decided_at = ?, error = ? "
            "WHERE proposal_id = ? AND status = 'pending'",
            (approver, now, reason[:300], proposal_id),
        )
        connection.commit()
    finally:
        connection.close()
    if cursor.rowcount != 1:
        raise WorkflowError("PROPOSAL_ALREADY_DECIDED", "提案已被处理")
    repository.update_task(proposal["task_id"], status="rejected")
    repository.add_event(proposal["task_id"], "proposal_rejected", {"proposal_id": proposal_id, "by": approver})


# 简单的进程内串行化，配合 SQLite BEGIN IMMEDIATE 防止同一提案并发执行。
_EXECUTE_LOCK = threading.Lock()


def approve_serialized(**kwargs: Any) -> dict[str, Any]:
    with _EXECUTE_LOCK:
        return approve_proposal(**kwargs)


def evidence_valid(knowledge: KnowledgeStore, evidence_ids: list[str]) -> set[str]:
    return knowledge.find_evidence(evidence_ids)
