"""Тела запросов каталога."""

from pydantic import BaseModel, ConfigDict, Field


class ClientError(BaseModel):
    """Ошибка браузера: в лог со всем, что поможет найти её по trace_id запроса."""

    model_config = ConfigDict(extra="ignore")
    message: str = Field("", max_length=2000)
    stack: str | None = Field(None, max_length=10000)
    url: str | None = Field(None, max_length=2000)
    userAgent: str | None = Field(None, max_length=500)
    build: str | None = Field(None, max_length=100)
    traceId: str | None = Field(None, max_length=100)


class Part(BaseModel):
    text: str
    hit: bool


class SuggestWork(BaseModel):
    id: str
    type: str
    title: str
    year: int | None = None
    parts: list[Part] = Field(description="Название кусками; hit — совпавшее с запросом слово, его подсвечивают")


class SuggestPerson(BaseModel):
    id: str
    fullName: str


class Suggest(BaseModel):
    """Подсказки строки поиска: до 6 опубликованных работ и до 4 сотрудников."""

    works: list[SuggestWork]
    people: list[SuggestPerson]
