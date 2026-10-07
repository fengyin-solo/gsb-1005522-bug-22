<template>
  <section class="page" data-module="contract">
    <header class="page-head">
      <div>
        <h2>运维合同管理</h2>
        <p class="page-desc">维护运维合同，围绕合同编号、合同名称、签约甲方、签约乙方做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记运维合同</button>
        <button class="btn" type="button" @click="exportRows">导出运维合同清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>合同编号</span>
        <input v-model="keyword" placeholder="按合同编号检索" />
      </label>
      <label class="filter-item">
        <span>合同状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-if="isTerminal(row)">
              <span class="terminal-hint">已定案</span>
            </template>
            <template v-else>
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无运维合同数据，可先登记运维合同</td>
        </tr>
      </tbody>
    </table>

    <div v-if="createVisible" class="modal-mask">
      <form class="modal-card" @submit.prevent="submitCreate">
        <h3>登记运维合同</h3>
        <label v-for="field in createFields" :key="field" class="modal-item">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <p class="modal-tip">合同编号、合同名称、签约甲方为必填，缺失会写明原因并拒绝保存。</p>
        <div class="modal-actions">
          <button class="btn primary" type="submit">保存</button>
          <button class="btn ghost" type="button" @click="closeCreate">取消</button>
        </div>
      </form>
    </div>

    <div v-if="renewTarget" class="modal-mask">
      <form class="modal-card" @submit.prevent="submitRenew">
        <h3>办理续签：{{ renewTarget['合同编号'] }}</h3>
        <label class="modal-item">
          <span>续签条款</span>
          <input v-model="renewForm['续签条款']" placeholder="续签条款为空时不会落成已续签" />
        </label>
        <label class="modal-item">
          <span>附件</span>
          <input v-model="renewForm['附件']" placeholder="附件名称，上传失败可重试一次" />
        </label>
        <p class="modal-tip">同一份合同重复提交续签只落一次；已终止的合同不能再续签。</p>
        <div class="modal-actions">
          <button class="btn primary" type="submit">提交续签</button>
          <button class="btn ghost" type="button" @click="renewTarget = null">取消</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条运维合同记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ActionReply = { ok: boolean; message?: string }

const ENDPOINT = '/api/contract'
const columns = ["合同编号", "合同名称", "签约甲方", "签约乙方", "合同金额", "起止日期", "续签条款", "合同状态"]
const actions = ["确认签订", "开始履行", "办理续签", "终止合同"]
const statuses = ["草稿中", "已签订", "履行中", "已到期", "已续签", "已终止"]
const terminalStatuses = ["已续签", "已终止"]
const createFields = ["合同编号", "合同名称", "签约甲方", "签约乙方", "合同金额", "起止日期", "续签条款"]

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([{ label: '履行中合同', value: 0 }, { label: '本月到期合同', value: 0 }, { label: '待签订合同', value: 0 }])
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const noticeMessage = ref('')
const createVisible = ref(false)
const createForm = ref<Record<string, string>>({})
const renewTarget = ref<Row | null>(null)
const renewForm = ref<Record<string, string>>({})

function isTerminal(row: Row) {
  return terminalStatuses.includes(String(row['合同状态'] ?? row.status ?? ''))
}

function resetMessages() {
  errorMessage.value = ''
  noticeMessage.value = ''
}

function showReply(payload: ActionReply) {
  if (payload.ok) {
    noticeMessage.value = payload.message ?? '操作成功'
  } else {
    errorMessage.value = payload.message ?? '操作未生效，请核对后重试'
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  const suffix = query.toString() ? `?${query}` : ''
  window.open(`${ENDPOINT}/export${suffix}`, '_blank')
}

function openCreate() {
  createForm.value = {}
  createVisible.value = true
}

function closeCreate() {
  createVisible.value = false
}

async function submitCreate() {
  resetMessages()
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const payload = (await response.json()) as ActionReply
    showReply(payload)
    if (payload.ok) {
      closeCreate()
      await reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运维合同登记失败'
  }
}

function runAction(action: string, row: Row) {
  if (action === '办理续签') {
    renewForm.value = { 续签条款: String(row['续签条款'] ?? ''), 附件: '' }
    renewTarget.value = row
    return
  }
  void postAction(action, row)
}

async function submitRenew() {
  const row = renewTarget.value
  if (!row) return
  await postAction('办理续签', row, { ...renewForm.value })
  renewTarget.value = null
}

async function postAction(action: string, row: Row, extra: Record<string, string> = {}) {
  resetMessages()
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...extra } }),
    })
    const payload = (await response.json()) as ActionReply
    showReply(payload)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运维合同操作失败'
  }
}

function monthOf(dateRange: Row[string]) {
  const text = String(dateRange ?? '')
  const tail = text.split(/[~～至—]/).pop()?.trim() ?? ''
  return /^\d{4}-\d{2}/.test(tail) ? tail.slice(0, 7) : ''
}

async function reload() {
  resetMessages()
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('运维合同列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await reloadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运维合同列表读取失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) return
    const payload = await response.json()
    const summary = payload.summary ?? {}
    const month = new Date().toISOString().slice(0, 7)
    const expiring = (payload.items ?? []).filter((item: Row) => monthOf(item['起止日期']) === month).length
    stats.value = [
      { label: '履行中合同', value: summary['履行中'] ?? 0 },
      { label: '本月到期合同', value: expiring },
      { label: '待签订合同', value: summary['草稿中'] ?? 0 },
    ]
  } catch {
    // 统计卡片失败不阻断列表
  }
}

onMounted(reload)
</script>
