<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN,
} from 'naive-ui'
import { computed } from 'vue'

import { useTheme } from '@/stores/theme'
import { naiveThemeOverrides } from '@/styles/naiveTheme'

const theme = useTheme()
const themeOverrides = computed(() => naiveThemeOverrides(theme.isDark.value))
</script>

<template>
  <NConfigProvider
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme="theme.isDark.value ? darkTheme : null"
    :theme-overrides="themeOverrides"
  >
    <NDialogProvider>
      <NMessageProvider>
        <RouterView />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>
