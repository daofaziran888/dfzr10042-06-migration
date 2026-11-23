"""关联键版本清单与报告聚合。

背景
====
存量 ``dataset_join`` 方案在匹配关联键时一律把两侧值转成字符串再比较，
因此「数字 7」和「文本 7」会被当成同一个键。新契约要求保留关联键的原始
类型，只有类型与值都相同的键才算匹配。

本模块把旧方案迁移成一份*关联键版本清单*（``JoinKeyManifest``）：

* ``V1``：旧契约，字符串匹配，状态固定为「保留」，永远可以按旧范围重算；
* ``V2``：新契约，保留原始键类型，必须经过「审核」后才能用于报告聚合；
  类型证据不明的旧记录标记为「待核」，不能审核、不能启用。

报告聚合（``aggregate``）不是读预先写死的结果，而是按清单所指的版本
真实执行关联匹配，再把匹配到的明细行金额加总。已签发报告在签发时冻结
了版本、匹配明细和金额，重算时仍按原范围回放，不会因为新版本启用而变化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterable


# ============================================================
# 常量 / 基础工具
# ============================================================

SEMANTICS_STRING = "string"
SEMANTICS_TYPED = "typed"

STATUS_LEGACY_KEPT = "保留"
STATUS_PENDING_REVIEW = "待审核"
STATUS_PENDING_EVIDENCE = "待核"
STATUS_REVIEWED = "已审核"
STATUS_ENABLED = "已启用"
STATUS_DISABLED = "已停用"

DRAFT_CLAIMED = "已领旧结果"
DRAFT_CONFIRMED = "已确认"
DRAFT_CORRECTED = "已更正"

ELIGIBLE_TYPED_STATUSES = {STATUS_REVIEWED, STATUS_ENABLED}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def key_type_of(value: Any) -> str:
    """返回关联键的原始类型名。

    ``bool`` 必须排在 ``int`` 前面判断；``None`` 视为无类型证据。
    """
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "text"
    return "unknown"


def string_key(value: Any) -> str:
    """旧契约：统一转字符串后匹配。"""
    return str(value)


def typed_match(left: Any, right: Any) -> bool:
    """新契约：原始类型相同且值相等才算匹配。"""
    left_type = key_type_of(left)
    right_type = key_type_of(right)
    if left_type in {"unknown", "bool"} or right_type in {"unknown", "bool"}:
        return False
    if left_type != right_type:
        return False
    return left == right


def matches(left: Any, right: Any, semantics: str) -> bool:
    if semantics == SEMANTICS_STRING:
        return string_key(left) == string_key(right)
    if semantics == SEMANTICS_TYPED:
        return typed_match(left, right)
    raise ValueError(f"未知的关联键匹配语义：{semantics}")


def _round_amount(value: float) -> float:
    # 金额按分保留两位，避免浮点尾差影响报告展示。
    return round(float(value) + 0.0, 2)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class JoinKeyVersion:
    version_id: str
    plan_id: str
    semantics: str
    key_type: str
    evidence: str  # legacy / declared / missing / conflict
    status: str
    contract: str
    created_at: str
    reviewed_by: str = ""
    reviewed_at: str = ""
    enabled_at: str = ""
    notes: str = ""

    @property
    def reviewed(self) -> bool:
        return self.status in {STATUS_REVIEWED, STATUS_ENABLED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "plan_id": self.plan_id,
            "semantics": self.semantics,
            "contract": self.contract,
            "key_type": self.key_type,
            "evidence": self.evidence,
            "status": self.status,
            "reviewed": self.reviewed,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "enabled_at": self.enabled_at,
            "created_at": self.created_at,
            "notes": self.notes,
        }


@dataclass
class JoinKeyManifest:
    manifest_id: str
    plan_id: str
    key_field: str
    versions: list[JoinKeyVersion] = field(default_factory=list)
    active_version_id: str = ""
    migrated_at: str = ""

    def version(self, version_id: str) -> JoinKeyVersion | None:
        for version in self.versions:
            if version.version_id == version_id:
                return version
        return None

    def legacy_version(self) -> JoinKeyVersion | None:
        for version in self.versions:
            if version.semantics == SEMANTICS_STRING:
                return version
        return None

    def typed_version(self) -> JoinKeyVersion | None:
        for version in self.versions:
            if version.semantics == SEMANTICS_TYPED:
                return version
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "plan_id": self.plan_id,
            "key_field": self.key_field,
            "active_version_id": self.active_version_id,
            "migrated_at": self.migrated_at,
            "versions": [version.to_dict() for version in self.versions],
        }


@dataclass
class IssuedReport:
    report_id: str
    plan_id: str
    version_id: str
    scope: list[str]
    amount: float
    matched_order_ids: list[str]
    matched_detail: list[dict[str, Any]]
    issued_at: str
    status: str = "已签"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "version_id": self.version_id,
            "scope": list(self.scope),
            "amount": self.amount,
            "matched_order_ids": list(self.matched_order_ids),
            "matched_detail": [dict(row) for row in self.matched_detail],
            "issued_at": self.issued_at,
            "status": self.status,
        }


@dataclass
class ReviewDraft:
    draft_id: str
    plan_id: str
    report_id: str
    bound_version_id: str
    scope: list[str]
    claimed_amount: float | None
    claimed_at: str
    state: str = DRAFT_CLAIMED
    confirmation_count: int = 0
    result: dict[str, Any] | None = None
    authorized_by: str = ""
    decided_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def decided(self) -> bool:
        return self.state in {DRAFT_CONFIRMED, DRAFT_CORRECTED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "plan_id": self.plan_id,
            "report_id": self.report_id,
            "bound_version_id": self.bound_version_id,
            "scope": list(self.scope),
            "claimed_amount": self.claimed_amount,
            "claimed_at": self.claimed_at,
            "state": self.state,
            "confirmation_count": self.confirmation_count,
            "authorized_by": self.authorized_by,
            "decided_at": self.decided_at,
            "result": dict(self.result) if self.result else None,
            "history": [dict(item) for item in self.history],
        }


class DomainError(Exception):
    """可直接转成 ``{ok: false, message: ...}`` 的业务错误。"""


# ============================================================
# 场景数据
# ============================================================

# 主场景：两个订单，金额 50 + 30 = 80。
# 订单侧键是数字（int 7 / int 9），会员明细侧同时存在文本 "7" 与数字 9。
#   旧契约（字符串）：7→"7" 命中、9→9 命中，合计 80；
#   新契约（保留类型）：int 7 ≠ text "7" 不命中，int 9 = int 9 命中，合计 30。
SCENARIO_DATASETS: dict[str, list[dict[str, Any]]] = {
    "orders": [
        {"order_id": "O-1001", "member_key": 7, "amount": 50.0, "status": "已支付"},
        {"order_id": "O-1002", "member_key": 9, "amount": 30.0, "status": "已支付"},
    ],
    "members": [
        {"member_key": "7", "member_name": "会员甲", "region": "华东"},
        {"member_key": 9, "member_name": "会员乙", "region": "华南"},
    ],
    # 无类型证据的存量订单：键全是「看起来像数字的文本」，口径待核。
    "legacy_orders": [
        {"order_id": "L-2001", "member_key": "7", "amount": 15.0, "status": "已支付"},
        {"order_id": "L-2002", "member_key": "9", "amount": 5.0, "status": "已支付"},
    ],
    "legacy_members": [
        {"member_key": "7", "member_name": "历史会员甲", "region": "华东"},
        {"member_key": "9", "member_name": "历史会员乙", "region": "华南"},
    ],
    # 同一主数据集内数字 7 与文本 7 混用：新契约拒绝一次聚合混两种键。
    "mixed_orders": [
        {"order_id": "M-3001", "member_key": 7, "amount": 40.0, "status": "已支付"},
        {"order_id": "M-3002", "member_key": "7", "amount": 20.0, "status": "已支付"},
        {"order_id": "M-3003", "member_key": 9, "amount": 10.0, "status": "已支付"},
    ],
    "mixed_members": [
        {"member_key": "7", "member_name": "混用会员甲", "region": "华东"},
        {"member_key": 9, "member_name": "混用会员乙", "region": "华南"},
    ],
}


SCENARIO_PLANS: dict[str, dict[str, Any]] = {
    "DATA-JK-001": {
        "plan_id": "DATA-JK-001",
        "plan_name": "订单-会员关联（明确类型存量方案）",
        "primary_dataset": "orders",
        "join_dataset": "members",
        "join_key": "member_key",
        "amount_field": "amount",
        "row_id_field": "order_id",
        "declared_key_type": "int",
        "evidence": "declared",
        "scope": ["O-1001", "O-1002"],
    },
    "DATA-JK-002": {
        "plan_id": "DATA-JK-002",
        "plan_name": "历史订单-会员关联（无类型证据）",
        "primary_dataset": "legacy_orders",
        "join_dataset": "legacy_members",
        "join_key": "member_key",
        "amount_field": "amount",
        "row_id_field": "order_id",
        "declared_key_type": None,
        "evidence": "missing",
        "scope": ["L-2001", "L-2002"],
    },
    "DATA-JK-003": {
        "plan_id": "DATA-JK-003",
        "plan_name": "混用键关联（类型冲突）",
        "primary_dataset": "mixed_orders",
        "join_dataset": "mixed_members",
        "join_key": "member_key",
        "amount_field": "amount",
        "row_id_field": "order_id",
        "declared_key_type": "int",
        "evidence": "declared",
        "scope": ["M-3001", "M-3002", "M-3003"],
    },
}


# ============================================================
# 场景服务
# ============================================================

class JoinKeyScenario:
    """内存中的迁移、报告聚合和复核草稿服务。

    所有写操作都在同一把 ``RLock`` 内完成；复核草稿的并发提交因此只会
    成功确认一次，其余请求拿回同一个已确认结果。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.datasets: dict[str, list[dict[str, Any]]] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.manifests: dict[str, JoinKeyManifest] = {}
        self.reports: dict[str, IssuedReport] = {}
        self.drafts: dict[str, ReviewDraft] = {}
        self.runs: list[dict[str, Any]] = []
        self._run_seq = 0
        self._draft_seq = 0
        self.reset(auto_migrate=False)

    # ---------- 生命周期 ----------

    def reset(self, *, auto_migrate: bool = False) -> None:
        with self._lock:
            self.datasets = {
                name: [dict(row) for row in rows]
                for name, rows in SCENARIO_DATASETS.items()
            }
            self.plans = {
                plan_id: {
                    **plan,
                    "scope": list(plan["scope"]),
                }
                for plan_id, plan in SCENARIO_PLANS.items()
            }
            self.manifests = {}
            self.reports = {}
            self.drafts = {}
            self.runs = []
            self._run_seq = 0
            self._draft_seq = 0

            # 草稿先于迁移产生：它已经领用了旧契约结果。
            seed_draft = self._create_draft_locked(
                plan_id="DATA-JK-001",
                version_id="DATA-JK-001-V1",
                scope=["O-1001", "O-1002"],
                draft_id="DRF-SEED-001",
            )
            seed_draft.claimed_amount = 80.0
            seed_draft.state = DRAFT_CLAIMED
            seed_draft.history.append({
                "at": seed_draft.claimed_at,
                "action": "领用旧契约结果",
                "version_id": "DATA-JK-001-V1",
                "amount": 80.0,
            })

            # 旧契约已经签发过的报告：冻结匹配明细和 80 元金额。
            legacy_detail = self._legacy_detail_locked(
                self.plans["DATA-JK-001"], ["O-1001", "O-1002"]
            )
            self.reports["RPT-LEGACY-001"] = IssuedReport(
                report_id="RPT-LEGACY-001",
                plan_id="DATA-JK-001",
                version_id="DATA-JK-001-V1",
                scope=["O-1001", "O-1002"],
                amount=_round_amount(
                    sum(row["amount"] for row in legacy_detail)
                ),
                matched_order_ids=[row["order_id"] for row in legacy_detail],
                matched_detail=legacy_detail,
                issued_at="2026-11-18T09:00:00+00:00",
                status="已签",
            )
            seed_draft.report_id = "RPT-LEGACY-001"

            if auto_migrate:
                self._migrate_locked(list(self.plans))

    # ---------- 迁移 ----------

    def migrate(
        self, plan_id: str | None = None, *, review: bool = False
    ) -> dict[str, Any]:
        with self._lock:
            plan_ids = [plan_id] if plan_id else list(self.plans)
            records = self._migrate_locked(plan_ids)
            if review:
                for record in records:
                    typed_id = record.get("typed_version_id")
                    if typed_id:
                        try:
                            self._review_locked(typed_id, reviewer="migration")
                        except DomainError:
                            pass
                records = [
                    self._migration_record_locked(pid) for pid in plan_ids
                ]
            return {
                "ok": True,
                "message": f"已迁移 {len(records)} 个存量关联方案",
                "records": records,
                "migrated_at": _now(),
            }

    def _migrate_locked(self, plan_ids: Iterable[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for plan_id in plan_ids:
            plan = self._require_plan_locked(plan_id)
            manifest = self.manifests.get(plan_id)
            if manifest is None:
                manifest = self._build_manifest_locked(plan)
                self.manifests[plan_id] = manifest
            records.append(self._migration_record_locked(plan_id))
        return records

    def _build_manifest_locked(self, plan: dict[str, Any]) -> JoinKeyManifest:
        plan_id = plan["plan_id"]
        key_field = plan["join_key"]
        primary_rows = self._scope_rows_locked(plan, plan["scope"])

        observed_types = sorted({
            key_type_of(row.get(key_field)) for row in primary_rows
        })
        declared = plan.get("declared_key_type")
        if declared:
            conflicts = [
                value for value in observed_types
                if value not in {declared, "unknown"}
            ]
            if conflicts:
                evidence = "conflict"
                typed_status = STATUS_PENDING_EVIDENCE
                typed_type = declared
                notes = (
                    f"主数据集键同时出现 {declared} 与 {', '.join(conflicts)}，"
                    "不能在同一个新类型版本中混用"
                )
            else:
                evidence = "declared"
                typed_status = STATUS_PENDING_REVIEW
                typed_type = declared
                notes = "键类型来自存量方案的显式声明"
        else:
            evidence = "missing"
            typed_status = STATUS_PENDING_EVIDENCE
            typed_type = "unknown"
            notes = "存量方案没有声明键类型，旧记录需人工核对后再审核"

        created_at = _now()
        legacy = JoinKeyVersion(
            version_id=f"{plan_id}-V1",
            plan_id=plan_id,
            semantics=SEMANTICS_STRING,
            key_type="string",
            evidence="legacy",
            status=STATUS_LEGACY_KEPT,
            contract="v1_string",
            created_at=created_at,
            notes="旧契约：按字符串匹配，历史报告可继续按此版本重算",
        )
        typed = JoinKeyVersion(
            version_id=f"{plan_id}-V2",
            plan_id=plan_id,
            semantics=SEMANTICS_TYPED,
            key_type=typed_type,
            evidence=evidence,
            status=typed_status,
            contract="v2_typed",
            created_at=created_at,
            notes=notes,
        )
        return JoinKeyManifest(
            manifest_id=f"JKM-{plan_id}",
            plan_id=plan_id,
            key_field=key_field,
            versions=[legacy, typed],
            active_version_id=legacy.version_id,
            migrated_at=created_at,
        )

    def _migration_record_locked(self, plan_id: str) -> dict[str, Any]:
        plan = self._require_plan_locked(plan_id)
        manifest = self.manifests[plan_id]
        typed = manifest.typed_version()
        legacy = manifest.legacy_version()
        primary_rows = self._scope_rows_locked(plan, plan["scope"])
        observations = [
            {
                "row_id": row.get(plan["row_id_field"]),
                "key_value": row.get(plan["join_key"]),
                "key_type": key_type_of(row.get(plan["join_key"])),
                "evidence": typed.evidence if typed else "missing",
            }
            for row in primary_rows
        ]
        return {
            "plan_id": plan_id,
            "plan_name": plan["plan_name"],
            "manifest_id": manifest.manifest_id,
            "key_field": plan["join_key"],
            "declared_key_type": plan.get("declared_key_type"),
            "evidence": typed.evidence if typed else "missing",
            "legacy_version_id": legacy.version_id if legacy else "",
            "typed_version_id": typed.version_id if typed else "",
            "typed_status": typed.status if typed else STATUS_PENDING_EVIDENCE,
            "active_version_id": manifest.active_version_id,
            "status": (
                "待核"
                if typed and typed.status in {STATUS_PENDING_EVIDENCE}
                else "已迁移"
            ),
            "row_count": len(primary_rows),
            "observations": observations,
            "typed_version": typed.to_dict() if typed else None,
            "legacy_version": legacy.to_dict() if legacy else None,
        }

    # ---------- 版本审核 / 启用 ----------

    def list_manifests(self) -> dict[str, Any]:
        with self._lock:
            return {
                "manifests": [
                    self.manifests[plan_id].to_dict()
                    for plan_id in self.plans
                    if plan_id in self.manifests
                ],
                "total": len(self.manifests),
            }

    def get_version(self, version_id: str) -> JoinKeyVersion:
        with self._lock:
            return self._find_version_locked(version_id)

    def review_version(
        self,
        version_id: str,
        *,
        decision: str = "approve",
        reviewer: str = "reviewer",
    ) -> dict[str, Any]:
        with self._lock:
            try:
                version, changed = self._review_locked(
                    version_id, decision=decision, reviewer=reviewer
                )
            except DomainError as exc:
                return {"ok": False, "message": str(exc), "version": None}
            return {
                "ok": True,
                "message": "版本已审核" if changed else "版本已经是审核通过状态",
                "changed": changed,
                "version": version.to_dict(),
            }

    def _review_locked(
        self,
        version_id: str,
        *,
        decision: str = "approve",
        reviewer: str = "reviewer",
    ) -> tuple[JoinKeyVersion, bool]:
        version = self._find_version_locked(version_id)
        if version.semantics != SEMANTICS_TYPED:
            raise DomainError("旧契约版本是保留版本，不需要审核")
        if decision not in {"approve", "reject"}:
            raise DomainError(f"未知审核结论：{decision}")
        if decision == "reject":
            if version.status == STATUS_ENABLED:
                raise DomainError("已启用的版本不能直接驳回，请先停用")
            if version.status == STATUS_DISABLED:
                return version, False
            version.status = STATUS_DISABLED
            version.reviewed_by = reviewer
            version.reviewed_at = _now()
            version.notes = "审核驳回，版本已停用"
            return version, True
        if version.evidence == "missing":
            raise DomainError("键类型证据不明，版本处于待核状态，不能审核通过")
        if version.evidence == "conflict":
            raise DomainError("主数据集内关联键类型混用，版本处于待核状态，不能审核通过")
        if version.status in {STATUS_REVIEWED, STATUS_ENABLED}:
            return version, False
        version.status = STATUS_REVIEWED
        version.reviewed_by = reviewer
        version.reviewed_at = _now()
        version.notes = "键类型证据完整，审核通过"
        return version, True

    def enable_version(self, version_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                version, manifest, changed = self._enable_locked(version_id)
            except DomainError as exc:
                return {"ok": False, "message": str(exc), "version": None}
            return {
                "ok": True,
                "message": "新类型版本已启用" if changed else "版本已经是启用状态",
                "changed": changed,
                "active_version_id": manifest.active_version_id,
                "version": version.to_dict(),
            }

    def _enable_locked(
        self, version_id: str
    ) -> tuple[JoinKeyVersion, JoinKeyManifest, bool]:
        version = self._find_version_locked(version_id)
        manifest = self.manifests[version.plan_id]
        if version.semantics != SEMANTICS_TYPED:
            raise DomainError("旧契约版本始终保留，不需要启用")
        if version.status == STATUS_PENDING_EVIDENCE:
            raise DomainError("版本仍待核，补齐键类型证据并审核后才能启用")
        if version.status == STATUS_PENDING_REVIEW:
            raise DomainError("版本尚未审核，不能启用")
        if version.status == STATUS_DISABLED:
            raise DomainError("版本已停用，不能启用")
        if manifest.active_version_id == version.version_id:
            return version, manifest, False
        version.status = STATUS_ENABLED
        version.enabled_at = _now()
        manifest.active_version_id = version.version_id
        return version, manifest, True

    def disable_version(self, version_id: str) -> dict[str, Any]:
        with self._lock:
            version = self._find_version_locked(version_id)
            manifest = self.manifests[version.plan_id]
            if version.semantics != SEMANTICS_TYPED:
                return {"ok": False, "message": "旧契约保留版本不能停用"}
            if manifest.active_version_id != version.version_id:
                return {"ok": False, "message": "只有启用中的版本可以停用"}
            version.status = STATUS_DISABLED
            legacy = manifest.legacy_version()
            manifest.active_version_id = legacy.version_id if legacy else ""
            return {
                "ok": True,
                "message": "已停用新类型版本，回退到旧契约保留版本",
                "active_version_id": manifest.active_version_id,
                "version": version.to_dict(),
            }

    # ---------- 报告聚合 ----------

    def aggregate(
        self,
        plan_id: str,
        *,
        version_id: str | None = None,
        scope: list[str] | None = None,
        report_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """按清单版本真实执行关联并汇总匹配明细金额。"""
        with self._lock:
            return self._aggregate_locked(
                plan_id,
                version_id=version_id,
                scope=scope,
                report_id=report_id,
                persist=persist,
            )

    def _aggregate_locked(
        self,
        plan_id: str,
        *,
        version_id: str | None = None,
        scope: list[str] | None = None,
        report_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        plan = self._require_plan_locked(plan_id)
        manifest = self.manifests.get(plan_id)
        if manifest is None:
            manifest = self._build_manifest_locked(plan)
            self.manifests[plan_id] = manifest

        chosen_version_id = version_id or manifest.active_version_id
        version = manifest.version(chosen_version_id)
        if version is None:
            return self._aggregation_failure(
                plan, report_id, chosen_version_id, "版本不存在于该方案的关联键版本清单中"
            )

        # 只有键类型证据不明的「新类型版本」被待核拦截；旧契约保留版本
        # 仍可按原字符串范围重算历史结果。
        if version.evidence == "missing":
            return self._aggregation_failure(
                plan,
                report_id,
                chosen_version_id,
                "关联键类型证据不明，旧输入待核，暂不出具金额",
                status="待核",
                semantics=version.semantics,
            )

        if version.evidence == "conflict":
            return self._aggregation_failure(
                plan,
                report_id,
                chosen_version_id,
                "主数据集内关联键类型混用，新类型版本待核，不能参与报告聚合",
                status="待核",
                semantics=version.semantics,
                key_type=version.key_type,
            )

        if (
            version.semantics == SEMANTICS_TYPED
            and version.status not in ELIGIBLE_TYPED_STATUSES
        ):
            return self._aggregation_failure(
                plan,
                report_id,
                chosen_version_id,
                "新类型版本尚未审核通过，不能参与报告聚合",
                status=version.status,
                semantics=version.semantics,
                key_type=version.key_type,
            )

        selected_scope = list(scope) if scope is not None else list(plan["scope"])
        primary_rows = self._scope_rows_locked(plan, selected_scope)
        join_rows = self.datasets.get(plan["join_dataset"], [])
        key_field = plan["join_key"]

        if version.semantics == SEMANTICS_TYPED:
            primary_types = sorted({
                key_type_of(row.get(key_field)) for row in primary_rows
            })
            if "unknown" in primary_types or "bool" in primary_types:
                return self._aggregation_failure(
                    plan,
                    report_id,
                    chosen_version_id,
                    "关联键存在无类型证据的值，需人工核对",
                    status="待核",
                    semantics=version.semantics,
                    key_type=version.key_type,
                )
            if len(primary_types) > 1:
                return self._aggregation_failure(
                    plan,
                    report_id,
                    chosen_version_id,
                    f"同一聚合内出现多种关联键类型：{', '.join(primary_types)}，不能混用",
                    status="键类型混用",
                    semantics=version.semantics,
                    key_type=version.key_type,
                    key_types=primary_types,
                )
            if (
                version.key_type not in {"unknown", ""}
                and primary_types[0] != version.key_type
            ):
                return self._aggregation_failure(
                    plan,
                    report_id,
                    chosen_version_id,
                    f"聚合键类型 {primary_types[0]} 与清单元数据 {version.key_type} 不一致",
                    status="键类型混用",
                    semantics=version.semantics,
                    key_type=version.key_type,
                    key_types=primary_types,
                )

        matched_detail: list[dict[str, Any]] = []
        unmatched_order_ids: list[str] = []
        for primary in primary_rows:
            joined = self._match_rows(primary, join_rows, key_field, version.semantics)
            order_id = primary.get(plan["row_id_field"])
            if not joined:
                unmatched_order_ids.append(order_id)
                continue
            for join_row in joined:
                matched_detail.append(self._detail_row(primary, join_row, plan, version))

        amount = _round_amount(sum(row["amount"] for row in matched_detail))
        matched_order_ids = [row["order_id"] for row in matched_detail]
        result = {
            "ok": True,
            "aggregation_id": "",
            "report_id": report_id or "",
            "plan_id": plan_id,
            "version_id": version.version_id,
            "semantics": version.semantics,
            "contract": version.contract,
            "key_type": version.key_type,
            "scope": selected_scope,
            "status": "已计算",
            "amount": amount,
            "total_amount": amount,
            "matched_order_count": len(matched_detail),
            "matched_order_ids": matched_order_ids,
            "unmatched_order_ids": unmatched_order_ids,
            "matched_detail": matched_detail,
            "amount_source": f"{plan['primary_dataset']}.{plan['amount_field']}",
            "computed_at": _now(),
        }
        if persist:
            self._run_seq += 1
            result["aggregation_id"] = f"AGG-{self._run_seq:04d}"
            self.runs.append(dict(result))
            # 已签发报告只做校验，不覆盖其冻结金额。
            if report_id and report_id in self.reports:
                report = self.reports[report_id]
                result["preserved_amount"] = report.amount
                result["matches_preserved"] = (
                    report.amount == amount
                    and report.matched_order_ids == matched_order_ids
                )
        return result

    def _aggregation_failure(
        self,
        plan: dict[str, Any],
        report_id: str | None,
        version_id: str,
        message: str,
        *,
        status: str = "已拒绝",
        semantics: str = "",
        key_type: str = "",
        key_types: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "aggregation_id": "",
            "report_id": report_id or "",
            "plan_id": plan["plan_id"],
            "version_id": version_id,
            "semantics": semantics,
            "key_type": key_type,
            "status": status,
            "amount": None,
            "total_amount": None,
            "matched_order_count": 0,
            "matched_order_ids": [],
            "unmatched_order_ids": [],
            "matched_detail": [],
            "key_types": key_types or [],
            "message": message,
            "computed_at": _now(),
        }

    def _match_rows(
        self,
        primary: dict[str, Any],
        join_rows: list[dict[str, Any]],
        key_field: str,
        semantics: str,
    ) -> list[dict[str, Any]]:
        primary_value = primary.get(key_field)
        matched = []
        for join_row in join_rows:
            if matches(primary_value, join_row.get(key_field), semantics):
                matched.append(join_row)
        return matched

    def _detail_row(
        self,
        primary: dict[str, Any],
        join_row: dict[str, Any],
        plan: dict[str, Any],
        version: JoinKeyVersion,
    ) -> dict[str, Any]:
        key_field = plan["join_key"]
        primary_key = primary.get(key_field)
        join_key = join_row.get(key_field)
        return {
            "order_id": primary.get(plan["row_id_field"]),
            "amount": _round_amount(primary.get(plan["amount_field"], 0.0)),
            "primary_key": primary_key,
            "primary_key_type": key_type_of(primary_key),
            "matched_key": join_key,
            "matched_key_type": key_type_of(join_key),
            "join_match": (
                "string" if version.semantics == SEMANTICS_STRING else "typed"
            ),
            "matched_row": dict(join_row),
        }

    def _legacy_detail_locked(
        self, plan: dict[str, Any], scope: list[str]
    ) -> list[dict[str, Any]]:
        primary_rows = self._scope_rows_locked(plan, scope)
        join_rows = self.datasets.get(plan["join_dataset"], [])
        legacy = JoinKeyVersion(
            version_id=f"{plan['plan_id']}-V1",
            plan_id=plan["plan_id"],
            semantics=SEMANTICS_STRING,
            key_type="string",
            evidence="legacy",
            status=STATUS_LEGACY_KEPT,
            contract="v1_string",
            created_at="2026-11-18T09:00:00+00:00",
        )
        detail: list[dict[str, Any]] = []
        for primary in primary_rows:
            for join_row in self._match_rows(
                primary, join_rows, plan["join_key"], SEMANTICS_STRING
            ):
                detail.append(self._detail_row(primary, join_row, plan, legacy))
        return detail

    # ---------- 已签报告 ----------

    def issue_report(
        self,
        plan_id: str,
        *,
        version_id: str | None = None,
        scope: list[str] | None = None,
        report_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if report_id and report_id in self.reports:
                return {
                    "ok": False,
                    "message": "报告编号已存在，已签报告不可覆盖",
                }
            result = self._aggregate_locked(
                plan_id,
                version_id=version_id,
                scope=scope,
                persist=False,
            )
            if not result["ok"]:
                return result
            self._run_seq += 1
            new_id = report_id or f"RPT-{self._run_seq:04d}"
            report = IssuedReport(
                report_id=new_id,
                plan_id=plan_id,
                version_id=result["version_id"],
                scope=list(result["scope"]),
                amount=result["amount"],
                matched_order_ids=list(result["matched_order_ids"]),
                matched_detail=list(result["matched_detail"]),
                issued_at=_now(),
                status="已签",
            )
            self.reports[new_id] = report
            return {
                "ok": True,
                "message": "报告已签发，匹配明细与金额已冻结",
                "report": report.to_dict(),
            }

    def list_reports(self) -> dict[str, Any]:
        with self._lock:
            return {
                "reports": [report.to_dict() for report in self.reports.values()],
                "total": len(self.reports),
            }

    def recompute_report(self, report_id: str) -> dict[str, Any]:
        """按已签报告冻结的版本与原范围重算，并校验金额是否一致。"""
        with self._lock:
            report = self.reports.get(report_id)
            if report is None:
                return {"ok": False, "message": f"报告 {report_id} 不存在或未签发"}
            result = self._aggregate_locked(
                report.plan_id,
                version_id=report.version_id,
                scope=list(report.scope),
                report_id=report_id,
                persist=True,
            )
            if not result["ok"]:
                return result
            result["preserved_amount"] = report.amount
            result["matches_preserved"] = (
                report.amount == result["amount"]
                and report.matched_order_ids == result["matched_order_ids"]
            )
            result["report"] = report.to_dict()
            result["message"] = (
                "已签报告按原版本、原范围重算，历史匹配与金额保持一致"
                if result["matches_preserved"]
                else "重算结果与已签快照不一致，请人工复核"
            )
            result["status"] = (
                "已计算" if result["matches_preserved"] else "历史值不一致"
            )
            return result

    def list_runs(self) -> dict[str, Any]:
        with self._lock:
            return {"runs": [dict(run) for run in self.runs], "total": len(self.runs)}

    # ---------- 复核草稿 ----------

    def create_draft(
        self,
        *,
        plan_id: str = "DATA-JK-001",
        version_id: str | None = None,
        scope: list[str] | None = None,
        report_id: str = "",
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                draft = self._create_draft_locked(
                    plan_id=plan_id,
                    version_id=version_id,
                    scope=scope,
                    report_id=report_id,
                    draft_id=draft_id,
                )
            except DomainError as exc:
                return {"ok": False, "message": str(exc), "draft": None}
            return {
                "ok": True,
                "message": "复核草稿已领用当前结果",
                "draft": draft.to_dict(),
            }

    def _create_draft_locked(
        self,
        *,
        plan_id: str,
        version_id: str | None,
        scope: list[str] | None,
        report_id: str = "",
        draft_id: str | None,
    ) -> ReviewDraft:
        plan = self._require_plan_locked(plan_id)
        if report_id:
            report = self.reports.get(report_id)
            if report is None:
                raise DomainError(f"报告 {report_id} 不存在，无法领用结果")
            bound_version = version_id or report.version_id
            draft_scope = list(scope) if scope is not None else list(report.scope)
        else:
            manifest = self.manifests.get(plan_id)
            bound_version = (
                version_id
                or (manifest.active_version_id if manifest else f"{plan_id}-V1")
            )
            draft_scope = list(scope) if scope is not None else list(plan["scope"])

        claimed = self._aggregate_locked(
            plan_id,
            version_id=bound_version,
            scope=draft_scope,
            persist=False,
        )
        self._draft_seq += 1
        draft = ReviewDraft(
            draft_id=draft_id or f"DRF-{self._draft_seq:04d}",
            plan_id=plan_id,
            report_id=report_id,
            bound_version_id=bound_version,
            scope=draft_scope,
            claimed_amount=claimed["amount"] if claimed["ok"] else None,
            claimed_at=_now(),
            state=DRAFT_CLAIMED,
        )
        draft.history.append({
            "at": draft.claimed_at,
            "action": "领用结果",
            "version_id": bound_version,
            "amount": draft.claimed_amount,
            "status": claimed["status"],
        })
        self.drafts[draft.draft_id] = draft
        return draft

    def list_drafts(self) -> dict[str, Any]:
        with self._lock:
            return {
                "drafts": [draft.to_dict() for draft in self.drafts.values()],
                "total": len(self.drafts),
            }

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            draft = self.drafts.get(draft_id)
            return draft.to_dict() if draft else None

    def submit_draft(
        self,
        draft_id: str,
        *,
        mode: str = "continue",
        version_id: str | None = None,
        authorized_by: str = "",
    ) -> dict[str, Any]:
        """提交复核草稿。

        * ``continue``：按草稿领用时绑定的版本续算（旧契约 → 80）；
        * ``correct``：授权更正到完整的新类型版本（→ 30）。

        并发提交同一草稿时只有第一个请求真正确认，后续请求返回同一个
        已确认结果且 ``confirmed_now=False``。
        """
        normalised = _normalise_mode(mode)
        with self._lock:
            draft = self.drafts.get(draft_id)
            if draft is None:
                return {
                    "ok": False,
                    "confirmed_now": False,
                    "message": f"复核草稿 {draft_id} 不存在",
                    "draft": None,
                }
            if draft.decided:
                return {
                    "ok": True,
                    "confirmed_now": False,
                    "message": "该复核草稿已经确认过，未重复确认",
                    "draft": draft.to_dict(),
                    "result": dict(draft.result) if draft.result else None,
                }

            plan = self._require_plan_locked(draft.plan_id)
            manifest = self.manifests.get(draft.plan_id)
            if manifest is None:
                manifest = self._build_manifest_locked(plan)
                self.manifests[draft.plan_id] = manifest

            if normalised == "continue":
                target_version_id = draft.bound_version_id
                authorized = False
            else:
                target_version_id = version_id
                if not target_version_id:
                    typed = manifest.typed_version()
                    target_version_id = typed.version_id if typed else ""
                target = manifest.version(target_version_id)
                if target is None:
                    return {
                        "ok": False,
                        "confirmed_now": False,
                        "message": "指定的更正版本不存在",
                        "draft": draft.to_dict(),
                    }
                if target.semantics != SEMANTICS_TYPED:
                    return {
                        "ok": False,
                        "confirmed_now": False,
                        "message": "授权更正只能切换到保留原始键类型的新版本",
                        "draft": draft.to_dict(),
                    }
                if target.status not in ELIGIBLE_TYPED_STATUSES:
                    return {
                        "ok": False,
                        "confirmed_now": False,
                        "message": "新类型版本尚未完成审核，不能用于授权更正",
                        "draft": draft.to_dict(),
                    }
                authorized = True

            result = self._aggregate_locked(
                draft.plan_id,
                version_id=target_version_id,
                scope=list(draft.scope),
                report_id=draft.report_id or None,
                persist=True,
            )
            if not result["ok"]:
                return {
                    "ok": False,
                    "confirmed_now": False,
                    "message": result.get("message", "复核草稿无法完成计算"),
                    "draft": draft.to_dict(),
                    "result": result,
                }

            draft.state = DRAFT_CORRECTED if authorized else DRAFT_CONFIRMED
            draft.confirmation_count = 1
            draft.result = result
            draft.decided_at = _now()
            draft.authorized_by = authorized_by if authorized else ""
            draft.history.append({
                "at": draft.decided_at,
                "action": "授权更正" if authorized else "确认续算",
                "version_id": target_version_id,
                "amount": result["amount"],
                "authorized_by": draft.authorized_by,
            })
            return {
                "ok": True,
                "confirmed_now": True,
                "message": (
                    "复核草稿已授权更正到新类型版本"
                    if authorized
                    else "复核草稿已按原领用范围确认"
                ),
                "draft": draft.to_dict(),
                "result": result,
            }

    # 兼容不同调用方的命名。
    def confirm_draft(self, draft_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.submit_draft(draft_id, mode="continue", **kwargs)

    def correct_draft(self, draft_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.submit_draft(draft_id, mode="correct", **kwargs)

    # ---------- 内部辅助 ----------

    def _require_plan_locked(self, plan_id: str) -> dict[str, Any]:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise DomainError(f"关联方案 {plan_id} 不存在")
        return plan

    def _scope_rows_locked(
        self, plan: dict[str, Any], scope: list[str]
    ) -> list[dict[str, Any]]:
        rows = self.datasets.get(plan["primary_dataset"], [])
        if not scope:
            return [dict(row) for row in rows]
        wanted = {str(item) for item in scope}
        return [
            dict(row) for row in rows
            if str(row.get(plan["row_id_field"])) in wanted
        ]

    def _find_version_locked(self, version_id: str) -> JoinKeyVersion:
        for manifest in self.manifests.values():
            version = manifest.version(version_id)
            if version is not None:
                return version
        raise DomainError(f"关联键版本 {version_id} 不存在")


def _normalise_mode(mode: str) -> str:
    value = (mode or "continue").strip().lower()
    if value in {"continue", "confirm", "续算", "确认续算", "确认"}:
        return "continue"
    if value in {"correct", "correction", "correct_new", "更正", "授权更正"}:
        return "correct"
    raise DomainError(f"未知复核提交方式：{mode}")


# ============================================================
# 进程内单例
# ============================================================

scenario = JoinKeyScenario()
# 模拟「升级构建部署」：服务启动时自动完成存量关联方案的版本清单迁移。
# 新类型版本只迁移、不自动审核/启用，仍需人工审核与启用。
scenario.reset(auto_migrate=True)
