#!/usr/bin/env python3
"""
Fall-monitor MCP server for the voice board (phase 5).

Implements the MCP 2024-11-05 JSON-RPC protocol over WebSocket, matching what
xiaozhi-server's MCP endpoint client speaks:
  initialize -> {protocolVersion, capabilities, serverInfo}
  tools/list -> {tools:[{name, description, inputSchema}]}
  tools/call -> {content:[{type:"text", text}]}

Run (Aliyun server, localhost only):
    python3 mcp_server.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import websockets

FALL_API = os.environ.get("FALL_API", "http://127.0.0.1:8000")
FALL_TOKEN = os.environ.get("FALL_APP_TOKEN", "YOUR_FALL_APP_TOKEN")
DEFAULT_DEVICE = "esp32s3-cam-01"
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8002"))

# 动态工具目录：每个 .py 模块需提供 TOOL_DEF（name/description/inputSchema）与 execute(args)->str
# 热加载：tools/list 与 tools/call 时实时扫描，新增工具无需重启本服务
DYNAMIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dynamic_tools")

TOOLS = [
    {
        "name": "query_fall_status",
        "description": (
            "查询跌倒设备的最新跌倒事件并判断是否有近期跌倒。"
            "当用户询问跌倒/安全/家人情况，或需要告知用户最近是否发生跌倒时调用。"
            "结果中 has_recent_fall=true 时应主动告知用户检测到跌倒并安抚。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "跌倒设备 ID，默认 esp32s3-cam-01"},
                "recent_minutes": {"type": "integer", "description": "只看最近多少分钟内的跌倒，默认 60"},
            },
            "required": [],
        },
    },
    {
        "name": "query_device_status",
        "description": "查询跌倒设备在线状态、固件版本、最后在线时间。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "跌倒设备 ID，默认 esp32s3-cam-01"},
            },
            "required": [],
        },
    },
    {
        "name": "query_fall_history",
        "description": (
            "查询跌倒设备最近一段时间的历史跌倒记录（多条）。"
            "当用户询问'昨天/前几天/最近有没有跌倒'、'跌倒过几次'、"
            "'最近一次是什么时候'等需要多条记录的问题时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "跌倒设备 ID，默认 esp32s3-cam-01"},
                "hours": {"type": "integer", "description": "查询最近多少小时内的记录，默认 24"},
                "limit": {"type": "integer", "description": "最多返回几条，默认 10，最大 50"},
            },
            "required": [],
        },
    },
    {
        "name": "query_fall_stats",
        "description": (
            "统计跌倒设备最近 N 天的跌倒次数（按天分布）。"
            "当用户询问'这周/最近几天摔了几次'、'跌倒情况怎么样'等统计问题时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "跌倒设备 ID，默认 esp32s3-cam-01"},
                "days": {"type": "integer", "description": "统计最近多少天，默认 7，最大 30"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_create_tool",
        "description": (
            "工具工厂：当用户要求'创建/制作/开发一个工具'来查询某类信息（如'帮我做个工具查B站播放量'）时调用。"
            "参数 description 为用户的功能需求描述。创建成功后提示用户：需要重新唤醒语音助手（说'你好小安'）后新工具才会生效。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "用户想创建的工具功能描述，如'查询B站某个视频的播放量'"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "dev_delete_tool",
        "description": (
            "删除之前通过工具工厂创建的自定义工具。"
            "当用户说'删掉XX工具'、'不要XX功能了'时调用。参数 name 为要删除的工具名。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要删除的工具名，如 query_bili_video_stats"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "dev_dispatch_task",
        "description": (
            "任务派发：当用户提出一个需要服务器执行的任务时调用（如'分析服务器磁盘''写个脚本备份数据库'"
            "'汇总一下跌倒数据''查一下XX并生成报告'）。参数 task 为任务描述。"
            "任务会在后台由AI智能体执行，立即返回确认；完成后用户可问'查询任务结果'获取结果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要执行的任务描述，越具体越好"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "dev_query_task",
        "description": (
            "查询已派发任务的状态和结果。当用户问'任务完成了吗''查询任务结果''刚才那个任务怎么样了'时调用。"
            "不传 task_id 时返回最近一个任务的结果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "可选，任务ID（如 0819035512）；不传则查最近任务"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_list_tools",
        "description": (
            "列出工具注册表里所有已注册工具及其描述、调用次数。"
            "当用户问'有哪些工具''都有什么功能'时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dev_register_board",
        "description": (
            "登记一块新的开发板/设备（接入新板流程）。当用户说'我有一块新板子要接入'"
            "'新板子怎么弄'时调用，之后按返回指引接线烧录。"
            "参数：device_id（板子唯一标识，如 board-s3-36ac）、model（型号）、mac（芯片 MAC）、notes（备注）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "板子唯一标识（小写，如 board-s3-36ac）"},
                "model": {"type": "string", "description": "型号/描述，如 'ESP32-S3 开发板（无外设）'"},
                "mac": {"type": "string", "description": "芯片 MAC 地址（可后补）"},
                "notes": {"type": "string", "description": "备注（如接线位置、用途）"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "dev_list_boards",
        "description": (
            "列出所有已登记接入的开发板：型号、MAC、当前固件版本、能力、最近在线时间。"
            "当用户问'有哪些板子''板子都在吗''有几块板'时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dev_ota_deploy",
        "description": (
            "固件迭代部署：当用户要求修改/增加某块外接设备的功能（如'跌倒后LED闪烁3次'"
            "'把报警音调大''给新板子加个温度传感器功能'）时调用。AI 会修改固件代码、编译、发布新版本，"
            "目标板会自动 OTA 升级重启。参数 requirement 为功能需求描述，board_id 指定目标板"
            "（缺省 fall-board 跌倒板；新板用 dev_list_boards 查）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "description": "功能修改需求描述，越具体越好"},
                "board_id": {"type": "string", "description": "目标板 device_id，缺省 fall-board"},
            },
            "required": ["requirement"],
        },
    },
    {
        "name": "dev_flash_supervise",
        "description": (
            "烧录监工：派 DSH 去盯一次云端烧录的全过程——发指令→读事件流→分析日志→"
            "关键节点语音播报→失败自动重试（最多 2 次）。当用户说'帮我盯着烧录''烧的时候提醒我'"
            "或需要完整监工一次烧录时调用。参数 device 为目标设备 id。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "目标设备 id，如 oled-display"},
            },
            "required": ["device"],
        },
    },
    {
        "name": "dev_first_flash",
        "description": (
            "新板接入（云端编排）：把一块新 ESP32 板从白片接入系统——自动完成"
            "生成板配置→编译引导固件→归档 OTA 仓库→向烧录板发指令→监控烧录→遥测自检确认→登记注册表→播报结果。"
            "当用户说'接入新板''新板子怎么弄''这块板要接入'时调用。参数 device_id 为新板唯一标识"
            "（如 board-s3-477c），mac 可填板子 MAC。用户只需物理接线（4 根线到烧录板）+ BOOT/RST。"
            "进度用 dev_first_flash_status 查询。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "新板唯一标识，如 board-s3-477c"},
                "model": {"type": "string", "description": "板型描述，如 ESP32-S3 开发板"},
                "mac": {"type": "string", "description": "板子 MAC 地址（可选）"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "dev_first_flash_status",
        "description": (
            "查询新板接入流程的进度/结果（配置→编译→归档→烧录→遥测→登记各步骤状态）。"
            "当用户问'接入到哪一步了''新板接好了吗''接入成功了吗'时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "要查询的板 id（可选，默认最近 3 条）"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_talk_to_dsh",
        "description": (
            "反馈通道：把用户对后台 DSH 任务确认问题的回答转达给任务（写回确认队列）。"
            "当用户回答了系统之前主动播报的确认问题（如'任务XX需要向你确认：...'），"
            "或用户想给正在执行的任务补充信息/指示时调用。参数 text 为用户回答内容，task_id 可省略（自动匹配最近任务）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "用户回答的内容"},
                "task_id": {"type": "string", "description": "目标任务 id（可选，默认最近待确认任务）"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "dev_query_confirm",
        "description": (
            "查询任务确认队列：有哪些 DSH 提问在等待用户回答、用户是否已回复。"
            "当用户问'有什么需要确认的吗''任务在等什么'时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "目标任务 id（可选，默认最近 5 个任务）"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_ota_rollback",
        "description": "固件一键回滚：把设备 OTA 指向历史版本并触发升级（默认回滚到当前版本的前一个，可指定版本）。固件新版本出问题时的快速恢复通道。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 id，如 board-s3-5798 / 5798"},
                "version": {"type": "string", "description": "目标版本（可选，默认当前版本的前一个）"}
            },
            "required": ["device_id"]
        }
    },
    {
        "name": "dev_enable_developer_mode",
        "description": "启用开发者模式（用户说'启用开发者模式'时调用）：开放 ESP32 开发板开发功能（固件部署/烧录/运行时指令/新板接入/回滚/自验收）。工具工厂与语音链路不受影响。",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "dev_disable_developer_mode",
        "description": "关闭开发者模式（用户说'关闭开发者模式'时调用）：收回开发板开发功能。",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "dev_developer_mode_status",
        "description": "查询开发者模式当前状态（启用/关闭）。",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "dev_self_verify",
        "description": "功能级遥测自验收：读设备最新遥测，断言字段满足条件（如 led>=1 灯已亮、fw==0.17.0 已升级）。部署后先自验收，通过则播报已验证；失败才需要用户确认。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 id，如 board-s3-5798 / 5798"},
                "field": {"type": "string", "description": "遥测字段名，如 led / fw / uptime_s / temp_c / rpm"},
                "op": {"type": "string", "description": "比较符：== != > < >= <=", "default": ">="},
                "value": {"type": "string", "description": "预期值，如 1 / 0.17.0 / 45"},
                "timeout_s": {"type": "integer", "description": "超时秒数（5-300，默认 60）"}
            },
            "required": ["device_id", "field"]
        }
    },
    {
        "name": "dev_board_command",
            "description": "向板子发送运行时指令（led_on/led_off/ota_check/reboot，MQTT 秒级生效）。开关灯、触发 OTA 检查等即时操作用这个；需要改固件逻辑才用 dev_ota_deploy。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "设备 id，如 board-s3-5798 / 5798"},
                    "command": {"type": "string", "description": "指令：led_on / led_off / ota_check / reboot"}
                },
                "required": ["device_id", "command"]
            }
        },
        {
        "name": "dev_speak",
        "description": (
            "厨师开口：把一段文本推送给语音服务员（xiaozhi），LLM 润色后用 TTS 播报给用户。"
            "当 DSH 完成任务/需要向用户播报进度/想和用户说话时调用（如'任务完成了告诉用户'）。"
            "参数 text 为要播报的内容（口语化），device_id 可选指定设备。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要播报的文本内容"},
                "device_id": {"type": "string", "description": "指定语音设备 id（可选，默认任意在线设备）"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "dev_flash_start",
        "description": (
            "云端烧录：向烧录板发出 flash_start 指令，给目标设备烧录固件。"
            "当用户要求'给XX板烧固件''把固件烧到板子上'时调用。参数 device 为目标设备 id"
            "（如 oled-display / fall-board，需 /firmware/<id>/latest.json 存在），"
            "或 url+size 直接指定固件地址。发出后用 dev_flash_status 查询事件流水监工。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "目标设备 id，如 oled-display"},
                "url": {"type": "string", "description": "直接指定 full.bin 地址（与 device 二选一）"},
                "size": {"type": "integer", "description": "url 模式下的固件字节数"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_flash_status",
        "description": (
            "查询烧录任务记录器：返回最近的烧录事件/日志/遥测流水（事件流=唯一事实源）。"
            "当用户问'烧录进度怎么样''烧到哪了''刚才烧录失败了吗'，或监工需要分析事件流时调用。"
            "不干扰烧录板工作。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回最近多少条记录，默认 20，最大 100"},
            },
            "required": [],
        },
    },
    {
        "name": "dev_flash_abort",
        "description": (
            "中断当前烧录流程（向烧录板发 abort 指令）。"
            "当烧录卡住/目标板接线异常/用户要求取消/监工判断需要干预时调用。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

SERVER_INFO = {"name": "fall-monitor-mcp", "version": "1.0.0"}


def _get(path: str) -> dict:
    headers = {"Authorization": f"Bearer {FALL_TOKEN}", "Accept": "application/json"}
    r = httpx.get(FALL_API + path, headers=headers, timeout=8)
    r.raise_for_status()
    return r.json()


def query_fall_status(args: dict) -> str:
    device_id = args.get("device_id", DEFAULT_DEVICE)
    recent_minutes = int(args.get("recent_minutes", 60))
    try:
        data = _get(f"/api/v1/devices/{device_id}/events?limit=1")
        events = data.get("events") or []
        if not events:
            return json.dumps(
                {"ok": True, "device_id": device_id, "has_recent_fall": False,
                 "latest_event": None, "note": "服务器暂无该设备的跌倒记录"},
                ensure_ascii=False,
            )
        ev = events[-1]
        now = datetime.now(timezone.utc)
        try:
            ts = datetime.fromisoformat(ev["server_received_at"].replace("Z", "+00:00"))
            minutes_ago = int((now - ts).total_seconds() // 60)
        except Exception:
            minutes_ago = -1
        is_fall = ev.get("event_type", "fall_confirmed") == "fall_confirmed"
        has_recent = is_fall and 0 <= minutes_ago <= recent_minutes
        return json.dumps(
            {
                "ok": True,
                "device_id": device_id,
                "has_recent_fall": has_recent,
                "latest_event": {
                    "event_id": ev.get("event_id"),
                    "event_type": ev.get("event_type"),
                    "time": ev.get("server_received_at"),
                    "minutes_ago": minutes_ago,
                    "radar": ev.get("radar"),
                    "vision": ev.get("vision"),
                },
                "note": "has_recent_fall=true 时应主动告知用户检测到跌倒并安抚；false 则如实回答暂无跌倒",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def query_device_status(args: dict) -> str:
    device_id = args.get("device_id", DEFAULT_DEVICE)
    try:
        return json.dumps(_get(f"/api/v1/devices/{device_id}/status"), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def query_fall_history(args: dict) -> str:
    device_id = args.get("device_id", DEFAULT_DEVICE)
    hours = int(args.get("hours", 24))
    limit = min(int(args.get("limit", 10)), 50)
    try:
        data = _get(f"/api/v1/devices/{device_id}/events?limit={limit}")
        events = data.get("events") or []
        now = datetime.now(timezone.utc)
        records = []
        for ev in events:
            ts = _parse_ts(ev.get("server_received_at", ""))
            if ts is None:
                continue
            if (now - ts).total_seconds() > hours * 3600:
                continue
            if ev.get("event_type", "fall_confirmed") != "fall_confirmed":
                continue
            records.append({
                "event_id": ev.get("event_id"),
                "time": ev.get("server_received_at"),
                "minutes_ago": int((now - ts).total_seconds() // 60),
                "radar": ev.get("radar"),
                "vision": ev.get("vision"),
            })
        records.sort(key=lambda r: r["time"], reverse=True)
        return json.dumps(
            {"ok": True, "device_id": device_id, "hours": hours,
             "fall_count": len(records), "records": records[:limit]},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def query_fall_stats(args: dict) -> str:
    device_id = args.get("device_id", DEFAULT_DEVICE)
    days = min(int(args.get("days", 7)), 30)
    try:
        # 拉最近最多 200 条（服务器 limit 上限），按天聚合 fall_confirmed
        data = _get(f"/api/v1/devices/{device_id}/events?limit=200")
        events = data.get("events") or []
        now = datetime.now(timezone.utc)
        per_day: dict[str, int] = {}
        for ev in events:
            if ev.get("event_type", "fall_confirmed") != "fall_confirmed":
                continue
            ts = _parse_ts(ev.get("server_received_at", ""))
            if ts is None:
                continue
            if (now - ts).total_seconds() > days * 86400:
                continue
            day = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
            per_day[day] = per_day.get(day, 0) + 1
        # 补齐 days 内每一天（含 0 次）
        daily = []
        for i in range(days - 1, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily.append({"date": day, "count": per_day.get(day, 0)})
        return json.dumps(
            {"ok": True, "device_id": device_id, "days": days,
             "total_falls": sum(per_day.values()), "daily": daily},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def call_tool(name: str, args: dict) -> dict:
    try:
        return _call_tool_impl(name, args)
    except Exception as _e:
        print(f"[mcp] 工具 {name} 执行异常: {_e}", flush=True)
        import traceback as _tb
        print(_tb.format_exc(), flush=True)
        return {"content": [{"type": "text", "text": f"工具 {name} 执行出错: {_e}（已记录日志，不会断开连接）"}]}


# 开发板开发功能（需语音启用开发者模式）；查询类/工具工厂/语音链路不受门控
GATED_DEV_TOOLS = {
    "dev_board_command", "dev_ota_deploy", "dev_ota_rollback",
    "dev_flash_supervise", "dev_flash_start", "dev_flash_status", "dev_flash_abort",
    "dev_first_flash", "dev_first_flash_status", "dev_register_board", "dev_self_verify",
}


def _call_tool_impl(name: str, args: dict) -> dict:
    if name in GATED_DEV_TOOLS:
        try:
            from dev_tool_factory import _dev_mode_enabled
            if not _dev_mode_enabled():
                return {"content": [{"type": "text", "text": (
                    "开发者模式未启用，开发板开发功能已锁定。"
                    "请先让用户说'启用开发者模式'，再执行此操作。"
                    "（查询板子状态等只读功能不受影响）" )}]}
        except Exception:
            pass
    if name == "query_fall_status":
        text = query_fall_status(args or {})
    elif name == "query_device_status":
        text = query_device_status(args or {})
    elif name == "query_fall_history":
        text = query_fall_history(args or {})
    elif name == "query_fall_stats":
        text = query_fall_stats(args or {})
    elif name == "dev_create_tool":
        from dev_tool_factory import create_tool
        import threading as _threading
        requirement = (args or {}).get("description", "")
        # 立即返回确认，DSH 在后台线程生成（语音板等待上限 30s，生成需 1-2 分钟）
        def _bg():
            try:
                print(f"[fall-mcp] dev_create_tool background start: {requirement}", flush=True)
                result = create_tool(requirement)
                print(f"[fall-mcp] dev_create_tool background done: {result[:200]}", flush=True)
                try:
                    from dev_tool_factory import dev_speak
                    dev_speak(result[:120])
                except Exception as _spk:
                    print(f"[fall-mcp] dev_create_tool 播报失败: {_spk}", flush=True)
            except Exception as exc:
                print(f"[fall-mcp] dev_create_tool background ERROR: {exc}", flush=True)
        _threading.Thread(target=_bg, daemon=True).start()
        text = (
            "好的，正在为您生成工具，大约需要一到两分钟。"
            "生成完成后，重新唤醒语音助手，说'你好小安'，新工具就能用了。"
        )
    elif name == "dev_delete_tool":
        from dev_tool_factory import delete_tool
        text = delete_tool((args or {}).get("name", ""))
    elif name == "dev_dispatch_task":
        from dev_tool_factory import dispatch_task
        text = dispatch_task((args or {}).get("task", ""))
    elif name == "dev_query_task":
        from dev_tool_factory import query_task
        text = query_task((args or {}).get("task_id") or None)
    elif name == "dev_list_tools":
        from dev_tool_factory import list_tools
        text = list_tools()
    elif name == "dev_ota_deploy":
        from dev_tool_factory import deploy_firmware
        text = deploy_firmware(
            (args or {}).get("requirement", ""),
            board_id=(args or {}).get("board_id", "fall-board"),
        )
    elif name == "dev_flash_supervise":
        from dev_tool_factory import dev_flash_supervise
        text = dev_flash_supervise((args or {}).get("device", ""))
    elif name == "dev_first_flash":
        from dev_tool_factory import dev_first_flash
        text = dev_first_flash(
            (args or {}).get("device_id", ""),
            model=(args or {}).get("model", ""),
            mac=(args or {}).get("mac", ""),
            notes=(args or {}).get("notes", ""),
        )
    elif name == "dev_first_flash_status":
        from dev_tool_factory import dev_first_flash_status
        text = dev_first_flash_status((args or {}).get("device_id", ""))
    elif name == "dev_talk_to_dsh":
        from dev_tool_factory import dev_talk_to_dsh
        text = dev_talk_to_dsh(
            (args or {}).get("task_id", ""),
            text=(args or {}).get("text", ""),
        )
    elif name == "dev_query_confirm":
        from dev_tool_factory import dev_query_confirm
        text = dev_query_confirm((args or {}).get("task_id", ""))
    elif name == "dev_ota_rollback":
        from dev_tool_factory import dev_ota_rollback
        text = dev_ota_rollback(
            (args or {}).get("device_id", ""),
            (args or {}).get("version", ""),
        )
    elif name == "dev_enable_developer_mode":
        from dev_tool_factory import dev_enable_developer_mode
        text = dev_enable_developer_mode()
    elif name == "dev_disable_developer_mode":
        from dev_tool_factory import dev_disable_developer_mode
        text = dev_disable_developer_mode()
    elif name == "dev_developer_mode_status":
        from dev_tool_factory import dev_developer_mode_status
        text = dev_developer_mode_status()
    elif name == "dev_self_verify":
        from dev_tool_factory import dev_self_verify
        text = dev_self_verify(
            (args or {}).get("device_id", ""),
            (args or {}).get("field", ""),
            (args or {}).get("op", ">="),
            (args or {}).get("value", ""),
            (args or {}).get("timeout_s", 60),
        )
    elif name == "dev_board_command":
        from dev_tool_factory import dev_board_command
        text = dev_board_command(
            (args or {}).get("device_id", ""),
            (args or {}).get("command", ""),
        )
    elif name == "dev_speak":
        from dev_tool_factory import dev_speak
        text = dev_speak(
            (args or {}).get("text", ""),
            device_id=(args or {}).get("device_id", ""),
        )
    elif name == "dev_flash_start":
        from dev_tool_factory import dev_flash_start
        text = dev_flash_start(
            (args or {}).get("device", ""),
            url=(args or {}).get("url", ""),
            size=(args or {}).get("size", 0),
        )
    elif name == "dev_flash_status":
        from dev_tool_factory import dev_flash_status
        text = dev_flash_status((args or {}).get("limit") or 20)
    elif name == "dev_flash_abort":
        from dev_tool_factory import dev_flash_abort
        text = dev_flash_abort()
    elif name == "dev_register_board":
        from dev_tool_factory import register_board
        text = register_board(
            (args or {}).get("device_id", ""),
            model=(args or {}).get("model", ""),
            mac=(args or {}).get("mac", ""),
            notes=(args or {}).get("notes", ""),
        )
    elif name == "dev_list_boards":
        from dev_tool_factory import list_boards
        text = list_boards()
    else:
        # 动态工具路由
        _, dyn_funcs = _load_dynamic()
        fn = dyn_funcs.get(name)
        if fn is not None:
            try:
                text = fn(args or {})
                # 调用统计（进化日志）
                try:
                    from dev_tool_factory import _record_tool_call
                    _record_tool_call(name)
                except Exception:
                    pass
                return {"content": [{"type": "text", "text": text}]}
            except Exception as exc:
                return {
                    "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)}],
                    "isError": True,
                }
        return {"content": [], "isError": True, "error": f"unknown tool: {name}"}
    return {"content": [{"type": "text", "text": text}]}


def _load_dynamic():
    """扫描 dynamic_tools/*.py 热加载动态工具。

    每个模块需提供：
      TOOL_DEF = {"name": str, "description": str, "inputSchema": dict}
      execute(args: dict) -> str   # 返回文本结果（建议 JSON 字符串）
    返回 (tools, funcs)：tools 为 TOOL_DEF 列表，funcs 为 name->execute 映射。
    """
    tools, funcs = [], {}
    if not os.path.isdir(DYNAMIC_DIR):
        return tools, funcs
    for fn in sorted(os.listdir(DYNAMIC_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(DYNAMIC_DIR, fn)
        mod_name = "dyn_" + fn[:-3]
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "TOOL_DEF") and hasattr(mod, "execute"):
                tools.append(mod.TOOL_DEF)
                funcs[mod.TOOL_DEF["name"]] = mod.execute
                print(f"[fall-mcp] dynamic tool loaded: {mod.TOOL_DEF['name']} ({fn})", flush=True)
        except Exception as exc:
            print(f"[fall-mcp] dynamic tool load failed: {fn}: {exc}", flush=True)
    return tools, funcs


async def handle(websocket) -> None:
    async for raw in websocket:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if not isinstance(msg, dict) or "method" not in msg:
            continue
        method = msg["method"]
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            if msg_id is not None:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": SERVER_INFO,
                    },
                }))
        elif method == "tools/list":
            if msg_id is not None:
                dyn_tools, _ = _load_dynamic()
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"tools": TOOLS + dyn_tools},
                }))
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if name in ("dev_create_tool", "dev_delete_tool"):
                # 长耗时任务（DSH agent 可能 5-10 分钟）放线程池，避免阻塞事件循环导致连接超时
                result = await asyncio.to_thread(call_tool, name, arguments)
            else:
                result = call_tool(name, arguments)
            if msg_id is not None:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": msg_id, "result": result,
                }))
        # notifications (notifications/initialized etc.) and other methods ignored


async def main() -> None:
    print(f"fall-monitor MCP listening on ws://{HOST}:{PORT}/mcp/", flush=True)
    # 启动 DSH 插件需求轮询器（后台线程，自我进化链路）
    try:
        from dev_tool_factory import start_plugin_poller
        start_plugin_poller()
    except Exception as e:
        print(f"[fall-mcp] poller start failed: {e}", flush=True)
    # #9b 反馈通道：确认提问自动播报轮询器
    try:
        from dev_tool_factory import start_confirm_poller
        start_confirm_poller()
    except Exception as e:
        print(f"[fall-mcp] confirm poller start failed: {e}", flush=True)
    # #8 线程守护：后台线程死亡自动重启
    try:
        from dev_tool_factory import start_thread_watchdog
        start_thread_watchdog()
    except Exception as e:
        print(f"[fall-mcp] watchdog start failed: {e}", flush=True)
    # 稳定性：离线告警 + DSH 任务进程守护
    try:
        from dev_tool_factory import start_offline_alert
        start_offline_alert()
        try:
            from dev_tool_factory import start_telemetry_health
            start_telemetry_health()
        except Exception as e:
            print(f"[fall-mcp] telemetry health start failed: {e}", flush=True)

    except Exception as e:
        print(f"[fall-mcp] offline alert start failed: {e}", flush=True)
    try:
        from dev_tool_factory import start_dsh_task_guard
        start_dsh_task_guard()
    except Exception as e:
        print(f"[fall-mcp] dsh guard start failed: {e}", flush=True)
    # #8 监工地基：烧录事件/日志/遥测监听器（唯一事实源）
    try:
        from dev_tool_factory import start_flash_monitor
        start_flash_monitor()
    except Exception as e:
        print(f"[fall-mcp] flash monitor start failed: {e}", flush=True)
    # websockets>=14 removed the path filter; localhost-only service, any path ok.
    async with websockets.serve(handle, HOST, PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
