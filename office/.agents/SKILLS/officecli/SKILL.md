---
name: officecli
description: "通过 OfficeCLI 操作 Word/Excel/PPT 文档的本地工具技能"
---

## officecli

调用本地 OfficeCLI 工具执行文档操作

### 使用方式
1. 确认 `officeli-win-x64.exe` 存在于项目根目录
2. 通过命令 `officeli-win-x64.exe <操作> <路径>` 调用

### 支持操作
- `read`：读取文档内容
- `create`：新建文档文件
- `edit`：修改文档内容

### 示例
```bash
officeli-win-x64.exe read ./test.docx
officeli-win-x64.exe create ./new_report.xlsx
officeli-win-x64.exe edit ./presentation.pptx
```
