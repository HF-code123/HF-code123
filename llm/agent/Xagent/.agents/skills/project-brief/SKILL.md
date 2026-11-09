---
name: project-brief
description: 生成 xAgent 项目结构简介。当用户询问「这个项目是什么」「介绍一下项目结构」时使用。
---

# Project Brief

生成 xAgent 项目简介时，按以下步骤执行，并配合已有工具完成：

1. 调用 `listDir` 列出项目根目录，确认 `core/`、`tui/`、`tests/` 存在；
2. 调用 `readFile` 读取 `config.example.ini`，说明配置项分为
   [llm] / [agent] / [search] 三部分；
3. 如需统计代码规模，调用 `runCli` 执行 `git`（args: ["log", "--oneline", "-5"]）
   或 `python`（args: ["--version"]）等命令辅助说明；
4. 输出一份不超过 200 字的中文简介，包含：项目定位、分层结构、
   工具调用机制、Skill 动态加载机制（`.agents/skills/` 目录）。

注意：涉及删除、覆盖等破坏性命令前先向用户确认；命令失败时如实说明原因。
