import { ref } from 'vue'
import { get } from '../api/client'

/**
 * 财务事务指派人候选列表(普通财务 + 超级财务)。
 *
 * 提交客户订单审批、收款申请、报销、采购付款申请等事务时都要选一个财务人员,
 * 三个页面共用同一份加载逻辑,避免各自重复请求与各自处理失败。
 */
export function useFinanceAssignees() {
  const financeAssignees = ref([])

  async function loadFinanceAssignees() {
    try {
      // api/client 的 get() 已解包 payload.data,这里拿到的就是数组本身。
      // 原先写 res?.data 会取到 undefined,四个页面的"指定财务"下拉一直是空的。
      const res = await get('/api/finance/assignable-users')
      financeAssignees.value = Array.isArray(res) ? res : (res?.data || [])
    } catch (error) {
      // 取不到候选列表时不阻塞页面:留空即走后端的自动分配兜底
      financeAssignees.value = []
    }
  }

  return { financeAssignees, loadFinanceAssignees }
}
