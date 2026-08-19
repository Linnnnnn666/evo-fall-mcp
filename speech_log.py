#!/usr/bin/env python3
"""语音对话监控：拉取最近 N 分钟的语音对话链路（识别→工具→播报）"""
import subprocess
import sys

mins = sys.argv[1] if len(sys.argv) > 1 else "10"
since = f"{mins} min ago"
out = subprocess.run(
    ["journalctl", "-u", "xiaozhi-server", "--since", since, "--no-pager"],
    capture_output=True, text=True).stdout

lines = out.splitlines()
for ln in lines:
    if "识别文本" in ln:
        text = ln.split("识别文本: ", 1)[-1].strip()
        print(f"\n🎤 你说: {text}")
    elif "大模型收到用户消息" in ln and "[内部播报" not in ln and "系统内部" not in ln:
        text = ln.split("大模型收到用户消息: ", 1)[-1].strip()
        if not text.startswith("nihaoxiaoan"):
            print(f"   (LLM收到: {text[:60]})")
    elif "执行工具" in ln:
        tool = ln.split("执行工具: ", 1)[-1].strip()
        print(f"   🔧 调用: {tool[:120]}")
    elif "内部通知注入" in ln:
        text = ln.split("内部通知注入: ", 1)[-1].strip()
        print(f"\n📢 系统播报(注入): {text[:100]}")
    elif "语音生成成功" in ln:
        text = ln.split("语音生成成功: ", 1)[-1].strip()
        print(f"   🔊 播报: {text[:90]}")
