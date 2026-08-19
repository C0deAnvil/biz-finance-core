<template>
  <PageSection title="用户管理" description="系统管理员可创建、编辑、停用账号，并设置数据继承来源与停用后的数据承接人。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canManageUsers" type="primary" @click="openCreate">新增用户</el-button>
    </template>

    <el-table :data="pagedRows" stripe>
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="full_name" label="姓名" width="120" />
      <el-table-column prop="job_title" label="岗位" width="140" />
      <el-table-column prop="department_path" label="组织路径" min-width="220" show-overflow-tooltip />
      <el-table-column prop="role" label="功能角色" width="140" />
      <el-table-column prop="data_scope" label="数据范围" width="120">
        <template #default="scope">{{ dataScopeLabel(scope.row.data_scope) }}</template>
      </el-table-column>
      <el-table-column label="继承来源" min-width="140">
        <template #default="scope">{{ userNameById(scope.row.inherited_from_user_id) }}</template>
      </el-table-column>
      <el-table-column label="数据承接人" min-width="140">
        <template #default="scope">{{ userNameById(scope.row.data_successor_user_id) }}</template>
      </el-table-column>
      <el-table-column prop="phone" label="电话" width="150" />
      <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
      <el-table-column prop="last_login_at" label="最近登录" min-width="180">
        <template #default="scope">{{ formatDateTime(scope.row.last_login_at) }}</template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="canManageUsers" label="操作" width="300" fixed="right">
        <template #default="scope">
          <el-button size="small" @click="openEdit(scope.row)">编辑</el-button>
          <el-button size="small" @click="openResetPassword(scope.row)">重置密码</el-button>
          <el-button
            size="small"
            :type="scope.row.is_active ? 'danger' : 'success'"
            plain
            @click="toggleStatus(scope.row)"
          >
            {{ scope.row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新增用户' : '编辑用户'" width="720px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="用户名">
          <el-input v-model="form.username" :disabled="dialogMode === 'edit'" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="姓名"><el-input v-model="form.full_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="岗位"><el-input v-model="form.job_title" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="组织单元">
          <el-select v-model="form.department_id" filterable style="width: 100%">
            <el-option v-for="item in orgUnits" :key="item.id" :label="item.path_name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="功能角色">
              <el-select v-model="form.role_id" style="width: 100%">
                <el-option v-for="item in roles" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="数据范围">
              <el-select v-model="form.data_scope" style="width: 100%">
                <el-option v-for="item in DATA_SCOPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="电话"><el-input v-model="form.phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="继承谁的数据">
          <el-select v-model="form.inherit_from_user_id" clearable filterable style="width: 100%">
            <el-option v-for="item in inheritanceCandidates" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-alert
          v-if="dialogMode === 'create'"
          title="新用户默认密码为 ChangeMe123!；如选择继承来源，系统会将来源用户的业务归属同步到新用户。"
          type="info"
          :closable="false"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button v-if="canManageUsers" type="primary" @click="submit">{{ dialogMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetVisible" title="重置密码" width="460px">
      <el-form :model="resetForm" label-width="90px">
        <el-form-item label="用户">
          <el-input :model-value="selectedUser?.full_name || ''" disabled />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="resetForm.new_password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button v-if="canManageUsers" type="primary" @click="submitResetPassword">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="successorVisible" title="停用用户并转交数据" width="560px">
      <el-form :model="successorForm" label-width="110px">
        <el-form-item label="停用用户">
          <el-input :model-value="selectedUser?.full_name || ''" disabled />
        </el-form-item>
        <el-form-item label="数据承接人">
          <el-select v-model="successorForm.successor_user_id" filterable style="width: 100%">
            <el-option
              v-for="item in successorCandidates"
              :key="item.id"
              :label="`${item.full_name} / ${item.username}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-alert
          title="停用时系统会保留历史数据，并将销售单、采购单、生产任务、补货需求、付款申请、回款申请、退换货与未完成待办统一转交给承接人。"
          type="warning"
          :closable="false"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="successorVisible = false">取消</el-button>
        <el-button type="danger" @click="confirmDeactivate">确认停用并转交</el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post, put } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime } from '../../utils/format'

const DATA_SCOPE_OPTIONS = [
  { value: 'self', label: '仅本人' },
  { value: 'team', label: '本小组' },
  { value: 'sub_department', label: '本子部门' },
  { value: 'department', label: '本部门' },
  { value: 'all', label: '全部数据' }
]

const auth = useAuthStore()
const canManageUsers = computed(() => auth.user?.role_code === 'system_admin')

const rows = ref([])
const orgUnits = ref([])
const roles = ref([])
const dialogVisible = ref(false)
const dialogMode = ref('create')
const resetVisible = ref(false)
const successorVisible = ref(false)
const selectedUser = ref(null)
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(rows)

const form = reactive({
  id: null,
  username: '',
  full_name: '',
  department_id: null,
  role_id: null,
  job_title: '',
  data_scope: 'self',
  phone: '',
  email: '',
  inherit_from_user_id: null
})

const resetForm = reactive({
  new_password: 'ChangeMe123!'
})

const successorForm = reactive({
  successor_user_id: null
})

const inheritanceCandidates = computed(() =>
  rows.value.filter((item) => item.is_active && item.id !== form.id)
)

const successorCandidates = computed(() =>
  rows.value.filter((item) => item.is_active && item.id !== selectedUser.value?.id)
)

function userNameById(userId) {
  if (!userId) return '-'
  return rows.value.find((item) => item.id === userId)?.full_name || `用户#${userId}`
}

function dataScopeLabel(value) {
  return DATA_SCOPE_OPTIONS.find((item) => item.value === value)?.label || value || '-'
}

function resetCreateForm() {
  form.id = null
  form.username = ''
  form.full_name = ''
  form.department_id = null
  form.role_id = null
  form.job_title = ''
  form.data_scope = 'self'
  form.phone = ''
  form.email = ''
  form.inherit_from_user_id = null
}

function openCreate() {
  dialogMode.value = 'create'
  resetCreateForm()
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  form.id = row.id
  form.username = row.username
  form.full_name = row.full_name
  form.department_id = row.department_id
  form.role_id = row.role_id
  form.job_title = row.job_title || ''
  form.data_scope = row.data_scope || 'self'
  form.phone = row.phone || ''
  form.email = row.email || ''
  form.inherit_from_user_id = row.inherited_from_user_id || null
  dialogVisible.value = true
}

async function load() {
  rows.value = await get('/api/admin/users')
}

async function loadMeta() {
  ;[orgUnits.value, roles.value] = await Promise.all([
    get('/api/admin/org-units?flat=true'),
    get('/api/master-data/roles')
  ])
}

async function submit() {
  const payload = {
    username: form.username,
    full_name: form.full_name,
    department_id: form.department_id,
    role_id: form.role_id,
    job_title: form.job_title,
    data_scope: form.data_scope,
    phone: form.phone,
    email: form.email,
    inherit_from_user_id: form.inherit_from_user_id || null
  }
  if (dialogMode.value === 'create') {
    const result = await post('/api/admin/users', payload)
    ElMessage.success(`用户已创建，默认密码：${result.default_password}`)
  } else {
    await put(`/api/admin/users/${form.id}`, payload)
    ElMessage.success('用户信息已更新')
  }
  dialogVisible.value = false
  resetCreateForm()
  await load()
}

function openResetPassword(row) {
  selectedUser.value = row
  resetForm.new_password = 'ChangeMe123!'
  resetVisible.value = true
}

async function submitResetPassword() {
  if (!selectedUser.value) return
  await post(`/api/admin/users/${selectedUser.value.id}/reset-password`, resetForm)
  ElMessage.success('密码已重置')
  resetVisible.value = false
}

async function toggleStatus(row) {
  if (!row.is_active) {
    await ElMessageBox.confirm(`确认启用用户 ${row.full_name} 吗？`, '提示', { type: 'warning' })
    await post(`/api/admin/users/${row.id}/toggle`, {})
    ElMessage.success('用户已启用')
    await load()
    return
  }
  selectedUser.value = row
  successorForm.successor_user_id = null
  successorVisible.value = true
}

async function confirmDeactivate() {
  if (!selectedUser.value) return
  if (!successorForm.successor_user_id) {
    ElMessage.warning('请选择数据承接人')
    return
  }
  await post(`/api/admin/users/${selectedUser.value.id}/toggle`, {
    successor_user_id: successorForm.successor_user_id
  })
  ElMessage.success('用户已停用，数据已转交')
  successorVisible.value = false
  await load()
}

function exportTable() {
  exportRowsToCsv('admin_users', rows.value)
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>
