from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class User(BaseModel):
    username: str
    email: EmailStr
    password: str
    id: Optional[str] = None                 # 회원 UUID
    is_verified: Optional[bool] = False      # 이메일 인증 여부
    verification_code: Optional[str] = None  # 인증 코드

class Diary(BaseModel):
    id: str
    userId: str
    title: str
    content: str
    date: datetime
