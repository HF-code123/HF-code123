import logging
import time
from typing import Literal

from pydantic import ValidationError

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server import MCPServer

from .config import Settings
from .logging_config import configure_logging
from .rest_client import PetHospitalClient
from .tools.list_pets import ListPetsInput, ListPetsData, list_pets as call_list_pets


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    configure_logging()
    logger = logging.getLogger("pet_hospital_mcp")
    client = PetHospitalClient(settings)
    server = MCPServer(
        name="pet-hospital",
        version="0.1.0",
        description="将宠物医院 REST API 的宠物档案查询能力提供给 AI Agent。",
    )

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @server.tool(
        name="list_pets",
        description=(
            "查询宠物医院宠物档案列表，支持关键词、主人、物种、医生、疾病、状态、费用范围、排序和分页。"
            "适用于查找和筛选宠物档案；返回 items、total、page、pageSize、totalPages、totalCost。"
        ),
        structured_output=True,
    )
    async def list_pets(
        q: str | None = None,
        name: str | None = None,
        ownerName: str | None = None,
        ownerPhone: str | None = None,
        species: Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"] | None = None,
        doctor: str | None = None,
        disease: str | None = None,
        status: Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"] | None = None,
        min: float | None = None,
        max: float | None = None,
        sortBy: Literal["id", "name", "ownerName", "species", "doctor", "disease", "status", "totalCost", "visitCount", "createdAt", "updatedAt"] | None = None,
        order: Literal["asc", "desc"] | None = None,
        page: int = 1,
        pageSize: int = 50,
    ) -> ListPetsData:
        started = time.perf_counter()
        try:
            input_data = ListPetsInput.model_validate({
                "q": q, "name": name, "ownerName": ownerName, "ownerPhone": ownerPhone,
                "species": species, "doctor": doctor, "disease": disease, "status": status,
                "min": min, "max": max, "sortBy": sortBy, "order": order,
                "page": page, "pageSize": pageSize,
            })
            return await call_list_pets(input_data, client, logger)
        except ValidationError as exc:
            from .errors import AppError, raise_tool_error

            raise_tool_error(AppError("VALIDATION_ERROR", "工具输入参数无效"))
        finally:
            logger.debug("list_pets request finished", extra={"tool_name": "list_pets", "duration_ms": round((time.perf_counter() - started) * 1000, 2)})

    return server


def create_app(settings: Settings | None = None):
    server = create_server(settings)
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host=(settings or Settings.from_env()).host,
    )