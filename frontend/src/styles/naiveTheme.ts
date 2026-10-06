import type { GlobalThemeOverrides } from 'naive-ui'

// Naive UI derives hover/pressed/alpha variants from concrete colors, so the
// brand palette is duplicated here instead of referencing CSS variables.
const FONT_FAMILY =
  'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'

const shared: GlobalThemeOverrides['common'] = {
  fontFamily: FONT_FAMILY,
  fontFamilyMono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  borderRadius: '6px',
  borderRadiusSmall: '4px',
}

const light: GlobalThemeOverrides = {
  common: {
    ...shared,
    primaryColor: '#176b55',
    primaryColorHover: '#1f7d64',
    primaryColorPressed: '#125a48',
    primaryColorSuppl: '#1f7d64',
    successColor: '#1d7a5c',
    successColorHover: '#24906d',
    successColorPressed: '#17644b',
    successColorSuppl: '#24906d',
    warningColor: '#c27400',
    warningColorHover: '#d98a17',
    warningColorPressed: '#9b5a00',
    warningColorSuppl: '#d98a17',
    errorColor: '#c12c4a',
    errorColorHover: '#d1435f',
    errorColorPressed: '#a3223d',
    errorColorSuppl: '#d1435f',
    infoColor: '#2566a7',
    infoColorHover: '#3479bd',
    infoColorPressed: '#1d538a',
    infoColorSuppl: '#3479bd',
  },
  DataTable: {
    thColor: '#f8faf9',
    thTextColor: '#66727b',
    thFontWeight: '600',
    tdColorHover: '#f6f9f8',
  },
}

const dark: GlobalThemeOverrides = {
  common: {
    ...shared,
    primaryColor: '#4ab894',
    primaryColorHover: '#5cc6a2',
    primaryColorPressed: '#3a9d7c',
    primaryColorSuppl: '#5cc6a2',
    successColor: '#4ab894',
    successColorHover: '#5cc6a2',
    successColorPressed: '#3a9d7c',
    successColorSuppl: '#5cc6a2',
    warningColor: '#f1b45d',
    warningColorHover: '#f4c37c',
    warningColorPressed: '#d99a40',
    warningColorSuppl: '#f4c37c',
    errorColor: '#f08aa0',
    errorColorHover: '#f4a3b5',
    errorColorPressed: '#d86f86',
    errorColorSuppl: '#f4a3b5',
    infoColor: '#75afe9',
    infoColorHover: '#8fbfee',
    infoColorPressed: '#5c98d4',
    infoColorSuppl: '#8fbfee',
    bodyColor: '#111617',
    cardColor: '#181f20',
    modalColor: '#1b2324',
    popoverColor: '#1d2526',
  },
  DataTable: {
    thColor: '#1b2324',
    thTextColor: '#a5b0b1',
    thFontWeight: '600',
    tdColor: '#181f20',
    tdColorHover: '#1d2627',
  },
}

export function naiveThemeOverrides(isDark: boolean): GlobalThemeOverrides {
  return isDark ? dark : light
}
