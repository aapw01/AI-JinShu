"""Auth request/response schemas."""
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """Register请求体模型。"""
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    """Login请求体模型。"""
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class AuthTokenResponse(BaseModel):
    """认证Token响应体模型。"""
    access_token: str
    token_type: str = "bearer"
    user: dict


class VerifyEmailRequest(BaseModel):
    """验证Email请求体模型。"""
    token: str = Field(min_length=16, max_length=256)


class VerifyEmailRequestSend(BaseModel):
    """验证EmailRequestSend。"""
    email: str = Field(min_length=5, max_length=255)


class ForgotPasswordRequest(BaseModel):
    """Forgot密码请求体模型。"""
    email: str = Field(min_length=5, max_length=255)


class ResetPasswordRequest(BaseModel):
    """重置密码请求体模型。"""
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=10, max_length=128)


class ChangePasswordRequest(BaseModel):
    """Change密码请求体模型。"""
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
