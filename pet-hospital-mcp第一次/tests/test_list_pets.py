import json

import httpx
import pytest
from pydantic import ValidationError

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import AppError
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools.list_pets import ListPetsInput, list_pets


def response_data() -> dict:
    return {
        "items": [{
            "id": "PET-1", "name": "旺财", "species": "犬", "ownerName": "张三",
            "ownerPhone": "13800000000", "doctor": "李医生", "disease": "皮肤病",
            "status": "已康复", "records": None, "charges": None, "totalCost": 12.5,
            "visitCount": 0,
        }],
        "total": 1, "page": 2, "pageSize": 10, "totalPages": 1, "totalCost": 12.5,
    }


@pytest.mark.asyncio
async def test_list_pets_forwards_all_parameters_and_accepts_null_collections():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 200, "data": response_data()})

    settings = Settings(base_url="http://test", retries=0)
    client = PetHospitalClient(settings, httpx.MockTransport(handler))
    value = await list_pets(ListPetsInput.model_validate({
        "q": "旺", "name": "旺财", "ownerName": "张三", "ownerPhone": "13800000000",
        "species": "犬", "doctor": "李医生", "disease": "皮肤病", "status": "已康复",
        "min": 1.0, "max": 20.0, "sortBy": "totalCost", "order": "desc",
        "page": 2, "pageSize": 10,
    }), client, __import__("logging").getLogger("test"))
    assert seen["path"] == "/api/v1/pets"
    assert seen["params"]["ownerPhone"] == "13800000000"
    assert seen["params"]["pageSize"] == "10"
    assert value.items[0].records == []
    await client.aclose()


@pytest.mark.parametrize("payload", [
    {"page": 0}, {"pageSize": 501}, {"species": "fish"},
    {"unknown": 1}, {"min": 10.0, "max": 1.0}, {"min": float("nan")},
])
def test_input_validation(payload):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate(payload)


@pytest.mark.asyncio
async def test_backend_error_is_structured():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    client = PetHospitalClient(Settings(base_url="http://test", retries=0), httpx.MockTransport(handler))
    with pytest.raises(AppError) as error:
        await client.list_pets({})
    assert error.value.code == "BACKEND_API_ERROR"
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_json_is_structured():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = PetHospitalClient(Settings(base_url="http://test", retries=0), httpx.MockTransport(handler))
    with pytest.raises(AppError) as error:
        await client.list_pets({})
    assert error.value.code == "BACKEND_INVALID_RESPONSE"
    await client.aclose()