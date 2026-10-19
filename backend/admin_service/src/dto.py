"""PersonIn — тело формы человека: его разбирает роутер и принимает сервис, поэтому отдельным модулем."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["staff", "student", "graduate", "external"]
Short = str | None


class PersonIn(BaseModel):
    """Человек из формы. Поля, которых нет в теле, не меняются."""

    model_config = ConfigDict(extra="ignore")  # карточка может прислать id, shortName, works — их ставит сервер
    kind: Kind | None = None
    fullName: str | None = Field(None, max_length=300)
    department: Short = Field(None, max_length=300)
    position: Short = Field(None, max_length=300)
    academicDegree: Short = Field(None, max_length=300)
    academicTitle: Short = Field(None, max_length=300)
    orcid: Short = Field(None, max_length=19)
    affiliation: Short = Field(None, max_length=500)
    aliases: list[Annotated[str, Field(max_length=300)]] | None = Field(None, max_length=50)
