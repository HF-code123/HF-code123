---
name: officecli-usage
description: "操作 .docx/.xlsx/.pptx 时必读：officecli-win-x64.exe 的正确调用方式、set/find-replace 语法、runCli 参数规则与常见坑。"
---

# OfficeCli 使用规范（.docx/.xlsx/.pptx）

操作 Office 文档时，必须通过 runCli 调用项目根目录下的 officecli-win-x64.exe。
动手前先读本技能，不要凭猜测拼命令。

## runCli 调用规则（最重要）

- command 只填 officecli-win-x64.exe，参数逐项放进 args 数组；
- subprocess 无 shell：**所有参数都不需要引号、不需要转义**；
  --prop 的 key=value 写成单个 arg，例如 "--prop"、"text=我是女生" 两项；
- **严禁**把路径塞进 --prop，也**不存在** /body/text() 这种路径——
  set 的正确形式是 set <file> <path> --prop key=value，path 是独立参数。

## 修改文本的正确写法

- 全文替换：set 通知.docx / --find 旧文字 --replace 新文字
- 指定段落格式：set 通知.docx /body/p[1] --prop bold=true
- 先查看结构：view <file> text 或 get <file> /body --depth 2
- 改完校验：validate <file>，再 view <file> text 确认

## 空文档 / 找不到段落时（重要）

- 检查文档内容**只用** view <file> text 或 view <file> outline，
  **不要用 view html**——它输出大量 CSS 噪声，浪费上下文且容易误判；
- view text 为空说明正文为空，此时直接**添加**段落，不要去找 p 元素：
  add 通知.docx /body --type paragraph --prop text=要写的文字
- add 成功返回 Added paragraph at /body/p[@paraId=...]；
- 之后要改这段文字，用全文替换：
  set 通知.docx / --find 旧文字 --replace 新文字

## 调用工具的纪律

- 需要执行命令时，**必须真正发起 runCli 工具调用**；
  严禁在回复文本里打印 {"name": "runCli", ...} 这样的 JSON 让用户自己执行；
- 一次只调用一个命令，等结果回来再决定下一步；
- 不要用 --from 来添加新文字内容——那是克隆已有元素的参数，
  写入新文字一律用 --type paragraph --prop text=...

## 常用命令

- create/view/get/query/add/set/remove/move/swap/validate/batch
- view 模式：text、outline、stats、issues、html
- 元素类型：docx 用 paragraph/run/table/cell；xlsx 用 cell（/Sheet1/A1）；
  pptx 用 slide/shape

## 帮助系统（不确定就查，不要猜）

- officecli help
- officecli help docx paragraph（元素 schema）
- officecli help docx set paragraph（set 可用属性）

## 常见坑

- runCli 不经过 shell：args 里不要带引号和转义符；
- 路径从 1 开始（/body/p[1] 是第一段）；
- 文件被 Word/WPS 打开时会失败，先让用户关闭；
- 修改非空文档前先 get 确认结构，避免破坏内容。
