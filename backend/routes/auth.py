from fastapi import APIRouter, HTTPException, Body
from models import User
from database import user_container
import uuid
from passlib.context import CryptContext
import smtplib
from email.message import EmailMessage
import os
from dotenv import load_dotenv

load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter()

# 🔹 이메일 발송
def send_verification_email(to_email: str, code: str):
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")

    msg = EmailMessage()
    msg["Subject"] = "Snaplog 이메일 인증"
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.set_content(f"Snaplog 이메일 인증 코드: {code}")

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

# 🔹 회원가입
@router.post("/signup")
def signup(user: User):
    print(f"type(user.password) = {type(user.password)}")
    print(f"password value = '{user.password}'")
    # username 중복 체크
    existing_username = list(user_container.query_items(
        query="SELECT * FROM c WHERE c.username=@username",
        parameters=[{"name":"@username", "value":user.username}],
        enable_cross_partition_query=True
    ))
    if existing_username:
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    # email 중복 체크
    existing_email = list(user_container.query_items(
        query="SELECT * FROM c WHERE c.email=@email",
        parameters=[{"name":"@email", "value":user.email}],
        enable_cross_partition_query=True
    ))
    if existing_email:
        raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")

    user.id = str(uuid.uuid4())
    user.password = pwd_context.hash(user.password)
    user.is_verified = False
    verification_code = str(uuid.uuid4())[:6]
    user.verification_code = verification_code

    user_container.upsert_item(user.dict())
    send_verification_email(user.email, verification_code)

    return {"message": "회원가입 성공! 이메일 인증 코드를 발송했습니다."}

# 🔹 이메일 인증
@router.post("/verify_email")
def verify_email(email: str = Body(...), code: str = Body(...)):
    users = list(user_container.query_items(
        query="SELECT * FROM c WHERE c.email=@email",
        parameters=[{"name":"@email","value":email}],
        enable_cross_partition_query=True
    ))
    if not users:
        raise HTTPException(status_code=400, detail="이메일 없음")

    user = users[0]
    if user.get("verification_code") != code:
        raise HTTPException(status_code=400, detail="인증 코드 틀림")

    user["is_verified"] = True
    user_container.upsert_item(user)
    return {"message": "이메일 인증 완료!"}

# 🔹 로그인
@router.post("/login")
def login(username: str = Body(...), password: str = Body(...)):
    users = list(user_container.query_items(
        query="SELECT * FROM c WHERE c.username=@username",
        parameters=[{"name":"@username","value":username}],
        enable_cross_partition_query=True
    ))
    if not users:
        raise HTTPException(status_code=400, detail="아이디 없음")

    user = users[0]
    if not pwd_context.verify(password, user["password"]):
        raise HTTPException(status_code=400, detail="비밀번호 틀림")

    if not user.get("is_verified"):
        raise HTTPException(status_code=400, detail="이메일 인증 필요")

    return {"message": "로그인 성공!", "userId": user["id"]}
 