export interface AgentInfo {
  id: number
  name: string
  version_label: string
  access_type: 'cli' | 'api'
  adapter_key: string
  adapter_config: Record<string, unknown>
  credential_masked: string
  resource_limits: Record<string, unknown>
  cost_params: Record<string, unknown>
  status: 'unverified' | 'available' | 'verify_failed' | 'unavailable'
  last_verify_at: string | null
  last_verify_error: string
  created_at: string
}

export interface AdapterInfo {
  key: string
  name: string
  access_type: string
  description: string
  required_config: string[]
}

export interface AgentCreatePayload {
  name: string
  version_label: string
  access_type: 'cli' | 'api'
  adapter_key: string
  adapter_config: Record<string, unknown>
  credential: string
  resource_limits: Record<string, unknown>
  cost_params: Record<string, unknown>
}

export interface VerifyResult {
  agent_id: number
  status: string
  passed: boolean
  message: string
}

export interface ScriptTurn {
  turn: number
  message: string
  intent_tag: string
}

export interface AutoCommand {
  cmd: string
  expect_exit_code?: number
}

export interface Acceptance {
  auto_commands?: AutoCommand[]
  semantic_points?: string[]
  process_expectations?: string[]
}

export interface EfficiencyBaseline {
  input_tokens?: number
  wall_seconds?: number
}

export interface TestCase {
  id: number
  title: string
  task_script: ScriptTurn[]
  repo_snapshot: Record<string, unknown>
  env_def: Record<string, unknown>
  acceptance: Acceptance
  gold_patch: string
  efficiency_baseline: EfficiencyBaseline
  difficulty: 'easy' | 'medium' | 'hard'
  tags: string[]
  est_minutes: number
  status: string
  created_at: string
}

export interface SelfCheckLog {
  command: string
  exit_code: number
  expect_exit_code: number
  stdout: string
  stderr: string
}

export interface SelfCheckResult {
  passed: boolean
  stage: string
  file_count: number
  install_logs: SelfCheckLog[]
  acceptance_logs: SelfCheckLog[]
  error: string
}

export interface BuildDetectResult {
  build_system: string
  install_commands: string[]
  suggested_test_cmd: string
  confidence: number
}

export interface CaseSetItem {
  testcase_id: number
  order_no: number
  title: string
  difficulty: string
  tags: string[]
}

export interface CaseSetVersion {
  id: number
  version_no: number
  status: 'draft' | 'published'
  published_at: string | null
  judge_config_id: number | null
  judge_config_name: string
  items: CaseSetItem[]
}

export interface CaseSet {
  id: number
  name: string
  description: string
  is_builtin: boolean
  created_at: string
  versions: CaseSetVersion[]
}

export interface WeightTemplate {
  id: number
  name: string
  is_default: boolean
  weights: Record<string, number>
}

export interface WeightTemplatePayload {
  name: string
  weights: Record<string, number>
  is_default?: boolean
}

export type Responsibility = 'agent' | 'platform' | 'third_party' | 'case_maintainer'

export interface DetectionRule {
  signal: string
  primary?: boolean
}

export interface DetectionSignals {
  rules?: (DetectionRule | string)[]
  llm_prompt?: string
  related_dimensions?: string[]
}

export interface RootCause {
  id: number
  code: string
  level: 1 | 2 | 3
  parent_id: number | null
  name: string
  description: string
  responsibility: Responsibility
  detection_signals: DetectionSignals
  enabled: boolean
  introduced_version: number
  reference_count: number
  children?: RootCause[]
}

export interface RootCauseCreatePayload {
  level: 1 | 2 | 3
  parent_id?: number | null
  name: string
  description?: string
  responsibility?: Responsibility
  detection_signals?: DetectionSignals
  source_execution_id?: number | null
  note?: string
}

export interface RootCauseUpdatePayload {
  name?: string
  description?: string
  responsibility?: Responsibility
  detection_signals?: DetectionSignals
  enabled?: boolean
  note?: string
}

export interface RootCauseWriteResult {
  rootcause: RootCause
  library_version: number
  warning: string
}

export interface LibraryVersionInfo {
  version_no: number
  note: string
}

// ---- Runs (M2) ----

export type RunStatus =
  | 'draft' | 'queued' | 'running' | 'scoring' | 'attributing' | 'reviewing'
  | 'completed' | 'failed' | 'canceled'

export type CaseStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled'

export interface RunProgress {
  total: number
  pending: number
  running: number
  succeeded: number
  failed: number
  canceled: number
}

export interface RunInfo {
  id: number
  name: string
  agent_id: number
  agent_name: string
  agent_version_label: string
  caseset_version_id: number
  caseset_name: string
  caseset_version_no: number
  weight_template_id: number | null
  weight_snapshot: Record<string, number>
  rootcause_version: number
  review_policy: 'skip' | 'sample' | 'full'
  fail_threshold: number
  baseline_run_id: number | null
  pass_k: number
  judge_config_id?: number | null
  judge_config_name?: string
  status: RunStatus
  error: string
  warning?: string
  created_by: string
  created_at: string
  submitted_at: string | null
  completed_at: string | null
  progress: RunProgress
}

export interface CaseExecutionInfo {
  id: number
  run_id: number
  testcase_id: number
  order_no: number
  status: CaseStatus
  rerun_count: number
  resource_usage: Record<string, unknown>
  error: string
  started_at: string | null
  finished_at: string | null
  testcase_title: string
  difficulty: string
}

export interface RunDetail extends RunInfo {
  cases: CaseExecutionInfo[]
}

export interface RunCreatePayload {
  name?: string
  agent_id: number
  caseset_version_id: number
  weight_template_id?: number | null
  rootcause_version?: number
  review_policy?: 'skip' | 'sample' | 'full'
  fail_threshold?: number
  baseline_run_id?: number | null
  judge_config_id?: number | null
  pass_k?: number
  created_by?: string
}

export interface TraceEventInfo {
  id: number
  execution_id: number
  seq: number
  turn: number
  event_type: 'message' | 'tool_call' | 'file_change' | 'terminal' | 'resource' | 'system'
  actor: string
  payload: Record<string, unknown>
  truncated: boolean
  created_at: string
}

export interface TaskTurn {
  turn?: number
  message: string
  intent_tag?: string
}

export interface AcceptanceSpec {
  auto_commands?: (string | { cmd: string; expect_exit_code?: number })[]
  semantic_points?: string[]
  process_expectations?: string[]
  manual_checks?: string[]
  [key: string]: unknown
}

export interface ExecutionDetail extends CaseExecutionInfo {
  tags: string[]
  est_minutes: number
  task_script?: TaskTurn[]
  acceptance?: AcceptanceSpec
}

// ---- Scores (M3, FR-05) ----

export interface ScoreSignal {
  name: string
  verdict: 'pass' | 'warn' | 'fail' | 'info'
  detail: string
  deduction: number
}

export interface DimensionScoreDetails {
  scored: boolean
  method: 'rule' | 'rule_approx' | 'llm'
  note: string
  signals: ScoreSignal[]
  stats: Record<string, unknown>
}

export interface DimensionScoreInfo {
  dimension: string
  score: number
  details: DimensionScoreDetails
}

export interface DimensionSummaryItem {
  name: string
  weight: number
  score: number | null
  scored_cases: number
  selected?: boolean
}

export interface CaseScores {
  execution_id: number
  testcase_id: number
  testcase_title: string
  status: string
  quality_score: number | null
  efficiency_score: number | null
  dimensions: Record<string, number | null>
}

export interface RunScores {
  run_id: number
  quality_score: number | null
  efficiency_score: number | null
  dimension_summary: Record<string, DimensionSummaryItem>
  cases: CaseScores[]
}

// ---- Attributions (M4, FR-06) ----

export type ReviewStatus = 'pending' | 'confirmed' | 'corrected' | 'rejected' | 'not_sampled'

export interface ReviewHistoryItem {
  action: string
  by: string
  at: string
  from_rootcause_id: number | null
  to_rootcause_id: number | null
  note: string
  from_status?: string
}

export interface RootCauseRef {
  id: number
  code: string
  name: string
  level: number
  responsibility: string
}

export interface Evidence {
  event_seqs?: number[]
  signals?: string[]
  details?: string[]
}

export interface AttributionInfo {
  id: number
  execution_id: number
  rootcause: RootCauseRef
  is_primary: boolean
  confidence: number
  source: 'rule' | 'llm' | 'human' | string
  evidence: Evidence
  review_status: ReviewStatus
  reviewed_by: string
  review_note: string
  review_history: ReviewHistoryItem[]
}

export interface CaseAttribution {
  execution_id: number
  testcase_id: number
  testcase_title: string
  status: string
  primary: Record<string, unknown> | null
  secondary: Record<string, unknown>[]
}

export interface UndeterminedCase {
  execution_id: number
  testcase_id: number
  testcase_title: string
  signals: string[]
  reason: string
}

export interface AttributionDistribution {
  code: string
  name: string
  level: number
  responsibility: string
  primary: number
  secondary: number
}

export interface RunAttributions {
  run_id: number
  rootcause_version: number
  review_policy: 'skip' | 'sample' | 'full' | string
  status: RunStatus
  total_attributions: number
  attributed_cases: number
  distribution: AttributionDistribution[]
  level1_summary: Record<string, number>
  responsibility_summary: Record<string, number>
  undetermined: UndeterminedCase[]
  cases: CaseAttribution[]
  pending_review: number
  not_sampled: number
  review_mark: 'unreviewed' | 'reviewed' | string
}

export interface AttributionReviewPayload {
  action: 'confirm' | 'correct' | 'reject'
  rootcause_id?: number | null
  note?: string
  reviewed_by?: string
}

// ---- Reports (M5, FR-09) ----

export interface ReportOverview {
  name: string
  status: RunStatus
  agent_name: string
  agent_version: string
  caseset_name: string
  caseset_version_no: number
  quality_score: number | null
  efficiency_score: number | null
  review_mark: 'unreviewed' | 'reviewed' | string
  review_policy: string
  rootcause_version: number
  weight_template_id: number | null
  weight_snapshot: Record<string, number>
  case_total: number
  case_succeeded: number
  case_failed: number
  completed_at: string | null
  baseline: {
    run_id: number
    quality_score: number | null
    efficiency_score: number | null
    quality_delta: number | null
    efficiency_delta: number | null
  } | null
}

export interface ReportDimensionRow {
  dim: string
  name: string
  weight: number
  score: number | null
  scored_cases: number
  selected?: boolean
}

export interface ReportTopFailure {
  execution_id: number
  testcase_title: string
  difficulty: string
  status: string
  quality_score: number
  efficiency_score: number | null
  deduction: number
  primary: { code: string; name: string; level: number; responsibility: string } | null
  evidence: Evidence
  review_status: string
}

export interface ReportAttribution {
  total_attributions: number
  distribution: AttributionDistribution[]
  level1_summary: Record<string, number>
  responsibility_summary: Record<string, number>
  agent_responsibility_pct: number | null
  undetermined: UndeterminedCase[]
  matrix: Record<string, Record<string, number>>
}

export interface DrilldownRow {
  key: string
  cases: number
  avg_quality: number | null
  avg_efficiency: number | null
}

export interface ReportPayload {
  run_id: number
  generated_at: string
  engine: string
  overview: ReportOverview
  dimensions: ReportDimensionRow[]
  top_failures: ReportTopFailure[]
  attribution: ReportAttribution
  drilldown: {
    by_difficulty: DrilldownRow[]
    by_tag: DrilldownRow[]
    scatter: { execution_id: number; title: string; quality: number | null; efficiency: number | null }[]
  }
  appendix: {
    weight_template_id: number | null
    judge: string
    data_integrity: string[]
  }
}

export interface ShareResult {
  token: string
  public: boolean
  expires_at: string | null
}

export interface CompareDimensionDiff {
  dim: string
  name: string
  left: number | null
  right: number | null
  delta: number | null
}

export interface CompareCaseItem {
  testcase_id: number
  testcase_title: string
  left: number | null
  right: number | null
  delta: number | null
}

export interface RootCauseMigrationItem {
  code: string
  left: number
  right: number
  delta: number
}

export interface CompareResult {
  left: { run_id: number; name: string; agent: string; quality_score: number | null; efficiency_score: number | null }
  right: { run_id: number; name: string; agent: string; quality_score: number | null; efficiency_score: number | null }
  delta: { quality: number | null; efficiency: number | null }
  version_matched: boolean
  version_note: string
  dimension_diff: CompareDimensionDiff[]
  case_waterfall: CompareCaseItem[]
  regressions: CompareCaseItem[]
  rootcause_migration: RootCauseMigrationItem[]
}

// ---- M6 加固:通知 / 权限 / 数据保留 / 统计 ----

export type UserRole = 'admin' | 'evaluator' | 'viewer'

export interface UserInfo {
  id: number
  username: string
  display_name: string
  role: UserRole
}

export type NotificationCategory = 'run_status' | 'agent_alert' | 'rc10_candidate' | 'review_overdue'

export interface NotificationInfo {
  id: number
  category: NotificationCategory
  title: string
  content: string
  link: string
  read_at: string | null
  created_at: string
}

export interface NotificationList {
  unread: number
  items: NotificationInfo[]
}

export interface AttributionCommentInfo {
  id: number
  attribution_id: number
  author: string
  role: string
  content: string
  created_at: string
}

export interface RetentionStats {
  retention_days: number
  trace_events_total: number
  trace_events_expired: number
  reports_total: number
  note: string
}

export interface RetentionPurgeResult {
  removed: number
  retention_days: number
}

export interface TrendPoint {
  run_id: number
  name: string
  agent_name: string
  caseset_name: string
  completed_at: string
  quality_score: number | null
  efficiency_score: number | null
}

export interface TrendData {
  agent_id: number | null
  agent_name: string
  points: TrendPoint[]
}

export interface FalsePositiveItem {
  rootcause_id: number
  code: string
  name: string
  level: number
  enabled: boolean
  confirmed: number
  corrected: number
  rejected: number
  reviewed: number
  error_rate: number | null
  suggestion: string
}

export interface FalsePositiveStats {
  overall: { reviewed: number; error_rate: number | null }
  items: FalsePositiveItem[]
}

// ---------- LLM-as-Judge 评审模型配置(支持多套) ----------
export interface JudgeConfig {
  id: number
  name: string
  enabled: boolean
  is_default: boolean
  provider: string
  base_url: string
  model: string
  api_key_masked: string
  has_api_key: boolean
  temperature: number
  max_tokens: number
  timeout_seconds: number
  dimensions: string[]
  rubrics: Record<string, string>
  updated_by: string
  created_at: string | null
  updated_at: string | null
}

export interface JudgeConfigPayload {
  name: string
  enabled: boolean
  is_default?: boolean
  provider?: string
  base_url: string
  model: string
  api_key?: string   // 留空表示保持原密钥
  temperature?: number
  max_tokens?: number
  timeout_seconds?: number
  dimensions: string[]
  rubrics: Record<string, string>
}

export interface JudgeTestResult {
  passed: boolean
  message: string
  latency_ms: number
}
