<template>
  <PageSection title="产品基本信息" description="维护销售、生产、库存共用的产品档案和默认包装配置。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canManage" type="primary" @click="openCreate">新增产品</el-button>
    </template>

    <el-form inline @submit.prevent="search">
      <el-form-item label="搜索产品">
        <el-input v-model="keyword" clearable placeholder="产品编码 / 名称 / 规格" style="width: 300px; max-width: 100%" @clear="search" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" native-type="submit">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="pagedRows" stripe>
      <el-table-column prop="product_code" label="产品编码" width="140" />
      <el-table-column prop="name" label="产品名称" min-width="180" />
      <el-table-column prop="specification" label="规格" width="120" />
      <el-table-column label="包装名称" min-width="120">
        <template #default="scope">{{ scope.row.packaging?.packaging_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="kg/袋" width="100">
        <template #default="scope">{{ scope.row.packaging?.kg_per_bag || '-' }}</template>
      </el-table-column>
      <el-table-column label="成品仓可用库存" width="150">
        <template #default="scope">{{ formatNumber(scope.row.stock?.available_quantity) }}</template>
      </el-table-column>
      <el-table-column prop="price" label="标准单价" width="120" />
      <el-table-column prop="fulfillment_mode_label" label="产品类型" width="120" />
      <el-table-column prop="safety_stock_qty" label="安全库存" width="120" />
      <el-table-column prop="purchase_threshold_qty" label="采购阈值" width="120" />
      <el-table-column prop="is_active" label="状态" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="scope">
          <el-button v-if="canManage" size="small" @click="openEdit(scope.row)">编辑</el-button>
          <el-button v-if="canManage" size="small" type="danger" plain @click="handleDelete(scope.row)">删除</el-button>
          <span v-else>-</span>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
      @change="handleProductPaginationChange"
    />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑产品' : '新增产品'" width="720px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="产品编码"><el-input v-model="form.product_code" :disabled="!editingId" :placeholder="editingId ? '' : '保存时自动生成（CP 开头）'" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="产品名称"><el-input v-model="form.name" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="包装名称">
              <el-select v-model="form.packaging_name" filterable style="width: 100%" @change="applyPackagingSelection">
                <el-option v-for="item in packagingOptions" :key="item.id" :label="item.packaging_name" :value="item.packaging_name" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="kg/袋"><el-input-number v-model="form.kg_per_bag" :min="0.01" :precision="2" style="width: 100%" disabled /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="标准单价"><el-input-number v-model="form.price" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="产品类型">
              <el-select v-model="form.fulfillment_mode" style="width: 100%">
                <el-option label="生产型" value="production" />
                <el-option label="贸易型" value="trade" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="安全库存"><el-input-number v-model="form.safety_stock_qty" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="采购阈值"><el-input-number v-model="form.purchase_threshold_qty" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">提交</el-button>
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
import 'element-plus/es/components/select/style/css'
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
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
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
import { formatNumber } from '../../utils/format'

const auth = useAuthStore()
const rows = ref([])
const keyword = ref('')
const appliedKeyword = ref('')
const packagingOptions = ref([])
const dialogVisible = ref(false)
const editingId = ref(null)
const canManage = computed(() => ['production', 'inventory', 'purchase', 'finance', 'finance_super', 'manager', 'master_data_admin', 'system_admin'].includes(auth.user?.role_code))
const pagination = reactive({ page: 1, pageSize: 10 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedRows = rows
const form = reactive({
  product_code: '',
  name: '',
  packaging_name: '25kg编织袋',
  kg_per_bag: 25,
  packaging_unit_price: 0,
  price: 0,
  fulfillment_mode: 'trade',
  safety_stock_qty: 0,
  purchase_threshold_qty: 0,
  is_active: true
})

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    product_code: '',
    name: '',
    packaging_name: '25kg编织袋',
    kg_per_bag: 25,
    packaging_unit_price: 0,
    price: 0,
    fulfillment_mode: 'trade',
    safety_stock_qty: 0,
    purchase_threshold_qty: 0,
    is_active: true
  })
  applyDefaultPackaging()
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    product_code: row.product_code,
    name: row.name,
    packaging_name: row.packaging?.packaging_name || '25kg编织袋',
    kg_per_bag: Number(row.packaging?.kg_per_bag || 25),
    packaging_unit_price: Number(row.packaging?.unit_price || 0),
    price: row.price,
    fulfillment_mode: row.fulfillment_mode || 'trade',
    safety_stock_qty: row.safety_stock_qty,
    purchase_threshold_qty: row.purchase_threshold_qty,
    is_active: row.is_active
  })
  applyPackagingSelection()
  dialogVisible.value = true
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
  const [productRows, packagingRows] = await Promise.all([
    get(`/api/master-data/products?page=${pagination.page}&page_size=${pagination.pageSize}&keyword=${encodeURIComponent(appliedKeyword.value)}`),
    get('/api/master-data/packaging-configs?entity_type=global&active_only=true')
  ])
  rows.value = productRows?.items || []
  total.value = Number(productRows?.pagination?.total || 0)
  packagingOptions.value = packagingRows || []
}

async function handleProductPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load()
}

function findPackagingByName(packagingName) {
  return packagingOptions.value.find((item) => item.packaging_name === packagingName) || null
}

function applyDefaultPackaging() {
  const option = packagingOptions.value.find((item) => item.is_default) || packagingOptions.value[0]
  if (!option) return
  form.packaging_name = option.packaging_name
  form.kg_per_bag = Number(option.kg_per_bag || 25)
  form.packaging_unit_price = Number(option.unit_price || 0)
}

function applyPackagingSelection() {
  const option = findPackagingByName(form.packaging_name)
  if (option) {
    form.kg_per_bag = Number(option.kg_per_bag || 25)
    form.packaging_unit_price = Number(option.unit_price || 0)
    return
  }
  const sameWeightOption = packagingOptions.value.find((item) => Number(item.kg_per_bag || 0) === Number(form.kg_per_bag || 0))
  if (sameWeightOption) {
    form.packaging_name = sameWeightOption.packaging_name
    form.kg_per_bag = Number(sameWeightOption.kg_per_bag || 25)
    form.packaging_unit_price = Number(sameWeightOption.unit_price || 0)
  }
}

async function savePackaging(entityId) {
  await post('/api/master-data/packaging-configs', {
    entity_type: 'product',
    entity_id: entityId,
    packaging_name: form.packaging_name,
    kg_per_bag: form.kg_per_bag,
    unit_price: form.packaging_unit_price,
    is_active: true
  })
}

async function submit() {
  const payload = {
    name: form.name,
    specification: `${form.kg_per_bag} kg/袋`,
    unit: 'kg',
    price: form.price,
    fulfillment_mode: form.fulfillment_mode,
    safety_stock_qty: form.safety_stock_qty,
    purchase_threshold_qty: form.purchase_threshold_qty,
    is_active: form.is_active
  }
  if (editingId.value) {
    payload.product_code = form.product_code
    await put(`/api/master-data/products/${editingId.value}`, payload)
    await savePackaging(editingId.value)
    ElMessage.success('产品已更新')
  } else {
    const result = await post('/api/master-data/products', payload)
    await savePackaging(result.id)
    ElMessage.success('产品已新增')
  }
  dialogVisible.value = false
  resetForm()
  await load()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除产品 ${row.name} 吗？若已有关联业务，将自动停用。`, '提示', { type: 'warning' })
  const result = await remove(`/api/master-data/products/${row.id}`)
  ElMessage.success(result?.disabled ? '产品已停用' : '产品已删除')
  await load()
}

function exportTable() {
  exportRowsToCsv('products', rows.value)
}

onMounted(load)
</script>
