<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import JSZip from 'jszip'
import { createTestCase, deleteTestCase, listTestCases, selfCheckTestCase, detectBuild, updateTestCase } from '../api'
import type { BuildDetectResult, ScriptTurn, SelfCheckResult, TestCase } from '../api/types'

const cases = ref<TestCase[]>([])
const loading = ref(false)
const drawerVisible = ref(false)
const saving = ref(false)
const editing = ref<TestCase | null>(null)   // null = 新建
const detailVisible = ref(false)
const detailRow = ref<TestCase | null>(null)
// 详情页执行环境:env_def 为自由 JSON,这里集中取 docker 相关字段
const detailEnv = computed<Record<string, any>>(() => (detailRow.value?.env_def ?? {}) as Record<string, any>)
const keyword = ref('')
const difficultyFilter = ref('')
const tagFilter = ref('')

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10
const pagedCases = computed<TestCase[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return cases.value.slice(start, start + PAGE_SIZE)
})

const TAG_OPTIONS = ['修 bug', '新功能', '重构', '测试编写', '跨文件理解', '安全修复', '冒烟']

interface RepoFileRow { path: string; content: string }

const form = reactive({
  title: '',
  difficulty: 'medium' as 'easy' | 'medium' | 'hard',
  tags: [] as string[],
  est_minutes: 30,
  turns: [{ turn: 1, message: '', intent_tag: '' }] as ScriptTurn[],
  auto_commands: '',
  install_commands: '',
  target_isolation: 'docker' as 'host' | 'docker' | 'any',
  docker_image: '',
  docker_volumes: '',
  semantic_points: '',
  process_expectations: '',
  gold_patch: '',
  baseline_tokens: null as number | null,
  baseline_seconds: null as number | null,
  repoFiles: [] as RepoFileRow[],
})

// 自检状态
const selfCheckVisible = ref(false)
const selfCheckLoading = ref(false)
const selfCheckResult = ref<SelfCheckResult | null>(null)
const selfCheckTargetId = ref<number | null>(null)
// 构建检测
const buildDetectLoading = ref(false)

const repoFilesVisible = ref(true)
const helpVisible = ref(true)

function addRepoFile() {
  form.repoFiles.push({ path: '', content: '' })
}

function removeRepoFile(idx: number) {
  form.repoFiles.splice(idx, 1)
}

/** 校验仓库快照路径:非空、非绝对路径、不含 .. 段、不重复 */
function validateRepoPaths(): string | null {
  const seen = new Set<string>()
  for (let i = 0; i < form.repoFiles.length; i++) {
    const p = form.repoFiles[i].path.trim()
    if (!p) return `第 ${i + 1} 个文件的路径不能为空`
    if (p.startsWith('/')) return `第 ${i + 1} 个文件路径不能以 / 开头:${p}`
    if (p.split('/').some((seg) => seg === '..')) return `第 ${i + 1} 个文件路径不能包含 .. :${p}`
    if (seen.has(p)) return `文件路径重复:${p}`
    seen.add(p)
  }
  return null
}

// ---------- 仓库快照导入(目录 / ZIP) ----------
const dirInput = ref<HTMLInputElement | null>(null)
const zipInput = ref<HTMLInputElement | null>(null)

/** 需跳过的目录段(VCS / 构建产物 / 依赖) */
const SKIP_DIRS = new Set([
  '.git', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
  'target', 'dist', 'build', '.idea', '.vscode', '.venv', 'venv', 'site-packages',
  '.gradle', '.idea_modules', 'out',
])
/** 需跳过的二进制/压缩文件扩展名 */
const SKIP_EXTS = new Set([
  '.pyc', '.pyo', '.class', '.jar', '.war', '.ear', '.png', '.jpg', '.jpeg', '.gif',
  '.ico', '.bmp', '.webp', '.pdf', '.zip', '.tar', '.gz', '.tgz', '.bz2', '.7z',
  '.rar', '.so', '.dll', '.dylib', '.exe', '.bin', '.mp3', '.mp4', '.avi', '.mov',
  '.wav', '.flac', '.woff', '.woff2', '.ttf', '.eot', '.otf', '.pyc', '.pyd',
])

/** 判断单个路径是否应跳过 */
function shouldSkip(relPath: string): boolean {
  const parts = relPath.split('/').filter(Boolean)
  if (!parts.length) return true
  // 任意一段命中跳过目录
  if (parts.slice(0, -1).some((p) => SKIP_DIRS.has(p))) return true
  // 隐藏文件(以 . 开头)跳过,但保留 .gitignore / .env.example 等常见配置?这里简单跳过所有隐藏文件
  if (parts.some((p) => p.startsWith('.'))) return false  // 保留隐藏配置文件
  const last = parts[parts.length - 1]
  const dot = last.lastIndexOf('.')
  if (dot > 0) {
    const ext = last.slice(dot).toLowerCase()
    if (SKIP_EXTS.has(ext)) return true
  }
  return false
}

/** 规范化路径:去掉 ./ 前缀、统一分隔符、去掉首个目录段(目录导入时的外层目录名) */
function normalizePath(raw: string, stripFirstSeg: boolean): string {
  let p = raw.replace(/\\/g, '/').replace(/^\.\//, '')
  if (stripFirstSeg) {
    const idx = p.indexOf('/')
    if (idx > 0) p = p.slice(idx + 1)
  }
  return p
}

function triggerDirImport() {
  dirInput.value?.click()
}

function triggerZipImport() {
  zipInput.value?.click()
}

/** 目录导入:递归读取所选目录下的文件 */
async function onDirImport(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files
  if (!files || !files.length) return
  const rows: RepoFileRow[] = []
  for (let i = 0; i < files.length; i++) {
    const f = files[i]
    const relPath = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
    const norm = normalizePath(relPath, true)
    if (!norm || shouldSkip(norm)) continue
    try {
      const content = await f.text()
      rows.push({ path: norm, content })
    } catch {
      // 二进制文件读取失败直接跳过
    }
  }
  applyImportedRows(rows)
  input.value = ''
}

/** ZIP 导入:用 JSZip 解压并读取文本文件 */
async function onZipImport(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const zip = await JSZip.loadAsync(file)
    const rows: RepoFileRow[] = []
    const tasks: Promise<void>[] = []
    zip.forEach((_relPath, entry) => {
      if (entry.dir) return
      const norm = normalizePath(entry.name, false)
      if (!norm || shouldSkip(norm)) return
      tasks.push(
        entry.async('string').then((content) => {
          rows.push({ path: norm, content })
        }).catch(() => { /* 二进制文件读取失败跳过 */ }),
      )
    })
    await Promise.all(tasks)
    applyImportedRows(rows)
  } catch (err) {
    ElMessage.error('ZIP 解压失败:' + (err as Error).message)
  } finally {
    input.value = ''
  }
}

/** 将导入的文件列表应用到表单(替换现有快照) */
function applyImportedRows(rows: RepoFileRow[]) {
  if (!rows.length) {
    ElMessage.warning('未导入任何文本文件(可能全部为二进制或被过滤)')
    return
  }
  form.repoFiles.splice(0, form.repoFiles.length, ...rows)
  repoFilesVisible.value = true
  ElMessage.success(`已导入 ${rows.length} 个文件`)
}

async function refresh() {
  loading.value = true
  try {
    const params: Record<string, string> = { status: 'active' }
    if (keyword.value.trim()) params.q = keyword.value.trim()
    if (difficultyFilter.value) params.difficulty = difficultyFilter.value
    if (tagFilter.value) params.tag = tagFilter.value
    cases.value = await listTestCases(params)
    page.value = 1
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    title: '', difficulty: 'medium', tags: [], est_minutes: 30,
    turns: [{ turn: 1, message: '', intent_tag: '' }],
    auto_commands: '', install_commands: '', semantic_points: '', process_expectations: '',
    target_isolation: 'docker' as 'host' | 'docker' | 'any',
    docker_image: '', docker_volumes: '',
    gold_patch: '',
    baseline_tokens: null, baseline_seconds: null,
    repoFiles: [] as RepoFileRow[],
  })
  repoFilesVisible.value = true
  drawerVisible.value = true
}

function openEdit(row: TestCase) {
  editing.value = row
  const acc = row.acceptance || {}
  const baseline = row.efficiency_baseline || {}
  const snapFiles = (row.repo_snapshot as Record<string, unknown> | undefined)?.files as Record<string, string> | undefined
  Object.assign(form, {
    title: row.title,
    difficulty: row.difficulty,
    tags: [...(row.tags || [])],
    est_minutes: row.est_minutes,
    turns: (row.task_script?.length ? row.task_script : [{ turn: 1, message: '', intent_tag: '' }])
      .map((t) => ({ turn: t.turn, message: t.message, intent_tag: t.intent_tag ?? '' })),
    auto_commands: (acc.auto_commands ?? []).map((c) => c.cmd).join('\n'),
    install_commands: ((row.env_def as Record<string, unknown> | undefined)?.install_commands as Array<{ cmd: string }> | undefined)
      ?.map((c) => c.cmd).join('\n') ?? '',
    docker_image: String((row.env_def as Record<string, unknown> | undefined)?.docker_image ?? ''),
    target_isolation: (String((row.env_def as Record<string, unknown> | undefined)?.target_isolation ?? 'docker') as 'host' | 'docker' | 'any'),
    docker_volumes: (((row.env_def as Record<string, unknown> | undefined)?.docker_volumes as string[] | undefined) ?? []).join('\n'),
    semantic_points: (acc.semantic_points ?? []).join('\n'),
    process_expectations: (acc.process_expectations ?? []).join('\n'),
    gold_patch: row.gold_patch ?? '',
    baseline_tokens: baseline.input_tokens ?? null,
    baseline_seconds: baseline.wall_seconds ?? null,
    repoFiles: snapFiles
      ? Object.entries(snapFiles).map(([path, content]) => ({ path, content }))
      : [] as RepoFileRow[],
  })
  repoFilesVisible.value = form.repoFiles.length > 0
  drawerVisible.value = true
}

function openDetail(row: TestCase) {
  detailRow.value = row
  detailVisible.value = true
}

function addTurn() {
  form.turns.push({ turn: form.turns.length + 1, message: '', intent_tag: '' })
}

function removeTurn(idx: number) {
  form.turns.splice(idx, 1)
  form.turns.forEach((t, i) => (t.turn = i + 1))
}

const difficultyMeta: Record<string, { label: string; type: 'success' | 'warning' | 'danger' }> = {
  easy: { label: '简单', type: 'success' },
  medium: { label: '中等', type: 'warning' },
  hard: { label: '困难', type: 'danger' },
}

async function submit() {
  if (form.turns.some((t) => !t.message.trim())) {
    ElMessage.warning('每轮消息不能为空')
    return
  }
  if (form.turns.length > 1 && form.turns.some((t) => !t.intent_tag.trim())) {
    ElMessage.warning('多轮脚本需为每轮标注考察意图标签')
    return
  }
  const autoCmds = form.auto_commands.split('\n').map((s) => s.trim()).filter(Boolean)
    .map((cmd) => ({ cmd, expect_exit_code: 0 }))
  const installCmds = form.install_commands.split('\n').map((s) => s.trim()).filter(Boolean)
    .map((cmd) => ({ cmd, expect_exit_code: 0 }))
  const semanticPoints = form.semantic_points.split('\n').map((s) => s.trim()).filter(Boolean)
  if (!autoCmds.length && !semanticPoints.length) {
    ElMessage.warning('至少填写一条自动化验收命令或语义验收点')
    return
  }
  const pathErr = validateRepoPaths()
  if (pathErr) {
    ElMessage.warning(pathErr)
    return
  }
  const volumeLines = form.docker_volumes.split('\n').map((s) => s.trim()).filter(Boolean)
  const badVol = volumeLines.find((v) => !v.includes(':'))
  if (badVol) {
    ElMessage.warning(`附加卷格式应为「宿主机路径:容器路径」:${badVol}`)
    return
  }
  if (form.target_isolation !== 'host' && !form.docker_image.trim()) {
    ElMessage.warning('执行方式为「仅 Docker」或「任意」时必须填写镜像(如 node:20-slim)')
    return
  }
  const repoSnapshot = {
    type: 'files' as const,
    files: form.repoFiles.reduce<Record<string, string>>((acc, f) => {
      acc[f.path.trim()] = f.content
      return acc
    }, {}),
  }
  saving.value = true
  try {
    const payload: Partial<TestCase> = {
      title: form.title,
      task_script: form.turns,
      repo_snapshot: repoSnapshot,
      acceptance: {
        auto_commands: autoCmds,
        semantic_points: semanticPoints,
        process_expectations: form.process_expectations.split('\n').map((s) => s.trim()).filter(Boolean),
      },
      efficiency_baseline: {
        ...(form.baseline_tokens ? { input_tokens: form.baseline_tokens } : {}),
        ...(form.baseline_seconds ? { wall_seconds: form.baseline_seconds } : {}),
      },
      difficulty: form.difficulty,
      tags: form.tags,
      est_minutes: form.est_minutes,
      env_def: {
        install_commands: installCmds,
        target_isolation: form.target_isolation,
        ...(form.docker_image.trim() ? { docker_image: form.docker_image.trim() } : {}),
        ...(volumeLines.length ? { docker_volumes: volumeLines } : {}),
      },
      gold_patch: form.gold_patch,
    }
    if (editing.value) {
      await updateTestCase(editing.value.id, payload)
      ElMessage.success('评测用例已保存')
    } else {
      await createTestCase(payload)
      ElMessage.success('评测用例已创建')
    }
    drawerVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function onDelete(row: TestCase) {
  await ElMessageBox.confirm(
    '删除评测用例「' + row.title + '」?若已被已发布评测集引用将自动转为归档。',
    '删除确认', { type: 'warning' },
  )
  await deleteTestCase(row.id)
  ElMessage.success('已处理')
  await refresh()
}

// ---------- 用例自检 ----------
async function runSelfCheck(row: TestCase) {
  selfCheckTargetId.value = row.id
  selfCheckResult.value = null
  selfCheckVisible.value = true
  selfCheckLoading.value = true
  try {
    selfCheckResult.value = await selfCheckTestCase(row.id)
  } catch (e) {
    ElMessage.error('自检失败: ' + (e as Error).message)
  } finally {
    selfCheckLoading.value = false
  }
}

// ---------- 构建系统检测 ----------
async function runDetectBuild() {
  if (!form.repoFiles.length) {
    ElMessage.warning('请先添加仓库快照文件')
    return
  }
  const snapshot = {
    type: 'files',
    files: form.repoFiles.reduce<Record<string, string>>((acc, f) => {
      acc[f.path.trim()] = f.content
      return acc
    }, {}),
  }
  buildDetectLoading.value = true
  try {
    const r: BuildDetectResult = await detectBuild(snapshot)
    if (r.build_system === 'unknown') {
      ElMessage.info('未识别到常见构建系统,请手动填写安装命令')
      return
    }
    if (r.install_commands.length) {
      form.install_commands = r.install_commands.join('\n')
    }
    if (r.suggested_test_cmd && !form.auto_commands.trim()) {
      form.auto_commands = r.suggested_test_cmd
    }
    ElMessage.success(`已识别构建系统:${r.build_system},已填充推荐命令`)
  } catch (e) {
    ElMessage.error('检测失败: ' + (e as Error).message)
  } finally {
    buildDetectLoading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <el-card class="help-card" shadow="never">
      <div class="help-head">
        <span class="help-title">📘 评测用例使用说明</span>
        <el-button link type="primary" @click="helpVisible = !helpVisible">
          {{ helpVisible ? '收起' : '展开' }}
        </el-button>
      </div>
      <div v-show="helpVisible" class="help-body">
        <p><b>什么是评测用例？</b>一条评测用例 = 一个可独立判定的研发任务。执行时平台会把「仓库快照」还原到临时目录，把「任务脚本」逐轮发给 Agent，最后用「验收规则」判定结果。</p>
        <el-divider content-position="left">创建流程</el-divider>
        <ol class="help-steps">
          <li><b>准备代码：</b>在「仓库快照」区点击「导入目录」选项目根目录，或「上传 ZIP」导入压缩包（自动跳过 .git / node_modules / 二进制文件），也可手动「+ 添加文件」。</li>
          <li><b>写任务脚本：</b>填多轮对话，首轮必填；多轮时每轮需标注「考察意图标签」（如缺陷修复、行为补全）。</li>
          <li><b>设验收规则：</b>「自动化验收命令」每行一条（退出码 0 即通过，如 <code>python -m pytest tests/</code>），或「语义验收点」每行一条自然语言要求（供 LLM 评审）。两者至少填一项。</li>
          <li><b>选难度与标签：</b>难度 easy/medium/hard，能力标签至少选一个，用于检索与统计。</li>
        </ol>
        <el-divider content-position="left">字段说明</el-divider>
        <ul class="help-fields">
          <li><b>仓库快照：</b>Agent 执行任务时的初始代码，执行时写入临时目录，原始目录零写入。</li>
          <li><b>自动化验收命令：</b>客观判定依据，在临时目录内执行，支持期望退出码（默认 0）。Node 项目建议用 <code>node --test test/*.test.js</code>。</li>
          <li><b>语义验收点：</b>主观判定依据，LLM Judge 会结合此处要求与维度 rubrics 打分（D4/D7/D8）。</li>
          <li><b>流程规范期望：</b>对研发流程的要求（如先跑测试再改），用于 D2 评分。</li>
          <li><b>效率基线：</b>可选，参考 token / 耗时，用于 D6 成本与效率评分。</li>
          <li><b>执行方式与镜像：</b>用例声明期望的执行环境（仅 Docker / 仅 Host / 任意）。仅 Docker 与任意必须填镜像，仅 Host 不需要；创建评测任务时会校验所选 Agent 的隔离模式与用例声明是否兼容。</li>
        </ul>
        <el-divider content-position="left">执行隔离机制</el-divider>
        <p>每次执行评测时，平台会：</p>
        <ol class="help-steps">
          <li>从数据库的「仓库快照」还原文件到一个<b>全新的临时目录</b>（如 <code>/tmp/capeval-run852-case123-xxx/</code>）。</li>
          <li>Agent 全程只在这个临时目录里读写，<b>不会触碰原始快照</b>。</li>
          <li>执行结束后（无论成功/失败/异常），临时目录会被<b>整体删除</b>。</li>
        </ol>
        <p>因此同一条用例可以反复评测，每次都是从原始快照重新还原，Agent 的修改不会残留，结果完全可比。</p>
        <el-alert type="success" :closable="false" show-icon style="margin-top: 12px">
          <template #title>仓库快照是只读真相源：只有主动「编辑」用例才会修改它，执行过程永远不会改动原始文件。</template>
        </el-alert>
        <el-alert type="info" :closable="false" show-icon style="margin-top: 12px">
          <template #title>提示：用例创建后可加入「评测集」并发布版本，再在「评测任务」中选择 Agent 与评测集版本即可开始评测。</template>
        </el-alert>
      </div>
    </el-card>

    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建评测用例</el-button>
      <el-input
        v-model="keyword" placeholder="搜索评测用例标题" clearable
        style="width: 200px" @keyup.enter="refresh" @clear="refresh"
      >
        <template #append>
          <el-button @click="refresh">查询</el-button>
        </template>
      </el-input>
      <el-select v-model="difficultyFilter" placeholder="全部难度" clearable style="width: 120px" @change="refresh">
        <el-option value="easy" label="简单" />
        <el-option value="medium" label="中等" />
        <el-option value="hard" label="困难" />
      </el-select>
      <el-select v-model="tagFilter" placeholder="全部标签" clearable style="width: 150px" @change="refresh">
        <el-option v-for="t in TAG_OPTIONS" :key="t" :value="t" :label="t" />
      </el-select>
      <span class="hint">评测用例 = 多轮任务脚本 + 仓库快照 + 环境 + 验收规则 + 效率基线</span>
    </div>

    <el-table :data="pagedCases" v-loading="loading" border>
      <el-table-column prop="title" label="标题" min-width="220" />
      <el-table-column label="难度" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="difficultyMeta[row.difficulty]?.type">
            {{ difficultyMeta[row.difficulty]?.label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="能力标签" min-width="160">
        <template #default="{ row }">
          <el-tag v-for="t in row.tags" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="轮次" width="70">
        <template #default="{ row }">{{ row.task_script?.length ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="验收" min-width="180">
        <template #default="{ row }">
          <span>自动 {{ row.acceptance?.auto_commands?.length ?? 0 }} 条 · </span>
          <span>语义 {{ row.acceptance?.semantic_points?.length ?? 0 }} 条</span>
        </template>
      </el-table-column>
      <el-table-column prop="est_minutes" label="预估(分)" width="90" />
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">查看</el-button>
          <el-button size="small" type="success" plain @click="runSelfCheck(row)">自检</el-button>
          <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="cases.length"
        layout="total, prev, pager, next" background
      />
    </div>

    <el-drawer v-model="drawerVisible" :title="editing ? `编辑评测用例 #${editing.id}` : '新建评测用例'" size="620px">
      <el-form label-width="100px" label-position="top">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="如:修复订单计算除零错误" />
        </el-form-item>

        <el-form-item label="任务脚本(多轮,首轮必填;多轮需标注每轮考察意图)" required>
          <div v-for="(t, idx) in form.turns" :key="idx" class="turn-row">
            <div class="turn-head">
              <span>第 {{ t.turn }} 轮</span>
              <el-button v-if="form.turns.length > 1" size="small" text type="danger" @click="removeTurn(idx)">
                移除
              </el-button>
            </div>
            <el-input v-model="t.message" type="textarea" :rows="2" placeholder="本轮用户消息" />
            <el-input v-model="t.intent_tag" size="small" placeholder="考察意图标签(如:需求变更/追问/指代)"
                      style="margin-top: 6px" />
          </div>
          <el-button size="small" style="margin-top: 8px" @click="addTurn">+ 添加一轮</el-button>
        </el-form-item>

        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>客观判定依据,在仓库快照还原的临时目录内逐行执行<br/>命令退出码等于期望值(默认 0)即判定该条通过<br/>用于 D1 代码正确性评分;Node 项目建议用 node --test test/*.test.js</template>
              <span>自动化验收命令 <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.auto_commands" type="textarea" :rows="3"
                    placeholder="python -m pytest tests/&#10;bandit -r ." />
        </el-form-item>
        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>主观判定依据,每行一条自然语言验收要求<br/>LLM 评审模型会结合此处要求与维度 rubrics 打分(D4/D7/D8)<br/>未配置自动化验收时,D1 正确性也依赖语义验收点判定</template>
              <span>语义验收点 <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.semantic_points" type="textarea" :rows="3"
                    placeholder="除零时抛出 ValueError&#10;正常除法结果不变" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>对研发流程的期望,每行一条<br/>例如「先运行现有测试再修改」「改动范围克制不重构无关代码」<br/>用于 D2 研发流程遵从评分</template>
              <span>流程规范期望 <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.process_expectations" type="textarea" :rows="2"
                    placeholder="应先运行现有测试再修改" />
        </el-form-item>

        <div class="inline-fields">
          <el-form-item label="难度">
            <el-select v-model="form.difficulty" style="width: 110px">
              <el-option value="easy" label="简单" />
              <el-option value="medium" label="中等" />
              <el-option value="hard" label="困难" />
            </el-select>
          </el-form-item>
          <el-form-item label="能力标签" required>
            <el-select v-model="form.tags" multiple style="width: 240px">
              <el-option v-for="t in TAG_OPTIONS" :key="t" :value="t" :label="t" />
            </el-select>
          </el-form-item>
          <el-form-item label="预估时长(分)">
            <el-input-number v-model="form.est_minutes" :min="1" :max="480" />
          </el-form-item>
        </div>

        <el-form-item label="效率基线(可选,供 D6 评分)">
          <el-input-number v-model="form.baseline_tokens" :min="0" placeholder="参考 token" />
          <el-input-number v-model="form.baseline_seconds" :min="0" placeholder="参考耗时(秒)"
                           style="margin-left: 12px" />
        </el-form-item>

        <el-form-item label="仓库快照(执行时还原到临时目录)">
          <div class="repo-block">
            <div class="repo-head">
              <el-button link type="primary" @click="repoFilesVisible = !repoFilesVisible">
                {{ repoFilesVisible ? '收起' : '展开' }}({{ form.repoFiles.length }} 个文件)
              </el-button>
              <el-tooltip placement="top" effect="dark">
                <template #content>选择本地项目根目录,递归导入所有源文件<br/>自动跳过 .git / node_modules / 二进制文件<br/>导入后会替换当前已有的仓库快照</template>
                <el-button size="small" @click="triggerDirImport">导入目录</el-button>
              </el-tooltip>
              <el-tooltip placement="top" effect="dark">
                <template #content>上传项目的 ZIP 压缩包,解压后导入其中的源文件<br/>自动跳过 .git / node_modules / 二进制文件<br/>导入后会替换当前已有的仓库快照</template>
                <el-button size="small" @click="triggerZipImport">上传 ZIP</el-button>
              </el-tooltip>
              <span class="hint">自动跳过 .git/node_modules/二进制文件;导入会替换当前快照</span>
            </div>
            <div v-if="repoFilesVisible" class="repo-list">
              <div v-for="(f, idx) in form.repoFiles" :key="idx" class="repo-row">
                <div class="repo-row-head">
                  <el-input v-model="f.path" size="small" placeholder="相对路径,如 app/pricing.py"
                            style="flex: 1" />
                  <el-button size="small" text type="danger" @click="removeRepoFile(idx)">删除</el-button>
                </div>
                <el-input v-model="f.content" type="textarea" :rows="4" placeholder="文件内容"
                          style="margin-top: 6px; font-family: monospace; font-size: 12px" />
              </div>
              <el-tooltip placement="top" effect="dark">
                <template #content>手动添加单个文件<br/>填写相对路径(如 app/pricing.py)和文件内容<br/>适合只有少量文件的简单用例</template>
                <el-button size="small" @click="addRepoFile" style="margin-top: 8px">+ 添加文件</el-button>
              </el-tooltip>
              <div v-if="!form.repoFiles.length" class="hint">未配置任何文件,执行时临时目录为空</div>
            </div>
            <input ref="dirInput" type="file" webkitdirectory directory multiple
                   style="display: none" @change="onDirImport" />
            <input ref="zipInput" type="file" accept=".zip"
                   style="display: none" @change="onZipImport" />
          </div>
        </el-form-item>

        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>本用例期望的执行环境<br/>仅 Docker:必须由 docker 隔离的 Agent 执行,且必须填镜像<br/>仅 Host:必须由 host 隔离的 Agent 执行,不需要镜像<br/>任意:两种模式都可执行,但必须填镜像(docker 下会用到)<br/>创建评测任务时会校验所选 Agent 与用例声明是否兼容</template>
              <span>执行方式 <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-radio-group v-model="form.target_isolation">
            <el-radio-button value="docker">仅 Docker</el-radio-button>
            <el-radio-button value="host">仅 Host</el-radio-button>
            <el-radio-button value="any">任意</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item :required="form.target_isolation !== 'host'">
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>本用例执行命令(环境准备 + 自动化验收)所用的容器镜像,镜像只来自用例本身<br/>执行方式为「仅 Docker」或「任意」时必填,未填写将无法保存<br/>Node 用例填 node:20-slim,Java 填 maven:3.9-eclipse-temurin-17,Python 填 python:3.12-slim<br/>执行方式为「仅 Host」时该字段不生效</template>
              <span>{{ form.target_isolation === 'host' ? '镜像(仅 Host 时忽略)' : '镜像(必填)' }} <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.docker_image" placeholder="如 node:20-slim、maven:3.9-eclipse-temurin-17" />
        </el-form-item>

        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>需要持久化的依赖缓存目录,每行一条「宿主机路径:容器路径」<br/>例如 /Users/you/.capeval-cache/m2:/root/.m2<br/>可避免每次评测重复下载依赖;留空则不额外挂载</template>
              <span>附加卷(可选) <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.docker_volumes" type="textarea" :rows="2"
                    placeholder="/Users/you/.capeval-cache/m2:/root/.m2" />
        </el-form-item>

        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>还原仓库后、启动 Agent 前执行的依赖安装/环境准备命令<br/>在临时目录内逐行执行,退出码 0 即通过(超时 600 秒)<br/>例如:pip install -r requirements.txt、npm install、mvn install -DskipTests<br/>失败则该用例判失败,不进入 Agent 执行阶段</template>
              <span>环境准备命令(可选) <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <div style="display:flex; gap:8px; margin-bottom:6px;">
            <el-button size="small" :loading="buildDetectLoading" @click="runDetectBuild">
              检测构建系统并填充
            </el-button>
            <span class="hint">自动识别 pom.xml / go.mod / package.json / requirements.txt 等</span>
          </div>
          <el-input v-model="form.install_commands" type="textarea" :rows="3"
                    placeholder="pip install -r requirements.txt&#10;npm install&#10;mvn install -DskipTests" />
        </el-form-item>

        <el-form-item>
          <template #label>
            <el-tooltip placement="top" effect="dark">
              <template #content>标准解答的 unified diff(可选)。<br/>用于与 Agent 实际产出做对比,辅助判定代码正确性。<br/>可手动粘贴,或从 git diff 输出复制。留空则不参与对比。</template>
              <span>Gold Patch(可选) <el-icon class="qmark"><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input v-model="form.gold_patch" type="textarea" :rows="5"
                    placeholder="--- a/src/calc.py&#10;+++ b/src/calc.py&#10;@@ -1,3 +1,3 @@&#10; def div(a, b):&#10;-    return a / b&#10;+    return a / b if b != 0 else 0"
                    style="font-family: monospace; font-size: 12px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.title || !form.tags.length" @click="submit">
          {{ editing ? '保存' : '创建评测用例' }}
        </el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="detailVisible" :title="detailRow ? `评测用例详情 #${detailRow.id}:${detailRow.title}` : ''" size="620px">
      <template v-if="detailRow">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="难度">
            <el-tag size="small" :type="difficultyMeta[detailRow.difficulty]?.type">
              {{ difficultyMeta[detailRow.difficulty]?.label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="预估时长">{{ detailRow.est_minutes }} 分钟</el-descriptions-item>
          <el-descriptions-item label="能力标签" :span="2">
            <el-tag v-for="t in detailRow.tags" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="效率基线" :span="2">
            <span v-if="detailRow.efficiency_baseline?.input_tokens || detailRow.efficiency_baseline?.wall_seconds">
              {{ detailRow.efficiency_baseline?.input_tokens ?? '—' }} tokens /
              {{ detailRow.efficiency_baseline?.wall_seconds ?? '—' }} 秒
            </span>
            <span v-else>未设置</span>
          </el-descriptions-item>
          <el-descriptions-item label="执行方式">
            {{ ({ host: '仅 Host', docker: '仅 Docker', any: '任意' } as Record<string, string>)[String(detailEnv.target_isolation ?? 'any')] ?? '任意' }}
          </el-descriptions-item>
          <el-descriptions-item label="镜像与附加卷" :span="2">
            <template v-if="detailEnv.docker_image || (detailEnv.docker_volumes ?? []).length">
              <div v-if="detailEnv.docker_image">镜像:{{ detailEnv.docker_image }}</div>
              <div v-for="(v, i) in (detailEnv.docker_volumes ?? [])" :key="i">附加卷:{{ v }}</div>
            </template>
            <span v-else>未配置{{ detailEnv.target_isolation === 'host' ? '(仅 Host 执行,不容器化)' : '(docker 隔离的 Agent 下无法执行)' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-h">任务脚本({{ detailRow.task_script?.length ?? 0 }} 轮)</h4>
        <div v-for="t in detailRow.task_script" :key="t.turn" class="turn-view">
          <div class="turn-view-head">
            第 {{ t.turn }} 轮
            <el-tag v-if="t.intent_tag" size="small" effect="plain" style="margin-left: 6px">{{ t.intent_tag }}</el-tag>
          </div>
          <div class="turn-view-msg">{{ t.message }}</div>
        </div>

        <h4 class="detail-h">环境准备命令</h4>
        <div v-if="((detailRow.env_def as Record<string, any>)?.install_commands ?? []).length">
          <div v-for="(c, i) in ((detailRow.env_def as Record<string, any>).install_commands)" :key="i" class="cmd-line">
            <code>{{ c.cmd }}</code><span class="hint">(期望退出码 {{ c.expect_exit_code ?? 0 }})</span>
          </div>
        </div>
        <div v-else class="hint">无(使用空环境)</div>

        <h4 class="detail-h">自动化验收命令</h4>
        <div v-if="detailRow.acceptance?.auto_commands?.length">
          <div v-for="(c, i) in detailRow.acceptance.auto_commands" :key="i" class="cmd-line">
            <code>{{ c.cmd }}</code><span class="hint">(期望退出码 {{ c.expect_exit_code ?? 0 }})</span>
          </div>
        </div>
        <div v-else class="hint">无</div>

        <h4 class="detail-h">语义验收点</h4>
        <ul v-if="detailRow.acceptance?.semantic_points?.length" class="point-list">
          <li v-for="(p, i) in detailRow.acceptance.semantic_points" :key="i">{{ p }}</li>
        </ul>
        <div v-else class="hint">无</div>

        <h4 class="detail-h">流程规范期望</h4>
        <ul v-if="detailRow.acceptance?.process_expectations?.length" class="point-list">
          <li v-for="(p, i) in detailRow.acceptance.process_expectations" :key="i">{{ p }}</li>
        </ul>
        <div v-else class="hint">无</div>

        <h4 class="detail-h">仓库快照({{ Object.keys((detailRow.repo_snapshot as Record<string, unknown>)?.files ?? {}).length }} 个文件)</h4>
        <div v-for="(content, path) in ((detailRow.repo_snapshot as Record<string, unknown>)?.files ?? {})" :key="path" class="repo-view">
          <div class="repo-view-path"><code>{{ path }}</code></div>
          <pre class="repo-view-content">{{ content as string }}</pre>
        </div>
        <div v-if="!Object.keys((detailRow.repo_snapshot as Record<string, unknown>)?.files ?? {}).length" class="hint">无</div>

        <h4 v-if="detailRow.gold_patch" class="detail-h">Gold Patch</h4>
        <pre v-if="detailRow.gold_patch" class="repo-view-content" style="white-space: pre-wrap">{{ detailRow.gold_patch }}</pre>
      </template>
    </el-drawer>

    <!-- 自检结果弹窗 -->
    <el-dialog v-model="selfCheckVisible" :title="`用例自检 #${selfCheckTargetId}`" width="720px">
      <div v-loading="selfCheckLoading">
        <div v-if="selfCheckResult">
          <el-alert
            :type="selfCheckResult.passed ? 'success' : 'error'"
            :title="selfCheckResult.passed ? '自检通过:环境准备 + 自动化验收全部成功' : `自检失败(${selfCheckResult.stage}): ${selfCheckResult.error}`"
            :closable="false" show-icon style="margin-bottom: 12px" />
          <div class="hint" style="margin-bottom: 12px">还原文件数:{{ selfCheckResult.file_count }}</div>

          <h4 class="detail-h">环境准备命令</h4>
          <div v-if="selfCheckResult.install_logs.length">
            <div v-for="(log, i) in selfCheckResult.install_logs" :key="'i'+i" class="selfcheck-log">
              <div class="selfcheck-cmd">
                <code>{{ log.command }}</code>
                <el-tag size="small" :type="log.exit_code === log.expect_exit_code ? 'success' : 'danger'">
                  退出码 {{ log.exit_code }}
                </el-tag>
              </div>
              <pre v-if="log.stderr" class="selfcheck-stderr">{{ log.stderr }}</pre>
            </div>
          </div>
          <div v-else class="hint">无环境准备命令</div>

          <h4 class="detail-h">自动化验收命令</h4>
          <div v-if="selfCheckResult.acceptance_logs.length">
            <div v-for="(log, i) in selfCheckResult.acceptance_logs" :key="'a'+i" class="selfcheck-log">
              <div class="selfcheck-cmd">
                <code>{{ log.command }}</code>
                <el-tag size="small" :type="log.exit_code === log.expect_exit_code ? 'success' : 'danger'">
                  退出码 {{ log.exit_code }}
                </el-tag>
              </div>
              <pre v-if="log.stderr" class="selfcheck-stderr">{{ log.stderr }}</pre>
            </div>
          </div>
          <div v-else class="hint">无自动化验收命令</div>
        </div>
        <div v-else class="hint">正在执行自检...</div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.turn-row { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 10px; margin-bottom: 10px; width: 100%; }
.turn-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
.inline-fields { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-h { margin: 18px 0 8px; font-size: 14px; }
.turn-view { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 10px; margin-bottom: 8px; }
.turn-view-head { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.turn-view-msg { white-space: pre-wrap; font-size: 13px; }
.cmd-line { margin-bottom: 4px; font-size: 13px; }
.point-list { margin: 0; padding-left: 18px; font-size: 13px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.repo-block { width: 100%; }
.selfcheck-log { border: 1px solid var(--el-border-color); border-radius: 4px; padding: 8px; margin-bottom: 8px; }
.selfcheck-cmd { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.selfcheck-stderr { margin: 6px 0 0; padding: 6px; background: #fff7f7; color: #c45656; font-size: 12px; white-space: pre-wrap; max-height: 200px; overflow: auto; border-radius: 3px; }
.repo-head { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.repo-list { width: 100%; }
.repo-row { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 10px; margin-bottom: 10px; width: 100%; box-sizing: border-box; }
.repo-row-head { display: flex; align-items: center; gap: 8px; }
.repo-view { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 10px; margin-bottom: 8px; }
.repo-view-path { font-size: 13px; margin-bottom: 6px; word-break: break-all; }
.repo-view-content { margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px; font-family: monospace; background: var(--el-fill-color-light); padding: 8px; border-radius: 4px; }
.help-card { margin-bottom: 14px; border: 1px solid var(--el-border-color-light); }
.help-head { display: flex; justify-content: space-between; align-items: center; }
.help-title { font-size: 14px; font-weight: 600; }
.help-body { font-size: 13px; color: var(--el-text-color-regular); line-height: 1.7; }
.help-body p { margin: 0 0 8px; }
.help-steps, .help-fields { margin: 6px 0; padding-left: 22px; }
.help-steps li, .help-fields li { margin-bottom: 6px; }
.help-steps { padding-left: 22px; margin: 6px 0; }
.help-steps li { margin: 4px 0; line-height: 1.7; }
.help-body code { background: var(--el-fill-color-light); padding: 1px 6px; border-radius: 3px; font-size: 12px; }
.qmark { font-size: 14px; color: var(--el-text-color-placeholder); vertical-align: -2px; cursor: help; margin-left: 4px; }
</style>
