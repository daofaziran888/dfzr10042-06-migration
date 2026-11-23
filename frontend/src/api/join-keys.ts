import { request } from './client'

export type JoinKeyVersion = {
  version_id: string
  plan_id: string
  semantics: 'string' | 'typed'
  contract: string
  key_type: string
  evidence: string
  status: string
  reviewed: boolean
  reviewed_by: string
  reviewed_at: string
  enabled_at: string
  created_at: string
  notes: string
}

export type JoinKeyManifest = {
  manifest_id: string
  plan_id: string
  key_field: string
  active_version_id: string
  migrated_at: string
  versions: JoinKeyVersion[]
}

export type MatchedDetail = {
  order_id: string
  amount: number
  primary_key: number | string
  primary_key_type: string
  matched_key: number | string
  matched_key_type: string
  join_match: string
  matched_row: Record<string, unknown>
}

export type AggregationResult = {
  ok: boolean
  aggregation_id: string
  plan_id: string
  version_id: string
  semantics: string
  contract: string
  key_type: string
  status: string
  amount: number | null
  matched_order_ids: string[]
  unmatched_order_ids: string[]
  matched_detail: MatchedDetail[]
  key_types?: string[]
  preserved_amount?: number
  matches_preserved?: boolean
  message?: string
  computed_at: string
}

export type IssuedReport = {
  report_id: string
  plan_id: string
  version_id: string
  scope: string[]
  amount: number
  matched_order_ids: string[]
  issued_at: string
  status: string
}

export type ReviewDraft = {
  draft_id: string
  plan_id: string
  report_id: string
  bound_version_id: string
  scope: string[]
  claimed_amount: number | null
  state: string
  confirmation_count: number
  authorized_by: string
  result: AggregationResult | null
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，操作未生效`)
  return (await response.json()) as T
}

export async function loadManifests(): Promise<JoinKeyManifest[]> {
  const response = await request('/api/join-keys/manifests')
  if (!response.ok) throw new Error('关联键版本清单读取失败')
  const payload = (await response.json()) as { manifests: JoinKeyManifest[] }
  return payload.manifests
}

export async function migratePlans(planId?: string, review = false): Promise<{ ok: boolean; message: string }> {
  return await post('/api/join-keys/migrate', { plan_id: planId ?? null, review })
}

export async function reviewVersion(versionId: string, decision = 'approve'): Promise<{ ok: boolean; message: string }> {
  return await post(`/api/join-keys/versions/${versionId}/review`, { decision })
}

export async function enableVersion(versionId: string): Promise<{ ok: boolean; message: string }> {
  return await post(`/api/join-keys/versions/${versionId}/enable`, {})
}

export async function disableVersion(versionId: string): Promise<{ ok: boolean; message: string }> {
  return await post(`/api/join-keys/versions/${versionId}/disable`, {})
}

export async function aggregate(planId: string, versionId: string | null): Promise<AggregationResult> {
  return await post('/api/join-keys/aggregate', { plan_id: planId, version_id: versionId })
}

export async function loadReports(): Promise<IssuedReport[]> {
  const response = await request('/api/join-keys/reports')
  if (!response.ok) throw new Error('报告清单读取失败')
  const payload = (await response.json()) as { reports: IssuedReport[] }
  return payload.reports
}

export async function recomputeReport(reportId: string): Promise<AggregationResult> {
  return await post(`/api/join-keys/reports/${reportId}/recompute`, {})
}

export async function loadDrafts(): Promise<ReviewDraft[]> {
  const response = await request('/api/join-keys/drafts')
  if (!response.ok) throw new Error('复核草稿读取失败')
  const payload = (await response.json()) as { drafts: ReviewDraft[] }
  return payload.drafts
}

export async function submitDraft(
  draftId: string,
  mode: 'continue' | 'correct',
): Promise<{ ok: boolean; confirmed_now: boolean; message: string; result?: AggregationResult | null }> {
  return await post(`/api/join-keys/drafts/${draftId}/submit`, { mode })
}
