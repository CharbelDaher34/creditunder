from enum import Enum

from fastapi import Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Select

from creditunder.db.models import EdwStaging


class UserRole(str, Enum):
    VALIDATOR = "VALIDATOR"
    SUPERVISOR = "SUPERVISOR"


class UserContext(BaseModel):
    user_id: str
    role: UserRole


async def auth_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
) -> UserContext:
    if not x_user_id or not x_user_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id or X-User-Role header.",
        )
    try:
        role = UserRole(x_user_role.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown role: {x_user_role}. Expected VALIDATOR or SUPERVISOR.",
        )
    return UserContext(user_id=x_user_id, role=role)


def scope_query(stmt: Select, user: UserContext) -> Select:
    """Apply role-based row scoping to any EdwStaging SELECT.

    Fail-closed: an unknown role yields a query that returns nothing, rather
    than silently widening to one of the known scopes.
    """
    if user.role == UserRole.VALIDATOR:
        return stmt.where(EdwStaging.validator_id == user.user_id)
    if user.role == UserRole.SUPERVISOR:
        return stmt.where(EdwStaging.supervisor_id == user.user_id)
    return stmt.where(False)
