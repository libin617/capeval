import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/runs' },
    { path: '/runs', name: 'runs', component: () => import('../views/RunsView.vue'), meta: { title: '评测任务' } },
    { path: '/runs/new', name: 'runCreate', component: () => import('../views/RunCreateView.vue'), meta: { title: '新建评测' } },
    { path: '/runs/:id', name: 'runDetail', component: () => import('../views/RunDetailView.vue'), meta: { title: '任务详情' } },
    { path: '/runs/:id/report', name: 'runReport', component: () => import('../views/ReportView.vue'), meta: { title: '评测报告' } },
    { path: '/compare', name: 'compare', component: () => import('../views/CompareView.vue'), meta: { title: '双任务对比' } },
    { path: '/trend', name: 'trend', component: () => import('../views/TrendView.vue'), meta: { title: '趋势看板' } },
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { title: '系统管理' } },
    { path: '/share/:token', name: 'sharedReport', component: () => import('../views/SharedReportView.vue'), meta: { title: '分享报告' } },
    { path: '/executions/:id', name: 'executionTrace', component: () => import('../views/ExecutionTraceView.vue'), meta: { title: '轨迹回放' } },
    { path: '/agents', name: 'agents', component: () => import('../views/AgentsView.vue'), meta: { title: 'Agent 管理' } },
    { path: '/testcases', name: 'testcases', component: () => import('../views/TestCasesView.vue'), meta: { title: '评测用例' } },
    { path: '/casesets', name: 'casesets', component: () => import('../views/CaseSetsView.vue'), meta: { title: '评测集' } },
    { path: '/root-causes', name: 'rootCauses', component: () => import('../views/RootCausesView.vue'), meta: { title: '根因库' } },
    { path: '/badcases', name: 'badCases', component: () => import('../views/BadCasesView.vue'), meta: { title: 'BadCase 回流' } },
    { path: '/judge-config', name: 'judgeConfig', component: () => import('../views/JudgeConfigView.vue'), meta: { title: '评审模型' } },
    { path: '/weight-templates', name: 'weightTemplates', component: () => import('../views/WeightTemplatesView.vue'), meta: { title: '权重模板' } },
  ],
})

export default router
