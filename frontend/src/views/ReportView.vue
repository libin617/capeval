<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createShare, exportReportUrl, getReport, revokeShare } from '../api'
import type { ReportPayload, ShareResult } from '../api/types'
import ReportBody from '../components/ReportBody.vue'

const route = useRoute()
const router = useRouter()
const runId = Number(route.params.id)

const report = ref<ReportPayload | null>(null)
const loading = ref(true)
const error = ref('')
const share = ref<ShareResult | null>(null)
const shareDays = ref(7)
const sharePublic = ref(true)
const sharing = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await getReport(runId)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function download(format: 'html' | 'pdf') {
  window.open(exportReportUrl(runId, format), '_blank')
}

async function onShare() {
  sharing.value = true
  try {
    share.value = await createShare(runId, { public: sharePublic.value, days: shareDays.value })
    ElMessage.success('分享链接已生成')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    sharing.value = false
  }
}

async function onRevoke() {
  try {
    await ElMessageBox.confirm('撤销后该分享链接立即失效,确认继续?', '撤销分享', { type: 'warning' })
  } catch { return }
  try {
    await revokeShare(runId)
    share.value = null
    ElMessage.success('已撤销分享链接')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function copyLink() {
  if (!share.value) return
  const url = `${location.origin}/share/${share.value.token}`
  navigator.clipboard?.writeText(url).then(
    () => ElMessage.success('链接已复制'),
    () => ElMessage.warning(`请手动复制:${url}`),
  )
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="head-row">
      <el-button @click="router.push(`/runs/${runId}`)">← 返回任务详情</el-button>
      <div class="ops">
        <el-button type="primary" plain @click="download('html')">导出 HTML</el-button>
        <el-button type="primary" plain @click="download('pdf')">导出 PDF</el-button>
        <el-dropdown trigger="click">
          <el-button type="success" plain>分享链接</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <div class="share-panel">
                <div class="share-row">
                  <el-checkbox v-model="sharePublic">免登录访问</el-checkbox>
                  <el-input-number v-model="shareDays" :min="1" :max="30" size="small" :disabled="!sharePublic" />
                  <span class="hint">天</span>
                </div>
                <div class="share-row">
                  <el-button size="small" type="primary" :loading="sharing" @click="onShare">生成</el-button>
                  <el-button v-if="share" size="small" @click="copyLink">复制链接</el-button>
                  <el-button v-if="share" size="small" type="danger" plain @click="onRevoke">撤销</el-button>
                </div>
                <div v-if="share" class="share-row">
                  <span class="hint">
                    /share/{{ share.token }} ·
                    {{ share.public ? `免登录至 ${share.expires_at}` : '需登录访问' }}
                  </span>
                </div>
              </div>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <el-alert v-if="error" type="error" :title="error" :closable="false" style="margin-top: 14px" />
    <ReportBody v-if="report" :report="report" />
  </div>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: center; }
.ops { display: flex; gap: 10px; }
.share-panel { padding: 10px 12px; width: 300px; }
.share-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); word-break: break-all; }
</style>
