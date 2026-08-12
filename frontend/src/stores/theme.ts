import { computed, ref } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'cx.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'
const preference = ref<ThemePreference>('system')
const systemDark = ref(false)
let initialized = false
let mediaQuery: MediaQueryList | null = null

function storedPreference(): ThemePreference {
  const value = localStorage.getItem(STORAGE_KEY)
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system'
}

function resolveTheme(value: ThemePreference): ResolvedTheme {
  return value === 'system' ? (systemDark.value ? 'dark' : 'light') : value
}

function applyRootTheme(resolved: ResolvedTheme): void {
  document.documentElement.dataset.theme = resolved
  document.documentElement.style.colorScheme = resolved
}

function handleSystemTheme(event: MediaQueryListEvent): void {
  systemDark.value = event.matches
  if (preference.value === 'system') applyRootTheme(resolveTheme('system'))
}

function initialize(): void {
  if (initialized) return
  initialized = true
  mediaQuery = window.matchMedia(DARK_QUERY)
  systemDark.value = mediaQuery.matches
  preference.value = storedPreference()
  applyRootTheme(resolveTheme(preference.value))
  mediaQuery.addEventListener('change', handleSystemTheme)
}

export function useTheme() {
  initialize()
  const resolvedTheme = computed<ResolvedTheme>(() => resolveTheme(preference.value))
  const isDark = computed(() => resolvedTheme.value === 'dark')

  function setPreference(value: ThemePreference): void {
    preference.value = value
    localStorage.setItem(STORAGE_KEY, value)
    applyRootTheme(resolveTheme(value))
  }

  return { preference, resolvedTheme, isDark, setPreference }
}
