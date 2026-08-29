<script setup lang="ts">
import { Ban, Copy, CreditCard, Plus } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NRadioGroup,
  NRadioButton,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, h, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { ApiError, apiRequest } from '@/api/client'
import type {
  CardKey,
  CardKeyGenerateResponse,
  CardKeyStatus,
} from '@/api/types'

const message = useMessage()
const dialog = useDialog()
const queryClient = useQueryClient()

const statusFilter = ref<CardKeyStatus | 'all'>('all')
const cardsQuery = useQuery({
  queryKey: ['card-keys', statusFilter],
  queryFn: () =>
    apiRequest<CardKey[]>(
      statusFilter.value === 'all' ? '/cards' : `/cards?status_filter=${statusFilter.value}`,
    ),
})

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

// --- 生成 ---
const showGenerate = ref(false)
const showResult = ref(false)
const generateForm = ref({ kind: 'time' as 'time' | 'count', value: 30, count: 10, batch: '' })
const lastBatch = ref<CardKeyGenerateResponse | null>(null)

const generateMutation = useMutation({
  mutationFn: () =>
    apiRequest<CardKeyGenerateResponse>('/cards', {
      method: 'POST',
      body: JSON.stringify({
        kind: generateForm.value.kind,
        value: generateForm.value.value,
        count: generateForm.value.count,
        batch: generateForm.value.batch.trim(),
      }),
    }),
  onSuccess: (result) => {
    lastBatch.value = result
    showResult.value = true
    message.success(`已生成 ${result.created} 个卡密`)
    void queryClient.invalidateQueries({ queryKey: ['card-keys'] })
  },
  onError: (error) =>
    message.error(error instanceof ApiError ? error.message : '生成失败，请稍后重试'),
})

function submitGenerate(): void {
  if (generateForm.value.value <= 0) {
    message.warning('面值必须大于 0')
    return
  }
  generateMutation.mutate()
}

async function copyBatch(): Promise<void> {
  const items = lastBatch.value?.items ?? []
  const text = items.map((item) => `${item.code}    ${kindLabel(item.kind)}${item.value}`).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制全部卡密')
  } catch {
    message.error('复制失败，请手动选择文本复制')
  }
}

function kindLabel(kind: string): string {
  return kind === 'time' ? '时间卡' : '次数卡'
}

function statusTag(status: CardKeyStatus) {
  const map = {
    unused: { label: '未使用', type: 'success' },
    used: { label: '已核销', type: 'default' },
    revoked: { label: '已作废', type: 'warning' },
  } as const
  const item = map[status]
  return h(NTag, { size: 'small', type: item.type, bordered: false }, { default: () => item.label })
}

const revokeMutation = useMutation({
  mutationFn: (id: number) =>
    apiRequest<CardKey>(`/cards/${id}/revoke`, { method: 'POST', body: JSON.stringify({}) }),
  onSuccess: () => {
    message.success('已作废')
    void queryClient.invalidateQueries({ queryKey: ['card-keys'] })
  },
  onError: (error) =>
    message.error(error instanceof ApiError ? error.message : '作废失败，请稍后重试'),
})

function requestRevoke(card: CardKey): void {
  dialog.warning({
    title: '作废卡密',
    content: `作废后该卡（${card.code_hint}）将无法被兑换。确认作废？`,
    positiveText: '作废',
    negativeText: '取消',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => revokeMutation.mutate(card.id),
  })
}

const columns = computed<DataTableColumns<CardKey>>(() => [
  { title: 'ID', key: 'id', width: 70 },
  {
    title: '卡密',
    key: 'code_hint',
    render: (card) =>
      h('code', { style: 'font-family:var(--font-mono, monospace);font-size:13px' }, card.code_hint),
  },
  {
    title: '类型',
    key: 'kind',
    width: 130,
    render: (card) => `${kindLabel(card.kind)} · ${card.value}${card.kind === 'time' ? ' 天' : ' 次'}`,
  },
  { title: '批次', key: 'batch', render: (card) => card.batch || '—', ellipsis: true },
  { title: '状态', key: 'status', width: 90, render: (card) => statusTag(card.status) },
  { title: '核销时间', key: 'used_at', width: 150, render: (card) => formatDateTime(card.used_at) },
  { title: '生成时间', key: 'created_at', width: 150, render: (card) => formatDateTime(card.created_at) },
  {
    title: '',
    key: 'actions',
    width: 70,
    render: (card) =>
      card.status === 'unused'
        ? h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              type: 'error',
              loading: revokeMutation.isPending.value,
              onClick: () => requestRevoke(card),
            },
            { icon: () => h(Ban, { size: 15 }) },
          )
        : null,
  },
])

const filterOptions: { label: string; value: CardKeyStatus | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '未使用', value: 'unused' },
  { label: '已核销', value: 'used' },
  { label: '已作废', value: 'revoked' },
]
</script>

<template>
  <section class="content-section flush">
    <div class="section-heading padded">
      <div>
        <h2>卡密管理</h2>
        <p>批量生成时间卡/次数卡，卡密明文仅在生成时展示一次</p>
      </div>
      <div class="heading-actions">
        <NRadioGroup v-model:value="statusFilter" size="small">
          <NRadioButton
            v-for="option in filterOptions"
            :key="option.value"
            :value="option.value"
            :label="option.label"
          />
        </NRadioGroup>
        <NButton
          quaternary
          circle
          aria-label="刷新"
          @click="queryClient.invalidateQueries({ queryKey: ['card-keys'] })"
        >
          <template #icon><CreditCard :size="16" /></template>
        </NButton>
        <NButton type="primary" @click="showGenerate = true">
          <template #icon><Plus :size="16" /></template>
          生成卡密
        </NButton>
      </div>
    </div>

    <NAlert v-if="cardsQuery.error.value" type="error" :bordered="false" class="padded-alert">
      {{ cardsQuery.error.value instanceof ApiError ? cardsQuery.error.value.message : '加载失败' }}
    </NAlert>

    <div v-if="!cardsQuery.isLoading.value && (cardsQuery.data.value?.length ?? 0) === 0" class="empty-state">
      <CreditCard :size="28" />
      <strong>还没有卡密</strong>
      <span>点击右上角「生成卡密」创建第一批卡密</span>
    </div>

    <NDataTable
      v-else
      :columns="columns"
      :data="cardsQuery.data.value ?? []"
      :loading="cardsQuery.isLoading.value"
      :bordered="false"
      :row-key="(row: CardKey) => row.id"
    />

    <NModal
      v-model:show="showGenerate"
      preset="card"
      title="生成卡密"
      class="form-modal"
      @after-leave="lastBatch = null"
    >
      <NForm label-placement="top">
        <NFormItem label="卡类型">
          <NRadioGroup v-model:value="generateForm.kind">
            <NRadioButton value="time">时间卡（天）</NRadioButton>
            <NRadioButton value="count">次数卡（任务次数）</NRadioButton>
          </NRadioGroup>
        </NFormItem>
        <div class="generate-grid">
          <NFormItem :label="generateForm.kind === 'time' ? '面值（天）' : '面值（次数）'">
            <NInputNumber v-model:value="generateForm.value" :min="1" style="width: 100%" />
          </NFormItem>
          <NFormItem label="数量（1-500）">
            <NInputNumber v-model:value="generateForm.count" :min="1" :max="500" style="width: 100%" />
          </NFormItem>
        </div>
        <NFormItem label="批次备注（可选）">
          <NInput v-model:value="generateForm.batch" placeholder="例如：2026秋季学期" />
        </NFormItem>
        <div class="modal-actions">
          <NButton @click="showGenerate = false">关闭</NButton>
          <NButton type="primary" :loading="generateMutation.isPending.value" @click="submitGenerate">
            生成
          </NButton>
        </div>
      </NForm>
    </NModal>

    <NModal
      v-model:show="showResult"
      preset="card"
      title="卡密已生成（明文仅显示这一次）"
      class="form-modal wide"
      @after-leave="lastBatch = null"
    >
      <NAlert type="warning" :bordered="false" style="margin-bottom: 12px">
        请立即复制并妥善保存。服务器只存哈希，关闭后无法再次查看明文。
      </NAlert>
      <div class="code-list">
        <code v-for="item in lastBatch?.items ?? []" :key="item.code">{{ item.code }}</code>
      </div>
      <div class="modal-actions">
        <NButton @click="copyBatch">
          <template #icon><Copy :size="15" /></template>
          复制全部
        </NButton>
        <NButton type="primary" @click="lastBatch = null">完成</NButton>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.padded-alert {
  margin: 0 16px;
}

.generate-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.form-modal.wide {
  width: min(560px, calc(100vw - 32px));
}

.code-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.08);
}

.code-list code {
  font-family: var(--font-mono, monospace);
  font-size: 13px;
  letter-spacing: 0.5px;
}
</style>
