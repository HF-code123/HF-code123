from __future__ import annotations

from pathlib import Path

import pytest

from core.config import AppConfig, DEFAULT_SYSTEM_PROMPT, load_config


def _write_config(path: Path, extra: str = "") -> None:
    path.write_text(
        "[llm]\n"
        "base_url = http://localhost:1234/v1\n"
        "api_key = test-key\n"
        "model = test-model\n"
        + extra,
        encoding="utf-8",
    )


def test_load_config_with_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    config = load_config(config_path)

    assert config.llm.base_url == "http://localhost:1234/v1"
    assert config.llm.api_key == "test-key"
    assert config.llm.model == "test-model"
    assert config.llm.timeout == 60
    assert config.agent.max_tool_rounds == 8
    assert config.agent.system_prompt == DEFAULT_SYSTEM_PROMPT


def test_agent_section_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(
        config_path,
        "\n[agent]\nsystem_prompt = 自定义提示\nmax_tool_rounds = 3\n",
    )

    config = AppConfig.from_ini(config_path)

    assert config.agent.system_prompt == "自定义提示"
    assert config.agent.max_tool_rounds == 3


def test_missing_llm_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("[agent]\nmax_tool_rounds = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[llm\]"):
        AppConfig.from_ini(config_path)


def test_missing_required_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[llm]\nbase_url = http://localhost\nmodel = m\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="api_key"):
        AppConfig.from_ini(config_path)


def test_invalid_max_tool_rounds(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(config_path, "\n[agent]\nmax_tool_rounds = 0\n")

    with pytest.raises(ValueError, match="max_tool_rounds"):
        AppConfig.from_ini(config_path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AppConfig.from_ini(tmp_path / "nope.ini")


def test_search_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(
        config_path,
        "\n[search]\napi_key = baidu-key\ntop_k = 5\ntimeout = 12\nrecency = week\n",
    )

    search = AppConfig.from_ini(config_path).search

    assert search.enabled is True
    assert search.api_key == "baidu-key"
    assert search.base_url == "https://qianfan.baidubce.com"
    assert search.top_k == 5
    assert search.timeout == 12
    assert search.recency == "week"


def test_search_disabled_without_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    assert AppConfig.from_ini(config_path).search.enabled is False


def test_search_key_falls_back_to_baidu_llm_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[llm]\n"
        "base_url = https://qianfan.baidubce.com/v2\n"
        "api_key = baidu-key\n"
        "model = ernie\n"
        "\n[search]\napi_key =\n",
        encoding="utf-8",
    )

    assert AppConfig.from_ini(config_path).search.api_key == "baidu-key"


def test_invalid_search_recency(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(config_path, "\n[search]\napi_key = k\nrecency = hour\n")

    with pytest.raises(ValueError, match="recency"):
        AppConfig.from_ini(config_path)


def test_search_reads_baidu_qianfan_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(
        config_path,
        "\n[search]\nbaidu_qianfan_api_key = bce-key\nmodel = ernie-x\n",
    )

    search = AppConfig.from_ini(config_path).search

    assert search.api_key == "bce-key"
    assert search.model == "ernie-x"


def test_search_prefers_baidu_qianfan_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    _write_config(
        config_path,
        "\n[search]\nbaidu_qianfan_api_key = new-key\napi_key = old-key\n",
    )

    assert AppConfig.from_ini(config_path).search.api_key == "new-key"


# ---------- find_config：示例模板不屏蔽真实配置 ----------

from core.config import MOCK_CONFIG_TEMPLATE, find_config  # noqa: E402


class TestFindConfigSkipsMock:
    def test_mock_in_cwd_falls_back_to_real(self, tmp_path: Path, monkeypatch) -> None:
        cwd = tmp_path / "cwd"
        root = tmp_path / "root"
        cwd.mkdir()
        root.mkdir()
        (cwd / "config.ini").write_text(MOCK_CONFIG_TEMPLATE, encoding="utf-8")
        _write_config(root / "config.ini")
        monkeypatch.chdir(cwd)
        monkeypatch.setattr("core.config.PROJECT_ROOT", root)

        assert find_config(None) == (root / "config.ini").resolve()

    def test_modified_cwd_config_wins(self, tmp_path: Path, monkeypatch) -> None:
        cwd = tmp_path / "cwd"
        root = tmp_path / "root"
        cwd.mkdir()
        root.mkdir()
        _write_config(cwd / "config.ini")
        _write_config(root / "config.ini")
        monkeypatch.chdir(cwd)
        monkeypatch.setattr("core.config.PROJECT_ROOT", root)

        assert find_config(None) == (cwd / "config.ini").resolve()

    def test_explicit_path_not_skipped_even_if_mock(self, tmp_path: Path) -> None:
        mock = tmp_path / "config.ini"
        mock.write_text(MOCK_CONFIG_TEMPLATE, encoding="utf-8")

        assert find_config(mock) == mock.resolve()

    def test_mock_only_raises(self, tmp_path: Path, monkeypatch) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        (cwd / "config.ini").write_text(MOCK_CONFIG_TEMPLATE, encoding="utf-8")
        monkeypatch.chdir(cwd)
        monkeypatch.setattr("core.config.PROJECT_ROOT", tmp_path / "root")

        with pytest.raises(FileNotFoundError):
            find_config(None)
