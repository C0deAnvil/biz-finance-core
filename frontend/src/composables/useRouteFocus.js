import { nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

export function parseFocusId(value) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

export function useRouteFocus(options = {}) {
  const route = options.route || useRoute()
  const queryKey = options.queryKey || 'focusId'
  const focusedId = ref(null)

  async function applyFocus(resolveRow, afterMatch, explicitValue = route.query[queryKey]) {
    focusedId.value = parseFocusId(explicitValue)
    if (!focusedId.value) {
      return null
    }
    const matched = resolveRow(focusedId.value)
    if (!matched) {
      return null
    }
    await nextTick()
    if (afterMatch) {
      await afterMatch(matched, focusedId.value)
    }
    return matched
  }

  function resetFocus() {
    focusedId.value = null
  }

  function watchFocus(resolveRow, afterMatch, source = () => route.query[queryKey]) {
    return watch(source, async (value) => {
      await applyFocus(resolveRow, afterMatch, value)
    })
  }

  return {
    route,
    focusedId,
    applyFocus,
    resetFocus,
    watchFocus
  }
}
