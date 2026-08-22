from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvironmentRequest(StrictModel):
    environment: Literal["DEMO", "LIVE"]


class ModeRequest(StrictModel):
    mode: Literal["STOPPED", "DRY_RUN", "MANUAL_APPROVAL", "AUTO"]


class ConfirmationRequest(StrictModel):
    confirmation: str = Field(min_length=1, max_length=80)


class ApprovalRequest(StrictModel):
    confirmation: str = Field(min_length=1, max_length=80)


class BacktestRequest(StrictModel):
    symbol: str = Field(min_length=3, max_length=30)
    days: Literal[7, 30, 90] = 7
    walk_forward: bool = False


class RuntimeSettingsRequest(StrictModel):
    scan_interval_seconds: float = Field(ge=5, le=3600)
