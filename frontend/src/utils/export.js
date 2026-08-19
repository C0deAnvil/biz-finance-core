function normalizeValue(value) {
  if (value == null) return ''
  if (Array.isArray(value)) {
    return value.map((item) => normalizeValue(item)).join('、')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  const text = String(value)
  return /^[=+\-@]/.test(text) ? `'${text}` : text
}

function escapeHtml(value) {
  return normalizeValue(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

function normalizeRows(rows) {
  const headers = []
  const headerSet = new Set()
  for (const row of rows) {
    for (const key of Object.keys(row || {})) {
      if (!headerSet.has(key)) {
        headerSet.add(key)
        headers.push(key)
      }
    }
  }
  return headers
}

function createDownloadLink(blob, filename) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(link.href)
}

function withExtension(filename, extension) {
  const normalizedExtension = extension.startsWith('.') ? extension : `.${extension}`
  return filename.toLowerCase().endsWith(normalizedExtension.toLowerCase()) ? filename : `${filename}${normalizedExtension}`
}

function escapeCsv(value) {
  const text = normalizeValue(value).replaceAll('"', '""')
  return /[",\r\n]/.test(text) ? `"${text}"` : text
}

export function exportRowsToExcel(filename, rows) {
  if (!rows || rows.length === 0) {
    return
  }

  const headers = normalizeRows(rows)
  if (!headers.length) {
    return
  }

  const headerHtml = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')
  const bodyHtml = rows
    .map((row) => `<tr>${headers.map((header) => `<td>${escapeHtml(row?.[header])}</td>`).join('')}</tr>`)
    .join('')

  const html = [
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">',
    '<head>',
    '<meta charset="UTF-8" />',
    '<meta http-equiv="Content-Type" content="application/vnd.ms-excel; charset=UTF-8" />',
    '</head>',
    '<body>',
    `<table border="1"><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`,
    '</body>',
    '</html>'
  ].join('')

  const blob = new Blob([`\uFEFF${html}`], { type: 'application/vnd.ms-excel;charset=utf-8;' })
  createDownloadLink(blob, /\.(xls|xlsx)$/i.test(filename) ? filename : `${filename}.xls`)
}

export function exportRowsToCsv(filename, rows) {
  if (!rows || rows.length === 0) {
    return
  }

  const headers = normalizeRows(rows)
  if (!headers.length) {
    return
  }

  const content = [
    headers.map((header) => escapeCsv(header)).join(','),
    ...rows.map((row) => headers.map((header) => escapeCsv(row?.[header])).join(','))
  ].join('\r\n')

  const blob = new Blob([`\uFEFF${content}`], { type: 'text/csv;charset=utf-8;' })
  createDownloadLink(blob, withExtension(filename, '.csv'))
}
