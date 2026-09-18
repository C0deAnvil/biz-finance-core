<template>
  <el-dialog :model-value="modelValue" :title="`${supplier?.name || '供应商'} · 收款账户`" width="900px" @update:model-value="$emit('update:modelValue', $event)">
    <p>在此维护供应商收款账户，采购申请付款时直接选择。账号变更请新增账户，旧账户可停用并保留历史记录。</p>
    <el-table :data="accounts" stripe>
      <el-table-column prop="account_name" label="开户名称" min-width="180" />
      <el-table-column prop="bank_name" label="开户行" min-width="180" />
      <el-table-column prop="account_number" label="账号" min-width="180" />
      <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template></el-table-column>
      <el-table-column label="操作" width="100"><template #default="{ row }"><el-button text @click="toggle(row)">{{ row.is_active ? '停用' : '启用' }}</el-button></template></el-table-column>
    </el-table>
    <el-form label-width="100px" class="account-form">
      <el-form-item label="开户名称" required><el-input v-model="form.account_name" maxlength="150" /></el-form-item>
      <el-form-item label="开户行" required><el-input v-model="form.bank_name" maxlength="150" /></el-form-item>
      <el-form-item label="账号" required><el-input v-model="form.account_number" maxlength="80" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" maxlength="500" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="$emit('update:modelValue', false)">关闭</el-button><el-button type="primary" :loading="saving" @click="save">新增账户</el-button></template>
  </el-dialog>
</template>
<script setup>
import { reactive, ref, watch } from 'vue'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/message/style/css'
import { get, post, put } from '../api/client'
const props = defineProps({ modelValue: Boolean, supplier: Object })
const emit = defineEmits(['update:modelValue', 'changed'])
const accounts = ref([]), saving = ref(false)
const form = reactive({ account_name: '', bank_name: '', account_number: '', remark: '' })
async function load() { accounts.value = await get(`/api/finance/accounts?owner_type=supplier&supplier_id=${props.supplier.id}&active_only=false`, { force: true }) }
watch(() => [props.modelValue, props.supplier?.id], async () => {
  if (!props.modelValue || !props.supplier?.id) return
  accounts.value = []
  Object.assign(form, { account_name: props.supplier.name, bank_name: '', account_number: '', remark: '' })
  try { await load() } catch (error) { ElMessage.error(error.message) }
})
async function save() {
  saving.value = true
  try {
    await post('/api/finance/accounts', { ...form, owner_type: 'supplier', supplier_id: props.supplier.id, currency: 'CNY' })
    form.account_number = ''; form.bank_name = ''; form.remark = ''
    await load(); emit('changed'); ElMessage.success('供应商账户已新增')
  } catch (error) { ElMessage.error(error.message) }
  finally { saving.value = false }
}
async function toggle(row) {
  try { await put(`/api/finance/accounts/${row.id}`, { is_active: !row.is_active }); await load(); emit('changed') }
  catch (error) { ElMessage.error(error.message) }
}
</script>
<style scoped>.account-form { margin-top: 20px; } p { color: #606266; }</style>
