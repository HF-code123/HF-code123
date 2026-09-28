from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

from ..errors import AppError, raise_tool_error
from ..logging_config import redact
from ..rest_client import PetHospitalClient

Species = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
Status = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SortBy = Literal["id", "name", "ownerName", "species", "doctor", "disease", "status", "totalCost", "visitCount", "createdAt", "updatedAt"]


class ListPetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    q: str | None = None
    name: str | None = None
    owner_name: Annotated[str | None, Field(alias="ownerName")] = None
    owner_phone: Annotated[str | None, Field(alias="ownerPhone")] = None
    species: Species | None = None
    doctor: str | None = None
    disease: str | None = None
    status: Status | None = None
    min: StrictFloat | None = Field(default=None, ge=0)
    max: StrictFloat | None = Field(default=None, ge=0)
    sort_by: Annotated[SortBy | None, Field(alias="sortBy")] = None
    order: Literal["asc", "desc"] | None = None
    page: StrictInt = Field(default=1, ge=1)
    page_size: Annotated[StrictInt, Field(alias="pageSize", ge=1, le=500)] = 50

    @model_validator(mode="after")
    def validate_range(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be less than or equal to max")
        return self


class Record(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    visit_date: str | None = Field(default=None, alias="visitDate")
    doctor: str | None = None
    diagnosis: str | None = None
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] = []
    weight_kg: float | None = Field(default=None, alias="weightKg")
    temperature: float | None = None
    follow_up: str | None = Field(default=None, alias="followUp")
    charge: float | None = None
    created_at: str | None = Field(default=None, alias="createdAt")


class Charge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    item: str
    category: str
    amount: float
    doctor: str | None = None
    date: str | None = None


class PetItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    species: str
    breed: str | None = None
    gender: str | None = None
    age_months: int | None = Field(default=None, alias="ageMonths")
    color: str | None = None
    chip_no: str | None = Field(default=None, alias="chipNo")
    owner_name: str = Field(alias="ownerName")
    owner_phone: str = Field(alias="ownerPhone")
    owner_addr: str | None = Field(default=None, alias="ownerAddr")
    doctor: str
    disease: str
    status: str
    allergy: str | None = None
    note: str | None = None
    records: list[Record] = []
    charges: list[Charge] = []
    total_cost: float = Field(alias="totalCost")
    visit_count: int = Field(alias="visitCount")
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")

    @field_validator("records", "charges", mode="before")
    @classmethod
    def null_collections_to_empty(cls, value):
        return [] if value is None else value


class ListPetsData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[PetItem]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")
    total_pages: int = Field(alias="totalPages")
    total_cost: float = Field(alias="totalCost")


async def list_pets(input_data: ListPetsInput, client: PetHospitalClient, logger) -> ListPetsData:
    params = {key: value for key, value in input_data.model_dump(by_alias=True).items() if value is not None}
    try:
        data = await client.list_pets(params)
        result = ListPetsData.model_validate(data)
    except AppError as exc:
        raise_tool_error(exc)
    except Exception as exc:
        raise_tool_error(AppError("BACKEND_INVALID_RESPONSE", "宠物医院接口返回的数据不符合约定"))
    logger.info("list_pets completed", extra={"tool_name": "list_pets", "params": redact(params), "status": "success", "duration_ms": 0})
    return result