<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const props = defineProps({ api: { type: Object, required: true }, pluginId: { type: [String, Number], required: true }, sourcePluginId: { type: [String, Number], default: '' } })
const options = ref(null)
const sourcePath = ref('')
const targetPath = ref('')
const mediaSource = ref('')
const preview = ref(null)
const confirmed = ref(false)
const busy = ref(false)
const previewBusy = ref(false)
const outcome = ref('')
const error = ref('')
const browser = ref(null)
const browseKind = ref('source')
const browsePath = ref('')
const parentPath = ref('')
const browseItems = ref([])
const now = ref(Date.now())
const pendingPreviewId = ref('')
const automaticStatus = ref(null)
const automaticStatusBusy = ref(false)
const automaticCheckBusy = ref(false)
const automaticCheckMessage = ref('')
let requestGeneration = 0
let clockTimer
const previewExpiryMs = computed(() => {
  const value = preview.value?.expires_at
  if (typeof value === 'number' || (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value))) return Number(value) * 1000
  return Date.parse(value)
})
const displayItems = computed(() => {
  const recognizedItems = preview.value?.items || []
  const executionItems = preview.value?.execution?.items || []
  if (!recognizedItems.length) return executionItems
  return recognizedItems.map(item => ({
    ...item,
    transferState: executionItems.find(result => result.source === item.source && result.target === item.target)?.state,
    completionScope: executionItems.find(result => result.source === item.source && result.target === item.target)?.completion_scope,
  }))
})
const previewExpired = computed(() => !preview.value || !Number.isFinite(previewExpiryMs.value) || previewExpiryMs.value <= now.value)
const executionLocked = computed(() => outcome.value === 'pendingaccepted' || outcome.value === 'unknown')
const formLocked = computed(() => busy.value || executionLocked.value)
const canExecute = computed(() => !!preview.value && !previewExpired.value && preview.value.summary?.failed === 0 && preview.value.summary?.success > 0 && confirmed.value && !busy.value && !previewBusy.value && outcome.value === '')
const endpoint = (name) => `plugin/${props.pluginId}/manual/${name}`
const automaticEndpoint = (name) => `plugin/${props.pluginId}/automatic/${name}`
const storageKey = () => `xiaojiejiescraping:manual-pending:${props.pluginId}`
const automaticModeLabel = computed(() => {
  if (!automaticStatus.value) return '状态未获取'
  if (!automaticStatus.value.enabled) return '自动触发已关闭'
  if (automaticStatus.value.mode === 'discover') return '只读发现'
  if (automaticStatus.value.mode === 'preview') return '自动预览'
  if (automaticStatus.value.mode === 'execute') return '自动整理并刮削'
  return '自动模式未知'
})
const automaticCounts = computed(() => {
  const counts = automaticStatus.value?.counts
  return counts && typeof counts === 'object' ? Object.entries(counts).filter(([, value]) => typeof value === 'number') : []
})

function invalidatePreview() {
  if (formLocked.value) return
  requestGeneration += 1
  preview.value = null
  confirmed.value = false
  outcome.value = ''
  clearPendingPreview()
  error.value = ''
}
function setPath(kind, path) {
  if (formLocked.value) return
  if (kind === 'source') sourcePath.value = path
  else targetPath.value = path
  invalidatePreview()
}
function setMediaSource(value) {
  if (formLocked.value) return
  mediaSource.value = value
  invalidatePreview()
}
function executionOutcome(state) {
  if (state === 'completed') return 'completed'
  if (state === 'failed') return 'failed'
  if (state === 'accepted' || state === 'submitting') return 'pendingaccepted'
  return 'unknown'
}
function itemStatus(item) {
  const state = item.transferState || item.state
  if (state === 'accepted' || state === 'submitting') return '已受理'
  if (state === 'completed') return item.completionScope === 'file_transfer' || item.completion_scope === 'file_transfer' ? '硬链接已核验' : '整理已完成'
  if (state === 'failed') return item.message || '失败'
  if (state === 'unknown' || state === 'uncertain') return '结果未知'
  if (item.success === true) return '可整理'
  if (item.success === false) return item.message || '失败'
  return '状态未知'
}
function persistPendingPreview(id) {
  pendingPreviewId.value = id || ''
  if (!id) return
  try { sessionStorage.setItem(storageKey(), id) } catch { /* session persistence is best-effort */ }
}
function clearPendingPreview() {
  pendingPreviewId.value = ''
  try { sessionStorage.removeItem(storageKey()) } catch { /* session storage may be unavailable */ }
}
function applyOutcome(state, id) {
  outcome.value = executionOutcome(state)
  if (outcome.value === 'pendingaccepted' || outcome.value === 'unknown') persistPendingPreview(id)
  else clearPendingPreview()
}
async function loadOptions() {
  if (formLocked.value) return
  error.value = ''
  try {
    options.value = await props.api.get(endpoint('options'))
    if (!targetPath.value) targetPath.value = options.value.default_target_path || ''
    if (!mediaSource.value) mediaSource.value = options.value.sources?.[0]?.value || ''
  } catch {
    error.value = '无法加载可用来源和目录。'
  }
}
async function openBrowser(kind) {
  if (formLocked.value) return
  browseKind.value = kind
  browsePath.value = kind === 'source' ? (sourcePath.value || options.value?.roots?.[0] || '') : (targetPath.value || options.value?.default_target_path || '')
  browser.value = true
  await loadBrowse()
}
async function loadBrowse(path = browsePath.value) {
  if (formLocked.value) return
  error.value = ''
  try {
    const result = await props.api.get(endpoint('browse'), { params: { path, kind: browseKind.value } })
    browsePath.value = result.path || path
    parentPath.value = result.parent || ''
    browseItems.value = result.items || []
  } catch {
    error.value = '无法读取所选目录。'
  }
}
function chooseItem(item) {
  if (formLocked.value) return
  if (item.type === 'dir' || item.type === 'directory') { void loadBrowse(item.path); return }
  setPath(browseKind.value, item.path)
  browser.value = false
}
function chooseCurrentDirectory() {
  if (formLocked.value) return
  setPath(browseKind.value, browsePath.value)
  browser.value = false
}
async function createPreview() {
  if (!options.value?.enabled || !sourcePath.value || !targetPath.value || !mediaSource.value || formLocked.value || previewBusy.value) return
  invalidatePreview()
  const generation = ++requestGeneration
  const snapshot = { path: sourcePath.value, target_path: targetPath.value, media_source: mediaSource.value }
  previewBusy.value = true
  error.value = ''
  try {
    const result = await props.api.post(endpoint('preview'), { ...snapshot, transfer_type: 'link', library_category_folder: false })
    if (requestGeneration === generation && snapshot.path === sourcePath.value && snapshot.target_path === targetPath.value && snapshot.media_source === mediaSource.value && !formLocked.value) {
      preview.value = result
      confirmed.value = false
    }
  } catch {
    if (requestGeneration === generation) error.value = '预览失败，请检查所选路径和来源。'
  } finally { previewBusy.value = false }
}
async function execute() {
  if (!canExecute.value) return
  const id = preview.value.preview_id
  busy.value = true
  applyOutcome('submitting', id)
  error.value = ''
  try {
    const result = await props.api.post(endpoint('execute'), { preview_id: id })
    applyOutcome(result.state, id)
    preview.value = { ...preview.value, execution: result }
  } catch {
    applyOutcome('unknown', id)
    error.value = '执行请求结果未知。请先刷新状态，勿重复执行。'
  } finally { busy.value = false }
}
async function refreshStatus() {
  const id = preview.value?.preview_id || pendingPreviewId.value
  if (!id || busy.value) return
  busy.value = true
  try {
    const result = await props.api.get(endpoint('status'), { params: { preview_id: id } })
    preview.value = { ...(preview.value || { preview_id: id, expires_at: 0 }), summary: result.summary || preview.value?.summary || { total: 0, success: 0, failed: 0 }, execution: result }
    applyOutcome(result.state, id)
    error.value = ''
  } catch { applyOutcome('unknown', id); error.value = '状态查询失败；请稍后只读重试。' }
  finally { busy.value = false }
}
async function restorePendingPreview() {
  try { pendingPreviewId.value = sessionStorage.getItem(storageKey()) || '' } catch { pendingPreviewId.value = '' }
  if (!pendingPreviewId.value) return
  preview.value = { preview_id: pendingPreviewId.value, expires_at: 0, items: [], summary: { total: 0, success: 0, failed: 0 } }
  outcome.value = 'unknown'
  await refreshStatus()
}
function formatAutomaticTime(value) {
  if (value === null || value === undefined || value === '') return '尚未记录'
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : '时间未知'
}
async function loadAutomaticStatus() {
  automaticStatusBusy.value = true
  try { automaticStatus.value = await props.api.get(automaticEndpoint('status')) }
  catch { automaticStatus.value = null }
  finally { automaticStatusBusy.value = false }
}
async function checkAutomaticConditions() {
  automaticCheckBusy.value = true
  automaticCheckMessage.value = ''
  try {
    const result = await props.api.post(automaticEndpoint('check'))
    if (result?.ready === true) automaticCheckMessage.value = '触发条件当前就绪。'
    else if (result?.ready === false) automaticCheckMessage.value = '触发条件当前未就绪。'
    else automaticCheckMessage.value = '检查已完成；条件结果未返回。'
  } catch { automaticCheckMessage.value = '触发条件检查暂不可用。' }
  finally { automaticCheckBusy.value = false }
}
onMounted(async () => {
  clockTimer = setInterval(() => { now.value = Date.now() }, 500)
  await loadOptions()
  await restorePendingPreview()
  await loadAutomaticStatus()
})
onUnmounted(() => { clearInterval(clockTimer); requestGeneration += 1 })
</script>

<template>
  <main class="manual-page">
    <header><div><p class="eyebrow">MANUAL LIBRARY TRANSFER</p><h1>手动识别与整理</h1><p>选择已有目录，预览识别结果后再确认整理。源文件会保留。</p></div><button data-testid="reload-options" :disabled="formLocked" @click="loadOptions">重新加载</button></header>
    <section class="panel auto-panel" data-testid="automatic-status">
      <div class="section-title"><div><h2>自动触发</h2><p>按已配置的 qBittorrent 下载器和标签检查下载完成任务。</p></div><button data-testid="automatic-refresh" :disabled="automaticStatusBusy" @click="loadAutomaticStatus">只读刷新状态</button></div>
      <p v-if="!automaticStatus" class="muted">自动触发状态未获取；手动识别与预览仍可使用。</p>
      <template v-else>
        <p><strong>{{ automaticModeLabel }}</strong><span> · {{ automaticStatus.armed ? '已启动' : '未启动' }}</span><span v-if="automaticStatus.started_at"> · 启动于 {{ formatAutomaticTime(automaticStatus.started_at) }}</span></p>
        <p>{{ automaticStatus.new_only ? '仅处理启用后完成的下载；历史完成任务跳过。' : '会包含启用前已完成的旧任务。' }}</p>
        <dl class="automatic-details"><dt>下载器 / 标签</dt><dd>{{ automaticStatus.downloader || '未设置' }} / {{ automaticStatus.torrent_tag || '未设置' }}</dd><dt>轮询</dt><dd>每 {{ automaticStatus.interval_seconds || 300 }} 秒；上次检查 {{ formatAutomaticTime(automaticStatus.last_poll_at) }}</dd><dt>根目录</dt><dd>{{ automaticStatus.roots?.join('、') || '未设置' }}</dd><dt>目标目录</dt><dd>{{ automaticStatus.target_path || '未设置' }}</dd></dl>
        <p v-if="automaticCounts.length" class="count-list">计数：<span v-for="([key, value]) in automaticCounts" :key="key">{{ key }} {{ value }} · </span></p>
        <p v-if="automaticStatus.error" class="error">{{ automaticStatus.error }}</p>
      </template>
      <div class="automatic-actions"><button data-testid="automatic-check" :disabled="automaticCheckBusy" @click="checkAutomaticConditions">{{ automaticCheckBusy ? '检查中…' : '检查触发条件（只读）' }}</button><span v-if="automaticCheckMessage" class="muted">{{ automaticCheckMessage }}</span></div>
    </section>
    <p v-if="!options?.enabled && options" class="notice">此功能当前不可用。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <section class="panel fields">
      <label>来源目录或文件<div class="path-row"><input data-testid="source-path" :value="sourcePath" :disabled="formLocked" placeholder="/downloads/..." @input="setPath('source', $event.target.value)"><button data-testid="browse-source" :disabled="formLocked" @click="openBrowser('source')">浏览</button></div></label>
      <label>目标目录<div class="path-row"><input data-testid="target-path" :value="targetPath" :disabled="formLocked" placeholder="/library/..." @input="setPath('target', $event.target.value)"><button data-testid="browse-target" :disabled="formLocked" @click="openBrowser('target')">浏览</button></div></label>
      <label>识别来源<select data-testid="media-source" :value="mediaSource" :disabled="formLocked" @change="setMediaSource($event.target.value)"><option value="" disabled>请选择</option><option v-for="source in options?.sources || []" :key="source.value" :value="source.value">{{ source.title }}</option></select></label>
      <div class="actions"><span class="muted">整理方式：硬链接（保留来源文件）</span><button data-testid="preview" class="primary" :disabled="previewBusy || formLocked || !options?.enabled || !sourcePath || !targetPath || !mediaSource" @click="createPreview">{{ previewBusy ? '处理中…' : '识别并预览' }}</button></div>
    </section>
    <section v-if="preview" class="panel results">
      <div class="section-title"><div><h2>预览结果</h2><p>{{ preview.summary?.success || 0 }} 项可整理，{{ preview.summary?.failed || 0 }} 项失败</p></div><span v-if="!previewExpired" class="valid">预览有效至 {{ new Date(previewExpiryMs).toLocaleString() }}</span><span v-else class="error">预览已过期</span></div>
      <div class="table-wrap"><table><thead><tr><th>项目</th><th>来源路径</th><th>完整来源 ID</th><th>目标路径</th><th>结果</th></tr></thead><tbody><tr v-for="(item, index) in displayItems" :key="`${item.source}-${item.target}-${index}`"><td>{{ item.title || '—' }}</td><td>{{ item.source }}</td><td>{{ item.media_id || '—' }}</td><td>{{ item.target }}</td><td>{{ itemStatus(item) }}</td></tr></tbody></table></div>
      <label class="confirm"><input data-testid="confirm-transfer" v-model="confirmed" type="checkbox" :disabled="!preview || previewExpired || formLocked">我已核对预览，并确认按上述硬链接路径整理。</label>
      <div class="actions"><span v-if="outcome === 'pendingaccepted'" class="notice">请求已受理，正在等待结果。</span><span v-else-if="outcome === 'completed'" class="valid">硬链接整理已核实；刮削结果请查看 MoviePilot 整理历史</span><span v-else-if="outcome === 'failed'" class="error">整理失败，原任务已结束。</span><span v-else-if="outcome === 'unknown'" class="error">结果未知，不能重复提交。</span><button data-testid="execute" class="primary" :disabled="!canExecute" @click="execute">确认并整理</button><button v-if="outcome" data-testid="refresh-status" :disabled="busy" @click="refreshStatus">只读刷新状态</button></div>
      <p v-if="preview.execution" class="muted">任务 {{ preview.execution.state }}：{{ preview.execution.summary?.success || 0 }} 项成功，{{ preview.execution.summary?.failed || 0 }} 项失败</p>
    </section>
    <div v-if="browser" class="overlay" role="dialog" aria-modal="true" aria-label="浏览目录"><section class="browser"><header><h2>选择{{ browseKind === 'source' ? '来源' : '目标' }}路径</h2><button @click="browser = false">关闭</button></header><div v-if="browseKind === 'source' && options?.roots?.length" class="root-list"><span>来源根目录</span><button v-for="root in options.roots" :key="root" :disabled="formLocked" @click="loadBrowse(root)">{{ root }}</button></div><p class="current-path">{{ browsePath }}</p><button :disabled="formLocked || !parentPath" @click="loadBrowse(parentPath)">返回上级目录</button><button :disabled="formLocked || !browsePath" @click="loadBrowse()">打开当前目录</button><ul><li v-for="item in browseItems" :key="item.path"><button data-testid="browse-item" :disabled="formLocked" @click="chooseItem(item)">{{ item.type === 'dir' || item.type === 'directory' ? '📁' : '文件' }} {{ item.name }}</button></li></ul><button class="primary" :disabled="formLocked || !browsePath" @click="chooseCurrentDirectory">选择当前目录</button></section></div>
  </main>
</template>

<style scoped>
.manual-page{box-sizing:border-box;min-height:100vh;max-width:1120px;margin:0 auto;padding:28px;color:#17232d;background:#f5f8f7;font:15px/1.5 system-ui,sans-serif}.manual-page *{box-sizing:border-box}header,.section-title,.actions,.path-row{display:flex;align-items:center;justify-content:space-between;gap:12px}header{margin-bottom:22px}h1,h2{color:#17232d}h1{margin:0;font-size:28px}h2{margin:0;font-size:19px}.eyebrow{color:#638078;font-size:11px;letter-spacing:.14em}.panel{padding:22px;border:1px solid #dce5e3;border-radius:14px;background:#fff;margin:16px 0;box-shadow:0 5px 20px #1231}.auto-panel p{margin:8px 0}.automatic-details{display:grid;grid-template-columns:max-content 1fr;gap:5px 14px;margin:14px 0}.automatic-details dt{font-weight:600}.automatic-details dd{margin:0;overflow-wrap:anywhere}.automatic-actions{display:flex;align-items:center;gap:12px}.count-list{color:#667874}.fields{display:grid;gap:18px}.fields label{display:grid;gap:7px;font-weight:600}.path-row input,select{width:100%;min-width:0;padding:10px;border:1px solid #bdcbc8;border-radius:7px;font:inherit}.path-row input{flex:1}button{border:1px solid #b8c8c4;background:#fff;border-radius:7px;padding:9px 13px;color:inherit;font:inherit;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}.primary{background:#176b5f;border-color:#176b5f;color:#fff}.muted{color:#667874}.notice{color:#805a12}.error{color:#a33434}.valid{color:#15704e}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;margin:16px 0}th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e4ecea;overflow-wrap:anywhere}.confirm{display:flex;gap:9px;align-items:flex-start;margin:18px 0}.overlay{position:fixed;inset:0;background:#10201c77;display:grid;place-items:center;padding:18px}.browser{width:min(680px,100%);max-height:80vh;overflow:auto;background:#fff;border-radius:14px;padding:20px}.browser header{margin:0}.current-path{overflow-wrap:anywhere;color:#667874}.root-list{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}li{margin:5px 0}
</style>
