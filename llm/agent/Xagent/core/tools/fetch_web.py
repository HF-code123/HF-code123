"""网页正文抓取工具：fetchWeb。

流程：本地 curl 取 HTML → trafilatura 提取正文 → 转 Markdown → 连同原始 URL 返回。

要点：
- 调用 curl 时使用参数列表 + ``shell=False``（默认），避免 shell 注入；
- 只保留正文：导航栏、侧边栏、广告、页脚由 trafilatura 的正文抽取算法剔除；
- 保留标题、段落、列表、表格和链接，方便模型直接引用；
- 所有异常（URL 非法、curl 缺失、超时、HTTP/网络失败、非 HTML、正文抽不出）
  都转成中文提示回给模型，不抛裸异常。
"""

from __future__ import annotations

import re
import subprocess
from typing import Any
from urllib.parse import urlparse

import trafilatura

from .registry import ToolRegistry, ToolSpec

DEFAULT_TIMEOUT = 30
MAX_CHARS = 12000
MAX_BYTES = 5 * 1024 * 1024
MIN_CONTENT_CHARS = 40
MIN_RECALL_CHARS = 80
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# curl 退出码 → 人话（只列常见项，其余链路统一兜底）
CURL_ERRORS = {
    6: "域名解析失败，请检查链接是否写错或网络是否正常",
    7: "连接失败，目标站点不可达（可能被墙、需要代理，或端口未开放）",
    22: "服务器返回了 HTTP 错误状态码（如 403/404/500）",
    28: "请求超时",
    35: "TLS 握手失败，站点安全连接异常",
    60: "HTTPS 证书校验失败",
    63: "响应内容过大，已超过 5MB 上限",
}

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "要抓取的网页完整地址，必须以 http:// 或 https:// 开头",
        }
    },
    "required": ["url"],
}


class FetchWebError(RuntimeError):
    """抓取失败，message 已是可直接展示给用户/LLM 的中文说明。"""


def register(registry: ToolRegistry, *, fetch: Any = None) -> None:
    """注册 fetchWeb 工具。

    fetch 仅用于测试注入，签名 ``fetch(url, timeout) -> bytes``；
    不传时使用本模块的 curl 实现。
    """
    registry.register(
        ToolSpec(
            name="fetchWeb",
            description=(
                "抓取指定网页的正文并转成 Markdown（curl + trafilatura）。"
                "当用户给出具体网址、或需要阅读某篇文章/文档的完整内容时使用；"
                "只做站内检索请改用 searchWeb。返回内容包含正文 Markdown 和原始 URL。"
            ),
            parameters=PARAMETERS,
            handler=lambda arguments: fetch_web(arguments, fetch=fetch),
        )
    )


def fetch_web(arguments: dict[str, Any], *, fetch: Any = None) -> str:
    url = str(arguments.get("url", "")).strip()
    if not url:
        return "抓取失败: url 不能为空，请提供要抓取的网页地址。"
    if not _is_http_url(url):
        return f"抓取失败: 只支持 http/https 链接，收到的是 {url!r}。"

    try:
        html = (fetch or _run_curl)(url, DEFAULT_TIMEOUT)
    except FetchWebError as exc:
        return f"抓取失败: {exc}（URL: {url}）"

    if not html or not html.strip():
        return f"抓取失败: 目标页面返回了空内容。（URL: {url}）"

    text = _decode(html)
    if not _looks_like_html(text):
        return (
            "抓取失败: 该链接返回的不是 HTML 网页"
            "（可能是 PDF、图片、JSON 接口或需要登录的跳转页），无法提取正文。"
            f"（URL: {url}）"
        )

    try:
        markdown = _extract_markdown(text, url)
    except Exception as exc:  # trafilatura 内部异常（lxml 解析失败等）
        return f"抓取失败: HTML 解析失败（{exc}）。（URL: {url}）"

    if not markdown:
        return (
            "抓取失败: 未能从页面提取到正文。"
            "常见原因：页面靠 JavaScript 动态渲染、需要登录、正文是图片，或站点有反爬限制。"
            f"（URL: {url}）"
        )

    header = _header(text, url)
    body, truncated = _truncate(markdown)
    parts = [header, "", "---", "", body]
    if truncated:
        parts.append("")
        parts.append(f"（提示：正文较长，已截断为前 {MAX_CHARS} 字；如需后续内容请抓取分页链接。）")
    return "\n".join(parts)


# ------------------------------------------------------------------ curl 抓取


def _run_curl(url: str, timeout: int) -> bytes:
    """用参数列表调用本地 curl，不经过 shell。"""

    command = [
        "curl",
        "-sS",  # 静默，但把错误信息打到 stderr
        "-L",  # 跟随重定向
        "--compressed",  # 让服务端压缩，省流量
        "--max-time",
        str(timeout),
        "--connect-timeout",
        "10",
        "--max-filesize",
        str(MAX_BYTES),
        "-A",
        USER_AGENT,
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        # 把状态码追加在正文末尾，便于区分 200 空页面与 404/403
        "-w",
        "\n%{http_code}",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout + 5,
            check=False,  # 退出码自己判断，便于给出更具体的原因
        )
    except FileNotFoundError as exc:
        raise FetchWebError("系统未找到 curl 命令，请先安装 curl 或把它加入 PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchWebError(f"请求超时（超过 {timeout} 秒）") from exc
    except OSError as exc:
        raise FetchWebError(f"启动 curl 失败: {exc}") from exc

    if completed.returncode != 0:
        reason = CURL_ERRORS.get(completed.returncode)
        if reason is None:
            stderr = _decode(completed.stderr).strip()[:200]
            reason = f"curl 退出码 {completed.returncode}" + (f": {stderr}" if stderr else "")
        raise FetchWebError(reason)

    body, status = _split_status(completed.stdout)
    if status >= 400:
        raise FetchWebError(_http_reason(status))
    return body


def _split_status(raw: bytes) -> tuple[bytes, int]:
    """拆掉 -w 追加的状态码，返回（正文, HTTP 状态码）。"""

    head, separator, tail = raw.rpartition(b"\n")
    code = tail.strip()
    if separator and code.isdigit() and len(code) == 3:
        return head, int(code)
    return raw, 0


def _http_reason(status: int) -> str:
    if status == 403:
        return "服务器返回 HTTP 403，访问被拒绝（可能需要登录、有反爬限制或地区限制）"
    if status == 404:
        return "服务器返回 HTTP 404，页面不存在，请检查链接"
    if status == 429:
        return "服务器返回 HTTP 429，请求过于频繁被限流，请稍后再试"
    if status >= 500:
        return f"服务器返回 HTTP {status}，目标站点自身故障，请稍后再试"
    return f"服务器返回 HTTP {status}，无法获取页面内容"


# ---------------------------------------------------------------- HTML 与正文


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _decode(raw: bytes) -> str:
    """优先按 HTML meta 声明的编码解码，其次 utf-8，最后 gb18030 兜底。"""

    if not raw:
        return ""
    head = raw[:2048].decode("ascii", errors="ignore")
    match = re.search(r"""charset\s*=\s*["']?([\w-]+)""", head, re.IGNORECASE)
    candidates = [match.group(1)] if match else []
    candidates += ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_html(text: str) -> bool:
    head = text[:2048].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head


def _extract_markdown(html: str, url: str) -> str:
    """先用高精度模式；抽不出正文时放宽召回再试一次。

    放宽召回容易把导航/版权声明当成正文，所以要求兜底结果达到一定长度，
    否则宁可报「没有正文」，也不把噪声喂给模型。
    """

    attempts = (
        ({"favor_precision": True, "deduplicate": True}, MIN_CONTENT_CHARS),
        ({"favor_recall": True}, MIN_RECALL_CHARS),
    )
    for options, min_chars in attempts:
        markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_formatting=True,  # 保留标题层级、列表、加粗
            include_links=True,
            include_tables=True,
            include_images=False,
            include_comments=False,  # 评论区属于噪声
            **options,
        )
        if markdown and len(markdown.strip()) >= min_chars:
            return _tidy(markdown)
    return ""


def _tidy(markdown: str) -> str:
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def _header(html: str, url: str) -> str:
    """页面元信息，方便模型判断来源可信度与时效性。"""

    title = sitename = date = ""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        meta = None
    if meta is not None:
        title = str(meta.title or "").strip()
        sitename = str(meta.sitename or "").strip()
        date = str(meta.date or "").strip()

    lines = []
    if title:
        lines.append(f"标题: {title}")
    if sitename:
        lines.append(f"站点: {sitename}")
    if date:
        lines.append(f"发布时间: {date}")
    lines.append(f"URL: {url}")
    return "\n".join(lines)


def _truncate(markdown: str) -> tuple[str, bool]:
    if len(markdown) <= MAX_CHARS:
        return markdown, False
    return markdown[:MAX_CHARS].rstrip(), True
