"""联网搜索工具：百度千帆「智能搜索生成」API。

文档：https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u
- 主接口：POST /v2/ai_search/chat/completions（检索 + 大模型总结，返回摘要与参考来源）
- 鉴权：Authorization: Bearer <API Key>，Key 由 config.ini 的 [search] 段提供，
  本模块不持有任何硬编码密钥。
- 兜底：主接口失败（欠费、模型不可用等）时自动退回 /v2/ai_search/web_search，
  只返回网页结果标题/链接/摘要，并在结果里说明已降级。

设计约束：
- 只依赖 config.SearchConfig，不读全局配置，不依赖任何前端；
- 所有失败都转成可读的中文提示返回给 LLM，不抛裸异常；
- 返回值面向模型阅读，控制长度以节省 token。
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import SearchConfig
from .registry import ToolRegistry, ToolSpec

CHAT_PATH = "/v2/ai_search/chat/completions"
WEB_PATH = "/v2/ai_search/web_search"
SEARCH_SOURCE = "baidu_search_v2"
# 主接口（智能搜索生成）的 search_recency_filter 只接受 semiyear/week/year/month，
# 不支持 day；工具层仍允许模型传 day（语义更自然），请求主接口时降级为 week。
PRIMARY_RECENCY_MAP = {"day": "week"}

MAX_TOP_K = 20
ANSWER_CHARS = 2000
SNIPPET_CHARS = 300
MAX_SOURCES = 8

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索关键词或问题，尽量精炼（建议 30 字以内）",
        },
        "top_k": {
            "type": "integer",
            "description": f"参考来源条数，1~{MAX_TOP_K}，不填用配置里的默认值",
        },
        "recency": {
            "type": "string",
            "enum": ["day", "week", "month", "semiyear", "year"],
            "description": "限定网页发布时间范围，不填表示不限",
        },
    },
    "required": ["query"],
}


class SearchError(RuntimeError):
    """搜索调用失败，message 已是可直接展示给用户/LLM 的中文说明。"""


class SearchWebTool:
    """把百度千帆智能搜索生成封装成 Function Calling 工具。"""

    def __init__(
        self,
        config: SearchConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.api_key.strip():
            raise ValueError("联网搜索缺少 baidu_qianfan_api_key")
        self._config = config
        self._transport = transport
        # 主接口出现过不可恢复的错误（如欠费）后，本次会话内直接走兜底，
        # 避免每次搜索都白白多打一次必然失败的请求。
        self._primary_error: str = ""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="searchWeb",
            description=(
                "联网搜索互联网实时信息（百度千帆智能搜索生成）。"
                "当问题涉及最新新闻、实时数据、近期事件，或你不确定答案时，先调用它检索再回答。"
                "返回内容包含搜索摘要和参考来源的标题与链接。"
            ),
            parameters=PARAMETERS,
            handler=self._run,
        )

    async def _run(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "搜索失败: query 不能为空，请提供要搜索的关键词或问题。"

        try:
            top_k = self._top_k(arguments)
            recency = self._recency(arguments)
        except SearchError as exc:
            return f"搜索失败: {exc}"

        payload = self._payload(query, top_k)
        # 两个接口对 search_recency_filter 的支持范围不同：
        # chat/completions 只接受 semiyear/week/year/month，web_search 还支持 day。
        web_payload = dict(payload)
        if recency:
            web_payload["search_recency_filter"] = recency
        primary_payload = {**payload, "model": self._config.model, "stream": False}
        if recency:
            primary_payload["search_recency_filter"] = PRIMARY_RECENCY_MAP.get(recency, recency)

        if self._primary_error:
            return await self._fallback(query, web_payload, self._primary_error)

        try:
            data = await self._post(CHAT_PATH, primary_payload)
            return _format_ai_answer(query, data, MAX_SOURCES)
        except SearchError as primary_error:
            self._remember_primary_error(str(primary_error))
            return await self._fallback(query, web_payload, str(primary_error))

    async def _fallback(self, query: str, payload: dict[str, Any], reason: str) -> str:
        """智能搜索生成不可用时，退化为纯网页搜索，保证 Agent 仍有可用信息。"""

        try:
            data = await self._post(WEB_PATH, payload)
        except SearchError as fallback_error:
            return f"搜索失败: 智能搜索生成不可用（{reason}）；网页搜索兜底也失败（{fallback_error}）。"
        return _format_web_results(query, data, reason)

    def _remember_primary_error(self, message: str) -> None:
        """只记住不可自愈的错误；网络抖动、超时下次仍应重试主接口。"""

        if "欠费" in message or "account_overdue" in message:
            self._primary_error = message

    # ------------------------------------------------------------------ 请求

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._config.timeout, transport=self._transport
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SearchError(f"请求超时（{self._config.timeout:g} 秒）") from exc
        except httpx.HTTPError as exc:
            raise SearchError(f"网络请求失败: {exc}") from exc

        data = _read_json(response)
        if response.status_code != 200:
            raise SearchError(_describe_error(response.status_code, data, response.text))
        if not data:
            raise SearchError(f"接口返回内容无法解析: {response.text[:200]}")
        if "choices" not in data and "references" not in data:
            code = data.get("code") or data.get("error_code")
            if code:
                raise SearchError(_describe_error(response.status_code, data, ""))
        return data

    def _payload(self, query: str, top_k: int) -> dict[str, Any]:
        """两个接口共用的检索参数（model / stream 只属于主接口，另外补）。"""

        return {
            "messages": [{"role": "user", "content": query}],
            "search_source": SEARCH_SOURCE,
            # 注意：文档里 resource_type_filter 写成对象，实测必须传数组，
            # 否则返回 InvalidArgument: cannot unmarshal object into []SearchResource。
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
        }

    def _top_k(self, arguments: dict[str, Any]) -> int:
        raw = arguments.get("top_k", self._config.top_k)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return self._config.top_k
        return min(max(value, 1), MAX_TOP_K)

    def _recency(self, arguments: dict[str, Any]) -> str:
        value = str(arguments.get("recency", "") or "").strip()
        if not value:
            return self._config.recency
        if value not in ("day", "week", "month", "semiyear", "year"):
            raise SearchError(
                f"recency 取值不合法: {value}，可选 day/week/month/semiyear/year。"
            )
        return value


def register(
    registry: ToolRegistry,
    config: SearchConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    registry.register(SearchWebTool(config, transport=transport).spec())


# ---------------------------------------------------------------- 结果格式化


def _format_ai_answer(query: str, data: dict[str, Any], max_sources: int) -> str:
    """智能搜索生成：返回「摘要 + 参考来源」。"""

    answer = _answer_text(data).strip()
    references = _references(data)

    if not answer and not references:
        return f"没有找到与「{query}」相关的搜索结果。"

    lines: list[str] = []
    if answer:
        lines.append(answer[:ANSWER_CHARS])

    sources = _source_lines(references, max_sources)
    if sources:
        lines.append("")
        lines.append("参考来源：")
        lines.extend(sources)
    return "\n".join(lines)


def _format_web_results(query: str, data: dict[str, Any], reason: str) -> str:
    """兜底：只有网页结果时，明确告知已降级，避免 LLM 误以为是完整总结。"""

    references = _references(data)
    if not references:
        return f"没有找到与「{query}」相关的搜索结果。（智能搜索生成不可用：{reason}）"

    lines = [
        f"注意：智能搜索生成不可用（{reason}），以下为百度网页搜索结果，仅供参考。",
        f"百度搜索「{query}」，共 {len(references)} 条结果：",
    ]
    for index, item in enumerate(references[:MAX_SOURCES], start=1):
        title = str(item.get("title") or item.get("web_anchor") or "无标题").strip()
        lines.append(f"{index}. {title}")
        if item.get("date"):
            lines.append(f"   发布时间: {item['date']}")
        if item.get("url"):
            lines.append(f"   链接: {item['url']}")
        snippet = str(item.get("content") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"   摘要: {snippet[:SNIPPET_CHARS]}")
    return "\n".join(lines)


def _answer_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(first.get("content") or "")


def _references(data: dict[str, Any]) -> list[dict[str, Any]]:
    references = data.get("references")
    if not isinstance(references, list):
        return []
    return [item for item in references if isinstance(item, dict)]


def _source_lines(references: list[dict[str, Any]], limit: int) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(references[:limit], start=1):
        title = str(item.get("title") or item.get("web_anchor") or "无标题").strip()
        url = str(item.get("url") or "").strip()
        date = str(item.get("date") or "").strip()
        suffix = f"（{date}）" if date else ""
        lines.append(f"{index}. {title} — {url}{suffix}" if url else f"{index}. {title}{suffix}")
    return lines


# -------------------------------------------------------------------- 错误处理


def _read_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _describe_error(status: int, data: dict[str, Any], text: str) -> str:
    code = data.get("code") or data.get("error_code") or ""
    message = data.get("message") or data.get("error_msg") or ""
    if code and str(code) == "account_overdue":
        return "百度千帆账号欠费（account_overdue），请充值或开通后付费"
    detail = _detail_text(data)
    if message:
        suffix = f"（{detail}）" if detail else ""
        return f"HTTP {status} [{code}] {message}{suffix}" if code else f"HTTP {status} {message}{suffix}"
    if code:
        return f"HTTP {status} 错误码 {code}"
    snippet = (text or "").strip()[:200]
    return f"HTTP {status} {snippet}".strip()


def _detail_text(data: dict[str, Any]) -> str:
    """把百度返回的 detail 字段压成一句人话，便于模型自己纠正参数。"""

    detail = data.get("detail")
    if not isinstance(detail, list):
        return ""
    parts = []
    for item in detail[:3]:
        if not isinstance(item, dict):
            continue
        field = item.get("field") or ""
        message = item.get("message") or ""
        parts.append(f"{field}: {message}" if field else str(message))
    return "; ".join(part for part in parts if part)
