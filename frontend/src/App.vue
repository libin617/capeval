<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Monitor, Collection, Files, Connection, VideoPlay, DataAnalysis, TrendCharts, Setting, Bell, User, Cpu, ScaleToOriginal } from '@element-plus/icons-vue'
import {
  listNotifications, markAllNotificationsRead, markNotificationRead, listUsers,
} from './api'
import { getCurrentUser, setCurrentUser } from './api/client'
import type { NotificationInfo, UserInfo } from './api/types'

const route = useRoute()
const router = useRouter()
const active = computed(() => route.path)
/** 分享页为免登录只读视图,不渲染侧边导航 */
const bare = computed(() => route.name === 'sharedReport')

// ---- 身份切换(M6,PRD 7.3 MVP) ----
const users = ref<UserInfo[]>([])
const actingUser = ref(getCurrentUser())
const currentRole = computed(() => users.value.find((u) => u.username === actingUser.value)?.role || 'admin')
const isAdmin = computed(() => currentRole.value === 'admin')
const roleLabel: Record<string, string> = { admin: '管理员', evaluator: '评测人员', viewer: '只读成员' }

async function loadUsers() {
  try {
    users.value = await listUsers()
  } catch {
    /* 用户列表加载失败不阻塞主界面 */
  }
}

function switchUser(username: string) {
  setCurrentUser(username)
  actingUser.value = username
  window.location.reload() // 切换身份后整体刷新,避免缓存旧权限视图
}

// ---- 站内通知(M6,PRD 7.2) ----
const unread = ref(0)
const notifications = ref<NotificationInfo[]>([])
const bellVisible = ref(false)
let pollTimer: number | undefined

const CATEGORY_META: Record<string, { label: string; type: 'primary' | 'warning' | 'danger' | 'success' }> = {
  run_status: { label: '任务状态', type: 'primary' },
  agent_alert: { label: 'Agent 告警', type: 'danger' },
  rc10_candidate: { label: '集体失败', type: 'warning' },
  review_overdue: { label: '复核超期', type: 'warning' },
}

async function loadNotifications() {
  try {
    const data = await listNotifications({ limit: 30 })
    unread.value = data.unread
    notifications.value = data.items
  } catch {
    /* 通知轮询失败静默,不打扰主流程 */
  }
}

async function onNotificationClick(n: NotificationInfo) {
  if (!n.read_at) {
    try {
      await markNotificationRead(n.id)
    } catch { /* 忽略 */ }
  }
  bellVisible.value = false
  loadNotifications()
  if (n.link) router.push(n.link)
}

async function onMarkAllRead() {
  try {
    await markAllNotificationsRead()
  } catch { /* 忽略 */ }
  loadNotifications()
}

onMounted(() => {
  loadUsers()
  loadNotifications()
  pollTimer = window.setInterval(loadNotifications, 15000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <router-view v-if="bare" />
  <el-container v-else class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <div class="brand-name">CapEval</div>
        <div class="brand-sub">Code Agent 评测平台</div>
      </div>
      <el-menu :default-active="active" router class="menu">
        <el-menu-item index="/runs">
          <el-icon><VideoPlay /></el-icon><span>评测任务</span>
        </el-menu-item>
        <el-menu-item index="/compare">
          <el-icon><DataAnalysis /></el-icon><span>任务对比</span>
        </el-menu-item>
        <el-menu-item index="/trend">
          <el-icon><TrendCharts /></el-icon><span>趋势看板</span>
        </el-menu-item>
        <el-menu-item index="/agents">
          <el-icon><Monitor /></el-icon><span>Agent 管理</span>
        </el-menu-item>
        <el-menu-item index="/testcases">
          <el-icon><Files /></el-icon><span>评测用例</span>
        </el-menu-item>
        <el-menu-item index="/casesets">
          <el-icon><Collection /></el-icon><span>评测集</span>
        </el-menu-item>
        <el-menu-item index="/root-causes">
          <el-icon><Connection /></el-icon><span>根因库</span>
        </el-menu-item>
        <el-menu-item index="/judge-config">
          <el-icon><Cpu /></el-icon><span>评审模型</span>
        </el-menu-item>
        <el-menu-item index="/weight-templates">
          <el-icon><ScaleToOriginal /></el-icon><span>权重模板</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/admin">
          <el-icon><Setting /></el-icon><span>系统管理</span>
        </el-menu-item>
      </el-menu>
      <div class="aside-foot">M6 · 平台加固</div>
    </el-aside>
    <el-container>
      <el-header class="header" height="56px">
        <span class="page-title">{{ route.meta.title }}</span>
        <div class="header-right">
          <el-popover v-model:visible="bellVisible" trigger="click" width="380" placement="bottom-end">
            <template #reference>
              <el-badge :value="unread" :hidden="unread === 0" :max="99" class="bell-badge">
                <el-button :icon="Bell" circle text />
              </el-badge>
            </template>
            <div class="notif-head">
              <span class="notif-title">站内通知</span>
              <el-button link type="primary" size="small" @click="onMarkAllRead">全部已读</el-button>
            </div>
            <el-empty v-if="notifications.length === 0" description="暂无通知" :image-size="60" />
            <div v-else class="notif-list">
              <div
                v-for="n in notifications" :key="n.id"
                class="notif-item" :class="{ unread: !n.read_at }"
                @click="onNotificationClick(n)"
              >
                <div class="notif-line">
                  <el-tag size="small" :type="CATEGORY_META[n.category]?.type || 'info'">
                    {{ CATEGORY_META[n.category]?.label || n.category }}
                  </el-tag>
                  <span class="notif-time">{{ n.created_at?.replace('T', ' ').slice(0, 16) }}</span>
                </div>
                <div class="notif-item-title">{{ n.title }}</div>
                <div class="notif-item-content">{{ n.content }}</div>
              </div>
            </div>
          </el-popover>
          <el-dropdown @command="switchUser">
            <span class="user-chip">
              <el-icon><User /></el-icon>
              <span>{{ users.find((u) => u.username === actingUser)?.display_name || actingUser }}</span>
              <el-tag size="small" type="info">{{ roleLabel[currentRole] || currentRole }}</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="u in users" :key="u.id" :command="u.username"
                  :disabled="u.username === actingUser"
                >
                  {{ u.display_name }}({{ roleLabel[u.role] || u.role }})
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout { height: 100vh; }
.aside { border-right: 1px solid var(--el-border-color); display: flex; flex-direction: column; }
.brand { padding: 20px 18px 14px; }
.brand-name { font-size: 20px; font-weight: 700; }
.brand-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
.menu { border-right: none; flex: 1; }
.aside-foot { padding: 12px 18px; font-size: 12px; color: var(--el-text-color-placeholder); }
.header { display: flex; align-items: center; border-bottom: 1px solid var(--el-border-color); }
.page-title { font-size: 16px; font-weight: 600; }
.header-right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
.bell-badge :deep(.el-badge__content) { z-index: 2; }
.user-chip { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; color: var(--el-text-color-primary); outline: none; }
.notif-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.notif-title { font-weight: 600; font-size: 14px; }
.notif-list { max-height: 380px; overflow-y: auto; }
.notif-item { padding: 8px 6px; border-bottom: 1px solid var(--el-border-color-lighter); cursor: pointer; border-radius: 4px; }
.notif-item:hover { background: var(--el-fill-color-light); }
.notif-item.unread .notif-item-title { font-weight: 600; }
.notif-line { display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }
.notif-time { font-size: 12px; color: var(--el-text-color-placeholder); }
.notif-item-title { font-size: 13px; }
.notif-item-content { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; display: -webkit-box; line-clamp: 2; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.main { background: var(--el-fill-color-light); }
</style>
