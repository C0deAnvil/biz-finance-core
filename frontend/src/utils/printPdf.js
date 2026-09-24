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

export async function downloadOrderPdf(kind, orderId, orderNo) {
  const path = `/api/printing/${kind === 'sales' ? 'sales-orders' : 'placement-orders'}/${orderId}.pdf`
  const url = URL.createObjectURL(await loadPdf(path))
  const link = document.createElement('a')
  link.href = url
  link.download = `${kind === 'sales' ? '销售单' : '置货单'}-${orderNo || orderId}.pdf`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}

export async function printOrderPdf(kind, orderId) {
  const path = `/api/printing/${kind === 'sales' ? 'sales-orders' : 'placement-orders'}/${orderId}.pdf`
  const url = URL.createObjectURL(await loadPdf(path))
  const frame = document.createElement('iframe')
  frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0'
  frame.onload = () => {
    window.setTimeout(() => {
      try {
        frame.contentWindow?.focus()
        frame.contentWindow?.print()
      } catch {
        window.open(url, '_blank', 'noopener')
      }
    }, 300)
  }
  frame.src = url
  document.body.appendChild(frame)
  window.setTimeout(() => {
    frame.remove()
    URL.revokeObjectURL(url)
  }, 120000)
}
