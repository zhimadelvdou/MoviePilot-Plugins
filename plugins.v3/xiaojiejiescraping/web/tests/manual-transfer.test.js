import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as vuetifyComponents from 'vuetify/components'
import Page from '../src/Page.vue'
import Config from '../src/Config.vue'

const goodPreview = { preview_id: 'p1', expires_at: 4070908800, items: [{ source: '/src/a.mp4', target: '/dst/a.mp4', title: '影片', media_source: 'manko', media_id: 'ID-123', success: true, message: 'ok' }], summary: { total: 1, success: 1, failed: 0 } }
const options = { sources: [{ title: 'Manko', value: 'manko' }], roots: ['/downloads'], default_target_path: '/library', transfer_type: 'link', enabled: true }
const automaticStatus = { enabled: false, mode: 'discover', match_mode: 'tag_and_path', new_only: true, armed: false, started_at: null, interval_seconds: 300, downloader: 'qb', torrent_tag: 'Manko', roots: ['/downloads'], target_path: '/library', last_poll_at: null, counts: {}, error: '' }
const initialPluginConfig = { enabled: true, discovery_enabled: false, auto_new_only: true, automatic_match_mode: 'tag_and_path', downloader: 'qb', torrent_tag: 'Manko', recognize_dirs: '/downloads', target_path: '/library', handoff_mode: 'execute', ownership_ack: true, auxiliary_ack: true, future_setting: { retained: true } }
const mounted = []
const vuetifyOptions = () => ({ attachTo: document.body, global: { plugins: [createVuetify({ components: vuetifyComponents, theme: { defaultTheme: 'dark' }, defaults: { VDialog: { transition: false }, VSelect: { transition: false } } })] } })
const inputFor = (wrapper, id) => wrapper.get(`[data-testid="${id}"]`).get('input')
const textareaFor = (wrapper, id) => wrapper.get(`[data-testid="${id}"]`).get('textarea')
const selectComponent = (wrapper, id) => wrapper.findAllComponents(vuetifyComponents.VSelect).find(component => component.attributes('data-testid') === id)
const switchComponent = (wrapper, id) => wrapper.findAllComponents(vuetifyComponents.VSwitch).find(component => component.attributes('data-testid') === id)
const selectValue = async (wrapper, id, value) => { await selectComponent(wrapper, id).vm.$emit('update:modelValue', value); await flushPromises() }
const selectValueFromMenu = async (wrapper, id, title) => {
  await wrapper.get(`[data-testid="${id}"]`).get('input').trigger('click')
  await wrapper.get(`[data-testid="${id}"]`).get('input').trigger('keydown', { key: 'ArrowDown' })
  await flushPromises()
  const option = [...document.body.querySelectorAll('.v-list-item')].find(item => item.textContent.includes(title))
  expect(option).toBeTruthy()
  option.click()
  await flushPromises()
}
const setSwitch = async (wrapper, id, value) => { await switchComponent(wrapper, id).vm.$emit('update:modelValue', value); await flushPromises() }
const setup = ({ automaticEnabled = false, newOnly = true } = {}) => {
  let pluginConfig = { ...initialPluginConfig, auto_new_only: newOnly, discovery_enabled: automaticEnabled }
  const api = {
    get: vi.fn(async (url) => url.endsWith('/options') ? options
      : url.endsWith('/automatic/status') ? { ...automaticStatus, new_only: pluginConfig.auto_new_only, enabled: pluginConfig.discovery_enabled }
        : url === 'plugin/plugin-x' ? { success: true, message: '', data: { ...pluginConfig } }
          : { state: 'completed', items: [], summary: {} }),
    put: vi.fn(async (url, body) => { pluginConfig = { ...body }; return { success: true } }),
    post: vi.fn(async (url) => url.endsWith('/preview') ? goodPreview : ({ state: 'completed', items: [], summary: {} })),
    currentPluginConfig: () => ({ ...pluginConfig }),
  }
  const wrapper = mount(Page, { ...vuetifyOptions(), props: { api, pluginId: 'plugin-x', sourcePluginId: 'source-x' } })
  mounted.push(wrapper)
  return { api, wrapper }
}
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
beforeEach(() => {
  window.matchMedia ??= () => ({ matches: false, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false })
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }
})
afterEach(() => { for (const wrapper of mounted.splice(0)) wrapper.unmount(); document.body.innerHTML = ''; vi.useRealTimers(); sessionStorage.clear() })

describe('manual transfer page', () => {
  it('loads choices only and sends preview with scoped plugin API', async () => {
    const { api, wrapper } = setup()
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x/manual/options')
    expect(api.post).not.toHaveBeenCalled()
    await inputFor(wrapper, 'source-path').setValue('/downloads/TEST-123-C')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({ path: '/downloads/TEST-123-C', target_path: '/library', media_source: 'manko', transfer_type: 'link' }))
    expect(wrapper.text()).toContain('ID-123')
  })

  it('shows automatic triggering as disabled without enabling or executing anything', async () => {
    const { api, wrapper } = setup()
    await flushPromises()
    expect(wrapper.get('main').attributes('data-host-theme')).toBe('dark')
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x/automatic/status')
    expect(wrapper.text()).toContain('自动触发已关闭')
    expect(wrapper.text()).toContain('历史完成任务跳过')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('loads visible automatic rules and only offers qBittorrent clients', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async url => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) return automaticStatus
      if (url === 'download/clients') return { success: true, message: '', data: [
        { name: 'qb-primary', type: 'qbittorrent', config: { password: 'must-not-render' } },
        { name: 'transmission', type: 'transmission' },
      ] }
      if (url === 'plugin/plugin-x') return { success: true, message: '', data: { ...initialPluginConfig } }
      return {}
    })
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('download/clients')
    expect(selectComponent(wrapper, 'automatic-match-mode').props('modelValue')).toBe('tag_and_path')
    const downloaderOptions = selectComponent(wrapper, 'automatic-downloader').props('items').map(item => item.title)
    expect(downloaderOptions.join(' ')).toContain('qb-primary')
    expect(downloaderOptions.join(' ')).not.toContain('transmission')
    expect(wrapper.text()).not.toContain('must-not-render')
  })

  it('uses the Vuetify directory browser to append another automatic source root', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async (url, request) => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) return automaticStatus
      if (url === 'plugin/plugin-x') return { success: true, message: '', data: { ...initialPluginConfig } }
      if (url === 'download/clients') return { success: true, message: '', data: [] }
      if (url.endsWith('/manual/browse')) {
        if (request.params.path === '/downloads') return { path: '/downloads', parent: '/', items: [{ name: 'New source', path: '/downloads/New source', type: 'dir' }] }
        return { path: request.params.path, parent: '/downloads', items: [] }
      }
      return {}
    })
    await flushPromises()
    await wrapper.get('[data-testid="browse-automatic-source"]').trigger('click')
    await flushPromises()
    const item = document.body.querySelector('[data-testid="browse-item"]')
    expect(item).not.toBeNull()
    item.click()
    await flushPromises()
    expect(document.body.querySelector('input[readonly]')?.value).toBe('/downloads/New source')
    document.body.querySelector('[data-testid="choose-browse-directory"]').click()
    await flushPromises()
    expect(textareaFor(wrapper, 'automatic-roots').element.value).toBe('/downloads\n/downloads/New source')
    expect(wrapper.get('main').attributes('data-host-theme')).toBe('dark')
  })

  it('reports backend matching mode and keeps old configs on legacy AND when the field is absent', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async url => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) return { ...automaticStatus, match_mode: 'path' }
      if (url === 'download/clients') return { success: true, message: '', data: [] }
      if (url === 'plugin/plugin-x') return { success: true, message: '', data: { ...initialPluginConfig, automatic_match_mode: undefined } }
      return {}
    })
    await flushPromises()
    expect(wrapper.text()).toContain('匹配方式按路径')
    expect(selectComponent(wrapper, 'automatic-match-mode').props('modelValue')).toBe('tag_and_path')
  })

  it('saves automatic rules without changing the disabled trigger switch or unknown config', async () => {
    const { api, wrapper } = setup()
    await flushPromises()
    await selectValue(wrapper, 'automatic-match-mode', 'path')
    await textareaFor(wrapper, 'automatic-roots').setValue('/downloads/new')
    await wrapper.get('[data-testid="automatic-save-rules"]').trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('plugin/plugin-x', {
      ...initialPluginConfig,
      automatic_match_mode: 'path',
      recognize_dirs: '/downloads/new',
    })
    expect(api.currentPluginConfig().discovery_enabled).toBe(false)
    expect(api.currentPluginConfig().future_setting).toEqual({ retained: true })
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('已关闭')
  })

  it('turns automatic triggering on through the full plugin config and confirms server status', async () => {
    const { api, wrapper } = setup()
    await flushPromises()
    await setSwitch(wrapper, 'automatic-toggle', true)
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x')
    expect(api.put).toHaveBeenCalledWith('plugin/plugin-x', { ...initialPluginConfig, discovery_enabled: true })
    expect(api.currentPluginConfig()).toEqual({ ...initialPluginConfig, discovery_enabled: true })
    expect(switchComponent(wrapper, 'automatic-toggle').props('modelValue')).toBe(true)
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('已开启')
    expect(wrapper.text()).toContain('只处理重新开启后完成的下载')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('describes legacy mode accurately when new-only boundary is disabled', async () => {
    const { wrapper } = setup({ newOnly: false })
    await flushPromises()
    expect(wrapper.text()).toContain('重新开启后也会处理启用前已完成的旧任务')
    await setSwitch(wrapper, 'automatic-toggle', true)
    await flushPromises()
    expect(wrapper.text()).toContain('会包含启用前已完成的旧任务')
    expect(wrapper.text()).not.toContain('只处理重新开启后完成的下载')
  })

  it.each([
    ['business error', { success: false, message: 'config unavailable', data: initialPluginConfig }],
    ['missing data', { success: true, message: '' }],
    ['null data', { success: true, message: '', data: null }],
    ['array data', { success: true, message: '', data: [] }],
    ['partial config', { success: true, message: '', data: { enabled: true } }],
  ])('does not write when plugin config envelope is invalid (%s)', async (_case, payload) => {
    const { api, wrapper } = setup()
    const get = api.get.getMockImplementation()
    api.get.mockImplementation(url => url === 'plugin/plugin-x' ? payload : get(url))
    await flushPromises()
    await setSwitch(wrapper, 'automatic-toggle', true)
    await flushPromises()
    expect(api.put).not.toHaveBeenCalled()
    expect(switchComponent(wrapper, 'automatic-toggle').props('modelValue')).toBe(false)
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('已关闭')
    expect(wrapper.get('[data-testid="automatic-toggle-message"]').text()).toContain('保存未确认')
  })

  it('turns automatic triggering off without changing mode or accepted work', async () => {
    const { api, wrapper } = setup({ automaticEnabled: true })
    await flushPromises()
    await setSwitch(wrapper, 'automatic-toggle', false)
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('plugin/plugin-x', { ...initialPluginConfig, discovery_enabled: false })
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('已关闭')
    expect(wrapper.text()).toContain('已受理的整理不会取消')
    expect(wrapper.text()).toContain('自动触发已关闭')
  })

  it('locks the switch and status/condition refresh while saving and prevents duplicate writes', async () => {
    const { api, wrapper } = setup()
    const pending = deferred()
    const save = api.put.getMockImplementation()
    api.put.mockImplementation(async (...args) => { await pending.promise; return save(...args) })
    await flushPromises()
    await setSwitch(wrapper, 'automatic-toggle', true)
    await flushPromises()
    expect(switchComponent(wrapper, 'automatic-toggle').props('modelValue')).toBe(false)
    expect(switchComponent(wrapper, 'automatic-toggle').props('disabled')).toBe(true)
    expect(wrapper.get('[data-testid="automatic-refresh"]').element.disabled).toBe(true)
    expect(wrapper.get('[data-testid="automatic-check"]').element.disabled).toBe(true)
    await switchComponent(wrapper, 'automatic-toggle').trigger('click')
    expect(api.put).toHaveBeenCalledTimes(1)
    pending.resolve()
    await flushPromises()
    expect(api.put).toHaveBeenCalledTimes(1)
  })

  it('reports an unconfirmed timeout when read-only status readback also fails', async () => {
    const { api, wrapper } = setup()
    const get = api.get.getMockImplementation()
    let statusReads = 0
    api.get.mockImplementation(async url => {
      if (url.endsWith('/automatic/status') && ++statusReads > 1) throw new Error('unavailable')
      return get(url)
    })
    api.put.mockRejectedValue(new Error('timeout'))
    await flushPromises()
    await setSwitch(wrapper, 'automatic-toggle', true)
    await flushPromises()
    expect(api.put).toHaveBeenCalledTimes(1)
    expect(statusReads).toBe(2)
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('状态未知')
    expect(switchComponent(wrapper, 'automatic-toggle').props('disabled')).toBe(true)
    expect(wrapper.get('[data-testid="automatic-toggle-message"]').text()).toContain('保存未确认；状态未知')
    expect(wrapper.text()).not.toContain('自动触发已开启。')
    expect(wrapper.text()).not.toContain('重新开启后也会处理启用前已完成的旧任务')
  })

  it('shows armed automatic mode and its backend start time', async () => {
    const { api, wrapper } = setup()
    const startedAt = 1761113520
    api.get.mockImplementation(async url => url.endsWith('/options') ? options : ({ ...automaticStatus, enabled: true, mode: 'execute', armed: true, started_at: startedAt }))
    await flushPromises()
    expect(wrapper.text()).toContain('自动整理并刮削')
    expect(wrapper.text()).toContain('已启动')
    expect(wrapper.text()).toContain(new Date(startedAt * 1000).toLocaleString())
  })

  it('keeps manual preview available when automatic status is unavailable', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async url => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) throw new Error('unsupported')
      return { state: 'completed', items: [], summary: {} }
    })
    await flushPromises()
    expect(wrapper.text()).toContain('自动触发状态未获取')
    await inputFor(wrapper, 'source-path').setValue('/downloads/manual')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({ path: '/downloads/manual' }))
    expect(wrapper.text()).toContain('ID-123')
  })

  it('invalidates preview on edits and requires confirmation before one execute', async () => {
    const { api, wrapper } = setup()
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await selectValue(wrapper, 'media-source', 'manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="execute"]').element.disabled).toBe(true)
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await flushPromises()
    expect(api.post.mock.calls.filter(([url]) => url.endsWith('/execute'))).toHaveLength(1)
    expect(wrapper.text()).toContain('硬链接整理已核实；刮削结果请查看 MoviePilot 整理历史')
    expect(wrapper.text()).not.toContain('整理已完成')
    await inputFor(wrapper, 'target-path').setValue('/library/other')
    expect(wrapper.text()).not.toContain('预览有效')
  })

  it('offers read-only status refresh after an uncertain execution response', async () => {
    const { api, wrapper } = setup()
    api.post.mockImplementation(async (url) => url.endsWith('/preview') ? goodPreview : Promise.reject(new Error('timeout')))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await selectValue(wrapper, 'media-source', 'manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('结果未知')
    await wrapper.get('[data-testid="refresh-status"]').trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x/manual/status', { params: { preview_id: 'p1' } })
    expect(wrapper.get('[data-testid="execute"]').element.disabled).toBe(true)
  })

  it('treats backend dir rows as folders and navigates instead of selecting them', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async (url, config) => url.endsWith('/options') ? options : ({ path: config.params.path, parent: '/downloads', items: [{ name: 'Season 1', path: '/downloads/Season 1', type: 'dir' }] }))
    await flushPromises()
    await wrapper.get('[data-testid="browse-source"]').trigger('click')
    await flushPromises()
    await document.body.querySelector('[data-testid="browse-item"]')?.click()
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x/manual/browse', { params: { path: '/downloads/Season 1', kind: 'source' } })
    expect(inputFor(wrapper, 'source-path').element.value).toBe('')
  })

  it('allows path edits during preview and rejects the old response after its request generation changes', async () => {
    const { api, wrapper } = setup()
    const pending = deferred()
    api.post.mockReturnValueOnce(pending.promise)
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    expect(inputFor(wrapper, 'source-path').element.disabled).toBe(false)
    expect(wrapper.get('[data-testid="reload-options"]').element.disabled).toBe(false)
    inputFor(wrapper, 'source-path').element.value = '/downloads/changed'
    await inputFor(wrapper, 'source-path').trigger('input')
    pending.resolve({ ...goodPreview, preview_id: 'stale-preview' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('ID-123')
    expect(wrapper.text()).not.toContain('stale-preview')
  })

  it('shows failed distinctly and allows another selection only after terminal failure', async () => {
    const { api, wrapper } = setup()
    api.post.mockImplementation(async url => url.endsWith('/preview') ? goodPreview : ({ preview_id: 'p1', state: 'failed', items: [], summary: { success: 0, failed: 1 } }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('整理失败')
    expect(inputFor(wrapper, 'source-path').element.disabled).toBe(false)
    await inputFor(wrapper, 'source-path').setValue('/downloads/retry')
    expect(wrapper.text()).not.toContain('ID-123')
    expect(sessionStorage.getItem('xiaojiejiescraping:manual-pending:plugin-x')).toBeNull()
  })

  it('locks all path and source controls while execute is in flight', async () => {
    const { api, wrapper } = setup()
    const pending = deferred()
    api.post.mockImplementation(url => url.endsWith('/preview') ? Promise.resolve(goodPreview) : pending.promise)
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    expect(inputFor(wrapper, 'source-path').element.disabled).toBe(true)
    expect(inputFor(wrapper, 'target-path').element.disabled).toBe(true)
    expect(selectComponent(wrapper, 'media-source').props('disabled')).toBe(true)
    expect(wrapper.get('[data-testid="browse-source"]').element.disabled).toBe(true)
    expect(wrapper.get('[data-testid="reload-options"]').element.disabled).toBe(true)
    pending.resolve({ preview_id: 'p1', state: 'completed', items: [], summary: { success: 1, failed: 0 } })
    await flushPromises()
    expect(inputFor(wrapper, 'source-path').element.disabled).toBe(false)
    await inputFor(wrapper, 'source-path').setValue('/downloads/next')
    expect(sessionStorage.getItem('xiaojiejiescraping:manual-pending:plugin-x')).toBeNull()
  })

  it('preserves recognized metadata and maps completed status items by source and target', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async url => url.endsWith('/options') ? options : ({
      preview_id: 'p1', state: 'completed',
      items: [{ source: '/src/a.mp4', target: '/dst/a.mp4', state: 'completed', message: '', completion_scope: 'file_transfer' }],
      summary: { total: 1, success: 1, failed: 0 },
    }))
    api.post.mockImplementation(async url => url.endsWith('/preview') ? goodPreview : ({ preview_id: 'p1', state: 'accepted', items: [], summary: { total: 1, success: 0, failed: 0 } }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="refresh-status"]').trigger('click')
    await flushPromises()
    const row = wrapper.get('tbody tr').text()
    expect(row).toContain('影片')
    expect(row).toContain('ID-123')
    expect(row).toContain('硬链接已核验')
    expect(row).not.toContain('失败')
  })

  it('restores a completed status-only row without presenting it as an unrecognized failure', async () => {
    sessionStorage.setItem('xiaojiejiescraping:manual-pending:plugin-x', 'restored-p1')
    const api = {
      get: vi.fn(async url => url.endsWith('/options') ? options : ({
        preview_id: 'restored-p1', state: 'completed',
        items: [{ source: '/src/b.mp4', target: '/dst/b.mp4', state: 'completed', message: '', completion_scope: 'file_transfer' }],
        summary: { total: 1, success: 1, failed: 0 },
      })),
      post: vi.fn(),
    }
    const wrapper = mount(Page, { ...vuetifyOptions(), props: { api, pluginId: 'plugin-x', sourcePluginId: 'source-x' } })
    mounted.push(wrapper)
    await flushPromises()
    const row = wrapper.get('tbody tr').text()
    expect(api.get).toHaveBeenCalledWith('plugin/plugin-x/manual/status', { params: { preview_id: 'restored-p1' } })
    expect(api.post).not.toHaveBeenCalled()
    expect(row).toContain('/src/b.mp4')
    expect(row).toContain('/dst/b.mp4')
    expect(row).toContain('硬链接已核验')
    expect(row).not.toContain('未识别')
    expect(row).not.toContain('失败')
    expect(wrapper.text()).toContain('硬链接整理已核实；刮削结果请查看 MoviePilot 整理历史')
    expect(sessionStorage.getItem('xiaojiejiescraping:manual-pending:plugin-x')).toBeNull()
  })

  it('keeps unknown work locked and restores its preview id with read-only status after remount', async () => {
    const { api, wrapper } = setup()
    api.post.mockImplementation(async url => url.endsWith('/preview') ? goodPreview : Promise.reject(new Error('timeout')))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    await wrapper.get('[data-testid="execute"]').trigger('click')
    await flushPromises()
    expect(sessionStorage.getItem('xiaojiejiescraping:manual-pending:plugin-x')).toBe('p1')
    expect(inputFor(wrapper, 'source-path').element.disabled).toBe(true)
    inputFor(wrapper, 'source-path').element.value = '/downloads/other'
    await inputFor(wrapper, 'source-path').trigger('input')
    expect(wrapper.text()).toContain('结果未知')
    expect(wrapper.text()).toContain('ID-123')
    wrapper.unmount()
    const restoredApi = {
      get: vi.fn(async url => url.endsWith('/options') ? options : ({ state: 'accepted', items: [], summary: { success: 0, failed: 0 } })),
      post: vi.fn(),
    }
    const restored = mount(Page, { ...vuetifyOptions(), props: { api: restoredApi, pluginId: 'plugin-x', sourcePluginId: 'source-x' } })
    mounted.push(restored)
    await flushPromises()
    expect(restoredApi.get).toHaveBeenCalledWith('plugin/plugin-x/manual/status', { params: { preview_id: 'p1' } })
    expect(restoredApi.post).not.toHaveBeenCalled()
    expect(inputFor(restored, 'source-path').element.disabled).toBe(true)
    expect(restored.text()).toContain('请求已受理')
  })

  it('automatically disables execute when the preview TTL elapses', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-22T00:00:00Z'))
    const { api, wrapper } = setup()
    api.post.mockImplementation(async () => ({ ...goodPreview, expires_at: (Date.now() + 2000) / 1000 }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/item')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    expect(wrapper.get('[data-testid="execute"]').element.disabled).toBe(false)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper.get('[data-testid="execute"]').element.disabled).toBe(true)
    expect(wrapper.text()).toContain('预览已过期')
  })
})

it('config edits preserve every original and unknown setting in the saved object', async () => {
  const initialConfig = { enabled: false, sources: ['manko'], storage: 'local', recognize_dirs: '', image_hosts: '', discovery_enabled: false, downloader: '', torrent_tag: '', actor_images: false, handoff_mode: 'discover', target_path: '', transfer_type: '', ownership_ack: false, auxiliary_ack: false, handoff_limit: 1, future_key: { keep: true } }
  const wrapper = mount(Config, { ...vuetifyOptions(), props: { initialConfig } })
  mounted.push(wrapper)
  expect(inputFor(wrapper, 'config-auto_new_only').element.checked).toBe(true)
  expect(wrapper.get('[data-testid="config-handoff-mode"]').text()).toContain('只读发现')
  await inputFor(wrapper, 'config-enabled').setValue(true)
  await wrapper.get('[data-testid="config-save"]').trigger('click')
  expect(wrapper.emitted('save')[0][0]).toMatchObject({ ...initialConfig, enabled: true, auto_new_only: true })
  expect(wrapper.emitted('save')[0][0].auto_new_only).toBe(true)
  expect(selectComponent(wrapper, 'config-automatic-match-mode').props('modelValue')).toBe('tag_and_path')
})

it('defaults a new configuration to OR matching while preserving all fields on save', async () => {
  const wrapper = mount(Config, { ...vuetifyOptions(), props: { initialConfig: {} } })
  mounted.push(wrapper)
  expect(selectComponent(wrapper, 'config-automatic-match-mode').props('modelValue')).toBe('tag_or_path')
  await wrapper.get('[data-testid="config-save"]').trigger('click')
  expect(wrapper.emitted('save')[0][0].automatic_match_mode).toBe('tag_or_path')
})
