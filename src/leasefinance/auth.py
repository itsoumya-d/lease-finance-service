from __future__ import annotations

import hmac
import json
import os
from typing import Callable

from fastapi import Header, HTTPException

ROLE_LEVEL = {"viewer": 1, "officer": 2, "admin": 3}


def load_api_keys() -> dict[str, str]:
    raw = os.getenv("API_KEYS", '{"dev-admin":"admin"}')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API_KEYS must be a JSON object of key -> role") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeError("API_KEYS must be a non-empty JSON object")
    for role in parsed.values():
        if role not in ROLE_LEVEL:
            raise RuntimeError(f"unsupported role: {role}")
    return {str(key): str(role) for key, role in parsed.items()}


def require_role(required: str, api_keys: dict[str, str]) -> Callable:
    required_level = ROLE_LEVEL[required]

    def dependency(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
        if not x_api_key:
            raise HTTPException(status_code=401, detail="missing API key")
        matched = None
        for key in api_keys:
            if hmac.compare_digest(x_api_key, key):
                matched = key
                break
        if matched is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        if ROLE_LEVEL[api_keys[matched]] < required_level:
            raise HTTPException(status_code=403, detail=f"{required} role required")
        return matched

    return dependency
