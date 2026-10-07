<template>
  <section class="page" data-module="contract">
    <header class="page-head">
      <div>
        <h2>运维合同管理</h2>
        <p class="page-desc">围绕合同编号、签约甲方、起止日期与续签条款做登记、状态流转与对账；列表与详情状态同源，终止与续签互斥。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记运维合同</button>
        <button class="btn" type="button" @click="exportRows">导出运维合同清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
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
          <td v-for="column in columns" :key="column">
            <template v-if="column === '合同状态'">
              <span class="status-tag" :class="statusClass(row.status)">{{ row.status }}</span>
            </template>
            <template v-else-if="column === '续签条款'">
              <span v-if="renewalText(row)" :title="renewalText(row)">{{ renewalText(row) }}</span>
              <span v-else class="empty-hint">暂无续签条款，续签时需补充</span>
            </template>
            <template v-else-if="column === '合同编号'">
              <button class="link" type="button" @click="openDetail(row)">{{ row[column] }}</button>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="empty-hint">无可用动作</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的运维合同，可先登记运维合同</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条运维合同记录</span>
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>

    <section class="recon-panel">
      <header class="recon-head">
        <h3>对账待处理清单</h3>
        <p class="page-desc">由履约状态驱动：仅统计「履行中」合同，已终止 / 已到期 / 已续签不进入对账。</p>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>合同编号</th><th>合同名称</th><th>签约甲方</th><th>起止日期</th><th>合同金额</th><th>合同状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in reconRows" :key="`recon-${String(row.id)}`">
            <td>{{ row['合同编号'] }}</td>
            <td>{{ row['合同名称'] ?? '—' }}</td>
            <td>{{ row['签约甲方'] }}</td>
            <td>{{ row['起止日期'] ?? '—' }}</td>
            <td>{{ row['合同金额'] ?? '—' }}</td>
            <td><span class="status-tag active">{{ row.status }}</span></td>
          </tr>
          <tr v-if="!reconRows.length">
            <td colspan="6" class="empty-state">当前没有履行中的合同，对账清单为空</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 登记合同 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal-card">
        <h3>登记运维合同</h3>
        <p class="page-desc">合同编号、签约甲方为必填；金额、起止日期与续签条款可后补。</p>
        <label v-for="field in createFields" :key="field.prop" class="modal-field">
          <span>{{ field.label }}<em v-if="field.required">*</em></span>
          <input v-model="createForm[field.prop]" :placeholder="field.placeholder" />
        </label>
        <p v-if="createError" class="error-text">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitCreate">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 申请续签 -->
    <div v-if="renewOpen" class="modal-mask" @click.self="renewOpen = false">
      <div class="modal-card">
        <h3>申请续签：{{ renewForm.合同编号 }}</h3>
        <label class="modal-field">
          <span>续签条款<em>*</em></span>
          <textarea v-model="renewForm.续签条款" rows="4" placeholder="请填写续签期限、价格调整、通知方式等条款"></textarea>
        </label>
        <div class="modal-field">
          <span>续签附件<em>*</em></span>
          <div class="upload-row">
            <input ref="fileInput" type="file" @change="pickFile" />
            <button class="btn" type="button" :disabled="!pickedFile || uploading" @click="uploadAttachment">
              {{ uploading ? '上传中…' : '上传附件' }}
            </button>
          </div>
          <p v-if="attachmentState === 'failed'" class="error-text">
            附件上传失败，可点击「上传附件」重试一次；未上传成功不能续签。
          </p>
          <p v-else-if="attachmentState === 'uploaded'" class="ok-text">
            附件 {{ attachmentId }}（{{ pickedFile?.name }}）已上传成功
          </p>
        </div>
        <p v-if="renewError" class="error-text">{{ renewError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="renewOpen = false">取消</button>
          <button class="btn primary" type="button" :disabled="renewing" @click="submitRenew">
            {{ renewing ? '提交中…' : '提交续签' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 合同详情 -->
    <div v-if="detailRow" class="modal-mask" @click.self="detailRow = null">
      <div class="modal-card detail-card">
        <h3>合同详情：{{ detailRow['合同编号'] }}</h3>
        <dl class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd v-if="column === '合同状态'">
              <span class="status-tag" :class="statusClass(detailRow.status)">{{ detailRow.status }}</span>
            </dd>
            <dd v-else-if="column === '续签条款'">
              <template v-if="renewalText(detailRow)">{{ renewalText(detailRow) }}</template>
              <span v-else class="empty-hint">暂无续签条款（空态）：如要续签需先补充条款</span>
            </dd>
            <dd v-else>{{ detailRow[column] ?? '—' }}</dd>
          </template>
        </dl>
        <div class="detail-block">
          <h4>续签记录</h4>
          <p v-if="!detailRow.renewals?.length" class="empty-hint">暂无续签记录；同一份合同重复提交续签只落一次。</p>
          <ul v-else class="record-list">
            <li v-for="(item, idx) in detailRow.renewals" :key="idx">
              {{ item['续签日期'] }}｜{{ item['续签条款'] }}｜附件 {{ item['附件'] }}
            </li>
          </ul>
        </div>
        <div class="detail-block">
          <h4>附件</h4>
          <p v-if="!detailRow.attachments?.length" class="empty-hint">暂无附件</p>
          <ul v-else class="record-list">
            <li v-for="att in detailRow.attachments" :key="att.id">{{ att.id }}｜{{ att.filename }}</li>
          </ul>
        </div>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailRow = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Scalar = string | number | boolean | null
type Row = {
  id: number
  status: string
  [key: string]: Scalar | Attachment[] | Renewal[]
}
type DetailRow = {
  id: number
  status: string
  renewals: Renewal[]
  attachments: Attachment[]
  [key: string]: Scalar | Attachment[] | Renewal[]
}
type Attachment = { id: string; filename: string }
type Renewal = { 续签条款: string; 附件: string; 续签日期: string }

const ENDPOINT = '/api/contract'
const columns = ['合同编号', '合同名称', '签约甲方', '签约乙方', '合同金额', '起止日期', '续签条款', '合同状态']
const statuses = ['草稿中', '已签订', '履行中', '已到期', '已终止', '已续签']
const STATUSES_FINAL = ['已终止', '已续签']

const ACTIONS_BY_STATUS: Record<string, string[]> = {
  草稿中: ['确认签订', '开始履行', '终止合同', '申请续签'],
  已签订: ['开始履行', '终止合同', '申请续签'],
  履行中: ['终止合同', '申请续签'],
  已到期: ['终止合同', '申请续签'],
}

const createFields = [
  { prop: '合同编号', label: '合同编号', required: true, placeholder: '如 CONT-2026-018' },
  { prop: '合同名称', label: '合同名称', required: false, placeholder: '如 XX 电站年度运维合同' },
  { prop: '签约甲方', label: '签约甲方', required: true, placeholder: '甲方全称' },
  { prop: '签约乙方', label: '签约乙方', required: false, placeholder: '乙方全称' },
  { prop: '合同金额', label: '合同金额（万元）', required: false, placeholder: '如 36.5' },
  { prop: '起止日期', label: '起止日期', required: false, placeholder: '2026-01-01~2026-12-31' },
  { prop: '续签条款', label: '续签条款', required: false, placeholder: '可后补；续签提交时必填' },
]

const rows = ref<Row[]>([])
const reconRows = ref<Row[]>([])
const total = ref(0)
const message = ref('')
const messageOk = ref(false)
const keyword = ref('')
const statusFilter = ref('')
const statCards = ref([
  { label: '履行中合同', value: 0 },
  { label: '本月到期合同', value: 0 },
  { label: '待签订合同', value: 0 },
  { label: '对账待处理', value: 0 },
])

function flash(text: string, ok = false) {
  message.value = text
  messageOk.value = ok
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function availableActions(row: Row): string[] {
  if (STATUSES_FINAL.includes(String(row.status))) return []
  return ACTIONS_BY_STATUS[String(row.status)] ?? []
}

function renewalText(row: Row): string {
  return String(row['续签条款'] ?? '').trim()
}

function statusClass(status: unknown): string {
  return {
    履行中: 'active',
    已到期: 'expired',
    已终止: 'terminated',
    已续签: 'renewed',
    草稿中: 'draft',
    已签订: 'signed',
  }[String(status)] ?? ''
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ------------------------------------------------------------- 登记合同
const createOpen = ref(false)
const createForm = ref<Record<string, string>>({})
const createError = ref('')
const saving = ref(false)

function openCreate() {
  createForm.value = {}
  createError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  createError.value = ''
  const values: Record<string, string> = {}
  for (const field of createFields) {
    const text = (createForm.value[field.prop] ?? '').trim()
    if (text) values[field.prop] = text
  }
  saving.value = true
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 缺合同编号 / 签约甲方：服务端逐条写明原因，直接展示。
      createError.value = payload.message || '合同未保存，请检查必填项'
      return
    }
    createOpen.value = false
    flash('运维合同已登记', true)
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '合同保存失败'
  } finally {
    saving.value = false
  }
}

// ------------------------------------------------------------- 动作 / 续签
const renewOpen = ref(false)
const renewing = ref(false)
const renewError = ref('')
const renewForm = ref<{ id: number | null; 合同编号: string; 续签条款: string }>({
  id: null,
  合同编号: '',
  续签条款: '',
})
const fileInput = ref<HTMLInputElement | null>(null)
const pickedFile = ref<File | null>(null)
const uploading = ref(false)
const attachmentState = ref<'idle' | 'failed' | 'uploaded'>('idle')
const attachmentId = ref('')
let uploadAttempts = 0

function pickFile(event: Event) {
  const target = event.target as HTMLInputElement
  pickedFile.value = target.files?.[0] ?? null
  attachmentState.value = 'idle'
  attachmentId.value = ''
  uploadAttempts = 0
}

async function runAction(action: string, row: Row) {
  if (action === '申请续签') {
    openRenew(row)
    return
  }
  flash('')
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      flash(payload.message || '动作未生效', false)
      return
    }
    flash(payload.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '运维合同操作失败', false)
  }
}

function openRenew(row: Row) {
  renewForm.value = {
    id: Number(row.id),
    合同编号: String(row['合同编号']),
    续签条款: renewalText(row),
  }
  pickedFile.value = null
  attachmentState.value = 'idle'
  attachmentId.value = ''
  renewError.value = ''
  uploadAttempts = 0
  renewOpen.value = true
  if (fileInput.value) fileInput.value.value = ''
}

async function uploadAttachment() {
  if (renewForm.value.id === null || !pickedFile.value) return
  if (uploadAttempts >= 2) {
    renewError.value = '附件两次上传均失败，请稍后重新选择文件再试'
    return
  }
  uploading.value = true
  renewError.value = ''
  uploadAttempts += 1
  try {
    // fail_times=1：模拟首次失败，第二次（重试）成功，便于验证重试边界。
    const response = await request(`${ENDPOINT}/${renewForm.value.id}/attachments`, {
      method: 'POST',
      body: JSON.stringify({ values: { filename: pickedFile.value.name, fail_times: 1 } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      attachmentState.value = 'failed'
      if (uploadAttempts >= 2) renewError.value = payload.message || '附件两次上传均失败'
      return
    }
    attachmentState.value = 'uploaded'
    attachmentId.value = payload.entry.id
  } catch (error) {
    attachmentState.value = 'failed'
  } finally {
    uploading.value = false
  }
}

async function submitRenew() {
  renewError.value = ''
  if (!renewForm.value.续签条款.trim()) {
    renewError.value = '续签条款为空，无法续签：请先补充续签条款'
    return
  }
  if (attachmentState.value !== 'uploaded' || !attachmentId.value) {
    renewError.value = '续签附件尚未上传成功，请先上传（失败可重试一次），不能直接落成已续签'
    return
  }
  renewing.value = true
  try {
    const response = await request(`${ENDPOINT}/${renewForm.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          action: '申请续签',
          续签条款: renewForm.value.续签条款.trim(),
          attachment_id: attachmentId.value,
        },
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 已终止 / 已续签 / 条款为空等原因由服务端给明。
      renewError.value = payload.message || '续签未生效'
      return
    }
    renewOpen.value = false
    flash(payload.message, true)
    await reload()
  } catch (error) {
    renewError.value = error instanceof Error ? error.message : '续签提交失败'
  } finally {
    renewing.value = false
  }
}

// ------------------------------------------------------------- 详情
const detailRow = ref<DetailRow | null>(null)

async function openDetail(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    const payload = await response.json()
    if (!response.ok) throw new Error('合同详情读取失败')
    detailRow.value = payload as DetailRow
  } catch (error) {
    flash(error instanceof Error ? error.message : '合同详情读取失败', false)
  }
}

// ------------------------------------------------------------- 列表 / 看板
async function reload() {
  message.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const [listResp, reconResp, summaryResp] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/reconciliation`),
      request(`${ENDPOINT}/summary`),
    ])
    if (!listResp.ok) throw new Error('运维合同列表读取失败')
    const payload = await listResp.json()
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length

    if (reconResp.ok) {
      const recon = await reconResp.json()
      reconRows.value = (recon.items ?? []) as Row[]
    }
    if (summaryResp.ok) {
      const summary = (await summaryResp.json()) as Record<string, number>
      statCards.value = [
        { label: '履行中合同', value: summary['履行中合同'] ?? 0 },
        { label: '本月到期合同', value: summary['本月到期合同'] ?? 0 },
        { label: '待签订合同', value: summary['待签订合同'] ?? 0 },
        { label: '对账待处理', value: summary['对账待处理'] ?? 0 },
      ]
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '运维合同列表读取失败', false)
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.status-tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; border: 1px solid var(--border); color: var(--muted); background: #f8fafc; }
.status-tag.active { color: #047857; background: #ecfdf5; border-color: #a7f3d0; }
.status-tag.expired { color: #b45309; background: #fffbeb; border-color: #fde68a; }
.status-tag.terminated { color: #b42318; background: #fef2f2; border-color: #fecaca; }
.status-tag.renewed { color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }
.empty-hint { color: var(--muted); font-size: 12px; }
.ok-text { color: #047857; }
.filter-item select { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.recon-panel { margin-top: 20px; }
.recon-head h3 { margin: 0 0 4px; font-size: 15px; }
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal-card { background: #fff; border-radius: 10px; padding: 18px 20px; width: 480px; max-height: 86vh; overflow: auto; box-shadow: 0 12px 32px rgba(15, 23, 42, 0.2); }
.detail-card { width: 640px; }
.modal-card h3 { margin: 0 0 8px; }
.modal-field { display: block; margin-bottom: 10px; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.modal-field em { color: #b42318; font-style: normal; margin-left: 2px; }
.modal-field input, .modal-field textarea { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font: inherit; }
.upload-row { display: flex; gap: 8px; align-items: center; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.btn[disabled] { opacity: 0.55; cursor: not-allowed; }
.detail-grid { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; margin: 8px 0; }
.detail-grid dt { color: var(--muted); font-size: 12px; }
.detail-grid dd { margin: 0; font-size: 13px; }
.detail-block h4 { margin: 10px 0 4px; font-size: 13px; }
.record-list { margin: 0; padding-left: 18px; font-size: 13px; }
</style>
