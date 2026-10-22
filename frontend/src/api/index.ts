import { http } from './client'
import type {
  AdapterInfo, AgentCreatePayload, AgentInfo, AttributionCommentInfo, AttributionInfo,
  AttributionReviewPayload, CaseSet, CompareResult, DimensionScoreInfo, ExecutionDetail,
  FalsePositiveStats, JudgeConfig, JudgeConfigPayload, JudgeTestResult, LibraryVersionInfo,
  NotificationList, ReportPayload, RetentionPurgeResult,
  RetentionStats, RootCause, RootCauseCreatePayload, RootCauseUpdatePayload, RootCauseWriteResult,
  RunAttributions, RunCreatePayload, RunDetail, RunInfo, RunScores, ShareResult, TestCase,
  TraceEventInfo, TrendData, UserInfo, VerifyResult, WeightTemplate, WeightTemplatePayload,
  SelfCheckResult, BuildDetectResult,
} from './types'

// Agents
export const listAgents = (params?: Record<string, string>) =>
  http.get<AgentInfo[]>('/agents', { params }).then((r) => r.data)
export const listAdapters = () => http.get<AdapterInfo[]>('/agents/adapters').then((r) => r.data)
export const createAgent = (p: AgentCreatePayload) => http.post<AgentInfo>('/agents', p).then((r) => r.data)
export const updateAgent = (id: number, p: Partial<AgentCreatePayload>) =>
  http.patch<AgentInfo>(`/agents/${id}`, p).then((r) => r.data)
export const deleteAgent = (id: number) => http.delete(`/agents/${id}`)
export const verifyAgent = (id: number) => http.post<VerifyResult>(`/agents/${id}/verify`).then((r) => r.data)

// TestCases
export const listTestCases = (params?: Record<string, string>) =>
  http.get<TestCase[]>('/testcases', { params }).then((r) => r.data)
export const createTestCase = (p: Partial<TestCase>) => http.post<TestCase>('/testcases', p).then((r) => r.data)
export const updateTestCase = (id: number, p: Partial<TestCase>) =>
  http.patch<TestCase>(`/testcases/${id}`, p).then((r) => r.data)
export const deleteTestCase = (id: number) => http.delete(`/testcases/${id}`)
export const selfCheckTestCase = (id: number) =>
  http.post<SelfCheckResult>(`/testcases/${id}/self-check`).then((r) => r.data)
export const detectBuild = (repoSnapshot: Record<string, unknown>) =>
  http.post<BuildDetectResult>('/testcases/detect-build', { repo_snapshot: repoSnapshot }).then((r) => r.data)

// CaseSets
export const listCaseSets = (params?: Record<string, string>) =>
  http.get<CaseSet[]>('/casesets', { params }).then((r) => r.data)
export const createCaseSet = (p: { name: string; description: string; testcase_ids: number[] }) =>
  http.post<CaseSet>('/casesets', p).then((r) => r.data)
export const updateCaseSet = (id: number, p: { name?: string; description?: string }) =>
  http.patch<CaseSet>(`/casesets/${id}`, p).then((r) => r.data)
export const deleteCaseSet = (id: number) => http.delete(`/casesets/${id}`)
export const publishVersion = (setId: number, versionId: number) =>
  http.post(`/casesets/${setId}/versions/${versionId}/publish`).then((r) => r.data)
export const createDraftVersion = (setId: number, testcaseIds: number[]) =>
  http.post(`/casesets/${setId}/versions`, { testcase_ids: testcaseIds }).then((r) => r.data)
export const replaceDraftItems = (setId: number, versionId: number, testcaseIds: number[]) =>
  http.put(`/casesets/${setId}/versions/${versionId}/items`, { testcase_ids: testcaseIds }).then((r) => r.data)

// Config
export const listWeightTemplates = () => http.get<WeightTemplate[]>('/weight-templates').then((r) => r.data)
export const createWeightTemplate = (p: WeightTemplatePayload) =>
  http.post<WeightTemplate>('/weight-templates', p).then((r) => r.data)
export const updateWeightTemplate = (id: number, p: Partial<WeightTemplatePayload>) =>
  http.put<WeightTemplate>(`/weight-templates/${id}`, p).then((r) => r.data)
export const deleteWeightTemplate = (id: number) => http.delete(`/weight-templates/${id}`)

// RootCause library (M5, FR-07)
export const listRootCauses = () => http.get<RootCause[]>('/root-causes').then((r) => r.data)
export const getSignalCatalog = () =>
  http.get<{ signals: string[] }>('/root-causes/signal-catalog').then((r) => r.data.signals)
export const getLibraryVersion = () =>
  http.get<LibraryVersionInfo>('/root-causes/library-version').then((r) => r.data)
export const listLibraryVersions = () =>
  http.get<LibraryVersionInfo[]>('/root-causes/library-versions').then((r) => r.data)
export const createRootCause = (p: RootCauseCreatePayload) =>
  http.post<RootCauseWriteResult>('/root-causes', p).then((r) => r.data)
export const updateRootCause = (id: number, p: RootCauseUpdatePayload) =>
  http.patch<RootCauseWriteResult>(`/root-causes/${id}`, p).then((r) => r.data)
export const deleteRootCause = (id: number) => http.delete(`/root-causes/${id}`)

// Runs (M2)
export const listRuns = (params?: Record<string, string>) =>
  http.get<RunInfo[]>('/runs', { params }).then((r) => r.data)
export const createRun = (p: RunCreatePayload) => http.post<RunInfo>('/runs', p).then((r) => r.data)
export const getRun = (id: number) => http.get<RunDetail>(`/runs/${id}`).then((r) => r.data)
export const submitRun = (id: number) => http.post<RunInfo>(`/runs/${id}/submit`).then((r) => r.data)
export const cancelRun = (id: number) => http.post<RunInfo>(`/runs/${id}/cancel`).then((r) => r.data)
export const retryRun = (id: number) => http.post<RunInfo>(`/runs/${id}/retry`).then((r) => r.data)
export const updateRun = (id: number, p: Partial<RunCreatePayload>) =>
  http.patch<RunInfo>(`/runs/${id}`, p).then((r) => r.data)
export const deleteRun = (id: number) => http.delete(`/runs/${id}`)
export const getExecution = (id: number) => http.get<ExecutionDetail>(`/executions/${id}`).then((r) => r.data)
export const listExecutionEvents = (id: number) =>
  http.get<TraceEventInfo[]>(`/executions/${id}/events`).then((r) => r.data)

// Scores (M3)
export const getRunScores = (id: number) => http.get<RunScores>(`/runs/${id}/scores`).then((r) => r.data)
export const getExecutionScores = (id: number) =>
  http.get<DimensionScoreInfo[]>(`/executions/${id}/scores`).then((r) => r.data)

// Attributions (M4)
export const getRunAttributions = (id: number) =>
  http.get<RunAttributions>(`/runs/${id}/attributions`).then((r) => r.data)
export const getExecutionAttributions = (id: number) =>
  http.get<AttributionInfo[]>(`/executions/${id}/attributions`).then((r) => r.data)
export const reviewAttribution = (id: number, p: AttributionReviewPayload) =>
  http.patch<AttributionInfo>(`/attributions/${id}`, p).then((r) => r.data)
export const completeRunReview = (id: number) =>
  http.post<RunInfo>(`/runs/${id}/review/complete`).then((r) => r.data)
export const waiveRunReview = (id: number) =>
  http.post<RunInfo>(`/runs/${id}/review/waive`).then((r) => r.data)

// Reports (M5, FR-09)
export const getReport = (id: number) => http.get<ReportPayload>(`/runs/${id}/report`).then((r) => r.data)
export const exportReportUrl = (id: number, format: 'html' | 'pdf') =>
  `/api/runs/${id}/report/export?format=${format}`
export const createShare = (id: number, p: { public: boolean; days: number }) =>
  http.post<ShareResult>(`/runs/${id}/report/share`, p).then((r) => r.data)
export const revokeShare = (id: number) => http.delete(`/runs/${id}/report/share`)
export const getSharedReport = (token: string) =>
  http.get<ReportPayload>(`/reports/shared/${token}`).then((r) => r.data)
export const compareRuns = (left: number, right: number) =>
  http.get<CompareResult>('/report/compare', { params: { left, right } }).then((r) => r.data)

// ---- M6 加固:通知 / 权限 / 数据保留 / 统计 / 异议评论 ----
export const listNotifications = (params?: Record<string, string | number | boolean>) =>
  http.get<NotificationList>('/notifications', { params }).then((r) => r.data)
export const scanNotifications = () =>
  http.post<{ created: number }>('/notifications/scan').then((r) => r.data)
export const markNotificationRead = (id: number) => http.post(`/notifications/${id}/read`)
export const markAllNotificationsRead = () =>
  http.post<{ marked: number }>('/notifications/read-all').then((r) => r.data)

export const listUsers = () => http.get<UserInfo[]>('/admin/users').then((r) => r.data)
export const getRetention = () => http.get<RetentionStats>('/admin/retention').then((r) => r.data)
export const purgeRetention = () =>
  http.post<RetentionPurgeResult>('/admin/retention/purge').then((r) => r.data)

export const getTrend = (agentId?: number) =>
  http.get<TrendData>('/stats/trend', { params: agentId ? { agent_id: agentId } : {} }).then((r) => r.data)
export const getFalsePositiveStats = () =>
  http.get<FalsePositiveStats>('/stats/false-positive-stats').then((r) => r.data)

export const listAttributionComments = (attributionId: number) =>
  http.get<AttributionCommentInfo[]>(`/attributions/${attributionId}/comments`).then((r) => r.data)
export const addAttributionComment = (attributionId: number, content: string) =>
  http.post<AttributionCommentInfo>(`/attributions/${attributionId}/comments`, { content }).then((r) => r.data)

// LLM-as-Judge 评审模型配置(支持多套)
export const listJudgeConfigs = () => http.get<JudgeConfig[]>('/judge-configs').then((r) => r.data)
export const createJudgeConfig = (p: JudgeConfigPayload) =>
  http.post<JudgeConfig>('/judge-configs', p).then((r) => r.data)
export const updateJudgeConfig = (id: number, p: Partial<JudgeConfigPayload>) =>
  http.put<JudgeConfig>(`/judge-configs/${id}`, p).then((r) => r.data)
export const deleteJudgeConfig = (id: number) => http.delete(`/judge-configs/${id}`)
export const testJudgeConfig = (p: {
  id?: number; base_url: string; model: string; api_key?: string; timeout_seconds?: number
}) => http.post<JudgeTestResult>('/judge-configs/test', p).then((r) => r.data)
export const setVersionJudgeConfig = (setId: number, versionId: number, judgeConfigId: number | null) =>
  http.put(`/casesets/${setId}/versions/${versionId}/judge-config`, { judge_config_id: judgeConfigId })
    .then((r) => r.data)

