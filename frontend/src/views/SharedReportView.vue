<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getSharedReport } from '../api'
import type { ReportPayload } from '../api/types'
import ReportBody from '../components/ReportBody.vue'

const route = useRoute()
const token = String(route.params.token)
const report = ref<ReportPayload | null>(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    report.value = await getSharedReport(token)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="share-page" v-loading="loading">
    <div class="share-head">
      <div class="brand-name">CapEval</div>
      <div class="brand-sub">只读分享报告</div>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" style="margin: 20px 0" />
    <ReportBody v-if="report" :report="report" />
  </div>
</template>

<style scoped>
.share-page { max-width: 1240px; margin: 0 auto; padding: 24px 32px 60px; }
.share-head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid var(--el-border-color); padding-bottom: 12px; }
.brand-name { font-size: 20px; font-weight: 700; }
.brand-sub { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
