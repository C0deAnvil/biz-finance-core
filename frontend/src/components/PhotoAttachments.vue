<template>
  <div class="photo-attachments">
    <div class="photo-header">
      <span class="photo-title">{{ title }}</span>
      <span class="photo-count">{{ photos.length }} 张</span>
      <span v-if="!readonly && required && !photos.length" class="photo-required">至少上传 1 张</span>
    </div>

    <div v-if="photos.length" class="photo-grid">
      <div v-for="(photo, index) in photos" :key="photo.id" class="photo-cell">
        <el-image
          :src="photoUrl(photo.id)"
          :preview-src-list="previewList"
          :initial-index="index"
          fit="cover"
          preview-teleported
          class="photo-thumb"
        >
          <template #error>
            <div class="photo-broken">加载失败</div>
          </template>
        </el-image>
        <div class="photo-meta">
          <span class="photo-name" :title="photo.original_name">{{ photo.original_name }}</span>
          <span class="photo-sub">{{ photo.uploaded_by_name || '-' }} · {{ shortTime(photo.created_at) }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else :description="readonly ? '暂无照片' : '还没有上传照片'" :image-size="60" />

    <div v-if="!readonly" class="photo-actions">
      <input
        ref="fileInput"
        type="file"
        accept=".jpg,.jpeg,.png"
        multiple
        class="photo-input"
        :disabled="!businessId || uploading"
        @change="handleFileChange"
      />
      <el-button
        plain
        size="small"
        :disabled="!businessId || uploading"
        @click="fileInput?.click()"
      >选择照片</el-button>
      <span v-if="pendingFiles.length" class="photo-hint">
        已选择 {{ pendingFiles.length }} 张：{{ pendingFiles.map(file => file.name).join('、') }}
      </span>
      <el-button
        type="primary"
        plain
        size="small"
        :loading="uploading"
        :disabled="!businessId || !pendingFiles.length"
        @click="submit"
      >
        上传{{ pendingFiles.length ? ` ${pendingFiles.length} 张` : '' }}
      </el-button>
      <span class="photo-hint">支持 jpg / png，单张不超过 10MB</span>
    </div>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/image/style/css'
import 'element-plus/es/components/message/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElImage from 'element-plus/es/components/image/index.mjs'

import { computed, onMounted, ref, watch } from 'vue'

import { get, upload } from '../api/client'

const props = defineProps({
  businessType: { type: String, required: true },
  businessId: { type: [Number, String], default: null },
  // 只展示 / 上传这一类附件，例如「发货照片」「到货照片」
  attachmentType: { type: String, default: '照片' },
  // 兼容历史附件类型。例如扫码发货早期保存为“发货凭证”，现统一为“发货照片”。
  attachmentTypes: { type: Array, default: () => [] },
  title: { type: String, default: '照片' },
  readonly: { type: Boolean, default: false },
  required: { type: Boolean, default: false }
})

const emit = defineEmits(['change'])

const photos = ref([])
const pendingFiles = ref([])
const uploading = ref(false)
const fileInput = ref(null)

const MAX_SIZE = 10 * 1024 * 1024

const previewList = computed(() => photos.value.map((item) => photoUrl(item.id)))

function photoUrl(id) {
  return `/api/files/${id}`
}

function shortTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 16)
}

function handleFileChange(event) {
  const files = [...(event.target.files || [])]
  const tooBig = files.filter((file) => file.size > MAX_SIZE)
  if (tooBig.length) {
    ElMessage.warning(`${tooBig.map((f) => f.name).join('、')} 超过 10MB，已忽略`)
  }
  pendingFiles.value = files.filter((file) => file.size <= MAX_SIZE)
}

function resetInput() {
  pendingFiles.value = []
  if (fileInput.value) fileInput.value.value = ''
}

async function loadPhotos() {
  if (!props.businessId) {
    photos.value = []
    return
  }
  try {
    const rows = await get(`/api/files/business/${props.businessType}/${props.businessId}`, { force: true })
    photos.value = (rows || []).filter((row) => {
      const isImage = /image\//i.test(row.mime_type || '') || /\.(jpe?g|png)$/i.test(row.original_name || '')
      if (!isImage) return false
      // attachmentType 为空表示不按类型过滤，展示该单据下全部照片
      const acceptedTypes = props.attachmentTypes.length
        ? props.attachmentTypes
        : (props.attachmentType === '发货照片' ? ['发货照片', '发货凭证'] : [props.attachmentType])
      return !props.attachmentType || acceptedTypes.includes(row.attachment_type)
    })
  } catch (error) {
    photos.value = []
  }
  emit('change', photos.value)
}

async function submit() {
  if (!pendingFiles.value.length) return
  if (!props.businessId) {
    ElMessage.warning('单据尚未生成，无法上传照片')
    return
  }
  uploading.value = true
  try {
    for (const file of pendingFiles.value) {
      const formData = new FormData()
      formData.append('business_type', props.businessType)
      formData.append('business_id', String(props.businessId))
      formData.append('attachment_type', props.attachmentType || '照片')
      formData.append('file', file)
      await upload('/api/files/upload', formData)
    }
    ElMessage.success(`已上传 ${pendingFiles.value.length} 张照片`)
    resetInput()
    await loadPhotos()
  } catch (error) {
    ElMessage.error(error?.message || '照片上传失败')
  } finally {
    uploading.value = false
  }
}

defineExpose({ reload: loadPhotos, count: () => photos.value.length })

onMounted(loadPhotos)

watch(
  () => [props.businessType, props.businessId, props.attachmentType, props.attachmentTypes.join('|')],
  () => {
    resetInput()
    loadPhotos()
  }
)
</script>

<style scoped>
.photo-attachments { display: flex; flex-direction: column; gap: 10px; }
.photo-header { display: flex; align-items: center; gap: 10px; font-weight: 600; color: #303133; }
.photo-count { font-weight: 400; color: #909399; font-size: 13px; }
.photo-required { font-weight: 400; color: #e6a23c; font-size: 12px; }
.photo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
}
.photo-cell { display: flex; flex-direction: column; gap: 4px; }
.photo-thumb {
  width: 100%;
  aspect-ratio: 1 / 1;
  border-radius: 6px;
  border: 1px solid #ebeef5;
  cursor: zoom-in;
  background: #fafafa;
}
.photo-broken {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%; color: #c0c4cc; font-size: 12px;
}
.photo-meta { display: flex; flex-direction: column; font-size: 12px; line-height: 1.4; }
.photo-name { color: #606266; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.photo-sub { color: #a8abb2; }
.photo-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.photo-input { display: none; }
.photo-hint { color: #a8abb2; font-size: 12px; }

@media (max-width: 768px) {
  .photo-grid { grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); }
}
</style>
