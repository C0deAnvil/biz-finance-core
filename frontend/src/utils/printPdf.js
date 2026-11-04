async function loadPdf(path) {
  const response = await fetch(path, { credentials: 'include', cache: 'no-store' })
  if (!response.ok) {
    let message = `生成单据失败（HTTP ${response.status}）`
    try {
      const payload = await response.json()
      message = payload.msg || payload.detail || message
    } catch {
      // Preserve the HTTP status when the server did not return JSON.
    }
    throw new Error(message)
  }
  return response.blob()
}

function printPath(kind, orderId) {
  if (kind === 'sales') return `/api/printing/sales-orders/${orderId}.pdf`
  if (kind === 'placement') return `/api/printing/placement-orders/${orderId}.pdf`
  if (kind === 'shipment') return `/api/printing/shipment-orders/${orderId}.pdf`
  if (kind === 'production-demand') return `/api/printing/production-demands/${orderId}.pdf`
  throw new Error('不支持的打印单据类型')
}

function printLabel(kind) {
  if (kind === 'sales') return '销售单'
  if (kind === 'placement') return '置货单'
  if (kind === 'shipment') return '发货单'
  if (kind === 'production-demand') return '生产补货需求单'
  return '业务单据'
}

export async function downloadOrderPdf(kind, orderId, orderNo) {
  const path = printPath(kind, orderId)
  const url = URL.createObjectURL(await loadPdf(path))
  const link = document.createElement('a')
  link.href = url
  link.download = `${printLabel(kind)}-${orderNo || orderId}.pdf`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}

export async function printOrderPdf(kind, orderId) {
  const path = printPath(kind, orderId)
  const url = URL.createObjectURL(await loadPdf(path))
  const frame = document.createElement('iframe')
  frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0'
  await new Promise((resolve) => {
    frame.onload = () => {
      window.setTimeout(() => {
        try {
          frame.contentWindow?.focus()
          frame.contentWindow?.print()
        } catch {
          window.open(url, '_blank', 'noopener')
        }
        resolve()
      }, 300)
    }
    frame.src = url
    document.body.appendChild(frame)
  })
  window.setTimeout(() => {
    frame.remove()
    URL.revokeObjectURL(url)
  }, 120000)
}
