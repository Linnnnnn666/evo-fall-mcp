export const name = 'ld6002b-frame'
export const inject = ['tools']

// ─────────────────────────────────────────────────────────────────────────────
// LD6002B（HLK 60GHz 毫米波雷达）TinyFrame 帧解析 / 校验
//
// 帧结构（字段大端；参考 MightyPork/TinyFrame 与 LD6002B 串口协议 V1.1）：
//   ,-----+--------+--------+--------+----------+---------+----------,
//   | SOF | ID     | LEN    | TYPE   | HCK      | DATA    | DCK      |
//   | 0x01| 2 byte | 2 byte | 2 byte | 1 byte   | LEN byte| 1 byte   |
//   '-----+--------+--------+--------+----------+---------+----------'
//   HCK = ~(XOR of header bytes)   DCK = ~(XOR of payload bytes)
//   LEN == 0 时 DCK 省略（TinyFrame 规范）
//
// 本插件为纯计算实现：无 shell、无文件读写、无网络、无第三方依赖。
// ─────────────────────────────────────────────────────────────────────────────

const SOF = 0x01
const DEFAULT_MAX_LEN = 4096
const ABS_MAX_LEN = 65535

// 协议消息类型（LD6002B 通信协议 V1.1）
const TYPE_NAMES = {
  0x0201: 'control 控制命令',
  0x0202: 'set_area 设置检测区域',
  0x0203: 'set_hold_delay 设置停留延时',
  0x0204: 'set_z_range 设置 Z 轴范围',
  0x0205: 'set_low_power_sleep 设置无人低功耗睡眠时间',
  0x0a04: 'report_target 目标/点云上报',
  0x0a08: 'report_point_cloud 点云上报',
  0x0a0a: 'report_area_presence 区域存在上报',
  0x0a0b: 'report_interference_areas 干扰区上报',
  0x0a0c: 'report_detection_areas 检测区上报',
  0x0a0d: 'report_delay 延时上报',
  0x0a0e: 'report_sensitivity 灵敏度上报',
  0x0a0f: 'report_trigger 触发速度上报',
  0x0a10: 'report_z_range Z 轴范围上报',
  0x0a11: 'report_installation 安装方式上报',
  0x0a12: 'report_low_power 低功耗上报',
  0x0a13: 'report_low_power_sleep 低功耗睡眠时间上报',
  0x0a14: 'report_work_mode 工作模式上报',
  0xffff: 'query_version 版本查询',
}

// ── 入参适配：允许直接传字符串，或传 { frame: ... } / { hex: ... } 等对象 ──
function pickFrame(args) {
  if (args == null) return ''
  if (typeof args === 'string') return args
  if (typeof args === 'number' || typeof args === 'boolean') return String(args)
  if (typeof args === 'object') {
    for (const k of ['frame', 'hex', 'input', 'text', 'data', 'value', 'str', 'message', 'bytes']) {
      const v = args[k]
      if (typeof v === 'string' || typeof v === 'number') return String(v)
    }
    return ''
  }
  return String(args)
}

function pickNumber(args, key, dflt) {
  if (args == null || typeof args !== 'object') return dflt
  const v = args[key]
  if (v == null || v === '') return dflt
  const n = typeof v === 'number' ? v : Number(String(v).trim())
  return Number.isFinite(n) ? n : dflt
}

// ── 十六进制字符串归一化：支持空格 / 0x 前缀 / 逗号 / 冒号 / 短横线分隔 ──
function normalizeHex(input) {
  if (typeof input !== 'string' || input.trim() === '') {
    throw new Error('frame 为空：请提供十六进制帧字符串，例如 "01 0001 000C 0001 F2 ..."')
  }
  const stripped = input.replace(/0[xX]/g, '').replace(/[\s,:_\-|]/g, '')
  if (stripped === '') throw new Error('frame 中未找到任何十六进制字符')
  const bad = stripped.replace(/[0-9a-fA-F]/g, '')
  if (bad !== '') {
    const uniq = Array.from(new Set(bad.split(''))).slice(0, 8).join('')
    throw new Error(`frame 含非十六进制字符: "${uniq}"（只允许 0-9 a-f A-F 及分隔符）`)
  }
  if (stripped.length % 2 !== 0) {
    throw new Error(`十六进制字符数为奇数（${stripped.length}），无法按字节切分`)
  }
  const bytes = []
  for (let i = 0; i < stripped.length; i += 2) bytes.push(parseInt(stripped.slice(i, i + 2), 16))
  return { bytes, hex: stripped.toLowerCase() }
}

// ── 字节序读取 ──
function readUint(bytes, off, size, endian) {
  let v = 0
  if (endian === 'le') {
    for (let i = size - 1; i >= 0; i--) v = v * 256 + bytes[off + i]
  } else {
    for (let i = 0; i < size; i++) v = v * 256 + bytes[off + i]
  }
  return v
}

function xorBytes(bytes, from, to) {
  let x = 0
  for (let i = from; i < to; i++) x ^= bytes[i]
  return x & 0xff
}

function hexOf(bytes, from, to) {
  const out = []
  for (let i = from; i < to; i++) out.push(bytes[i].toString(16).padStart(2, '0'))
  return out.join('')
}

// ── 按给定布局尝试解析一帧 ──
function tryLayout(bytes, cfg, maxLen) {
  const { endian, typeBytes, hckIncludesSof, dckPresent } = cfg
  const headerEnd = 1 + 2 + 2 + typeBytes // SOF 之后：ID(2) + LEN(2) + TYPE(n)
  if (bytes.length < headerEnd + 1) {
    return { parseable: false, fail: '数据不足', detail: `按该布局至少需要 ${headerEnd + 1} 字节（含 HCK），实际仅 ${bytes.length} 字节` }
  }
  const id = readUint(bytes, 1, 2, endian)
  const len = readUint(bytes, 3, 2, endian)
  const type = typeBytes > 0 ? readUint(bytes, 5, typeBytes, endian) : null

  if (len > maxLen) {
    return {
      parseable: false, fail: '长度超限',
      detail: `LEN=${len} 超过上限 ${maxLen} 字节（疑似字节序/字段宽度不符或帧损坏）`,
      id, len, type,
    }
  }
  const hckIndex = headerEnd
  const payloadStart = hckIndex + 1
  const payloadEnd = payloadStart + len
  const needDck = dckPresent && len > 0
  const total = payloadEnd + (needDck ? 1 : 0)

  // 头部已完整，HCK 总能校验（即使是截断帧，也能判断头是否可信 —— 排障关键信息）
  const hckCalc = (~xorBytes(bytes, hckIncludesSof ? 0 : 1, hckIndex)) & 0xff
  const hckGot = bytes[hckIndex]
  const hckOk = hckGot === hckCalc

  if (bytes.length < total) {
    return {
      parseable: false, fail: '数据截断',
      detail: `按该布局需要 ${total} 字节（HCK+payload${needDck ? '+DCK' : ''}），实际仅 ${bytes.length} 字节，缺 ${total - bytes.length} 字节`,
      id, len, type, headerEnd, hckIndex, payloadStart,
      payloadEnd: Math.min(payloadEnd, bytes.length),
      needDck, total, hckGot, hckCalc, hckOk,
      missingBytes: total - bytes.length,
    }
  }

  const dckCalc = needDck ? (~xorBytes(bytes, payloadStart, payloadEnd)) & 0xff : null
  const dckGot = needDck ? bytes[payloadEnd] : null

  return {
    parseable: true,
    id, len, type,
    headerEnd, hckIndex, payloadStart, payloadEnd, needDck, total,
    hckGot, hckCalc, hckOk,
    dckGot, dckCalc, dckOk: needDck ? dckGot === dckCalc : true,
    exact: bytes.length === total,
    trailing: bytes.length > total ? bytes.length - total : 0,
  }
}

// ── 小端 float32 解码（DataView，语言内建，无依赖） ──
function decodeFloat32LE(bytes, start, count) {
  const buf = new ArrayBuffer(count * 4)
  const u8 = new Uint8Array(buf)
  for (let i = 0; i < count * 4; i++) u8[i] = bytes[start + i]
  const dv = new DataView(buf)
  const out = []
  for (let i = 0; i < count; i++) {
    const v = dv.getFloat32(i * 4, true)
    out.push(Number.isFinite(v) ? Math.round(v * 1e6) / 1e6 : null)
  }
  return out
}

// ── 主解析流程 ──
function parseFrame(args) {
  const norm = normalizeHex(pickFrame(args))
  const bytes = norm.bytes

  const maxLen = Math.max(1, Math.min(ABS_MAX_LEN, Math.floor(pickNumber(args, 'max_len', DEFAULT_MAX_LEN))))
  const cfgEndian = pickNumber(args, 'endian_le', 0) ? 'le' : 'be'
  let cfgTypeBytes = Math.floor(pickNumber(args, 'type_bytes', 2))
  if (![0, 1, 2, 4].includes(cfgTypeBytes)) cfgTypeBytes = 2

  const configured = { endian: cfgEndian, typeBytes: cfgTypeBytes, hckIncludesSof: true, dckPresent: true }

  // 1) SOF 检查
  const sofOk = bytes[0] === SOF
  let sofNote = ''
  if (!sofOk) {
    const idx = bytes.indexOf(SOF)
    sofNote = idx > 0
      ? `首字节为 0x${bytes[0].toString(16).padStart(2, '0')}，不是 SOF(0x01)；0x01 首次出现在偏移 ${idx}（可能前导噪声/上一个帧的残尾）`
      : `首字节为 0x${bytes[0].toString(16).padStart(2, '0')}，不是 SOF(0x01)，且整段数据中不含 0x01`
  }

  // 2) 候选布局枚举（含自动纠偏：字节序 / TYPE 宽度 / HCK 是否含 SOF / DCK 是否存在）
  const endians = cfgEndian === 'be' ? ['be', 'le'] : ['le', 'be']
  const typeSizes = Array.from(new Set([cfgTypeBytes, 2, 1, 0, 4]))
  const candidates = []
  for (const endian of endians) {
    for (const typeBytes of typeSizes) {
      for (const hckIncludesSof of [true, false]) {
        for (const dckPresent of [true, false]) {
          const cfg = { endian, typeBytes, hckIncludesSof, dckPresent }
          const r = tryLayout(bytes, cfg, maxLen)
          r.cfg = cfg
          let score = 0
          if (r.parseable) {
            if (r.hckOk) score += 4
            if (r.dckOk) score += 2
            if (r.exact) score += 8
          }
          if (endian === cfgEndian) score += 1
          if (typeBytes === cfgTypeBytes) score += 1
          if (hckIncludesSof === configured.hckIncludesSof) score += 1
          r.score = score
          candidates.push(r)
        }
      }
    }
  }

  // 完整性优先：HCK+DCK 全通过即为可信帧；帧后若还有数据，按“后续帧/噪声”单独提示
  const fullOk = candidates
    .filter((r) => r.parseable && r.hckOk && r.dckOk && r.len <= maxLen)
    .sort((a, b) => b.score - a.score)
  // 非精确长度时，只有“后续数据以 SOF(0x01) 开头（即下一帧）”才认作可信，避免把坏 DCK 当作尾部噪声放过
  const perfect = fullOk.filter((r) => r.exact || bytes[r.total] === SOF)

  const configuredResult = candidates.find(
    (r) => r.cfg.endian === cfgEndian && r.cfg.typeBytes === cfgTypeBytes &&
      r.cfg.hckIncludesSof === true && r.cfg.dckPresent === true,
  )
  const cfgParsed = configuredResult && configuredResult.parseable
  const cfgTrusted = cfgParsed && configuredResult.hckOk // 帧头可信：标准布局下 HCK 通过

  // 选择优先级：
  //  1) 调用方指定布局本身就完整通过 → 直接用（不做多余纠偏）
  //  2) 指定布局下 HCK 通过但 DCK 失败 → 如实报错，不拿别的布局掩盖真实校验失败
  //  3) 指定布局不可用（字节序/字段宽度/约定不符）→ 采用能完整校验通过的候选布局
  let best
  if (cfgParsed && configuredResult.hckOk && configuredResult.dckOk) best = configuredResult
  else if (cfgTrusted) best = configuredResult
  else best = perfect[0] || configuredResult || candidates.slice().sort((a, b) => b.score - a.score)[0]

  const out = {
    valid: false,
    sof_ok: sofOk,
    hck_ok: false,
    dck_ok: false,
    id: null,
    len: null,
    type: null,
    type_name: null,
    payload_hex: '',
    floats: [],
    points: [],
    reason: '',
  }

  const nameOf = (t) => (t != null && TYPE_NAMES[t] ? TYPE_NAMES[t] : null)

  if (!best || !best.parseable) {
    // 无法完整解析（截断/超限）：仍然给出头部诊断信息，便于逐字节排障
    out.reason = `解析失败：${best ? `${best.fail}（${best.detail}）` : '无法按任何已知布局解析'}${sofNote ? `；${sofNote}` : ''}`
    if (best) {
      if (best.id != null) out.id = best.id
      if (best.len != null) out.len = best.len
      if (best.type !== undefined) { out.type = best.type; out.type_name = nameOf(best.type) }
      if (best.hckGot !== undefined) {
        out.hck_ok = !!best.hckOk
        out.hck = {
          got: best.hckGot, calc: best.hckCalc, ok: !!best.hckOk,
          note: best.hckOk ? '头部完整且 HCK 通过，帧头可信，仅 payload 未收全' : 'HCK 不通过，帧头本身可疑',
        }
        out.layout = {
          endian: best.cfg.endian, type_bytes: best.cfg.typeBytes,
          hck_includes_sof: best.cfg.hckIncludesSof, dck_present: best.needDck,
        }
      }
      if (best.payloadStart != null && best.payloadEnd > best.payloadStart) {
        out.payload_hex = hexOf(bytes, best.payloadStart, best.payloadEnd)
        out.payload_hex_note = `payload 仅收到 ${out.payload_hex.length / 2}/${best.len} 字节`
      }
      out.missing_bytes = best.missingBytes
      out.expected_total_bytes = best.total
    }
    out.dck_ok = false
    out.total_bytes = bytes.length
    out.hex = norm.hex
    return out
  }

  // 3) 填充字段
  const { payloadStart, payloadEnd } = best
  out.id = best.id
  out.len = best.len
  out.type = best.type
  out.type_name = best.type != null && TYPE_NAMES[best.type] ? TYPE_NAMES[best.type] : null
  out.hck_ok = best.hckOk
  out.dck_ok = best.dckOk
  out.payload_hex = hexOf(bytes, payloadStart, payloadEnd)
  out.hck = { got: best.hckGot, calc: best.hckCalc, ok: best.hckOk }
  out.dck = best.needDck
    ? { got: best.dckGot, calc: best.dckCalc, ok: best.dckOk }
    : { got: null, calc: null, ok: true, note: 'LEN=0，按 TinyFrame 规范省略 DCK' }
  out.layout = {
    endian: best.cfg.endian,
    type_bytes: best.cfg.typeBytes,
    hck_includes_sof: best.cfg.hckIncludesSof,
    dck_present: best.needDck,
  }
  out.total_bytes = bytes.length
  out.consumed_bytes = best.total
  out.has_trailing = bytes.length > best.total
  out.trailing_bytes = best.trailing || 0
  out.trailing_hex = bytes.length > best.total ? hexOf(bytes, best.total, bytes.length) : ''
  out.hex = norm.hex

  // 4) 点云解码：payload ≥ 12 字节 → 小端 float32 × (x/y/z/speed)
  const fullFloats = Math.floor(best.len / 4)
  if (fullFloats > 0) {
    out.floats = decodeFloat32LE(bytes, payloadStart, fullFloats)
    if (best.len >= 12) {
      const nPoints = Math.floor(fullFloats / 4)
      const pts = []
      for (let i = 0; i < nPoints; i++) {
        const b = i * 4
        pts.push({ x: out.floats[b], y: out.floats[b + 1], z: out.floats[b + 2], speed: out.floats[b + 3] })
      }
      out.points = pts
      out.point_count = nPoints
    }
    if (best.len % 4 !== 0) out.float_remainder_bytes = best.len % 4
  } else if (best.len > 0) {
    out.float_note = `payload 仅 ${best.len} 字节（<4），不足以解码 float32`
  }

  // 5) 结论与提示
  const layoutAuto = best.cfg.endian !== cfgEndian || best.cfg.typeBytes !== cfgTypeBytes
  const layoutNotes = []
  if (layoutAuto) {
    layoutNotes.push(
      `已自动纠偏布局：endian=${best.cfg.endian}, type_bytes=${best.cfg.typeBytes}` +
      `（调用方指定 endian=${cfgEndian}, type_bytes=${cfgTypeBytes}）`,
    )
  }
  if (!best.cfg.hckIncludesSof) layoutNotes.push('HCK 覆盖范围不含 SOF（标准 TinyFrame 约定，LD6002B 实测固件通常含 SOF）')
  if (!best.needDck && best.len > 0) layoutNotes.push('DCK 缺失但 LEN>0（帧被截断或非标准实现）')
  if (!(sofOk && best.hckOk && best.dckOk)) {
    const alt = perfect.find((r) => r !== best)
    if (alt) {
      layoutNotes.push(
        `按调用方指定布局校验未通过，但换成 endian=${alt.cfg.endian}, type_bytes=${alt.cfg.typeBytes}, ` +
        `hck_includes_sof=${alt.cfg.hckIncludesSof} 可完整校验通过（若实际固件约定不同，请显式指定参数）`,
      )
    }
  }
  if (out.trailing_hex) layoutNotes.push(`帧后另有 ${out.trailing_hex.length / 2} 字节数据（可能是下一帧或线路噪声，未计入本帧）：${out.trailing_hex}`)
  if (best.len >= 20 && best.len % 20 === 0) layoutNotes.push(`payload 为 20 字节整数倍，符合 LD6002B 目标记录格式（x/y/z/dop_idx/cluster_id ×${best.len / 20}）`)
  if (best.len >= 12 && best.len % 12 !== 0) layoutNotes.push(`payload 长度 ${best.len} 不是 12 的整数倍，末尾 ${best.len % 12} 字节未构成完整点`)

  // valid 表示「本帧自身完整可信」：SOF 正确 + HCK/DCK 双校验通过
  out.valid = sofOk && best.hckOk && best.dckOk

  const ckText = `HCK ${best.hckOk ? 'OK' : `FAIL(got 0x${best.hckGot.toString(16).padStart(2, '0')} / calc 0x${best.hckCalc.toString(16).padStart(2, '0')})`}` +
    `, DCK ${!best.needDck ? 'N/A(LEN=0)' : best.dckOk ? 'OK' : `FAIL(got 0x${best.dckGot.toString(16).padStart(2, '0')} / calc 0x${best.dckCalc.toString(16).padStart(2, '0')})`}`
  out.reason = (out.valid ? '帧解析成功：' : '帧解析失败：') +
    `SOF ${sofOk ? 'OK(0x01)' : `FAIL(${sofNote})`}；` +
    `id=${out.id}, len=${out.len}, type=0x${(out.type == null ? 0 : out.type).toString(16).padStart(4, '0')}${out.type_name ? `(${out.type_name})` : ''}；${ckText}` +
    `；共 ${bytes.length} 字节，解析消耗 ${best.total} 字节` +
    (layoutNotes.length ? `。提示：${layoutNotes.join('；')}` : '')

  return out
}

function safe(fn) {
  return (args) => {
    try {
      return String(fn(args))
    } catch (e) {
      const msg = e && e.message ? e.message : String(e)
      return JSON.stringify(
        { valid: false, hck_ok: false, dck_ok: false, id: null, len: null, type: null, payload_hex: '', floats: [], reason: `错误: ${msg}` },
        null, 2,
      )
    }
  }
}

const parseLd6002bFrame = safe((args) => JSON.stringify(parseFrame(args), null, 2))

export function apply(ctx, config) {
  const TOOLS = {
    parse_ld6002b_frame: {
      desc: '解析 LD6002B 毫米波雷达 TinyFrame 十六进制帧：校验 SOF(0x01)/长度上限/双校验和（HCK/DCK 均为 XOR 后取反），输出 id/len/type/payload；payload ≥12 字节时按小端 float32 解码为点云（x/y/z/speed）',
      fn: parseLd6002bFrame,
    },
  }
  for (const [toolName, tool] of Object.entries(TOOLS)) {
    ctx.tools.register({
      name: toolName,
      description: tool.desc,
      parameters: {
        type: 'object',
        properties: {
          frame: { type: 'string', description: '十六进制帧字符串，可含空格/0x 前缀/逗号分隔，例如 "01 0001 000C 0001 F2 ..."' },
          max_len: { type: 'number', description: 'payload 长度上限（字节），默认 4096' },
          type_bytes: { type: 'number', description: 'TYPE 字段字节数：0/1/2/4，默认 2（LD6002B）' },
          endian_le: { type: 'number', description: '非 0 表示按小端读取 ID/LEN/TYPE，默认 0（大端，LD6002B）' },
        },
        required: ['frame'],
      },
      execute: tool.fn,
      output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    })
  }
}
