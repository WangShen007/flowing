from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.auth import (
    ADMIN_ACCOUNT_DEPENDENCY,
    CURRENT_ACCOUNT_DEPENDENCY,
    AccountInfo,
)
from app.core.model_config import ModelConfigRequest, apply_model_config, current_model_config, presets_payload

router = APIRouter()


@router.get("/models/config")
async def get_model_config(_account: AccountInfo = CURRENT_ACCOUNT_DEPENDENCY) -> dict:
    return {
        "code": 0,
        "data": {
            "current": current_model_config(),
            "presets": presets_payload(),
        },
    }


@router.post("/models/config")
async def set_model_config(
    request: ModelConfigRequest,
    _account: AccountInfo = ADMIN_ACCOUNT_DEPENDENCY,
) -> dict:
    return {
        "code": 0,
        "data": {
            "current": apply_model_config(request),
            "presets": presets_payload(),
        },
    }
