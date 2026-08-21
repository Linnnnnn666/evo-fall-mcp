#!/usr/bin/env python3
"""动态工具：查询今日黄金价格（现货黄金 / 黄金T+D / 沪金主力期货）。

数据来源：金投网行情接口 api.jijinhao.com（国内公开行情接口，中国大陆可直接访问，
金投网官网 www.cngold.org 首页实时行情同源）：
    https://api.jijinhao.com/quoteCenter/realTime.htm?codes=JO_92233,JO_9753,JO_165732
返回三种主流黄金报价：
  - 现货黄金（伦敦金，XAU，美元/盎司）
  - 黄金T+D（上海黄金交易所 Au(T+D)，元/克）
  - 沪金主力（上海期货交易所沪金主力合约，元/克）
每项包含最新价、涨跌值、涨跌幅、开盘价、最高价、最低价及行情时间。
可选参数 type 限定查询范围：all（全部，默认）/ spot（现货黄金）/ td（黄金T+D）/
futures（沪金主力）。
模块格式：TOOL_DEF + execute(args) -> str（fall-mcp 动态工具规范）。
"""

import json
import re
from datetime import datetime, timedelta, timezone

import httpx

TOOL_DEF = {
    "name": "query_gold_price",
    "description": (
        "查询今日黄金价格。数据来源为金投网（www.cngold.org）同源的国内公开行情接口 "
        "api.jijinhao.com（中国大陆可直接访问），返回现货黄金（伦敦金，美元/盎司）、"
        "黄金T+D（上海黄金交易所，元/克）、沪金主力（上海期货交易所，元/克）三种黄金报价，"
        "每项包含最新价、涨跌值、涨跌幅、开盘价、最高价、最低价与行情时间。"
        "可选参数 type 可限定只查其中一类：all（全部，默认）/ spot（现货黄金）/ "
        "td（黄金T+D）/ futures（沪金主力）。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["all", "spot", "td", "futures"],
                "description": "查询范围：all 全部（默认）、spot 现货黄金、td 黄金T+D、futures 沪金主力",
            }
        },
    },
}

_URL = "https://api.jijinhao.com/quoteCenter/realTime.htm"

# 金投网行情代码：现货黄金（伦敦金）、黄金T+D、沪金主力
_CODES = {
    "spot": "JO_92233",      # 现货黄金（伦敦金，美元/盎司）
    "td": "JO_9753",         # 黄金T+D（上海黄金交易所，元/克）
    "futures": "JO_165732",  # 沪金主力（上海期货交易所，元/克）
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cngold.org/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_BJ = timezone(timedelta(hours=8))


def _parse_jsonp(text: str) -> dict:
    """从 JSONP 响应（var quote_json = {...}）中提取 JSON 对象。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("接口返回格式异常，未找到 JSON 数据")
    return json.loads(text[start : end + 1])


def _fmt_time(ts) -> str:
    """毫秒时间戳转为北京时间字符串，异常时返回 None。"""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=_BJ).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def execute(args: dict) -> str:
    """查询今日黄金价格，返回 JSON 字符串（成功 ok:true，失败 ok:false 含 error）。"""
    try:
        args = args or {}
        qtype = str(args.get("type", "all") or "all").strip().lower()
        if qtype != "all" and qtype not in _CODES:
            return json.dumps(
                {"ok": False, "error": f"参数 type 取值无效: {qtype}（可选 all/spot/td/futures）"},
                ensure_ascii=False,
            )
        codes = list(_CODES.values()) if qtype == "all" else [_CODES[qtype]]

        r = httpx.get(_URL, params={"codes": ",".join(codes)}, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        data = _parse_jsonp(r.text)

        items = []
        for code in codes:
            q = data.get(code)
            if not isinstance(q, dict) or q.get("q63") is None:
                continue
            pct = q.get("q80")
            items.append(
                {
                    "name": q.get("showName"),
                    "code": q.get("showCode") or code,
                    "unit": q.get("unit"),
                    "price": q.get("q63"),
                    "change": q.get("q70"),
                    "change_pct": round(pct, 2) if isinstance(pct, (int, float)) else pct,
                    "open": q.get("q1"),
                    "high": q.get("q3"),
                    "low": q.get("q4"),
                    "time": _fmt_time(q.get("time")),
                }
            )
        if not items:
            return json.dumps({"ok": False, "error": "接口未返回有效的黄金行情数据"}, ensure_ascii=False)

        return json.dumps(
            {
                "ok": True,
                "date": datetime.now(_BJ).strftime("%Y-%m-%d"),
                "source": "金投网行情接口（https://api.jijinhao.com/quoteCenter/realTime.htm）",
                "items": items,
            },
            ensure_ascii=False,
        )
    except httpx.TimeoutException:
        return json.dumps({"ok": False, "error": "请求超时（15秒），请稍后重试"}, ensure_ascii=False)
    except httpx.HTTPStatusError as exc:
        return json.dumps({"ok": False, "error": f"HTTP 错误: {exc.response.status_code}"}, ensure_ascii=False)
    except httpx.RequestError as exc:
        return json.dumps({"ok": False, "error": f"网络请求失败: {exc}"}, ensure_ascii=False)
    except (ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"ok": False, "error": f"数据解析失败: {exc}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"未知错误: {exc}"}, ensure_ascii=False)
