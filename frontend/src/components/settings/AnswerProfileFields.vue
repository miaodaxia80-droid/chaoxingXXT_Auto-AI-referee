<script setup lang="ts">
import { Layers3 } from 'lucide-vue-next'
import {
  NAlert,
  NDynamicTags,
  NFormItem,
  NInput,
  NInputNumber,
  NSwitch,
  NTag,
} from 'naive-ui'

import type { AnswerProfile } from '@/api/types'

const props = withDefaults(
  defineProps<{
    modelValue: AnswerProfile
    disabled?: boolean
    modelCapable?: boolean
    compact?: boolean
  }>(),
  {
    disabled: false,
    modelCapable: true,
    compact: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: AnswerProfile]
}>()

function updateField<K extends keyof AnswerProfile>(key: K, value: AnswerProfile[K]) {
  emit('update:modelValue', {
    ...props.modelValue,
    models: [...props.modelValue.models],
    [key]: value,
  })
}

function updateModels(models: string[]) {
  updateField('models', models)
}

function createModel(label: string): string {
  return label.trim()
}

function updateMaxWorkers(value: number | null) {
  if (value !== null) updateField('max_workers', value)
}

function updateCacheTtl(value: number | null) {
  if (value !== null) updateField('cache_ttl_seconds', value)
}
</script>

<template>
  <section class="profile-editor" :class="{ compact }">
    <div class="profile-heading">
      <span class="profile-icon"><Layers3 :size="18" /></span>
      <div>
        <strong>答案增强</strong>
        <span>缓存、课程上下文与多模型协同</span>
      </div>
    </div>

    <div class="profile-row">
      <div class="profile-copy">
        <strong>多模型共识</strong>
        <span>并行请求多个模型，分歧时交由裁判模型判断</span>
      </div>
      <div class="profile-control">
        <NTag size="small" :type="modelValue.ensemble_enabled ? 'success' : 'default'">
          {{ modelValue.ensemble_enabled ? '已启用' : '已关闭' }}
        </NTag>
        <NSwitch
          :value="modelValue.ensemble_enabled"
          :disabled="disabled"
          aria-label="多模型共识"
          @update:value="updateField('ensemble_enabled', $event)"
        />
      </div>
    </div>

    <div v-if="modelValue.ensemble_enabled" class="profile-fields ensemble-fields">
      <NAlert v-if="!modelCapable" type="warning" :bordered="false">
        当前答案源不支持切换模型，多模型设置仅会在 Like 题库或模型 API 下生效。
      </NAlert>
      <NFormItem label="共识模型" class="models-field">
        <NDynamicTags
          :value="modelValue.models"
          :disabled="disabled"
          :max="8"
          :on-create="createModel"
          :input-props="{ maxlength: 512 }"
          @update:value="updateModels"
        />
      </NFormItem>
      <NFormItem label="裁判模型">
        <NInput
          :value="modelValue.referee_model"
          :disabled="disabled"
          :maxlength="512"
          placeholder="可选，留空时使用首个模型"
          @update:value="updateField('referee_model', $event)"
        />
      </NFormItem>
      <NFormItem label="最大并行数">
        <NInputNumber
          :value="modelValue.max_workers"
          :disabled="disabled"
          :min="1"
          :max="8"
          :precision="0"
          @update:value="updateMaxWorkers"
        />
      </NFormItem>
    </div>

    <div class="profile-row">
      <div class="profile-copy">
        <strong>答案缓存</strong>
        <span>相同题目优先使用仍在有效期内的答案</span>
      </div>
      <NSwitch
        :value="modelValue.cache_enabled"
        :disabled="disabled"
        aria-label="答案缓存"
        @update:value="updateField('cache_enabled', $event)"
      />
    </div>
    <div v-if="modelValue.cache_enabled" class="profile-fields cache-fields">
      <NFormItem label="缓存有效期">
        <NInputNumber
          :value="modelValue.cache_ttl_seconds"
          :disabled="disabled"
          :min="60"
          :max="2592000"
          :step="3600"
          :precision="0"
          @update:value="updateCacheTtl"
        >
          <template #suffix>秒</template>
        </NInputNumber>
      </NFormItem>
    </div>

    <div class="profile-row">
      <div class="profile-copy">
        <strong>课程上下文</strong>
        <span>向模型提供当前课程名称，减少跨学科歧义</span>
      </div>
      <NSwitch
        :value="modelValue.course_context_enabled"
        :disabled="disabled"
        aria-label="课程上下文"
        @update:value="updateField('course_context_enabled', $event)"
      />
    </div>

    <div class="profile-row web-search-row">
      <div class="profile-copy">
        <strong>外部联网搜索</strong>
        <span>使用 DuckDuckGo 搜索题目与课程上下文，为模型补充参考信息</span>
      </div>
      <NSwitch
        :value="modelValue.web_search_enabled"
        :disabled="disabled"
        aria-label="外部联网搜索"
        @update:value="updateField('web_search_enabled', $event)"
      />
    </div>
    <NAlert
      v-if="modelValue.web_search_enabled"
      class="search-disclosure"
      type="warning"
      :bordered="false"
    >
      仅 OpenAI 兼容接口与硅基流动支持外部搜索增强。题目与课程上下文将发送给
      DuckDuckGo；Like 题库自身的搜索开关独立生效。
    </NAlert>
  </section>
</template>

<style scoped>
.profile-editor {
  border-top: 1px solid var(--color-border-soft);
}

.profile-heading {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 16px 20px 8px;
}

.profile-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 6px;
  background: var(--color-accent-muted);
  color: var(--color-accent);
}

.profile-heading > div > strong,
.profile-heading > div > span,
.profile-copy > strong,
.profile-copy > span {
  display: block;
  overflow-wrap: anywhere;
}

.profile-heading > div > strong,
.profile-copy > strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.profile-heading > div > span,
.profile-copy > span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.profile-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  min-height: 62px;
  align-items: center;
  gap: 16px;
  padding: 11px 20px;
  border-bottom: 1px solid var(--color-border-faint);
}

.profile-copy {
  min-width: 0;
}

.profile-control {
  display: flex;
  align-items: center;
  gap: 9px;
}

.profile-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-surface-muted);
  padding: 14px 20px 0 68px;
}

.profile-fields :deep(.n-input-number),
.profile-fields :deep(.n-dynamic-tags) {
  width: 100%;
}

.ensemble-fields > :deep(.n-alert),
.ensemble-fields :deep(.models-field) {
  grid-column: 1 / -1;
}

.cache-fields {
  grid-template-columns: minmax(180px, 280px);
  justify-content: end;
}

.search-disclosure {
  margin: 12px 20px;
}

.compact {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
}

.compact .profile-heading {
  padding: 12px 14px 7px;
}

.compact .profile-row {
  padding-right: 14px;
  padding-left: 14px;
}

.compact .profile-fields {
  padding-right: 14px;
  padding-left: 14px;
}

.compact .search-disclosure {
  margin-right: 14px;
  margin-left: 14px;
}

@media (max-width: 720px) {
  .profile-heading,
  .profile-row {
    padding-right: 14px;
    padding-left: 14px;
  }

  .profile-fields {
    grid-template-columns: minmax(0, 1fr);
    padding-right: 14px;
    padding-left: 14px;
  }

  .ensemble-fields > :deep(.n-alert),
  .ensemble-fields :deep(.models-field) {
    grid-column: auto;
  }

  .cache-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .search-disclosure {
    margin-right: 14px;
    margin-left: 14px;
  }
}

@media (max-width: 420px) {
  .profile-control :deep(.n-tag) {
    display: none;
  }
}
</style>
