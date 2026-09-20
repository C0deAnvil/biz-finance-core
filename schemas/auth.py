from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    device_name: str | None = Field(default=None, max_length=100)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class SelfProfileUpdate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=100)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)


class UserProfile(BaseModel):
    id: int
    username: str
    full_name: str
    role_code: str
    role_name: str
    department_id: int | None = None
    department_name: str | None
    department_path: str | None = None
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    data_scope: str | None = None
    is_admin: bool
    navigation: list[dict]


class LoginResponse(BaseModel):
    expires_at: datetime
    refresh_expires_at: datetime | None = None
    user: UserProfile
    auth_mode: str = "cookie"
