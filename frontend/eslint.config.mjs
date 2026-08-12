import eslint from '@eslint/js'
import tsParser from '@typescript-eslint/parser'
import globals from 'globals'
import vue from 'eslint-plugin-vue'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', '*.d.ts', '*.js', '*.tsbuildinfo'],
  },
  eslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.ts'],
    languageOptions: {
      globals: globals.browser,
      parser: tsParser,
      parserOptions: {
        sourceType: 'module',
      },
    },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'vue/html-self-closing': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/multi-word-component-names': 'off',
      'vue/singleline-html-element-content-newline': 'off',
    },
  },
  {
    files: ['**/*.vue'],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        extraFileExtensions: ['.vue'],
        parser: tsParser,
        sourceType: 'module',
      },
    },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'vue/html-self-closing': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/multi-word-component-names': 'off',
      'vue/singleline-html-element-content-newline': 'off',
    },
  },
  {
    files: ['vite.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
]
