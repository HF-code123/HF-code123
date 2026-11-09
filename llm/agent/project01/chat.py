"""一次性 LLM 对话脚本：python chat.py

依赖: pip install openai
配置: 同目录下 config.ini 的 [llm] 段（base_url / api_key / model）
退出: Ctrl+C
"""

import configparser
from pathlib import Path

from openai import OpenAI

cfg = configparser.ConfigParser()
cfg.read(Path(__file__).with_name("config.ini"), encoding="utf-8")

client = OpenAI(
    base_url=cfg["llm"]["base_url"],
    api_key=cfg["llm"]["api_key"],
)
MODEL = cfg["llm"]["model"]

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

    stream = client.chat.completions.create(
        model=MODEL, messages=messages, stream=True
    )
    reply = ""
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            reply += text
            print(text, end="", flush=True)
    print()

    messages.append({"role": "assistant", "content": reply})
