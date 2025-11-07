# server.py — OpenAI API로 복구, 1인칭 일기, 10장, CORS+HTML 제공
# 1) pip install openai
# 2) setx OPENAI_API_KEY "sk-..."  (새 터미널)
# 3) python server.py
# 4) http://127.0.0.1:5000

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI
import os, traceback, re, time

app = Flask(__name__)
CORS(app)

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")

client = OpenAI(api_key=API_KEY)

MAX_IMAGES = 10

# --------- 정리 유틸 ---------
FILE_RE = re.compile(r"\b[\w\-]+\.(jpg|jpeg|png|webp|heic)\b", re.I)
DATE_RE = re.compile(r"\b20\d{2}\s*[-.]?\s*\d{1,2}\s*[-.]?\s*\d{1,2}\b|\b20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일\b")
BAN_WORDS = [
    "사진","이미지","촬영","캡처","찍힌","장면이 담겼다",
    "미상","확인되지 않음","unknown","현재 시각",
    "듯하다","감돈다","어우러져","마치","은은하다","여운이 남는다",
    "남성","여성","사람들","군중","여럿","1명","2명","3명"
]

def clean_line(s: str) -> str:
    if not s: return ""
    t = re.sub(r"\s+", " ", s).strip()
    t = FILE_RE.sub("", t)
    t = DATE_RE.sub("", t)
    for w in BAN_WORDS:
        t = t.replace(w, "")
    return t.strip()

# --------- 카테고리 ---------
import re as _re
FOOD_RE = _re.compile(r"(음식|식당|카페|요리|coffee|cafe|cake|bread|meal|lunch|dinner|brunch|dessert|커피|빵|케이크|디저트)", _re.I)
def decide_category(desc_list):
    if len(desc_list) == 1:
        return "food_single" if FOOD_RE.search(desc_list[0]) else "general_single"
    return "journey_multi"

# --------- OpenAI Vision으로 이미지 기반 일기 생성 ---------
def generate_diary_from_images(images, tone):
    """OpenAI GPT-4o-mini Vision으로 이미지 기반 일기 생성"""
    try:
        print(f"🔍 이미지 {len(images)}장 분석 중...")
        
        num_images = len(images)
        
        # 메시지 구성
        content = [
            {"type": "text", "text": f"""이 {'사진들' if num_images > 1 else '사진'}을 보고 한국어 1인칭 일기를 작성해주세요.

**지시문:**
- {'여러 장이므로 시간 흐름과 장소 이동을 따라 5~7문장' if num_images > 1 else '한 장이므로 보이는 사실 + 나의 행동 + 감각을 포함해 3~4문장'}
- 첫 문장은 '나는 …했다' 또는 '…하고 있다'로 시작
- 파일명, 날짜, "사진", "이미지" 같은 메타 표현 절대 금지
- 성별, 인원수 추정 금지
- 3인칭 금지, 한 단락으로 작성
- 감정 톤: {tone or '중립'} (은은하게 암시)

**예시:**
"나는 오후의 거리를 천천히 걸었다. 햇빛이 건물 사이로 비스듬히 들어왔고, 그림자가 길게 늘어났다. 공기는 따뜻했지만 바람이 불 때마다 시원함이 스쳤다."

일기를 작성하세요:"""}
        ]
        
        # 이미지 추가
        for img_data in images[:MAX_IMAGES]:
            img_url = img_data if img_data.startswith("data:image") else f"data:image/jpeg;base64,{img_data}"
            content.append({"type": "image_url", "image_url": {"url": img_url}})
        
        # OpenAI API 호출
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "관찰 사실+감각 기반 한국어 1인칭 일기. 메타표현·날짜·파일명·성별/인원 금지. 문장 수 준수. 한 단락."},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=600
        )
        
        text = (response.choices[0].message.content or "").strip()
        print(f"✅ 일기 생성 완료: {text[:50]}...")
        return clean_line(text)
        
    except Exception as e:
        print(f"OpenAI API error: {e}")
        traceback.print_exc()
        raise

# --------- Fallback 일기 생성 (API 없이) ---------
def simple_fallback_diary(category="general_single"):
    """API 호출 없이 간단한 일기 생성"""
    templates = {
        "general_single": [
            "오늘 하루를 천천히 되돌아본다. 작은 순간들이 모여 하나의 풍경이 되었다. 기억 한 조각을 이곳에 남긴다.",
            "시간이 조용히 흘렀다. 특별할 것 없던 순간들이 쌓여 오늘이 되었다. 그 평범함이 소중하다.",
            "오늘도 하루가 지나갔다. 무언가를 하고, 무언가를 보고, 무언가를 느꼈다. 그것만으로 충분하다."
        ],
        "journey_multi": [
            "아침부터 저녁까지 천천히 걸었다. 공간이 바뀌고 빛이 바뀌는 동안 나는 그저 그 흐름에 몸을 맡겼다. 돌아보니 하루가 지나 있었다.",
            "여러 곳을 거쳐 왔다. 각각의 장소에서 잠시 머물렀고, 그때마다 다른 공기를 마셨다. 하루의 궤적이 발 아래 쌓였다."
        ]
    }
    import random
    cat_templates = templates.get(category, templates["general_single"])
    return random.choice(cat_templates)

# --------- 기존 프롬프트 방식(라인 → 일기) ---------
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
"- 날짜/파일명/메타표현(사진·이미지·촬영·캡처) 금지.\n"
"- 성별·인원수 언급 금지. 관계 중심 표현만.\n"
"- 입력에 없는 사실(정확한 장소명/정시/브랜드/대화) 생성 금지.\n"
"- 톤은 암시로만. 한 단락."
)

def build_prompt_from_lines(lines, tone):
    category = decide_category(lines)
    obs_block = "\n".join(f"- {l}" for l in lines)
    prompt = (
        f"[사진 관찰]\n{obs_block}\n\n"
        f"[감정 톤] {tone or '중립'}\n\n"
        f"[지시문]\n{GUIDE[category]}\n\n"
        f"[규칙]\n{RULES}\n"
        "- 첫 문장은 ‘나는 …했다/하고 있다’로 시작.\n"
        "- 한 단락으로 출력."
    )
    return category, prompt

def generate_diary_from_lines(category, prompt):
    """OpenAI로 텍스트 기반 일기 생성"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "관찰 사실+감각 기반 한국어 1인칭 일기. 메타표현·날짜·파일명·성별/인원 금지. 문장 수 준수. 한 단락."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        text = (response.choices[0].message.content or "").strip()
        return clean_line(text)
    except Exception as e:
        print(f"OpenAI text generation error: {e}")
        traceback.print_exc()
        raise

# --------- HTML ---------
@app.get("/")
def index():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Snaplog_test3.html")
    if not os.path.exists(html_path):
        return f"Error: Snaplog_test3.html 없음: {html_path}", 404
    return send_file(html_path)

# --------- API ---------
@app.post("/api/auto-diary")
def api_auto_diary():
    try:
        data = request.get_json(silent=True) or {}
        tone = data.get("tone") or "중립"
        images = (data.get("images") or [])[:MAX_IMAGES]
        photos = data.get("photosSummary") or []

        # 수신 로그 (더 자세히)
        print("[auto-diary] Received data keys:", list(data.keys()))
        print("[auto-diary] images count:", len(images), "photosSummary count:", len(photos))
        if images:
            print("  first image type:", type(images[0]))
            print("  first image head:", (images[0][:60] if isinstance(images[0], str) else "NOT_STRING"))
        else:
            print("  WARNING: images is empty!")

        # A) 이미지 우선: 직접 일기 생성 (API 호출 1회로 단축)
        if images:
            try:
                diary = generate_diary_from_images(images, tone)
                if diary:
                    cat = "journey_multi" if len(images) > 1 else "general_single"
                    return jsonify({"ok": True, "body": diary, "category": cat, "used": "openai-vision", "observations": []})
                else:
                    # 빈 응답 → 폴백
                    print("⚠️ OpenAI 응답 없음, 폴백 일기 생성")
                    cat = "journey_multi" if len(images) > 1 else "general_single"
                    fallback_diary = simple_fallback_diary(cat)
                    return jsonify({
                        "ok": True,
                        "body": fallback_diary + "\n\n💡 사진 분석 중 문제가 발생했습니다. 간단한 일기를 생성했습니다.",
                        "category": cat,
                        "used": "fallback-safety"
                    })
            except Exception as e:
                error_msg = str(e)
                cat = "journey_multi" if len(images) > 1 else "general_single"
                
                # finish_reason 2 = SAFETY 필터
                if "finish_reason" in error_msg.lower() or "safety" in error_msg.lower():
                    print(f"⚠️ 안전 필터 감지, 폴백 일기 생성")
                    fallback_diary = simple_fallback_diary(cat)
                    return jsonify({
                        "ok": True,
                        "body": fallback_diary + "\n\n💡 사진이 AI 필터에 걸렸습니다. 간단한 일기를 생성했습니다.",
                        "category": cat,
                        "used": "fallback-safety"
                    })
                
                # Rate limit 에러
                if "rate_limit" in error_msg.lower() or "429" in error_msg:
                    print(f"⚠️ Rate limit, 폴백 일기 생성")
                    fallback_diary = simple_fallback_diary(cat)
                    return jsonify({
                        "ok": True,
                        "body": fallback_diary + "\n\n💡 AI가 바쁩니다. 간단한 일기를 생성했습니다.\n20초 후 다시 시도하면 사진 기반 일기를 받을 수 있습니다.",
                        "category": cat,
                        "used": "fallback-rate-limit"
                    })
                
                # 기타 에러
                print(f"Vision API error: {error_msg}")
                traceback.print_exc()
                # 폴백 일기 생성
                fallback_diary = simple_fallback_diary(cat)
                return jsonify({
                    "ok": True,
                    "body": fallback_diary + "\n\n💡 AI 생성 중 오류가 발생했습니다. 간단한 일기를 생성했습니다.",
                    "category": cat,
                    "used": "fallback-error"
                })

        # B) 이미지가 없을 때: photosSummary로 강제 라인 생성
        lines = []
        for p in photos:
            base = " ".join([
                (p.get("place") or "").strip(),
                (p.get("time") or "").strip(),
                (p.get("weather") or "").strip(),
                (p.get("desc") or "").strip()
            ]).strip()
            base = clean_line(base)
            if base:
                lines.append(base)

        # 라인이 비어도 최소 라인 강제(정오/오전/오후/저녁 중 하나라도 넣음)
        if not lines and photos:
            for p in photos:
                t = (p.get("time") or "").strip()
                t = re.sub(r"\b20\d{2}[\-\.]?\d{1,2}[\-\.]?\d{1,2}\b", "", t).strip()  # 날짜 제거
                t = t or "오후"
                lines.append(f"{t}에 주변을 천천히 둘러봤다.")

        if lines:
            category, prompt = build_prompt_from_lines(lines, tone)
            diary = generate_diary_from_lines(category, prompt)
            return jsonify({"ok": True, "body": diary, "category": category, "used": "summary-lines", "observations": lines})

        # 진짜 입력 없음
        return jsonify({"ok": False, "error":"no_input", "message":"사진을 넣거나 최소 텍스트 단서를 제공하세요."}), 400

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/health")
def health():
    return {"ok": True}

# --------- CORS ---------
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

# --------- 실행 ---------
if __name__ == "__main__":
    print("\n===========================================")
    print("서버 시작 → http://127.0.0.1:5000")
    print("===========================================\n")
    app.run(host="0.0.0.0", port=5000, debug=False)