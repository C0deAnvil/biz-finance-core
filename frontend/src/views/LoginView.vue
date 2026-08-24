<template>
  <div class="login-page">
    <el-card class="login-card" shadow="never">
      <template #header>
        <div class="login-header">
          <div class="login-mark">Y</div>
          <div>
            <div class="login-title">业财一体化系统</div>
            <div class="login-subtitle">业务、库存、生产与财务协同平台</div>
          </div>
        </div>
      </template>
      <el-form :model="form" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="form.username" placeholder="请输入账号" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" show-password size="large" />
        </el-form-item>
        <el-form-item label="设备">
          <el-input v-model="form.device_name" placeholder="可选：当前设备名称" size="large" />
        </el-form-item>
        <el-button type="primary" size="large" class="full-width login-button" @click="submit" :loading="loading">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'

import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
  device_name: ''
})

async function submit() {
  loading.value = true
  try {
    await auth.login(form)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #185f93;
  padding: 28px;
  box-sizing: border-box;
}

.login-card {
  width: min(480px, 100%);
  border: none;
  border-radius: 18px;
  box-shadow: 0 22px 60px rgba(10, 43, 70, 0.28);
}

.login-card :deep(.el-card__header) {
  padding: 28px 30px 18px;
  border-bottom: 1px solid #e7eef5;
}

.login-card :deep(.el-card__body) {
  padding: 24px 30px 30px;
}

.login-header {
  display: flex;
  align-items: center;
  gap: 14px;
}

.login-mark {
  width: 46px;
  height: 46px;
  border-radius: 14px;
  background: #185f93;
  color: #fff;
  font-size: 24px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 10px 22px rgba(24, 95, 147, 0.22);
}

.login-title {
  font-size: 24px;
  font-weight: 700;
  color: #172b3a;
  line-height: 1.25;
}

.login-subtitle {
  margin-top: 4px;
  font-size: 14px;
  color: #7a8b9a;
  line-height: 1.5;
}

.login-card :deep(.el-form-item__label) {
  font-size: 15px;
  color: #34495e;
}

.login-card :deep(.el-input__wrapper) {
  border-radius: 10px;
}

.full-width {
  width: 100%;
}

.login-button {
  margin-top: 6px;
  border-radius: 10px;
  background: #185f93;
  border-color: #185f93;
  font-size: 16px;
  letter-spacing: 2px;
}

.login-button:hover,
.login-button:focus {
  background: #1d6fa9;
  border-color: #1d6fa9;
}

@media (max-width: 600px) {
  .login-page {
    align-items: flex-start;
    padding: 16px;
  }

  .login-card {
    margin-top: 8vh;
    border-radius: 14px;
  }

  .login-card :deep(.el-card__header) {
    padding: 20px 18px 14px;
  }

  .login-card :deep(.el-card__body) {
    padding: 18px;
  }

  .login-header {
    align-items: flex-start;
  }

  .login-title {
    font-size: 20px;
  }

  .login-subtitle {
    font-size: 13px;
  }

  .login-mark {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    font-size: 21px;
  }
}
</style>
