from fastapi import APIRouter
from models import Diary
from database import diary_container
from datetime import datetime

router = APIRouter()

@router.post("/diary")
def create_diary(diary: Diary):
    diary_container.upsert_item(diary.dict())
    return {"message": "저장 완료!"}

@router.get("/diaries")
def get_diaries():
    items = list(diary_container.read_all_items())
    return items
