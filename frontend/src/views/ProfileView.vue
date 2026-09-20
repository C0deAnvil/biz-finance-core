<template>
  <div class="page-stack profile-page">
    <PageSection title="个人中心" description="管理个人资料和登录密码，角色与系统权限由管理员统一维护。">
      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header>个人资料</template>
            <el-form :model="profileForm" label-width="100px">
              <el-form-item label="登录账号"><el-input :model-value="auth.user?.username || '-'" disabled /></el-form-item>
              <el-form-item label="姓名" required><el-input v-model="profileForm.full_name" maxlength="100" /></el-form-item>
              <el-form-item label="联系电话"><el-input v-model="profileForm.phone" maxlength="50" /></el-form-item>
              <el-form-item label="邮箱"><el-input v-model="profileForm.email" maxlength="120" /></el-form-item>
              <el-form-item label="岗位"><el-input v-model="profileForm.job_title" maxlength="100" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="savingProfile" @click="saveProfile">保存个人资料</el-button></el-form-item>
            </el-form>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header>我的组织信息</template>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="角色">{{ auth.user?.role_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="所属部门">{{ auth.user?.department_path || auth.user?.department_name || '-' }}</el-descriptions-item>
              <el-descriptions-item label="数据范围">{{ scopeLabel(auth.user?.data_scope) }}</el-descriptions-item>
              <el-descriptions-item label="管理员">{{ auth.user?.is_admin ? '是' : '否' }}</el-descriptions-item>
            </el-descriptions>
            <p class="profile-hint">角色、部门、数据范围和权限需要由管理员调整，员工本人只能查看。</p>
          </el-card>
          <el-card shadow="never" class="profile-card">
            <template #header>我的可访问页面</template>
            <div class="permission-list"><el-tag v-for="item in accessiblePages" :key="item.path" type="info" effect="plain">{{ item.title }}</el-tag><span v-if="!accessiblePages.length">暂无可显示的页面</span></div>
          </el-card>
        </el-col>
      </el-row>
    </PageSection>

    <PageSection title="修改密码" description="修改后当前登录状态保持不变，下次登录请使用新密码。">
      <el-card shadow="never" class="password-card">
        <el-form :model="passwordForm" label-width="120px" style="max-width: 560px">
          <el-form-item label="当前密码" required><el-input v-model="passwordForm.current_password" type="password" show-password /></el-form-item>
          <el-form-item label="新密码" required><el-input v-model="passwordForm.new_password" type="password" show-password minlength="6" /></el-form-item>
          <el-form-item label="确认新密码" required><el-input v-model="passwordForm.confirm_password" type="password" show-password /></el-form-item>
          <el-form-item><el-button type="primary" :loading="savingPassword" @click="changePassword">修改密码</el-button></el-form-item>
        </el-form>
      </el-card>
    </PageSection>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import PageSection from '../components/PageSection.vue'
import { patch, post } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const profileForm = reactive({ full_name: '', phone: '', email: '', job_title: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })
const savingProfile = computed(() => profileForm._saving === true)
const savingPassword = computed(() => passwordForm._saving === true)
const scopeLabels = { self: '仅本人', team: '本人及团队', sub_department: '本人及子部门', department: '本人及部门', all: '全部数据' }
const accessiblePages = computed(() => {
  const result = []
  const visit = (items = []) => items.forEach((item) => item.children?.length ? visit(item.children) : item.path && result.push(item))
  visit(auth.user?.navigation || [])
  return result
})
function scopeLabel(value) { return scopeLabels[value] || value || '-' }
function syncForm() { Object.assign(profileForm, { full_name: auth.user?.full_name || '', phone: auth.user?.phone || '', email: auth.user?.email || '', job_title: auth.user?.job_title || '' }) }
watch(() => auth.user, syncForm, { immediate: true })
async function saveProfile() {
  if (!profileForm.full_name.trim()) { ElMessage.warning('姓名不能为空'); return }
  profileForm._saving = true
  try { await patch('/api/auth/me', { full_name: profileForm.full_name, phone: profileForm.phone, email: profileForm.email, job_title: profileForm.job_title }); await auth.fetchProfile(); syncForm(); ElMessage.success('个人资料已保存') } catch (e) { ElMessage.error(e.message) } finally { profileForm._saving = false }
}
async function changePassword() {
  if (!passwordForm.current_password || !passwordForm.new_password) { ElMessage.warning('请填写密码'); return }
  if (passwordForm.new_password.length < 6) { ElMessage.warning('新密码至少需要 6 位'); return }
  if (passwordForm.new_password !== passwordForm.confirm_password) { ElMessage.warning('两次输入的新密码不一致'); return }
  passwordForm._saving = true
  try { await post('/api/auth/change-password', { current_password: passwordForm.current_password, new_password: passwordForm.new_password }); Object.assign(passwordForm, { current_password: '', new_password: '', confirm_password: '' }); ElMessage.success('密码已修改') } catch (e) { ElMessage.error(e.message) } finally { passwordForm._saving = false }
}
</script>

<style scoped>
.profile-card { margin-top: 16px; }
.password-card { max-width: 760px; }
.profile-hint { color: var(--el-text-color-secondary); margin: 16px 0 0; line-height: 1.6; }
.permission-list { display: flex; flex-wrap: wrap; gap: 8px; }
.permission-list span { color: var(--el-text-color-secondary); }
</style>
