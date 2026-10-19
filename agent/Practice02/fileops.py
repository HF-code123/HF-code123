"""带本地文件操作工具的流式循环对话：python fileops.py

工具: getDate / isLeapYear / getCwd / listDir / getFileType / readFile
      createFile / editFile / createDir / deleteDir / deleteFile / renamePath
依赖: pip install openai
配置: 同目录下 config.ini 的 [llm] 段（base_url / api_key / model）
退出: Ctrl+C
"""

import configparser
import json
import mimetypes
import os
import shutil
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


# ---------- 原来的两个工具 ----------

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


def is_leap_year(year):
    """用 Python 判断闰年：能被 4 整除且不能被 100 整除，或者能被 400 整除。"""
    year = int(year)
    if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0:
        return f"{year} 年是闰年"
    return f"{year} 年不是闰年"


# ---------- 文件操作工具 ----------

def read_text(p):
    """按 utf-8 / gbk 依次尝试解码，都失败说明不是纯文本。"""
    raw = Path(p).read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("不是纯文本文件")


def get_cwd():
    return f"当前工作目录: {os.getcwd()}"


def list_dir(path):
    p = Path(path)
    if not p.is_dir():
        return f"目录不存在: {path}"
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    if not items:
        return f"{p} 是空目录"
    lines = [f"[目录] {i.name}" if i.is_dir() else f"[文件] {i.name}" for i in items]
    return f"{p} 共 {len(items)} 项:\n" + "\n".join(lines)


def get_file_type(path):
    p = Path(path)
    if not p.exists():
        return f"路径不存在: {path}"
    if p.is_dir():
        return f"{path} 是目录，不是文件"
    mime, _ = mimetypes.guess_type(p)
    return f"文件: {p.name}\n扩展名: {p.suffix or '(无)'}\nMIME: {mime or '(未知)'}"


def read_file(path):
    p = Path(path)
    if not p.is_file():
        return f"文件不存在: {path}"
    try:
        text = read_text(p)
    except (ValueError, OSError) as e:
        return f"读取失败: {e}"
    if len(text) > 5000:
        text = text[:5000] + "\n...(内容过长已截断)"
    return f"文件内容:\n{text}"


def create_file(dir_path, filename, content=""):
    p = Path(dir_path) / filename
    if p.exists():
        return f"文件已存在，未创建: {p}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已创建文件: {p}（{len(content)} 字符）"


def edit_file(path, content):
    p = Path(path)
    if not p.is_file():
        return f"文件不存在: {path}"
    try:
        read_text(p)
    except ValueError:
        return f"不是纯文本文件，拒绝写入: {path}"
    p.write_text(content, encoding="utf-8")
    return f"已写入: {p}（原有内容已被覆盖，{len(content)} 字符）"


def create_dir(path):
    p = Path(path)
    if p.exists():
        return f"已存在: {p}"
    p.mkdir(parents=True)
    return f"已创建目录: {p}"


def delete_dir(path):
    p = Path(path)
    if not p.is_dir():
        return f"目录不存在: {path}"
    shutil.rmtree(p)
    return f"已删除目录及其全部内容: {p}"


def delete_file(path):
    p = Path(path)
    if not p.is_file():
        return f"文件不存在: {path}"
    p.unlink()
    return f"已删除文件: {p}"


def rename_path(old, new):
    o, n = Path(old), Path(new)
    if not o.exists():
        return f"源路径不存在: {old}"
    if n.exists():
        return f"目标已存在，未重命名: {new}"
    o.rename(n)
    return f"已重命名: {o} -> {n}"


# ---------- 工具声明 ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑本地的日期和时间。当用户询问今天是几号、现在几点、当前时间等问题时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断某一年是否为闰年。当用户问某年是不是闰年、某年 2 月有没有 29 天时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份，例如 2024"}
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCwd",
            "description": "获取当前工作目录（程序运行时所在目录）的绝对路径。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDir",
            "description": "列出指定目录下的所有文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，例如 D:\\data"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的文件类型，返回扩展名和 MIME 类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取指定文件的文本内容，仅支持纯文本文件（如 .txt/.py/.md/.json）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下用指定文件名创建新文件，可同时写入初始内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "目标目录路径"},
                    "filename": {"type": "string", "description": "文件名，含扩展名，如 note.txt"},
                    "content": {"type": "string", "description": "文件初始内容，可为空"},
                },
                "required": ["dir", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑指定纯文本文件的内容。注意：会用新内容整体覆盖原有内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"},
                    "content": {"type": "string", "description": "写入的新内容（覆盖全部原有内容）"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDir",
            "description": "创建新目录，父目录不存在时会一并创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDir",
            "description": "删除指定目录及其内部所有内容（目录可以非空）。危险操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定文件。危险操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件完整路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录，也可以借此把文件/目录移动到另一个路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "old": {"type": "string", "description": "原路径"},
                    "new": {"type": "string", "description": "新路径（含新名字）"},
                },
                "required": ["old", "new"],
            },
        },
    },
]


def run_tool(name, a):
    if name == "getDate":
        return get_date()
    if name == "isLeapYear":
        return is_leap_year(a["year"])
    if name == "getCwd":
        return get_cwd()
    if name == "listDir":
        return list_dir(a["path"])
    if name == "getFileType":
        return get_file_type(a["path"])
    if name == "readFile":
        return read_file(a["path"])
    if name == "createFile":
        return create_file(a["dir"], a["filename"], a.get("content", ""))
    if name == "editFile":
        return edit_file(a["path"], a["content"])
    if name == "createDir":
        return create_dir(a["path"])
    if name == "deleteDir":
        return delete_dir(a["path"])
    if name == "deleteFile":
        return delete_file(a["path"])
    if name == "renamePath":
        return rename_path(a["old"], a["new"])
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
            result = run_tool(c["name"], json.loads(c["arguments"] or "{}"))
            print(f"[调用 {c['name']} -> {result}]")
            messages.append(
                {"role": "tool", "tool_call_id": c["id"], "content": result}
            )
