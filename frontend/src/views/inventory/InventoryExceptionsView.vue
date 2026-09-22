<template>
  <div class="page-stack">
    <PageSection title="库存异常" description="登记置货现场的成品、包装报损或额外包装；经理、董事长任一人审批后，系统自动生成出库流水并扣减库存。">
      <template #actions>
        <el-button v-if="canCreate" type="primary" @click="openCreate">登记库存异常</el-button>
        <el-button @click="load">刷新</el-button>
      </template>

      <el-tabs v-model="activeStatus" @tab-change="load">
        <el-tab-pane label="待审批" name="pending_approval" />
        <el-tab-pane label="已审批出库" name="approved" />
        <el-tab-pane label="已驳回" name="rejected" />
      </el-tabs>
      <el-table :data="rows" stripe>
        <el-table-column prop="exception_no" label="异常单号" width="160" />
        <el-table-column prop="exception_type_label" label="异常类型" width="130" />
        <el-table-column prop="warehouse_name" label="仓库" width="130" />
        <el-table-column prop="sales_order_no" label="关联客户订单" width="165"><template #default="{ row }">{{ row.sales_order_no || '-' }}</template></el-table-column>
        <el-table-column label="异常明细" min-width="220"><template #default="{ row }">{{ itemText(row.items) }}</template></el-table-column>
        <el-table-column prop="remark" label="原因/说明" min-width="220" show-overflow-tooltip />
        <el-table-column prop="created_by_name" label="提交人" width="110" />
        <el-table-column label="提交时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canApprove && row.status === 'pending_approval'" type="success" size="small" @click="decide(row, 'approve')">同意并出库</el-button>
            <el-button v-if="canApprove && row.status === 'pending_approval'" type="danger" plain size="small" @click="decide(row, 'reject')">驳回</el-button>
            <span v-else>{{ row.approved_by_name || row.rejected_by_name || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="createVisible" title="登记库存异常" width="860px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="异常类型" required>
          <el-select v-model="form.exception_type" style="width: 100%" @change="resetItems">
            <el-option label="成品报损" value="product_damage" />
            <el-option label="包装报损" value="packaging_damage" />
            <el-option label="追加包装" value="extra_packaging" />
            <el-option label="包装调整" value="packaging_adjustment" />
          </el-select>
        </el-form-item>
        <el-form-item label="仓库" required>
          <el-select v-model="form.warehouse_id" filterable style="width: 100%"><el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" /></el-select>
        </el-form-item>
        <el-form-item label="关联订单">
          <el-input-number v-model="form.sales_order_id" :min="1" controls-position="right" placeholder="可选：客户订单ID" />
          <span class="form-tip">如报损的是某订单已置货的货物，可填写订单 ID；否则留空。</span>
        </el-form-item>
        <el-form-item label="异常说明" required><el-input v-model="form.remark" type="textarea" :rows="3" maxlength="2000" show-word-limit /></el-form-item>
        <el-form-item label="异常明细" required>
          <div class="items-box">
            <div v-for="(item, index) in form.items" :key="index" class="item-row">
              <el-select v-if="isProduct" v-model="item.product_id" filterable placeholder="选择成品" style="width: 330px"><el-option v-for="product in products" :key="product.id" :label="`${product.product_code} - ${product.name}`" :value="product.id" /></el-select>
              <el-select v-else v-model="item.packaging_config_id" filterable placeholder="选择包装" style="width: 330px"><el-option v-for="packaging in packagings" :key="packaging.id" :label="packaging.packaging_name" :value="packaging.id" /></el-select>
              <el-input-number v-model="item.quantity" :min="0.0001" :precision="4" controls-position="right" class="quantity" />
              <el-button type="danger" text @click="removeItem(index)">删除</el-button>
            </div>
            <el-button type="primary" plain size="small" @click="addItem">新增明细</el-button>
          </div>
        </el-form-item>
        <div class="dialog-tip">提交后不会立即扣库存；经理或董事长任一人同意后，才会生成库存出库流水。审批通过后，请回到置货/发货列表继续处理订单。</div>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="submitting" @click="submit">提交审核</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'
import { get, post } from '../../api/client'
import PageSection from '../../components/PageSection.vue'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime } from '../../utils/format'

const auth = useAuthStore()
const rows = ref([]); const warehouses = ref([]); const products = ref([]); const packagings = ref([])
const activeStatus = ref('pending_approval'); const createVisible = ref(false); const submitting = ref(false)
const form = reactive({ exception_type: 'product_damage', warehouse_id: null, sales_order_id: null, remark: '', items: [] })
const role = computed(() => auth.user?.role_code || '')
const canCreate = computed(() => ['inventory', 'system_admin'].includes(role.value))
const canApprove = computed(() => ['manager', 'chairman', 'system_admin'].includes(role.value))
const isProduct = computed(() => form.exception_type === 'product_damage')
function blankItem () { return { product_id: null, packaging_config_id: null, quantity: 1 } }
function resetItems () { form.items = [blankItem()] }
function addItem () { form.items.push(blankItem()) }
function removeItem (index) { if (form.items.length > 1) form.items.splice(index, 1) }
function itemText (items = []) { return items.map((item) => `${item.item_name} × ${item.quantity}`).join('；') }
async function load () { try { rows.value = await get(`/api/inventory/exceptions?status=${activeStatus.value}`) } catch (error) { ElMessage.error(error.message || '加载库存异常失败') } }
async function loadOptions () { const [warehouseRows, productRows, packagingRows] = await Promise.all([get('/api/master-data/warehouses'), get('/api/master-data/products?active_only=true'), get('/api/master-data/packaging-configs')]); warehouses.value = warehouseRows || []; products.value = productRows || []; packagings.value = (packagingRows || []).filter((item) => item.entity_type === 'global' && item.is_active !== false) }
function openCreate () { Object.assign(form, { exception_type: 'product_damage', warehouse_id: warehouses.value[0]?.id || null, sales_order_id: null, remark: '', items: [blankItem()] }); createVisible.value = true }
async function submit () { if (!form.warehouse_id || !form.remark.trim()) return ElMessage.warning('请填写仓库和异常说明'); if (form.items.some((item) => !(isProduct.value ? item.product_id : item.packaging_config_id) || Number(item.quantity) <= 0)) return ElMessage.warning('请完善异常明细'); submitting.value = true; try { await post('/api/inventory/exceptions', { exception_type: form.exception_type, warehouse_id: form.warehouse_id, sales_order_id: form.sales_order_id || null, remark: form.remark, items: form.items.map((item) => ({ item_type: isProduct.value ? 'product' : 'packaging', product_id: isProduct.value ? item.product_id : null, packaging_config_id: isProduct.value ? null : item.packaging_config_id, quantity: item.quantity })) }); ElMessage.success('已提交库存异常审核'); createVisible.value = false; activeStatus.value = 'pending_approval'; await load() } catch (error) { ElMessage.error(error.message || '提交库存异常失败') } finally { submitting.value = false } }
async function decide (row, action) { try { const { value } = await ElMessageBox.prompt(action === 'approve' ? '确认同意并生成出库流水？' : '请输入驳回原因', action === 'approve' ? '审批库存异常' : '驳回库存异常', { inputPlaceholder: '审批说明（可选）', inputValidator: (value) => action === 'reject' && !String(value || '').trim() ? '驳回时请填写原因' : true }); await post(`/api/inventory/exceptions/${row.id}/${action}`, { note: value || null }); ElMessage.success(action === 'approve' ? '已审批并生成出库流水' : '已驳回'); await load() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '操作失败') } }
onMounted(async () => { await Promise.all([load(), loadOptions()]) })
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.items-box { width: 100%; display: flex; flex-direction: column; gap: 10px; }
.item-row { display: flex; align-items: center; gap: 10px; }
.quantity { width: 160px; }
.form-tip, .dialog-tip { color: #909399; font-size: 13px; margin-left: 10px; }
.dialog-tip { margin: 0 18px; padding: 10px; background: #f4f4f5; }
</style>
