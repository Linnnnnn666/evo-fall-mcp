export const name = 'evolution-log-summary'
export const inject = ['tools']

// 进化日志摘要插件（零依赖、纯计算、无 IO）
// 输入形如：
//   [2026-09-28 03:07:43] plugin_installed | req_1787900002 -> evolution-log-summary
// 输出：total / first / last / span_hours / by_type / by_hour / anomalies[]

const LINE_RE = /^\s*\[(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\]\s*([^|]*?)\s*(?:\|\s*([\s\S]*))?$/
const ANOMALY_RE = /(fail|failed|failure|reject|rejected|warn|warning|skip|skipped|error|abort|timeout|denied|panic|crash|exception)/i

// 从入参中取出要分析的日志全文（兼容字符串直传或 { text/log/content/... } 对象形式）
function pickText(args) {
  if (args == null) return ''
  if (typeof args === 'string') return args
  if (typeof args === 'number' || typeof args === 'boolean') return String(args)
  if (typeof args === 'object') {
    for (const k of ['text', 'log', 'content', 'input', 'data', 'value', 'str', 'logs', 'raw']) {
      const v = args[k]
      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v)
    }
    return ''
  }
  return String(args)
}

function pickPositiveInt(args, key, fallback, max) {
  if (args && typeof args === 'object') {
    const v = args[key]
    const n = typeof v === 'string' ? Number(v) : v
    if (typeof n === 'number' && Number.isFinite(n) && n > 0) return Math.min(Math.floor(n), max)
  }
  return fallback
}

function parseMs(date, time) {
  const ms = Date.parse(`${date}T${time}Z`)
  return Number.isFinite(ms) ? ms : NaN
}

function round(n, d) {
  const f = Math.pow(10, d)
  return Math.round(n * f) / f
}

function summarize(text, args) {
  const anomalyLimit = pickPositiveInt(args, 'anomaly_limit', 50, 500)
  const showDetail = !(args && typeof args === 'object' && args.include_detail === false)

  const lines = String(text).split(/\r?\n/)
  const byType = new Map()
  const byHour = new Map()
  const anomalies = []
  let total = 0
  let unparsed = 0
  let anomalyTotal = 0
  let firstMs = Infinity
  let lastMs = -Infinity
  let first = null
  let last = null

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    if (raw.trim() === '') continue
    const m = LINE_RE.exec(raw)
    if (!m) {
      unparsed++
      continue
    }
    const date = m[1]
    const time = m[2]
    const type = (m[3] || '').trim() || '(unknown)'
    const detail = (m[4] || '').trim()
    const stamp = `${date} ${time}`
    const ms = parseMs(date, time)

    total++
    byType.set(type, (byType.get(type) || 0) + 1)

    const hour = `${date} ${time.slice(0, 2)}:00`
    byHour.set(hour, (byHour.get(hour) || 0) + 1)

    if (Number.isFinite(ms)) {
      if (ms < firstMs) {
        firstMs = ms
        first = stamp
      }
      if (ms > lastMs) {
        lastMs = ms
        last = stamp
      }
    } else if (first === null) {
      first = stamp
      last = stamp
    }

    if (ANOMALY_RE.test(type) || ANOMALY_RE.test(detail)) {
      anomalyTotal++
      if (anomalies.length < anomalyLimit) {
        const item = { line: i + 1, time: stamp, type }
        if (showDetail) item.detail = detail.length > 300 ? `${detail.slice(0, 300)}…` : detail
        anomalies.push(item)
      }
    }
  }

  const byTypeSorted = Object.fromEntries(
    [...byType.entries()].sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0])),
  )
  const byHourSorted = Object.fromEntries([...byHour.entries()].sort((a, b) => a[0].localeCompare(b[0])))
  const spanHours = Number.isFinite(firstMs) && Number.isFinite(lastMs) && lastMs >= firstMs
    ? round((lastMs - firstMs) / 3600000, 3)
    : 0

  const out = {
    total,
    first,
    last,
    span_hours: spanHours,
    by_type: byTypeSorted,
    by_hour: byHourSorted,
    anomalies,
    anomaly_count: anomalyTotal,
    unparsed_lines: unparsed,
  }
  if (total === 0) out.note = '未解析到任何形如 [YYYY-MM-DD HH:MM:SS] event_type | detail 的日志行'
  if (anomalyTotal > anomalies.length) out.anomalies_truncated = true
  return out
}

function safe(fn) {
  return (args) => {
    try {
      return String(fn(args))
    } catch (e) {
      return '错误: ' + (e && e.message ? e.message : String(e))
    }
  }
}

const summarizeEvolutionLog = safe((args) => {
  const text = pickText(args)
  if (text.trim() === '') {
    return JSON.stringify(
      {
        total: 0,
        first: null,
        last: null,
        span_hours: 0,
        by_type: {},
        by_hour: {},
        anomalies: [],
        anomaly_count: 0,
        unparsed_lines: 0,
        note: '日志文本为空，请把 evolution.log 全文作为 text 参数传入',
      },
      null,
      2,
    )
  }
  return JSON.stringify(summarize(text, args), null, 2)
})

export function apply(ctx, config) {
  const TOOLS = {
    summarize_evolution_log: {
      desc: '解析进化日志文本（每行形如 [YYYY-MM-DD HH:MM:SS] event_type | detail），输出事件总数 total、时间跨度 first/last/span_hours、按事件类型计数 by_type、按小时分布 by_hour，并把 failed/rejected/warn/skipped 类条目单列为 anomalies（需关注项）',
      fn: summarizeEvolutionLog,
    },
  }
  for (const [toolName, tool] of Object.entries(TOOLS)) {
    ctx.tools.register({
      name: toolName,
      description: tool.desc,
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: '进化日志全文（多行字符串，可直接粘贴）' },
          anomaly_limit: { type: 'number', description: 'anomalies 最多返回多少条，默认 50，上限 500' },
          include_detail: { type: 'boolean', description: 'anomalies 是否包含 detail 明细，默认 true' },
        },
        required: ['text'],
      },
      execute: tool.fn,
      output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    })
  }
}
