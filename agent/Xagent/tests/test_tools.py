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
        "createDir",
        "deleteDir",
        "deleteFile",
        "renamePath",
        "fetchWeb",
        "runCli",
    }
    assert len(registry.definitions()) == 14


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


def test_unknown_tool_and_invalid_json() -> None:
    registry = build_default_registry()

    unknown = asyncio.run(registry.execute("nope", {}))
    invalid = asyncio.run(registry.execute_raw("readFile", "{bad json"))

    assert "未知工具" in unknown
    assert "不是合法 JSON" in invalid
