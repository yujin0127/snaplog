from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from routes import auth, diary
import os

app = FastAPI()

# 🔹 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# 🔹 정적 파일 (HTML 등) 경로 등록
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/files", StaticFiles(directory="."), name="files")

# 🔹 기본 페이지를 login.html로 연결
@app.get("/")
def read_root():
    login_path = os.path.join("static", "login.html")
    return FileResponse(login_path)

# 🔹 회원가입 페이지 접근 가능
@app.get("/signup")
def read_signup():
    signup_path = os.path.join("static", "signup.html")
    return FileResponse(signup_path)

@app.get("/diary")
def read_diary():
    diary_path = os.path.join(os.path.dirname(__file__), "..", "Snaplog_test4.html")
    return FileResponse(diary_path)


# 🔹 라우터 등록
app.include_router(auth.router)
app.include_router(diary.router)
