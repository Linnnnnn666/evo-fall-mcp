#!/usr/bin/env python3
"""工具工厂：根据用户需求用 LLM 生成动态工具模块并部署到 dynamic_tools/。

安全边界：
- import 白名单（标准库 + httpx）
- 禁止本地文件/内网/shell/危险模式
- 生成后编译 + 热加载验证通过才写入
- 失败时把错误反馈给 LLM 自修复（最多 3 轮）
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re

import httpx

DYNAMIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dynamic_tools")
TASK_RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_results")
MANIFEST_PATH = os.path.join(DYNAMIC_DIR, "manifest.json")
EVOLUTION_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evolution.log")
PLUGIN_REQUEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin_requests")
PLUGINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins.json")
PLUGINS_DIR = "/opt/dsh-plugins"
HEADLESS_PATCH = "/root/.dsh/profiles/headless/cordis.patch.yml"
DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.json")
OTA_ROOT = "/opt/ota"


# ═══════════════════ 记忆层：工具注册表 + 进化日志 ═══════════════════

def _load_manifest() -> dict:
    """工具注册表：记录所有工具的元数据与调用统计。"""
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tools": {}}


def _save_manifest(m: dict) -> None:
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)


def _log_evolution(event_type: str, detail: str) -> None:
    """进化日志：谁发起 → 生成什么 → 验证结果 → 谁调用 → 效果。"""
    import time
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type} | {detail}"
    with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _git_commit(msg: str) -> None:
    """dynamic_tools 目录 git 管理（回滚基础）。"""
    try:
        import subprocess
        subprocess.run(["git", "-C", DYNAMIC_DIR, "add", "-A"], capture_output=True)
        subprocess.run(
            ["git", "-C", DYNAMIC_DIR, "-c", "user.email=evo@local",
             "-c", "user.name=evo-agent", "commit", "-qm", msg],
            capture_output=True,
        )
    except Exception:
        pass


def _sync_manifest(created_by: str = "DSH") -> None:
    """扫描 dynamic_tools，把新工具注册进 manifest + 写进化日志 + git 提交。
    由 create_tool / dispatch_task 完成后调用——这就是"自我进化"的落盘动作。
    """
    import time
    m = _load_manifest()
    known = set(m["tools"].keys())
    changed = False
    if os.path.isdir(DYNAMIC_DIR):
        for fn in sorted(os.listdir(DYNAMIC_DIR)):
            if not fn.endswith(".py") or fn.startswith("_") or fn == "manifest.json":
                continue
            path = os.path.join(DYNAMIC_DIR, fn)
            try:
                spec = importlib.util.spec_from_file_location("dyn_sync_" + fn[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                name = mod.TOOL_DEF["name"]
                desc = mod.TOOL_DEF.get("description", "")
            except Exception:
                continue
            if name not in known:
                m["tools"][name] = {
                    "name": name,
                    "description": desc[:200],
                    "file": fn,
                    "version": "1.0.0",
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "created_by": created_by,
                    "calls": 0,
                    "last_called_at": None,
                }
                _log_evolution("tool_created", f"{name} ({fn}) by {created_by}")
                changed = True
    if changed:
        _save_manifest(m)
        _git_commit("auto-register new tools")


def _record_tool_call(name: str) -> None:
    """工具被调用时更新注册表统计（供进化日志分析）。"""
    import time
    try:
        m = _load_manifest()
        if name in m["tools"]:
            t = m["tools"][name]
            t["calls"] = int(t.get("calls", 0)) + 1
            t["last_called_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_manifest(m)
            _log_evolution("tool_called", name)
    except Exception:
        pass


def list_tools() -> str:
    """返回工具注册表的结构化列表（供 dev_list_tools 使用）。"""
    m = _load_manifest()
    tools = m.get("tools", {})
    if not tools:
        return "工具注册表为空"
    lines = []
    for name, meta in sorted(tools.items()):
        calls = meta.get("calls", 0)
        lines.append(
            f"- {name}：{meta.get('description', '')[:60]}"
            f"（调用{calls}次，{meta.get('created_by', '?')}创建）"
        )
    return "\n".join(lines)

ALLOWED_IMPORTS = {
    "json", "re", "time", "hashlib", "urllib", "urllib.parse", "datetime",
    "httpx", "typing", "math", "random", "string", "collections", "itertools",
}

FORBIDDEN_PATTERNS = [
    "open(", "subprocess", "os.system", "os.popen", "eval(", "exec(",
    "socket", "127.0.0.1", "localhost", "shutil", "pathlib", "tempfile",
]

BUILD_PROMPT = """你是"小安工具工厂"，根据用户需求生成一个 Python 动态工具模块。

输出格式（必须是单个 JSON 对象，不要输出任何其他内容）：
{"file_name": "小写蛇形文件名.py", "code": "完整 Python 模块代码"}

代码硬性要求：
1. 必须定义 TOOL_DEF = {"name": "小写蛇形工具名", "description": "详细描述（说明何时调用、各参数含义、返回什么）", "inputSchema": {"type":"object","properties":{...},"required":[...]}}
2. 必须定义 execute(args: dict) -> str，返回 JSON 字符串（成功 {"ok": true, ...}；失败 {"ok": false, "error": "原因"}）
3. 只允许 import：json, re, time, hashlib, urllib.parse, datetime, httpx, typing, math, random, string, collections, itertools
4. 网络请求必须用 httpx，设置 timeout，带常见浏览器 User-Agent 头；所有异常必须捕获并返回 ok:false
5. 禁止：读写本地文件、访问内网/本地地址（127.0.0.1/localhost）、执行 shell、连接数据库
6. 优先选择无需登录的公开 HTTP API；若接口需要签名/密钥，在 execute 内自行实现
7. **必须使用中国大陆可直接访问的公开 API**（如国内站点 api.bilibili.com、api.github.com 可达；api.coingecko.com 等海外接口不可达，禁止使用）
8. 工具名用英文小写蛇形；描述用中文写清楚

用户需求：{requirement}
"""

# DSH headless 任务模板：完整 agent 干活（优先路径）
DSH_TASK_TEMPLATE = """在 /opt/fall-mcp/dynamic_tools/ 目录创建一个新的 MCP 动态工具文件，需求：{requirement}。

要求：
- 文件名小写蛇形（如 query_xxx.py），内容定义 TOOL_DEF（name 小写蛇形、description 中文详细、inputSchema）和 execute(args)->str（返回 JSON 字符串，成功 ok:true，失败 ok:false 含 error）
- 只允许 import：json, re, time, hashlib, urllib.parse, datetime, httpx, typing, math, random, string, collections, itertools
- 网络请求用 httpx，timeout 15 秒，带浏览器 User-Agent，异常全捕获
- 禁止读写本地文件/内网/127.0.0.1/shell/数据库
- **必须使用中国大陆可直接访问的公开 API**（api.coingecko.com 等海外接口不可达，禁止用；优先 bilibili/github/国内公开接口），写完后用 curl 或 httpx 实测接口可达性，不通就换可用接口
- 文件直接写入 /opt/fall-mcp/dynamic_tools/ 目录
- **先检查 /opt/fall-mcp/dynamic_tools/ 目录和 manifest.json：若已有同名或同功能工具，直接告诉用户已有该工具，不要重复创建**
- 完成后用一句话回复工具名和功能。不要做其他任何事情。"""


def _llm_config() -> tuple:
    """读取 xiaozhi 配置中的 LLM 凭据（key/model/url）。模型可用 FALL_LLM_MODEL 覆盖。"""
    import yaml
    p = "/opt/xiaozhi-server/main/xiaozhi-server/data/.config.yaml"
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm = cfg["LLM"]["DeepSeekLLM"]
    model = os.environ.get("FALL_LLM_MODEL", llm.get("model_name", "deepseek-v4-flash"))
    return llm["api_key"], model, llm["url"]


def _call_llm(messages: list, api_key: str, model: str, url: str) -> str:
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4000,
    }
    r = httpx.post(
        url + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict:
    """从 LLM 输出提取 JSON 对象（容忍 markdown 代码块/前后杂文）。"""
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError("无法从 LLM 输出中解析 JSON")


def _validate(code: str, file_name: str) -> tuple:
    """返回 (ok, error)。"""
    if not re.fullmatch(r"[a-z0-9_]{1,40}\.py", file_name or ""):
        return False, f"文件名不合法: {file_name!r}"
    try:
        compile(code, file_name, "exec")
    except SyntaxError as e:
        return False, f"语法错误: {e}"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in ALLOWED_IMPORTS:
                    return False, f"禁止 import: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top not in ALLOWED_IMPORTS:
                return False, f"禁止 import: {node.module}"
    for pat in FORBIDDEN_PATTERNS:
        if pat in code:
            return False, f"代码包含禁止模式: {pat}"
    return True, ""


def _dsh_cmd(task: str, profile: str = "headless") -> list:
    """构造 dsh headless 命令：任务写入临时文件，bash 用 $(cat) 读取（避免转义/展开/乱码）。
    profile 默认 headless（DSH-1 干活环境）；DSH-2 用 headless-builder（干净隔离环境）。"""
    import subprocess
    import uuid
    task_file = f"/tmp/dsh_task_{uuid.uuid4().hex[:8]}.txt"
    with open(task_file, "w", encoding="utf-8") as f:
        f.write(task)
    return (
        ["bash", "-lc",
         f'[ -s /etc/profile.d/dsh-env.sh ] && . /etc/profile.d/dsh-env.sh; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; nvm use default >/dev/null 2>&1; dsh --profile {profile} "$(cat {task_file})"; rm -f {task_file}'],
        task_file,
    )


def _run_dsh_headless(requirement: str, timeout: int = 600) -> str:
    """启动 DSH headless 完整 agent 干活，返回其最终回复文本。失败抛异常。"""
    import subprocess
    task = DSH_TASK_TEMPLATE.replace("{requirement}", requirement.strip())
    env = dict(os.environ)
    env.update({
        "NVM_DIR": os.path.expanduser("~/.nvm"),
        "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
    })
    cmd, _ = _dsh_cmd(task)
    proc = subprocess.run(
        cmd,
        cwd="/opt/fall-mcp",
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    # DSH 正常结束（exit 0）且提到了工具名即视为成功；否则抛错降级
    if proc.returncode != 0:
        raise RuntimeError(f"DSH 退出码 {proc.returncode}: {out[-500:]}")
    return out.strip()[-1000:]


def create_tool(requirement: str, api_key: str = None, model: str = None, url: str = None) -> str:
    """生成并部署工具，返回给用户的播报文本。优先 DSH headless，失败降级单次 LLM。"""
    if not requirement or not requirement.strip():
        return "请描述你想创建的工具功能，比如'查询B站视频播放量'。"

    # 路径一：DSH 完整 agent（推荐）
    try:
        # 修复(2026-08-21)：快照必须先于 DSH 写文件，否则新文件全被 fn in before 跳过，
        # _sync_manifest 永不执行（进化日志无 by DSH 记录）
        before = set(os.listdir(DYNAMIC_DIR))
        reply = _run_dsh_headless(requirement)
        # 失败检测（2026-08-24）：DSH 可能 rc=0 但实际是错误（QUOTA/超时/生成失败）
        _bad_markers = ("QUOTA", "Insufficient Balance", "insufficient", "余额不足",
                        "生成失败", "创建失败", "FAILED", "failed to", "error:")
        if any(mk in reply for mk in _bad_markers):
            raise RuntimeError(f"DSH 返回错误: {reply[:200]}")
        if os.path.isdir(DYNAMIC_DIR):
            # DSH 已写文件，热加载验证
            import importlib.util
            for fn in sorted(os.listdir(DYNAMIC_DIR)):
                if not fn.endswith(".py") or fn.startswith("_") or fn in before:
                    continue
                path = os.path.join(DYNAMIC_DIR, fn)
                try:
                    spec = importlib.util.spec_from_file_location("dyn_dsh_" + fn[:-3], path)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    name = mod.TOOL_DEF["name"]
                    # 自我进化落盘：注册 manifest + 进化日志 + git
                    _sync_manifest(created_by="DSH")
                    _log_evolution("tool_created_request", f"{name} from: {requirement[:80]}")
                    return (
                        f"工具创建成功：{name}（由AI智能体编写）。"
                        f"重新唤醒语音助手后即可使用。{reply[-80:]}"
                    )
                except Exception as e:
                    print(f"[tool-factory] DSH wrote file but load failed {fn}: {e}", flush=True)
        return f"工具已由AI智能体创建：{reply}"
    except Exception as e:
        print(f"[tool-factory] DSH headless failed, fallback to single-LLM: {e}", flush=True)

    # 路径二：单次 LLM 生成（降级）
    if api_key is None:
        api_key, model, url = _llm_config()

    last_code, last_error = None, "未知错误"
    for attempt in range(1, 4):  # 最多 3 轮自修复
        msgs = [
            {"role": "system", "content": "你是一个严谨的 Python 代码生成器，只输出 JSON。"},
            {"role": "user", "content": BUILD_PROMPT.replace("{requirement}", requirement.strip())},
        ]
        if attempt > 1:
            msgs.append({"role": "assistant", "content": last_code})
            msgs.append({"role": "user", "content": f"上面的代码校验失败：{last_error}。请修复后重新输出完整 JSON。"})
        try:
            raw = _call_llm(msgs, api_key, model, url)
            data = _extract_json(raw)
            code = data["code"]
            file_name = data["file_name"]
        except Exception as e:
            last_code, last_error = raw if "raw" in dir() else "", f"LLM 输出解析失败: {e}"
            continue

        ok, err = _validate(code, file_name)
        if not ok:
            last_code, last_error = code, err
            continue

        path = os.path.join(DYNAMIC_DIR, file_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        # 热加载验证
        try:
            spec = importlib.util.spec_from_file_location("dyn_check_" + file_name[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            name = mod.TOOL_DEF["name"]
            desc = mod.TOOL_DEF["description"]
        except Exception as e:
            last_code, last_error = code, f"加载验证失败: {e}"
            continue

        print(f"[tool-factory] created {name} ({file_name}) from: {requirement}", flush=True)
        # 自我进化落盘
        _sync_manifest(created_by="LLM")
        _log_evolution("tool_created_request", f"{name} from: {requirement[:80]}")
        return (
            f"工具创建成功：{name}——{desc[:50]}。"
            f"重新唤醒语音助手后即可使用，你可以说'查一下'加功能名来测试。"
        )

    print(f"[tool-factory] FAILED to create tool from: {requirement} | {last_error}", flush=True)
    _log_evolution("tool_create_failed", f"{requirement[:60]} | {last_error[:100]}")
    try:
        dev_speak(f"工具创建失败了：{last_error[:60]}。可以换个说法再试，或者我人工来做。")
    except Exception:
        pass
    return f"工具创建失败：{last_error}。可以换个说法再试，或者这个功能需要人工开发。"


def delete_tool(name: str) -> str:
    """按工具名删除动态工具（遍历 dynamic_tools 匹配 TOOL_DEF['name']）。"""
    name = (name or "").strip()
    if not os.path.isdir(DYNAMIC_DIR):
        return f"没有找到工具 {name}"
    for fn in sorted(os.listdir(DYNAMIC_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(DYNAMIC_DIR, fn)
        try:
            spec = importlib.util.spec_from_file_location("dyn_del_" + fn[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if mod.TOOL_DEF["name"] == name:
                os.remove(path)
                # 同步注册表 + 进化日志 + git
                m = _load_manifest()
                if name in m.get("tools", {}):
                    del m["tools"][name]
                    _save_manifest(m)
                _log_evolution("tool_deleted", name)
                _git_commit(f"delete {name}")
                print(f"[tool-factory] deleted {name} ({fn})", flush=True)
                return f"已删除工具 {name}，重新唤醒后生效。"
        except Exception:
            continue
    return f"没有找到工具 {name}"


# ═══════════════════ 通用任务派发 ═══════════════════

GENERAL_TASK_TEMPLATE = """你是服务器智能助手，请执行以下用户任务：

任务：{task}

要求：
1. 可以执行 bash 命令、读写 /opt/fall-mcp/ 目录下的文件、调用公开 API 来完成任务
2. 禁止破坏性操作：不要删除系统数据/配置文件、不要停服务，除非任务明确要求
3. 完成后把结果写入 {result_path}（UTF-8 文本文件），内容包括：
   - 任务概述：你做了什么
   - 关键结果：数据/结论（尽量具体，带数字）
   - 建议或后续步骤（如适用）
4. 写完后用一句话回复任务摘要。不要做其他任何事情。

【确认循环（零障碍沟通，重要）】
如果任务执行中需要用户提供信息/确认/选择才能继续（如"用哪个方案""是否继续""目标板是哪块"），
不要瞎猜，按以下流程向用户提问并等待回答：
1. task_id = 去掉 {result_path} 的文件扩展名（如 0821043229.txt 的 task_id=0821043229）
2. mkdir -p /opt/fall-mcp/confirm_queue
3. 用 python3 写入问题（一次只问一个问题；问题必须是纯口语化的自然问句，
   像对朋友说话一样，例如"这个方案可以吗？"，严禁加"请回答：同意/不同意"之类的指令后缀）：
   python3 -c "import json,time; open('/opt/fall-mcp/confirm_queue/<task_id>.jsonl','a').write(json.dumps({'q':'<自然问句>','ts':time.time()},ensure_ascii=False)+chr(10))"
4. 等待回答：最多 20 次循环，每次 sleep 10 秒，用 grep -c '\"a\"' /opt/fall-mcp/confirm_queue/<task_id>.jsonl 检查是否已有回答；有则读取最后一条回答
5. 超时未回答：用最合理的默认假设继续，并在结果里注明"用户未确认，采用默认方案"
6. 拿到回答后继续执行任务
注意：一次只问一件事，问句要短；禁止出现任务ID/文件名/技术术语。

【任务复盘（自我进化）】
完成任务后请判断：这个需求（或类似查询）以后还会再遇到吗？值得固化成 MCP 工具吗？
- 如果值得：直接在 /opt/fall-mcp/dynamic_tools/ 目录创建工具文件（格式：TOOL_DEF = {{"name", "description", "inputSchema"}} + execute(args)->str 返回 JSON，只允许 import json/re/time/hashlib/urllib.parse/datetime/httpx/typing/math/random/string/collections/itertools，网络请求用 httpx 带 UA 超时 15 秒，异常捕获返回 ok:false，必须用中国大陆可访问的公开 API 并实测）。先检查目录里是否已有同名或同功能工具，有就不要重复创建。创建后在回复中说明创建了什么工具。
- 如果只是偶发任务不值得固化：直接说明"无需固化"。

【能力缺口复盘（DSH 插件进化）】
完成任务后另请判断：这次任务里，你是否遇到了**自己能力不足**的情况（比如需要解析 PDF/Excel、需要某种数据处理能力、需要某个 DSH 工具，但你的工具列表里没有）？
- 如果遇到了且这个能力以后还会用到：不要自己硬造，**写一个插件需求文件**：
  mkdir -p /opt/fall-mcp/plugin_requests
  写入 /opt/fall-mcp/plugin_requests/req_$(date +%s).json，格式：
  {{"capability": "一句话描述需要的能力", "tools": [{{"name": "建议工具名", "desc": "工具功能", "input": "输入参数", "output": "输出"}}], "task_type": "该能力主要服务于哪类任务（如 data-processing/web/audio）", "urgency": "high|medium|low", "requester_task": "当前任务简述"}}
  注意：先查看 /opt/fall-mcp/plugins.json（若存在）确认没有已装同名能力；只写这一个文件，不要自己开发插件。
- 如果不需要：在回复中说明"无需插件需求"。"""


def _run_dsh_general(task: str, result_path: str, timeout: int = 900) -> str:
    """启动 DSH headless 执行通用任务，返回其最终回复。失败抛异常。"""
    import subprocess
    prompt = GENERAL_TASK_TEMPLATE.replace("{task}", task.strip()).replace("{result_path}", result_path)
    env = dict(os.environ)
    env.update({
        "NVM_DIR": os.path.expanduser("~/.nvm"),
        "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
    })
    cmd, _ = _dsh_cmd(prompt)
    proc = subprocess.run(
        cmd,
        cwd="/opt/fall-mcp",
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"DSH 退出码 {proc.returncode}: {out[-500:]}")
    return out.strip()[-1000:]


def dispatch_task(task: str) -> str:
    """派发通用任务给 DSH 后台执行。立即返回任务ID（语音板可感知的确认文本）。"""
    if not task or not task.strip():
        return "请描述要执行的任务，比如'分析一下服务器磁盘使用情况'。"
    import threading
    import time as _time
    os.makedirs(TASK_RESULT_DIR, exist_ok=True)
    task_id = _time.strftime("%m%d%H%M%S")  # 如 0819035512
    result_path = os.path.join(TASK_RESULT_DIR, f"{task_id}.txt")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(f"任务ID: {task_id}\n状态: 执行中\n任务: {task}\n\n")

    def _bg():
        try:
            print(f"[task-dispatch] start {task_id}: {task[:80]}", flush=True)
            # 记忆层：检索相关历史经验注入任务
            related = _find_related_experience(task)
            if related:
                exp_block = (
                    "\n\n【参考经验】服务器上有相似的历史任务，建议先查看其做法再执行：\n"
                    + "\n".join(f"- {p}（任务：{d[:60]}）" for _, p, d in related)
                )
                _log_evolution("task_exp_injected", f"{task_id}: 注入 {len(related)} 条经验")
            else:
                exp_block = ""
            reply = _run_dsh_general(task + exp_block, result_path)
            with open(result_path, "a", encoding="utf-8") as f:
                f.write(f"\n状态: 已完成\nDSH摘要: {reply}\n")
            print(f"[task-dispatch] done {task_id}: {reply[:120]}", flush=True)
            # 复盘落盘：DSH 可能创建了新工具 → 注册进 manifest + 进化日志 + git
            _sync_manifest(created_by="task-review")
            _log_evolution("task_done", f"{task_id}: {task[:60]} | {reply[:100]}")
            # #9a 零障碍：任务完成主动播报（语音板在线时开口，失败不影响任务）
            # 语感优化(2026-08-21)：去任务ID/markdown/括号细节，只给核心结论让 LLM 组织语言
            try:
                import re as _re
                brief = _re.sub(r"[*#`>]+", "", reply or "")
                brief = _re.sub(r"（[^）]{0,40}）|\([^)]{0,40}\)", "", brief)
                brief = brief.replace("任务完成", "").replace("任务摘要", "").strip()
                dev_speak(f"{brief[:80]}")
            except Exception:
                pass
        except Exception as exc:
            with open(result_path, "a", encoding="utf-8") as f:
                f.write(f"\n状态: 失败\n错误: {exc}\n")
            print(f"[task-dispatch] ERROR {task_id}: {exc}", flush=True)
            try:
                _maybe_repair_headless("task_failed")
            except Exception:
                pass

    threading.Thread(target=_bg, daemon=True).start()
    return (
        f"任务已派发，任务ID {task_id}。正在执行中，大约需要一到三分钟。"
        f"稍后可以问'查询任务结果'来获取完成情况。"
    )


def query_task(task_id: str = None) -> str:
    """查询任务状态/结果。task_id 缺省时返回最近一个任务。"""
    if not os.path.isdir(TASK_RESULT_DIR):
        return "还没有派发过任务。"
    files = sorted(f for f in os.listdir(TASK_RESULT_DIR) if f.endswith(".txt"))
    if not files:
        return "还没有派发过任务。"
    if task_id:
        path = os.path.join(TASK_RESULT_DIR, f"{task_id}.txt")
        if not os.path.exists(path):
            return f"没有找到任务 {task_id}。最近的任务是 {files[-1][:-4]}。"
    else:
        path = os.path.join(TASK_RESULT_DIR, files[-1])
    content = open(path, encoding="utf-8").read()
    return content[-2500:]


# ═══════════════════ DSH 插件进化：需求协议 + 轮询器 + DSH-2 ═══════════════════

# DSH headless 常驻内置工具（冲突检查用）
DSH_BUILTIN_TOOLS = {
    "bash", "read", "write", "edit", "glob", "grep", "pwsh", "web_search",
    "subagent", "subagent_fork", "list_agents", "send_message", "interrupt_agent",
    "workflow", "ralph", "skill", "find_plugin", "exit_plan_mode", "todo_write",
    "create_goal", "get_goal", "update_goal", "job_list", "job_output", "job_kill",
    "dev_mode_subagent", "dev_router_mode", "dev_router_status",
    "str_replace_editor", "vision_bootstrap", "vision_describe", "vision_detect",
    "vision_ground", "vision_crop", "vision_ocr", "vision_long_screenshot_ocr",
    "vision_pixel_diff", "vision_present", "vision_colors", "vision_extract_foreground",
    "vision_trace", "vision_materialize", "vision_html_screenshot",
}

# 插件交付代码二次扫描（不依赖模型自检）的危险模式
PLUGIN_DANGEROUS_PATTERNS = [
    "child_process", "execSync", "exec(", "spawn(", "fs.write", "fs.rm",
    "fs.unlink", "fs.rmdir", "process.env", "require('fs'", 'require("fs"',
    "eval(", "Function(",
]


def _validate_plugin_request(req) -> str:
    """需求字段校验：返回错误信息（空串 = 合法）。"""
    if not isinstance(req, dict):
        return "需求不是 JSON 对象"
    cap = (req.get("capability") or "").strip()
    if len(cap) < 4:
        return "capability 缺失或过短"
    tools = req.get("tools")
    if not isinstance(tools, list) or not tools:
        return "tools 列表缺失或为空"
    for t in tools:
        name = (t.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{1,40}", name):
            return f"工具名不合法: {name!r}（需小写字母数字下划线，≤40字符）"
        if not (t.get("desc") or "").strip():
            return f"工具 {name} 缺少 desc 描述"
    if req.get("urgency") not in (None, "high", "medium", "low"):
        return f"urgency 不合法: {req.get('urgency')!r}"
    return ""


def _installed_plugin_tool_names() -> set:
    names = set()
    try:
        plugins = _load_plugins()
        for p in plugins.get("plugins", {}).values():
            names.update(p.get("tools", []))
    except Exception:
        pass
    return names


def _mcp_tool_names() -> set:
    names = {"query_fall_status", "query_device_status", "query_fall_history",
             "query_fall_stats", "dev_create_tool", "dev_delete_tool",
             "dev_dispatch_task", "dev_query_task", "dev_list_tools"}
    try:
        m = _load_manifest()
        names.update(m.get("tools", {}).keys())
    except Exception:
        pass
    return names


def _tool_name_conflicts(req) -> list:
    """返回冲突说明列表（空 = 无冲突）。"""
    conflicts = []
    builtin = DSH_BUILTIN_TOOLS
    plugins = _installed_plugin_tool_names()
    mcp = _mcp_tool_names()
    for t in req.get("tools", []):
        name = t.get("name", "")
        if name in builtin:
            conflicts.append(f"{name}（DSH 内置工具）")
        elif name in plugins:
            conflicts.append(f"{name}（已装插件工具）")
        elif name in mcp:
            conflicts.append(f"{name}（MCP 工具）")
    return conflicts


def _post_install_scan() -> None:
    """需求 done 后扫描 /opt/dsh-plugins 新文件（危险模式二次扫描，不依赖模型自检）。"""
    if not os.path.isdir(PLUGINS_DIR):
        return
    for fn in sorted(os.listdir(PLUGINS_DIR)):
        if not fn.endswith(".mjs"):
            continue
        path = os.path.join(PLUGINS_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        hits = [p for p in PLUGIN_DANGEROUS_PATTERNS if p in content]
        if hits:
            _log_evolution("plugin_scan_warn", f"{fn}: 检测到危险模式 {hits}，请人工复核")
            print(f"[fall-mcp] ⚠️ 插件扫描警告 {fn}: {hits}", flush=True)

PLUGIN_BUILD_TEMPLATE = """你是插件制造与安装工程师（DSH-2）。请为 headless DSH 提供一个新的 cordis 插件能力。

【重要】只读取和处理指定的需求文件：{req_path}。禁止查看或处理 plugin_requests/ 目录下的其他任何需求文件——其他文件是其他工程师的任务，与你无关。

需求文件：{req_path}
先读取该 JSON 需求（capability / tools / task_type / urgency / requester_task）。

【插件获取策略（按优先级）】
第一步（优先）：搜索开源高星插件，不要一上来就自己做。
1. 用 GitHub API 搜索（curl，带 UA）：
   curl -s "https://api.github.com/search/repositories?q=dsh+<能力关键词>&sort=stars&order=desc&per_page=10"
   若 api.github.com 不通，用镜像：https://gh-proxy.com/https://api.github.com/search/repositories?...
   也可以搜 npm：npm search dsh-<关键词> --json | head -c 2000
2. 评估候选（必须全部满足才算合格）：
   a) 功能与需求匹配（看 README/description）
   b) 高星：stars ≥ 200 优先考虑；stars < 100 的除非功能完全匹配否则放弃
   c) 是 DSH/cordis 插件（package.json 含 dsh.profile.bundles 或 dsh.bundle.patch，或 cordis 插件结构）
   d) 支持 Linux
   e) 代码安全核对：下载或查看仓库关键文件（package.json / 入口 / 构建产物），检查无混淆代码、无凭据窃取、无数据外发、无危险 shell 操作；有疑虑就放弃
3. 安装（核对通过后）：
   cd /root/.dsh/profiles/headless
   export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use default >/dev/null 2>&1
   dsh plugin --profile headless add github:<owner>/<repo>
   若 GitHub 直连失败，用镜像：dsh plugin --profile headless add https://gh-proxy.com/https://github.com/<owner>/<repo>.git
4. 验证：起 headless 进程调用新插件工具确认可用：
   export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use default >/dev/null 2>&1; . /etc/profile.d/dsh-env.sh
   dsh --profile headless "先用 bash 工具执行 pwd（触发工具集全量开放，Router 第一轮只显示核心工具），然后调用 <工具名> 做一次最小测试，一句话回复结果"
   【重要】第一轮只有核心工具（read/edit/glob/grep/bash），必须让模型先调用一次 bash 才会开放全部工具；若模型说"没有该工具"且没先调 bash，视为验证方式错误，重试时强调先调 bash
5. 验证失败：dsh plugin --profile headless remove <包名> 卸载，换下一个候选；所有候选都不行才进入第二步。

第二步（兜底）：找不到合格的开源插件时，自己制造并安装：
【自制插件规范】
1. 插件文件写到 /opt/dsh-plugins/<plugin_name>.mjs（plugin_name 用 kebab-case，先 mkdir -p /opt/dsh-plugins）
2. 插件格式（cordis 插件，零依赖）：
   export const name = '<plugin_name>'
   export const inject = ['tools']
   export function apply(ctx, config) {{
     const TOOLS = {{
       '<tool_name>': {{ desc: '<tool 功能描述>', fn: (args) => '...' }},
     }}
     for (const [toolName, tool] of Object.entries(TOOLS)) {{
       ctx.tools.register({{
         name: toolName,
         description: tool.desc,
         parameters: {{ type: 'object', properties: {{}} }},
         execute: tool.fn,
         output: {{ schema: {{ type: 'string' }}, render: (_a, v) => [{{ type: 'text', text: String(v) }}] }},
       }})
     }}
   }}
3. execute 函数只允许：纯计算、字符串/JSON 处理、通过全局 fetch 或 https 模块调用公开 HTTP API（带超时、异常捕获返回可读错误）；禁止 shell 执行、文件写入/删除、import 任何 npm 包
4. 工具名不能与 DSH 内置工具（bash/read/write/edit/glob/grep/pwsh/web_search/subagent 等）及 /opt/fall-mcp/plugins.json 中已注册插件工具重名
5. 自检：node --check 语法检查；grep 检查无危险模式（child_process/exec/spawn/fs.write/fs.rm/process.env 等）
【自制插件安装流程（全部由你完成）】
1. 备份 patch：cp {patch_path} {patch_path}.bak.$(date +%s)
2. 加安装锁：mkdir /tmp/dsh-plugin-install.lock（若已存在，每 10 秒重试，最多等 60 秒；仍占用则放弃，需求标 failed）
3. 把插件条目追加到 {patch_path}（YAML 顶层数组，用 insert 语法）：
   - insert:
       - id: <plugin_name>
         name: /opt/dsh-plugins/<plugin_name>.mjs
         config: {{}}
   【原子写要求】不要直接追加到原文件！先 cp 一份到临时文件，在临时文件上追加，然后 mv 临时文件覆盖原文件（原子替换，防止半写）。写完后用 python3 -c "import yaml; yaml.safe_load(open('{patch_path}'))" 校验 YAML 合法，不合法则恢复备份并重试。
4. 验证（关键）：用 bash 起 headless 测试进程（同上命令），确认：a) DSH 正常启动无报错 b) 新工具真实可用并返回结果
5. 验证失败：回滚（cp 备份恢复 patch），修改插件代码重试（最多 3 轮）；全失败：恢复备份、释放锁、需求文件标 status=failed 并写 error
6. 验证成功：释放锁；需求文件标 status=done（可附加 installed_plugin / installed_tools / source=market|self-made 字段）；回复：来源（市场/自制）、插件名、工具列表、验证结果。不要做其他任何事情。"""




# ═══════════════ 两层进化保险：DSH-1 健康检测 + 自动修复 + DSH-2 兜底 ═══════════════
# 设计（用户定义，2026-08-21）：
#   DSH-1（headless）= 干活环境；DSH-2（headless-builder）= 隔离进化环境。
#   保险逻辑：DSH-2 给 DSH-1 装插件可能装坏 → 必须让 DSH-2 与 DSH-1 分离（隔离），
#   这样 DSH-1 坏了 DSH-2 自己不会坏，且 DSH-2 有能力给 DSH-1 修复。
#   本模块把"检测→自动修复→DSH-2 兜底"固化为机制。

HEALTH_REPAIR_TEMPLATE = """你是 EvoAgent 系统的修复工程师（DSH-2，隔离环境）。DSH-1（headless profile）的确定性自动修复未能恢复健康，需要你智能诊断并修复。

【健康报告】{report}

【自动修复已尝试】{auto_result}

【你的任务】
1. 读取 /root/.dsh/profiles/headless/cordis.patch.yml，诊断问题根因（YAML 结构损坏 / insert 引用文件缺失 / 插件间冲突等）
2. 修复原则：保持 DSH-1 可用前提下最小改动。优先：移除或注释坏 insert 条目 + 把坏插件文件移到 /opt/dsh-plugins/quarantine/
3. 全程遵守：
   - 先备份：cp /root/.dsh/profiles/headless/cordis.patch.yml /root/.dsh/profiles/headless/cordis.patch.yml.bak.$(date +%s)
   - 原子写：先写临时文件再 mv 覆盖（防止半写）
   - 写完用 python3 -c "import yaml;yaml.safe_load(open('/root/.dsh/profiles/headless/cordis.patch.yml'))" 校验 YAML 合法
4. 修复后验证：
   - YAML 合法 + patch 中所有 insert 引用文件存在
   - 冒烟测试：dsh --profile headless "只回复两个字：健康"（若 DSH 无法启动说明没修好，继续诊断；冒烟前可用 bash 工具 pwd 触发工具集全量开放）
5. 最后回复：修了什么、动了哪些文件、冒烟结果。不要做其他任何事情。"""

_REPAIR_LOCK = "/tmp/dsh-headless-repair.lock"
_thread_registry = {}  # 线程守护注册表：name -> threading.Thread
_PERIODIC_CHECK_INTERVAL = 30  # 轮询器每 N 轮做一次周期健康检查


def _node_bin() -> str:
    """定位 node（fall-mcp 进程 PATH 可能没有 nvm node）。"""
    import shutil as _sh
    n = _sh.which("node")
    if n:
        return n
    for cand in sorted(__import__("glob").glob("/root/.nvm/versions/node/*/bin/node"), reverse=True):
        return cand
    return "node"


def _health_check_headless() -> dict:
    """DSH-1（headless profile）静态健康检查（秒级，不起 DSH 进程）。
    检查项：patch 存在 / YAML 合法 / insert 引用文件存在 / .mjs 语法。
    返回 {"ok": bool, "issues": [{"type","item","detail"}]}"""
    import subprocess
    issues = []
    patch = HEADLESS_PATCH
    if not os.path.exists(patch):
        return {"ok": False, "issues": [{"type": "patch_missing", "item": patch,
                                         "detail": "headless cordis.patch.yml 不存在"}]}
    try:
        import yaml
        data = yaml.safe_load(open(patch, encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "issues": [{"type": "patch_broken", "item": patch,
                                         "detail": f"YAML 解析失败: {e}"}]}
    refs = []
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            inserts = entry.get("insert")
            if not isinstance(inserts, list):
                continue
            for ins in inserts:
                if isinstance(ins, dict) and ins.get("name"):
                    refs.append((str(ins.get("id", "?")), str(ins["name"])))
    for pid, name in refs:
        if not os.path.exists(name):
            issues.append({"type": "file_missing", "item": name,
                           "detail": f"插件 [{pid}] 引用文件不存在"})
        elif name.endswith(".mjs"):
            try:
                r = subprocess.run([_node_bin(), "--check", name],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    issues.append({"type": "syntax_error", "item": name,
                                   "detail": f"插件 [{pid}] 语法错误: {(r.stderr or '')[-200:]}"})
            except Exception as e:
                issues.append({"type": "check_error", "item": name, "detail": str(e)})
    return {"ok": not issues, "issues": issues}


def _auto_repair_headless(report: dict) -> dict:
    """确定性自动修复（秒级，不依赖 LLM）：
    - file_missing/syntax_error：按文本块注释掉坏 insert 条目 + 隔离坏插件文件
    - patch_broken：恢复最近备份；无备份则交给 DSH-2 兜底
    返回 {"summary": str, "removed": int, "ok": bool}"""
    import time as _t
    import shutil as _sh
    patch = HEADLESS_PATCH
    ts = _t.strftime("%Y%m%d-%H%M%S")
    removed, notes = 0, []

    # 1) patch_broken：恢复最近备份
    if any(i["type"] == "patch_broken" for i in report["issues"]):
        baks = sorted(__import__("glob").glob(patch + ".bak.*"))
        if baks:
            _sh.copy2(baks[-1], patch)
            removed += 1
            notes.append(f"patch 损坏，恢复备份 {baks[-1]}")
        else:
            return {"summary": "patch 损坏且无备份，需 DSH-2 兜底", "removed": 0, "ok": False}

    # 2) file_missing / syntax_error：逐块注释坏 insert 条目
    bad_names = {i["item"] for i in report["issues"] if i["type"] in ("file_missing", "syntax_error")}
    if bad_names:
        try:
            lines = open(patch, encoding="utf-8").read().splitlines(keepends=True)
            out = []
            i = 0
            while i < len(lines):
                ln = lines[i]
                if ln.strip() == "- insert:":
                    # 收集该 insert 块（后续缩进行，直到下一个顶层条目/注释）
                    j = i + 1
                    block = []
                    while j < len(lines):
                        nxt = lines[j]
                        stripped = nxt.strip()
                        if not stripped or stripped.startswith("#") or (nxt[:1] not in (" ", "	", "-") and stripped.startswith("-")):
                            if not stripped or stripped.startswith("#"):
                                # 注释/空行可能属于块尾，继续看下一个非空行
                                if stripped.startswith("#") or not stripped:
                                    block.append(nxt)
                                    j += 1
                                    continue
                            break
                        block.append(nxt)
                        j += 1
                    block_text = "".join(block)
                    hit = [b for b in bad_names if b in block_text]
                    if hit:
                        first = hit[0]
                        out.append(f"# [auto-repair {ts}] 坏插件条目已隔离: {first}\n")
                        out.append(f"# {ln}")
                        for bl in block:
                            out.append(f"# {bl}")
                        removed += 1
                        notes.append(f"注释掉引用 {first} 的 insert 条目")
                        # 隔离文件
                        qdir = "/opt/dsh-plugins/quarantine"
                        os.makedirs(qdir, exist_ok=True)
                        qpath = os.path.join(qdir, os.path.basename(first) + "." + ts)
                        if os.path.exists(first):
                            _sh.move(first, qpath)
                            notes.append(f"插件文件已隔离到 {qpath}")
                        i = j
                        continue
                    out.append(ln)
                    i += 1
                else:
                    out.append(ln)
                    i += 1
            # 原子写
            tmp = patch + ".repair.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(out)
            os.replace(tmp, patch)
        except Exception as e:
            return {"summary": f"自动修复异常: {e}", "removed": removed, "ok": False}

    # 3) 校验
    after = _health_check_headless()
    if after["ok"]:
        return {"summary": "; ".join(notes) if notes else "无需修复", "removed": removed, "ok": True}
    return {"summary": f"自动修复后仍不健康: {after['issues'][:3]}", "removed": removed, "ok": False}


def _repair_with_dsh2(report: dict, auto_result: str) -> str:
    """DSH-2 兜底修复：起隔离环境 DSH-2 智能诊断修复（复用 _run_dsh2 模式）。"""
    import subprocess as _sp
    task = HEALTH_REPAIR_TEMPLATE.replace("{report}", __import__("json").dumps(report, ensure_ascii=False)[:3000])                                  .replace("{auto_result}", (auto_result or "")[:1000])                                  .replace("{patch_path}", HEADLESS_PATCH)
    env = dict(os.environ)
    env.update({
        "NVM_DIR": os.path.expanduser("~/.nvm"),
        "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
        "DSH_PERMISSION_MODE": "danger-full-access",
    })
    try:
        cmd, _ = _dsh_cmd(task, profile="headless-builder")  # DSH-2 隔离环境
        proc = _sp.run(cmd, cwd="/opt/fall-mcp", env=env, capture_output=True, text=True, timeout=1800)
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"DSH-2 rc={proc.returncode}: {out.strip()[-400:]}"
    except Exception as e:
        return f"DSH-2 修复异常: {e}"


def _maybe_repair_headless(reason: str) -> dict:
    """健康检查入口：坏 → 确定性自动修复 → 仍坏 → DSH-2 兜底。防并发（锁文件）。"""
    if os.path.exists(_REPAIR_LOCK):
        return {"ok": False, "skipped": "repair lock held"}
    try:
        with open(_REPAIR_LOCK, "w", encoding="utf-8") as f:
            f.write(reason)
        report = _health_check_headless()
        if report["ok"]:
            return {"ok": True, "healthy": True}
        n = len(report["issues"])
        _log_evolution("health_check", f"headless 不健康: {n} 个问题（触发: {reason}）: "
                                       f"{[i['type'] for i in report['issues']]}")
        auto = _auto_repair_headless(report)
        if auto["ok"]:
            _log_evolution("auto_repair_done", f"{reason}: 确定性修复成功（{auto['summary']}）")
            return {"ok": True, "repaired": True, "auto": auto["summary"]}
        _log_evolution("auto_repair_fallback", f"{reason}: 自动修复未恢复，起 DSH-2 兜底（{auto['summary']}）")
        dsh2 = _repair_with_dsh2(report, auto["summary"])
        after = _health_check_headless()
        ok = after["ok"]
        _log_evolution("auto_repair_done",
                       f"{reason}: DSH-2 兜底{'成功' if ok else '未恢复'} | {dsh2[:200]}")
        return {"ok": ok, "repaired": True, "fallback": dsh2[:300]}
    finally:
        try:
            os.remove(_REPAIR_LOCK)
        except Exception:
            pass


def _periodic_health_check() -> None:
    """轮询器周期健康检查（低频，防静默损坏）。"""
    import time as _t
    if not hasattr(_periodic_health_check, "_n"):
        _periodic_health_check._n = 0
    _periodic_health_check._n += 1
    if _periodic_health_check._n % _PERIODIC_CHECK_INTERVAL == 0:
        try:
            r = _maybe_repair_headless("periodic")
            if not r.get("healthy"):
                print(f"[fall-mcp] 周期健康检查: {r}", flush=True)
        except Exception as e:
            print(f"[fall-mcp] 周期健康检查异常: {e}", flush=True)




# ═══════════════ #8 监工地基：MQTT 事件/日志/遥测监听器（烧录任务记录器）═══════════════
# 事件流水 = 唯一事实源：flash_events.jsonl 全量记录 + online_state.json 心跳状态

CONFIRM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confirm_queue")
_confirm_spoken = set()  # 已播报过的问题（task_id|q）


def dev_talk_to_dsh(task_id: str = "", text: str = "") -> str:
    """#9b 反馈通道：把用户对 DSH 提问的回答写回任务确认队列。
    xiaozhi LLM 在用户回答了系统之前播报的确认问题时调用。"""
    if not text or not text.strip():
        return "请提供要转达给 DSH 的回答内容"
    if not task_id:
        import glob as _g
        files = sorted(_g.glob(os.path.join(CONFIRM_DIR, "*.jsonl")))
        if not files:
            return "没有找到待确认的任务（task_id 缺失）"
        task_id = os.path.basename(files[-1])[:-6]
    os.makedirs(CONFIRM_DIR, exist_ok=True)
    path = os.path.join(CONFIRM_DIR, f"{task_id}.jsonl")
    try:
        import time as _t
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"a": text.strip(), "ts": round(_t.time(), 3)}, ensure_ascii=False) + "\n")
        _log_evolution("user_reply", f"task {task_id}: {text.strip()[:60]}")
        return f"已把回答转达给任务 {task_id} 的 DSH：{text.strip()[:80]}"
    except Exception as e:
        return f"写入确认队列失败: {e}"


def dev_query_confirm(task_id: str = "") -> str:
    """查询任务确认队列：有哪些待确认问题、用户是否已回答（服务端/语音查询用）。"""
    if not os.path.isdir(CONFIRM_DIR):
        return "确认队列为空"
    if task_id:
        path = os.path.join(CONFIRM_DIR, f"{task_id}.jsonl")
        if not os.path.exists(path):
            return f"任务 {task_id} 没有确认记录"
        files = [path]
    else:
        import glob as _g
        files = sorted(_g.glob(os.path.join(CONFIRM_DIR, "*.jsonl")), reverse=True)[:5]
    out = []
    for p in files:
        tid = os.path.basename(p)[:-6]
        qs, ans = [], []
        for ln in open(p, encoding="utf-8"):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if "q" in rec:
                qs.append(rec["q"])
            elif "a" in rec:
                ans.append(rec["a"])
        out.append(f"任务 {tid}: 问题{len(qs)}个, 已回答{len(ans)}个 | 最后问题: {qs[-1][:60] if qs else '-'} | 最后回答: {ans[-1][:60] if ans else '-'}")
    return "\n".join(out)


def _mark_spoken(path: str, q_ts: float) -> None:
    """把已成功播报的 q 行标记 s=1（落盘，重启后不重播）"""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
        out = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                out.append(ln)
                continue
            if "q" in rec and rec.get("ts") == q_ts:
                rec["s"] = 1
                out.append(json.dumps(rec, ensure_ascii=False))
            else:
                out.append(ln)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        pass


def _confirm_poller_loop():
    """#9b 轮询器：发现 DSH 新提问 → dev_speak 播报给用户（零障碍沟通）。
    修复(2026-08-21)：每轮最多播 1 条（防连环问）；问题超过 1 小时视为过期不播（防残留旧问题复活）。"""
    import time as _t
    while True:
        try:
            if os.path.isdir(CONFIRM_DIR):
                for fn in sorted(os.listdir(CONFIRM_DIR)):
                    if not fn.endswith(".jsonl"):
                        continue
                    tid = fn[:-6]
                    path = os.path.join(CONFIRM_DIR, fn)
                    qs, last_a_ts = [], 0.0
                    try:
                        lines = open(path, encoding="utf-8").read().splitlines()
                    except Exception:
                        continue
                    for ln in lines:
                        try:
                            rec = json.loads(ln)
                        except Exception:
                            continue
                        if "q" in rec:
                            qs.append(rec)
                        elif "a" in rec:
                            try:
                                last_a_ts = max(last_a_ts, float(rec.get("ts", 0) or 0))
                            except Exception:
                                pass
                    broadcasted = False
                    for rec in qs:
                        q = rec.get("q", "")
                        key = f"{tid}|{q}"
                        q_ts = rec.get("ts", 0) or 0
                        if not q or key in _confirm_spoken or rec.get("s"):
                            continue
                        if q_ts and last_a_ts and q_ts < last_a_ts:
                            # 该问题提出时已有更新的回答 → 已处理过，不播（修复：历史回答不再屏蔽新问题）
                            _confirm_spoken.add(key)
                            print(f"[confirm-poller] 已回答问题跳过: {tid} {q[:40]}", flush=True)
                            continue
                        if q_ts and _t.time() - q_ts > 3600:
                            # 过期问题：标记不播，避免残留旧问题复活骚扰用户
                            _confirm_spoken.add(key)
                            print(f"[confirm-poller] 过期问题跳过: {tid} {q[:40]}", flush=True)
                            continue
                        # 语感优化：播报不带任务ID/指令尾巴，纯人话提问
                        r = dev_speak(f"有件事需要你拿主意：{q}")
                        if "已推送" in r:
                            _confirm_spoken.add(key)
                            _mark_spoken(path, rec.get("ts", 0))  # 落盘：重启不重播
                        else:
                            print(f"[confirm-poller] 播报失败待重试: {r[:120]}", flush=True)
                        broadcasted = True
                        break  # 每轮只播 1 条
                    if broadcasted:
                        break  # 跳出文件遍历，sleep 10 后下一轮（线程常驻，修复 return 退出 bug）
        except Exception as e:
            print(f"[confirm-poller] error: {e}", flush=True)
        _t.sleep(10)


def start_confirm_poller() -> None:
    """fall-mcp 启动时调用：#9b 确认提问自动播报轮询器（线程守护注册）"""
    import threading as _th
    t = _th.Thread(target=_confirm_poller_loop, daemon=True, name="confirm-poller")
    t.start()
    _thread_registry["confirm-poller"] = t
    print("[fall-mcp] confirm poller started (confirm_queue/)", flush=True)


FLASH_EVENT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flash_events.jsonl")
ONLINE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "online_state.json")
_monitor_client = None


def _monitor_log(topic: str, payload) -> None:
    try:
        import time as _t
        rec = {"ts": round(_t.time(), 3), "topic": topic, "payload": payload}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        if os.path.exists(FLASH_EVENT_LOG) and os.path.getsize(FLASH_EVENT_LOG) > 5 * 1024 * 1024:
            os.replace(FLASH_EVENT_LOG, FLASH_EVENT_LOG + ".1")
        with open(FLASH_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _update_online(payload: dict, topic: str = "") -> None:
    """心跳到达：更新 online_state.json（运行时在线状态，不污染注册表）。
    修复(2026-08-24)：board-template 遥测无 device 字段 → 从 topic fall/telemetry/<id> 解析。"""
    import time as _t
    dev = str(payload.get("device") or payload.get("device_id") or "")
    if not dev and topic.startswith("fall/telemetry/"):
        dev = topic.split("/")[-1]
    if not dev:
        return
    st = {}
    if os.path.exists(ONLINE_STATE_FILE):
        try:
            st = json.load(open(ONLINE_STATE_FILE, encoding="utf-8"))
        except Exception:
            st = {}
    entry = st.setdefault(dev, {})
    entry["last_seen"] = _t.strftime("%Y-%m-%d %H:%M:%S")
    entry["ts"] = round(_t.time(), 3)
    for k in ("fw", "state", "uptime", "tgt"):
        if payload.get(k) is not None:
            entry[k] = payload[k]
    try:
        with open(ONLINE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _on_mqtt_message(client, userdata, msg) -> None:
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
    except Exception:
        payload = {"raw": msg.payload.decode("utf-8", errors="replace")[:300]}
    _monitor_log(msg.topic, payload)
    if msg.topic.startswith("fall/telemetry/"):
        _update_online(payload, msg.topic)


def _log_command_event(topic: str, payload) -> None:
    """命令事件溯源（2026-08-24）：服务器→板子的每条命令都落事件流（direction=cmd）。
    与板子上报的遥测/事件同文件，全链路可查：命令发出 → 板子响应 → 遥测确认。"""
    try:
        import json as _j
        import time as _t
        rec = {"ts": _t.time(), "topic": topic, "payload": payload, "direction": "cmd"}
        with open(FLASH_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[trace] 命令事件写入失败: {e}", flush=True)


def _monitor_publish(topic: str, payload: dict) -> bool:
    if _monitor_client is None:
        return False
    try:
        _monitor_client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=0)
        _log_command_event(topic, payload)
        return True
    except Exception:
        return False


def start_flash_monitor() -> None:
    """fall-mcp 启动时调用：MQTT 监听器（事件/日志/遥测 → flash_events.jsonl + online_state.json）"""
    global _monitor_client
    import threading as _th
    import paho.mqtt.client as _mqtt

    def _loop():
        global _monitor_client
        try:
            c = _mqtt.Client(_mqtt.CallbackAPIVersion.VERSION2, client_id="fall-mcp-monitor")
            c.username_pw_set("YOUR_MQTT_USER", "YOUR_MQTT_PASSWORD")
            c.on_message = _on_mqtt_message
            c.connect("127.0.0.1", 1883, 60)
            c.subscribe([("fall/flasher/events", 0), ("fall/flasher/log", 0), ("fall/telemetry/#", 0)])
            _monitor_client = c
            print("[fall-mcp] flash monitor online (fall/flasher/* + fall/telemetry/#)", flush=True)
            c.loop_forever()
        except Exception as e:
            print(f"[fall-mcp] flash monitor error: {e}", flush=True)

    t = _th.Thread(target=_loop, daemon=True, name="flash-monitor")
    t.start()
    _thread_registry["flash-monitor"] = t


def dev_flash_start(device: str = "", url: str = "", size: int = 0) -> str:
    """发布 flash_start 指令到烧录板（#8 监工执行入口）。device 走 /firmware/<id>/latest.json；url 直接烧。"""
    if not device and not url:
        return "请提供 device（如 oled-display / fall-board）或 url"
    cmd = {"cmd": "flash_start"}
    if device:
        cmd["device"] = str(device).strip().lower()
    if url:
        cmd["url"] = url
        cmd["size"] = int(size or 0)
    ok = _monitor_publish("fall/commands/flasher-board", cmd)
    _log_evolution("flash_cmd_sent", f"flash_start {device or url}")
    head = f"已向烧录板发出 flash_start（目标 {device}）" if device else "已向烧录板发出 flash_start（直连 URL）"
    tail = "，可周期性调用 dev_flash_status 查询事件流水" if ok else "，但 MQTT 监听器未就绪（检查 fall-mcp 日志）"
    return head + tail


def dev_flash_status(limit: int = 20) -> str:
    """读取烧录任务记录器（flash_events.jsonl），汇总最近事件/日志/遥测（不干扰烧录板）。"""
    if not os.path.exists(FLASH_EVENT_LOG):
        return "还没有烧录事件记录（监听器启动后开始累积）"
    try:
        lines = open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-int(limit):]
    except Exception:
        return "读取事件记录失败"
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
            p = rec.get("payload", {})
            t = rec.get("topic", "")
            ts = rec.get("ts", "")
            if t == "fall/flasher/events":
                detail = {k: v for k, v in p.items() if k not in ("type", "device")}
                out.append(f"[{ts}] EVT {p.get('type')} dev={p.get('device')} {json.dumps(detail, ensure_ascii=False)[:100]}")
            elif t == "fall/flasher/log":
                out.append(f"[{ts}] LOG {str(p.get('line', ''))[:140]}")
            else:
                out.append(f"[{ts}] TEL {t} {json.dumps(p, ensure_ascii=False)[:110]}")
        except Exception:
            continue
    return "\n".join(out) if out else "（记录为空）"


def dev_flash_abort() -> str:
    """发布 abort 指令中断当前烧录流程（监工异常干预）。"""
    ok = _monitor_publish("fall/commands/flasher-board", {"cmd": "abort"})
    _log_evolution("flash_cmd_sent", "abort")
    return "已向烧录板发出 abort 指令" if ok else "MQTT 监听器未就绪，无法发布"


# 运行时指令白名单：即时操作走这里，不要走固件部署
BOARD_COMMAND_WHITELIST = {"ota_check", "led_on", "led_off", "led_green_on", "led_blue_on", "reboot"}


def dev_self_verify(device_id: str = "", field: str = "", op: str = ">=", value: str = "", timeout_s: int = 60) -> str:
    """功能级遥测自验收：读取设备最新遥测，断言指定字段满足条件。

    例：dev_self_verify("5798", "led", ">=", "1", 60)  —— 60 秒内遥测出现 led>=1 即通过
    例：dev_self_verify("5798", "fw", "==", "0.17.0", 90) —— 板子已跑到新版本
    例：dev_self_verify("5798", "temp_c", ">=", "45", 120) —— 加热到 45 度
    判定规则：字段来自硬件回读（固件诚实上报）才算数；超时未满足返回失败详情。
    用法：部署后先自验收，通过就播报"已验证"，不必再问用户；自验收失败才需要用户人工确认。
    """
    import time as _t
    device_id = _resolve_board_id((device_id or "").strip())
    if not device_id:
        return "请提供目标设备 id（如 board-s3-5798）"
    _reg_info = _load_devices().get("devices", {}).get(device_id) or {}
    if not _reg_info:
        known = "、".join(sorted(_load_devices().get("devices", {}).keys())) or "（暂无登记设备）"
        return f"未知设备 {device_id}，可选：{known}。新板请先用 dev_first_flash/dev_register_board 接入。"
    field = (field or "").strip()
    if not field:
        return "请提供要断言的遥测字段（如 led / fw / uptime_s / temp_c）"
    op = (op or "").strip()
    if op not in ("==", "!=", ">", "<", ">=", "<="):
        return f"不支持的比较符 {op!r}，可用：== != > < >= <="
    try:
        timeout_s = max(5, min(int(timeout_s), 300))
    except Exception:
        timeout_s = 60

    def _cmp(a, b):
        try:
            x, y = float(a), float(b)
            return x, y
        except Exception:
            pass
        # 版本号比较（0.11.0 vs 0.9.0 按数字段比较）
        def _ver(v):
            parts = str(v).split('.')
            return tuple(int(p) if p.isdigit() else p for p in parts)
        try:
            x, y = _ver(a), _ver(b)
            if isinstance(x, tuple) and isinstance(y, tuple) and len(x) == len(y):
                return x, y
        except Exception:
            pass
        return str(a), str(b)

    import json as _json
    deadline = _t.time() + timeout_s
    seen = []
    while _t.time() < deadline:
        try:
            for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-120:]:
                try:
                    rec = _json.loads(ln)
                except Exception:
                    continue
                if rec.get("topic") != f"fall/telemetry/{device_id}":
                    continue
                pld = rec.get("payload", {})
                if field not in pld:
                    continue
                seen.append(f"{field}={pld[field]}")
                x, y = _cmp(pld[field], value)
                ok = {"==": x == y, "!=": x != y, ">": x > y,
                      "<": x < y, ">=": x >= y, "<=": x <= y}[op]
                if ok:
                    return (f"自验收通过：{device_id} 遥测 {field}{op}{value} 成立"
                            f"（实测 {field}={pld[field]}，最近观测：{'、'.join(seen[-5:])}）")
        except Exception as e:
            return f"自验收异常: {e}"
        _t.sleep(3)
    return (f"自验收失败：{timeout_s}s 内未观察到 {field}{op}{value}"
            f"（最近观测：{'、'.join(seen[-5:]) if seen else '无该字段遥测'}）。"
            "可能需要用户人工确认或检查固件上报逻辑。")


# ═══════════════ 开发者模式门控（2026-08-24）═══════════════
# 语音说"启用开发者模式"才开放开发板功能；MCP 工具工厂/语音链路不受影响。
DEVELOPER_MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "developer_mode.json")


def _dev_mode_enabled() -> bool:
    try:
        if os.path.exists(DEVELOPER_MODE_FILE):
            return bool(json.load(open(DEVELOPER_MODE_FILE, encoding="utf-8")).get("enabled"))
    except Exception:
        pass
    return False


def dev_developer_mode_status() -> str:
    """查询开发者模式状态。"""
    st = "已启用" if _dev_mode_enabled() else "未启用"
    extra = ""
    try:
        d = json.load(open(DEVELOPER_MODE_FILE, encoding="utf-8"))
        if d.get("ts"):
            extra = f"（启用时间 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['ts']))}）"
    except Exception:
        pass
    return f"开发者模式{st}{extra}"


def dev_enable_developer_mode() -> str:
    """启用开发者模式（语音说"启用开发者模式"时调用）：开放 ESP32 开发板开发功能
    （固件部署/烧录/运行时指令/新板接入/回滚/自验收）。工具工厂与语音链路不受影响。"""
    import time as _t
    try:
        json.dump({"enabled": True, "ts": _t.time(), "source": "voice"},
                  open(DEVELOPER_MODE_FILE, "w", encoding="utf-8"))
        _log_evolution("developer_mode_on", f"语音启用")
        try:
            dev_speak("开发者模式已开启，开发板相关功能现在可以使用了")
        except Exception:
            pass
        return "开发者模式已开启：固件部署、烧录、板子指令、新板接入等功能已开放。关闭请说'关闭开发者模式'。"
    except Exception as e:
        return f"启用失败: {e}"


def dev_disable_developer_mode() -> str:
    """关闭开发者模式（语音说"关闭开发者模式"时调用）：收回开发板开发功能。"""
    import time as _t
    try:
        json.dump({"enabled": False, "ts": _t.time(), "source": "voice"},
                  open(DEVELOPER_MODE_FILE, "w", encoding="utf-8"))
        _log_evolution("developer_mode_off", f"语音关闭")
        try:
            dev_speak("开发者模式已关闭")
        except Exception:
            pass
        return "开发者模式已关闭：开发板开发功能已收回，查询类功能不受影响。"
    except Exception as e:
        return f"关闭失败: {e}"


def dev_board_command(device_id: str = "", command: str = "") -> str:
    """向板子发送运行时指令（MQTT 纯文本命令，秒级生效）。

    适用：开关灯（led_on/led_off）、触发 OTA 检查（ota_check）、重启（reboot）。
    注意：这是即时操作，不改固件。需要修改固件逻辑时请用 dev_ota_deploy。
    """
    device_id = _resolve_board_id((device_id or "").strip())
    if not device_id:
        return "请提供目标设备 id（如 board-s3-5798）"
    command = (command or "").strip()
    # 设备与能力统一以注册表（devices.json）为准
    _reg = _load_devices().get("devices", {})
    _reg_info = _reg.get(device_id) or {}
    if not _reg_info:
        known = "、".join(sorted(_reg.keys())) or "（暂无登记设备）"
        return f"未知设备 {device_id}，可选：{known}。新板请先用 dev_first_flash/dev_register_board 接入。"
    board_cmds = _reg_info.get("runtime_commands") or []
    # 特殊协议板（JSON 命令，不在文本白名单）→ 指引专用工具
    if board_cmds and any(c not in BOARD_COMMAND_WHITELIST for c in board_cmds):
        if command in board_cmds or command not in BOARD_COMMAND_WHITELIST:
            return (f"{device_id} 是特殊协议板（支持 {', '.join(board_cmds)}），"
                    "不适用文本命令；请用 dev_flash_start / dev_flash_abort / dev_first_flash_status。")
    if command not in BOARD_COMMAND_WHITELIST:
        return (f"不支持的命令 {command!r}，可用：{', '.join(sorted(BOARD_COMMAND_WHITELIST))}"
                "。改固件逻辑请用 dev_ota_deploy。")
    if not board_cmds:
        return (f"{device_id} 的固件没有运行时命令支持（能力为空，当前只能 OTA 升级）。"
                "如需遥控功能请先升级固件（dev_ota_deploy）。")
    if command not in board_cmds:
        return (f"{device_id} 的固件不支持 {command}（该板支持：{', '.join(board_cmds)}）。"
                "如需新命令先升级固件（dev_ota_deploy），或改固件逻辑。")
    try:
        _publish_mqtt_command(command, _reg_info.get("mqtt_device_id", device_id))
        return f"已向 {device_id} 发送 {command} 指令（生效约 1-2 秒，可通过遥测确认）"
    except Exception as e:
        return f"指令发送失败: {e}"


def dev_speak(text: str, device_id: str = "") -> str:
    """#9a 厨师开口：把文本推给语音服务员（xiaozhi），LLM 润色后 TTS 播报。
    语音板在线时立即开口；不在线返回提示，不影响其他流程。"""
    if not text or not text.strip():
        return "请提供要播报的文本"
    try:
        import httpx
        token = os.environ.get("FALL_APP_TOKEN", "")
        r = httpx.post("http://127.0.0.1:8003/api/push",
                       json={"token": token, "text": text.strip(), "device_id": device_id},
                       timeout=10)
        if r.status_code == 200:
            return f"已推送给语音服务员，播报中（设备 {r.json().get('device', '?')}）"
        return f"推送失败 HTTP {r.status_code}: {r.text[:160]}"
    except Exception as e:
        return f"推送异常: {e}"


FLASH_SUPERVISE_TEMPLATE = """你是 EvoAgent 的烧录监工（厨师）。用户要求给目标设备烧录固件，你需要完整监工一次云端烧录，并向用户汇报关键节点。
重写(2026-08-23)：你的环境没有 MCP 工具，所有操作通过 bash 完成。

目标设备：{device}

【你的操作手段（bash）】
- 读事件流（唯一事实源）：tail -60 /opt/fall-mcp/flash_events.jsonl
  用 python3 解析：python3 -c "import json,sys; [print(json.loads(l)['topic'], json.loads(l)['payload']) for l in open('/opt/fall-mcp/flash_events.jsonl').read().splitlines()[-40:]]"
- 向烧录板发指令：docker exec mqtt mosquitto_pub -h localhost -u YOUR_MQTT_USER -P 'YOUR_MQTT_PASSWORD' \
    -t fall/commands/flasher-board -m '{{"cmd":"flash_start","device":"{device}"}}'
  abort 同理：-m '{{"cmd":"abort"}}'
- 向用户播报：写确认队列（confirm poller 会自动语音播报）：
  python3 -c "import json,time; open('/opt/fall-mcp/confirm_queue/{device}.jsonl','a').write(json.dumps({{'q':'<口语化播报内容>','ts':time.time()}},ensure_ascii=False)+chr(10))"

【监工步骤】
1. 发起烧录：发 flash_start 指令（见上）
2. 每 15 秒读一次事件流（最多 30 轮），解析出事件与日志
3. 决策（必须基于真实事件，禁止臆造）：
   - EVT waiting + LOG"连接失败 N 次"→ 目标板未接线/未进下载模式：播报"请接线并按住 BOOT 按 RST 进入下载模式"，继续等待
   - EVT connected → 等待 progress 增长（0%→100%）
   - progress 同一百分比停滞超过 60 秒 → 发 abort，播报失败并建议重试
   - EVT error → 分析 err：no_latest（设备名/仓库错）→ 播报并停止；aborted → 播报已中断；
     md5/write → 烧录失败：自动重试（最多 2 次，重新发 flash_start），仍失败则播报"请重新接线后重试"
   - EVT done → 播报"烧录完成，MD5 校验通过"，随后在日志中找 TGT> 行确认目标板启动
4. 结束：done（成功）或确认失败原因（失败），把最终结论写入确认队列播报。

【规则】只在关键节点播报，不刷屏；所有判断基于事件流真实数据。"""




# ═══════════════ #17 云端编排：新板接入全自动流程（dev_first_flash）═══════════════
# 把"新板接入"（配置→编译→归档→烧录→监控→遥测→登记→播报）固化为一个工具。
# 流程确定性执行 + dev_speak 播报进度（用户只需接线+BOOT 物理操作）。

FIRST_FLASH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "first_flash")
BOARD_TEMPLATE_DIR = "/opt/firmware/board-template"

BOARD_H_TEMPLATE = """/* {model}（{mac}）：{device_id} */
#define BOARD_DEVICE_ID      "{device_id}"
#define BOARD_MODEL          "{model}"
#define BOARD_WIFI_SSID      "YOUR_WIFI_SSID"
#define BOARD_WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"
#define BOARD_API_BASE      "http://YOUR_SERVER_HOST"
#define BOARD_MQTT_HOST      "YOUR_SERVER_HOST"
#define BOARD_MQTT_PORT      1883
#define BOARD_MQTT_USER      "YOUR_MQTT_USER"
#define BOARD_MQTT_PASS      "YOUR_MQTT_PASSWORD"
#define BOARD_TELEMETRY_TOPIC "fall/telemetry/" BOARD_DEVICE_ID
#define BOARD_COMMAND_TOPIC   "fall/commands/" BOARD_DEVICE_ID
#define BOARD_LED_GPIO       48
#define BOARD_FW_VERSION      "0.1.0"
"""


def _ff_state(device_id: str) -> dict:
    import time as _t
    os.makedirs(FIRST_FLASH_DIR, exist_ok=True)
    path = os.path.join(FIRST_FLASH_DIR, f"{device_id}.json")
    st = {}
    if os.path.exists(path):
        try:
            st = json.load(open(path, encoding="utf-8"))
        except Exception:
            st = {}
    st.setdefault("device_id", device_id)
    st.setdefault("step", "init")
    st.setdefault("ts", _t.strftime("%Y-%m-%d %H:%M:%S"))
    st.setdefault("log", [])
    return st


def _ff_save(st: dict) -> None:
    os.makedirs(FIRST_FLASH_DIR, exist_ok=True)
    with open(os.path.join(FIRST_FLASH_DIR, f"{st['device_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def _ff_log(st: dict, msg: str) -> None:
    import time as _t
    st["log"].append(f"[{_t.strftime('%H:%M:%S')}] {msg}")
    st["log"] = st["log"][-50:]
    _ff_save(st)
    print(f"[first-flash] {st['device_id']}: {msg}", flush=True)


def _ff_run(device_id: str, model: str, mac: str) -> None:
    """后台执行：配置→编译→归档→烧录指令→监控→遥测→登记→播报"""
    import subprocess as _sp
    import time as _t
    st = _ff_state(device_id)
    try:
        # 1) 生成板配置
        _ff_log(st, "步骤1/7：生成板配置 boards/%s.h" % device_id)
        header = BOARD_H_TEMPLATE.format(device_id=device_id, model=model or "esp32s3-generic", mac=mac or "unknown")
        cfg_path = os.path.join(BOARD_TEMPLATE_DIR, "main", "boards", f"{device_id}.h")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(header)
        _ff_log(st, "配置已写入 %s" % cfg_path)

        # 2) 编译
        st["step"] = "build"
        _ff_log(st, "步骤2/7：编译引导固件（build_board.sh，约 5-20 分钟）")
        dev_speak(f"开始接入新板 {device_id}，正在编译引导固件，预计需要几分钟")
        env = dict(os.environ)
        env.update({
            "NVM_DIR": os.path.expanduser("~/.nvm"),
            "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
        })
        proc = _sp.run(["bash", os.path.join(BOARD_TEMPLATE_DIR, "build_board.sh"), device_id],
                       cwd=BOARD_TEMPLATE_DIR, env=env, capture_output=True, text=True, timeout=1500)
        if proc.returncode != 0:
            raise RuntimeError(f"编译失败: {(proc.stdout or '')[-400:]}")
        _ff_log(st, "编译成功")

        # 3) 合并 full.bin + 归档
        st["step"] = "archive"
        _ff_log(st, "步骤3/7：合并 full.bin 并归档 /opt/ota/%s/" % device_id)
        build_dir = os.path.join(BOARD_TEMPLATE_DIR, f"build-{device_id}")
        dst = os.path.join("/opt/ota", device_id)
        os.makedirs(dst, exist_ok=True)
        parts = [
            (0x0, os.path.join(build_dir, "bootloader/bootloader.bin")),
            (0x8000, os.path.join(build_dir, "partition_table/partition-table.bin")),
            (0xF000, os.path.join(build_dir, "ota_data_initial.bin")),
            (0x20000, os.path.join(build_dir, "board_template.bin")),
        ]
        end = max(off + os.path.getsize(p) for off, p in parts)
        buf = bytearray(end)
        for off, p in parts:
            data = open(p, "rb").read()
            buf[off:off + len(data)] = data
        open(os.path.join(dst, "full.bin"), "wb").write(buf)
        import shutil as _sh
        _copy_map = {
            "board_template.bin": os.path.join(build_dir, "board_template.bin"),
            "bootloader.bin": os.path.join(build_dir, "bootloader", "bootloader.bin"),
            "partition-table.bin": os.path.join(build_dir, "partition_table", "partition-table.bin"),
            "ota_data_initial.bin": os.path.join(build_dir, "ota_data_initial.bin"),
        }
        for _name, _srcp in _copy_map.items():
            _sh.copy2(_srcp, os.path.join(dst, _name))
        import hashlib as _hl
        def _h(p): return _hl.sha256(open(p, "rb").read()).hexdigest()
        app = os.path.join(dst, "board_template.bin")
        full = os.path.join(dst, "full.bin")
        latest = {
            "version": "v0.1.0",
            "bin": "board_template.bin",
            "size": os.path.getsize(app),
            "sha256": _h(app),
            "full": {"bin": "full.bin", "size": os.path.getsize(full), "sha256": _h(full)},
        }
        with open(os.path.join(dst, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=1)
        _ff_log(st, "归档完成（full.bin %d B）" % len(buf))

        # 4) 发烧录指令
        st["step"] = "flash_cmd"
        _ff_log(st, "步骤4/7：向烧录板发出 flash_start")
        dev_speak(f"{device_id} 的引导固件已就绪，请把新板接到烧录板，按住 BOOT 再按 RST 进入下载模式")
        ok = _monitor_publish("fall/commands/flasher-board", {"cmd": "flash_start", "device": device_id})
        if not ok:
            raise RuntimeError("MQTT 监听器未就绪，无法发指令")
        _ff_log(st, "flash_start 已发出")

        # 5) 监控事件流（waiting→connected→progress→done，最多 10 分钟）
        st["step"] = "flash_watch"
        _ff_log(st, "步骤5/7：监控烧录事件流（等待目标接线进下载模式）")
        deadline = _t.time() + 600
        done = False
        while _t.time() < deadline:
            _t.sleep(8)
            try:
                lines = open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-60:]
            except Exception:
                continue
            for ln in lines:
                try:
                    rec = json.loads(ln)
                    p = rec.get("payload", {})
                    if rec.get("topic") == "fall/flasher/events" and p.get("device") == device_id:
                        t = p.get("type")
                        if t == "connected":
                            _ff_log(st, "目标已连接（ROM loader），开始烧录")
                            dev_speak("已找到目标板，开始烧录")
                        elif t == "progress":
                            pct = p.get("pct")
                            if pct and pct % 50 == 0:
                                _ff_log(st, "烧录进度 %d%%" % pct)
                        elif t == "done":
                            _ff_log(st, "烧录完成（MD5 通过）")
                            dev_speak(f"{device_id} 烧录完成，校验通过，请按一下新板 RST 启动系统")
                            done = True
                            break
                        elif t == "error":
                            raise RuntimeError(f"烧录失败: {p.get('err')}")
                except Exception:
                    continue
            if done:
                break
        if not done:
            raise RuntimeError("烧录超时（10 分钟未完成，检查接线与下载模式）")

        # 6) 等遥测（自检确认，最多 3 分钟）
        st["step"] = "telemetry"
        _ff_log(st, "步骤6/7：等待新板遥测（自检确认）")
        t_deadline = _t.time() + 180
        telemetry_ok = False
        while _t.time() < t_deadline:
            _t.sleep(8)
            try:
                lines = open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-40:]
            except Exception:
                continue
            for ln in lines:
                try:
                    rec = json.loads(ln)
                    if rec.get("topic") == f"fall/telemetry/{device_id}":
                        p = rec.get("payload", {})
                        _ff_log(st, f"遥测到达：fw={p.get('fw')} rssi={p.get('rssi')} uptime={p.get('uptime_s')}s")
                        telemetry_ok = True
                        break
                except Exception:
                    continue
            if telemetry_ok:
                break
        if not telemetry_ok:
            raise RuntimeError("遥测未到达（3 分钟），新板可能未启动，请检查")

        # 7) 登记 + 播报
        st["step"] = "register"
        _ff_log(st, "步骤7/7：登记注册表")
        register_board(device_id, model=model or "esp32s3-generic", mac=mac or "",
                       notes="dev_first_flash 自动接入：首烧引导固件 v0.1.0，遥测正常")
        # E3 接入档案沉淀（2026-08-24）：成功接入 → 生成可复用档案（新板接入时 DSH 参考）
        try:
            _save_onboarding_archive(device_id, model or "esp32s3-generic", mac or "",
                                     first_telemetry=p if "p" in dir() else {})
        except Exception as _ae:
            print(f"[onboarding] 档案生成失败: {_ae}", flush=True)
        st["done"] = True
        _ff_log(st, "接入完成！")
        dev_speak(f"新板 {device_id} 接入完成，遥测正常，以后可以直接对它无线升级了")
        _log_evolution("first_flash_done", f"{device_id} 接入完成")
    except Exception as e:
        st["step"] = "failed"
        st["error"] = str(e)[:300]
        _ff_log(st, f"失败: {e}")
        try:
            dev_speak(f"{device_id} 接入流程出错了：{str(e)[:80]}")
        except Exception:
            pass
    _ff_save(st)


_offline_alert_ts = {}  # 板子离线告警限频


_telemetry_health_ts = {}  # (board, kind) -> last alert ts


def _analyze_telemetry_health() -> None:
    """遥测异常分析：重启频发 / 内存骤降 / 信号弱 → 语音告警（每类 30 分钟限频）。
    门控（2026-08-24）：开发者模式关闭时不播报。"""
    import time as _t4
    try:
        if not _dev_mode_enabled():
            return
        if not os.path.exists(FLASH_EVENT_LOG):
            return
        now = _t4.time()
        board_records = {}
        for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-400:]:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            t = rec.get("topic", "")
            if t.startswith("fall/telemetry/") and now - rec.get("ts", 0) < 1200:
                dev = t.split("/")[-1]
                board_records.setdefault(dev, []).append((rec.get("ts", 0), rec.get("payload", {})))
        for dev, recs in board_records.items():
            recs.sort()
            # --- 重启检测：uptime_s 骤降（豁免 OTA 升级重启 restart_reason=3）---
            restarts = 0
            prev_uptime = None
            for ts, p in recs:
                u = p.get("uptime_s")
                if u is None:
                    continue
                if prev_uptime is not None and u < prev_uptime * 0.5 and u < 120:
                    # 2026-08-25：OTA 升级后的正常重启（restart_reason=3=ESP_RST_SW）不算异常
                    if p.get("restart_reason") not in (3, 9):
                        restarts += 1
                prev_uptime = u
            if restarts >= 2:
                k = (dev, "restart")
                if now - _telemetry_health_ts.get(k, 0) > 1800:
                    _telemetry_health_ts[k] = now
                    dev_speak(f"{dev} 最近 20 分钟内重启了 {restarts} 次，可能供电不稳或代码异常，建议检查一下")
            # --- 内存骤降：free_heap 连续下降 >30% ---
            heap_vals = [p.get("free_heap") for _, p in recs if p.get("free_heap") is not None]
            if len(heap_vals) >= 3 and heap_vals[-1] < heap_vals[0] * 0.7:
                k = (dev, "heap")
                if now - _telemetry_health_ts.get(k, 0) > 1800:
                    _telemetry_health_ts[k] = now
                    dev_speak(f"{dev} 可用内存从 {heap_vals[0]} 掉到 {heap_vals[-1]}，可能有内存泄漏，建议关注")
            # --- 信号弱：rssi 持续 < -85 ---
            rssi_vals = [p.get("rssi") for _, p in recs if p.get("rssi") is not None]
            if len(rssi_vals) >= 3 and all(r < -85 for r in rssi_vals[-3:]):
                k = (dev, "rssi")
                if now - _telemetry_health_ts.get(k, 0) > 1800:
                    _telemetry_health_ts[k] = now
                    dev_speak(f"{dev} WiFi 信号偏弱（{rssi_vals[-1]} dBm），建议移近路由器")
    except Exception as e:
        print(f"[telemetry-health] error: {e}", flush=True)


# ═══════════════ E5 部署自愈 + E7 自诊断（2026-08-24）═══════════════
_DEPLOY_HEAL_STATE = {}   # device -> {attempts, last_pub, last_report, muted_until}
DIAG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_reports")


def _fw_cmp(remote: str, local: str) -> bool:
    """remote > local 版本比较（v 前缀兼容）。"""
    def _ver(v):
        parts = str(v).strip().lstrip("v").split(".")
        out = []
        for x in parts:
            try:
                out.append(int(x))
            except Exception:
                out.append(0)
        return tuple(out)
    return _ver(remote) > _ver(local)


def _board_current_fw(device_id: str) -> str:
    """最近遥测的 fw 版本（无则空串）。"""
    import time as _t7
    now = _t7.time()
    fw = ""
    try:
        for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-200:]:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("topic") != f"fall/telemetry/{device_id}":
                continue
            if now - rec.get("ts", 0) > 1800:
                continue
            # 取最新（文件时间序，后面的匹配覆盖前面的）
            fw = str(rec.get("payload", {}).get("fw", ""))
    except Exception:
        pass
    return fw


def _recent_command(device_id: str, cmd: str, window_s: int = 600) -> bool:
    """命令事件流里最近 window_s 内是否发过该命令。"""
    import time as _t8
    now = _t8.time()
    try:
        for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-2000:]:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("direction") != "cmd":
                continue
            if rec.get("topic") != f"fall/commands/{device_id}":
                continue
            if str(rec.get("payload", "")).strip() != cmd:
                continue
            if now - rec.get("ts", 0) <= window_s:
                return True
    except Exception:
        pass
    return False


def _deploy_diag(device_id: str) -> str:
    """E7 自诊断报告：定位部署卡在哪个断点，落盘 diag_reports/ 并返回摘要。"""
    import time as _t9
    os.makedirs(DIAG_DIR, exist_ok=True)
    lines = [f"# 部署诊断报告：{device_id}", f"- 生成时间: {_t9.strftime('%Y-%m-%d %H:%M:%S')}"]
    # 1. 期望版本 vs 实际版本
    latest = ""
    try:
        latest = str(json.load(open(os.path.join(OTA_ROOT, device_id, "latest.json"), encoding="utf-8")).get("version", "")).lstrip("v")
    except Exception:
        pass
    cur = _board_current_fw(device_id)
    lines.append(f"- 目标版本: {latest or '未知'}；板子当前: {cur or '无遥测'}")
    # 2. 断点定位
    if not cur:
        lines.append("- 断点: 板子无遥测（离线或未启动）→ 检查供电/网络")
    elif latest and _fw_cmp(latest, cur):
        pub = _recent_command(device_id, "ota_check", 600)
        if not pub:
            lines.append("- 断点: ota_check 从未发布 → 部署任务未完成或发布失败")
        else:
            lines.append("- 断点: ota_check 已发布但板子未升级 → 命令丢失(QoS0)/下载失败/固件 OTA 逻辑异常")
            lines.append("  建议: 1) 手动重发 ota_check 2) 验证 /firmware/ 下载 URL 3) 抓板子串口日志")
    else:
        lines.append("- 状态: 板子已是最新（或目标未知），无异常")
    # 3. 命令与遥测时间线
    lines.append("- 事件时间线（最近 5 条命令 + 5 条遥测）:")
    cmd_n, tel_n = 0, 0
    for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-300:]:
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("direction") == "cmd" and rec.get("topic") == f"fall/commands/{device_id}" and cmd_n < 5:
            lines.append(f"  [cmd] {_t9.strftime('%H:%M:%S', _t9.localtime(rec.get('ts', 0)))} {str(rec.get('payload'))[:30]}")
            cmd_n += 1
        elif rec.get("topic") == f"fall/telemetry/{device_id}" and tel_n < 5:
            p = rec.get("payload", {})
            lines.append(f"  [tel] {_t9.strftime('%H:%M:%S', _t9.localtime(rec.get('ts', 0)))} fw={p.get('fw')} uptime={p.get('uptime_s')}s")
            tel_n += 1
    report = "\n".join(lines)
    path = os.path.join(DIAG_DIR, f"{device_id}-{int(_t9.time())}.md")
    try:
        open(path, "w", encoding="utf-8").write(report)
    except Exception:
        pass
    print(f"[diag] 诊断报告已生成: {path}", flush=True)
    return report


def _deploy_heal_check() -> None:
    """E5 部署自愈：发现'归档新版本但板子未升级' → 自动补发 ota_check（≤3 次）→ 仍失败出诊断报告。"""
    import time as _t10
    try:
        if not os.path.isdir(OTA_ROOT):
            return
        now = _t10.time()
        for dev in os.listdir(OTA_ROOT):
            latest_path = os.path.join(OTA_ROOT, dev, "latest.json")
            if not os.path.isfile(latest_path):
                continue
            try:
                latest = str(json.load(open(latest_path, encoding="utf-8")).get("version", "")).lstrip("v")
            except Exception:
                continue
            if not latest:
                continue
            cur = _board_current_fw(dev)
            if not cur:
                continue  # 板子离线，跳过（上线后开机自检会处理）
            if not _fw_cmp(latest, cur):
                continue  # 已是最新，无需自愈
            st = _DEPLOY_HEAL_STATE.setdefault(dev, {"attempts": 0, "last_pub": 0, "last_report": 0})
            # 30 分钟静默期（报告/播报限频）
            if now < st.get("muted_until", 0):
                continue
            if st["attempts"] >= 3:
                # 3 次仍失败 → 诊断报告 + 播报（30 分钟一次）
                if now - st.get("last_report", 0) > 1800:
                    st["last_report"] = now
                    st["muted_until"] = now + 1800
                    _deploy_diag(dev)
                    try:
                        dev_speak(f"{dev} 固件升级一直没成功，我已经重试了 3 次，生成了一份诊断报告，你可以让我读给你听，或者查看服务器上的报告文件")
                    except Exception:
                        pass
                continue
            # 10 分钟内发过 ota_check 且板子未变 → 等下一轮（避免连续轰炸）
            if _recent_command(dev, "ota_check", 600):
                if now - st.get("last_pub", 0) < 180:
                    continue
            # 补发 ota_check
            st["attempts"] += 1
            st["last_pub"] = now
            try:
                _publish_mqtt_command("ota_check", dev)
                print(f"[deploy-heal] {dev} 检测到未升级（目标 {latest}，当前 {cur}），补发 ota_check 第 {st['attempts']} 次", flush=True)
            except Exception as e:
                print(f"[deploy-heal] {dev} 补发失败: {e}", flush=True)
    except Exception as e:
        print(f"[deploy-heal] error: {e}", flush=True)


def start_deploy_heal() -> None:
    """E5 部署自愈线程（30s 周期）"""
    import threading as _th3
    import time as _t11

    def _loop():
        while True:
            _t11.sleep(30)
            try:
                _deploy_heal_check()
            except Exception:
                pass

    t = _th3.Thread(target=_loop, daemon=True, name="deploy-heal")
    t.start()
    _thread_registry["deploy-heal"] = t
    print("[fall-mcp] deploy heal started", flush=True)


def start_telemetry_health() -> None:
    """稳定性：遥测健康分析线程（60s 周期，异常语音告警）"""
    import threading as _th2
    import time as _t5

    def _loop():
        while True:
            _t5.sleep(60)
            try:
                _analyze_telemetry_health()
            except Exception:
                pass

    t = _th2.Thread(target=_loop, daemon=True, name="telemetry-health")
    t.start()
    _thread_registry["telemetry-health"] = t
    print("[fall-mcp] telemetry health started", flush=True)


def start_offline_alert() -> None:
    """稳定性：板子心跳超过 5 分钟 → 自动语音告警（每板 30 分钟限频）"""
    import threading as _th
    import time as _t

    _alert_count = {}
    _recovered_announced = set()  # 本次离线周期是否已播报恢复

    def _loop():
        while True:
            _t.sleep(60)
            try:
                # 门控（2026-08-24）：开发者模式关闭时，不播报开发板离线/恢复
                if not _dev_mode_enabled():
                    continue
                st = json.load(open(ONLINE_STATE_FILE, encoding="utf-8"))
                now = _t.time()
                for k, v in st.items():
                    age = now - v.get("ts", 0)
                    last = _offline_alert_ts.get(k, 0)
                    if age <= 300:
                        # 板子回来了：重置告警计数；若此前掉线并告警过 → 播报恢复
                        if _alert_count.get(k, 0) > 0 and k not in _recovered_announced:
                            _recovered_announced.add(k)
                            _r = dev_speak(f"{k} 已经恢复在线了")
                            if "已推送" not in _r:
                                print(f"[offline-alert] {k} 恢复播报失败: {_r[:80]}", flush=True)
                            else:
                                print(f"[offline-alert] {k} 恢复在线已播报", flush=True)
                        _alert_count[k] = 0
                        continue
                    else:
                        _recovered_announced.discard(k)
                    if _alert_count.get(k, 0) >= 3:
                        # 已连续告警 3 次，当日静默（板上线后重置），防长期离线骚扰
                        continue
                    if now - last > 1800:
                        _offline_alert_ts[k] = now
                        _alert_count[k] = _alert_count.get(k, 0) + 1
                        r = dev_speak(f"{k} 已经离线超过 5 分钟了，请检查一下供电或连接，插好或者恢复后我会告诉你")
                        if "已推送" not in r:
                            print(f"[offline-alert] {k} 告警播报失败: {r[:100]}", flush=True)
            except Exception as e:
                print(f"[offline-alert] error: {e}", flush=True)

    t = _th.Thread(target=_loop, daemon=True, name="offline-alert")
    t.start()
    _thread_registry["offline-alert"] = t
    print("[fall-mcp] offline alert started", flush=True)


def start_dsh_task_guard() -> None:
    """稳定性：DSH 任务进程运行超 45 分钟自动清理（防卡死残留）"""
    import threading as _th
    import time as _t
    import subprocess as _sp

    def _loop():
        while True:
            _t.sleep(300)
            try:
                out = _sp.run(["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, timeout=20).stdout
                for ln in out.splitlines():
                    if "dsh_task_" in ln and "grep" not in ln:
                        parts = ln.split(None, 2)
                        if len(parts) == 3 and parts[1].isdigit() and int(parts[1]) > 2700:
                            pid = int(parts[0])
                            print(f"[dsh-guard] 任务进程超时清理 pid={pid} etimes={parts[1]}s", flush=True)
                            try:
                                _sp.run(["kill", str(pid)], timeout=10)
                            except Exception:
                                pass
            except Exception as e:
                print(f"[dsh-guard] error: {e}", flush=True)

    t = _th.Thread(target=_loop, daemon=True, name="dsh-task-guard")
    t.start()
    _thread_registry["dsh-task-guard"] = t
    print("[fall-mcp] dsh task guard started", flush=True)


def start_thread_watchdog() -> None:
    """线程守护（2026-08-23）：confirm-poller/flash-monitor 等后台线程死亡时自动重启。
    fall-mcp 启动时调用一次。"""
    import threading as _th
    import time as _t
    _RESTART = {
        "confirm-poller": start_confirm_poller,
        "flash-monitor": start_flash_monitor,
    }

    def _loop():
        while True:
            _t.sleep(30)
            for name, fn in list(_RESTART.items()):
                t = _thread_registry.get(name)
                if t is None or not t.is_alive():
                    print(f"[fall-mcp] ⚠️ 线程 {name} 死亡，自动重启", flush=True)
                    try:
                        fn()
                    except Exception as e:
                        print(f"[fall-mcp] 线程 {name} 重启失败: {e}", flush=True)

    _th.Thread(target=_loop, daemon=True, name="thread-watchdog").start()
    print("[fall-mcp] thread watchdog started", flush=True)


def dev_flash_supervise(device: str = "") -> str:
    """#8 监工：派发 DSH 烧录监工任务（DSH 用 bash 监工烧录全程：发指令→盯事件流→决策→播报）。
    烧录完成后用户会收到语音播报的关键节点与结论。"""
    import threading as _th
    device = (device or "").strip().lower()
    if not device:
        return "请提供目标设备 id（如 oled-display）"
    task = FLASH_SUPERVISE_TEMPLATE.format(device=device)

    def _bg():
        try:
            import subprocess as _sp
            import time as _t
            env = dict(os.environ)
            env.update({
                "NVM_DIR": os.path.expanduser("~/.nvm"),
                "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
                "DSH_PERMISSION_MODE": "danger-full-access",
            })
            cmd, _ = _dsh_cmd(task)
            proc = _sp.run(cmd, cwd="/opt/fall-mcp", env=env, capture_output=True, text=True, timeout=1800)
            out = (proc.stdout or "") + (proc.stderr or "")
            print(f"[flash-supervise] DSH 监工完成 rc={proc.returncode}: {out[-300:]}", flush=True)
            _log_evolution("flash_supervised", f"{device} rc={proc.returncode}")
        except Exception as e:
            print(f"[flash-supervise] ERROR: {e}", flush=True)
            _log_evolution("flash_supervise_failed", f"{device} | {e}")

    _th.Thread(target=_bg, daemon=True).start()
    return f"烧录监工已启动（目标 {device}）：DSH 会盯事件流、播报关键节点、失败自动重试。用 dev_flash_status 可随时查看进度。"


def dev_first_flash(device_id: str = "", model: str = "", mac: str = "", notes: str = "") -> str:
    """#17 云端编排：新板接入全自动流程（配置→编译→归档→烧录→监控→遥测→登记→播报）。
    用户只需物理接线 + BOOT/RST；进度可用 dev_first_flash_status 查询。"""
    import threading as _th
    device_id = (device_id or "").strip().lower()
    if not device_id:
        return "请提供 device_id（如 board-s3-477c；也可用 MAC 末 4 位命名）"
    st = _ff_state(device_id)
    if st.get("step") not in ("init", "failed") and not st.get("done"):
        return f"{device_id} 已有进行中的接入流程（步骤 {st.get('step')}），用 dev_first_flash_status 查询"
    _th.Thread(target=_ff_run, args=(device_id, model or "esp32s3-generic", mac or ""), daemon=True).start()
    _log_evolution("first_flash_start", f"{device_id} ({model})")
    return (f"新板 {device_id} 接入流程已启动：将自动完成 配置→编译→归档→烧录→遥测确认→登记。"
            f"请把新板接到烧录板（GPIO1→RX、GPIO2→TX、3V3、GND）并准备 BOOT/RST；"
            f"过程中语音助手会播报进度。用 dev_first_flash_status 查询详细进度。")


def dev_first_flash_status(device_id: str = "") -> str:
    """查询新板接入流程进度。"""
    if not os.path.isdir(FIRST_FLASH_DIR):
        return "还没有接入记录"
    if device_id:
        files = [os.path.join(FIRST_FLASH_DIR, f"{device_id}.json")]
    else:
        import glob as _g
        files = sorted(_g.glob(os.path.join(FIRST_FLASH_DIR, "*.json")), reverse=True)[:3]
    out = []
    for p in files:
        if not os.path.exists(p):
            continue
        try:
            st = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        status = "完成" if st.get("done") else ("失败" if st.get("step") == "failed" else f"进行中（步骤 {st.get('step')}）")
        out.append(f"{st['device_id']}: {status}" + (f" | {st.get('error', '')[:60]}" if st.get("error") else ""))
        for ln in st.get("log", [])[-4:]:
            out.append(f"   {ln}")
    return "\n".join(out) if out else "（无记录）"


def _load_plugins() -> dict:
    if os.path.exists(PLUGINS_FILE):
        try:
            with open(PLUGINS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"plugins": {}}


def _save_plugins(p: dict) -> None:
    with open(PLUGINS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def _run_dsh2(req_path: str) -> None:
    """起 DSH-2 进程执行插件制造+安装（模板内完成全部工作与状态标记）。
    DSH-2 是受信任的制造者：全权限（需写 /opt/dsh-plugins 与 headless patch）。"""
    import subprocess
    task = PLUGIN_BUILD_TEMPLATE.replace("{req_path}", req_path).replace("{patch_path}", HEADLESS_PATCH)
    env = dict(os.environ)
    env.update({
        "NVM_DIR": os.path.expanduser("~/.nvm"),
        "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
        "DSH_PERMISSION_MODE": "danger-full-access",
    })
    try:
        cmd, _ = _dsh_cmd(task, profile="headless-builder")  # DSH-2 跑隔离环境，永远不受业务插件影响
        proc = subprocess.run(
            cmd,
            cwd="/opt/fall-mcp",
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        print(f"[plugin-poller] DSH-2 done {req_path} rc={proc.returncode}", flush=True)
        # P0-4 交付代码二次扫描（不依赖模型自检）
        try:
            _post_install_scan()
        except Exception as e:
            print(f"[plugin-poller] post-install scan error: {e}", flush=True)
        # 两层进化保险：装完立即健康检查 DSH-1（装坏则自动修复/DSH-2 兜底）
        try:
            _maybe_repair_headless("post_install")
        except Exception as e:
            print(f"[plugin-poller] post-install health check error: {e}", flush=True)
        # 需求文件状态由 DSH-2 按模板写入；兜底：进程异常退出则标记 failed
        try:
            req = json.load(open(req_path, encoding="utf-8"))
            if req.get("status") in ("pending", "processing"):
                req["status"] = "failed"
                req["error"] = f"DSH-2 异常退出 rc={proc.returncode}: {(proc.stderr or '')[-300:]}"
                json.dump(req, open(req_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    except Exception as exc:
        print(f"[plugin-poller] DSH-2 ERROR {req_path}: {exc}", flush=True)
        try:
            req = json.load(open(req_path, encoding="utf-8"))
            req["status"] = "failed"
            req["error"] = str(exc)
            json.dump(req, open(req_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass


def _process_plugin_requests() -> None:
    """轮询：发现 pending 需求 → 标 processing → 起 DSH-2（一次一个，串行安装）。"""
    import threading
    import time as _t
    if not os.path.isdir(PLUGIN_REQUEST_DIR):
        return
    for fn in sorted(os.listdir(PLUGIN_REQUEST_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(PLUGIN_REQUEST_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                req = json.load(f)
        except Exception:
            continue
        if req.get("status", "pending") != "pending":
            continue
        # P0-1 需求字段校验（不合法直接拒绝，不浪费 DSH-2）
        err = _validate_plugin_request(req)
        if err:
            req["status"] = "rejected"
            req["error"] = f"需求校验失败: {err}"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(req, f, ensure_ascii=False, indent=1)
            _log_evolution("plugin_req_rejected", f"{fn}: {err}")
            continue
        # P0-2 工具名冲突落盘校验（内置/已装插件/MCP 三张表）
        conflicts = _tool_name_conflicts(req)
        if conflicts:
            req["status"] = "rejected"
            req["error"] = f"工具名冲突: {'; '.join(conflicts)}"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(req, f, ensure_ascii=False, indent=1)
            _log_evolution("plugin_req_rejected", f"{fn}: 冲突 {conflicts}")
            continue
        # 防重复：查插件注册表能力是否已存在（按工具名）
        plugins = _load_plugins()
        want_tools = {t.get("name") for t in req.get("tools", []) if t.get("name")}
        installed = set()
        for p in plugins.get("plugins", {}).values():
            installed.update(p.get("tools", []))
        dup = want_tools & installed
        if dup:
            req["status"] = "skipped"
            req["error"] = f"已有相同能力工具: {dup}"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(req, f, ensure_ascii=False, indent=1)
            _log_evolution("plugin_req_skipped", f"{fn}: {dup}")
            continue
        req["status"] = "processing"
        req["started_at"] = _t.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False, indent=1)
        _log_evolution("plugin_req_started", f"{fn}: {req.get('capability', '')[:60]}")
        threading.Thread(target=_run_dsh2, args=(path,), daemon=True).start()
        return  # 一次一个


# ═══════════════════ 轻量经验索引（记忆层升级）═══════════════════

# ═══════════════ RAG 经验检索（2026-08-24 升级：embedding + 余弦，bigram 保底）═══════════════
_EMB_CACHE_FILE = os.path.join(TASK_RESULT_DIR, ".exp_embeddings.json")
_emb_cache = {}


def _load_emb_cache() -> dict:
    global _emb_cache
    if _emb_cache:
        return _emb_cache
    try:
        if os.path.exists(_EMB_CACHE_FILE):
            _emb_cache = json.load(open(_EMB_CACHE_FILE, encoding="utf-8"))
    except Exception:
        _emb_cache = {}
    return _emb_cache


def _save_emb_cache() -> None:
    try:
        json.dump(_emb_cache, open(_EMB_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _embed_text(text: str) -> list:
    """dashscope text-embedding-v3 向量化（失败返回 None，调用方降级 bigram）。"""
    text = (text or "").strip()[:512]
    if not text:
        return None
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        return None
    try:
        import dashscope
        rsp = dashscope.TextEmbedding.call(
            model="text-embedding-v3",
            input=text,
            api_key=key,
        )
        if rsp and rsp.status_code == 200 and rsp.output and rsp.output.get("embeddings"):
            return rsp.output["embeddings"][0].get("embedding") or None
        return None
    except Exception as e:
        print(f"[rag] embed 失败: {e}", flush=True)
        return None


def _cosine(a: list, b: list) -> float:
    try:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def _exp_embedding(desc: str, path: str) -> list:
    """经验描述向量（磁盘缓存：按内容 hash，内容变则重算）"""
    import hashlib as _h
    key = _h.md5(desc.encode("utf-8")).hexdigest()
    cache = _load_emb_cache()
    if cache.get(key):
        return cache[key]
    vec = _embed_text(desc)
    if vec:
        cache[key] = vec
        _save_emb_cache()
    return vec or []


def _find_related_experience_rag(task: str, top_n: int = 2) -> list:
    """RAG 检索：embedding 余弦相似度，阈值 0.35；API 不可用返回空（调用方走 bigram）。"""
    qv = _embed_text(task)
    if not qv:
        return []
    scored = []
    for path, desc in _build_experience_index().items():
        dv = _exp_embedding(desc, path)
        if not dv:
            continue
        s = _cosine(qv, dv)
        if s >= 0.55:
            scored.append((s, path, desc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


def _count_failures_by_cause() -> dict:
    """失败聚类：统计 task_results 中各失败原因分类出现次数（含经验中的原因分类行）"""
    counts = {}
    if not os.path.isdir(TASK_RESULT_DIR):
        return counts
    for fn in os.listdir(TASK_RESULT_DIR):
        if not fn.endswith(".txt"):
            continue
        try:
            head = open(os.path.join(TASK_RESULT_DIR, fn), encoding="utf-8").read()[:600]
        except Exception:
            continue
        for ln in head.splitlines():
            if ln.startswith("原因分类:"):
                c = ln.split(":", 1)[1].strip()
                counts[c] = counts.get(c, 0) + 1
    return counts


def _failure_cluster_note(task: str) -> str:
    """失败聚类注入文本：任务相关失败模式若累计>=3 次，标注为已知问题"""
    try:
        counts = _count_failures_by_cause()
        if not counts:
            return ""
        top = sorted(counts.items(), key=lambda x: -x[1])[:3]
        known = [f"{c}×{n}" for c, n in top if n >= 3]
        if known:
            return (f"\n【失败聚类】历史失败模式统计：{'、'.join(known)}。"
                    "若本次任务失败，先对照上述模式自查（同类问题已出现多次，属已知问题，"
                    "按对应经验教训修复，不要重复踩坑）。"
                    f"（全部：{'、'.join(f'{c}×{n}' for c, n in top)}）")
        return f"\n【失败聚类】历史失败模式：{'、'.join(f'{c}×{n}' for c, n in top) or '暂无'}。"
    except Exception:
        return ""


def _build_experience_index() -> dict:
    """扫描 task_results 建立经验索引：{path: 任务描述}。
    兼容两种文件格式：fall-mcp 标准（"任务: xxx"）与 DSH 覆写（"任务概述：/【任务概述】"）。"""
    idx = {}
    if not os.path.isdir(TASK_RESULT_DIR):
        return idx
    for fn in sorted(os.listdir(TASK_RESULT_DIR)):
        if not fn.endswith(".txt"):
            continue
        path = os.path.join(TASK_RESULT_DIR, fn)
        try:
            lines = open(path, encoding="utf-8").read()[:600].split("\n")
            task_line = ""
            for line in lines:
                if line.startswith("任务:"):
                    task_line = line[len("任务:"):].strip()
                    break
                if line.startswith("任务概述："):
                    task_line = line[len("任务概述："):].strip()
                    break
            if not task_line:
                for i, line in enumerate(lines):
                    if "【任务概述】" in line and i + 1 < len(lines) and lines[i + 1].strip():
                        task_line = lines[i + 1].strip()[:100]
                        break
            if task_line:
                idx[path] = task_line
        except Exception:
            continue
    return idx


def _find_related_experience(task: str, top_n: int = 2) -> list:
    """按字符 bigram 相似度匹配相关历史任务经验。返回 [(score, path, desc)]。"""
    q = _grams(task)
    if not q:
        return []
    scored = []
    for path, desc in _build_experience_index().items():
        overlap = len(q & _grams(desc))
        if overlap >= 3:  # ≥3 个字符双联词重叠视为相关
            scored.append((overlap, path, desc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


def _read_experience_confidence(path: str) -> int:
    """读取经验文件置信度（置信度: N 字段，缺省 2）"""
    try:
        for ln in open(path, encoding="utf-8").read().splitlines()[:10]:
            if ln.startswith("置信度:") or ln.startswith("confidence:"):
                return int(ln.split(":", 1)[1].strip())
    except Exception:
        pass
    return 2


def _auto_review(requirement: str, failure_summary: str, cause: str = "") -> str:
    """任务失败自动复盘：生成经验记录（置信度 1=待验证）+ 进化日志留痕。
    cause 分类：fake_done / build_failed / parallel / offline / timeout / other"""
    try:
        import time as _t2
        ts = _t2.strftime("%m%d%H%M%S")
        path = os.path.join(TASK_RESULT_DIR, f"{ts}.txt")
        lessons = {
            "fake_done": "部署任务必须以归档验证通过（latest.json 版本更新 + merged.bin >100KB）为完成标志；"
                         "DSH 禁止把编译/归档丢后台提前退出；未验证归档不得播报'部署完成'。",
            "build_failed": "编译失败：读取错误信息修复重试（最多 5 轮）；5 轮仍失败如实报告失败原因，"
                            "不得宣称成功或让用户空等。",
            "parallel": "同一板子同时只允许一个部署任务（并行编译同一工程树会互相破坏 build 目录）；"
                        "新需求应在当前部署完成后排队。",
            "offline": "板子离线时 MQTT 命令（QoS0）可能丢失；先确认板子在线（dev_list_boards）再发命令/部署。",
            "timeout": "任务超时：检查 DSH 是否卡在等待/轮询；合理设置超时并如实播报进度。",
        }
        lesson = lessons.get(cause, "任务失败需如实向用户报告；未经验证的承诺（'马上就好''应该可以'）会破坏信任，"
                                    "不确定就说明确状态再行动。")
        content = (f"任务ID: {ts}\n状态: 失败（自动复盘）\n任务: {str(requirement)[:200]}\n"
                   f"现象: {str(failure_summary)[:300]}\n原因分类: {cause}\n置信度: 1\n教训: {lesson}\n")
        open(path, "w", encoding="utf-8").write(content)
        _log_evolution("auto_review_created", f"{os.path.basename(path)}: {str(requirement)[:60]} | {cause}")
        print(f"[auto-review] 经验已生成: {os.path.basename(path)} ({cause})", flush=True)
        return path
    except Exception as e:
        print(f"[auto-review] 生成失败: {e}", flush=True)
        return ""


def _bump_experience_confidence(requirement: str, cause: str = "") -> None:
    """经验置信度提升：本次任务成功且无同类失败 → 相关失败经验 置信度+1（已验证一次）"""
    try:
        if not os.path.isdir(TASK_RESULT_DIR):
            return
        import time as _t3
        now = _t3.time()
        q = _grams(requirement)
        if not q:
            return
        for fn in os.listdir(TASK_RESULT_DIR):
            if not fn.endswith(".txt"):
                continue
            path = os.path.join(TASK_RESULT_DIR, fn)
            try:
                head = open(path, encoding="utf-8").read()[:800]
            except Exception:
                continue
            if "失败（自动复盘）" not in head:
                continue
            mtime = os.path.getmtime(path)
            if now - mtime > 86400:  # 只提升近 1 天的失败经验
                continue
            desc = ""
            for ln in head.splitlines():
                if ln.startswith("任务:"):
                    desc = ln[3:].strip()
                    break
            overlap = len(q & _grams(desc))
            if overlap >= 3:
                lines = open(path, encoding="utf-8").read().splitlines()
                today = _t3.strftime("%Y-%m-%d")
                if any(today in ln for ln in lines if ln.startswith("验证记录")):
                    continue  # 同一天已提升过，防重复
                conf = _read_experience_confidence(path)
                for i, ln in enumerate(lines):
                    if ln.startswith("置信度:"):
                        lines[i] = f"置信度: {conf + 1}"
                        lines.insert(i + 1, f"验证记录: {_t3.strftime('%Y-%m-%d %H:%M')} 同类任务成功，教训再次验证")
                        break
                open(path, "w", encoding="utf-8").write("\n".join(lines))
                _log_evolution("exp_confidence_up", f"{fn}: {conf}->{conf + 1}")
                print(f"[exp-confidence] {fn} 置信度 {conf}->{conf + 1}", flush=True)
    except Exception as e:
        print(f"[exp-confidence] 提升失败: {e}", flush=True)


def _grams(text: str) -> set:
    import re as _re
    t = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _dispatch_task(requirement: str, result_path: str, task_id: str) -> None:
    """任务派发执行体：注入相关经验 + 运行 DSH。"""
    # 记忆层注入：检索相关历史经验（RAG 优先，bigram 保底）
    exp_block = ""
    related = _find_related_experience_rag(requirement) or _find_related_experience(requirement)
    if related:
        exp_block = "\n\n【参考经验】服务器上有相似的历史任务，建议先查看其做法再执行：\n"
        parts = []
        for _, p, d in related:
            conf = _read_experience_confidence(p)
            tag = "（已验证，务必遵守）" if conf >= 2 else "（待验证，参考）"
            parts.append(f"- {p}（任务：{d[:50]}）{tag}")
        exp_block += "\n".join(parts)
    task_text = requirement + exp_block + _failure_cluster_note(requirement)
    reply = _run_dsh_general(task_text, result_path)
    return reply


# ═══════════════════ 设备注册表（多板支持）═══════════════════

def _load_devices() -> dict:
    """权威设备注册表：device_id -> 设备信息（token/型号/MAC/能力/固件工程）。"""
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "devices" in data:
                return data
            print(f"[devices] 注册表格式异常，拒绝覆盖: {DEVICES_FILE}", flush=True)
            return {"devices": {}, "_load_error": True}
        except Exception as e:
            print(f"[devices] 注册表读取失败（不覆盖原文件）: {e}", flush=True)
            return {"devices": {}, "_load_error": True}
    return {"devices": {}}


def _save_devices(d: dict) -> None:
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _board_spec(device_id: str) -> dict | None:
    """按 device_id 找固件工程规格；找不到返回 None。"""
    reg = _load_devices()
    return reg.get("devices", {}).get(device_id)


BOARD_TEMPLATE_DIR = "/opt/firmware/board-template"

BOARD_SPECS_FIXED = {
    # device_id -> 固件迭代所需工程参数（OTA_DEPLOY_TEMPLATE 占位符）
    "fall-board": {
        "project": "/opt/firmware/fall-board",
        "bin_path": "build/project_AIbushu_study2.bin",
        "version_hint": "main/device_config.h 的 FALL_FW_VERSION",
        "version_macro": "FALL_FW_VERSION",
        "version_file": "/opt/firmware/fall-board/main/device_config.h",
        "commit_dir": "/opt/firmware/fall-board",
        "ota_dir": "/opt/ota/fall-board",
        "mqtt_device_id": "esp32s3-cam-01",
        "model": "跌倒板（ESP32-S3 + 雷达/视觉）",
    },
}


def _discover_template_boards() -> dict:
    """动态扫描 board-template/main/boards/*.h 生成工程规格。

    新板接入：只要建了 boards/<device_id>.h（含 BOARD_DEVICE_ID / BOARD_FW_VERSION 等宏），
    dev_ota_deploy 自动认识它，无需改任何代码。
    """
    specs = {}
    boards_dir = os.path.join(BOARD_TEMPLATE_DIR, "main", "boards")
    if not os.path.isdir(boards_dir):
        return specs
    for fn in sorted(os.listdir(boards_dir)):
        if not fn.endswith(".h") or fn.startswith("_"):
            continue
        did = fn[:-2]
        specs[did] = {
            "project": BOARD_TEMPLATE_DIR,
            "bin_path": f"build-{did}/board_template.bin",
            "version_hint": f"main/boards/{fn} 的 BOARD_FW_VERSION",
            "version_macro": "BOARD_FW_VERSION",
            "version_file": os.path.join(boards_dir, fn),
            "commit_dir": BOARD_TEMPLATE_DIR,
            "ota_dir": f"/opt/ota/{did}",
            "mqtt_device_id": did,
            "model": f"通用引导固件板（{did}，board-template）",
        }
    return specs


def _board_specs() -> dict:
    """全部可用板规格 = 静态专用工程（fall-board）+ board-template 动态发现。

    静态优先：boards/ 目录里同名文件（如 fall-board.h 模板副本）不会覆盖专用工程。
    """
    specs = dict(BOARD_SPECS_FIXED)
    for key, val in _discover_template_boards().items():
        if key not in specs:
            specs[key] = val
    return specs


ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "onboarding_archives")


def _save_onboarding_archive(device_id: str, model: str, mac: str, first_telemetry: dict = None) -> str:
    """接入档案：记录新板从零到在线的完整参数（配置/烧录/遥测基线），供后续新板复用。"""
    import time as _t6
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    board_h = os.path.join(BOARD_TEMPLATE_DIR, "main", "boards", f"{device_id}.h")
    board_cfg = ""
    try:
        board_cfg = open(board_h, encoding="utf-8").read()[:2000]
    except Exception:
        pass
    tel = first_telemetry or {}
    content = (
        f"# 接入档案：{device_id}\n"
        f"- 接入时间: {_t6.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 型号: {model}\n"
        f"- MAC: {mac or '未知'}\n"
        f"- 遥测基线: fw={tel.get('fw')} rssi={tel.get('rssi')} heap={tel.get('free_heap')} "
        f"uptime={tel.get('uptime_s')}s\n"
        f"- 配置（boards/{device_id}.h 前 2000 字符）:\n```\n{board_cfg}\n```\n"
        f"- 流程: dev_first_flash（编译→合并 full.bin→云端烧录→遥测自检→登记）\n"
    )
    path = os.path.join(ARCHIVE_DIR, f"{device_id}.md")
    open(path, "w", encoding="utf-8").write(content)
    _log_evolution("onboarding_archive", f"{device_id} 接入档案已沉淀")
    print(f"[onboarding] 档案已生成: {path}", flush=True)
    return path


def register_board(device_id: str, model: str = "", mac: str = "",
                   notes: str = "", capabilities: list | None = None) -> str:
    """登记/更新一块开发板（语音接入新板流程的落盘动作）。"""
    import time
    device_id = (device_id or "").strip().lower()
    if not device_id:
        return "请提供 device_id（如 board-s3-36ac）。"
    reg = _load_devices()
    devs = reg.setdefault("devices", {})
    old = devs.get(device_id, {})
    spec = _board_specs().get(device_id, {})
    devs[device_id] = {
        "device_id": device_id,
        "model": model or old.get("model") or spec.get("model", "未知"),
        "mac": mac or old.get("mac", ""),
        "mqtt_device_id": old.get("mqtt_device_id") or spec.get("mqtt_device_id") or device_id,
        "firmware_project": old.get("firmware_project") or spec.get("project", ""),
        "board_config": old.get("board_config") or (f"boards/{device_id}.h" if spec.get("project") == "/opt/firmware/board-template" else None),
        "capabilities": capabilities or old.get("capabilities") or ["ota", "telemetry"],
        "notes": notes or old.get("notes", ""),
        "registered_at": old.get("registered_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen": old.get("last_seen", ""),
        "current_version": old.get("current_version", ""),
        "ota_verified": old.get("ota_verified", False),
    }
    _save_devices(reg)
    _log_evolution("board_registered", f"{device_id} ({devs[device_id]['model']})")
    return json.dumps({
        "ok": True,
        "device": devs[device_id],
        "next_steps": [
            "1. 接线烧录引导固件（build_board.sh <board_id> 编译产物，双分区 OTA 配置）",
            "2. 串口确认启动日志：WiFi 连接 + MQTT 遥测正常",
            "3. 【必做】验证 OTA 往返：发布 v0.1.1 并推送 ota_check 命令，确认板子无线升级成功",
            "4. 确认升级成功后重新调用 dev_register_board 标记 ota_verified=true，板子才算接入完成可离线",
        ],
    }, ensure_ascii=False)


def list_boards() -> str:
    """列出全部已登记板：型号/MAC/版本/能力/最近在线时间。
    修复(2026-08-23)：在线状态按心跳新鲜度判定（5 分钟内=在线），避免陈旧心跳误报。"""
    reg = _load_devices()
    devs = reg.get("devices", {})
    if not devs:
        return json.dumps({"ok": True, "devices": [], "note": "暂无登记设备"}, ensure_ascii=False)
    import os as _os
    import time as _t
    online = {}
    try:
        st = json.load(open(ONLINE_STATE_FILE, encoding="utf-8"))
        now = _t.time()
        for k, v in st.items():
            online[k] = (now - v.get("ts", 0)) < 300  # 5 分钟心跳窗口
    except Exception:
        pass
    # 兜底：遥测记录新鲜度（fall/telemetry/<id> 60 秒内）
    try:
        if os.path.exists(FLASH_EVENT_LOG):
            now = _t.time()
            for ln in open(FLASH_EVENT_LOG, encoding="utf-8").read().splitlines()[-200:]:
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                t = rec.get("topic", "")
                if t.startswith("fall/telemetry/"):
                    dev = t.split("/")[-1]
                    if now - rec.get("ts", 0) < 60:
                        online[dev] = True
                    elif dev not in online:
                        online[dev] = False
    except Exception:
        pass
    out = []
    for did, info in sorted(devs.items()):
        info = dict(info)
        # 扫描 OTA 产物目录的最新版本
        latest_path = _os.path.join(OTA_ROOT, did, "latest.json")
        try:
            with open(latest_path, encoding="utf-8") as f:
                latest = json.load(f)
            info["current_version"] = latest.get("version", "")
        except Exception:
            pass
        # 板子能力感知：运行时命令 + capabilities（LLM 据此判断能对板子做什么）
        info["runtime_commands"] = info.get("runtime_commands") or []
        # 在线状态：心跳或遥测新鲜（board-s3-36ac 的 MQTT id 是 ESP32_8436AC，遥测主题 flasher-board）
        dev_key = did
        if did == "board-s3-36ac":
            dev_key = "flasher-board"
        info["online"] = online.get(dev_key, False)
        out.append(info)
    return json.dumps({"ok": True, "devices": out}, ensure_ascii=False)


# ═══════════════════ 固件迭代 OTA 部署 ═══════════════════

OTA_DEPLOY_TEMPLATE = """你是固件迭代工程师（OTA 部署）。任务：{requirement}
目标工程：{project}（{model}，ESP-IDF v5.5.4，设备 {device_id}）

流程：
1. 阅读 {project}/main/ 下现有代码，理解当前实现
2. 按需求做最小改动；不要修改 ota.cpp / telemetry.cpp / partitions.csv / sdkconfig.defaults（除非需求明确要求）
   遥测诚实性：实现的效果必须在 telemetry.cpp 里以硬件回读/真实状态字段上报（如 LED 亮灭→led:0/1、温度→temp_c、PWM→pwm_duty）；
   禁止用假变量冒充硬件状态——自验收只信真实硬件字段。
3. 版本递增：{version_file} 的 {version_macro}（如 "1.2.0" → "1.3.0"）
4. 编译：
   export IDF_TOOLS_PATH=/opt/esp; export IDF_SKIP_CHECK_SUBMODULES=1
   cd /opt/esp-idf; . ./export.sh >/dev/null 2>&1
   cd {project}; {build_cmd}
5. 编译失败：读取错误信息修复后重试（最多 5 轮）；5 轮仍失败：回复失败原因，需求标 failed
   注意：绝不允许把编译/归档丢到后台子进程后提前退出！必须等归档完成才结束任务。
6. 编译成功，归档（注意：OTA 下载必须用纯 app 镜像，不能用 merged.bin！）：
   - mkdir -p {ota_dir}/v<新版本>
   - cp {bin_path} {ota_dir}/v<新版本>/app.bin          ← OTA 用（纯 app，esp_https_ota 校验必需）
   - cp {bin_path} {ota_dir}/v<新版本>/merged.bin        ← 串口/云端烧录用（合并镜像，仅供参考）
   - cd {ota_dir}/v<新版本> && sha256sum app.bin > sha256.txt
   - 写 manifest.json（version/device/bin=app.bin/size/sha256/built_at/features）
   - 更新 {ota_dir}/latest.json（version/bin=app.bin/size/sha256）
7. git 提交（cd {commit_dir} && git add -A && git commit -m "feat: <需求摘要>"）
8. 归档完成自检（必须通过）：python3 -c "import json,os;d=json.load(open('{ota_dir}/latest.json'));print(d['version'], os.path.getsize(d['bin']))"
   输出必须是新版本号 + 大于 100000 的文件大小，且 d['bin'] 必须是 app.bin（merged.bin 会校验失败）。
   自检失败 = 任务失败（禁止宣称完成）。
   通过后系统会自动向板子发布 ota_check 命令，板子会 OTA 升级重启。

【部署后验收循环（人在环中，重要）】
升级是给用户用的，必须由用户验收。流程：
a. 发布并确认升级（重要：必须自己发 ota_check，不要等"系统自动发布"——系统在任务结束后才发，
   你等版本变化会永远等不到）：
   1. 立即发布 ota_check：docker exec mqtt mosquitto_pub -h localhost -u YOUR_MQTT_USER
      -P 'YOUR_MQTT_PASSWORD' -t fall/commands/{device_id} -m 'ota_check'
      （纯文本命令！发布后按步骤 3 的写法写命令事件流）
   2. 等 60 秒，读取 /opt/fall-mcp/flash_events.jsonl 中该设备最近遥测（grep fall/telemetry/ + 设备名），
      确认：版本号已变为你发布的新版本、uptime_s 变小（刚重启）。
   3. 把确认结果记入最终回复。若 2 分钟内版本未变化，说明升级失败或命令丢失，先自行再发一次
      ota_check，仍无变化再自我诊断（见 e）。
b. 功能级遥测自验收（优先，2026-08-24 升级）：检查本次需求效果是否有遥测字段可断言。
   - 先读该设备最近遥测：python3 -c "import json;L=[json.loads(x) for x in open('/opt/fall-mcp/flash_events.jsonl').read().splitlines()[-200:] if x and 'fall/telemetry/{device_id}' in x];print(L[-1]['payload'] if L else 'NO_TELEMETRY')"
   - 有对应效果字段（如灯效→led、温度→temp_c、转速→rpm、版本→fw）→ 自动验证：
     循环等待该字段满足预期（最多 90 秒，每 5 秒读一次遥测）：
     python3 -c "import json,time
for _ in range(18):
    L=[json.loads(x) for x in open('/opt/fall-mcp/flash_events.jsonl').read().splitlines()[-200:] if x and 'fall/telemetry/{device_id}' in x]
    if L and L[-1]['payload'].get('led',-1) == 1: print('LED_ON'); break
    time.sleep(5)"
     （把 led/==1 换成实际字段和预期值；预期值从你的代码逻辑推演，禁止猜）
   - 自动验证通过 → 回复"自验收通过：<字段>=<值>" 并结束（不需要再问用户）。
   - 无字段可验 / 自动验证失败（90 秒未满足）→ 才走下面的人工验收：
c. 用户验收（仅当自验收不可行或失败时）：写一个口语化的确认问题到 /opt/fall-mcp/confirm_queue/ 等待用户回答：
   - 文件名：/opt/fall-mcp/confirm_queue/{device_id}.jsonl
   - 内容格式（python3 写）：{{"q": "<自然问句>", "ts": <时间戳>}}
   - 问句必须自然口语化、**针对需求的核心效果**（不是附带效果）：
     例：需求是"定时关灯"→ 问"灯亮 60 秒后会不会自己灭？"；需求是"灯变蓝"→ 问"灯是蓝色吗？"；
     需求是"跌倒报警"→ 问"跌倒时会响吗？"。先对照需求原文确定核心效果，再写问句。
d. 等待回答：最多 12 轮，每轮 sleep 10 秒，用下面命令检查是否出现回答（读到 a 字段即视为回答）：
   python3 -c "import json;L=[json.loads(x) for x in open('/opt/fall-mcp/confirm_queue/{device_id}.jsonl')];print([r.get('a','') for r in L if 'a' in r][-1] if any('a' in r for r in L) else '')"
   输出非空即拿到回答。
d1. 用户回答正常（如"正常/好了/亮了/可以"）→ 验收通过，回复"用户验收通过"并结束。
d2. 12 轮（约 2 分钟）无回答：回复"验收等待超时（用户未及时确认），固件已部署；用户看到功能后如发现问题会再反馈"，直接结束（不要无限等待）。
e. 用户回答有问题（如"没亮/不对/有 bug"+ 现象描述）→ 进入修复循环（最多 3 轮）：
   1. 自我诊断：读 /opt/fall-mcp/flash_events.jsonl 的遥测/日志；检查你的代码逻辑；
      怀疑 ESP-IDF API 用法时，必须查头文件定义：grep -rn "函数名" /opt/esp-idf/components/*/include/ 或
      /opt/esp-idf/components/esp_driver_*/include/，对照官方签名与返回值检查
   2. 修改代码 → 版本再递增 → 重新编译 → 重新归档（同步骤 4-7）
   3. 自己向板子发布命令：docker exec mqtt mosquitto_pub -h localhost -u YOUR_MQTT_USER
      -P 'YOUR_MQTT_PASSWORD' -t fall/commands/{device_id} -m 'ota_check'
      （注意：板子命令是纯文本 ota_check，不是 JSON！）
      发布后必须同步写命令事件流（全链路溯源）：
      python3 -c "import json,time; open('/opt/fall-mcp/flash_events.jsonl','a').write(json.dumps({{'ts':time.time(),'topic':'fall/commands/{device_id}','payload':'ota_check','direction':'cmd'}})+chr(10))"
   4. 回到 a（等升级完成）→ b（再问用户验收）
f. 3 轮仍未解决：写确认问题向用户如实说明："这个问题我尝试了 N 轮仍未解决，现象是……，
   我怀疑是……，你可以：1) 描述更多现象 2) 提供板子型号/引脚信息 3) 让我换个方案"。
   等待用户指示，按其回答继续或结束。
g. 最终回复必须包含：最终版本号、改动文件、用户验收结果（通过/未通过/求助）、诊断过程摘要。
不要做其他任何事情。"""


def _ota_versions(ota_dir: str) -> list:
    """列出可用版本目录（含 merged.bin），按版本号升序。"""
    import re as _re
    vers = []
    try:
        for fn in os.listdir(ota_dir):
            m = _re.match(r"v?(\d+)\.(\d+)\.(\d+)$", fn)
            if not m:
                continue
            if os.path.isfile(os.path.join(ota_dir, fn, "merged.bin")):
                vers.append((tuple(int(x) for x in m.groups()), fn))
    except Exception:
        pass
    vers.sort(key=lambda x: x[0])
    return vers


def dev_ota_rollback(device_id: str = "", version: str = "") -> str:
    """固件一键回滚：把设备 OTA 最新版本指回历史版本并触发升级。

    用法：dev_ota_rollback("5798") 回滚到当前版本的前一个；
          dev_ota_rollback("5798", "0.11.0") 回滚到指定版本。
    安全：只允许回滚到 /opt/ota/<id>/ 下已归档（merged.bin 存在）的版本；
          回滚前自动备份当前 latest.json。"""
    import time as _tr
    device_id = _resolve_board_id((device_id or "").strip())
    if not device_id:
        return "请提供目标设备 id（如 board-s3-5798）"
    specs = _board_specs()
    spec = specs.get(device_id)
    if spec is None:
        _reg = _load_devices().get("devices", {})
        if device_id in _reg:
            spec = {"ota_dir": os.path.join(OTA_ROOT, device_id)}
        else:
            known = "、".join(sorted(_reg.keys()) or specs.keys())
            return f"未知设备 {device_id}，可选：{known}"
    ota_dir = spec.get("ota_dir") or os.path.join(OTA_ROOT, device_id)
    if not os.path.isdir(ota_dir):
        return f"{device_id} 没有 OTA 目录：{ota_dir}"
    vers = _ota_versions(ota_dir)
    if not vers:
        return f"{device_id} 没有可回滚的已归档版本"
    cur = ""
    try:
        cur = str(json.load(open(os.path.join(ota_dir, "latest.json"), encoding="utf-8")).get("version", "")).lstrip("v")
    except Exception:
        pass
    if version:
        target = None
        for _, fn in vers:
            if fn.lstrip("v") == version.lstrip("v") or fn == version:
                target = fn
                break
        if target is None:
            return f"版本 {version} 不存在（可用：{'、'.join(fn for _, fn in vers)}）"
    else:
        if not cur:
            return f"无法确定 {device_id} 当前版本，请显式指定 version"
        idx = next((i for i, (_, fn) in enumerate(vers) if fn.lstrip("v") == cur), -1)
        if idx <= 0:
            return f"{device_id} 当前 {cur} 已是最早版本，没有可回滚目标"
        target = vers[idx - 1][1]
    import shutil as _sh
    latest_path = os.path.join(ota_dir, "latest.json")
    try:
        _sh.copy(latest_path, latest_path + f".bak.rollback-{int(_tr.time())}")
        manifest = os.path.join(ota_dir, target, "manifest.json")
        if os.path.isfile(manifest):
            info = json.load(open(manifest, encoding="utf-8"))
        else:
            info = {"version": target, "bin": "merged.bin"}
        info["bin"] = info.get("bin", "merged.bin")
        import hashlib as _h
        bin_path = os.path.join(ota_dir, target, info["bin"])
        if os.path.isfile(bin_path):
            info["size"] = os.path.getsize(bin_path)
            info["sha256"] = _h.sha256(open(bin_path, "rb").read()).hexdigest()
        json.dump(info, open(latest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        return f"回滚写入失败: {e}"
    mqtt_id = (spec.get("mqtt_device_id") or device_id)
    try:
        _publish_mqtt_command("ota_check", mqtt_id)
    except Exception as e:
        return f"回滚已写入 latest.json（指向 {target}）但 ota_check 发布失败: {e}"
    _log_evolution("firmware_rollback", f"{device_id} {cur} -> {target}")
    # OTA 只升不降：目标低于板子当前版本时提示需串口烧录
    cur_fw = _board_current_fw(device_id)
    if cur_fw and _fw_cmp(cur_fw, target.lstrip("v")):
        try:
            dev_speak(f"{device_id} 的 OTA 配置已回滚到 {target}，但板子当前是 {cur_fw}，OTA 不会自动降级，需要串口烧录才能立即回到 {target}")
        except Exception:
            pass
        return (f"已回滚 {device_id}：{cur or '?'} -> {target}（latest.json 已更新）。"
                f"注意：板子当前 {cur_fw} 高于回滚目标，OTA 只升不降，不会自动降级；"
                "如需立即降级请串口烧录，或先让板子升到更新版本再回滚。")
    try:
        dev_speak(f"{device_id} 固件回滚到 {target}，板子正在升级，稍后看效果")
    except Exception:
        pass
    return f"已回滚 {device_id}：{cur or '?'} -> {target}（latest.json 已更新，ota_check 已发布，生效约 1-2 分钟）"


def _template_selfcheck() -> None:
    """模板冒烟自检（2026-08-24）：模块加载时验证模板可格式化，防花括号转义 bug。"""
    try:
        OTA_DEPLOY_TEMPLATE.format(
            requirement="r", project="p", model="m", device_id="d",
            version_file="v", version_macro="m", build_cmd="b",
            ota_dir="o", bin_path="b", commit_dir="c")
        FLASH_SUPERVISE_TEMPLATE.format(device="d")
        print("[fall-mcp] 模板自检通过", flush=True)
    except Exception as e:
        print(f"[fall-mcp] ❌ 模板自检失败（花括号未转义？）: {e}", flush=True)


def _publish_mqtt_command(command: str, device_id: str = "esp32s3-cam-01") -> None:
    """发布 MQTT 命令给板子（如 ota_check）。"""
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set("YOUR_MQTT_USER", "YOUR_MQTT_PASSWORD")
        client.connect("127.0.0.1", 1883, 10)
        topic = f"fall/commands/{device_id}"
        client.publish(topic, command, qos=1)
        client.disconnect()
        _log_command_event(topic, command)
        print(f"[ota-deploy] 命令已发布 {topic}: {command}", flush=True)
    except Exception as exc:
        print(f"[ota-deploy] 命令发布失败: {exc}", flush=True)


def _resolve_board_id(raw: str) -> str:
    """board_id 模糊匹配（2026-08-23 修复）：支持 '5798'/'board-s3-5798'/'五七九八' 等写法。
    返回注册表中的正式 device_id；找不到返回原值（由调用方报未知设备）。"""
    raw = (raw or "").strip().lower()
    if not raw:
        return raw
    specs = _board_specs()
    if raw in specs:
        return raw
    # 1) 子串匹配：'5798' in 'board-s3-5798'；'477c' in 'board-s3-477c'
    for did in specs:
        if raw in did or did.replace("board-s3-", "") == raw:
            return did
    # 2) 中文数字映射：五七九八 → 5798
    cn2num = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    digits = "".join(cn2num.get(c, c) for c in raw if c in cn2num or c.isdigit())
    if digits:
        for did in specs:
            if digits in did:
                return did
    return raw


def deploy_firmware(requirement: str, board_id: str = "fall-board") -> str:
    """派发固件迭代任务（后台 DSH 执行：改码→编译→归档→发布 ota_check 命令）。
    board_id 指定目标设备（fall-board 跌倒板 / board-s3-36ac 通用板等）。"""
    if not requirement or not requirement.strip():
        return "请描述要修改的功能，比如'跌倒后 LED 闪烁3次'。"
    board_id = _resolve_board_id(board_id or "fall-board")
    specs = _board_specs()
    spec = specs.get(board_id)
    if spec is None:
        known = "、".join(specs.keys())
        return f"未知设备 {board_id}，可选：{known}。新板需先在 board-template/main/boards/ 建 {board_id}.h 并登记。"
    import threading
    build_cmd = {
        "fall-board": "idf.py build",
    }.get(board_id, f"bash build_board.sh {board_id} && ls build-{board_id}/board_template.bin")
    task = OTA_DEPLOY_TEMPLATE.format(
        requirement=requirement.strip(),
        project=spec["project"],
        model=spec["model"],
        device_id=board_id,
        version_file=spec["version_file"],
        version_macro=spec["version_macro"],
        build_cmd=build_cmd,
        ota_dir=spec["ota_dir"],
        bin_path=spec["bin_path"],
        commit_dir=spec["commit_dir"],
    )

    import threading as _lock_th
    _deploy_locks = getattr(_lock_th, "_deploy_locks", {})
    if not hasattr(_lock_th, "_deploy_locks"):
        _lock_th._deploy_locks = {}

    def _bg():
        try:
            # 同板部署锁：同一板子只允许一个部署任务在跑
            if board_id in _lock_th._deploy_locks and _lock_th._deploy_locks[board_id].is_alive():
                print(f"[ota-deploy] 拒绝并行部署: {board_id} 已有任务在跑", flush=True)
                try:
                    dev_speak(f"{board_id} 刚才的部署还没完成，先等它跑完，我再帮你处理这个新需求")
                except Exception:
                    pass
                return
            _lock_th._deploy_locks[board_id] = _lock_th.current_thread()
            print(f"[ota-deploy] 固件迭代开始: {board_id} {requirement[:60]}", flush=True)
            env = dict(os.environ)
            env.update({
                "NVM_DIR": os.path.expanduser("~/.nvm"),
                "PATH": os.path.expanduser("~/.nvm/versions/node/v22.23.2/bin") + ":" + env.get("PATH", ""),
                "DSH_PERMISSION_MODE": "danger-full-access",
            })
            cmd, _ = _dsh_cmd(task)
            proc = subprocess_run(cmd, cwd="/opt/fall-mcp", env=env, timeout=2400)
            out = (proc.stdout or "") + (proc.stderr or "")
            print(f"[ota-deploy] DSH 完成 rc={proc.returncode}: {out[-300:]}", flush=True)
            if proc.returncode == 0:
                # 假完成防护（2026-08-24）：归档产物验证——DSH 可能提前退出（后台化/编译中）
                _arch_ok = False
                try:
                    _lv = json.load(open(os.path.join(spec["ota_dir"], "latest.json"), encoding="utf-8"))
                    # 归档产物在 <ota_dir>/<version>/<bin> 子目录（2026-08-25 修复路径 bug）
                    _bin = os.path.join(spec["ota_dir"], _lv.get("version", ""), _lv.get("bin", ""))
                    _arch_ok = bool(_lv.get("version")) and os.path.isfile(_bin) and os.path.getsize(_bin) > 100000
                    if not _arch_ok:
                        print(f"[ota-deploy] 归档验证详情: version={_lv.get('version')} bin={_lv.get('bin')} path={_bin} exists={os.path.isfile(_bin)}", flush=True)
                except Exception:
                    _arch_ok = False
                if not _arch_ok:
                    _log_evolution("firmware_deploy_failed", f"{board_id} 假完成(无归档): {out[-150:]}")
                    print(f"[ota-deploy] 假完成拦截: {board_id} 无有效归档产物，不播报完成", flush=True)
                    _auto_review(requirement, f"{board_id} 假完成：DSH rc=0 但无归档产物 ({out[-150:]})", "fake_done")
                    try:
                        dev_speak(f"{board_id} 这次部署没有成功，固件没编译出来，我继续排查，先不打扰你")
                    except Exception:
                        pass
                    return
                _log_evolution("firmware_deployed", f"{board_id} {requirement[:80]}")
                # 同类失败经验置信度提升（越用越聪明：同样的坑绕过后经验转正）
                _bump_experience_confidence(requirement)
                # 通知板子立即检查升级
                _publish_mqtt_command("ota_check", spec["mqtt_device_id"])
                # 修复(2026-08-23)：部署完成直接播报，不依赖 confirm poller 线程
                # （poller 可能卡死/未启动导致验收播报丢失——用户根本不知道任务完成了）
                try:
                    # 生效确认（2026-08-24）：等板子重启到新版本（最多 90s），播报才说"已生效"
                    import time as _wt
                    _new_ver = ""
                    try:
                        _new_ver = str(json.load(open(os.path.join(spec["ota_dir"], "latest.json"), encoding="utf-8")).get("version", "")).lstrip("v")
                    except Exception:
                        pass
                    _eff = False
                    for _i in range(9):
                        _wt.sleep(10)
                        try:
                            for _ln in open("/opt/fall-mcp/flash_events.jsonl", encoding="utf-8").read().splitlines()[-60:]:
                                _d = json.loads(_ln)
                                if _d.get("topic") == f"fall/telemetry/{board_id}" and _new_ver and str(_d.get("payload", {}).get("fw", "")).startswith(_new_ver):
                                    _eff = True
                                    break
                        except Exception:
                            pass
                        if _eff:
                            break
                    # 完成播报按 DSH 验收结果动态化（验收循环已在 DSH 内结束）
                    if _eff:
                        _msg = f"{board_id} 的新固件已经装好并生效了"
                    elif "验收通过" in out:
                        _msg = f"{board_id} 的新固件已经装好，你也确认过没问题，搞定！"
                    elif "超时" in out or "未及时确认" in out:
                        _msg = f"{board_id} 的新固件已经装好，有空看一眼效果，不对随时喊我"
                    else:
                        _msg = f"{board_id} 的新固件已经发布，板子正在升级重启，稍等一两分钟看效果"
                    _r = dev_speak(_msg)
                    print(f"[ota-deploy] 完成播报结果: {_r}", flush=True)
                except Exception as _e:
                    print(f"[ota-deploy] 完成播报异常: {_e}", flush=True)
            else:
                _log_evolution("firmware_deploy_failed", f"{board_id} {requirement[:60]} | {out[-200:]}")
                _cause = "build_failed" if any(k in out for k in ("error:", "failed", "FAILED", "Error")) else "other"
                _auto_review(requirement, f"{board_id} DSH rc={proc.returncode}: {out[-200:]}", _cause)
        except Exception as exc:
            print(f"[ota-deploy] ERROR: {exc}", flush=True)
        finally:
            if board_id in _lock_th._deploy_locks:
                _lock_th._deploy_locks.pop(board_id, None)
            _log_evolution("firmware_deploy_failed", f"{board_id} {requirement[:60]} | {exc}")

    threading.Thread(target=_bg, daemon=True).start()
    print(f"[ota-deploy] 派发耗时 {(_td.time() - _t0) * 1000:.0f}ms（同步部分）", flush=True)
    return (
        f"固件迭代任务已开始：{board_id}｜{requirement.strip()[:60]}。"
        f"大约需要 10-20 分钟（改代码+编译+归档），完成后 {board_id} 会自动 OTA 升级并重启。"
    )


import subprocess as _subprocess


def subprocess_run(cmd, cwd, env, timeout):
    return _subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def start_plugin_poller() -> None:
    """fall-mcp 启动时调用：后台轮询插件需求。"""
    import threading
    import time as _t

    def _loop():
        while True:
            try:
                _process_plugin_requests()
            except Exception as e:
                print(f"[plugin-poller] loop error: {e}", flush=True)
            try:
                _periodic_health_check()
            except Exception:
                pass
            _t.sleep(10)

    threading.Thread(target=_loop, daemon=True).start()
    print("[plugin-poller] started", flush=True)


# 模板自检（模块加载即执行）
try:
    _template_selfcheck()
except Exception:
    pass
