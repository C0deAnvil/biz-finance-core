<template>
  <PageSection title="收付款账户" description="参考余额 = 系统内已确认收款 − 已登记付款，允许为负数，不代表银行实际余额，不限制付款。供应商账户也可在供应商基本信息中维护。">
    <template #actions><el-button @click="load">刷新</el-button><el-button type="primary" plain @click="transferVisible = true">账户转账</el-button><el-button type="primary" @click="openCreate">新增账户</el-button></template>
    <el-form inline>
      <el-form-item label="账户类型"><el-select v-model="ownerType" style="width: 160px"><el-option label="全部" value="" /><el-option v-for="(label, key) in types" :key="key" :label="label" :value="key" /></el-select></el-form-item>
      <el-form-item label="搜索"><el-input v-model="keyword" clearable placeholder="往来单位 / 户名 / 开户行 / 账号" style="width: 300px" /></el-form-item>
    </el-form>
    <el-table :data="filtered" stripe>
      <el-table-column label="类型" width="100"><template #default="{ row }">{{ types[row.owner_type] }}</template></el-table-column>
      <el-table-column prop="owner_name" label="所属单位" min-width="170" />
      <el-table-column prop="account_name" label="开户名称" min-width="180" />
      <el-table-column prop="bank_name" label="开户银行" min-width="180" />
      <el-table-column prop="account_number" label="账号" min-width="210" />
      <el-table-column prop="currency" label="币种" width="80" />
      <el-table-column label="参考余额" width="140"><template #default="{ row }">{{ row.owner_type === 'company' ? formatMoney(row.balance || 0) : '-' }}</template></el-table-column>
      <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template></el-table-column>
      <el-table-column label="操作" width="310"><template #default="{ row }"><el-button text type="primary" @click="openEdit(row)">编辑</el-button><el-button v-if="row.owner_type === 'company'" text type="primary" @click="openAdjustment(row)">余额调整</el-button><el-button text type="primary" @click="toggle(row)">{{ row.is_active ? '停用' : '启用' }}</el-button><el-button text type="danger" @click="deleteAccount(row)">删除</el-button></template></el-table-column>
    </el-table>
    <el-dialog v-model="visible" :title="form.editing ? '编辑收付款账户' : '新增收付款账户'" width="640px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="账户类型"><el-select v-model="form.owner_type" @change="form.customer_id = null; form.supplier_id = null"><el-option v-for="(label, key) in types" :key="key" :label="label" :value="key" /></el-select></el-form-item>
        <el-form-item v-if="form.owner_type === 'customer'" label="客户" required><el-select v-model="form.customer_id" filterable remote :remote-method="searchCustomers" placeholder="输入客户名称搜索" style="width: 100%"><el-option v-for="item in customers" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
        <el-form-item v-if="form.owner_type === 'supplier'" label="供应商" required><el-select v-model="form.supplier_id" filterable style="width: 100%"><el-option v-for="item in suppliers" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
        <el-form-item label="开户名称" required><el-input v-model="form.account_name" maxlength="150" /></el-form-item>
        <el-form-item label="开户银行" required><el-input v-model="form.bank_name" maxlength="150" placeholder="银行名称及支行" /></el-form-item>
        <el-form-item label="账号" required><el-input v-model="form.account_number" maxlength="80" /></el-form-item>
        <el-form-item label="币种"><el-input v-model="form.currency" maxlength="3" placeholder="CNY" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" maxlength="500" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="adjustVisible" title="调整账户参考余额" width="480px" destroy-on-close>
      <el-form label-width="100px"><el-form-item label="账户">{{ adjustmentRow?.account_name }} / {{ adjustmentRow?.account_number }}</el-form-item><el-form-item label="调整金额" required><el-input-number v-model="adjustment.amount" :precision="4" controls-position="right" style="width: 100%" /></el-form-item><el-form-item label="说明" required><el-input v-model="adjustment.remark" type="textarea" maxlength="500" /></el-form-item></el-form>
      <template #footer><el-button @click="adjustVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveAdjustment">保存调整</el-button></template>
    </el-dialog>
    <el-dialog v-model="transferVisible" title="公司账户之间转账" width="520px" destroy-on-close>
      <el-form label-width="100px"><el-form-item label="转出账户"><el-select v-model="transfer.from_account_id" style="width: 100%"><el-option v-for="item in companyRows" :key="item.id" :value="item.id" :label="item.label" /></el-select></el-form-item><el-form-item label="转入账户"><el-select v-model="transfer.to_account_id" style="width: 100%"><el-option v-for="item in companyRows" :key="item.id" :value="item.id" :label="item.label" /></el-select></el-form-item><el-form-item label="金额"><el-input-number v-model="transfer.amount" :min="0.0001" :precision="4" style="width: 100%" /></el-form-item><el-form-item label="说明"><el-input v-model="transfer.remark" maxlength="500" /></el-form-item></el-form>
      <template #footer><el-button @click="transferVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveTransfer">确认转账</el-button></template>
    </el-dialog>
  </PageSection>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import PageSection from '../../components/PageSection.vue'
import { get, post, put, patch, remove } from '../../api/client'
import { formatMoney } from '../../utils/format'
const types = { company: '企业账户', customer: '客户账户', supplier: '供应商账户' }
const rows = ref([]), customers = ref([]), suppliers = ref([]), keyword = ref(''), ownerType = ref('company'), visible = ref(false), adjustVisible = ref(false), transferVisible = ref(false), saving = ref(false), adjustmentRow = ref(null)
const form = reactive({})
const adjustment = reactive({ amount: 0, remark: '' })
const transfer = reactive({ from_account_id: null, to_account_id: null, amount: 0, remark: '' })
const companyRows = computed(() => rows.value.filter(row => row.owner_type === 'company' && row.is_active))
const filtered = computed(() => rows.value.filter(a => (!ownerType.value || a.owner_type === ownerType.value) && [a.owner_name, a.account_name, a.bank_name, a.account_number].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase())))
async function load() {
  try {
    rows.value = await get('/api/finance/accounts?active_only=false', { force: true })
  } catch (error) {
    rows.value = []
    ElMessage.error(error.message || '账户加载失败，请确认服务端已执行数据库迁移')
  }
}
async function searchCustomers(keyword = '') {
  const data = await get(`/api/master-data/customers?page=1&page_size=100&keyword=${encodeURIComponent(keyword)}`)
  customers.value = data.items || data
}
async function openCreate() {
  Object.assign(form, { id: null, editing: false, owner_type: 'company', customer_id: null, supplier_id: null, account_name: '', bank_name: '', account_number: '', currency: 'CNY', remark: '' })
  visible.value = true
  try {
    await searchCustomers()
    const data = await get('/api/master-data/suppliers')
    suppliers.value = data.items || data
  } catch (error) { ElMessage.error(error.message) }
}
function openEdit(row) { Object.assign(form, row, { editing: true }); visible.value = true }
async function save() {
  saving.value = true
  try { if (form.editing) await patch(`/api/finance/accounts/${form.id}`, form); else await post('/api/finance/accounts', form); visible.value = false; ElMessage.success(form.editing ? '账户已修改' : '账户已新增'); await load() }
  catch (error) { ElMessage.error(error.message) }
  finally { saving.value = false }
}
function openAdjustment(row) { adjustmentRow.value = row; adjustment.amount = 0; adjustment.remark = ''; adjustVisible.value = true }
async function saveAdjustment() { if (!adjustment.amount || !adjustment.remark.trim()) { ElMessage.warning('请填写非零金额和说明'); return }; saving.value = true; try { await post(`/api/finance/accounts/${adjustmentRow.value.id}/adjustments`, adjustment); adjustVisible.value = false; ElMessage.success('余额调整已记录'); await load() } catch (error) { ElMessage.error(error.message) } finally { saving.value = false } }
async function saveTransfer() { if (!transfer.from_account_id || !transfer.to_account_id || transfer.from_account_id === transfer.to_account_id || !transfer.amount || !transfer.remark.trim()) { ElMessage.warning('请完整填写转账信息，且转出转入账户不能相同'); return }; saving.value = true; try { await post('/api/finance/accounts/transfers', transfer); transferVisible.value = false; ElMessage.success('转账已记录'); await load() } catch (error) { ElMessage.error(error.message) } finally { saving.value = false } }
async function toggle(row) {
  try { await put(`/api/finance/accounts/${row.id}`, { is_active: !row.is_active }); await load() }
  catch (error) { ElMessage.error(error.message) }
}
async function deleteAccount(row) {
  try {
    await ElMessageBox.confirm(`确认删除账户“${row.account_name} / ${row.account_number}”吗？已有业务引用的账户只能停用。`, '删除账户', { type: 'warning' })
    await remove(`/api/finance/accounts/${row.id}`)
    ElMessage.success('账户已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '删除失败')
  }
}
onMounted(load)
</script>
