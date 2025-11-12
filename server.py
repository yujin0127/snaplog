"""Snaplog server – 3단계(분석→초안→보정) + 교차검증(모델 이중생성)"""

from __future__ import annotations
import os, re, json, random, traceback, time, io, base64, uuid
from threading import Lock
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI, RateLimitError
from datetime import datetime
from werkzeug.utils import secure_filename

# ---------------- Flask ----------------
app = Flask(__name__)
CORS(app)

# 원본 저장 디렉터리
UPLOAD_DIR = os.getenv("SNAPLOG_UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------- OpenAI ----------------
API_KEY = os.getenv("OPENAI_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError('OPENAI_API_KEY 환경변수가 없습니다. Windows: setx OPENAI_API_KEY "sk-..."')

client = OpenAI(api_key=API_KEY)

# 모델 설정
MODEL_VISION = "gpt-4o-mini"   # 이미지 분석
MODEL_TEXT   = "gpt-4o-mini"   # 초안 1차
ALT_TEXT_MODEL = os.getenv("OPENAI_ALT_TEXT_MODEL", "gpt-4o")  # 초안 2차(동일 프롬포트)

MAX_IMAGES   = 10
THROTTLE_SECONDS = float(os.getenv("OPENAI_THROTTLE_SECONDS", "0.5"))
MAX_WAIT_SECONDS = float(os.getenv("OPENAI_MAX_WAIT_SECONDS", "30"))
_last_call_ts = 0.0
_throttle_lock = Lock()


def throttled_chat_completion(**kwargs):
    global _last_call_ts
    backoff = THROTTLE_SECONDS
    last_error: RateLimitError | None = None
    total_wait = 0.0
    while total_wait <= MAX_WAIT_SECONDS:
        with _throttle_lock:
            wait = THROTTLE_SECONDS - (time.monotonic() - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
            total_wait += wait

        retry_secs = THROTTLE_SECONDS
        with _throttle_lock:
            try:
                resp = client.chat.completions.create(**kwargs)
                _last_call_ts = time.monotonic()
                return resp
            except RateLimitError as e:
                last_error = e
                msg = str(e) or ""
                retry_ms_match = re.search(r"try again in\s+(\d+)\s*ms", msg, re.I)
                if retry_ms_match:
                    retry_secs = max(retry_secs, float(retry_ms_match.group(1)) / 1000.0)
                else:
                    retry_secs = max(retry_secs, backoff)
                _last_call_ts = time.monotonic() + retry_secs

        time.sleep(retry_secs)
        total_wait += retry_secs
        backoff = min(backoff * 2, THROTTLE_SECONDS * 16)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Rate limit exhausted without meaningful error")

# ---------------- 금지/정리 유틸 ----------------
FILE_RE = re.compile(r"\b[\w\-]+\.(jpg|jpeg|png|webp|heic)\b", re.I)
DATE_RE = re.compile(r"\b20\d{2}\s*[-.]?\s*\d{1,2}\s*[-.]?\s*\d{1,2}\b|\b20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일\b")

BAN_WORDS_INLINE = [
    "사진", "이미지", "촬영", "캡처", "찍은",
    "미상", "확인되지 않음", "unknown", "현재 시각",
]

def clean_inline(s: str) -> str:
    if not s:
        return ""
    t = re.sub(r"\s+", " ", s).strip()
    t = FILE_RE.sub("", t)
    t = DATE_RE.sub("", t)
    for w in BAN_WORDS_INLINE:
        t = t.replace(w, "")
    return t.strip()

# ---------------- 교체 유틸 함수 ----------------
def replace_proper_nouns_if_no_visible_text(analysis: dict, draft: str) -> str:
    if not analysis or not draft:
        return draft
    frames = analysis.get("frames") or []
    any_visible_text = any(f.get("visible_text", "").strip() for f in frames)
    if any_visible_text:
        return draft
    replace_map = {
        r"스타벅스": "카페",
        r"이디야": "카페",
        r"투썸": "카페",
        r"던킨": "카페",
        r"파리바게뜨": "빵집",
        r"맥도날드": "패스트푸드점",
        r"롯데리아": "패스트푸드점",
    }
    text = draft
    for pat, rep in replace_map.items():
        text = re.sub(pat, rep, text, flags=re.I)
    return text

# ---------------- 카테고리 ----------------
FOOD_RE = re.compile(r"(음식|식당|카페|요리|coffee|cafe|cake|bread|meal|lunch|dinner|brunch|dessert|커피|빵|케이크|디저트)", re.I)
def decide_category_from_lines(lines: list[str]) -> str:
    if len(lines) == 1:
        return "food_single" if FOOD_RE.search(lines[0]) else "general_single"
    return "journey_multi"

# ============ 다양한 시각 포맷 파서 ============ #
def _parse_any_dt(x: str | int | float) -> datetime | None:
    if x is None:
        return None
    # epoch-like
    if isinstance(x, (int, float)):
        try:
            ts = float(x)
            if ts > 10_000_000_000:  # ms
                ts /= 1000.0
            return datetime.fromtimestamp(ts)
        except Exception:
            pass
    xs = str(x).strip()
    if re.fullmatch(r"\d{10,13}", xs):
        try:
            ts = int(xs)
            if len(xs) >= 13:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts)
        except Exception:
            pass
    if xs.endswith("Z"):
        xs = xs[:-1] + "+00:00"
    fmts = (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d.",
        "%Y.%m.%d. %H:%M:%S",
        "%Y.%m.%d. %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d-%H-%M-%S",
        "%Y.%m.%d-%H-%M-%S",
        "%Y:%m:%d %H:%M:%S",
        "%Y%m%d_%H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S.%f",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(xs, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(xs)
    except Exception:
        return None

# ============ 파일명에서 날짜/시간 추출 ============ #
_FILENAME_DT_PATTERNS = [
    re.compile(r".*?(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[_\- ](?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2}).*", re.I),
    re.compile(r".*?(?P<y>\d{4})[-_.](?P<m>\d{2})[-_.](?P<d>\d{2})[-_ ](?P<H>\d{2})[-_.:](?P<M>\d{2})(?:[-_.:](?P<S>\d{2}))?.*", re.I),
    re.compile(r".*?(?P<y>\d{4})[.](?P<m>\d{2})[.](?P<d>\d{2})[ _](?P<H>\d{2})[.](?P<M>\d{2})(?:[.](?P<S>\d{2}))?.*", re.I),
    re.compile(r".*?(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[_-](?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2}).*", re.I),
]

def _dt_from_filename(name: str) -> datetime | None:
    if not name:
        return None
    base = os.path.basename(name)
    for pat in _FILENAME_DT_PATTERNS:
        m = pat.match(base)
        if m:
            try:
                y = int(m.group("y")); mth = int(m.group("m")); d = int(m.group("d"))
                H = int(m.group("H")); M = int(m.group("M")); S = int(m.group("S") or 0)
                return datetime(y, mth, d, H, M, S)
            except Exception:
                continue
    return None

# ============ EXIF 메타데이터 추출 (bytes 기준) ============ #
def _read_exif_datetime_from_bytes(raw: bytes) -> datetime | None:
    """이미지 바이트에서 EXIF datetime 추출 → datetime 객체 반환"""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        img = Image.open(io.BytesIO(raw))
        exif = getattr(img, "_getexif", lambda: None)() or {}
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag in ("DateTimeOriginal", "DateTime"):
                dt = _parse_any_dt(str(value))
                if dt:
                    return dt
        info = getattr(img, "info", {}) or {}
        for k in ("Creation Time", "date:create", "date:modify"):
            v = info.get(k)
            if v:
                dt = _parse_any_dt(str(v))
                if dt:
                    return dt
    except Exception as e:
        print(f"EXIF 추출 실패: {e}")
    return None

# ---------------- 날짜 경계 유틸 + 스티처 ----------------
def _day_break_positions(date_sequence: list[str]) -> list[tuple[int,int]]:
    if not date_sequence or len(date_sequence) < 2:
        return []
    out = []
    def _to_date(x):
        try:
            return datetime.fromisoformat(str(x)).date()
        except Exception:
            return None
    for i in range(1, len(date_sequence)):
        a = _to_date(date_sequence[i-1]); b = _to_date(date_sequence[i])
        if a and b and a != b:
            out.append((i+1, (b - a).days))
    return out

def _label_for_days(diff: int) -> str:
    if diff <= 0: return ""
    if diff == 1: return "다음 날, "
    if diff == 2: return "이틀 뒤, "
    if diff == 3: return "사흘 뒤, "
    return f"{diff}일 뒤, "

def compose_from_frames(analysis: dict) -> str:
    frames = (analysis or {}).get("frames") or []
    if not frames:
        return ""
    breaks = {pos: diff for (pos, diff) in _day_break_positions((analysis or {}).get("date_sequence") or [])}
    pieces = []
    for idx, f in enumerate(frames, start=1):
        if idx in breaks:
            pieces.append(_label_for_days(breaks[idx]))
        s = (f.get("summary") or "").strip()
        io = f.get("indoor_outdoor") or ""
        ph = f.get("place_hint") or ""
        frag = []
        if ph: frag.append(f"{ph}에서")
        if s: frag.append(s)
        else:
            if io == "indoor": frag.append("실내 장면을 잠시 살폈다")
            elif io == "outdoor": frag.append("바깥 장면을 잠시 바라봤다")
            else: frag.append("장면을 잠시 바라봤다")
        sent = " ".join(x for x in frag if x).strip()
        if not sent.endswith("."): sent += "."
        pieces.append(sent)
    text = " ".join(pieces)
    return clean_inline(soften_report_tone(text))

_TIME_SHIFT_PAT = re.compile(r"(다음\s*날|이틀\s*뒤|사흘\s*뒤|\d+\s*일\s*뒤|며칠\s*후)", re.I)

# ======== 태그 기반 재배열 보정기 ========
_TAG_RE = re.compile(r"</?f(\d+)>", re.I)

def _reorder_by_tags(text: str, n_frames: int, date_sequence: list[str]) -> str | None:
    if not text or n_frames <= 0:
        return None
    blocks = {}
    for i in range(1, n_frames + 1):
        m = re.search(rf"<f{i}>(.*?)</f{i}>", text, re.I | re.S)
        if not m: return None
        blk = m.group(1).strip()
        if not blk: return None
        blocks[i] = blk
    breaks = {pos: diff for (pos, diff) in _day_break_positions(date_sequence or [])}
    out_parts = []
    for i in range(1, n_frames + 1):
        if i in breaks:
            out_parts.append(_label_for_days(breaks[i]))
        seg = blocks[i].strip()
        if i in breaks and _TIME_SHIFT_PAT.match(seg):
            out_parts.append(seg)
        else:
            out_parts.append(seg)
    out = " ".join(out_parts).strip()
    out = _TAG_RE.sub("", out)
    return clean_inline(out)

# ---------------- 1) 분석: 이미지 → 구조화 JSON ----------------
def analyze_images(images: list[str] | list[dict], photos_summary: list[dict] | None = None) -> dict | None:
    if not images:
        return None

    images_with_time = []
    ordering_debug = []
    for idx, img in enumerate(images[:MAX_IMAGES]):
        img_data = None
        dt = None
        src = "unknown"

        if isinstance(img, dict):
            img_data = img.get("data") or img.get("url") or ""
            cand_img_keys = ["order_ts", "shotAt", "takenAt", "timestamp", "time", "fileCreatedAt"]
            client_ts = next((img.get(k) for k in cand_img_keys if img.get(k) is not None), None)
            if client_ts is not None:
                dt = _parse_any_dt(client_ts)
                if dt: src = "pre_extracted"
            if dt is None:
                name = img.get("filename") or img.get("name") or img.get("originalName") or ""
                dt_name = _dt_from_filename(name)
                if dt_name:
                    dt = dt_name; src = "filename"
        else:
            img_data = img

        if dt is None and photos_summary and idx < len(photos_summary):
            ps = photos_summary[idx] or {}
            cand_ps_keys = ["time","takenAt","timestamp","fileCreatedAt","createdAt","created_at","sentAt","sent_at","messageTime","message_time","kakaoTime","kakao_time"]
            ps_time = next((ps.get(k) for k in cand_ps_keys if ps.get(k)), None)
            if ps_time:
                dt_ps = _parse_any_dt(ps_time)
                if dt_ps: dt = dt_ps; src = "photosSummary"

        if dt is None and isinstance(img_data, str) and img_data and img_data.startswith("data:image"):
            try:
                image_data = img_data.split(",")[1] if "," in img_data else img_data
                img_bytes = base64.b64decode(image_data)
                dt_exif = _read_exif_datetime_from_bytes(img_bytes)
                if dt_exif: dt = dt_exif; src = "exif_fallback"
            except Exception as e:
                print(f"[{idx}] data URL EXIF 추출 실패: {e}")

        images_with_time.append({
            "data": img_data,
            "original_index": idx,
            "datetime": dt,
            "date_iso": dt.date().isoformat() if dt else ""
        })
        ordering_debug.append({"i": idx, "source": src, "parsed": dt.isoformat() if dt else ""})
        print(f"[analyze_images] idx={idx}, source={src}, dt={dt.isoformat() if dt else 'None'}")

    images_with_time.sort(key=lambda x: (
        x["datetime"] is None,
        x["datetime"] if x["datetime"] else datetime.max,
        x["original_index"]
    ))
    sorted_images = [item["data"] for item in images_with_time]
    date_info_iso = [item["date_iso"] for item in images_with_time]

    sys = "당신은 사진을 사실대로 기록하는 관찰자입니다."
    prompt = (
        "아래 이미지를 **추측 없이** 관찰해 JSON으로 요약하세요.\n"
        "- 메타표현(사진/이미지/촬영 등) 금지, 파일명/날짜 언급 금지\n"
        "- 성별·인원수 추정 금지, 불확실하면 생략\n"
        "- 각 사진에 대해: 핵심 한줄(summary), 보이는 요소(elements), 실내/실외(indoor_outdoor), 시간단서(time_hint: 오전/오후/저녁/밤 등), 장소단서(place_hint: 보이면 한 단어), 공간관계(space_relations: 배경·거리감·시선방향 등 간략히), 흐름단서(flow: 이동/머무름 등)\n"
        "- 음식·장소 **고유명사(메뉴/지명)**는 **보일 때만** 기록.\n"
        "- 야외/가정/카페 추측 금지. 공원/벤치/바람/하늘/창문/카페/커피 같은 단어는 보이는 경우만 허용.\n"
        "- 한식 상차림이나 반찬류는 '반찬'으로, 명확한 명칭이 보이면 해당 단어 사용.\n"
        "- 가능한 경우, 사진 내부의 표시(간판·메뉴판 등)를 **있다/없다** 수준으로만 언급\n\n"
        "- 메뉴/요리 이름은 visible_text 에 있을 때만 기록.\n"
        "  food:{has_food,has_drink,serving_style(단품/코스/사이드 등), cuisine_guess_low_conf(저신뢰 추측)},\n"
        "- visible_text: 사진 안에 실제로 보이는 글자(간판·라벨·메뉴판 등). 없으면 빈 문자열.\n\n"
        "JSON 형식:\n"
        "{\n"
        "  \"frames\": [\n"
        "     {\"index\": 1, \"summary\": \"...\", \"elements\": [\"...\"],\n"
        "      \"indoor_outdoor\": \"indoor|outdoor|unknown\",\n"
        "      \"time_hint\": \"오전|정오|오후|저녁|밤|불명\",\n"
        "      \"place_hint\": \"보이면 한 단어, 없으면 빈 문자열\",\n"
        "      \"space_relations\": \"보이는 배경·거리감·시선방향 등 간략히\",\n"
        "      \"visible_text\": \"보이는 텍스트, 없으면 빈 문자열\",\n"
        "      \"flow\": \"이동|머무름|불명\"}\n"
        "  ],\n"
        "  \"global\": {\n"
        "     \"dominant_time\": \"오전|정오|오후|저녁|밤|불명\",\n"
        "     \"movement\": \"있음|없음|불명\"\n"
        "  }\n"
        "}\n"
        "**중요**: 입력된 이미지 순서는 **촬영시각 오름차순**입니다. 그 순서를 그대로 frames에 반영하세요."
    )

    content = [{"type":"text","text": prompt}]
    for data_url in sorted_images:
        url = data_url if isinstance(data_url, str) and data_url.startswith("data:image") else f"data:image/jpeg;base64,{data_url}"
        content.append({"type":"image_url","image_url":{"url": url, "detail":"high"}})

    r = throttled_chat_completion(
        model=MODEL_VISION,
        temperature=0.0,
        max_tokens=700,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": content}
        ]
    )
    try:
        data = json.loads(r.choices[0].message.content or "{}")
        frames = data.get("frames") or []
        for i, f in enumerate(frames, 1):
            f["index"] = i
        for f in frames:
            f["summary"] = clean_inline(f.get("summary",""))
            f["elements"] = [clean_inline(x) for x in (f.get("elements") or []) if x]
        data["date_sequence"] = date_info_iso
        data["ordering_debug"] = ordering_debug
        return data
    except Exception as e:
        print("분석 JSON 파싱 실패:", e)
        return None

# ---------------- 2) 초안 ----------------
def draft_diary(analysis: dict | None, tone: str, category_hint: str, text_model: str = MODEL_TEXT) -> str:
    """
    핵심: 설명문이 아니라 '말하듯' 쓰기. 짧고 긴 문장 섞기.
    '~있었다' 반복 줄이고, 행동/감각을 섞어서 20~30대 일기 톤.
    """
    if not analysis:
        return ""
    frames = analysis.get("frames") or []
    global_info = analysis.get("global") or {}
    date_sequence = analysis.get("date_sequence") or []

    def _to_date(x):
        try: return datetime.fromisoformat(str(x)).date()
        except Exception: return None

    date_changes = []
    if len(date_sequence) > 1:
        for i in range(1, len(date_sequence)):
            a = _to_date(date_sequence[i-1]); b = _to_date(date_sequence[i])
            if a and b and a != b:
                days_diff = (b - a).days
                if days_diff >= 1:
                    date_changes.append({"position": i + 1, "days_diff": days_diff})

    date_context = ""
    if date_changes:
        date_context = "\n[시간 흐름 정보]\n"
        for dc in date_changes:
            if dc["days_diff"] == 1: date_context += f"- {dc['position']}번 사진부터: 다음 날\n"
            elif dc["days_diff"] == 2: date_context += f"- {dc['position']}번 사진부터: 이틀 뒤\n"
            elif dc["days_diff"] == 3: date_context += f"- {dc['position']}번 사진부터: 사흘 뒤\n"
            else: date_context += f"- {dc['position']}번 사진부터: {dc['days_diff']}일 뒤\n"

    bullets = []
    for f in frames:
        idx = f.get("index"); s = f.get("summary",""); io = f.get("indoor_outdoor",""); tm = f.get("time_hint",""); ph = f.get("place_hint",""); flow= f.get("flow","")
        parts = []
        if s: parts.append(s)
        if io and io!="unknown": parts.append(f"({io})")
        if tm and tm!="불명": parts.append(f"[{tm}]")
        if ph: parts.append(f"#{ph}")
        if flow and flow!="불명": parts.append(f"{{{flow}}}")
        if parts: bullets.append(f"- {idx}번: " + " ".join(parts))

    dom_time = global_info.get("dominant_time","불명")
    movement = global_info.get("movement","불명")
    header = f"[흐름] 시각:{dom_time} 이동:{movement}"
    length_rule = "5~7문장" if (category_hint == "journey_multi" or len(frames) > 1) else "3~4문장"

    sys = (
        "당신은 20~30대가 쓰는 한국어 일기를 잘 쓰는 작가입니다. "
        "설명문이 아니라 '말하듯' 씁니다. 자연스러운 회상체로, 과장 없이 간결하게."
        "감각과 감정은 최소한만 씁니다. 과장, 의성어, 비유 금지."
        "**입력 프레임 순서를 반드시 유지**하고, 날짜가 바뀌는 지점에서는 전환사를 명시합니다."
    )
    user = f"""
아래 관찰 단서를 바탕으로 20~30대 자연체 일기를 **한 단락**으로 작성하세요.

{header}
[관찰]
{os.linesep.join(bullets) if bullets else "- 단서 적음"}
{date_context}

[출발 규칙]
- 과거형 유지. 1인칭 체험이 드러나되 '나는' 생략.
- 문장 수: {length_rule}. 짧은 문장 1—2개 포함.
- **중요**: 시간 흐름 정보에 날짜 변화가 명시되어 있으면, 해당 위치에서 **반드시** '다음 날', '이틀 뒤', '사흘 뒤', 'N일 뒤' 등으로 날짜 전환을 표시.

[절제 규칙]
- 감각 언급은 최대 2개. 미각·후각 중 1개 + 온도·촉각 중 1개만 허용.
- 감정 문장 최대 1개. '기뻤다/즐거웠다/특별했다/괜히' 등 직접 감정어 금지. 행동으로 암시.
- 의성어·과장 표현 금지: 지글지글/바삭/촉촉/입안 가득/코끝/스며들다/감돌다/간질이다/한껏/가득/벅차다/특별했다/미소가 지어졌다.
- 비유 금지. 수식어는 짧게.

[경험 중심]
- 단순히 장면을 묘사하지 말고, 그 **순간의 경험과 행동**을 중심으로 써주세요.
- '나는' 같은 주어를 직접 쓰지 않아도, 주체의 **행동**이 자연스럽게 드러나야 합니다.
- 시각적 묘사만 나열하지 말고, **후각·식감·촉각·온도감·질감** 같은 보조 감각을 섞으세요.
- 그러나 주요 감각(청각, 미각) 한 두개만 남기고 나머지는 암시로 처리해야 합니다.
- 감정이 드러날 때는 **왜 그런 감정이 생겼는지** 구체적인 이유를 함께 표현하세요.
- 그리고 감정을 결과로 두지 말고, 행위나 침묵으로 암시를 하도록 합니다.
- 문장 리듬이 단조로워지지 않도록 **짧은 문장과 묘사 문장**을 교차해 변주하세요.
- 한 두 문장은 짧게 끊고, 중간에 호흡을 만들어 줘야 합니다.

[사실 일치]
- 음식·장소 **고유명사**는 보일 때만 사용.
- **보이지 않으면 절대 추측하거나 대체 이름을 만들지 말 것.** 
- 한식 반찬류는 '반찬', 단품 요리는 '요리' 정도로만 표현.
- 사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.

[작성 규칙 — 20~30대 자연체]
- 첫 문장은 고정되어있지 않다. 맥락과 감각을 순서로 배치한다.
- 모든 문장은 **과거형**으로 통일. 중요함. 
- **관찰보다 체험을 우선** 설명. 그러나 '나는'과 같은 주어를 드러내는 표현은 금지.
- 말하듯 써라. 보고/하고/느낀 것을 직접 행위 중심 문장으로 바꿔가며 짧고 긴 문장 섞어 표현.
- '~있었다'만 반복하지 말고, '남아 있었다/눈에 들어왔다/한참 봤다/꺼냈다/잠깐 고민했다'처럼 변주하라.
- 감정은 직접 말하기보다 '조금/잠깐/괜히' 같은 부사로 은은히. 
- 음식 사진의 감각은 구체적 감각으로 암시. 그리고 감정은 있으나 원인과 연결되어야 한다.
- 메타표현(사진/이미지/촬영 등) 금지, 파일명/날짜 금지.
- 성별·인원수 추정 금지, 관계/거리감은 간접적으로.
- 너무 길어지지 않게 문장의 리듬을 다양하게 사용해야 함. 짧은 문장과 묘사 중심 문장을 교차시켜야 함. 감정의 고저가 느껴져야 한다.
- {length_rule} 준수.
- 톤: {tone or "중립"} (과장 금지, 담백하게).

[출력 서식 강화]
- 프레임 i에 대응하는 문장은 반드시 <f{i}>로 시작해 </f{i}>로 끝냅니다.
- 같은 프레임의 여러 문장은 하나의 태그 안에 포함해도 됩니다.
- 태그는 출력에만 쓰이며 최종 결과에서 제거됩니다.
"""
    r = throttled_chat_completion(
        model=text_model,
        temperature=0.15,
        top_p=0.8,
        max_tokens=600,
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": user}
        ]
    )
    draft = (r.choices[0].message.content or "").strip()
    draft = clean_inline(draft)
    draft = replace_proper_nouns_if_no_visible_text(analysis, draft)

    # 태그 기반 재배열 시도
    reordered = _reorder_by_tags(draft, n_frames=len(frames), date_sequence=date_sequence)
    if reordered:
        draft = reordered

    # 날짜 경계가 있는데 전환사가 없다면 스티치 본문으로 교체
    has_break = len(_day_break_positions(date_sequence)) > 0
    if has_break and not _TIME_SHIFT_PAT.search(draft):
        stitched = compose_from_frames(analysis)
        if stitched:
            draft = stitched
    return draft

# ----------- 교차검증: 동일 프롬포트, 다른 모델 -----------
def _norm(x: str) -> str:
    return re.sub(r"\s+", " ", (x or "").strip())

def select_draft_via_cross_validation(analysis: dict, tone: str, category_hint: str) -> tuple[str, dict]:
    primary = draft_diary(analysis, tone, category_hint, text_model=MODEL_TEXT)
    used = "primary"
    alt = None
    debug = {"primary_len": len(primary), "alt_len": 0, "used": used, "same": None, "primary_model": MODEL_TEXT, "alt_model": ALT_TEXT_MODEL}

    # ALT가 기본과 다를 때만 수행
    if ALT_TEXT_MODEL and ALT_TEXT_MODEL != MODEL_TEXT:
        try:
            alt = draft_diary(analysis, tone, category_hint, text_model=ALT_TEXT_MODEL)
            debug["alt_len"] = len(alt)
            if _norm(alt) == _norm(primary):
                debug["same"] = True
                used = "primary"
                return primary, debug
            else:
                debug["same"] = False
                used = "alt"
                debug["used"] = used
                return alt, debug
        except Exception as e:
            debug["alt_error"] = str(e)
            return primary, debug
    else:
        debug["note"] = "ALT_TEXT_MODEL is same as primary or unset"
        return primary, debug

# ---------------- 3) 보정 ----------------
def refine_diary(analysis: dict | None, draft: str, tone: str, category_hint: str) -> str:
    if not draft:
        return ""
    frames = analysis.get("frames") or [] if analysis else []
    length_rule = "5~7문장" if (category_hint == "journey_multi" or len(frames) > 1) else "3~4문장"

    sys = "당신은 말하듯 쓰는 텍스트를 다듬는 한국어 에디터입니다."
    user = f"""
[초안]
{draft}

[보정 지침]
- **1인칭 체험체 + 과거형** 유지. 관찰 표현(눈에 들어왔다/보였다)은 행동 표현(잠시 바라봤다/앞에 있었다)로 정리.
- 직접 체험 시점으로 전환하라. "나는"이나 "주어"를 직접적으로 쓰지 않고도, 주체의 **행위**가 자연스럽게 드러나게 표현해주세요.
- 장면 간의 **맥락 연결어**(그때 / 잠시 후 / 그러다 / 한참 뒤 등)를 자연스럽게 추가해 시간 흐름을 암시하라.
- 감정은 한순간이 아니라 **시간 속에서 변화**하는 느낌으로 조정하라.
- 사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.
- 감정을 구체 감각으로 자연화. 리듬 단조는 문장 길이 변주로 보정.
- 감정 변화의 원인이 있어야 한다.
- 사실과 다른 고유명사(요리명·지명) 금지. 보이지 않으면 일반어 유지.
- 너무 딱딱한 명사구 연쇄, '일상적인 풍경' 같은 추상 표현은 구체로 치환하거나 제거.
- 문장 길이와 어미를 다양화. '~있었다' 반복을 줄이고 필요한 곳만 남김.
- 감정의 포화가 되지 않도록 한 요소만 남기고 나머지는 암시로 처리해라.
- 과장/비유/메타표현 금지 유지. 한 단락 유지.
- **문장 수: {length_rule}. 톤: {tone or "중립"}**.중요.

[절제 적용]
- 감각 언급 총 2개 초과 시 초과분 삭제.
- 감정 직접 표현은 1문장 이하. 나머지는 행동으로 암시.
- 금지어 제거: 지글지글, 노릇노릇, 바삭, 촉촉, 입안 가득, 코끝, 스며들다, 감돌다, 간질이다, 한껏, 가득, 벅차다, 특별했다, 미소가 지어졌다.
- 비유·수사 제거. 추상어('일상적인 풍경/특별한 시간')는 구체로 치환하거나 삭제.


[출력]
- 한 단락만. 불필요한 수식어 축소. 관찰 나열 금지.
"""
    r = throttled_chat_completion(
        model=MODEL_TEXT,
        temperature=0.15,
        max_tokens=700,
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": user}
        ]
    )
    final_text = (r.choices[0].message.content or "").strip()
    final_text = clean_inline(final_text)
    final_text = soften_report_tone(final_text)
    return final_text

# 과장/감상문 느낌 줄이는 표현들
TRIM_PHRASES = [
    "일상적인 분위기로 가득 차 있었다",
    "시각적으로도 즐거움을 주었다",
    "상업적인 느낌을 더했다",
]

def soften_report_tone(text: str) -> str:
    """설명문 어색한 표현 정리."""
    if not text:
        return text
    t = text
    for p in TRIM_PHRASES:
        t = t.replace(p, "")
    # 과도한 '있었다' 반복 완화(아주 약하게만)
    t = re.sub(r"(있었다\.)\s+(있었다\.)", r"\1 ", t)
    return t.strip()

# ---------------- 이미지 없을 때(요약 단서) ----------------
def generate_from_lines(lines: list[str], tone: str) -> str:
    cat = decide_category_from_lines(lines)
    sys = "당신은 20~30대가 쓰는 한국어 일기를 잘 쓰는 작가입니다."
    user = f"""
[관찰 단서]
{os.linesep.join(f"- {clean_inline(x)}" for x in lines)}

[작성 규칙 – 20~30대 자연체]
- 말하듯 써라. 짧고 긴 문장 섞기.
- 시제는 모두 과거형으로 통일.
- 행동+감각 중심. 과장 금지.
- 메타표현·날짜·파일명 금지. 성별/인원수 추정 금지.
- 문장 수: {"5~7문장" if len(lines)>1 else "3~4문장"}.
- 톤: {tone or "중립"}.
- 한 단락만 출력.
"""
    r = throttled_chat_completion(
        model=MODEL_TEXT,
        temperature=0.35,
        max_tokens=600,
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": user}
        ]
    )
    text = (r.choices[0].message.content or "").strip()
    text = soften_report_tone(clean_inline(text))
    return text

# ---------------- Fallback ----------------
FALLBACKS = [
    "오늘은 별일 없었지만, 작은 장면들이 기억에 남았다.",
    "짧게 움직였을 뿐인데 공기가 조금 달랐다.",
    "별스러운 건 없었지만, 손끝에 남은 촉각이 오래 갔다.",
]

# ---------------- HTML ----------------
@app.get("/")
def index():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Snaplog_test4+map.html")
    if not os.path.exists(html_path):
        return f"Error: {html_path} 가 없습니다.", 404
    return send_file(html_path)

# ---------------- API ----------------
@app.post("/api/auto-diary")
def api_auto_dairy():
    try:
        # 1) multipart/form-data
        if len(request.files) > 0:
            tone = request.form.get("tone") or "중립"
            photos = json.loads(request.form.get("photosSummary") or "[]")
            files = request.files.getlist("images")

            images = []
            saved_files = []
            debug_injected = []
            debug_meta_head = []

            print(f"\n{'='*60}")
            print(f"[multipart 업로드] {len(files)}개 파일 수신")
            print(f"{'='*60}")

            for idx, f in enumerate(files[:MAX_IMAGES]):
                raw = f.read()
                orig_name = secure_filename(f.filename or f"upload_{uuid.uuid4().hex}")
                _, ext = os.path.splitext(orig_name)
                if not ext:
                    mime = (f.mimetype or "").lower()
                    ext = {
                        "image/jpeg": ".jpg",
                        "image/jpg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                        "image/heic": ".heic",
                        "image/heif": ".heif",
                    }.get(mime, ".bin")
                save_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + orig_name
                save_path = os.path.join(UPLOAD_DIR, save_name)
                with open(save_path, "wb") as out:
                    out.write(raw)
                saved_files.append(save_path)

                exif_dt = _read_exif_datetime_from_bytes(raw)

                ps_time = None
                if idx < len(photos):
                    ps = photos[idx] or {}
                    cand_ps_keys = ["time","takenAt","timestamp","fileCreatedAt","createdAt","created_at","sentAt","sent_at","messageTime","message_time","kakaoTime","kakao_time"]
                    ps_time_str = next((ps.get(k) for k in cand_ps_keys if ps.get(k)), None)
                    if ps_time_str:
                        ps_time = _parse_any_dt(ps_time_str)

                final_dt = exif_dt or ps_time or _dt_from_filename(orig_name)
                data_url = f"data:{f.mimetype or 'image/jpeg'};base64,{base64.b64encode(raw).decode('ascii')}"

                img_dict = {"data": data_url, "filename": orig_name, "originalName": orig_name, "saved_path": save_path}
                if final_dt:
                    img_dict["takenAt"] = final_dt.isoformat(sep=" ")
                    img_dict["timestamp"] = int(final_dt.timestamp() * 1000)
                    img_dict["shotAt"] = img_dict["timestamp"]
                    img_dict["order_ts"] = img_dict["timestamp"]
                    debug_injected.append({"i": idx, "source": "exif|ps|name", "takenAt": img_dict["takenAt"]})
                else:
                    debug_injected.append({"i": idx, "source": "none", "takenAt": ""})
                images.append(img_dict)

            print(f"{'='*60}\n")

            analysis = analyze_images(images, photos_summary=photos)
            category_hint = "journey_multi" if (analysis and len(analysis.get("frames") or []) > 1) else "general_single"

            # 교차검증 단계
            selected_draft, cv_debug = select_draft_via_cross_validation(analysis, tone, category_hint)

            # 보정 단계
            final_text = refine_diary(analysis, selected_draft, tone, category_hint)

            if final_text:
                return jsonify({
                    "ok": True,
                    "body": final_text,
                    "category": category_hint,
                    "used": "vision-3stage",
                    "observations": (analysis or {}).get("frames", []),
                    "ordering_debug": (analysis or {}).get("ordering_debug", []),
                    "date_sequence": (analysis or {}).get("date_sequence", []),
                    "saved_files": saved_files,
                    "debug_injected": debug_injected,
                    "debug_meta_head": debug_meta_head,
                    "cv_debug": cv_debug
                })
            return jsonify({
                "ok": True,
                "body": random.choice(FALLBACKS),
                "category": category_hint,
                "used": "fallback",
                "saved_files": saved_files,
                "cv_debug": cv_debug
            })

        # 2) JSON 경로
        data = request.get_json(silent=True) or {}
        tone = data.get("tone") or "중립"
        images_raw = (data.get("images") or [])[:MAX_IMAGES]
        photos = data.get("photosSummary") or []
        images_meta = data.get("imagesMeta") or []

        images: list[dict] = []
        debug_injected = []
        debug_meta_head = [{"i": i, "shotAt": (images_meta[i] or {}).get("shotAt")} for i in range(min(len(images_meta), len(images_raw)))]

        print(f"\n{'='*60}")
        print(f"[JSON 업로드] {len(images_raw)}개 이미지 수신")
        print(f"{'='*60}")

        for i, img in enumerate(images_raw):
            item = {"data": img} if not isinstance(img, dict) else img.copy()
            if i < len(images_meta):
                meta = images_meta[i] or {}
                shot = meta.get("shotAt")
                if shot is not None:
                    try:
                        ts_ms = None; dt = None
                        if isinstance(shot, (int, float)):
                            shot_int = int(shot)
                            ts_ms = shot_int if shot_int > 10_000_000_000 else shot_int * 1000
                            dt = datetime.fromtimestamp(ts_ms / 1000.0)
                        elif isinstance(shot, str):
                            dt = _parse_any_dt(shot)
                            if dt: ts_ms = int(dt.timestamp() * 1000)
                        if dt and ts_ms is not None:
                            item["takenAt"]  = dt.isoformat(sep=" ")
                            item["timestamp"]= ts_ms
                            item["shotAt"]   = ts_ms
                            item["order_ts"] = ts_ms
                            debug_injected.append({"i": i, "source": "shotAt", "takenAt": item["takenAt"]})
                        else:
                            debug_injected.append({"i": i, "source": "shotAt_parse_fail", "takenAt": ""})
                    except Exception as e:
                        debug_injected.append({"i": i, "source": "shotAt_exception", "err": str(e)})
                else:
                    debug_injected.append({"i": i, "source": "no_shotAt"})
            else:
                debug_injected.append({"i": i, "source": "no_meta"})
            images.append(item)

        print(f"{'='*60}\n")

        if images:
            try:
                analysis = analyze_images(images, photos_summary=photos)
                category_hint = "journey_multi" if (analysis and len(analysis.get("frames") or []) > 1) else "general_single"

                # 교차검증 단계
                selected_draft, cv_debug = select_draft_via_cross_validation(analysis, tone, category_hint)

                # 보정 단계
                final_text = refine_diary(analysis, selected_draft, tone, category_hint)

                if final_text:
                    return jsonify({
                        "ok": True,
                        "body": final_text,
                        "category": category_hint,
                        "used": "vision-3stage",
                        "observations": (analysis or {}).get("frames", []),
                        "ordering_debug": (analysis or {}).get("ordering_debug", []),
                        "date_sequence": (analysis or {}).get("date_sequence", []),
                        "debug_injected": debug_injected,
                        "debug_meta_head": debug_meta_head,
                        "cv_debug": cv_debug
                    })
                return jsonify({
                    "ok": True,
                    "body": random.choice(FALLBACKS),
                    "category": category_hint,
                    "used": "fallback",
                    "cv_debug": cv_debug
                })
            except RateLimitError as e:
                msg = getattr(e, "message", None) or str(e) or "rate_limit"
                retry_ms = None
                body = getattr(e, "body", {}) or {}
                err = body.get("error") if isinstance(body, dict) else {}
                if isinstance(err, dict):
                    retry_ms = err.get("retry_after")
                if retry_ms is None:
                    match = re.search(r"try again in\s+(\d+)\s*ms", msg, re.I)
                    if match:
                        retry_ms = int(match.group(1))
                return jsonify({"ok": False, "error": "rate_limit", "message": msg, "retry_after_ms": retry_ms}), 429
            except Exception as e:
                traceback.print_exc()
                category_hint = "journey_multi" if len(images) > 1 else "general_single"
                return jsonify({"ok": True, "body": random.choice(FALLBACKS), "category": category_hint, "used": "fallback", "error": str(e)})

        # 3) 이미지 없으면 photosSummary로 최소 단서 생성
        lines: list[str] = []
        for p in photos:
            base = " ".join([(p.get("place") or "").strip(), (p.get("time") or "").strip(), (p.get("weather") or "").strip(), (p.get("desc") or "").strip()]).strip()
            base = clean_inline(base)
            if base: lines.append(base)

        if lines:
            text = generate_from_lines(lines, tone)
            if text:
                return jsonify({"ok": True, "body": text, "category": decide_category_from_lines(lines), "used": "summary-lines", "observations": lines})

        return jsonify({"ok": False, "error": "no_input", "message": "사진을 넣거나 최소 단서를 제공하세요."}), 400

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/health")
def health():
    return {"ok": True}

# ---------------- CORS ----------------
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
    print("ALT_TEXT_MODEL =", ALT_TEXT_MODEL)
    print("===========================================\n")
    app.run(host="0.0.0.0", port=5000, debug=False)

    