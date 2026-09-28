# Pet Hospital MCP

独立的 Python MCP 适配器，将现有 Go 宠物医院 REST API 的 `GET /api/v1/pets` 暴露为唯一工具 `list_pets`。

## 安装与启动

先启动仓库根目录的 Go 服务：

```powershell
..\pethospital.exe
```

然后在本目录安装并启动 MCP 服务：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pet_hospital_mcp
```

默认监听 `127.0.0.1:8000`。可配置：

- `MCP_HOST`：监听地址，默认 `127.0.0.1`
- `MCP_PORT`：监听端口，默认 `8000`
- `PET_HOSPITAL_BASE_URL`：Go API 地址，默认 `http://127.0.0.1:8080`

MCP Streamable HTTP 端点为 `http://127.0.0.1:8000/mcp`，健康检查为 `http://127.0.0.1:8000/health`。

本项目使用 `mcp==2.0.0`、`MCPServer` 和 MCP 协议 `2026-07-28` 对应的 SDK 2.x API，HTTP 模式是无状态 Streamable HTTP，不使用 FastMCP、会话存储或 `Mcp-Session-Id`。

## 工具示例

`list_pets` 的输入参数直接对应 Go API 支持的查询参数，例如：

```json
{"species":"犬","min":1000.0,"sortBy":"totalCost","order":"desc","page":1,"pageSize":10}
```

返回 `items`、`total`、`page`、`pageSize`、`totalPages`、`totalCost`。无效输入和上游异常返回统一结构化错误。

## 验证

浏览器或 PowerShell 访问健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

使用 MCP Inspector 或 SDK 2.x 客户端连接 `http://127.0.0.1:8000/mcp`，调用 `list_pets`。SDK 负责标准 MCP 握手、工具发现和调用；本服务不实现旧版手写 `initialize` 流程。

运行测试：

```powershell
cd pet-hospital-mcp
pytest -q
```

测试不会访问真实 Go 服务，预期全部通过。

阶段二工具未实现；本次仅交付 `list_pets`。