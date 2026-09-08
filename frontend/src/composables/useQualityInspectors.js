import { ref } from 'vue'
import { get } from '../api/client'

/**
 * 质检任务指派人候选列表(普通质检员 + 超级质检员)。
 *
 * 申请质检(采购来料、生产报工)时都要选一个质检员,
 * 多个页面共用同一份加载逻辑,避免各自重复请求与各自处理失败。
 */
export function useQualityInspectors() {
  const qualityInspectors = ref([])

  async function loadQualityInspectors() {
    try {
      // api/client 的 get() 已解包 payload.data,这里拿到的就是数组本身。
      // 不能再写 res?.data —— 那会取到 undefined,下拉框静默变空。
      const res = await get('/api/quality/assignable-inspectors')
      qualityInspectors.value = Array.isArray(res) ? res : (res?.data || [])
    } catch (error) {
      // 取不到候选列表时不阻塞页面:留空即走"该仓库质检员自行领取"的兜底
      qualityInspectors.value = []
    }
  }

  return { qualityInspectors, loadQualityInspectors }
}
