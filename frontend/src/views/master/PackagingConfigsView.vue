<template>
  <PageSection title="包装基本信息" description="维护全局可选包装类型、每袋含量及采购/销售成本；“无包装”不占用库存。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canManage" type="primary" @click="openCreate">新增包装</el-button>
    </template>

    <el-form inline @submit.prevent="search">
      <el-form-item label="搜索包装">
        <el-input v-model="keyword" clearable placeholder="包装名称，如25kg编织袋" style="width: 300px; max-width: 100%" @clear="search" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" native-type="submit">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="pagedRows" stripe>
      <el-table-column prop="packaging_name" label="包装名称" min-width="180" />
      <el-table-column prop="kg_per_bag" label="kg/袋" width="110">
        <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
      </el-table-column>
      <el-table-column prop="purchase_cost" label="采购成本" width="120">
        <template #default="scope">{{ formatNumber(scope.row.purchase_cost) }}</template>
      </el-table-column>
      <el-table-column prop="sales_cost" label="销售成本" width="120">
        <template #default="scope">{{ formatNumber(scope.row.sales_cost) }}</template>
      </el-table-column>
      <el-table-column prop="is_default" label="默认" width="90">
        <template #default="scope">
          <el-tag :type="scope.row.is_default ? 'success' : 'info'">{{ scope.row.is_default ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.is_active ? 'success' : 'danger'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="160">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="scope">
          <el-button v-if="canManage" size="small" @click="openEdit(scope.row)">编辑</el-button>
          <el-button v-if="canManage" size="small" type="danger" plain @click="handleDelete(scope.row)">停用</el-button>
        </template>
      </el-table-column>
    </el-table>

    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑包装' : '新增包装'" width="720px">
      <el-form :model="form" label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="包装名称">
              <el-input v-model="form.packaging_name" placeholder="例如：25kg编织袋" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="kg/袋">
              <el-input-number v-model="form.kg_per_bag" :min="0.01" :precision="2" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="采购成本">
              <el-input-number v-model="form.purchase_cost" :min="0" :precision="4" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="销售成本"><el-input-number v-model="form.sales_cost" :min="0" :precision="4" style="width:100%" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="默认包装">
              <el-switch v-model="form.is_default" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-switch v-model="form.is_active" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post, put, remove } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber } from '../../utils/format'

const auth = useAuthStore()
const canManage = computed(() => ['sales', 'purchase', 'production', 'inventory', 'finance', 'finance_super', 'manager', 'chairman', 'system_admin', 'master_data_admin'].includes(auth.user?.role_code))

const rows = ref([])
const keyword = ref('')
const appliedKeyword = ref('')
const dialogVisible = ref(false)
const editingId = ref(null)
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(rows)

const form = reactive({
  packaging_name: '',
  kg_per_bag: 25,
  unit_price: 0,
  purchase_cost: 0,
  sales_cost: 0,
  is_default: true,
  is_active: true
})

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    packaging_name: '',
    kg_per_bag: 25,
    unit_price: 0,
    purchase_cost: 0,
    sales_cost: 0,
    is_default: true,
    is_active: true
  })
}

async function search() {
  appliedKeyword.value = keyword.value.trim()
  pagination.page = 1
  await load()
}

async function resetSearch() {
  keyword.value = ''
  await search()
}

async function load() {
  rows.value = await get(`/api/master-data/packaging-configs?entity_type=global&keyword=${encodeURIComponent(appliedKeyword.value)}`)
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    packaging_name: row.packaging_name,
    kg_per_bag: Number(row.kg_per_bag || 0),
    unit_price: Number(row.unit_price || 0),
    purchase_cost: Number(row.purchase_cost ?? row.unit_price ?? 0),
    sales_cost: Number(row.sales_cost || 0),
    is_default: Boolean(row.is_default),
    is_active: Boolean(row.is_active)
  })
  dialogVisible.value = true
}

async function submit() {
  if (!String(form.packaging_name || '').trim()) {
    ElMessage.warning('请填写包装名称')
    return
  }
  const payload = {
    entity_type: 'global',
    entity_id: 0,
    packaging_name: String(form.packaging_name || '').trim(),
    kg_per_bag: Number(form.kg_per_bag || 0),
    unit_price: Number(form.unit_price || 0),
    purchase_cost: Number(form.purchase_cost || 0),
    sales_cost: Number(form.sales_cost || 0),
    is_default: Boolean(form.is_default),
    is_active: Boolean(form.is_active)
  }
  if (editingId.value) {
    await put(`/api/master-data/packaging-configs/${editingId.value}`, payload)
    ElMessage.success('包装已更新')
  } else {
    await post('/api/master-data/packaging-configs', payload)
    ElMessage.success('包装已新增')
  }
  dialogVisible.value = false
  resetForm()
  await load()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认停用包装 ${row.packaging_name} 吗？`, '提示', { type: 'warning' })
  await remove(`/api/master-data/packaging-configs/${row.id}`)
  ElMessage.success('包装已停用')
  await load()
}

function exportTable() {
  exportRowsToCsv('packaging-configs', rows.value)
}

onMounted(load)
</script>
