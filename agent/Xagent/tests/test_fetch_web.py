from __future__ import annotations

import asyncio

import pytest

from core.tools import ToolRegistry, build_default_registry
from core.tools import fetch_web as module
from core.tools.fetch_web import FetchWebError

ARTICLE_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>大模型工具调用实践</title>
<meta property="og:site_name" content="示例站点"></head>
<body>
<nav class="menu"><a href="/">首页</a><a href="/about">关于我们</a></nav>
<div class="ad">限时优惠，立即购买</div>
<article>
  <h1>大模型工具调用实践</h1>
  <p>工具调用让模型可以读取外部数据。本文介绍 <a href="https://example.com/doc">Function Calling</a> 的基本流程。</p>
  <p>第一步是声明工具的参数结构，第二步由模型决定何时调用，第三步把执行结果回填给模型继续推理。</p>
  <h2>常见问题</h2>
  <ul>
    <li>参数必须用 JSON Schema 描述</li>
    <li>流式响应里 tool_calls 会分片到达</li>
  </ul>
  <table>
    <tr><th>字段</th><th>说明</th></tr>
    <tr><td>name</td><td>工具名</td></tr>
    <tr><td>arguments</td><td>JSON 字符串</td></tr>
  </table>
</article>
<footer class="footer">版权所有 广告位招租</footer>
</body></html>
"""


def _registry(fetch) -> ToolRegistry:
    registry = ToolRegistry()
    module.register(registry, fetch=fetch)
    return registry


def _html_fetch(html: str):
    return lambda url, timeout: html.encode("utf-8")


def test_extracts_markdown_with_structure() -> None:
    result = asyncio.run(
        _registry(_html_fetch(ARTICLE_HTML)).execute(
            "fetchWeb", {"url": "https://example.com/post"}
        )
    )

    # 元信息与 URL
    assert "https://example.com/post" in result
    assert "标题: 大模型工具调用实践" in result
    assert "站点: 示例站点" in result

    # 保留正文结构：标题层级、列表、表格、链接
    assert "大模型工具调用实践" in result
    assert "## 常见问题" in result
    assert "参数必须用 JSON Schema 描述" in result
    assert "https://example.com/doc" in result
    assert "arguments" in result

    # 剔除导航、广告、页脚
    assert "关于我们" not in result
    assert "限时优惠" not in result
    assert "广告位招租" not in result


def test_blank_url_rejected() -> None:
    result = asyncio.run(_registry(_html_fetch(ARTICLE_HTML)).execute("fetchWeb", {"url": "  "}))
    assert "url 不能为空" in result


def test_non_http_url_rejected() -> None:
    result = asyncio.run(
        _registry(_html_fetch(ARTICLE_HTML)).execute("fetchWeb", {"url": "file:///etc/passwd"})
    )
    assert "只支持 http/https" in result


def test_empty_response() -> None:
    result = asyncio.run(
        _registry(lambda url, timeout: b"").execute("fetchWeb", {"url": "https://example.com"})
    )
    assert "空内容" in result


def test_non_html_response() -> None:
    result = asyncio.run(
        _registry(lambda url, timeout: b'{"ok": true}').execute(
            "fetchWeb", {"url": "https://api.example.com/data"}
        )
    )
    assert "不是 HTML" in result


def test_no_main_content() -> None:
    html = "<html><body><nav>菜单</nav><footer>底部</footer></body></html>"
    result = asyncio.run(
        _registry(_html_fetch(html)).execute("fetchWeb", {"url": "https://example.com/empty"})
    )
    assert "未能从页面提取到正文" in result


def test_curl_error_is_reported() -> None:
    def fetch(url: str, timeout: int) -> bytes:
        raise FetchWebError("连接失败，目标站点不可达")

    result = asyncio.run(
        _registry(fetch).execute("fetchWeb", {"url": "https://unreachable.example"})
    )
    assert "抓取失败" in result
    assert "连接失败" in result
    assert "https://unreachable.example" in result


def test_long_content_is_truncated() -> None:
    paragraph = "<p>" + ("这是用于测试截断的长句子。" * 40) + "</p>"
    html = f"<html><body><article><h1>长文</h1>{paragraph * 40}</article></body></html>"
    result = asyncio.run(
        _registry(_html_fetch(html)).execute("fetchWeb", {"url": "https://example.com/long"})
    )
    assert "已截断" in result
    assert len(result) < module.MAX_CHARS + 800


def test_curl_is_called_with_argument_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = b"<html><body>ok</body></html>"
        stderr = b""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._run_curl("https://example.com/a b", 5)

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "curl"
    assert command[-1] == "https://example.com/a b"
    assert "--max-time" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("shell") is not True
    assert kwargs.get("capture_output") is True


def test_curl_exit_code_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 6
        stdout = b""
        stderr = b"Could not resolve host"

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Completed())

    with pytest.raises(FetchWebError, match="域名解析失败"):
        module._run_curl("https://no-such-host.example", 5)


def test_curl_timeout_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="curl", timeout=1)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(FetchWebError, match="请求超时"):
        module._run_curl("https://example.com", 1)


def test_fetch_registered_by_default() -> None:
    assert "fetchWeb" in build_default_registry().names


def test_http_status_is_split_from_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 0
        stdout = b"<html><body>hello</body></html>\n200"
        stderr = b""

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Completed())

    body = module._run_curl("https://example.com", 5)
    assert body == b"<html><body>hello</body></html>"


def test_http_404_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 0
        stdout = b"\n404"
        stderr = b""

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Completed())

    with pytest.raises(FetchWebError, match="404"):
        module._run_curl("https://example.com/missing", 5)


def test_http_403_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 0
        stdout = b"\n403"
        stderr = b""

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Completed())

    with pytest.raises(FetchWebError, match="403"):
        module._run_curl("https://example.com/private", 5)
