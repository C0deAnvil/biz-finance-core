<template>
  <el-card class="page-section">
    <template #header>
      <div class="section-header">
        <div class="section-main">
          <div class="section-title">{{ title }}</div>
          <div v-if="description" class="section-description">{{ description }}</div>
        </div>
        <div class="section-actions">
          <slot name="actions" />
        </div>
      </div>
    </template>
    <slot />
  </el-card>
</template>

<script setup>
import 'element-plus/es/components/card/style/css'

import ElCard from 'element-plus/es/components/card/index.mjs'

defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' }
})
</script>

<style scoped>
.page-section {
  margin-bottom: 16px;
  border-radius: 14px;
  border: 1px solid #e4edf6;
  box-shadow: 0 10px 26px rgba(31, 47, 61, 0.05);
}
.section-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.section-main { flex: 1; min-width: 0; }
.section-title { font-size: 17px; font-weight: 700; color: #1f2f3d; }
.section-description { font-size: 13px; color: #7a8a99; margin-top: 5px; line-height: 1.5; }
.section-actions { display: flex; gap: 8px; flex-wrap: nowrap; align-items: center; flex-shrink: 0; white-space: nowrap; }

@media (max-width: 768px) {
  .page-section {
    margin-bottom: 12px;
  }

  .section-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    width: 100%;
    flex-wrap: wrap;
    white-space: normal;
  }

  .section-actions :deep(.el-button),
  .section-actions :deep(.el-button-group),
  .section-actions :deep(.el-select),
  .section-actions :deep(.el-input) {
    max-width: 100%;
  }

  .section-title {
    font-size: 16px;
  }
}
</style>
