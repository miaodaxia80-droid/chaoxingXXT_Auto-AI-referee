<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN,
} from 'naive-ui'

import { useTheme } from '@/stores/theme'

const theme = useTheme()
</script>

<template>
  <NConfigProvider
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme="theme.isDark.value ? darkTheme : null"
  >
    <NDialogProvider>
      <NMessageProvider>
        <RouterView />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>
