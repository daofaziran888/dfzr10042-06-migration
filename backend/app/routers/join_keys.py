"""关联键版本清单与报告聚合接口。

* ``GET  /api/join-keys/manifests``        版本清单
* ``POST /api/join-keys/migrate``          存量方案迁移
* ``POST /api/join-keys/versions/{id}/review``   审核新类型版本
* ``POST /api/join-keys/versions/{id}/enable``   启用新类型版本
* ``POST /api/join-keys/versions/{id}/disable``  停用并回退旧版本
* ``POST /api/join-keys/aggregate``        按版本真实匹配并汇总金额
* ``GET  /api/join-keys/runs``             实际运行记录
* ``GET  /api/join-keys/reports``          已签发报告（冻结值）
* ``POST /api/join-keys/reports``          签发报告
* ``POST /api/join-keys/reports/{id}/recompute`` 按原范围重算并校验
* ``GET  /api/join-keys/drafts``           复核草稿
* ``POST /api/join-keys/drafts``           领用结果生成草稿
* ``POST /api/join-keys/drafts/{id}/submit`` 确认续算 / 授权更正（幂等）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import join_versions as jk
from app.join_versions import DomainError

router = APIRouter(prefix="/api/join-keys", tags=["关联键版本"])


class MigratePayload(BaseModel):
    plan_id: str | None = None
    review: bool = False


class ReviewPayload(BaseModel):
    decision: str = "approve"
    reviewer: str = "reviewer"


class AggregatePayload(BaseModel):
    plan_id: str
    version_id: str | None = None
    scope: list[str] | None = None
    report_id: str | None = None


class IssueReportPayload(BaseModel):
    plan_id: str
    version_id: str | None = None
    scope: list[str] | None = None
    report_id: str | None = None


class DraftPayload(BaseModel):
    plan_id: str = "DATA-JK-001"
    version_id: str | None = None
    scope: list[str] | None = None
    report_id: str = ""
    draft_id: str | None = None


class DraftSubmitPayload(BaseModel):
    mode: str = "continue"
    version_id: str | None = None
    authorized_by: str = ""


class EnablePayload(BaseModel):
    force: bool = Field(default=False, description="保留字段，当前启用不允许跳过审核")


@router.get("/manifests")
def list_manifests() -> dict[str, Any]:
    return jk.scenario.list_manifests()


@router.post("/migrate")
def migrate(payload: MigratePayload | None = None) -> dict[str, Any]:
    payload = payload or MigratePayload()
    try:
        return jk.scenario.migrate(payload.plan_id, review=payload.review)
    except DomainError as exc:
        return {"ok": False, "message": str(exc), "records": []}


@router.post("/versions/{version_id}/review")
def review_version(version_id: str, payload: ReviewPayload | None = None) -> dict[str, Any]:
    payload = payload or ReviewPayload()
    return jk.scenario.review_version(
        version_id, decision=payload.decision, reviewer=payload.reviewer
    )


@router.post("/versions/{version_id}/enable")
def enable_version(version_id: str, payload: EnablePayload | None = None) -> dict[str, Any]:
    return jk.scenario.enable_version(version_id)


@router.post("/versions/{version_id}/disable")
def disable_version(version_id: str) -> dict[str, Any]:
    return jk.scenario.disable_version(version_id)


@router.post("/aggregate")
def aggregate(payload: AggregatePayload) -> dict[str, Any]:
    try:
        return jk.scenario.aggregate(
            payload.plan_id,
            version_id=payload.version_id,
            scope=payload.scope,
            report_id=payload.report_id,
        )
    except DomainError as exc:
        return {"ok": False, "message": str(exc), "amount": None}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    return jk.scenario.list_runs()


@router.get("/reports")
def list_reports() -> dict[str, Any]:
    return jk.scenario.list_reports()


@router.post("/reports")
def issue_report(payload: IssueReportPayload) -> dict[str, Any]:
    try:
        return jk.scenario.issue_report(
            payload.plan_id,
            version_id=payload.version_id,
            scope=payload.scope,
            report_id=payload.report_id,
        )
    except DomainError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/reports/{report_id}/recompute")
def recompute_report(report_id: str) -> dict[str, Any]:
    return jk.scenario.recompute_report(report_id)


@router.get("/drafts")
def list_drafts() -> dict[str, Any]:
    return jk.scenario.list_drafts()


@router.post("/drafts")
def create_draft(payload: DraftPayload | None = None) -> dict[str, Any]:
    payload = payload or DraftPayload()
    return jk.scenario.create_draft(
        plan_id=payload.plan_id,
        version_id=payload.version_id,
        scope=payload.scope,
        report_id=payload.report_id,
        draft_id=payload.draft_id,
    )


@router.post("/drafts/{draft_id}/submit")
def submit_draft(draft_id: str, payload: DraftSubmitPayload | None = None) -> dict[str, Any]:
    payload = payload or DraftSubmitPayload()
    try:
        return jk.scenario.submit_draft(
            draft_id,
            mode=payload.mode,
            version_id=payload.version_id,
            authorized_by=payload.authorized_by,
        )
    except DomainError as exc:
        return {"ok": False, "confirmed_now": False, "message": str(exc)}


@router.post("/reset")
def reset() -> dict[str, Any]:
    jk.scenario.reset(auto_migrate=True)
    return {"ok": True, "message": "场景已重置并重新迁移"}
