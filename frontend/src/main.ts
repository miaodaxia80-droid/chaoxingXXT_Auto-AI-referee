import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/base.css'

createApp(App).use(createPinia()).use(VueQueryPlugin).use(router).mount('#app')
