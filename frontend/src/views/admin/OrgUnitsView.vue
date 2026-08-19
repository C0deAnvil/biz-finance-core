<template>
  <PageSection title="组织架构" description="系统管理员可维护部门、子部门和小组，并定义仓库作用域。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="openCreate()">新增顶级组织</el-button>
    </template>

    <el-table :data="pagedRows" row-key="id" default-expand-all stripe :tree-props="{ children: 'children' }">
      <el-table-column prop="name" label="组织名称" min-width="220" />
      <el-table-column prop="code" label="编码" width="160" />
      <el-table-column prop="unit_type" label="层级类型" width="120">
        <template #default="scope">{{ unitTypeLabel(scope.row.unit_type) }}</template>
      </el-table-column>
      <el-table-column prop="path_name" label="完整路径" min-width="240" show-overflow-tooltip />
      <el-table-column prop="warehouse_scope" label="仓库作用域" width="140">
        <template #default="scope">{{ warehouseScopeLabel(scope.row.warehouse_scope) }}</template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="90" />
      <el-table-column prop="is_active" label="状态" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="scope">
          <el-button size="small" @click="openCreate(scope.row)">新增下级</el-button>
          <el-button size="small" @click="openEdit(scope.row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="removeUnit(scope.row)">停用/删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新增组织单元' : '编辑组织单元'" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="组织编码">
          <el-input v-model="form.code" :disabled="dialogMode === 'edit'" />
        </el-form-item>
        <el-form-item label="组织名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="上级组织">
          <el-select v-model="form.parent_id" clearable filterable style="width: 100%">
            <el-option v-for="item in flatRows" :key="item.id" :label="item.path_name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="层级类型">
              <el-select v-model="form.unit_type" style="width: 100%">
                <el-option label="部门" value="department" />
                <el-option label="子部门" value="sub_department" />
                <el-option label="小组" value="team" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="排序">
              <el-input v-model.number="form.sort_order" type="number" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="仓库作用域">
          <el-select v-model="form.warehouse_scope" clearable style="width: 100%">
            <el-option label="无" value="" />
            <el-option label="成品仓" value="finished" />
            <el-option label="原料仓" value="raw" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">{{ dialogMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

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

import { get, post, put, remove as removeRequest } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'

const rows = ref([])
const flatRows = ref([])
const dialogVisible = ref(false)
const dialogMode = ref('create')
const treeRows = computed(() => rows.value)
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(treeRows)

const form = reactive({
  id: null,
  code: '',
  name: '',
  description: '',
  parent_id: null,
  unit_type: 'department',
  sort_order: 0,
  warehouse_scope: ''
})

function unitTypeLabel(value) {
  return {
    department: '部门',
    sub_department: '子部门',
    team: '小组'
  }[value] || value || '-'
}

function warehouseScopeLabel(value) {
  return {
    finished: '成品仓',
    raw: '原料仓'
  }[value] || '-'
}

function resetForm() {
  form.id = null
  form.code = ''
  form.name = ''
  form.description = ''
  form.parent_id = null
  form.unit_type = 'department'
  form.sort_order = 0
  form.warehouse_scope = ''
}

function openCreate(parent = null) {
  dialogMode.value = 'create'
  resetForm()
  if (parent) {
    form.parent_id = parent.id
    form.unit_type = parent.unit_type === 'department' ? 'sub_department' : 'team'
  }
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  form.id = row.id
  form.code = row.code
  form.name = row.name
  form.description = row.description || ''
  form.parent_id = row.parent_id
  form.unit_type = row.unit_type
  form.sort_order = row.sort_order || 0
  form.warehouse_scope = row.warehouse_scope || ''
  dialogVisible.value = true
}

async function load() {
  ;[rows.value, flatRows.value] = await Promise.all([
    get('/api/admin/org-units'),
    get('/api/admin/org-units?flat=true')
  ])
}

async function submit() {
  const payload = {
    name: form.name,
    description: form.description,
    parent_id: form.parent_id,
    unit_type: form.unit_type,
    sort_order: form.sort_order,
    is_active: true,
    warehouse_scope: form.warehouse_scope || null
  }
  if (dialogMode.value === 'create') {
    await post('/api/admin/org-units', { code: form.code, ...payload })
    ElMessage.success('组织单元已创建')
  } else {
    await put(`/api/admin/org-units/${form.id}`, payload)
    ElMessage.success('组织单元已更新')
  }
  dialogVisible.value = false
  await load()
}

async function removeUnit(row) {
  await ElMessageBox.confirm(`确认停用或删除组织单元“${row.name}”吗？`, '提示', { type: 'warning' })
  await removeRequest(`/api/admin/org-units/${row.id}`)
  ElMessage.success('组织单元处理完成')
  await load()
}

onMounted(load)
</script>
