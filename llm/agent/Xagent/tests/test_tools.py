from __future__ import annotations

import asyncio
from pathlib import Path

from core.tools import build_default_registry
from core.tools.datetime_tools import is_leap_year


def test_default_registry_contains_all_tools() -> None:
    registry = build_default_registry()

    assert set(registry.names) == {
        "getDate",
        "isLeapYear",
        "getCwd",
        "listDir",
        "getFileType",
        "readFile",
        "createFile",
        "editFile",
        "patchFile",
        "createDir",
        "deleteDir",
        "deleteFile",
        "renamePath",
        "fetchWeb",
        "runCli",
    }
    assert len(registry.definitions()) == 15


def test_leap_year() -> None:
    assert "是闰年" in is_leap_year(2024)
    assert "不是闰年" in is_leap_year(2023)
    assert "是闰年" in is_leap_year(2000)
    assert "不是闰年" in is_leap_year(1900)


def test_file_workflow(tmp_path: Path) -> None:
    registry = build_default_registry()

    async def run(name: str, **arguments):
        return await registry.execute(name, arguments)

    target_dir = tmp_path / "notes"
    created_dir = asyncio.run(run("createDir", path=str(target_dir)))
    assert "已创建目录" in created_dir

    created_file = asyncio.run(
        run("createFile", dir=str(target_dir), filename="a.txt", content="hello")
    )
    assert "已创建文件" in created_file

    listed = asyncio.run(run("listDir", path=str(target_dir)))
    assert "a.txt" in listed

    file_type = asyncio.run(run("getFileType", path=str(target_dir / "a.txt")))
    assert "text/plain" in file_type

    read = asyncio.run(run("readFile", path=str(target_dir / "a.txt")))
    assert "hello" in read

    edited = asyncio.run(
        run("editFile", path=str(target_dir / "a.txt"), content="world")
    )
    assert "已写入" in edited
    assert (target_dir / "a.txt").read_text(encoding="utf-8") == "world"

    renamed = asyncio.run(
        run(
            "renamePath",
            old=str(target_dir / "a.txt"),
            new=str(target_dir / "b.txt"),
        )
    )
    assert "已重命名" in renamed

    deleted_file = asyncio.run(run("deleteFile", path=str(target_dir / "b.txt")))
    assert "已删除文件" in deleted_file
    deleted_dir = asyncio.run(run("deleteDir", path=str(target_dir)))
    assert "已删除目录" in deleted_dir
    assert not target_dir.exists()


class TestPatchFile:
    """patchFile：只改指定片段，用于大文件里填占位符。"""

    def _run(self, **arguments):
        registry = build_default_registry()
        return asyncio.run(registry.execute("patchFile", arguments))

    def test_replaces_unique_snippet(self, tmp_path: Path) -> None:
        target = tmp_path / "deck.html"
        target.write_text("<html>\n<!-- SLIDES_HERE -->\n</html>\n", encoding="utf-8")

        result = self._run(
            path=str(target),
            find="<!-- SLIDES_HERE -->",
            replace="<!-- SLIDES_HERE -->\n<section class=\"slide\">第一页</section>",
        )

        assert "已修改" in result
        text = target.read_text(encoding="utf-8")
        assert "第一页" in text
        assert text.count("SLIDES_HERE") == 1
        assert text.endswith("</html>\n")

    def test_missing_snippet_touches_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("原始内容", encoding="utf-8")

        result = self._run(path=str(target), find="不存在", replace="x")

        assert "未找到" in result
        assert target.read_text(encoding="utf-8") == "原始内容"

    def test_ambiguous_snippet_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("dup\ndup\n", encoding="utf-8")

        result = self._run(path=str(target), find="dup", replace="x")

        assert "出现 2 次" in result
        assert target.read_text(encoding="utf-8") == "dup\ndup\n"

    def test_empty_replace_deletes_snippet(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("保留-删除-保留", encoding="utf-8")

        result = self._run(path=str(target), find="-删除-")

        assert "已修改" in result
        assert target.read_text(encoding="utf-8") == "保留保留"

    def test_preserves_crlf_and_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_bytes("\ufeff行一\r\n\u5360\u4f4d\r\n".encode("utf-8"))

        self._run(path=str(target), find="占位", replace="填空")

        raw = target.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert raw.decode("utf-8-sig") == "行一\r\n填空\r\n"

    def test_missing_file(self, tmp_path: Path) -> None:
        assert "文件不存在" in self._run(
            path=str(tmp_path / "nope.txt"), find="a", replace="b"
        )

    def test_empty_find_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("内容", encoding="utf-8")
        # find 是必需参数：空串被参数校验挡下，提示语应指明 field 而非泛泛报错
        result = self._run(path=str(target), find="", replace="x")
        assert "缺少必需参数" in result and "find" in result
        assert target.read_text(encoding="utf-8") == "内容"


class TestParseToolArguments:
    """小模型常见 JSON 笔误的自动修复。"""

    def _run_raw(self, raw: str, tmp_path: Path) -> str:
        registry = build_default_registry()
        return asyncio.run(registry.execute_raw("createFile", raw))

    @staticmethod
    def _json_path(path: Path) -> str:
        """模型输出的路径是 JSON 转义过的（Windows 下 `\\` 写成 `\\\\`）。"""
        return str(path).replace("\\", "\\\\")

    def test_missing_comma_no_whitespace(self, tmp_path: Path) -> None:
        """截图场景：引号紧挨着缺逗号（"x""y"），中间没有任何空白。"""
        raw = '{"dir": "%s""filename": "a.txt""content": "hi"}' % self._json_path(
            tmp_path
        )
        assert "已创建文件" in self._run_raw(raw, tmp_path)

    def test_missing_comma_between_fields(self, tmp_path: Path) -> None:
        raw = '{"dir": "%s" "filename": "a.txt" "content": "hi"}' % self._json_path(
            tmp_path
        )
        assert "已创建文件" in self._run_raw(raw, tmp_path)

    def test_markdown_fence_stripped(self, tmp_path: Path) -> None:
        raw = '```json\n{"dir": "%s", "filename": "b.txt"}\n```' % self._json_path(
            tmp_path
        )
        assert "已创建文件" in self._run_raw(raw, tmp_path)

    def test_prose_around_json(self, tmp_path: Path) -> None:
        raw = '好的，参数如下：{"dir": "%s", "filename": "c.txt"} 以上。' % self._json_path(
            tmp_path
        )
        assert "已创建文件" in self._run_raw(raw, tmp_path)

    def test_literal_newline_inside_string(self, tmp_path: Path) -> None:
        raw = (
            '{"dir": "%s", "filename": "d.txt", '
            '"content": "第一行\n第二行"}' % self._json_path(tmp_path)
        )
        assert "已创建文件" in self._run_raw(raw, tmp_path)
        text = (tmp_path / "d.txt").read_text(encoding="utf-8")
        assert "第一行" in text and "第二行" in text

    def test_unrepairable_reports_error(self) -> None:
        registry = build_default_registry()
        result = asyncio.run(registry.execute_raw("createFile", "{bad json"))
        assert "不是合法 JSON" in result

    def test_missing_required_params_friendly(self, tmp_path: Path) -> None:
        registry = build_default_registry()
        result = asyncio.run(registry.execute("createFile", {}))
        assert "缺少必需参数" in result
        assert "dir" in result and "filename" in result
        assert "目标目录路径" in result


class TestCreateFileImage:
    """createFile 对图片扩展名做 base64 自动解码。"""

    # 1x1 透明 PNG
    PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    def _create(self, tmp_path: Path, filename: str, content: str) -> str:
        registry = build_default_registry()
        return asyncio.run(
            registry.execute(
                "createFile",
                {"dir": str(tmp_path), "filename": filename, "content": content},
            )
        )

    def test_base64_png_decoded(self, tmp_path: Path) -> None:
        result = self._create(tmp_path, "a.png", self.PNG_B64)
        assert "已创建图片" in result and "base64 已解码" in result
        assert (tmp_path / "a.png").read_bytes().startswith(b"\x89PNG")

    def test_data_uri_prefix_accepted(self, tmp_path: Path) -> None:
        result = self._create(
            tmp_path, "b.png", "data:image/png;base64," + self.PNG_B64
        )
        assert "已创建图片" in result
        assert (tmp_path / "b.png").read_bytes().startswith(b"\x89PNG")

    def test_wrapped_base64_with_newlines(self, tmp_path: Path) -> None:
        wrapped = "\n".join(self.PNG_B64[i : i + 40] for i in range(0, len(self.PNG_B64), 40))
        result = self._create(tmp_path, "c.png", wrapped)
        assert "已创建图片" in result
        assert (tmp_path / "c.png").read_bytes().startswith(b"\x89PNG")

    def test_invalid_base64_warns_but_writes(self, tmp_path: Path) -> None:
        result = self._create(tmp_path, "d.png", "这不是base64###")
        assert "警告" in result
        assert (tmp_path / "d.png").read_text(encoding="utf-8") == "这不是base64###"

    def test_text_file_unaffected(self, tmp_path: Path) -> None:
        result = self._create(tmp_path, "e.txt", self.PNG_B64)
        assert "已创建文件" in result
        assert (tmp_path / "e.txt").read_text(encoding="utf-8") == self.PNG_B64


def test_unknown_tool_and_invalid_json() -> None:
    registry = build_default_registry()

    unknown = asyncio.run(registry.execute("nope", {}))
    invalid = asyncio.run(registry.execute_raw("readFile", "{bad json"))

    assert "未知工具" in unknown
    assert "不是合法 JSON" in invalid
