import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = process.env.MOVIEPILOT_FRONTEND_DEPS
if (!root) throw new Error('MOVIEPILOT_FRONTEND_DEPS must point to the installed MoviePilot frontend')
const require = createRequire(path.join(root, 'package.json'))
const vue = require('@vitejs/plugin-vue')
const federation = require('@originjs/vite-plugin-federation')
const vuePlugin = vue.default ?? vue
const here = path.dirname(fileURLToPath(import.meta.url))

export default {
  root: here,
  plugins: [vuePlugin(), federation({
    name: 'xiaojiejiescraping',
    filename: 'remoteEntry.js',
    exposes: {
      './AppPage': path.join(here, 'src/Page.vue'),
      './Page': path.join(here, 'src/PageAlias.js'),
      './Config': path.join(here, 'src/Config.vue'),
    },
    shared: { vue: { requiredVersion: false, generate: false, singleton: true } },
  })],
  resolve: { alias: { vue: path.join(root, 'node_modules/vue/dist/vue.esm-bundler.js'), '@vue/test-utils': path.join(root, 'node_modules/@vue/test-utils'), '@originjs/vite-plugin-federation': require.resolve('@originjs/vite-plugin-federation') }, dedupe: ['vue'] },
  build: { target: 'esnext', cssCodeSplit: false, outDir: path.join(here, 'dist'), assetsDir: 'assets', emptyOutDir: true },
  test: { environment: 'jsdom', include: ['tests/**/*.test.js'] },
}
