# server.py — 사실 고정·자연 문장 강화판
# 1) setx OPENAI_API_KEY "sk-..."   2) python server.py
# 프런트 API_URL = "http://127.0.0.1:5000/api/auto-diary"

from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os, re, json

# -------------------- App / OpenAI --------------------
app = Flask(__name__)
CORS(app)

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
client = OpenAI(api_key=API_KEY)

# -------------------- 유틸: 정규식/후처리 --------------------
FILE_RE = re.compile(r"\b[\w\-]+\.(jpg|jpeg|png|webp|heic)\b", re.I)
DATE_RE = re.compile(r"\b20\d{2}\s*[-.]?\s*\d{1,2}\s*[-.]?\s*\d{1,2}\b|\b20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일\b")
META_PHRASES = [
    "사진 속", "이 사진", "이미지 속", "장면이 담겼다", "촬영되었다", "찍힌", "캡처된",
    "미상", "알 수 없", "확인되지 않", "unknown", "현재 시각"
]

def hard_filter(text: str) -> str:
    t = text or ""
    t = FILE_RE.sub("", t)
    t = DATE_RE.sub("", t)
    for p in META_PHRASES:
        t = t.replace(p, "")
    # 시작부 ‘…에서’ 제거(메타 서두 방지)
    t = re.sub(r"^\s*[^.,]{1,12}\s*에서\s*", "", t)
    # 공백 정리
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t

# -------------------- 분류/키워드 --------------------
FOOD_RE = re.compile(r"(음식|식당|카페|요리|커피|빵|케이크|차|음료)", re.I)
PLURAL_RE = re.compile(r"(사람들|여러|무리|군중)", re.I)

def decide_category(items):
    if len(items) == 1:
        s = " ".join([
            items[0].get("desc",""),
            items[0].get("place","")
        ])
        return "food_single" if FOOD_RE.search(s) else "general_single"
    return "journey_multi"

def extract_tokens(desc: str):
    # 핵심 명사성 토큰 추출(한글/숫자/기호 혼합 중 2자 이상)
    raw = re.findall(r"[가-힣A-Za-z0-9#\+]{2,}", desc or "")
    # 중복 제거, 과도한 일반어 제거
    stop = {"그리고","하지만","그러나","오늘","정말","아주","매우","너무"}
    toks = []
    for w in raw:
        if w in stop: 
            continue
        if len(toks) >= 12: 
            break
        if w not in toks:
            toks.append(w)
    return toks

# -------------------- 템플릿 지시문 --------------------
GUIDE = {
"journey_multi": (
"1) 첫 문장은 그날 여정의 시작 장면을 ‘대상+동작’으로 자연스럽게 개시. "
"‘장소에서/날짜/파일명’ 같은 서두 금지.\n"
"2) 이후 사진들을 시간순으로 연결. 이동·활동·하늘·빛·공간 변화를 중심으로.\n"
"3) 각 장소명은 입력에 있을 때만 사용. 모르면 생략.\n"
"4) 마지막 문장은 풍경·정서·시간의 흐름으로 정리.\n"
"문장 수: 5~7.")
,
"general_single": (
"1) [장면 사실]로 시작: 보이는 대상·색·빛·공간감을 자연스럽게.\n"
"2) 핵심 대상/행동 1~2문장. 입력에 없는 사실은 쓰지 말 것.\n"
"3) 짧은 맥락 또는 여운으로 마무리.\n"
"문장 수: 3~4.")
,
"food_single": (
"1) [공간/시간/분위기] 사실 제시(알면), 모르면 생략.\n"
"2) 음식은 장면의 일부로 간결히. 질감·향·온기 등 감각 단서 1개.\n"
"3) 선택/머무름의 맥락 1문장 → 여운으로 마무리.\n"
"문장 수: 3~4.")
}

RULES_BASE = (
"- 입력에 없는 사실(정확한 장소명/정시/인물 수/관계/브랜드/대화 내용) 생성 금지. 모르면 쓰지 말고 생략.\n"
"- 날짜/파일명/‘~에서 찍힌/촬영된/이미지’ 같은 메타 표현 금지.\n"
"- 첫 문장을 ‘장소에서…’로 시작하지 말 것. 대상과 동작부터 자연스럽게 시작.\n"
"- 일기는 한 단락으로. 감정 단어 직접 표기 금지. 분위기는 장면으로 암시.\n"
"- 다음 ‘필수 키워드’를 가능한 한 원형 그대로 6개 이상 포함."
)

def build_prompt(items, tone):
    category = decide_category(items)
    # [사진 요약]
    lines, all_tokens = [], []
    plural_flags = []
    for i, m in enumerate(items, 1):
        place, time, weather, desc = (m.get("place","").strip(),
                                      m.get("time","").strip(),
                                      m.get("weather","").strip(),
                                      m.get("desc","").strip())
        head = ", ".join([x for x in [place, time, weather] if x])
        dash = " — " if head and desc else ""
        line = f"{i}. {head}{dash}{desc}".strip()
        lines.append(line)
        all_tokens += extract_tokens(desc + " " + place)
        plural_flags.append(bool(PLURAL_RE.search(desc)))
    must_tokens = list(dict.fromkeys(all_tokens))[:10]
    plural_ban = (sum(plural_flags) == 0)  # 입력 전반에 복수 단서가 없으면 복수 금지

    prompt = (
        "🧭 감정 일기 자동화\n"
        "[사진 요약]\n" + "\n".join(lines) + f"\n[감정 톤] {tone or '중립'}\n\n"
        "지시문:\n" + GUIDE[category] + "\n\n"
        "규칙:\n" + RULES_BASE + "\n"
        f"- 필수 키워드: {', '.join(must_tokens) if must_tokens else '(입력 토큰 없음)'}\n"
        + ("- ‘사람들/여러/무리’ 등 복수 표현 금지. 보이는 인물은 단수로만.\n" if plural_ban else "")
        + "- 문장 수 규칙을 반드시 지키고, 자연스러운 구어체 서술로 작성."
    )
    return category, prompt

# -------------------- 생성 호출 --------------------
def generate_diary(category, prompt):
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2, top_p=0.9, max_tokens=420,
        messages=[
            {"role":"system",
             "content":"관찰 사실 기반 한국어 일기를 한 단락으로 작성한다. "
                       "발명/메타표현/날짜/파일명 금지. 첫 문장은 대상+동작으로 시작. "
                       "요구된 문장 수 준수. 마지막은 풍경·정서·시간 흐름 중 하나로 잔잔히 마무리."},
            {"role":"user","content":prompt}
        ]
    )
    text = (r.choices[0].message.content or "").strip()
    return hard_filter(text)

# -------------------- API --------------------
@app.post("/api/auto-diary")
def api_auto_diary():
    data = request.get_json(silent=True) or {}
    tone = data.get("tone") or "중립"
    items = data.get("photosSummary") or []  # [{place,time,weather,desc}, ...]

    if not items:
        return jsonify({"ok": False, "error":"no_input", "message":"직접 입력하시거나 사진을 넣어주세요."}), 400

    category, prompt = build_prompt(items, tone)
    diary = generate_diary(category, prompt)

    return jsonify({"ok": True, "body": diary, "category": category})

@app.get("/health")
def health(): return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
    # --- server.py 공통부 끝부분에 추가 ---

@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp

# 모든 경로의 OPTIONS 즉시 허용
@app.route("/api/auto-diary", methods=["OPTIONS"])
def _auto_diary_preflight():
    return ("", 200)