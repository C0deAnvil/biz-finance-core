<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">客户总数</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">星级客户</div><div class="summary-value">{{ starCustomerCount }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">已有订单客户</div><div class="summary-value">{{ activeOrderCustomerCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="客户管理">
      <template #actions>
        <el-button @click="filterDialogVisible = true">筛选</el-button>
        <el-button @click="resetFilters">重置筛选</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button @click="exportTable">导出</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增客户</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="code" label="客户编码" width="150" />
        <el-table-column prop="name" label="客户名称" min-width="180" />
        <el-table-column prop="contact_name" label="联系人" width="140" />
        <el-table-column prop="phone" label="联系电话" width="160" />
        <el-table-column prop="unified_social_credit_code" label="统一社会信用代码" min-width="190" />
        <el-table-column prop="source_type_label" label="客户来源" width="120" />
        <el-table-column prop="owner_user_name" label="所属销售" width="120" />
        <el-table-column prop="created_at" label="建档时间" width="170" />
        <el-table-column prop="star_level" label="客户星级" width="110" />
        <el-table-column prop="order_count" label="关联订单" width="100" />
        <el-table-column prop="address" label="地址" min-width="220" show-overflow-tooltip />
        <el-table-column v-if="canManage" label="操作" width="240" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openEdit(scope.row)">编辑</el-button>
            <el-button v-if="canTransfer" size="small" text type="warning" @click="openTransfer(scope.row)">转移</el-button>
            <el-button size="small" text type="danger" @click="removeCustomer(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-dialog v-model="filterDialogVisible" title="客户多维筛选" width="720px">
      <el-form :model="filters" label-width="110px">
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" clearable placeholder="客户名称 / 联系人 / 电话 / 信用代码" />
        </el-form-item>
        <el-form-item label="客户来源">
          <el-select v-model="filters.source_type" clearable style="width:100%">
            <el-option label="个人开发" value="personal_development" />
            <el-option label="公司转交" value="company_transfer" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属销售">
          <el-select v-model="filters.owner_user_id" clearable filterable style="width:100%">
            <el-option v-for="item in salesUsers" :key="item.id" :label="item.full_name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="客户星级">
          <el-input v-model="filters.star_level" clearable />
        </el-form-item>
        <el-form-item label="建档日期">
          <el-date-picker
            v-model="filters.created_range"
            type="daterange"
            unlink-panels
            range-separator="至"
            start-placeholder="建档开始"
            end-placeholder="建档结束"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetFilters">重置</el-button>
        <el-button type="primary" @click="filterDialogVisible = false">应用筛选</el-button>
        <el-button type="success" plain @click="exportTable">导出当前结果</el-button>
      </template>
    </el-dialog>

    <PageSection title="客户投诉记录" description="收录客户投诉原因、投诉人信息和处理进度，录入后同步提醒经理。">
      <template #actions>
        <el-button @click="loadComplaints">刷新投诉</el-button>
        <el-button v-if="canManage" type="warning" @click="openComplaintCreate">新增投诉</el-button>
      </template>

      <el-table :data="complaints" stripe>
        <el-table-column prop="complaint_no" label="投诉编号" width="150" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="complainant_name" label="投诉人" width="120" />
        <el-table-column prop="complainant_phone" label="联系电话" width="150" />
        <el-table-column prop="complaint_reason" label="投诉原因" min-width="200" show-overflow-tooltip />
        <el-table-column prop="status_label" label="状态" width="110">
          <template #default="scope">
            <el-tag :type="complaintStatusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="录入时间" min-width="160" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openComplaintDetail(scope.row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑客户' : '新增客户'" width="640px">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="客户名称"><el-input v-model="form.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="客户星级"><el-input v-model="form.star_level" placeholder="如：A / B / VIP" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系人"><el-input v-model="form.contact_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话"><el-input v-model="form.phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="信用代码"><el-input v-model="form.unified_social_credit_code" placeholder="统一社会信用代码" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="客户来源"><el-select v-model="form.source_type" style="width:100%"><el-option label="个人开发" value="personal_development" /><el-option label="公司转交" value="company_transfer" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="地址"><el-input v-model="form.address" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="transferDialogVisible" title="客户转移" width="560px">
      <el-form :model="transferForm" label-width="100px">
        <el-form-item label="客户">
          <el-input :model-value="activeCustomer?.name || ''" disabled />
        </el-form-item>
        <el-form-item label="当前销售">
          <el-input :model-value="activeCustomer?.owner_user_name || '-'" disabled />
        </el-form-item>
        <el-form-item label="转交销售">
          <el-select v-model="transferForm.to_user_id" filterable style="width: 100%">
            <el-option v-for="item in salesUsers" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="转移原因">
          <el-input v-model="transferForm.reason" type="textarea" :rows="3" placeholder="请填写客户转移原因，便于后续追溯" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="transferDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitTransfer">确认转移</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="complaintDialogVisible" :title="complaintMode === 'detail' ? '客户投诉详情' : '新增客户投诉'" width="760px">
      <el-form :model="complaintForm" label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="投诉客户">
              <el-select v-model="complaintForm.customer_id" filterable style="width: 100%" :disabled="complaintMode === 'detail'">
                <el-option v-for="item in rows" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="投诉人"><el-input v-model="complaintForm.complainant_name" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话"><el-input v-model="complaintForm.complainant_phone" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系地址"><el-input v-model="complaintForm.complainant_address" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="投诉原因"><el-input v-model="complaintForm.complaint_reason" :disabled="complaintMode === 'detail'" /></el-form-item>
        <el-form-item label="详细说明"><el-input v-model="complaintForm.complaint_detail" type="textarea" :rows="4" :disabled="complaintMode === 'detail'" /></el-form-item>
        <el-form-item v-if="complaintMode === 'detail'" label="处理状态">
          <el-tag :type="complaintStatusType(activeComplaint?.status)">{{ activeComplaint?.status_label }}</el-tag>
        </el-form-item>
        <el-form-item v-if="complaintMode === 'detail'" label="处理结果">
          <div>{{ activeComplaint?.handled_result || '-' }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="complaintDialogVisible = false">关闭</el-button>
        <el-button v-if="complaintMode === 'create'" type="primary" @click="submitComplaint">保存投诉</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post, put, remove } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const rows = ref([])
const complaints = ref([])
const salesUsers = ref([])
const dialogVisible = ref(false)
const complaintDialogVisible = ref(false)
const transferDialogVisible = ref(false)
const filterDialogVisible = ref(false)
const complaintMode = ref('create')
const editingId = ref(null)
const activeComplaint = ref(null)
const activeCustomer = ref(null)
const filters = reactive({ keyword: '', source_type: '', owner_user_id: null, star_level: '', created_range: [] })
const form = reactive({ name: '', contact_name: '', phone: '', address: '', star_level: '', unified_social_credit_code: '', source_type: 'personal_development' })
const transferForm = reactive({ to_user_id: null, reason: '' })
const complaintForm = reactive({
  customer_id: null,
  complainant_name: '',
  complainant_phone: '',
  complainant_address: '',
  complaint_reason: '',
  complaint_detail: ''
})
const canManage = computed(() => ['sales', 'manager', 'chairman', 'master_data_admin', 'system_admin'].includes(auth.user?.role_code) && pagePermission.can('create'))
const canTransfer = computed(() => ['manager', 'chairman', 'master_data_admin', 'system_admin'].includes(auth.user?.role_code))

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  const starLevel = filters.star_level.trim().toLowerCase()
  const [startDate, endDate] = filters.created_range || []
  return rows.value.filter((item) => {
    if (keyword && ![item.name, item.contact_name, item.phone, item.code, item.unified_social_credit_code, item.address].filter(Boolean).some((value) => String(value).toLowerCase().includes(keyword))) {
      return false
    }
    if (filters.source_type && item.source_type !== filters.source_type) return false
    if (filters.owner_user_id && item.owner_user_id !== filters.owner_user_id) return false
    if (starLevel && !String(item.star_level || '').toLowerCase().includes(starLevel)) return false
    const createdDate = item.created_at ? String(item.created_at).slice(0, 10) : ''
    if (startDate && createdDate < startDate) return false
    if (endDate && createdDate > endDate) return false
    return true
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(filteredRows)
const starCustomerCount = computed(() => filteredRows.value.filter((item) => item.star_level).length)
const activeOrderCustomerCount = computed(() => filteredRows.value.filter((item) => Number(item.order_count || 0) > 0).length)

function resetForm() {
  Object.assign(form, { name: '', contact_name: '', phone: '', address: '', star_level: '', unified_social_credit_code: '', source_type: 'personal_development' })
  editingId.value = null
}

function resetFilters() {
  Object.assign(filters, { keyword: '', source_type: '', owner_user_id: null, star_level: '', created_range: [] })
}

function resetComplaintForm() {
  Object.assign(complaintForm, {
    customer_id: null,
    complainant_name: '',
    complainant_phone: '',
    complainant_address: '',
    complaint_reason: '',
    complaint_detail: ''
  })
  activeComplaint.value = null
}

async function load() {
  rows.value = await get('/api/master-data/customers')
}

async function loadUsers() {
  const users = await get('/api/admin/users/options')
  salesUsers.value = users.filter((item) => item.role_code === 'sales')
}

async function loadComplaints() {
  complaints.value = await get('/api/master-data/customer-complaints')
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    contact_name: row.contact_name,
    phone: row.phone,
    address: row.address,
    star_level: row.star_level || '',
    unified_social_credit_code: row.unified_social_credit_code || '',
    source_type: row.source_type || 'personal_development'
  })
  dialogVisible.value = true
}

async function submit() {
  if (editingId.value) {
    await put(`/api/master-data/customers/${editingId.value}`, form)
    ElMessage.success('客户已更新')
  } else {
    await post('/api/master-data/customers', form)
    ElMessage.success('客户已新增')
  }
  dialogVisible.value = false
  resetForm()
  await load()
}

async function removeCustomer(row) {
  await ElMessageBox.confirm(`确认删除客户“${row.name}”吗？`, '删除确认', { type: 'warning' })
  await remove(`/api/master-data/customers/${row.id}`)
  ElMessage.success('客户已删除')
  await load()
}

function openTransfer(row) {
  activeCustomer.value = row
  transferForm.to_user_id = row.owner_user_id || null
  transferForm.reason = ''
  transferDialogVisible.value = true
}

async function submitTransfer() {
  if (!activeCustomer.value) return
  if (!transferForm.to_user_id) {
    ElMessage.warning('请选择转交销售')
    return
  }
  await post(`/api/master-data/customers/${activeCustomer.value.id}/transfer`, {
    to_user_id: transferForm.to_user_id,
    reason: transferForm.reason.trim()
  })
  ElMessage.success('客户已转移')
  transferDialogVisible.value = false
  activeCustomer.value = null
  await load()
}

function complaintStatusType(status) {
  return ({ pending: 'warning', processing: 'primary', resolved: 'success', closed: 'info' })[status] || 'info'
}

function openComplaintCreate() {
  complaintMode.value = 'create'
  resetComplaintForm()
  complaintDialogVisible.value = true
}

function openComplaintDetail(row) {
  complaintMode.value = 'detail'
  activeComplaint.value = row
  Object.assign(complaintForm, {
    customer_id: row.customer_id,
    complainant_name: row.complainant_name,
    complainant_phone: row.complainant_phone,
    complainant_address: row.complainant_address || '',
    complaint_reason: row.complaint_reason,
    complaint_detail: row.complaint_detail || ''
  })
  complaintDialogVisible.value = true
}

async function submitComplaint() {
  if (!complaintForm.customer_id || !complaintForm.complainant_name.trim() || !complaintForm.complainant_phone.trim() || !complaintForm.complaint_reason.trim()) {
    ElMessage.warning('请填写投诉客户、投诉人、联系电话和投诉原因')
    return
  }
  await post('/api/master-data/customer-complaints', {
    ...complaintForm,
    complainant_name: complaintForm.complainant_name.trim(),
    complainant_phone: complaintForm.complainant_phone.trim(),
    complainant_address: complaintForm.complainant_address.trim(),
    complaint_reason: complaintForm.complaint_reason.trim(),
    complaint_detail: complaintForm.complaint_detail.trim()
  })
  ElMessage.success('客户投诉已录入')
  complaintDialogVisible.value = false
  resetComplaintForm()
  await loadComplaints()
}

function exportTable() {
  exportRowsToCsv('customers', filteredRows.value.map((item) => ({
    客户编码: item.code,
    客户名称: item.name,
    联系人: item.contact_name,
    联系电话: item.phone,
    统一社会信用代码: item.unified_social_credit_code,
    客户来源: item.source_type_label,
    所属销售: item.owner_user_name,
    建档时间: item.created_at,
    客户星级: item.star_level,
    关联订单数: item.order_count,
    地址: item.address
  })))
}

onMounted(async () => {
  await Promise.all([load(), loadComplaints(), loadUsers()])
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.w-260 { width: 260px; }
.w-160 { width: 160px; }
.w-140 { width: 140px; }
.w-120 { width: 120px; }
</style>
