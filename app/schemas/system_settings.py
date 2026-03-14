"""Pydantic schemas for admin system settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ModelProvider = Literal["openai", "anthropic", "gemini"]
ProtocolOverride = Literal["openai_compatible", "anthropic", "gemini"]


class AdminPrimaryChatSettingsIn(BaseModel):
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=255)
    base_url: str | None = None
    api_key: str | None = None
    protocol_override: ProtocolOverride | None = None


class AdminEmbeddingSettingsIn(BaseModel):
    enabled: bool = False
    model: str | None = Field(default=None, max_length=255)
    reuse_primary_connection: bool = True
    base_url: str | None = None
    api_key: str | None = None
    protocol_override: ProtocolOverride | None = None


class AdminModelSettingsUpdateRequest(BaseModel):
    primary_chat: AdminPrimaryChatSettingsIn
    embedding: AdminEmbeddingSettingsIn


class AdminRuntimeSettingsUpdateRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class AdminPrimaryChatSettingsOut(BaseModel):
    provider: ModelProvider
    model: str
    base_url: str | None = None
    protocol_override: ProtocolOverride | None = None
    resolved_protocol: str
    api_key_value: str | None = None
    api_key_masked: str = ""
    api_key_source: str = "none"
    api_key_is_encrypted: bool = False
    source: str = "env"


class AdminEmbeddingSettingsOut(BaseModel):
    enabled: bool = False
    model: str | None = None
    reuse_primary_connection: bool = True
    base_url: str | None = None
    protocol_override: ProtocolOverride | None = None
    resolved_protocol: str = "openai_compatible"
    api_key_value: str | None = None
    api_key_masked: str = ""
    api_key_source: str = "none"
    api_key_is_encrypted: bool = False
    source: str = "env"


class AdminModelSettingsResponse(BaseModel):
    primary_chat: AdminPrimaryChatSettingsOut
    embedding: AdminEmbeddingSettingsOut
    security_mode: str = "plaintext"


class RuntimeSettingItem(BaseModel):
    key: str
    value: Any = None
    source: str = "env"


class AdminRuntimeSettingsResponse(BaseModel):
    items: list[RuntimeSettingItem] = Field(default_factory=list)
