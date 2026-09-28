<template>
  <div class="page-stack">
    <PageSection v-if="canViewAll" title="报销统计">
      <div class="stats-toolbar">
        <el-date-picker
          v-model="reportFilters.dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 320px"
        />
        <el-select v-model="reportFilters.applicantUserId" clearable filterable placeholder="按人员筛选" style="width: 240px">
          <el-option v-for="item in applicantOptions" :key="item.id" :label="item.label" :value="item.id" />
        </el-select>
        <el-button @click="resetReportFilters">重置</el-button>
      </div>
      <el-table :data="pagedStatsRows" row-key="applicant_user_id" stripe>
        <el-table-column prop="applicant_name" label="人员" min-width="160" />
        <el-table-column prop="reimbursement_count" label="报销笔数" width="120" />
        <el-table-column prop="reimbursement_total" label="报销总额" width="140">
          <template #default="scope">{{ formatNumber(scope.row.reimbursement_total) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="statsPagination.page"
        v-model:page-size="statsPagination.pageSize"
        :total="reimbursementStatsRows.length"
        :page-sizes="statsPageSizes"
      />
    </PageSection>

    <PageSection title="事务申请">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button type="warning" plain @click="openMatterCreate">申请事务</el-button>
        <el-button type="primary" @click="openCreate">提交报销</el-button>
      </template>
      <el-table :data="pagedMatterRows" row-key="id" stripe :expand-row-keys="expandedMatterRowKeys">
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <div class="expand-summary">
              <div><strong>事务详情：</strong>{{ scope.row.detail || '-' }}</div>
              <div><strong>申请时间：</strong>{{ formatDateTime(scope.row.occurred_at) }}</div>
              <div>
                <strong>附件：</strong>
                <el-button v-if="scope.row.attachment_id" size="small" text type="primary" @click="openAttachment(scope.row.attachment_id)">查看附件</el-button>
                <span v-else>-</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="matter_no" label="事务单号" width="170" />
        <el-table-column prop="applicant_name" label="申请人" width="120" />
        <el-table-column v-if="canViewAll" label="归属" width="90">
          <template #default="scope">{{ ownershipLabel(scope.row) }}</template>
        </el-table-column>
        <el-table-column prop="expense_category_label" label="费用大类" width="120" />
        <el-table-column prop="expense_subcategory" label="费用小类" width="140" />
        <el-table-column prop="estimated_amount" label="预估金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.estimated_amount) }}</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="140" />
        <el-table-column prop="detail" label="事务详情" min-width="240" show-overflow-tooltip />
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openAttachments(scope.row)">附件</el-button>
            <DelayedActionButton
              v-if="canManager && scope.row.status === 'pending_manager_approval'"
              size="small"
              type="success"
              @click="auditMatter(scope.row, 'approved')"
            >
              通过
            </DelayedActionButton>
            <DelayedActionButton
              v-if="canManager && scope.row.status === 'pending_manager_approval'"
              size="small"
              type="danger"
              @click="auditMatter(scope.row, 'rejected')"
            >
              驳回
            </DelayedActionButton>
            <el-button v-if="canConvertMatter(scope.row)" size="small" type="primary" text @click="openConvertMatter(scope.row)">转报销</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="matterPagination.page"
        v-model:page-size="matterPagination.pageSize"
        :total="matterTotal"
        :page-sizes="matterPageSizes"
      />
    </PageSection>

    <PageSection title="报销管理">
      <el-table :data="pagedReimbursementRows" row-key="id" stripe :expand-row-keys="expandedReimbursementRowKeys">
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <div class="expand-summary">
              <div><strong>报销事由：</strong>{{ scope.row.reason || '-' }}</div>
              <div><strong>报销时间：</strong>{{ formatDateTime(scope.row.occurred_at) }}</div>
              <div><strong>付款方式：</strong>{{ scope.row.payment_method || '-' }}</div>
              <div><strong>财务备注：</strong>{{ scope.row.finance_remark || '-' }}</div>
              <div><strong>董事长意见：</strong>{{ scope.row.chairman_remark || '-' }}</div>
              <div>
                <strong>附件：</strong>
                <el-button v-if="scope.row.attachment_id" size="small" text type="primary" @click="openAttachment(scope.row.attachment_id)">报销凭证</el-button>
                <el-button v-if="scope.row.bank_receipt_attachment_id" size="small" text type="primary" @click="openAttachment(scope.row.bank_receipt_attachment_id)">出账凭证</el-button>
                <span v-if="!scope.row.attachment_id && !scope.row.bank_receipt_attachment_id">-</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="reimbursement_no" label="报销单号" width="170" />
        <el-table-column prop="applicant_name" label="申请人" width="120" />
        <el-table-column v-if="canViewAll" label="归属" width="90">
          <template #default="scope">{{ ownershipLabel(scope.row) }}</template>
        </el-table-column>
        <el-table-column prop="expense_category_label" label="费用大类" width="120" />
        <el-table-column prop="expense_subcategory" label="费用小类" width="140" />
        <el-table-column prop="amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="occurred_at" label="报销时间" width="170">
          <template #default="scope">{{ formatDateTime(scope.row.occurred_at) }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="事由" min-width="220" show-overflow-tooltip />
        <el-table-column prop="status_label" label="状态" width="140" />
        <el-table-column prop="chairman_remark" label="董事长意见" min-width="160" show-overflow-tooltip />
        <el-table-column prop="finance_remark" label="财务备注" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openAttachments(scope.row)">附件</el-button>
            <DelayedActionButton
              v-if="canChairman && ['pending', 'pending_chairman_approval'].includes(scope.row.status)"
              size="small"
              type="success"
              @click="audit(scope.row, 'approved')"
            >
              通过
            </DelayedActionButton>
            <DelayedActionButton
              v-if="canChairman && ['pending', 'pending_chairman_approval'].includes(scope.row.status)"
              size="small"
              type="danger"
              @click="audit(scope.row, 'rejected')"
            >
              驳回
            </DelayedActionButton>
            <el-button v-if="canFinance && scope.row.status === 'approved'" size="small" type="primary" @click="openPay(scope.row)">出账</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="reimbursementPagination.page"
        v-model:page-size="reimbursementPagination.pageSize"
        :total="reimbursementTotal"
        :page-sizes="reimbursementPageSizes"
      />
    </PageSection>

    <el-dialog v-model="matterVisible" title="申请事务" width="660px">
      <el-form :model="matterForm" label-width="110px">
        <el-form-item label="费用大类">
          <el-select v-model="matterForm.expense_category" style="width:100%" @change="matterForm.expense_subcategory = ''">
            <el-option v-for="item in options.categories" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="费用小类">
          <el-select v-model="matterForm.expense_subcategory" style="width:100%">
            <el-option v-for="item in matterSubcategoryOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="预估金额"><el-input-number v-model="matterForm.estimated_amount" :min="0" :precision="4" style="width:100%" /></el-form-item>
        <el-form-item label="申请时间"><el-date-picker v-model="matterForm.occurred_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" /></el-form-item>
        <el-form-item label="事务附件"><input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="handleMatterFileChange" /></el-form-item>
        <el-form-item label="事务详情"><el-input v-model="matterForm.detail" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="matterVisible = false">取消</el-button>
        <el-button type="primary" @click="submitMatter">提交事务</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="提交报销" width="660px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="费用大类">
          <el-select v-model="form.expense_category" style="width:100%" @change="form.expense_subcategory = ''">
            <el-option v-for="item in options.categories" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="费用小类">
          <el-select v-model="form.expense_subcategory" style="width:100%">
            <el-option v-for="item in subcategoryOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="实际金额"><el-input-number v-model="form.amount" :min="0.0001" :precision="4" style="width:100%" /></el-form-item>
        <el-form-item label="发生时间"><el-date-picker v-model="form.occurred_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" /></el-form-item>
        <el-form-item label="报销凭证"><input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="handleAttachmentChange" /></el-form-item>
        <el-form-item label="报销事由"><el-input v-model="form.reason" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="指定财务">
          <el-select v-model="form.assigned_finance_user_id" clearable placeholder="不指定则系统自动分配" style="width:100%">
            <el-option v-for="u in financeAssignees" :key="u.id" :label="u.is_super ? `${u.full_name}（超级财务）` : u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="attachmentVisible" title="报销单附件" width="720px">
      <template v-if="attachmentReimbursement">
        <el-alert type="info" :closable="false" show-icon class="mb-12">
          <template #title>报销单 {{ attachmentReimbursement.reimbursement_no }}：可上传发票、凭证等附件(支持多张),财务可核验发票。</template>
        </el-alert>
        <AttachmentUploader
          :key="attachmentReimbursement.id"
          business-type="reimbursement"
          :business-id="attachmentReimbursement.id"
          :preset-types="['发票', '报销凭证', '其他']"
        />
      </template>
      <template #footer>
        <el-button @click="attachmentVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="payVisible" title="财务出账" width="620px">
      <el-form :model="payForm" label-width="110px">
        <el-form-item label="报销单号"><el-input :model-value="activeRow?.reimbursement_no || '-'" disabled /></el-form-item>
        <el-form-item label="出账金额"><el-input-number :model-value="Number(activeRow?.amount || 0)" disabled :precision="4" style="width:100%" /></el-form-item>
        <el-form-item label="出账时间"><el-date-picker v-model="payForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" /></el-form-item>
        <el-form-item label="出账方式"><el-input v-model="payForm.payment_method" placeholder="如银行转账、现金等" /></el-form-item>
        <el-form-item label="出账凭证"><input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="handlePayFileChange" /></el-form-item>
        <el-form-item label="财务备注"><el-input v-model="payForm.finance_remark" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="payVisible = false">取消</el-button>
        <DelayedActionButton type="primary" @click="submitPay">一键出账</DelayedActionButton>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/message/style/css'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElMessage } from 'element-plus'

import { computed, onMounted, reactive, ref } from 'vue'
import { get, post, upload } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import { useFinanceAssignees } from '../../composables/useFinanceAssignees'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber } from '../../utils/format'

const auth = useAuthStore()
const rows = ref([])
const matters = ref([])
const options = reactive({ categories: [] })
const reportFilters = reactive({ dateRange: [], applicantUserId: null })
const createVisible = ref(false)
const matterVisible = ref(false)
const attachmentVisible = ref(false)
const attachmentReimbursement = ref(null)

function openAttachments(row) {
  attachmentReimbursement.value = row
  attachmentVisible.value = true
}

const payVisible = ref(false)
const activeRow = ref(null)
const { financeAssignees, loadFinanceAssignees } = useFinanceAssignees()

const form = reactive({ expense_category: 'management', expense_subcategory: '', amount: 0, occurred_at: '', reason: '', business_matter_id: null, assigned_finance_user_id: null })
const matterForm = reactive({ expense_category: 'management', expense_subcategory: '', estimated_amount: 0, occurred_at: '', detail: '' })
const payForm = reactive({ paid_at: '', payment_method: '', finance_remark: '' })
const selectedAttachmentFile = ref(null)
const selectedMatterFile = ref(null)
const selectedPayFile = ref(null)
const canFinance = computed(() => ['finance', 'system_admin'].includes(auth.user?.role_code))
const canManager = computed(() => ['manager', 'system_admin'].includes(auth.user?.role_code))
const canChairman = computed(() => ['chairman', 'system_admin'].includes(auth.user?.role_code))
const canViewAll = computed(() => ['finance', 'manager', 'chairman', 'system_admin'].includes(auth.user?.role_code))
const subcategoryOptions = computed(() => options.categories.find((item) => item.value === form.expense_category)?.children || [])
const matterSubcategoryOptions = computed(() => options.categories.find((item) => item.value === matterForm.expense_category)?.children || [])

const applicantOptions = computed(() => {
  const map = new Map()
  rows.value.forEach((row) => {
    if (row.applicant_user_id && row.applicant_name) {
      map.set(row.applicant_user_id, row.applicant_name)
    }
  })
  return [...map.entries()].map(([id, label]) => ({ id, label }))
})

const filteredReimbursements = computed(() => {
  const [dateFrom, dateTo] = Array.isArray(reportFilters.dateRange) ? reportFilters.dateRange : []
  return rows.value.filter((row) => {
    if (reportFilters.applicantUserId && Number(row.applicant_user_id || 0) !== Number(reportFilters.applicantUserId)) {
      return false
    }
    const occurredDate = String(row.occurred_at || '').slice(0, 10)
    if (dateFrom && occurredDate && occurredDate < dateFrom) return false
    if (dateTo && occurredDate && occurredDate > dateTo) return false
    return true
  })
})

const reimbursementStatsRows = computed(() => {
  const statsMap = new Map()
  filteredReimbursements.value.forEach((row) => {
    const key = row.applicant_user_id || row.applicant_name || 'unknown'
    const current = statsMap.get(key) || {
      applicant_user_id: row.applicant_user_id || null,
      applicant_name: row.applicant_name || '-',
      reimbursement_count: 0,
      reimbursement_total: 0
    }
    current.reimbursement_count += 1
    current.reimbursement_total += Number(row.amount || 0)
    statsMap.set(key, current)
  })
  return [...statsMap.values()].sort((left, right) => Number(right.reimbursement_total || 0) - Number(left.reimbursement_total || 0))
})

const { pagination: statsPagination, total: statsTotal, pageSizes: statsPageSizes, pagedItems: pagedStatsRows } = useLocalPagination(reimbursementStatsRows)
const { pagination: matterPagination, total: matterTotal, pageSizes: matterPageSizes, pagedItems: pagedMatterRows } = useLocalPagination(matters)
const { pagination: reimbursementPagination, total: reimbursementTotal, pageSizes: reimbursementPageSizes, pagedItems: pagedReimbursementRows } = useLocalPagination(rows)
const expandedMatterRowKeys = computed(() => pagedMatterRows.value.filter((row) => !['rejected', 'converted'].includes(row.status)).map((row) => row.id))
const expandedReimbursementRowKeys = computed(() => pagedReimbursementRows.value.filter((row) => !['rejected', 'paid'].includes(row.status)).map((row) => row.id))

function nowValue() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

function resetReportFilters() {
  reportFilters.dateRange = []
  reportFilters.applicantUserId = null
}

async function load() {
  try {
    const [reimbursementRows, matterRows, optionData] = await Promise.all([
      get('/api/reimbursements'),
      get('/api/reimbursements/matters'),
      get('/api/reimbursements/options')
    ])
    rows.value = reimbursementRows || []
    matters.value = matterRows || []
    options.categories = optionData.categories || []
  } catch (error) {
    ElMessage.error(error.message || '加载报销数据失败')
  }
}

function openCreate() {
  Object.assign(form, { expense_category: 'management', expense_subcategory: '', amount: 0, occurred_at: nowValue(), reason: '', business_matter_id: null })
  selectedAttachmentFile.value = null
  createVisible.value = true
}

function openMatterCreate() {
  Object.assign(matterForm, { expense_category: 'management', expense_subcategory: '', estimated_amount: 0, occurred_at: nowValue(), detail: '' })
  selectedMatterFile.value = null
  matterVisible.value = true
}

function openConvertMatter(row) {
  Object.assign(form, {
    expense_category: row.expense_category,
    expense_subcategory: row.expense_subcategory,
    amount: Number(row.estimated_amount || 0),
    occurred_at: nowValue(),
    reason: row.detail || '',
    business_matter_id: row.id
  })
  selectedAttachmentFile.value = null
  createVisible.value = true
}

function isMine(row) {
  return Number(row?.applicant_user_id || 0) === Number(auth.user?.id || 0)
}

function ownershipLabel(row) {
  return isMine(row) ? '本人' : '他人'
}

function canConvertMatter(row) {
  return row?.status === 'approved' && !row.converted_reimbursement_id && (canViewAll.value || isMine(row))
}

function handleAttachmentChange(event) {
  selectedAttachmentFile.value = event.target.files?.[0] || null
}

function handleMatterFileChange(event) {
  selectedMatterFile.value = event.target.files?.[0] || null
}

function handlePayFileChange(event) {
  selectedPayFile.value = event.target.files?.[0] || null
}

async function uploadFileIfNeeded(fileRef, businessType, attachmentType, businessId = 0) {
  if (!fileRef.value) return null
  const formData = new FormData()
  formData.append('business_type', businessType)
  formData.append('business_id', String(businessId || 0))
  formData.append('attachment_type', attachmentType)
  formData.append('file', fileRef.value)
  const result = await upload('/api/files/upload', formData)
  fileRef.value = null
  return result.id
}

function openAttachment(id) {
  if (!id) return
  window.open(`/api/files/${id}`, '_blank')
}

function validateExpense(payload, amountField, detailField) {
  if (!payload.expense_category || !payload.expense_subcategory) {
    ElMessage.warning('请选择费用大类和费用小类')
    return false
  }
  if (Number(payload[amountField] || 0) <= 0 && amountField !== 'estimated_amount') {
    ElMessage.warning('请填写有效金额')
    return false
  }
  if (!String(payload[detailField] || '').trim()) {
    ElMessage.warning('请填写事由或事务详情')
    return false
  }
  return true
}

async function submitMatter() {
  if (!validateExpense(matterForm, 'estimated_amount', 'detail')) return
  try {
    const attachmentId = await uploadFileIfNeeded(selectedMatterFile, 'business_matter', '事务附件')
    await post('/api/reimbursements/matters', { ...matterForm, attachment_id: attachmentId })
    ElMessage.success('事务申请已提交')
    matterVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '事务申请提交失败')
  }
}

async function submit() {
  if (!validateExpense(form, 'amount', 'reason')) return
  try {
    const attachmentId = await uploadFileIfNeeded(selectedAttachmentFile, 'reimbursement', '报销凭证')
    if (form.business_matter_id) {
      await post(`/api/reimbursements/matters/${form.business_matter_id}/convert-reimbursement`, {
        actual_amount: form.amount,
        occurred_at: form.occurred_at,
        reason: form.reason,
        attachment_id: attachmentId,
        assigned_finance_user_id: form.assigned_finance_user_id
      })
    } else {
      await post('/api/reimbursements', { ...form, attachment_id: attachmentId })
    }
    ElMessage.success('报销申请已提交')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '报销申请提交失败')
  }
}

async function auditMatter(row, decision) {
  try {
    await post(`/api/reimbursements/matters/${row.id}/audit`, { decision, manager_remark: '' })
    ElMessage.success('事务审批已处理')
    await load()
  } catch (error) {
    ElMessage.error(error.message || '事务审批失败')
  }
}

async function audit(row, decision) {
  try {
    await post(`/api/reimbursements/${row.id}/audit`, { decision, finance_remark: '' })
    ElMessage.success('报销审批已处理')
    await load()
  } catch (error) {
    ElMessage.error(error.message || '报销审批失败')
  }
}

function openPay(row) {
  activeRow.value = row
  Object.assign(payForm, { paid_at: nowValue(), payment_method: '', finance_remark: row.finance_remark || '' })
  selectedPayFile.value = null
  payVisible.value = true
}

async function submitPay() {
  if (!activeRow.value) return
  try {
    const attachmentId = await uploadFileIfNeeded(selectedPayFile, 'reimbursement', '出账凭证', activeRow.value.id)
    if (!attachmentId && !activeRow.value.bank_receipt_attachment_id) {
      ElMessage.warning('请先上传出账凭证')
      return
    }
    await post(`/api/reimbursements/${activeRow.value.id}/pay`, {
      paid_at: payForm.paid_at,
      payment_method: payForm.payment_method || null,
      finance_remark: payForm.finance_remark,
      bank_receipt_attachment_id: attachmentId || activeRow.value.bank_receipt_attachment_id
    })
    ElMessage.success('报销已出账')
    payVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '报销出账失败')
  }
}

onMounted(async () => {
  await Promise.all([load(), loadFinanceAssignees()])
})
</script>

<style scoped>
.page-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.expand-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  line-height: 1.6;
  color: #303133;
}

.stats-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
</style>
