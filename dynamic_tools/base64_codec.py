#!/usr/bin/env python3
"""动态工具：Base64 编解码（纯本机计算，不联网）。

模块格式：TOOL_DEF + execute(args) -> str（fall-mcp 动态工具规范）。
不联网、不读写本地文件，仅用标准库 json/re/typing 手工实现 RFC 4648 标准 Base64（含 '=' 填充），
不依赖 base64 模块。
"""

import json
import re
import typing

_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0YOUR_WIFI_PASSWORD9+/"
_B64_REVERSE = {ch: i for i, ch in enumerate(_B64_ALPHABET)}
# 标准 Base64 串：主体字符 + 末尾最多 2 个填充 '='
_B64_FULL_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")

TOOL_DEF = {
    "name": "base64_codec",
    "description": "Base64 编解码工具（纯本机计算，不联网、不读写文件）：输入任意字符串（text），返回其标准 Base64（RFC 4648）编码结果 encoded；若该输入本身是合法 Base64 编码串（仅含 A-Za-z0-9+/，末尾可有 0-2 个 '='，长度合规），则同时返回解码后的原文 decoded。可用于文本与 Base64 互转、校验某字符串是否为合法 Base64、查看某串解码后的内容。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "任意字符串：普通文本（如 'Hello 世界'）将返回其 UTF-8 的 Base64 编码结果；若输入本身是合法 Base64 编码串（如 'SGVsbG8gV29ybGQ='），将同时返回编码结果与解码结果。",
            }
        },
        "required": ["text"],
    },
}


def _b64_encode(data: bytes) -> str:
    """标准 Base64 编码（RFC 4648，3 字节一组，末尾补 '='）。"""
    out: typing.List[str] = []
    for i in range(0, len(data), 3):
        chunk = data[i : i + 3]
        # 不足 3 字节时左对齐补零到 24 位
        n = int.from_bytes(chunk, "big") << (8 * (3 - len(chunk)))
        pad = 3 - len(chunk)
        for j in range(4):
            if j < 4 - pad:
                out.append(_B64_ALPHABET[(n >> (18 - 6 * j)) & 0x3F])
            else:
                out.append("=")
    return "".join(out)


def _b64_decode(text: str) -> bytes:
    """标准 Base64 解码（兼容省略末尾 '=' 的无填充写法）。"""
    if not _B64_FULL_RE.match(text) or len(text) % 4 == 1:
        raise ValueError("不是合法 Base64：含非法字符或长度非法（mod 4 为 1）")
    body = text.rstrip("=")
    if len(body) % 4 == 1:
        raise ValueError("不是合法 Base64：长度非法")
    out = bytearray()
    for i in range(0, len(body), 4):
        group = body[i : i + 4]
        n = 0
        for ch in group:
            n = (n << 6) | _B64_REVERSE[ch]
        n <<= (4 - len(group)) * 6  # 左对齐到 24 位
        nbytes = len(group) * 6 // 8
        out.extend(n.to_bytes(3, "big")[:nbytes])
    return bytes(out)


def execute(args: dict) -> str:
    try:
        text = args.get("text")
        if text is None:
            return json.dumps({"ok": False, "error": "缺少必需参数 text"}, ensure_ascii=False)
        if not isinstance(text, str):
            text = str(text)

        result = {
            "ok": True,
            "text": text,
            "encoded": _b64_encode(text.encode("utf-8")),
        }

        # 输入若为合法 Base64 编码串，则同时给出解码结果
        if _B64_FULL_RE.match(text) and len(text) % 4 != 1:
            try:
                raw = _b64_decode(text)
                try:
                    result["decoded"] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    result["decoded_hex"] = raw.hex()
                    result["decode_note"] = "解码得到非 UTF-8 字节（疑似二进制数据），已用十六进制返回"
            except ValueError as exc:
                result["decode_note"] = str(exc)

        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Base64 处理失败: {exc}"}, ensure_ascii=False)


if __name__ == "__main__":
    # 自测（不联网）
    for s in ["", "f", "fo", "foo", "Hello", "你好，世界", "SGVsbG8=", "5L2g5aW9", "test", "/w==", "a==="]:
        print(s, "->", execute({"text": s}))
    print(execute({}))
