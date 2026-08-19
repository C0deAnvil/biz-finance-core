import { computed, reactive, watch } from 'vue'

const DEFAULT_PAGE_SIZES = [10, 20, 50, 100]

export function useLocalPagination(source, { initialPage = 1, initialPageSize = 10, resetOnSourceChange = true } = {}) {
  const pagination = reactive({
    page: initialPage,
    pageSize: initialPageSize
  })

  const total = computed(() => source.value.length)

  const pagedItems = computed(() => {
    const start = (pagination.page - 1) * pagination.pageSize
    return source.value.slice(start, start + pagination.pageSize)
  })

  function syncBounds() {
    const maxPage = Math.max(1, Math.ceil(total.value / pagination.pageSize) || 1)
    if (pagination.page > maxPage) {
      pagination.page = maxPage
    }
  }

  function reset() {
    pagination.page = 1
  }

  function ensureVisible(predicate) {
    const index = source.value.findIndex(predicate)
    if (index < 0) {
      return null
    }
    pagination.page = Math.floor(index / pagination.pageSize) + 1
    return source.value[index]
  }

  watch(
    () => source.value.length,
    () => {
      if (resetOnSourceChange) {
        pagination.page = 1
      }
      syncBounds()
    }
  )

  watch(
    () => pagination.pageSize,
    () => {
      pagination.page = 1
      syncBounds()
    }
  )

  return {
    pagination,
    total,
    pageSizes: DEFAULT_PAGE_SIZES,
    pagedItems,
    reset,
    ensureVisible
  }
}
