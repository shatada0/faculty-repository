"""Тела запросов входа."""

from pydantic import BaseModel, Field

PASSWORD_MAX = 200  # и для CLI users: длиннее не задать — иначе с таким паролем не войти


class LoginRequest(BaseModel):
    login: str = Field(max_length=100)
    password: str = Field(max_length=PASSWORD_MAX)
