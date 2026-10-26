from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=1, max_length=100)
    department_id: int
    role_id: int
    job_title: str | None = Field(default=None, max_length=100)
    data_scope: str = Field(default="self", pattern="^(self|team|sub_department|department|all)$")
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=120)
    inherit_from_user_id: int | None = None


class UserUpdate(BaseModel):
    full_name: str = Field(..., max_length=100)
    department_id: int
    role_id: int
    job_title: str | None = Field(default=None, max_length=100)
    data_scope: str = Field(default="self", pattern="^(self|team|sub_department|department|all)$")
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=120)
    inherit_from_user_id: int | None = None


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=100)


class UserToggleRequest(BaseModel):
    successor_user_id: int | None = None


class UserModulePermissionUpdate(BaseModel):
    route_paths: list[str] = Field(default_factory=list)


class OrgUnitCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    parent_id: int | None = None
    unit_type: str = Field(default="department", pattern="^(department|sub_department|team)$")
    sort_order: int = 0
    is_active: bool = True
    warehouse_scope: str | None = Field(default=None, pattern="^(finished|raw)?$")


class OrgUnitUpdate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    parent_id: int | None = None
    unit_type: str = Field(default="department", pattern="^(department|sub_department|team)$")
    sort_order: int = 0
    is_active: bool = True
    warehouse_scope: str | None = Field(default=None, pattern="^(finished|raw)?$")
