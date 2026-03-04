"""Pydantic schemas for admin system settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ModelType = Literal["chat", "embedding", "image", "video"]
AdapterType = Literal["openai_compatible", "anthropic", "gemini"]


class AdminModelDefinitionIn(BaseModel):
    model_name: str = Field(min_length=1, max_length=255)
    model_type: ModelType = "chat"
    is_default: bool = False
    is_enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminModelProviderIn(BaseModel):
    provider_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    adapter_type: AdapterType = "openai_compatible"
    base_url: str | None = None
    api_key: str | None = None
    is_enabled: bool = True
    priority: int = 100
    models: list[AdminModelDefinitionIn] = Field(default_factory=list)


class AdminModelSettingsUpdateRequest(BaseModel):
    providers: list[AdminModelProviderIn] = Field(default_factory=list)


class AdminRuntimeSettingsUpdateRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class AdminModelDefinitionOut(BaseModel):
    model_name: str
    model_type: ModelType
    is_default: bool
    is_enabled: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = "db"


class AdminModelProviderOut(BaseModel):
    provider_key: str
    display_name: str
    adapter_type: AdapterType
    base_url: str | None = None
    api_key_value: str | None = None
    api_key_masked: str = ""
    api_key_source: str = "none"
    api_key_is_encrypted: bool = False
    is_enabled: bool = True
    priority: int = 100
    models: list[AdminModelDefinitionOut] = Field(default_factory=list)
    source: str = "db"


class AdminModelSettingsResponse(BaseModel):
    providers: list[AdminModelProviderOut] = Field(default_factory=list)
    default_models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fallback_order: list[str] = Field(default_factory=list)
    security_mode: str = "plaintext"


class RuntimeSettingItem(BaseModel):
    key: str
    value: Any = None
    source: str = "env"


class AdminRuntimeSettingsResponse(BaseModel):
    items: list[RuntimeSettingItem] = Field(default_factory=list)
