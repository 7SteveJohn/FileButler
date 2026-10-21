import { createApp } from 'vue'
import naive from 'naive-ui'
import App from './App.vue'
import { i18n } from './lib/i18n'
import './style.css'

createApp(App).use(naive).use(i18n).mount('#app')
