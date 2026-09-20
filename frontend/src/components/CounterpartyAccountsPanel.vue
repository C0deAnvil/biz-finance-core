<template>
  <section class="accounts-panel">
    <div class="panel-heading">
      <div>
        <strong>{{ title }}</strong>
        <div class="hint">{{ hint }}</div>
      </div>
      <el-button text type="primary" :loading="loading" @click="load">刷新账户</el-button>
    </div>

    <el-table :data="accounts" stripe empty-text="暂无账户信息">
      <el-table-column prop="account_name" label="开户名称" min-width="160" />
      <el-table-column prop="bank_name" label="开户行" min-width="170" />
      <el-table-column prop="account_number" label="账号" min-width="190" />
      <el-table-column prop="currency" label="币种" width="75" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag></template>
      </el-table-column>
      <el-table-column v-if="editable" label="操作" width="90">
        <template #default="{ row }"><el-button text type="primary" @click="toggle(row)">{{ row.is_active ? '停用' : '启用' }}</el-button></template>
      </el-table-column>
    </el-table>

    <template v-if="editable">
      <el-divider content-position="left">新增{{ ownerType === 'supplier' ? '收款' : '付款' }}账户</el-divider>
      <el-row :gutter="12" class="account-form">
        <el-col :span="12"><label>开户名称 <span>*</span></label><el-input v-model="form.account_name" maxlength="150" /></el-col>
        <el-col :span="12"><label>开户行 <span>*</span></label><el-input v-model="form.bank_name" maxlength="150" /></el-col>
        <el-col :span="12"><label>账号 <span>*</span></label><el-input v-model="form.account_number" maxlength="80" /></el-col>
        <el-col :span="12"><label>备注</label><el-input v-model="form.remark" maxlength="500" /></el-col>
      </el-row>
      <div class="form-actions"><el-button type="primary" plain :loading="saving" @click="save">添加账户</el-button></div>
    </template>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/message/style/css'
import { get, post, put } from '../api/client'

const props = defineProps({
  ownerType: { type: String, required: true, validator: value => ['customer', 'supplier'].includes(value) },
  ownerId: { type: Number, default: null },
  ownerName: { type: String, default: '' },
  editable: { type: Boolean, default: false }
})
const emit = defineEmits(['changed'])
const accounts = ref([])
const loading = ref(false)
const saving = ref(false)
const form = reactive({ account_name: '', bank_name: '', account_number: '', remark: '' })
const title = computed(() => props.ownerType === 'supplier' ? '供应商收款账户' : '客户付款账户')
const hint = computed(() => props.ownerType === 'supplier'
  ? '采购申请付款时从启用账户中选择；账号变更时新增账户并停用旧账户。'
  : '客户账户不是必填项，可用于记录付款来源或办理退款。')

function resetForm() {
  Object.assign(form, { account_name: props.ownerName || '', bank_name: '', account_number: '', remark: '' })
}

async function load() {
  if (!props.ownerId) { accounts.value = []; return }
  loading.value = true
  try {
    const idKey = props.ownerType === 'supplier' ? 'supplier_id' : 'customer_id'
    accounts.value = await get(`/api/finance/accounts?owner_type=${props.ownerType}&${idKey}=${props.ownerId}&active_only=false`, { force: true })
  } catch (error) {
    accounts.value = []
    ElMessage.error(error.message || '账户信息加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.account_name.trim() || !form.bank_name.trim() || !form.account_number.trim()) {
    ElMessage.warning('请填写开户名称、开户行和账号')
    return
  }
  saving.value = true
  try {
    const idKey = props.ownerType === 'supplier' ? 'supplier_id' : 'customer_id'
    await post('/api/finance/accounts', {
      owner_type: props.ownerType,
      [idKey]: props.ownerId,
      account_name: form.account_name.trim(),
      bank_name: form.bank_name.trim(),
      account_number: form.account_number.trim(),
      currency: 'CNY',
      remark: form.remark.trim() || null
    })
    resetForm()
    await load()
    emit('changed')
    ElMessage.success('账户已添加')
  } catch (error) {
    ElMessage.error(error.message || '账户添加失败')
  } finally {
    saving.value = false
  }
}

async function toggle(row) {
  try {
    await put(`/api/finance/accounts/${row.id}`, { is_active: !row.is_active })
    await load()
    emit('changed')
  } catch (error) {
    ElMessage.error(error.message || '账户状态修改失败')
  }
}

watch(() => props.ownerId, () => { resetForm(); load() }, { immediate: true })
watch(() => props.ownerName, (value, previous) => {
  if (!form.account_name || form.account_name === previous) form.account_name = value || ''
})
</script>

<style scoped>
.accounts-panel { margin-top: 8px; }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.hint { margin-top: 4px; color: #909399; font-size: 12px; font-weight: 400; }
.account-form { row-gap: 12px; margin-bottom: 12px; }
.account-form label { display: block; margin-bottom: 6px; color: #606266; font-size: 14px; }
.account-form label span { color: #f56c6c; }
.form-actions { display: flex; justify-content: flex-end; }
</style>
