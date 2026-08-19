<template>
  <div class="attachment-uploader">
    <el-form v-if="!readonly" inline>
      <el-form-item label="附件类型">
        <el-select v-if="presetTypes.length" v-model="attachmentType" filterable allow-create default-first-option placeholder="请选择或输入附件类型" class="type-select">
          <el-option v-for="item in presetTypes" :key="item" :label="item" :value="item" />
        </el-select>
        <el-input v-else v-model="attachmentType" placeholder="如：合同、发票、凭证、质检照片" />
      </el-form-item>
      <el-form-item>
        <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="handleFileChange" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :disabled="!selectedFile || !attachmentType" @click="submit">上传</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="attachments" stripe v-if="attachments.length">
      <el-table-column prop="attachment_type" label="类型" width="180" />
      <el-table-column prop="original_name" label="文件名" />
      <el-table-column label="操作" width="160">
        <template #default="scope">
          <el-button size="small" @click="openAttachment(scope.row.id)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { onMounted, ref } from 'vue'

import { get, upload } from '../api/client'

const props = defineProps({
  businessType: { type: String, required: true },
  businessId: { type: Number, required: true },
  readonly: { type: Boolean, default: false },
  presetTypes: { type: Array, default: () => [] }
})

const attachments = ref([])
const attachmentType = ref('')
const selectedFile = ref(null)

function handleFileChange(event) {
  selectedFile.value = event.target.files[0]
}

async function loadAttachments() {
  attachments.value = await get(`/api/files/business/${props.businessType}/${props.businessId}`)
}

async function submit() {
  if (!selectedFile.value) return
  const formData = new FormData()
  formData.append('business_type', props.businessType)
  formData.append('business_id', props.businessId)
  formData.append('attachment_type', attachmentType.value)
  formData.append('file', selectedFile.value)
  await upload('/api/files/upload', formData)
  ElMessage.success('附件上传成功')
  attachmentType.value = ''
  selectedFile.value = null
  await loadAttachments()
}

function openAttachment(id) {
  window.open(`/api/files/${id}`, '_blank')
}

onMounted(loadAttachments)
</script>

<style scoped>
.attachment-uploader { display: flex; flex-direction: column; gap: 12px; }
.type-select { width: 220px; }
</style>
