<template>
  <div class="print-admin">
    <PageSection title="打印设备认证码" description="认证码仅在创建时显示一次；停用后设备立即无法领取或下载置货单、发货单。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="createVisible = true">生成认证码</el-button>
      </template>
      <el-table :data="agents" stripe>
        <el-table-column prop="name" label="设备名称" min-width="180" />
        <el-table-column prop="id" label="设备编号" width="100" />
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="生成时间" width="190"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="120"><template #default="scope"><el-button v-if="scope.row.is_active" type="danger" text @click="revoke(scope.row)">停用</el-button></template></el-table-column>
      </el-table>
    </PageSection>

    <PageSection title="自动打印任务" description="已完成记录保留三天；待确认、失败和待重试任务继续保留，避免漏单。">
      <el-table :data="jobs" stripe>
        <el-table-column prop="id" label="任务编号" width="110" />
        <el-table-column prop="order_id" label="订单 ID" width="110" />
        <el-table-column label="单据类型" width="110"><template #default="scope">{{ scope.row.document_kind === 'shipment' ? '发货单' : '置货单' }}</template></el-table-column>
        <el-table-column label="状态" width="150"><template #default="scope"><el-tag :type="printJobStatus(scope.row.status).type">{{ printJobStatus(scope.row.status).label }}</el-tag></template></el-table-column>
        <el-table-column prop="attempts" label="尝试次数" width="100" />
        <el-table-column prop="last_error" label="错误" min-width="260" show-overflow-tooltip />
        <el-table-column label="创建时间" width="190"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="140"><template #default="scope"><el-button v-if="['failed', 'submitted'].includes(scope.row.status)" text type="primary" @click="retry(scope.row)">{{ scope.row.status === 'submitted' ? '重新打印' : '重试' }}</el-button></template></el-table-column>
      </el-table>
    </PageSection>

    <PageSection title="设备接口调用记录" description="记录设备、来源 IP、调用时间和结果；三天后自动清理。IP 取服务器实际连接地址。">
      <el-table :data="logs" stripe>
        <el-table-column label="调用时间" width="185"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column prop="agent_name" label="设备" width="150"><template #default="scope">{{ scope.row.agent_name || '未知设备' }}</template></el-table-column>
        <el-table-column prop="ip_address" label="来源 IP" width="150" />
        <el-table-column prop="result" label="认证结果" width="110"><template #default="scope"><el-tag :type="scope.row.result === 'authorized' ? 'success' : 'danger'">{{ scope.row.result === 'authorized' ? '通过' : '拒绝' }}</el-tag></template></el-table-column>
        <el-table-column prop="path" label="接口" min-width="260" show-overflow-tooltip />
        <el-table-column prop="user_agent" label="设备信息" min-width="180" show-overflow-tooltip />
        <el-table-column prop="token_fingerprint" label="认证码指纹" width="135" />
      </el-table>
    </PageSection>

    <PageSection title="仓库扫码设备" description="扫描枪使用键盘模式；认证码只保存在可信仓库电脑中。每台电脑必须绑定一个仓库和一名库管。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openScanDeviceDialog">生成扫码认证码</el-button>
      </template>
      <el-table :data="scanDevices" stripe>
        <el-table-column prop="name" label="设备名称" min-width="170" />
        <el-table-column prop="warehouse_name" label="绑定仓库" min-width="150" />
        <el-table-column prop="operator_name" label="绑定库管" min-width="140" />
        <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="最后在线" width="190"><template #default="scope">{{ scope.row.last_seen_at ? formatDateTime(scope.row.last_seen_at) : '从未连接' }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="scope"><el-button v-if="scope.row.is_active" type="danger" text @click="revokeScanDevice(scope.row)">停用</el-button></template></el-table-column>
      </el-table>
    </PageSection>

    <PageSection title="扫码业务记录" description="置货、发货处理结果保留三天；正式出入库与发货凭证独立留存。">
      <el-table :data="scanEvents" stripe>
        <el-table-column label="时间" width="185"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column prop="action" label="动作" width="100"><template #default="scope">{{ scope.row.action === 'placement' ? '整单置货' : '整单发货' }}</template></el-table-column>
        <el-table-column prop="result" label="结果" width="100"><template #default="scope"><el-tag :type="scope.row.result === 'success' ? 'success' : 'danger'">{{ scope.row.result === 'success' ? '成功' : '拒绝' }}</el-tag></template></el-table-column>
        <el-table-column prop="message" label="处理结果" min-width="300" show-overflow-tooltip />
        <el-table-column prop="order_id" label="订单 ID" width="110" />
      </el-table>
    </PageSection>

    <PageSection title="扫码接口安全日志" description="保留设备、IP、调用路径和认证结果，三天后自动删除。">
      <el-table :data="scanLogs" stripe>
        <el-table-column label="调用时间" width="185"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column prop="device_name" label="设备" width="150"><template #default="scope">{{ scope.row.device_name || `未知设备 #${scope.row.device_id || '-'}` }}</template></el-table-column>
        <el-table-column prop="ip_address" label="来源 IP" width="150" />
        <el-table-column prop="result" label="认证" width="100"><template #default="scope"><el-tag :type="scope.row.result === 'authorized' ? 'success' : 'danger'">{{ scope.row.result === 'authorized' ? '通过' : '拒绝' }}</el-tag></template></el-table-column>
        <el-table-column prop="path" label="接口" min-width="260" show-overflow-tooltip />
        <el-table-column prop="token_fingerprint" label="认证码指纹" width="135" />
      </el-table>
    </PageSection>

    <el-dialog v-model="createVisible" title="生成设备认证码" width="460px">
      <el-input v-model="newName" maxlength="100" placeholder="例如：成品仓打印机电脑" />
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" @click="createAgent">生成</el-button></template>
    </el-dialog>
    <el-dialog v-model="tokenVisible" title="请立即保存认证码" width="650px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" title="关闭后无法再次查看；请只配置到可信的打印或仓库扫码电脑。" />
      <el-input :model-value="newToken" readonly class="token-field" />
      <template #footer><el-button type="primary" @click="copyToken">复制认证码</el-button><el-button @click="tokenVisible = false; newToken = ''">已保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="scanDeviceVisible" title="生成仓库扫码设备认证码" width="520px">
      <el-form label-width="90px">
        <el-form-item label="设备名称"><el-input v-model="scanDeviceForm.name" maxlength="100" placeholder="例如：沧州仓库扫码电脑" /></el-form-item>
        <el-form-item label="绑定仓库"><el-select v-model="scanDeviceForm.warehouse_id" style="width: 100%"><el-option v-for="row in warehouses" :key="row.id" :label="row.name" :value="row.id" /></el-select></el-form-item>
        <el-form-item label="绑定库管"><el-select v-model="scanDeviceForm.operator_user_id" filterable style="width: 100%"><el-option v-for="row in inventoryUsers" :key="row.id" :label="`${row.full_name}（${row.username}）`" :value="row.id" /></el-select></el-form-item>
      </el-form>
      <el-alert type="warning" :closable="false" title="该电脑将以所选库管身份执行整单置货/发货，只能处理绑定仓库的作业单。" />
      <template #footer><el-button @click="scanDeviceVisible = false">取消</el-button><el-button type="primary" @click="createScanDevice">生成</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'
import { get, post } from '../../api/client'
import PageSection from '../../components/PageSection.vue'
import { formatDateTime } from '../../utils/format'

const agents = ref([])
const jobs = ref([])
const logs = ref([])
const createVisible = ref(false)
const tokenVisible = ref(false)
const newName = ref('')
const newToken = ref('')
const scanDevices = ref([])
const scanEvents = ref([])
const scanLogs = ref([])
const warehouses = ref([])
const users = ref([])
const scanDeviceVisible = ref(false)
const scanDeviceForm = reactive({ name: '', warehouse_id: null, operator_user_id: null })
const inventoryUsers = computed(() => users.value.filter((row) => ['inventory', 'inventory_super'].includes(row.role_code)))

function printJobStatus(status) {
  return {
    pending: { label: '待打印', type: 'info' },
    leased: { label: '打印处理中', type: 'warning' },
    submitted: { label: '已提交待确认', type: 'warning' },
    printed: { label: '队列已完成', type: 'success' },
    manual_printed: { label: '已手动打印', type: 'success' },
    failed: { label: '打印异常', type: 'danger' }
  }[status] || { label: status || '-', type: 'info' }
}

async function load() {
  try {
    const [agentRows, jobRows, logRows, deviceRows, eventRows, scanLogRows, warehouseRows, userRows] = await Promise.all([
      get('/api/printing/agents', { force: true }),
      get('/api/printing/jobs', { force: true }),
      get('/api/printing/access-logs', { force: true }),
      get('/api/warehouse-scanning/devices', { force: true }),
      get('/api/warehouse-scanning/events', { force: true }),
      get('/api/warehouse-scanning/access-logs', { force: true }),
      get('/api/master-data/warehouses', { force: true }),
      get('/api/admin/users/options', { force: true })
    ])
    agents.value = agentRows || []
    jobs.value = jobRows || []
    logs.value = logRows || []
    scanDevices.value = deviceRows || []
    scanEvents.value = eventRows || []
    scanLogs.value = scanLogRows || []
    warehouses.value = warehouseRows || []
    users.value = userRows || []
  } catch (error) { ElMessage.error(error.message || '加载打印设备失败') }
}
async function createAgent() {
  if (newName.value.trim().length < 2) { ElMessage.warning('请输入设备名称'); return }
  try {
    const result = await post('/api/printing/agents', { name: newName.value.trim() })
    newToken.value = result.token
    createVisible.value = false
    tokenVisible.value = true
    newName.value = ''
    await load()
  } catch (error) { ElMessage.error(error.message || '生成认证码失败') }
}
async function revoke(row) {
  await ElMessageBox.confirm(`确认停用 ${row.name} 的认证码吗？`, '停用设备', { type: 'warning' })
  try { await post(`/api/printing/agents/${row.id}/revoke`, {}); ElMessage.success('已停用'); await load() }
  catch (error) { ElMessage.error(error.message || '停用失败') }
}
async function retry(row) {
  if (row.status === 'submitted') {
    try {
      await ElMessageBox.confirm('仅在确认没有实际出纸时重新打印，否则可能产生重复单据。是否继续？', '重新打印', { type: 'warning' })
    } catch { return }
  }
  try { await post(`/api/printing/jobs/${row.id}/retry`, {}); ElMessage.success('已重新排队'); await load() }
  catch (error) { ElMessage.error(error.message || '重试失败') }
}
async function copyToken() {
  try { await navigator.clipboard.writeText(newToken.value); ElMessage.success('已复制') }
  catch { ElMessage.warning('复制失败，请手动复制') }
}
function openScanDeviceDialog() {
  scanDeviceForm.name = ''
  scanDeviceForm.warehouse_id = warehouses.value[0]?.id || null
  scanDeviceForm.operator_user_id = inventoryUsers.value[0]?.id || null
  scanDeviceVisible.value = true
}
async function createScanDevice() {
  if (scanDeviceForm.name.trim().length < 2 || !scanDeviceForm.warehouse_id || !scanDeviceForm.operator_user_id) {
    ElMessage.warning('请完整填写设备名称、仓库和库管'); return
  }
  try {
    const result = await post('/api/warehouse-scanning/devices', {
      name: scanDeviceForm.name.trim(),
      warehouse_id: scanDeviceForm.warehouse_id,
      operator_user_id: scanDeviceForm.operator_user_id
    })
    newToken.value = result.token
    scanDeviceVisible.value = false
    tokenVisible.value = true
    await load()
  } catch (error) { ElMessage.error(error.message || '生成扫码设备认证码失败') }
}
async function revokeScanDevice(row) {
  await ElMessageBox.confirm(`确认停用 ${row.name} 的扫码认证码吗？`, '停用设备', { type: 'warning' })
  try { await post(`/api/warehouse-scanning/devices/${row.id}/revoke`, {}); ElMessage.success('已停用'); await load() }
  catch (error) { ElMessage.error(error.message || '停用失败') }
}
onMounted(load)
</script>

<style scoped>
.print-admin { display: grid; gap: 16px; }
.token-field { margin-top: 18px; }
</style>
