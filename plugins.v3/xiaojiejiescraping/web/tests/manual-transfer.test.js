import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as vuetifyComponents from 'vuetify/components'
import Page from '../src/Page.vue'
import Config from '../src/Config.vue'

const goodPreview = { preview_id: 'p1', expires_at: 4070908800, items: [{ source: '/src/a.mp4', target: '/dst/a.mp4', title: '影片', media_source: 'manko', media_id: 'ID-123', success: true, message: 'ok' }], summary: { total: 1, success: 1, failed: 0 } }
const options = { sources: [{ title: 'Manko', value: 'manko' }], roots: ['/downloads'], default_target_path: '/library', transfer_type: 'link', enabled: true }
const automaticStatus = { enabled: false, mode: 'discover', match_mode: 'tag_and_path', new_only: true, armed: false, started_at: null, interval_seconds: 300, downloader: 'qb', torrent_tag: 'Manko', roots: ['/downloads'], target_path: '/library', last_poll_at: null, counts: {}, error: '' }
const initialPluginConfig = { enabled: true, discovery_enabled: false, auto_new_only: true, automatic_match_mode: 'tag_and_path', downloader: 'qb', torrent_tag: 'Manko', torrent_category: '', recognize_dirs: '/downloads', target_path: '/library', handoff_mode: 'execute', ownership_ack: true, auxiliary_ack: true, future_setting: { retained: true } }
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
const setup = ({ automaticEnabled = false, newOnly = true, optionsValue = options } = {}) => {
  let pluginConfig = { ...initialPluginConfig, auto_new_only: newOnly, discovery_enabled: automaticEnabled }
  const api = {
    get: vi.fn(async (url) => url.endsWith('/options') ? optionsValue
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

  it('inherits the configured actress-folder option and includes it in a fresh preview', async () => {
    const { api, wrapper } = setup({ optionsValue: { ...options, actress_subfolder: true } })
    await flushPromises()
    expect(switchComponent(wrapper, 'actress-subfolder').props('modelValue')).toBe(true)
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({
      path: '/downloads/ADN-000775.mkv', actress_subfolder: true, actress_choices: {},
    }))
  })

  it('sends a chosen actor on a new preview after the candidate list requests a choice', async () => {
    const { api, wrapper } = setup()
    const choicePreview = {
      preview_id: null, expires_at: null,
      items: [{ source: '/downloads/ADN-000775.mkv', title: '影片', success: false,
        message: '请从本片可信女主演候选中选择后重新预览',
        needs_actress_choice: true, actor_candidates: ['女優甲', '女優乙'] }],
      summary: { total: 1, success: 0, failed: 1 },
    }
    let previewCount = 0
    api.post.mockImplementation(async url => url.endsWith('/preview')
      ? (++previewCount === 1 ? choicePreview : goodPreview)
      : ({ state: 'completed', items: [], summary: {} }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await setSwitch(wrapper, 'actress-subfolder', true)
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('请从本片可信女主演候选中选择后重新预览')
    await selectValue(wrapper, 'actress-choice-0', '女優乙')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenLastCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({
      actress_subfolder: true, actress_choices: { '/downloads/ADN-000775.mkv': '女優乙' },
    }))
  })

  it('shows the default trusted actress, clears confirmation on change, and previews the explicit choice', async () => {
    const { api, wrapper } = setup({ optionsValue: { ...options, actress_subfolder: true } })
    const selectedPreview = { ...goodPreview, items: [{ ...goodPreview.items[0],
      needs_actress_choice: true, actor_candidates: ['女優甲', '女優乙'], selected_actress: '女優甲' }] }
    api.post.mockImplementation(async url => url.endsWith('/preview') ? selectedPreview
      : ({ state: 'completed', items: [], summary: {} }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()

    expect(selectComponent(wrapper, 'actress-choice-0').props('modelValue')).toBe('女優甲')
    await inputFor(wrapper, 'confirm-transfer').setValue(true)
    expect(wrapper.get('[data-testid="execute"]').attributes('disabled')).toBeUndefined()
    await selectValue(wrapper, 'actress-choice-0', '女優乙')
    expect(wrapper.text()).toContain('选择已变更，请重新预览')
    expect(inputFor(wrapper, 'confirm-transfer').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="execute"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenLastCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({
      actress_subfolder: true, actress_choices: { '/src/a.mp4': '女優乙' },
    }))
  })

  it('explains strict source flags and waits without offering ordinary cast when none are trusted', async () => {
    const { api, wrapper } = setup()
    api.post.mockImplementation(async url => url.endsWith('/preview') ? {
      items: [{ source: '/downloads/ADN-000775.mkv', success: false,
        message: '本次来源识别没有可信女主演来源证据；等待来源数据补齐后重试',
        needs_actress_choice: false, actor_candidates: [] }],
      summary: { total: 1, success: 0, failed: 1 },
    } : ({ state: 'completed', items: [], summary: {} }))
    await flushPromises()
    expect(wrapper.text()).toContain('按识别来源提供的女优名单选择')
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await setSwitch(wrapper, 'actress-subfolder', true)
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('等待来源数据补齐后重试')
    expect(wrapper.findAll('[data-testid^="actress-choice-"]')).toHaveLength(0)
  })

  it('keeps other file choices visible and sends per-file selections together', async () => {
    const { api, wrapper } = setup()
    const choicePreview = {
      items: [
        { source: '/downloads/ADN-000775-A.mkv', title: '影片甲', success: false,
          needs_actress_choice: true, actor_candidates: ['女優甲', '女優乙'] },
        { source: '/downloads/ADN-000775-B.mkv', title: '影片乙', success: false,
          needs_actress_choice: true, actor_candidates: ['女優丙', '女優丁'] },
      ], summary: { total: 2, success: 0, failed: 2 },
    }
    api.post.mockImplementation(async url => url.endsWith('/preview') ? choicePreview : ({ state: 'completed' }))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/batch')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await setSwitch(wrapper, 'actress-subfolder', true)
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await selectValue(wrapper, 'actress-choice-0', '女優乙')
    expect(wrapper.findAllComponents(vuetifyComponents.VSelect)
      .filter(component => (component.attributes('data-testid') || '').startsWith('actress-choice-'))).toHaveLength(2)
    await selectValue(wrapper, 'actress-choice-1', '女優丁')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenLastCalledWith('plugin/plugin-x/manual/preview', expect.objectContaining({
      actress_choices: {
        '/downloads/ADN-000775-A.mkv': '女優乙',
        '/downloads/ADN-000775-B.mkv': '女優丁',
      },
    }))
  })

  it('clears actor choices when source, target, metadata source, or folder mode changes', async () => {
    const { api, wrapper } = setup({ optionsValue: {
      ...options, sources: [...options.sources, { title: 'FANZA', value: 'fanza' }],
    } })
    const choicePreview = {
      items: [{ source: '/downloads/ADN-000775.mkv', title: '影片', success: false,
        needs_actress_choice: true, actor_candidates: ['女優甲'] }],
      summary: { total: 1, success: 0, failed: 1 },
    }
    api.post.mockImplementation(async url => url.endsWith('/preview') ? choicePreview : ({}))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await setSwitch(wrapper, 'actress-subfolder', true)
    const chooseAndVerifyEmpty = async () => {
      await wrapper.get('[data-testid="preview"]').trigger('click')
      await flushPromises()
      expect(api.post.mock.calls.at(-1)[1].actress_choices).toEqual({})
      await selectValue(wrapper, 'actress-choice-0', '女優甲')
    }
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await selectValue(wrapper, 'actress-choice-0', '女優甲')
    await inputFor(wrapper, 'target-path').setValue('/library/changed')
    await chooseAndVerifyEmpty()
    await inputFor(wrapper, 'source-path').setValue('/downloads/changed')
    await chooseAndVerifyEmpty()
    await selectValueFromMenu(wrapper, 'media-source', 'FANZA')
    await chooseAndVerifyEmpty()
    await setSwitch(wrapper, 'actress-subfolder', false)
    await chooseAndVerifyEmpty()
  })

  it('ignores an old preview response after the actress-folder setting changes', async () => {
    const { api, wrapper } = setup()
    const pending = deferred()
    api.post.mockImplementation(url => url.endsWith('/preview') ? pending.promise : Promise.resolve({}))
    await flushPromises()
    await inputFor(wrapper, 'source-path').setValue('/downloads/ADN-000775.mkv')
    await selectValueFromMenu(wrapper, 'media-source', 'Manko')
    await wrapper.get('[data-testid="preview"]').trigger('click')
    await flushPromises()
    await setSwitch(wrapper, 'actress-subfolder', true)
    pending.resolve({ ...goodPreview, preview_id: 'stale-actress-preview' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('stale-actress-preview')
    expect(wrapper.text()).not.toContain('ID-123')
  })

  it('shows automatic files blocked for manual actress choice without retrying them', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async url => url.endsWith('/options') ? options
      : url.endsWith('/automatic/status') ? { ...automaticStatus,
        pending_actress_choices: [{ path: '/downloads/ADN-000775.mkv', reason: 'primary_actress_choice_required' }] }
        : url === 'plugin/plugin-x' ? { success: true, message: '', data: { ...initialPluginConfig } } : {})
    await flushPromises()
    expect(wrapper.text()).toContain('待人工选择女主演：/downloads/ADN-000775.mkv')
    expect(api.post).not.toHaveBeenCalled()
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

  it.each([
    ['name-asc', ['Folder 2', 'Folder 10', 'clip 2.mp4', 'clip 10.mp4', 'invalid.mp4', 'same.mp4', 'same.mp4', 'unknown.mp4', 'zero.mp4']],
    ['name-desc', ['Folder 10', 'Folder 2', 'zero.mp4', 'unknown.mp4', 'same.mp4', 'same.mp4', 'invalid.mp4', 'clip 10.mp4', 'clip 2.mp4']],
    ['modified-desc', ['Folder 10', 'Folder 2', 'clip 2.mp4', 'clip 10.mp4', 'same.mp4', 'same.mp4', 'zero.mp4', 'invalid.mp4', 'unknown.mp4']],
    ['modified-asc', ['Folder 2', 'Folder 10', 'zero.mp4', 'clip 2.mp4', 'clip 10.mp4', 'same.mp4', 'same.mp4', 'invalid.mp4', 'unknown.mp4']],
    ['size-desc', ['Folder 2', 'Folder 10', 'clip 2.mp4', 'same.mp4', 'same.mp4', 'clip 10.mp4', 'zero.mp4', 'invalid.mp4', 'unknown.mp4']],
    ['size-asc', ['Folder 2', 'Folder 10', 'zero.mp4', 'clip 10.mp4', 'same.mp4', 'same.mp4', 'clip 2.mp4', 'invalid.mp4', 'unknown.mp4']],
  ])('sorts browser entries by %s with directories first and local-only updates', async (sort, expectedNames) => {
    const { api, wrapper } = setup()
    const entries = [
      { name: 'Folder 10', path: '/downloads/Folder 10', type: 'dir', modify_time: 200, size: null },
      { name: 'Folder 2', path: '/downloads/Folder 2', type: 'dir', modify_time: 100, size: null },
      { name: 'clip 10.mp4', path: '/downloads/clip 10.mp4', type: 'file', modify_time: 20, size: 2 },
      { name: 'clip 2.mp4', path: '/downloads/clip 2.mp4', type: 'file', modify_time: 20, size: 10 },
      { name: 'zero.mp4', path: '/downloads/zero.mp4', type: 'file', modify_time: 0, size: 0 },
      { name: 'invalid.mp4', path: '/downloads/invalid.mp4', type: 'file', modify_time: 'invalid', size: -1 },
      { name: 'unknown.mp4', path: '/downloads/unknown.mp4', type: 'file' },
      { name: 'same.mp4', path: '/downloads/z/same.mp4', type: 'file', modify_time: 20, size: 5 },
      { name: 'same.mp4', path: '/downloads/a/same.mp4', type: 'file', modify_time: 20, size: 5 },
    ]
    api.get.mockImplementation(async (url, request) => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) return automaticStatus
      if (url === 'plugin/plugin-x') return { success: true, message: '', data: { ...initialPluginConfig } }
      if (url === 'download/clients') return { success: true, message: '', data: [] }
      if (url.endsWith('/manual/browse')) return { path: request.params.path, parent: '/', items: entries }
      return {}
    })
    await flushPromises()
    await wrapper.get('[data-testid="browse-source"]').trigger('click')
    await flushPromises()
    const browseCalls = () => api.get.mock.calls.filter(([url]) => url.endsWith('/manual/browse')).length
    expect(browseCalls()).toBe(1)
    await selectValue(wrapper, 'browse-sort', sort)
    const rows = [...document.body.querySelectorAll('[data-testid="browse-item"]')]
    expect(rows.map(row => row.querySelector('.v-list-item-title')?.textContent.trim())).toEqual(expectedNames)
    expect(rows.filter(row => row.textContent.includes('/downloads/a/same.mp4'))[0]).toBeTruthy()
    expect(browseCalls()).toBe(1)
  })

  it('keeps the chosen sort while navigating and selects the original path from the sorted browser row', async () => {
    const { api, wrapper } = setup()
    api.get.mockImplementation(async (url, request) => {
      if (url.endsWith('/options')) return options
      if (url.endsWith('/automatic/status')) return automaticStatus
      if (url === 'plugin/plugin-x') return { success: true, message: '', data: { ...initialPluginConfig } }
      if (url === 'download/clients') return { success: true, message: '', data: [] }
      if (url.endsWith('/manual/browse')) return request.params.path === '/downloads'
        ? { path: '/downloads', parent: '/', items: [
            { name: 'Folder 10', path: '/downloads/Folder 10', type: 'dir' },
            { name: 'Folder 2', path: '/downloads/Folder 2', type: 'dir' },
          ] }
        : { path: request.params.path, parent: '/downloads', items: [
            { name: 'Picked 10.mp4', path: `${request.params.path}/Picked 10.mp4`, type: 'file', size: 10 },
            { name: 'Picked 2.mp4', path: `${request.params.path}/Picked 2.mp4`, type: 'file', size: 2 },
          ] }
      return {}
    })
    await flushPromises()
    await wrapper.get('[data-testid="browse-source"]').trigger('click')
    await flushPromises()
    await selectValue(wrapper, 'browse-sort', 'size-asc')
    const folder = [...document.body.querySelectorAll('[data-testid="browse-item"]')]
      .find(row => row.querySelector('.v-list-item-title')?.textContent.trim() === 'Folder 2')
    expect(folder).toBeTruthy()
    folder.click()
    await flushPromises()
    expect(selectComponent(wrapper, 'browse-sort').props('modelValue')).toBe('size-asc')
    const selected = [...document.body.querySelectorAll('[data-testid="browse-item"]')]
      .find(row => row.querySelector('.v-list-item-title')?.textContent.trim() === 'Picked 2.mp4')
    expect(selected).toBeTruthy()
    selected.click()
    await flushPromises()
    expect(inputFor(wrapper, 'source-path').element.value).toBe('/downloads/Folder 2/Picked 2.mp4')
    expect(api.get.mock.calls.filter(([url]) => url.endsWith('/manual/browse')).map(([, request]) => request.params.path))
      .toEqual(['/downloads', '/downloads/Folder 2'])
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

  it('saves an exact qBittorrent category with empty tags and roots, preserves config, and reads it back', async () => {
    let current = { ...initialPluginConfig, automatic_match_mode: 'category', torrent_tag: '', torrent_category: '', recognize_dirs: '' }
    const api = {
      get: vi.fn(async url => url.endsWith('/options') ? options
        : url.endsWith('/automatic/status') ? { ...automaticStatus, match_mode: 'category', torrent_tag: '', torrent_category: current.torrent_category }
          : url === 'download/clients' ? { success: true, message: '', data: [{ name: 'qb', type: 'qbittorrent' }] }
            : url === 'plugin/plugin-x' ? { success: true, message: '', data: { ...current } } : {}),
      put: vi.fn(async (_url, body) => { current = { ...body }; return { success: true } }),
      post: vi.fn(),
    }
    const wrapper = mount(Page, { ...vuetifyOptions(), props: { api, pluginId: 'plugin-x', sourcePluginId: 'source-x' } })
    mounted.push(wrapper)
    await flushPromises()
    expect(selectComponent(wrapper, 'automatic-match-mode').props('items')).toContainEqual({ title: '按下载器分类', value: 'category' })
    expect(wrapper.text()).toContain('下载器 / 分类')
    expect(wrapper.find('[data-testid="automatic-tag"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('分类不是标签')
    expect(wrapper.text()).toContain('必须精确匹配')
    expect(wrapper.text()).toContain('来源路径需在 MoviePilot 可见')
    expect(wrapper.get('[data-testid="automatic-save-rules"]').element.disabled).toBe(true)
    const category = wrapper.get('[data-testid="automatic-category"]')
    expect(category.get('input').attributes('placeholder')).toBe('AV')
    await category.get('input').setValue('AV')
    expect(wrapper.get('[data-testid="automatic-save-rules"]').element.disabled).toBe(false)
    await wrapper.get('[data-testid="automatic-save-rules"]').trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('plugin/plugin-x', {
      ...initialPluginConfig,
      automatic_match_mode: 'category', torrent_tag: '', torrent_category: 'AV', recognize_dirs: '',
    })
    expect(current.torrent_tag).toBe('')
    expect(current.torrent_category).toBe('AV')
    expect(current.discovery_enabled).toBe(false)
    expect(current.future_setting).toEqual({ retained: true })
    await wrapper.get('[data-testid="automatic-reload-rules"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="automatic-category"]').get('input').element.value).toBe('AV')
    expect(wrapper.text()).toContain('qb / AV')
    expect(wrapper.get('[data-testid="automatic-toggle-state"]').text()).toBe('已关闭')
  })

  it('keeps a legacy torrent tag unchanged while saving category and blocks a blank category', async () => {
    let current = { ...initialPluginConfig, automatic_match_mode: 'category', torrent_tag: 'legacy-tag', torrent_category: '', recognize_dirs: '' }
    const api = {
      get: vi.fn(async url => url.endsWith('/options') ? options
        : url.endsWith('/automatic/status') ? { ...automaticStatus, match_mode: 'category', torrent_category: current.torrent_category }
          : url === 'download/clients' ? { success: true, message: '', data: [{ name: 'qb', type: 'qbittorrent' }] }
            : url === 'plugin/plugin-x' ? { success: true, message: '', data: { ...current } } : {}),
      put: vi.fn(async (_url, body) => { current = { ...body }; return { success: true } }),
      post: vi.fn(),
    }
    const wrapper = mount(Page, { ...vuetifyOptions(), props: { api, pluginId: 'plugin-x', sourcePluginId: 'source-x' } })
    mounted.push(wrapper)
    await flushPromises()
    expect(wrapper.get('[data-testid="automatic-save-rules"]').element.disabled).toBe(true)
    current = { ...current, torrent_tag: 'latest-legacy-tag' }
    await wrapper.get('[data-testid="automatic-category"]').get('input').setValue('AV')
    await wrapper.get('[data-testid="automatic-save-rules"]').trigger('click')
    await flushPromises()
    expect(current.torrent_tag).toBe('latest-legacy-tag')
    expect(current.torrent_category).toBe('AV')
    await wrapper.get('[data-testid="automatic-category"]').get('input').setValue('')
    expect(wrapper.get('[data-testid="automatic-save-rules"]').element.disabled).toBe(true)
    expect(api.put).toHaveBeenCalledTimes(1)
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
  const initialConfig = { enabled: false, sources: ['manko'], storage: 'local', recognize_dirs: '', image_hosts: '', discovery_enabled: false, downloader: '', torrent_tag: '', actor_images: false, actress_subfolder: false, handoff_mode: 'discover', target_path: '', transfer_type: '', ownership_ack: false, auxiliary_ack: false, handoff_limit: 1, future_key: { keep: true } }
  const wrapper = mount(Config, { ...vuetifyOptions(), props: { initialConfig } })
  mounted.push(wrapper)
  expect(inputFor(wrapper, 'config-auto_new_only').element.checked).toBe(true)
  expect(wrapper.get('[data-testid="config-handoff-mode"]').text()).toContain('只读发现')
  await inputFor(wrapper, 'config-enabled').setValue(true)
  await wrapper.get('[data-testid="config-save"]').trigger('click')
  expect(wrapper.emitted('save')[0][0]).toMatchObject({ ...initialConfig, enabled: true, auto_new_only: true })
  expect(wrapper.emitted('save')[0][0].auto_new_only).toBe(true)
  expect(selectComponent(wrapper, 'config-automatic-match-mode').props('modelValue')).toBe('tag_and_path')
  await setSwitch(wrapper, 'config-actress_subfolder', true)
  await wrapper.get('[data-testid="config-save"]').trigger('click')
  expect(wrapper.emitted('save')[1][0].actress_subfolder).toBe(true)
})

it('defaults a new configuration to OR matching while preserving all fields on save', async () => {
  const wrapper = mount(Config, { ...vuetifyOptions(), props: { initialConfig: {} } })
  mounted.push(wrapper)
  expect(selectComponent(wrapper, 'config-automatic-match-mode').props('modelValue')).toBe('tag_or_path')
  await wrapper.get('[data-testid="config-save"]').trigger('click')
  expect(wrapper.emitted('save')[0][0].automatic_match_mode).toBe('tag_or_path')
})
