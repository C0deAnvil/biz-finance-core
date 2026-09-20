<template>
  <el-dialog :model-value="modelValue" :title="`${customer?.name || '客户'} · 付款账户`" width="900px" @update:model-value="$emit('update:modelValue', $event)">
    <p class="hint">客户账户不是必填项。只有发生退款或需要记录客户付款账户时再维护即可。</p>
    <el-table :data="accounts" stripe>
      <el-table-column prop="account_name" label="账户名称" min-width="170" />
      <el-table-column prop="bank_name" label="开户行" min-width="180" />
      <el-table-column prop="account_number" label="账号" min-width="220" />
      <el-table-column prop="currency" label="币种" width="80" />
      <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template></el-table-column>
      <el-table-column label="操作" width="90"><template #default="{ row }"><el-button text type="primary" @click="toggle(row)">{{ row.is_active ? '停用' : '启用' }}</el-button></template></el-table-column>
    </el-table>
    <el-divider content-position="left">新增客户付款账户</el-divider>
    <el-form :model="form" inline label-width="80px">
      <el-form-item label="账户名称" required><el-input v-model="form.account_name" /></el-form-item>
      <el-form-item label="开户行" required><el-input v-model="form.bank_name" /></el-form-item>
      <el-form-item label="账号" required><el-input v-model="form.account_number" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="$emit('update:modelValue', false)">关闭</el-button><el-button type="primary" :loading="saving" @click="save">新增账户</el-button></template>
  </el-dialog>
</template>
<script setup>
import { reactive, ref, watch } from 'vue'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { get, post, put } from '../api/client'
const props = defineProps({ modelValue: Boolean, customer: Object })
const emit = defineEmits(['update:modelValue', 'changed'])
const accounts = ref([]), saving = ref(false)
const form = reactive({ account_name: '', bank_name: '', account_number: '', remark: '' })
async function load() { if (props.customer?.id) accounts.value = await get(`/api/finance/accounts?owner_type=customer&customer_id=${props.customer.id}&active_only=false`, { force: true }) }
watch(() => [props.modelValue, props.customer?.id], ([open]) => { if (open) { Object.assign(form, { account_name: '', bank_name: '', account_number: '', remark: '' }); load() } })
async function save() { if (!form.account_name.trim() || !form.bank_name.trim() || !form.account_number.trim()) { ElMessage.warning('请填写账户名称、开户行和账号'); return }; saving.value = true; try { await post('/api/finance/accounts', { ...form, owner_type: 'customer', customer_id: props.customer.id, currency: 'CNY' }); await load(); emit('changed'); ElMessage.success('客户账户已新增') } catch (e) { ElMessage.error(e.message) } finally { saving.value = false } }
async function toggle(row) { try { await put(`/api/finance/accounts/${row.id}`, { is_active: !row.is_active }); await load(); emit('changed') } catch (e) { ElMessage.error(e.message) } }
</script>
