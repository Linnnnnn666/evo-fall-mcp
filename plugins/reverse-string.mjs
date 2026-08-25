export const name = 'reverse-string'
export const inject = ['tools']

// 从入参中取出要反转的字符串（兼容字符串直接传入或 { text: ... } 等对象形式）
function pickText(args) {
  if (args == null) return ''
  if (typeof args === 'string') return args
  if (typeof args === 'number' || typeof args === 'boolean') return String(args)
  if (typeof args === 'object') {
    for (const k of ['text', 'input', 'content', 'value', 'data', 'str', 'message']) {
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

const reverseString = safe((args) => {
  const s = pickText(args)
  if (s === '') return ''
  // 按 Unicode 码点反转，正确处理 emoji / 代理对
  return Array.from(s).reverse().join('')
})

export function apply(ctx, config) {
  const TOOLS = {
    reverse_string: { desc: '反转字符串：输入字符串，输出反转后的结果（按 Unicode 码点处理）', fn: reverseString },
  }
  for (const [toolName, tool] of Object.entries(TOOLS)) {
    ctx.tools.register({
      name: toolName,
      description: tool.desc,
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: '要反转的字符串' },
        },
      },
      execute: tool.fn,
      output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    })
  }
}
