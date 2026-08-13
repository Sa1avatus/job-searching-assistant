"""FastAPI dependencies for user context and ownership validation.

Provides get_current_user and get_user_from_path dependencies that
ensure user existence and pass user context to service calls.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.storage.database import session_scope
from app.storage.tables import UserRow


def get_user_from_path(
    user_id: str = Path(...),
    session: Session = Depends(session_scope)  # noqa: B008,
) -> UserRow:
    """Validate user exists and return them. Used as FastAPI dependency.

    Usage:
        @app.get("/v1/users/{user_id}/facts")
        def list_facts(user: Annotated[UserRow, Depends(get_user_from_path)]):
            # user is guaranteed to exist
            ...
    """
    user = session.get(UserRow, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


def get_user_id_from_path(
    user_id: str = Path(...),
    session: Session = Depends(session_scope)  # noqa: B008,
) -> str:
    """Validate user exists and return their ID. Lighter version.

    Usage:
        @app.get("/v1/users/{user_id}/facts")
        def list_facts(uid: Annotated[str, Depends(get_user_id_from_path)]):
            # uid is guaranteed to be a valid user
            ...
    """
    user = session.get(UserRow, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user_id
