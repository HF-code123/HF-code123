"""配置加载层：读取并校验 config.ini，不包含任何界面逻辑。

配置分为三个部分：
- [llm]：模型服务连接信息；
- [agent]：Agent 行为参数（系统提示词、工具调用轮次上限等）；
- [search]：联网搜索服务（百度千帆「智能搜索生成」），留空则不启用该工具。

任何前端（tui / web / desktop / mobile）都通过 AppConfig 获取配置，
不需要自己解析 ini 文件。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = (
    "你是一个可以调用本地工具和联网搜索的智能助手。"
    "涉及当前日期、时间、年份的问题，必须先调用 getDate 工具获取真实日期，"
    "严禁凭记忆猜测今天的日期或年份；判断闰年前也必须先用 getDate 确认年份。"
    "涉及新闻、实时数据、近期事件或你不确定的信息时，先调用 searchWeb 搜索再回答，"
    "并在回答中标注信息来源链接。"
    "用户给出具体网址，或需要阅读某篇文章、文档的完整内容时，调用 fetchWeb 抓取正文，"
    "不要凭标题猜测页面内容。"
    "fetchWeb 失败时如实说明失败原因并提示用户核对链接，严禁凭记忆编造网页内容。"
    "用户要求运行命令行程序、查看某个工具版本、执行 git/npm/pip 等命令时，调用 runCli："
    "command 只填程序名（如 git），参数逐项放进 args 数组，不要拼接整条命令字符串；"
    "根据返回的退出码、标准输出和标准错误判断执行结果，失败时如实说明原因并给出修正建议。"
    "runCli 不支持管道、重定向等 shell 语法，需要时显式调用 cmd /c 或 bash -c。"
    "涉及删除、覆盖等破坏性命令前，先向用户确认。"
    "需要读取或操作本地文件时，优先调用对应的文件工具，不要凭空猜测文件内容或路径。"
    "回答使用简体中文，保持简洁准确。"
)

CONFIG_ENV_VAR = "XAGENT_CONFIG"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SEARCH_BASE_URL = "https://qianfan.baidubce.com"
DEFAULT_SEARCH_TOP_K = 8
DEFAULT_SEARCH_MODEL = "ernie-4.5-turbo-128k"
SEARCH_RECENCY_VALUES = ("day", "week", "month", "semiyear", "year")


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI 兼容服务的连接配置。"""

    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0


@dataclass(frozen=True)
class AgentConfig:
    """Agent 的业务行为配置。"""

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_tool_rounds: int = 8


@dataclass(frozen=True)
class SearchConfig:
    """联网搜索（百度千帆「智能搜索生成」）配置。

    参考文档：https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u
    baidu_qianfan_api_key 为空表示不启用联网搜索工具。
    """

    api_key: str = ""
    base_url: str = DEFAULT_SEARCH_BASE_URL
    model: str = DEFAULT_SEARCH_MODEL
    top_k: int = DEFAULT_SEARCH_TOP_K
    timeout: float = 30.0
    recency: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())


@dataclass(frozen=True)
class AppConfig:
    """应用完整配置，由 core 层对外提供。"""

    llm: LLMConfig
    agent: AgentConfig = field(default_factory=AgentConfig)
    search: SearchConfig = field(default_factory=SearchConfig)

    @classmethod
    def from_ini(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).expanduser()
        if not config_path.is_file():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if "llm" not in parser:
            raise ValueError(f"配置文件缺少 [llm] 段: {config_path}")

        section = parser["llm"]
        missing = [
            key for key in ("base_url", "api_key", "model") if not section.get(key, "").strip()
        ]
        if missing:
            raise ValueError(f"配置项缺失: {', '.join(missing)}")

        agent_section = parser["agent"] if "agent" in parser else {}
        system_prompt = agent_section.get("system_prompt", "").strip() or DEFAULT_SYSTEM_PROMPT
        max_tool_rounds = _parse_positive_int(
            agent_section.get("max_tool_rounds", "8"), "max_tool_rounds"
        )
        timeout = _parse_positive_float(section.get("timeout", "60"), "timeout")

        return cls(
            llm=LLMConfig(
                base_url=section["base_url"].strip(),
                api_key=section["api_key"].strip(),
                model=section["model"].strip(),
                timeout=timeout,
            ),
            agent=AgentConfig(
                system_prompt=system_prompt,
                max_tool_rounds=max_tool_rounds,
            ),
            search=_parse_search(parser),
        )


def find_config(path: str | Path | None = None) -> Path:
    """按优先级查找配置文件：显式路径 > 环境变量 > 当前目录 > 项目根目录。"""

    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path).expanduser())
    else:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.append(Path.cwd() / "config.ini")
        candidates.append(PROJECT_ROOT / "config.ini")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    searched = "\n".join(f"  - {item}" for item in candidates)
    raise FileNotFoundError(f"未找到配置文件，已查找:\n{searched}")


def load_config(path: str | Path | None = None) -> AppConfig:
    """加载配置。调用方只需关心返回的 AppConfig。"""

    return AppConfig.from_ini(find_config(path))


def _parse_search(parser: configparser.ConfigParser) -> SearchConfig:
    """解析 [search] 段；没有该段或没有 key 时返回禁用状态。

    鉴权 Key 的读取优先级：
    1. baidu_qianfan_api_key（推荐，与界面/文档命名一致）
    2. api_key（旧配置名，保留兼容）
    3. 当 [llm] 也指向百度千帆时，复用 [llm].api_key
    """

    if "search" not in parser:
        return SearchConfig()

    section = parser["search"]
    api_key = (
        section.get("baidu_qianfan_api_key", "").strip()
        or section.get("api_key", "").strip()
    )
    if not api_key:
        llm_base_url = parser["llm"].get("base_url", "").lower()
        if "qianfan" in llm_base_url or "baidu" in llm_base_url:
            api_key = parser["llm"].get("api_key", "").strip()
    if not api_key:
        return SearchConfig()

    recency = section.get("recency", "").strip()
    if recency and recency not in SEARCH_RECENCY_VALUES:
        raise ValueError(
            f"配置项 search.recency 取值不合法: {recency!r}，"
            f"可选值: {', '.join(SEARCH_RECENCY_VALUES)}"
        )

    return SearchConfig(
        api_key=api_key,
        base_url=section.get("base_url", "").strip() or DEFAULT_SEARCH_BASE_URL,
        model=section.get("model", "").strip() or DEFAULT_SEARCH_MODEL,
        top_k=_parse_positive_int(
            section.get("top_k", str(DEFAULT_SEARCH_TOP_K)), "search.top_k"
        ),
        timeout=_parse_positive_float(section.get("timeout", "30"), "search.timeout"),
        recency=recency,
    )


def _parse_positive_int(raw: str, key: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置项 {key} 必须是整数: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"配置项 {key} 必须大于 0: {value}")
    return value


def _parse_positive_float(raw: str, key: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置项 {key} 必须是数字: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"配置项 {key} 必须大于 0: {value}")
    return value
