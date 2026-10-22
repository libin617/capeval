<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRetention, listUsers, purgeRetention } from '../api'
import { getCurrentUser } from '../api/client'
import type { RetentionStats, UserInfo } from '../api/types'

const retention = ref<RetentionStats | null>(null)
const users = ref<UserInfo[]>([])
const purging = ref(false)
const roleLabel: Record<string, string> = { admin: '管理员', evaluator: '评测人员', viewer: '只读成员' }

/** 仅管理员可见清理按钮(后端 purge 接口同样做了 require_admin 强制) */
const canPurge = computed(() => getCurrentUser() === 'admin')

async function load() {
  try {
    retention.value = await getRetention()
  } catch { /* 忽略 */ }
  try {
    users.value = await listUsers()
  } catch { /* 忽略 */ }
}

async function onPurge() {
  await ElMessageBox.confirm(
    `将物理删除保留期(>${retention.value?.retention_days ?? 180} 天)外的轨迹事件,被归因证据链引用的片段会保留。该操作不可恢复,确认执行?`,
    '清理过期轨迹', { confirmButtonText: '确认清理', cancelButtonText: '取消', type: 'warning' },
  )
  purging.value = true
  try {
    const r = await purgeRetention()
    ElMessage.success(`已清理 ${r.removed} 条过期轨迹事件(保留期 ${r.retention_days} 天)`)
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message || '清理失败')
  } finally {
    purging.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>数据保留策略(PRD 7.4:轨迹事件保留 180 天,报告永久保留,证据链片段豁免清理)</span>
          <el-button
            v-if="canPurge" type="danger" plain :loading="purging"
            :disabled="!retention || retention.trace_events_expired === 0" @click="onPurge"
          >
            清理过期轨迹({{ retention?.trace_events_expired ?? 0 }})
          </el-button>
        </div>
      </template>
      <el-alert
        v-if="retention && retention.trace_events_expired > 0" type="warning" :closable="false" show-icon
        :title="`有 ${retention.trace_events_expired} 条轨迹事件已超出保留期,可执行清理释放存储`" class="mb"
      />
      <div v-if="retention" class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">轨迹保留期</div>
          <div class="stat-value">{{ retention.retention_days }}<span class="stat-unit">天</span></div>
          <div class="stat-note">策略周期,可通过配置调整</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">轨迹事件总量</div>
          <div class="stat-value">{{ retention.trace_events_total.toLocaleString() }}</div>
          <div class="stat-note">全部已落库轨迹事件</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">超出保留期</div>
          <div class="stat-value danger">{{ retention.trace_events_expired.toLocaleString() }}</div>
          <div class="stat-note">清理时豁免证据链引用片段</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">报告数量</div>
          <div class="stat-value">{{ retention.reports_total.toLocaleString() }}</div>
          <div class="stat-note">报告永久保留,不受清理影响</div>
        </div>
      </div>
      <el-skeleton v-else :rows="3" animated />
      <div v-if="retention" class="retention-note">{{ retention.note }}</div>
    </el-card>

    <el-card shadow="never" class="mt">
      <template #header>成员与角色(PRD 7.3:admin 管理员 / evaluator 评测人员 / viewer 只读成员)</template>
      <el-table :data="users" size="small">
        <el-table-column label="用户名" prop="username" width="140" />
        <el-table-column label="显示名" prop="display_name" min-width="140" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : row.role === 'evaluator' ? 'primary' : 'info'" size="small">
              {{ roleLabel[row.role] || row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="权限说明" min-width="320">
          <template #default="{ row }">
            <span v-if="row.role === 'admin'">全部权限:写操作、Agent 凭证管理、数据清理、复核操作</span>
            <span v-else-if="row.role === 'evaluator'">评测操作:任务/评测用例/评测集/根因库写操作、复核操作;不可管理凭证与系统数据</span>
            <span v-else>只读查看;唯一可写操作为对归因结论提交异议评论</span>
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        type="info" :closable="false" show-icon class="mt"
        title="MVP 通过顶栏头像下拉切换身份(X-Acting-User);生产环境应替换为统一登录/SSO。"
      />
    </el-card>
  </div>
</template>

<style scoped>
.admin-page { max-width: 1100px; }
.mt { margin-top: 16px; }
.mb { margin-bottom: 14px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.stat-card { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 16px; }
.stat-label { font-size: 13px; color: var(--el-text-color-secondary); }
.stat-value { font-size: 28px; font-weight: 700; margin: 6px 0 4px; }
.stat-unit { font-size: 13px; font-weight: 400; margin-left: 4px; color: var(--el-text-color-secondary); }
.stat-value.danger { color: var(--el-color-danger); }
.stat-note { font-size: 12px; color: var(--el-text-color-placeholder); }
.retention-note { margin-top: 14px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
