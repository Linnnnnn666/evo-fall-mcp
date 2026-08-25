export const name = 'text-stats'
export const inject = ['tools']

// 从入参中取出要统计的文本（兼容字符串直接传入或 { text: ... } 等对象形式）
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

function stats(text) {
  if (text === '') {
    return { chars: 0, chars_no_space: 0, lines: 0, words: 0, cjk_chars: 0, english_words: 0 }
  }
  const chars = text.length
  const charsNoSpace = text.replace(/\s/g, '').length
  // 行数：忽略末尾单个换行（"a\n" 记 1 行），空文本记 0 行
  const lines = text.replace(/\n+$/, '') === '' ? 0 : text.replace(/\n+$/, '').split('\n').length
  // 词数：英文/数字单词（含连字符/撇号）+ 每个汉字单独计 1 词
  const englishWords = (text.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) || []).length
  const cjkChars = (text.match(/[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/g) || []).length
  return {
    chars,
    chars_no_space: charsNoSpace,
    lines,
    words: englishWords + cjkChars,
    cjk_chars: cjkChars,
    english_words: englishWords,
  }
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

const textStats = safe((args) => {
  const text = pickText(args)
  if (text === '') return '文本为空，无法统计'
  const r = stats(text)
  return [
    `字数: ${r.chars}（不含空白: ${r.chars_no_space}）`,
    `行数: ${r.lines}`,
    `词数: ${r.words}（汉字: ${r.cjk_chars}，英文/数字单词: ${r.english_words}）`,
  ].join('\n')
})

export function apply(ctx, config) {
  const TOOLS = {
    text_stats: { desc: '统计文本的字数（含/不含空白）、行数、词数（汉字每字计 1 词，英文按单词计）', fn: textStats },
  }
  for (const [toolName, tool] of Object.entries(TOOLS)) {
    ctx.tools.register({
      name: toolName,
      description: tool.desc,
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: '要统计的文本' },
        },
      },
      execute: tool.fn,
      output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    })
  }
}
