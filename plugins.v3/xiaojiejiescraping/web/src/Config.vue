<script setup>
import { computed, reactive, watch } from 'vue'

const props = defineProps({ initialConfig: { type: Object, default: () => ({}) }, api: { type: Object, default: null }, pluginId: { type: [String, Number], default: '' }, sourcePluginId: { type: [String, Number], default: '' } })
const emit = defineEmits(['save', 'close'])
const defaults = { enabled: false, sources: ['manko'], storage: 'local', recognize_dirs: '', image_hosts: '', discovery_enabled: false, downloader: '', torrent_tag: '', actor_images: false, handoff_mode: 'discover', target_path: '', transfer_type: '', ownership_ack: false, auxiliary_ack: false, handoff_limit: 1 }
const model = reactive({ ...defaults, ...(props.initialConfig || {}) })
watch(() => props.initialConfig, (value) => Object.assign(model, defaults, value || {}), { deep: true })
const sourceText = computed({ get: () => Array.isArray(model.sources) ? model.sources.join(', ') : '', set: value => { model.sources = value.split(',').map(item => item.trim()).filter(Boolean) } })
const boolFields = [['enabled','启用已完成的来源能力'],['discovery_enabled','定期检查已完成任务'],['actor_images','启用演员缩略图'],['ownership_ack','已排除 MoviePilot 原生自动监控'],['auxiliary_ack','已理解宿主可能补充辅助字段']]
</script>

<template>
  <main class="config-page"><header><div><p class="eyebrow">XIAOJIEJIE SCRAPING</p><h1>插件配置</h1></div><button @click="emit('close')">关闭</button></header>
    <p class="warning">实验版：自动模式默认只读发现；来源与真实整理仍需在预览中核对。保存会保留所有现有及未知配置项。</p>
    <section class="settings">
      <label v-for="[key, title] in boolFields" :key="key" class="check"><input v-model="model[key]" :data-testid="key === 'enabled' ? 'config-enabled' : `config-${key}`" type="checkbox">{{ title }}</label>
      <label>元数据来源（逗号分隔）<input v-model="sourceText" data-testid="config-sources"></label>
      <label>存储<input v-model="model.storage" data-testid="config-storage"></label>
      <label>触发目录（每行一个绝对路径）<textarea v-model="model.recognize_dirs" data-testid="config-recognize-dirs" /></label>
      <label>qBittorrent 下载器<input v-model="model.downloader" data-testid="config-downloader"></label>
      <label>专用标签<input v-model="model.torrent_tag" data-testid="config-torrent-tag"></label>
      <label>自动流程模式<select v-model="model.handoff_mode" data-testid="config-handoff-mode"><option value="discover">只读发现</option><option value="preview">预览</option><option value="execute">执行</option></select></label>
      <label>明确目标目录<input v-model="model.target_path" data-testid="config-target-path"></label>
      <label>整理方式<select v-model="model.transfer_type" data-testid="config-transfer-type"><option value="">未设置</option><option value="copy">复制</option><option value="link">硬链接</option></select></label>
      <label>每轮最多处理的新文件<input v-model.number="model.handoff_limit" type="number" min="1" max="5" data-testid="config-handoff-limit"></label>
      <label>允许的图片 HTTPS 主机（每行一个）<textarea v-model="model.image_hosts" data-testid="config-image-hosts" /></label>
    </section>
    <footer><button data-testid="config-save" class="save" @click="emit('save', { ...model, sources: [...model.sources] })">保存配置</button></footer>
  </main>
</template>

<style scoped>
.config-page{min-height:100vh;max-width:760px;margin:auto;padding:24px;color:#17232d;background:#f5f8f7;font:15px/1.5 system-ui,sans-serif}.config-page *{box-sizing:border-box}header,footer{display:flex;align-items:center;justify-content:space-between}h1{color:#17232d;margin:0}.eyebrow{font-size:11px;letter-spacing:.14em;color:#638078}.warning{padding:12px;background:#fff6df;border-radius:8px;color:#735314}.settings{display:grid;grid-template-columns:1fr 1fr;gap:16px}.settings label{display:grid;gap:6px}.settings input:not([type=checkbox]),.settings textarea,.settings select{width:100%;padding:9px;border:1px solid #bdcbc8;border-radius:6px;font:inherit}.settings textarea{min-height:72px}.settings .check{display:flex;align-items:center;gap:8px}.save{margin:20px 0 0;padding:10px 18px;border:0;border-radius:7px;background:#176b5f;color:#fff;font:inherit;cursor:pointer}@media(max-width:600px){.settings{grid-template-columns:1fr}}
</style>
