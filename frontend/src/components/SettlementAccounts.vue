<template>
  <el-form-item v-if="showCompany" :label="direction === 'receipt' ? '企业收款账户' : '企业付款账户'" required>
    <el-select :model-value="companyAccountId" :disabled="companyReadonly" filterable clearable style="width: 100%" placeholder="请选择企业账户" @update:model-value="$emit('update:companyAccountId', $event || null)">
      <el-option v-for="item in companyAccounts" :key="item.id" :value="item.id" :label="item.balance === undefined ? item.label : `${item.label} / 参考余额 ${formatMoney(item.balance)}`" />
    </el-select>
    <span v-if="companyReadonly" class="hint">申请人已选择收款账户，请核对该账户的到账情况</span>
    <span v-if="!companyAccounts.length" class="hint">请联系财务在“收付款账户”中维护企业账户</span>
  </el-form-item>
  <el-form-item :label="direction === 'receipt' ? '客户付款账户' : '供应商收款账户'" :required="counterpartyRequired">
    <el-select :model-value="counterpartyAccountId" filterable clearable style="width: 100%" :placeholder="counterpartyRequired ? '请选择供应商收款账户' : '可选，选择已维护的往来账户'" :disabled="!partyId || counterpartyReadonly" @update:model-value="$emit('update:counterpartyAccountId', $event || null)">
      <el-option v-for="item in partyAccounts" :key="item.id" :value="item.id" :label="item.label" />
    </el-select>
    <span v-if="direction === 'payment' && partyId && !partyAccounts.length" class="hint">请在供应商基本信息的“账户”中维护该供应商的收款账户</span>
  </el-form-item>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/message/style/css'
import { get } from '../api/client'
import { formatMoney } from '../utils/format'

const props = defineProps({ companyAccountId: Number, counterpartyAccountId: Number, partyId: Number, direction: { type: String, default: 'receipt' }, showCompany: { type: Boolean, default: true }, companyReadonly: Boolean, counterpartyReadonly: Boolean, counterpartyRequired: Boolean })
const emit = defineEmits(['update:companyAccountId', 'update:counterpartyAccountId'])
const accounts = ref([])
const companyAccounts = computed(() => accounts.value.filter(a => a.owner_type === 'company' && (a.is_active || a.id === props.companyAccountId)))
const partyAccounts = computed(() => accounts.value.filter(a => (a.is_active || a.id === props.counterpartyAccountId) && (props.direction === 'receipt'
  ? a.owner_type === 'customer' && a.customer_id === props.partyId
  : a.owner_type === 'supplier' && a.supplier_id === props.partyId)))
watch(() => props.partyId, () => {
  if (!props.counterpartyReadonly && props.counterpartyAccountId && !partyAccounts.value.some(a => a.id === props.counterpartyAccountId)) emit('update:counterpartyAccountId', null)
})
onMounted(async () => {
  try { accounts.value = await get('/api/finance/accounts?active_only=false', { force: true }) }
  catch (error) { ElMessage.error(error.message || '账户加载失败') }
})
</script>
<style scoped>.hint { color: #909399; font-size: 12px; }</style>
