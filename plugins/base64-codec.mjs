export const name = 'base64-codec'
export const inject = ['tools']

function pickInput(args) {
  if (args == null) return ''
  if (typeof args === 'string') return args
  if (typeof args === 'number' || typeof args === 'boolean') return String(args)
  if (typeof args === 'object') {
    for (const k of ['text', 'input', 'value', 'content', 'data', 'str', 'message']) {
      const v = args[k]
      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v)
    }
    return JSON.stringify(args)
  }
  return String(args)
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

const encode = safe((args) => {
  const s = pickInput(args)
  if (s === '') return ''
  return Buffer.from(s, 'utf8').toString('base64')
})

const decode = safe((args) => {
  const s = pickInput(args).replace(/\s+/g, '')
  if (s === '') return ''
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) throw new Error('不是合法的 BASE64 字符串（含非法字符）')
  if (s.length % 4 === 1) throw new Error('不是合法的 BASE64 字符串（长度非法）')
  const buf = Buffer.from(s, 'base64')
  if (buf.toString('base64').replace(/\s+/g, '') !== s) throw new Error('不是合法的 BASE64 字符串（校验失败）')
  return buf.toString('utf8')
})

export function apply(ctx, config) {
  const TOOLS = {
    base64_encode: { desc: '把字符串编码为 BASE64', fn: encode },
    base64_decode: { desc: '把 BASE64 解码为原字符串', fn: decode },
  }
  for (const [toolName, tool] of Object.entries(TOOLS)) {
    ctx.tools.register({
      name: toolName,
      description: tool.desc,
      parameters: { type: 'object', properties: {} },
      execute: tool.fn,
      output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    })
  }
}
