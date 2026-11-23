<template>
  <section class="page" data-testid="join-keys-page">
    <header class="page-head">
      <div>
        <h2>关联键版本</h2>
        <p class="page-desc">
          存量关联方案迁移成保留原始键类型的版本清单；报告聚合按清单版本真实执行匹配后再汇总金额。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="migrate-all" type="button" @click="runMigrate()">
          迁移存量方案
        </button>
      </div>
    </header>

    <p v-if="message" class="page-foot" data-testid="message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <article v-for="manifest in manifests" :key="manifest.manifest_id" class="analysis-card" style="margin-bottom: 12px">
      <header class="page-head">
        <div>
          <strong>{{ manifest.plan_id }}</strong>
          <p class="page-desc">
            关联键 {{ manifest.key_field }} · 当前生效版本 {{ manifest.active_version_id || '—' }}
          </p>
        </div>
        <button class="btn" type="button" :data-testid="`aggregate-${manifest.plan_id}`" @click="runAggregate(manifest)">
          按当前版本聚合
        </button>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>版本</th><th>契约</th><th>键类型</th><th>证据</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="version in manifest.versions"
            :key="version.version_id"
            :data-testid="`version-${version.version_id}`"
            :data-status="version.status"
          >
            <td>{{ version.version_id }}</td>
            <td>{{ version.contract }}</td>
            <td>{{ version.key_type }}</td>
            <td>{{ version.evidence }}</td>
            <td>{{ version.status }}</td>
            <td class="row-actions">
              <button
                v-if="version.semantics === 'typed'"
                class="link"
                type="button"
                :data-testid="`review-${version.version_id}`"
                @click="runReview(version)"
              >
                审核
              </button>
              <button
                v-if="version.semantics === 'typed'"
                class="link"
                type="button"
                :data-testid="`enable-${version.version_id}`"
                @click="runEnable(version)"
              >
                启用
              </button>
              <button
                v-if="version.semantics === 'typed'"
                class="link"
                type="button"
                :data-testid="`disable-${version.version_id}`"
                @click="runDisable(version)"
              >
                停用
              </button>
              <span v-if="version.semantics !== 'typed'">保留版本</span>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <article v-if="aggregation" class="analysis-card" data-testid="aggregation">
      <strong>最近一次聚合：{{ aggregation.plan_id }} / {{ aggregation.version_id }}</strong>
      <p class="page-desc">{{ aggregation.status }} · {{ aggregation.message || '' }}</p>
      <table class="data-table">
        <thead>
          <tr><th>金额</th><th>匹配订单</th><th>未匹配订单</th><th>键类型</th></tr>
        </thead>
        <tbody>
          <tr>
            <td data-testid="aggregation-amount">{{ aggregation.amount ?? '—' }}</td>
            <td>{{ aggregation.matched_order_ids.join('、') || '—' }}</td>
            <td>{{ aggregation.unmatched_order_ids.join('、') || '—' }}</td>
            <td>{{ (aggregation.key_types?.length ? aggregation.key_types : [aggregation.key_type]).join(' / ') }}</td>
          </tr>
        </tbody>
      </table>
      <table v-if="aggregation.matched_detail.length" class="data-table">
        <thead>
          <tr><th>订单</th><th>主键</th><th>主键类型</th><th>匹配键</th><th>匹配键类型</th><th>金额</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in aggregation.matched_detail" :key="row.order_id" :data-testid="`detail-${row.order_id}`">
            <td>{{ row.order_id }}</td>
            <td>{{ String(row.primary_key) }}</td>
            <td>{{ row.primary_key_type }}</td>
            <td>{{ String(row.matched_key) }}</td>
            <td>{{ row.matched_key_type }}</td>
            <td>{{ row.amount }}</td>
          </tr>
        </tbody>
      </table>
    </article>

    <article class="analysis-card" style="margin-top: 12px">
      <strong>已签发报告</strong>
      <table class="data-table">
        <thead>
          <tr><th>报告</th><th>版本</th><th>范围</th><th>金额</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="report in reports" :key="report.report_id" :data-testid="`report-${report.report_id}`">
            <td>{{ report.report_id }}</td>
            <td>{{ report.version_id }}</td>
            <td>{{ report.scope.join('、') }}</td>
            <td>{{ report.amount }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`recompute-${report.report_id}`" @click="runRecompute(report)">
                按原范围重算
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <article class="analysis-card" style="margin-top: 12px">
      <strong>复核草稿</strong>
      <table class="data-table">
        <thead>
          <tr><th>草稿</th><th>绑定版本</th><th>已领金额</th><th>状态</th><th>确认次数</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="draft in drafts" :key="draft.draft_id" :data-testid="`draft-${draft.draft_id}`">
            <td>{{ draft.draft_id }}</td>
            <td>{{ draft.bound_version_id }}</td>
            <td>{{ draft.claimed_amount ?? '—' }}</td>
            <td>{{ draft.state }}</td>
            <td>{{ draft.confirmation_count }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`continue-${draft.draft_id}`" @click="runSubmit(draft, 'continue')">
                确认续算
              </button>
              <button class="link" type="button" :data-testid="`correct-${draft.draft_id}`" @click="runSubmit(draft, 'correct')">
                授权更正
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  aggregate,
  disableVersion,
  enableVersion,
  loadDrafts,
  loadManifests,
  loadReports,
  migratePlans,
  recomputeReport,
  reviewVersion,
  submitDraft,
} from '@/api/join-keys'
import type {
  AggregationResult,
  IssuedReport,
  JoinKeyManifest,
  JoinKeyVersion,
  ReviewDraft,
} from '@/api/join-keys'

const manifests = ref<JoinKeyManifest[]>([])
const reports = ref<IssuedReport[]>([])
const drafts = ref<ReviewDraft[]>([])
const aggregation = ref<AggregationResult | null>(null)
const message = ref('')
const errorMessage = ref('')

async function reload() {
  errorMessage.value = ''
  try {
    const [manifestList, reportList, draftList] = await Promise.all([
      loadManifests(),
      loadReports(),
      loadDrafts(),
    ])
    manifests.value = manifestList
    reports.value = reportList
    drafts.value = draftList
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据读取失败'
  }
}

async function guard(action: () => Promise<void>) {
  message.value = ''
  errorMessage.value = ''
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

function runMigrate() {
  void guard(async () => {
    const result = await migratePlans()
    message.value = result.message
    await reload()
  })
}

function runReview(version: JoinKeyVersion) {
  void guard(async () => {
    const result = await reviewVersion(version.version_id)
    message.value = result.message
    await reload()
  })
}

function runEnable(version: JoinKeyVersion) {
  void guard(async () => {
    const result = await enableVersion(version.version_id)
    message.value = result.message
    await reload()
  })
}

function runDisable(version: JoinKeyVersion) {
  void guard(async () => {
    const result = await disableVersion(version.version_id)
    message.value = result.message
    await reload()
  })
}

function runAggregate(manifest: JoinKeyManifest) {
  void guard(async () => {
    aggregation.value = await aggregate(manifest.plan_id, manifest.active_version_id || null)
    message.value = aggregation.value.ok
      ? `聚合完成：${aggregation.value.amount}`
      : aggregation.value.message ?? '聚合未完成'
  })
}

function runRecompute(report: IssuedReport) {
  void guard(async () => {
    aggregation.value = await recomputeReport(report.report_id)
    message.value = aggregation.value.message ?? '重算完成'
    await reload()
  })
}

function runSubmit(draft: ReviewDraft, mode: 'continue' | 'correct') {
  void guard(async () => {
    const result = await submitDraft(draft.draft_id, mode)
    message.value = result.message
    if (result.result) aggregation.value = result.result
    await reload()
  })
}

onMounted(reload)
</script>
