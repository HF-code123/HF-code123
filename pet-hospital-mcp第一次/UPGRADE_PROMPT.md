# Upgrade Notes

后续新增工具时，在 `src/pet_hospital_mcp/tools/` 添加模块，复用 `PetHospitalClient`、统一错误结构和日志脱敏约定。不要引入 FastMCP，也不要添加会话状态。