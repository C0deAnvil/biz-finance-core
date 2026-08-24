<template>
  <div class="app-pagination">
    <el-pagination
      :current-page="page"
      :page-size="pageSize"
      :page-sizes="pageSizes"
      :layout="layout"
      :total="total"
      background
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />
  </div>
</template>

<script setup>
import 'element-plus/es/components/pagination/style/css'

import ElPagination from 'element-plus/es/components/pagination/index.mjs'

const props = defineProps({
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
  pageSizes: {
    type: Array,
    default: () => [10, 20, 50, 100]
  },
  layout: {
    type: String,
    default: 'total, sizes, prev, pager, next, jumper'
  }
})

const emit = defineEmits(['update:page', 'update:pageSize', 'change'])

function handleSizeChange(size) {
  emit('update:pageSize', size)
  emit('change', { page: 1, pageSize: size })
}

function handleCurrentChange(currentPage) {
  emit('update:page', currentPage)
  emit('change', { page: currentPage, pageSize: props.pageSize })
}
</script>

<style scoped>
.app-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
}

@media (max-width: 768px) {
  .app-pagination {
    justify-content: center;
    padding-top: 12px;
  }

  .app-pagination :deep(.el-pagination) {
    justify-content: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .app-pagination :deep(.el-pagination__jump) {
    display: none;
  }
}
</style>
