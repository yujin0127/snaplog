# server.py — Vision 우선, 1인칭 일기, 10장, 금지어 억제, CORS+HTML 제공, 관찰 로그 반환
# 1) setx OPENAI_API_KEY "sk-..."  (새 터미널 열기)
# 2) python server.py
# 3) 브라우저: http://127.0.0.1:5000

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI
import os, traceback, re, json

app = Flask(__name__)
CORS(app)

# ---------------- OpenAI ----------------
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
client = OpenAI(api_key=API_KEY)

MAX_IMAGES = 10

# ---------------- 유틸/정규화 ----------------
FILE_RE = re.compile(r"\b[\w\-]+\.(jpg|jpeg|png|webp|heic)\b", re.I)
DATE_RE = re.compile(r"\b20\d{2}\s*[-.]?\s*\d{1,2}\s*[-.]?\s*\d{1,2}\b|\b20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일\b")
BAN_WORDS = [
    "사진", "이미지", "촬영", "캡처", "찍힌", "장면이 담겼다",
    "미상", "확인되지 않음", "unknown", "현재 시각",
    "듯하다", "감돈다", "어우러져", "마치", "은은하다", "여운이 남는다",
    "남성", "여성", "사람들", "군중", "여럿", "1명", "2명", "3명"
]

def _one_line(s: str, max_len: int = 140) -> str:
    """한 줄로 압축하고 메타/날짜/파일명 제거."""
    if not s:
        return ""
    t = re.sub(r"\s+", " ", s).strip()
    t = FILE_RE.sub("", t)
    t = DATE_RE.sub("", t)
    for w in BAN_WORDS:
        t = t.replace(w, "")
    return t[:max_len].strip()

# ---------------- 카테고리 ----------------
FOOD_RE = re.compile(r"(음식|식당|카페|요리|coffee|cafe|cake|bread|meal|lunch|dinner|brunch|dessert|커피|빵|케이크|디저트)", re.I)

def decide_category(items):
    if len(items) == 1:
        return "food_single" if FOOD_RE.search(items[0].get("desc","")) else "general_single"
    return "journey_multi"

# ---------------- Vision: 이미지 → 관찰 한 줄 ----------------
def vision_images_to_items(images):
    """각 이미지를 1회씩 호출해 '사실 기반 한 줄' 설명으로 리스트 생성."""
    items = []
    for du in images[:MAX_IMAGES]:
        try:
            img_url = du if du.startswith("data:image") else f"data:image/jpeg;base64,{du}"
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.0,
                max_tokens=300,
                messages=[
                    {
                        "role":"system",
                        "content":(
                            "사진에 보이는 사실만 한국어로 간결히 기술한다. "
                            "파일명·날짜·메타(‘사진, 이미지, 촬영, ~에서 찍힌’) 금지. "
                            "인원수·성별 추정 금지. 불확실하면 생략. 한 문장으로."
                        )
                    },
                    {
                        "role":"user",
                        "content":[
                            {"type":"text","text":
                             "배경/대상/색/빛/활동 중 2~3개를 고르고 한 문장으로 요약. "
                             "예) 핑크뮬리 풀밭과 검은 지붕 카페, 노을 빛"},
                            {"type":"image_url","image_url":{"url": img_url}}
                        ]
                    }
                ]
            )
            desc = _one_line(r.choices[0].message.content)
            if not desc:
                desc = "짧은 장면 설명"
            items.append({"desc": desc})
        except Exception as e:
            print("Vision fail:", e)
    return items

# ---------------- 일기 프롬프트 ----------------
GUIDE = {
"journey_multi": (
"1) 1인칭으로 시작. 장소명은 보일 때만 사용.\n"
"2) 사진들을 시간순으로 연결. 이동·활동·빛·공간 변화를 중심으로.\n"
"3) 마지막은 풍경/정리/시간의 흐름으로 닫기.\n"
"문장 수: 5~7."
),
"general_single": (
"1) 보이는 사실 2가지 이상(대상·색·빛·공간감)으로 시작.\n"
"2) 내가 한 행동 1개 포함.\n"
"3) 감각 단서 1개 포함(바람/소리/향/빛 등).\n"
"문장 수: 3~4."
),
"food_single": (
"1) 공간/분위기 + 음식은 장면의 일부로 간결히.\n"
"2) 질감·향·온기 중 1개 감각 포함.\n"
"3) 선택·머무름의 맥락 1문장 → 여운으로 마무리.\n"
"문장 수: 3~4."
)
}

RULES = (
"- 1인칭 일기체. 3인칭 금지.\n"
"- 날짜/파일명/메타표현(‘사진·이미지·촬영·캡처/~에서 찍힌’) 금지.\n"
"- 성별·인원수 언급 금지. 관계 중심 표현만.\n"
"- 입력에 없는 사실(정확한 장소명/정시/브랜드/대화) 생성 금지. 모르면 생략.\n"
"- 금지어 예: 미상, 확인되지 않음, 듯하다, 감돈다, 어우러져, 마치.\n"
"- 톤은 암시로만. 한 단락으로."
)

def build_prompt(items, tone):
    category = decide_category(items)
    lines = "\n".join([f"- {it.get('desc','')}" for it in items])
    prompt = (
        f"[사진 관찰]\n{lines}\n\n"
        f"[감정 톤] {tone or '중립'}\n\n"
        f"[지시문]\n{GUIDE[category]}\n\n"
        f"[규칙]\n{RULES}\n"
        "- 첫 문장은 ‘나는 …했다/하고 있다’로 시작.\n"
        "- 한 단락으로 출력."
    )
    return category, prompt

# ---------------- 일기 생성 ----------------
def generate_diary(category, prompt):
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        top_p=0.9,
        max_tokens=420,
        messages=[
            {"role":"system",
             "content":"관찰 사실+감각 기반의 한국어 1인칭 일기. "
                       "첫 문장은 ‘나는 …했다/하고 있다’로 시작. "
                       "추측/비유 과잉 금지. 메타표현·날짜·파일명·성별/인원 금지. "
                       "문장 수 규칙 준수. 한 단락으로."},
            {"role":"user","content": prompt}
        ]
    )
    text = (r.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("empty generation")
    # 최종 금지어/메타 클린업
    text = _one_line(text, max_len=2000)
    return text

# ---------------- HTML 제공 ----------------
@app.get("/")
def index():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Snaplog_test3.html")
    if not os.path.exists(html_path):
        return f"Error: Snaplog_test3.html을 찾을 수 없습니다. 경로: {html_path}", 404
    return send_file(html_path)

# ---------------- API ----------------
@app.post("/api/auto-diary")
def api_auto_diary():
    try:
        data = request.get_json(silent=True) or {}
        tone = data.get("tone") or "중립"
        images = (data.get("images") or [])[:MAX_IMAGES]
        photos = data.get("photosSummary") or []

        print("[auto-diary] images:", len(images), "photosSummary:", len(photos))

        items = vision_images_to_items(images) if images else []
        if not items and photos:
            # 비전 실패 시 폴백: 요약 desc만 사용
            for p in photos:
                desc = _one_line(p.get("desc",""))
                if desc: items.append({"desc": desc})
        if not items:
            return jsonify({"ok": False, "error":"no_input", "message":"직접 입력하시거나 사진을 넣어주세요."}), 400

        category, prompt = build_prompt(items, tone)
        diary = generate_diary(category, prompt)

        return jsonify({
            "ok": True,
            "body": diary,
            "category": category,
            "used": "vision" if images else "summary",
            "observations": [it.get("desc","") for it in items]  # 디버깅/품질 확인용
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/health")
def health():
    return {"ok": True}

# ---------------- CORS/Preflight ----------------
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp

@app.route("/api/auto-diary", methods=["OPTIONS"])
def _auto_diary_preflight():
    return ("", 200)

# ---------------- 실행 ----------------
if __name__ == "__main__":
    print("\n===========================================")
    print("서버 시작 → http://127.0.0.1:5000")
    print("===========================================\n")
    app.run(host="0.0.0.0", port=5000, debug=False)