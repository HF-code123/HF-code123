"""带 getDate 工具的流式循环对话：python getdate.py

依赖: pip install openai
配置: 同目录下 config.ini 的 [llm] 段（base_url / api_key / model）
退出: Ctrl+C
"""

import configparser
import subprocess
from pathlib import Path

from openai import OpenAI

cfg = configparser.ConfigParser()
cfg.read(Path(__file__).with_name("config.ini"), encoding="utf-8")

client = OpenAI(
    base_url=cfg["llm"]["base_url"],
    api_key=cfg["llm"]["api_key"],
)
MODEL = cfg["llm"]["model"]


def get_date():
    """调用本地 date / time 命令取当前日期时间。

    Windows 下 date 不带 /t 会进入"输入新日期"的交互提示并卡住，所以用 /t 只显示。
    """
    raw = subprocess.run("date /t & time /t", shell=True, capture_output=True).stdout
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    return " ".join(text.split())  # 两行合成一行：2026/10/19 周一 10:32


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑本地的日期和时间。当用户询问今天是几号、现在几点、当前时间等问题时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def run_tool(name):
    if name == "getDate":
        return get_date()
    return f"未知工具: {name}"


messages = []  # 保留多轮上下文

while True:
    try:
        prompt = input("please input your prompt: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break

    if not prompt:
        continue

    print("---")
    messages.append({"role": "user", "content": prompt})

    # 一次提问可能需要多轮工具调用，直到模型不再要工具才结束
    while True:
        stream = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, stream=True
        )

        content = ""
        calls = {}  # index -> {id, name, arguments}，流式返回的工具调用要自己拼
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content += delta.content
                print(delta.content, end="", flush=True)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    slot["name"] += tc.function.name or ""
                    slot["arguments"] += tc.function.arguments or ""
        if content:
            print()  # 纯工具调用的轮次没有正文，不留空行

        if not calls:
            # 没有工具调用，说明这轮就是最终回答
            messages.append({"role": "assistant", "content": content})
            break

        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in calls.values()
                ],
            }
        )

        for c in calls.values():
            result = run_tool(c["name"])
            print(f"[调用 {c['name']} -> {result}]")
            messages.append(
                {"role": "tool", "tool_call_id": c["id"], "content": result}
            )
