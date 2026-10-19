"""Тела запросов входа."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    login: str = Field(max_length=100)
    password: str = Field(max_length=200)
