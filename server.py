# server.py — Vision 우선, 1인칭 일기, 10장, 에러 가시화, CORS
# 1) setx OPENAI_API_KEY "sk-..."  후 새 터미널
# 2) python server.py
# 3) 프런트 API_URL = "http://127.0.0.1:5000/api/auto-diary"
#    payload: { tone, images:[dataURL...], photosSummary:[{place,time,weather,desc}] }

from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os, traceback, re, json

app = Flask(__name__)
CORS(app)

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
client = OpenAI(api_key=API_KEY)

MAX_IMAGES = 10

# ---------------- Vision: 이미지 → 관찰 설명 ----------------
def vision_images_to_items(images):
    items = []
    for du in images[:MAX_IMAGES]:
        try:
            # dataURL 그대로 전달 가능. base64만 있어도 됨.
            if du.startswith("data:image"):
                img_url = du
            else:
                img_url = f"data:image/jpeg;base64,{du}"
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.0,
                max_tokens=400,
                messages=[
                    {"role":"system","content":"사진에서 보이는 요소를 구체적으로, 사실 위주로 한국어로 묘사하라. 불확실하면 생략."},
                    {"role":"user","content":[
                        {"type":"text","text":"구성요소(배경/대상/색/빛), 활동/상호작용, 공간감, 눈에 띄는 물체를 간결한 문장으로 설명해. 메타표현 금지."},
                        {"type":"image_url","image_url":{"url": img_url}}
                    ]}
                ]
            )
            desc = (r.choices[0].message.content or "").strip()
            if not desc:
                desc = "짧은 장면 설명"
            items.append({"desc": desc})
        except Exception as e:
            print("Vision fail:", e)
    return items

# ---------------- Prompt 구성 ----------------
FOOD_RE = re.compile(r"(음식|식당|카페|요리|coffee|cafe|cake|bread|meal|lunch|dinner)", re.I)
def decide_category(items):
    if len(items) == 1:
        s = items[0].get("desc","")
        return "food_single" if FOOD_RE.search(s) else "general_single"
    return "journey_multi"

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
"- 날짜/파일명/‘사진·이미지·촬영·캡처/~에서 찍힌’ 메타 표현 금지.\n"
"- 성별·인원수 언급 금지. 관계 중심 표현만.\n"
"- 입력에 없는 사실(정확한 장소명/정시/브랜드/대화 내용) 생성 금지.\n"
"- 분위기(톤)는 암시로만."
)

def build_prompt(items, tone):
    category = decide_category(items)
    lines = "\n".join([f"- {it.get('desc','')}" for it in items])
    prompt = (
        f"[사진 관찰]\n{lines}\n\n"
        f"[감정 톤] {tone or '중립'}\n\n"
        f"[지시문]\n{GUIDE[category]}\n\n"
        f"[규칙]\n{RULES}\n"
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
             "content":"관찰 사실+감각 기반의 한국어 1인칭 일기를 쓰는 작가. 메타표현·날짜·파일명·성별/인원 금지. 문장 수 규칙 준수."},
            {"role":"user","content": prompt}
        ]
    )
    text = (r.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("empty generation")
    return text

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
            for p in photos:
                items.append({
                    "desc": (p.get("desc") or "").strip()
                })

        if not items:
            return jsonify({"ok": False, "error":"no_input", "message":"직접 입력하시거나 사진을 넣어주세요."}), 400

        category, prompt = build_prompt(items, tone)
        diary = generate_diary(category, prompt)
        return jsonify({"ok": True, "body": diary, "category": category, "used": "vision" if images else "summary"})
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
    return resp

@app.route("/api/auto-diary", methods=["OPTIONS"])
def _auto_diary_preflight():
    return ("", 200)

# ---------------- 실행 ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)