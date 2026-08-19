<template>
  <div class="page-stack">
    <PageSection title="模块权限分配" description="部门主管、子部门主管和小组长可在自身权限范围内，为下属分配可操作的小功能模块。首页与审批中心为基础入口，不在此处配置。">
      <template #actions>
        <el-button @click="loadOptions">刷新</el-button>
        <el-button type="primary" :disabled="!selectedUserId" @click="savePermissions">保存权限</el-button>
      </template>

      <el-alert
        class="mb-16"
        type="info"
        :closable="false"
        show-icon
        title="未勾选的模块不会出现在该用户左侧导航栏中，用户直接访问对应地址也会被退回首页。"
      />

      <el-row :gutter="16">
        <el-col :span="8">
          <el-card shadow="never" class="panel-card">
            <template #header>
              <div class="panel-title">可授权人员</div>
            </template>
            <el-input v-model="keyword" placeholder="搜索姓名、账号或组织" clearable class="mb-12" />
            <el-scrollbar height="520px">
              <div
                v-for="item in filteredUsers"
                :key="item.id"
                class="user-item"
                :class="{ active: item.id === selectedUserId }"
                @click="selectUser(item)"
              >
                <div class="user-line">
                  <span class="user-name">{{ item.full_name }}</span>
                  <el-tag size="small" type="info">{{ item.role }}</el-tag>
                </div>
                <div class="user-meta">{{ item.username }}｜{{ item.department_path || '-' }}</div>
              </div>
              <el-empty v-if="!filteredUsers.length" description="暂无可授权下属" />
            </el-scrollbar>
          </el-card>
        </el-col>
        <el-col :span="16">
          <el-card shadow="never" class="panel-card">
            <template #header>
              <div class="panel-title">
                {{ selectedUser ? `${selectedUser.full_name} 的模块权限` : '请选择左侧人员' }}
              </div>
            </template>
            <el-empty v-if="!selectedUserId" description="请选择需要配置权限的人员" />
            <template v-else>
              <div v-for="group in groupedModules" :key="group.group" class="module-group">
                <div class="module-group-title">{{ group.group }}</div>
                <el-checkbox-group v-model="checkedPaths">
                  <el-checkbox
                    v-for="item in group.items"
                    :key="item.path"
                    :label="item.path"
                    border
                    class="module-checkbox"
                  >
                    {{ item.title }}
                  </el-checkbox>
                </el-checkbox-group>
              </div>
              <el-empty v-if="!modules.length" description="当前人员没有可分配模块" />
            </template>
          </el-card>
        </el-col>
      </el-row>
    </PageSection>
  </div>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/scrollbar/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCheckbox, { ElCheckboxGroup } from 'element-plus/es/components/checkbox/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElScrollbar from 'element-plus/es/components/scrollbar/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, ref } from 'vue'

import { get, put } from '../../api/client'
import PageSection from '../../components/PageSection.vue'

const users = ref([])
const modules = ref([])
const selectedUserId = ref(null)
const checkedPaths = ref([])
const keyword = ref('')

const selectedUser = computed(() => users.value.find((item) => item.id === selectedUserId.value) || null)
const filteredUsers = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  if (!value) return users.value
  return users.value.filter((item) =>
    [item.username, item.full_name, item.department_path, item.role]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(value))
  )
})
const groupedModules = computed(() => {
  const groupMap = new Map()
  modules.value.forEach((item) => {
    const group = item.group || '业务功能'
    if (!groupMap.has(group)) groupMap.set(group, [])
    groupMap.get(group).push(item)
  })
  return [...groupMap.entries()].map(([group, items]) => ({ group, items }))
})

async function loadOptions() {
  const data = await get('/api/admin/module-permissions/options')
  users.value = data.users || []
  if (!selectedUserId.value && users.value.length) {
    await selectUser(users.value[0])
  } else if (selectedUserId.value && !users.value.some((item) => item.id === selectedUserId.value)) {
    selectedUserId.value = null
    modules.value = []
    checkedPaths.value = []
  }
}

async function selectUser(user) {
  selectedUserId.value = user.id
  const data = await get(`/api/admin/module-permissions/${user.id}`, { force: true })
  modules.value = data.modules || []
  checkedPaths.value = data.route_paths || []
}

async function savePermissions() {
  if (!selectedUserId.value) return
  await put(`/api/admin/module-permissions/${selectedUserId.value}`, {
    route_paths: checkedPaths.value
  })
  ElMessage.success('模块权限已保存')
}

onMounted(loadOptions)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.mb-16 { margin-bottom: 16px; }
.mb-12 { margin-bottom: 12px; }
.panel-card { min-height: 610px; }
.panel-title { font-size: 16px; font-weight: 700; color: #1f2f3d; }
.user-item {
  padding: 12px;
  border: 1px solid #e4edf6;
  border-radius: 10px;
  margin-bottom: 10px;
  cursor: pointer;
  background: #fff;
  transition: all 0.18s ease;
}
.user-item:hover,
.user-item.active {
  border-color: #4fb3e8;
  background: #eef9ff;
  box-shadow: 0 8px 18px rgba(79, 179, 232, 0.12);
}
.user-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.user-name { font-size: 15px; font-weight: 700; color: #1f2f3d; }
.user-meta { margin-top: 6px; font-size: 13px; color: #7a8a99; line-height: 1.5; }
.module-group { margin-bottom: 22px; }
.module-group-title {
  font-size: 15px;
  font-weight: 700;
  color: #1f2f3d;
  margin-bottom: 12px;
  padding-left: 8px;
  border-left: 4px solid #4fb3e8;
}
.module-checkbox {
  margin: 0 10px 10px 0;
  min-width: 150px;
}
</style>
