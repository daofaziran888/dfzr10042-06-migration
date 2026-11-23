"""关联键版本清单迁移与报告聚合的场景测试。

覆盖题目要求的真实数据流：
* 两个订单旧方案合计 80；数字 7 与文本 7 旧版能配、新版不能配；真实同类型键
  的 30 元两版都匹配；
* 已复核草稿先领旧结果，随后新类型版迁移、审核、启用交错发生；
* 草稿续算得 80、授权更正得 30；已签报告按原范围仍得 80；
* 两种键不能在同一次聚合里混用；
* 并发提交同一次复核只确认一次；
* 有明确类型与无证类型的旧记录都执行迁移，实际重跑两种关联与报告聚合。
"""
from __future__ import annotations

import threading

import pytest

from app import join_versions as jk
from app.join_versions import (
    SEMANTICS_STRING,
    SEMANTICS_TYPED,
    DomainError,
    JoinKeyScenario,
    key_type_of,
    matches,
)


@pytest.fixture
def scenario():
    service = JoinKeyScenario()
    service.reset(auto_migrate=False)
    return service


@pytest.fixture
def migrated(scenario):
    scenario.migrate()
    return scenario


# ============================================================
# 基础匹配语义
# ============================================================

class TestKeySemantics:
    def test_key_type_keeps_original_type(self):
        assert key_type_of(7) == "int"
        assert key_type_of("7") == "text"
        assert key_type_of(7.0) == "float"
        assert key_type_of(True) == "bool"
        assert key_type_of(None) == "unknown"

    def test_string_contract_conflates_number_and_text(self):
        assert matches(7, "7", SEMANTICS_STRING)
        assert matches("7", 7, SEMANTICS_STRING)

    def test_typed_contract_separates_number_and_text(self):
        assert not matches(7, "7", SEMANTICS_TYPED)
        assert not matches("7", 7, SEMANTICS_TYPED)
        assert matches(9, 9, SEMANTICS_TYPED)

    def test_typed_contract_ignores_booleans_and_none(self):
        assert not matches(True, 1, SEMANTICS_TYPED)
        assert not matches(None, None, SEMANTICS_TYPED)


# ============================================================
# 迁移
# ============================================================

class TestMigration:
    def test_legacy_aggregation_totals_80(self, scenario):
        result = scenario.aggregate("DATA-JK-001", version_id="DATA-JK-001-V1")
        assert result["ok"] is True
        assert result["amount"] == 80.0
        assert result["matched_order_ids"] == ["O-1001", "O-1002"]

    def test_typed_aggregation_totals_30(self, scenario):
        scenario.migrate("DATA-JK-001")
        scenario.review_version("DATA-JK-001-V2")
        result = scenario.aggregate("DATA-JK-001", version_id="DATA-JK-001-V2")
        assert result["ok"] is True
        assert result["amount"] == 30.0
        assert result["matched_order_ids"] == ["O-1002"]
        assert result["unmatched_order_ids"] == ["O-1001"]

    def test_migration_creates_legacy_and_typed_versions(self, migrated):
        manifest = migrated.manifests["DATA-JK-001"]
        legacy = manifest.legacy_version()
        typed = manifest.typed_version()
        assert legacy.status == "保留"
        assert legacy.semantics == SEMANTICS_STRING
        assert typed.semantics == SEMANTICS_TYPED
        assert typed.status == "待审核"
        assert typed.evidence == "declared"
        # 迁移只建清单，不自动启用新类型版本。
        assert manifest.active_version_id == legacy.version_id

    def test_missing_evidence_rows_are_pending(self, migrated):
        record = migrated._migration_record_locked("DATA-JK-002")
        assert record["evidence"] == "missing"
        assert record["typed_status"] == "待核"
        assert record["status"] == "待核"
        assert all(obs["evidence"] == "missing" for obs in record["observations"])

    def test_typed_version_cannot_be_reviewed_while_pending(self, migrated):
        outcome = migrated.review_version("DATA-JK-002-V2")
        assert outcome["ok"] is False
        assert "待核" in outcome["message"]

    def test_pending_plan_aggregation_is_refused(self, migrated):
        result = migrated.aggregate("DATA-JK-002", version_id="DATA-JK-002-V2")
        assert result["ok"] is False
        assert result["status"] == "待核"
        assert result["amount"] is None

    def test_typed_requires_review_before_aggregation(self, migrated):
        result = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V2")
        assert result["ok"] is False
        assert result["status"] == "待审核"

    def test_migration_is_idempotent(self, migrated):
        before = migrated.list_manifests()
        migrated.migrate()
        after = migrated.list_manifests()
        assert [m["manifest_id"] for m in before["manifests"]] == [
            m["manifest_id"] for m in after["manifests"]
        ]


# ============================================================
# 审核 / 启用
# ============================================================

class TestReviewAndEnable:
    def test_review_then_enable_switches_active_version(self, migrated):
        review = migrated.review_version("DATA-JK-001-V2", reviewer="alice")
        assert review["ok"] and review["changed"]
        enable = migrated.enable_version("DATA-JK-001-V2")
        assert enable["ok"] and enable["changed"]
        assert enable["active_version_id"] == "DATA-JK-001-V2"
        assert migrated.manifests["DATA-JK-001"].active_version_id == "DATA-JK-001-V2"

    def test_enable_without_review_is_refused(self, migrated):
        outcome = migrated.enable_version("DATA-JK-001-V2")
        assert outcome["ok"] is False
        assert "审核" in outcome["message"]

    def test_aggregation_follows_active_version(self, migrated):
        assert migrated.aggregate("DATA-JK-001")["amount"] == 80.0
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        assert migrated.aggregate("DATA-JK-001")["amount"] == 30.0

    def test_duplicate_review_is_idempotent(self, migrated):
        first = migrated.review_version("DATA-JK-001-V2")
        second = migrated.review_version("DATA-JK-001-V2")
        assert first["changed"] is True
        assert second["changed"] is False

    def test_legacy_version_is_always_retained(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        legacy = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V1")
        assert legacy["ok"] and legacy["amount"] == 80.0
        assert migrated.manifests["DATA-JK-001"].legacy_version().status == "保留"

    def test_disable_rolls_back_to_legacy(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        outcome = migrated.disable_version("DATA-JK-001-V2")
        assert outcome["ok"]
        assert migrated.aggregate("DATA-JK-001")["amount"] == 80.0
        assert migrated.manifests["DATA-JK-001"].legacy_version().status == "保留"


# ============================================================
# 已签报告
# ============================================================

class TestIssuedReports:
    def test_seed_report_is_frozen_at_80(self, migrated):
        report = migrated.reports["RPT-LEGACY-001"]
        assert report.amount == 80.0
        assert report.version_id == "DATA-JK-001-V1"
        assert report.matched_order_ids == ["O-1001", "O-1002"]

    def test_recompute_after_new_version_enabled_keeps_80(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        # 活动版本已经是新契约，但已签报告仍按原版本、原范围重算。
        recompute = migrated.recompute_report("RPT-LEGACY-001")
        assert recompute["ok"] is True
        assert recompute["version_id"] == "DATA-JK-001-V1"
        assert recompute["amount"] == 80.0
        assert recompute["preserved_amount"] == 80.0
        assert recompute["matches_preserved"] is True
        assert migrated.reports["RPT-LEGACY-001"].amount == 80.0

    def test_new_issue_uses_enabled_typed_version(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        issued = migrated.issue_report("DATA-JK-001", report_id="RPT-NEW-001")
        assert issued["ok"] is True
        assert issued["report"]["version_id"] == "DATA-JK-001-V2"
        assert issued["report"]["amount"] == 30.0

    def test_issued_report_cannot_be_overwritten(self, migrated):
        first = migrated.issue_report("DATA-JK-001", report_id="RPT-X")
        second = migrated.issue_report("DATA-JK-001", report_id="RPT-X")
        assert first["ok"] is True
        assert second["ok"] is False


# ============================================================
# 复核草稿
# ============================================================

class TestReviewDraft:
    def test_seed_draft_claimed_legacy_result(self, migrated):
        draft = migrated.get_draft("DRF-SEED-001")
        assert draft["state"] == "已领旧结果"
        assert draft["claimed_amount"] == 80.0
        assert draft["bound_version_id"] == "DATA-JK-001-V1"
        assert draft["report_id"] == "RPT-LEGACY-001"

    def test_continue_after_review_and_enable_still_counts_80(self, migrated):
        # 交错顺序：草稿已领 → 迁移 → 审核 → 启用 → 草稿确认续算。
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        outcome = migrated.submit_draft("DRF-SEED-001", mode="continue")
        assert outcome["ok"] is True
        assert outcome["confirmed_now"] is True
        assert outcome["result"]["amount"] == 80.0
        assert outcome["result"]["version_id"] == "DATA-JK-001-V1"
        assert outcome["draft"]["state"] == "已确认"

    def test_authorized_correction_uses_full_new_version_and_counts_30(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        outcome = migrated.submit_draft(
            "DRF-SEED-001", mode="correct", authorized_by="reviewer-01"
        )
        assert outcome["ok"] is True
        assert outcome["confirmed_now"] is True
        assert outcome["result"]["amount"] == 30.0
        assert outcome["result"]["version_id"] == "DATA-JK-001-V2"
        assert outcome["draft"]["state"] == "已更正"
        assert outcome["draft"]["authorized_by"] == "reviewer-01"
        assert outcome["result"]["unmatched_order_ids"] == ["O-1001"]

    def test_correction_requires_reviewed_typed_version(self, migrated):
        outcome = migrated.submit_draft("DRF-SEED-001", mode="correct")
        assert outcome["ok"] is False
        assert "审核" in outcome["message"]
        assert migrated.get_draft("DRF-SEED-001")["state"] == "已领旧结果"

    def test_draft_cannot_switch_back_to_legacy_via_correction(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        outcome = migrated.submit_draft(
            "DRF-SEED-001", mode="correct", version_id="DATA-JK-001-V1"
        )
        assert outcome["ok"] is False

    def test_concurrent_submit_confirms_only_once(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")

        barrier = threading.Barrier(8)
        outcomes: list[dict] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            result = migrated.submit_draft("DRF-SEED-001", mode="continue")
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(outcomes) == 8
        assert sum(1 for item in outcomes if item["confirmed_now"]) == 1
        assert all(item["ok"] for item in outcomes)
        amounts = {item["result"]["amount"] for item in outcomes}
        assert amounts == {80.0}
        assert migrated.get_draft("DRF-SEED-001")["confirmation_count"] == 1

    def test_concurrent_mixed_modes_still_confirm_once(self, migrated):
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")

        barrier = threading.Barrier(6)
        outcomes: list[dict] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            mode = "continue" if index % 2 == 0 else "correct"
            barrier.wait()
            result = migrated.submit_draft("DRF-SEED-001", mode=mode)
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(1 for item in outcomes if item["confirmed_now"]) == 1
        # 混合并发下先到者决定结果，其余请求拿到同一份已确认结果。
        decided = {item["result"]["amount"] for item in outcomes}
        assert len(decided) == 1
        assert decided <= {80.0, 30.0}
        assert migrated.get_draft("DRF-SEED-001")["confirmation_count"] == 1


# ============================================================
# 键类型混用
# ============================================================

class TestMixedKeyTypes:
    def test_legacy_handles_mixed_rows_as_strings(self, migrated):
        result = migrated.aggregate("DATA-JK-003", version_id="DATA-JK-003-V1")
        assert result["amount"] == 70.0
        assert result["matched_order_ids"] == ["M-3001", "M-3002", "M-3003"]

    def test_typed_refuses_to_mix_two_key_types_in_one_aggregation(self, migrated):
        # 该方案在迁移阶段就被标记为待核（主数据集键类型混用）。
        record = migrated._migration_record_locked("DATA-JK-003")
        assert record["evidence"] == "conflict"
        assert record["status"] == "待核"
        review = migrated.review_version("DATA-JK-003-V2")
        assert review["ok"] is False

        result = migrated.aggregate("DATA-JK-003", version_id="DATA-JK-003-V2")
        assert result["ok"] is False
        assert "待核" in result["message"]

    def test_typed_refuses_mixed_scope_at_runtime(self, migrated):
        # 即便清单元数据声明 int，运行期也拒绝一次聚合混两种键。
        migrated.review_version("DATA-JK-001-V2")
        result = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V2")
        assert result["ok"] is True
        assert result["amount"] == 30.0

        migrated.datasets["orders"].append(
            {"order_id": "O-1003", "member_key": "7", "amount": 20.0, "status": "已支付"}
        )
        mixed = migrated.aggregate(
            "DATA-JK-001",
            version_id="DATA-JK-001-V2",
            scope=["O-1001", "O-1002", "O-1003"],
        )
        assert mixed["ok"] is False
        assert mixed["status"] == "键类型混用"
        assert mixed["amount"] is None
        assert mixed["key_types"] == ["int", "text"]

    def test_typed_scope_with_single_type_still_computes(self, migrated):
        # 不混用的子范围仍可计算：只取 int 的那一单。
        migrated.review_version("DATA-JK-001-V2")
        migrated.datasets["orders"].append(
            {"order_id": "O-1003", "member_key": "7", "amount": 20.0, "status": "已支付"}
        )
        typed_only = migrated.aggregate(
            "DATA-JK-001", version_id="DATA-JK-001-V2", scope=["O-1002"]
        )
        assert typed_only["ok"] is True
        assert typed_only["amount"] == 30.0


# ============================================================
# 有明确类型 / 无证类型：两版关联与报告聚合各跑一遍
# ============================================================

class TestRerunBothEvidenceKinds:
    def test_declared_and_missing_plans_are_both_migrated(self, migrated):
        records = {record["plan_id"]: record for record in migrated.migrate()["records"]}
        assert records["DATA-JK-001"]["evidence"] == "declared"
        assert records["DATA-JK-001"]["status"] == "已迁移"
        assert records["DATA-JK-002"]["evidence"] == "missing"
        assert records["DATA-JK-002"]["status"] == "待核"
        for record in records.values():
            assert record["legacy_version_id"].endswith("-V1")
            assert record["typed_version_id"].endswith("-V2")

    def test_declared_plan_reruns_legacy_and_typed(self, migrated):
        legacy = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V1")
        migrated.review_version("DATA-JK-001-V2")
        migrated.enable_version("DATA-JK-001-V2")
        typed = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V2")
        assert (legacy["amount"], typed["amount"]) == (80.0, 30.0)
        # 两次运行都留下了真实匹配明细。
        assert legacy["matched_order_count"] == 2
        assert typed["matched_order_count"] == 1

    def test_missing_plan_reruns_legacy_but_blocks_typed(self, migrated):
        legacy = migrated.aggregate("DATA-JK-002", version_id="DATA-JK-002-V1")
        assert legacy["ok"] is True
        assert legacy["amount"] == 20.0
        assert legacy["matched_order_ids"] == ["L-2001", "L-2002"]
        typed = migrated.aggregate("DATA-JK-002", version_id="DATA-JK-002-V2")
        assert typed["ok"] is False
        assert typed["status"] == "待核"
        assert typed["amount"] is None

    def test_missing_plan_legacy_report_is_recomputable(self, migrated):
        issued = migrated.issue_report(
            "DATA-JK-002", version_id="DATA-JK-002-V1", report_id="RPT-LEGACY-002"
        )
        assert issued["ok"] is True
        assert issued["report"]["amount"] == 20.0
        rerun = migrated.recompute_report("RPT-LEGACY-002")
        assert rerun["ok"] is True
        assert rerun["amount"] == 20.0
        assert rerun["matches_preserved"] is True


# ============================================================
# 实际运行记录
# ============================================================

class TestRunAudit:
    def test_runs_record_real_match_detail(self, migrated):
        migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V1")
        migrated.review_version("DATA-JK-001-V2")
        migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V2")
        runs = migrated.list_runs()["runs"]
        assert len(runs) == 2
        legacy, typed = runs
        assert legacy["amount"] == 80.0
        assert {row["matched_row"]["member_key"] for row in legacy["matched_detail"]} == {"7", 9}
        assert typed["amount"] == 30.0
        assert [row["join_match"] for row in typed["matched_detail"]] == ["typed"]
        assert typed["matched_detail"][0]["matched_key"] == 9

    def test_amount_is_computed_from_matched_detail_not_hardcoded(self, migrated):
        migrated.datasets["orders"][0]["amount"] = 55.0
        result = migrated.aggregate("DATA-JK-001", version_id="DATA-JK-001-V1")
        assert result["amount"] == 85.0


# ============================================================
# HTTP 接口冒烟
# ============================================================

def test_router_endpoints_expose_scenario():
    from app.routers import join_keys

    jk.scenario.reset(auto_migrate=True)

    manifests = join_keys.list_manifests()
    assert manifests["total"] == 3

    legacy = join_keys.aggregate(
        join_keys.AggregatePayload(plan_id="DATA-JK-001", version_id="DATA-JK-001-V1")
    )
    assert legacy["amount"] == 80.0

    review = join_keys.review_version("DATA-JK-001-V2", join_keys.ReviewPayload())
    assert review["ok"] is True
    enable = join_keys.enable_version("DATA-JK-001-V2")
    assert enable["active_version_id"] == "DATA-JK-001-V2"

    typed = join_keys.aggregate(join_keys.AggregatePayload(plan_id="DATA-JK-001"))
    assert typed["amount"] == 30.0

    reports = join_keys.list_reports()["reports"]
    assert reports[0]["amount"] == 80.0

    draft = join_keys.create_draft(join_keys.DraftPayload(plan_id="DATA-JK-001"))
    submitted = join_keys.submit_draft(
        draft["draft"]["draft_id"], join_keys.DraftSubmitPayload(mode="correct")
    )
    assert submitted["result"]["amount"] == 30.0


def test_http_endpoints_expose_scenario():
    try:
        import httpx  # noqa: F401
    except ModuleNotFoundError:
        try:
            import httpx2  # noqa: F401
        except ModuleNotFoundError:
            pytest.skip("未安装 httpx/httpx2，跳过 HTTP 层冒烟")

    from fastapi.testclient import TestClient

    from app.main import app

    jk.scenario.reset(auto_migrate=True)
    client = TestClient(app)

    manifests = client.get("/api/join-keys/manifests")
    assert manifests.status_code == 200
    assert manifests.json()["total"] == 3

    legacy = client.post(
        "/api/join-keys/aggregate",
        json={"plan_id": "DATA-JK-001", "version_id": "DATA-JK-001-V1"},
    )
    assert legacy.json()["amount"] == 80.0

    review = client.post("/api/join-keys/versions/DATA-JK-001-V2/review", json={})
    assert review.json()["ok"] is True
    enable = client.post("/api/join-keys/versions/DATA-JK-001-V2/enable", json={})
    assert enable.json()["active_version_id"] == "DATA-JK-001-V2"

    typed = client.post("/api/join-keys/aggregate", json={"plan_id": "DATA-JK-001"})
    assert typed.json()["amount"] == 30.0

    recompute = client.post("/api/join-keys/reports/RPT-LEGACY-001/recompute")
    assert recompute.json()["amount"] == 80.0
    assert recompute.json()["matches_preserved"] is True

    draft = client.post("/api/join-keys/drafts", json={"plan_id": "DATA-JK-001"}).json()
    submitted = client.post(
        f"/api/join-keys/drafts/{draft['draft']['draft_id']}/submit",
        json={"mode": "correct"},
    ).json()
    assert submitted["result"]["amount"] == 30.0
