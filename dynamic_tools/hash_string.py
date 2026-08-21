#!/usr/bin/env python3
"""动态工具：字符串哈希计算（MD5 + SHA256）。

纯本机计算，无需联网。模块格式：TOOL_DEF + execute(args) -> str（fall-mcp 动态工具规范）。
"""

import hashlib
import json

TOOL_DEF = {
    "name": "hash_string",
    "description": "字符串哈希计算工具：输入任意字符串（text），在本机计算并返回其 MD5 和 SHA256 哈希值（均为 32/64 位十六进制小写）。纯本地计算，无需联网，不访问任何外部接口，不读写本地文件。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要计算哈希的任意字符串，如 'hello world'。",
            }
        },
        "required": ["text"],
    },
}


def execute(args: dict) -> str:
    try:
        text = args.get("text")
        if text is None:
            return json.dumps({"ok": False, "error": "缺少必需参数 text"}, ensure_ascii=False)
        if not isinstance(text, str):
            text = str(text)
        if text == "":
            return json.dumps({"ok": False, "error": "text 不能为空字符串"}, ensure_ascii=False)

        data = text.encode("utf-8")
        md5 = hashlib.md5(data).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()

        return json.dumps(
            {
                "ok": True,
                "text": text,
                "md5": md5,
                "sha256": sha256,
                "md5_length": len(md5),
                "sha256_length": len(sha256),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"计算失败: {exc}"}, ensure_ascii=False)
