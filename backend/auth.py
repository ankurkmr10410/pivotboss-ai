from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def get_api_key(api_key: Optional[str] = Depends(api_key_header)) -> str:
    expected = os.getenv("PIVOTBOSS_API_KEY", "").strip()
    if not expected:
        return ""
    if api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": f"{API_KEY_NAME}"},
        )
    return api_key
