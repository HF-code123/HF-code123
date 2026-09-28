from typing import Any

import httpx

from .config import Settings
from .errors import AppError


class PetHospitalClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=settings.base_url, timeout=settings.timeout_seconds, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._settings.retries + 1):
            try:
                response = await self._client.get("/api/v1/pets", params=params)
                if response.status_code >= 400:
                    raise AppError("BACKEND_API_ERROR", "宠物医院接口返回错误", {"status": response.status_code})
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AppError("BACKEND_INVALID_RESPONSE", "宠物医院接口返回了非法 JSON") from exc
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                    raise AppError("BACKEND_INVALID_RESPONSE", "宠物医院接口返回结构无效")
                return payload["data"]
            except AppError:
                raise
            except httpx.TimeoutException as exc:
                if attempt >= self._settings.retries:
                    raise AppError("BACKEND_TIMEOUT", "请求宠物医院接口超时") from exc
            except httpx.RequestError as exc:
                if attempt >= self._settings.retries:
                    raise AppError("BACKEND_UNAVAILABLE", "无法连接宠物医院接口") from exc
        raise AppError("BACKEND_UNAVAILABLE", "无法连接宠物医院接口")