"""Snaplog server – 3단계(분석→초안→보정)로 20~30대 자연체 일기 생성"""

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
MODEL_VISION = "gpt-4o-mini"
MODEL_TEXT   = "gpt-4o-mini"
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
    """연속 날짜 시퀀스에서 (변화가 시작되는 1-based 위치, day_diff) 목록."""
    if not date_sequence or len(date_sequence) < 2:
        return []
    out = []
    from datetime import date as _date
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
    if diff <= 0:
        return ""
    if diff == 1:
        return "다음 날, "
    if diff == 2:
        return "이틀 뒤, "
    if diff == 3:
        return "사흘 뒤, "
    return f"{diff}일 뒤, "

def compose_from_frames(analysis: dict) -> str:
    """프레임 순서를 강제 반영. 날짜 경계에 안전 전환사 삽입."""
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
        if ph:
            frag.append(f"{ph}에서")
        if s:
            frag.append(s)
        else:
            if io == "indoor":
                frag.append("실내 장면을 잠시 살폈다")
            elif io == "outdoor":
                frag.append("바깥 장면을 잠시 바라봤다")
            else:
                frag.append("장면을 잠시 바라봤다")

        sent = " ".join(x for x in frag if x).strip()
        if not sent.endswith("."):
            sent += "."
        pieces.append(sent)

    text = " ".join(pieces)
    return clean_inline(soften_report_tone(text))

_TIME_SHIFT_PAT = re.compile(r"(다음\s*날|이틀\s*뒤|사흘\s*뒤|\d+\s*일\s*뒤|며칠\s*후)", re.I)

# ======== 추가: 태그 기반 재배열 보정기 ========
_TAG_RE = re.compile(r"</?f(\d+)>", re.I)

def _reorder_by_tags(text: str, n_frames: int, date_sequence: list[str]) -> str | None:
    """<f1>..</f1> ~ <fN>..</fN> 태그가 있을 때 프레임 순서대로 재조립하고
       날짜 경계에 전환사를 삽입. 태그는 제거한다.
       태그가 불충분하면 None 반환."""
    if not text or n_frames <= 0:
        return None
    # 태그 블록 추출
    blocks = {}
    for i in range(1, n_frames + 1):
        m = re.search(rf"<f{i}>(.*?)</f{i}>", text, re.I | re.S)
        if not m:
            return None
        blk = m.group(1).strip()
        if not blk:
            return None
        blocks[i] = blk

    # 날짜 경계 계산
    breaks = {pos: diff for (pos, diff) in _day_break_positions(date_sequence or [])}

    out_parts = []
    for i in range(1, n_frames + 1):
        if i in breaks:
            out_parts.append(_label_for_days(breaks[i]))
        seg = blocks[i].strip()
        # 세그먼트 선두에 이미 전환사가 있으면 중복 방지
        out_parts.append(seg)
    out = " ".join(out_parts).strip()

    # 태그 흔적 제거
    out = _TAG_RE.sub("", out)
    return clean_inline(out)

# ======== 추가: 태그 기반 재배열(태그 보존 버전) ========
def _reorder_by_tags_keep(text: str, n_frames: int, date_sequence: list[str]) -> str | None:
    """프레임 순서대로 재조립하되 <fi>...</fi> 태그를 유지한다.
       태그가 불충분하면 None."""
    if not text or n_frames <= 0:
        return None
    blocks = {}
    for i in range(1, n_frames + 1):
        m = re.search(rf"<f{i}>(.*?)</f{i}>", text, re.I | re.S)
        if not m:
            return None
        blk = m.group(1).strip()
        if not blk:
            return None
        blocks[i] = blk

    breaks = {pos: diff for (pos, diff) in _day_break_positions(date_sequence or [])}
    out_parts = []
    for i in range(1, n_frames + 1):
        if i in breaks:
            out_parts.append(_label_for_days(breaks[i]))
        out_parts.append(f"<f{i}>{blocks[i]}</f{i}>")
    return " ".join(out_parts).strip()

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

            # 1순위: 이미 주입된 시간
            cand_img_keys = ["order_ts", "shotAt", "takenAt", "timestamp", "time", "fileCreatedAt"]
            client_ts = next((img.get(k) for k in cand_img_keys if img.get(k) is not None), None)
            if client_ts is not None:
                dt = _parse_any_dt(client_ts)
                if dt:
                    src = "pre_extracted"
            
            # 2순위: 파일명
            if dt is None:
                name = img.get("filename") or img.get("name") or img.get("originalName") or ""
                dt_name = _dt_from_filename(name)
                if dt_name:
                    dt = dt_name
                    src = "filename"
        else:
            img_data = img
        
        # 3순위: photosSummary
        if dt is None and photos_summary and idx < len(photos_summary):
            ps = photos_summary[idx] or {}
            cand_ps_keys = [
                "time", "takenAt", "timestamp", "fileCreatedAt",
                "createdAt", "created_at",
                "sentAt", "sent_at",
                "messageTime", "message_time",
                "kakaoTime", "kakao_time"
            ]
            ps_time = next((ps.get(k) for k in cand_ps_keys if ps.get(k)), None)
            if ps_time:
                dt_ps = _parse_any_dt(ps_time)
                if dt_ps:
                    dt = dt_ps
                    src = "photosSummary"
        
        # 4순위: data URL EXIF
        if dt is None and isinstance(img_data, str) and img_data and img_data.startswith("data:image"):
            try:
                image_data = img_data.split(",")[1] if "," in img_data else img_data
                img_bytes = base64.b64decode(image_data)
                dt_exif = _read_exif_datetime_from_bytes(img_bytes)
                if dt_exif:
                    dt = dt_exif
                    src = "exif_fallback"
            except Exception as e:
                print(f"[{idx}] data URL EXIF 추출 실패: {e}")

        images_with_time.append({
            "data": img_data,
            "original_index": idx,
            "datetime": dt,
            "date_iso": dt.date().isoformat() if dt else ""
        })
        ordering_debug.append({
            "i": idx,
            "source": src,
            "parsed": dt.isoformat() if dt else ""
        })
        print(f"[analyze_images] idx={idx}, source={src}, dt={dt.isoformat() if dt else 'None'}")

    # 시간순 정렬
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

        # index를 정렬된 입력 순서대로 강제
        for i, f in enumerate(frames, 1):
            f["index"] = i

        # 안전 정리
        for f in frames:
            f["summary"] = clean_inline(f.get("summary",""))
            f["elements"] = [clean_inline(x) for x in (f.get("elements") or []) if x]

        data["date_sequence"] = date_info_iso
        data["ordering_debug"] = ordering_debug
        return data
    except Exception as e:
        print("분석 JSON 파싱 실패:", e)
        return None

# ================== 검증 포함 분석 ==================
# (이 블록은 이전 답변에서 추가된 검증 로직 그대로 사용한다고 가정,
#  여기서는 코드 축약. 기존 파일에 있는 analyze_with_validation()을 사용하세요.)
# ---- 기존 analyze_with_validation 정의 유지 ----

# ---------------- 2) 초안 ----------------
def draft_diary(analysis: dict | None, tone: str, category_hint: str) -> str:
    if not analysis:
        return ""
    frames = analysis.get("frames") or []
    global_info = analysis.get("global") or {}
    date_sequence = analysis.get("date_sequence") or []

    from datetime import date as _date
    def _to_date(x):
        if not x:
            return None
        if isinstance(x, _date):
            return x
        try:
            return datetime.fromisoformat(str(x)).date()
        except Exception:
            return None

    date_changes = []
    if len(date_sequence) > 1:
        for i in range(1, len(date_sequence)):
            a = _to_date(date_sequence[i-1])
            b = _to_date(date_sequence[i])
            if a and b and a != b:
                days_diff = (b - a).days
                if days_diff >= 1:
                    date_changes.append({
                        "position": i + 1,
                        "days_diff": days_diff
                    })
    
    date_context = ""
    if date_changes:
        date_context = "\n[시간 흐름 정보]\n"
        for dc in date_changes:
            if dc["days_diff"] == 1:
                date_context += f"- {dc['position']}번 사진부터: 다음 날\n"
            elif dc["days_diff"] == 2:
                date_context += f"- {dc['position']}번 사진부터: 이틀 뒤\n"
            elif dc["days_diff"] == 3:
                date_context += f"- {dc['position']}번 사진부터: 사흘 뒤\n"
            else:
                date_context += f"- {dc['position']}번 사진부터: {dc['days_diff']}일 뒤\n"

    bullets = []
    for f in frames:
        idx = f.get("index")
        s   = f.get("summary","")
        io  = f.get("indoor_outdoor","")
        tm  = f.get("time_hint","")
        ph  = f.get("place_hint","")
        flow= f.get("flow","")
        parts = []
        if s: parts.append(s)
        if io and io!="unknown": parts.append(f"({io})")
        if tm and tm!="불명": parts.append(f"[{tm}]")
        if ph: parts.append(f"#{ph}")
        if flow and flow!="불명": parts.append(f"{{{flow}}}")
        if parts:
            bullets.append(f"- {idx}번: " + " ".join(parts))

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
- 감각 2개 이하. 직접 감정 1문장 이하. 나머지는 행동으로 암시.
- 의성어·과장 표현 금지.
- 비유 금지. 수식어는 짧게.

[경험 중심]
- 장면 나열 금지. 각 문장은 행동 중심.
- 시각 외 감각 1~2개만 보조로.
- 원인과 행동으로 감정을 암시.

[사실 일치]
- 고유명사는 보일 때만.
- 메타표현·날짜·파일명 금지.
- 사람이 보이지 않으면 군중 묘사 금지.

[작성 규칙 – 20~30대 자연체]
- 모든 문장은 과거형.
- '**프레임 순서 유지**'는 필수. 날짜 전환 문구는 누락하지 말 것.
- {length_rule} 준수.
- 톤: {tone or "중립"}.

[출력 서식 강화]
- 프레임 i에 대응하는 문장은 반드시 <f{i}>로 시작해 </f{i}>로 끝냅니다.
- 같은 프레임의 여러 문장은 하나의 태그 안에 포함해도 됩니다.
- 태그는 출력에만 쓰이며 최종 결과에서 제거됩니다.
"""
    r = throttled_chat_completion(
        model=MODEL_TEXT,
        temperature=0.15,
        top_p=0.8,
        max_tokens=600,
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": user}
        ]
    )
    draft_raw = (r.choices[0].message.content or "").strip()
    draft_raw = clean_inline(draft_raw)
    draft_raw = replace_proper_nouns_if_no_visible_text(analysis, draft_raw)

    # === 핵심 추가: 초안 단계에서 태그 보존 재정렬 ===
    kept = _reorder_by_tags_keep(draft_raw, n_frames=len(frames), date_sequence=date_sequence)
    if kept:
        return kept  # 태그 유지 상태로 반환 → 보정 단계 입력에서도 순서를 고정
    # 태그가 없거나 불완전하면, 기존 방식으로 최소 보정
    ordered = _reorder_by_tags(draft_raw, n_frames=len(frames), date_sequence=date_sequence)
    if ordered:
        return ordered
    # 날짜 경계가 있는데 전환사 없음 → 프레임 합성
    has_break = len(_day_break_positions(date_sequence)) > 0
    if has_break and not _TIME_SHIFT_PAT.search(draft_raw):
        stitched = compose_from_frames(analysis)
        if stitched:
            return stitched
    return draft_raw

# ---------------- 3) 보정 ----------------
def refine_diary(analysis: dict | None, draft: str, tone: str, category_hint: str) -> str:
    if not draft:
        return ""
    frames = analysis.get("frames") or [] if analysis else []
    date_sequence = analysis.get("date_sequence") or [] if analysis else []
    length_rule = "5~7문장" if (category_hint == "journey_multi" or len(frames) > 1) else "3~4문장"

    sys = "당신은 말하듯 쓰는 텍스트를 다듬는 한국어 에디터입니다."
    user = f"""
[초안]
{draft}

[보정 지침]
- 1인칭 체험체 + 과거형 유지. 관찰 나열 → 행동 중심으로 정리.
- 프레임 **순서와 날짜 전환**을 보존. 전환사가 누락되면 추가.
- 사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.
- 사실과 다른 고유명사 금지.
- 문장 길이 변주. '~있었다' 반복 축소.
- 과장·비유·메타표현 금지. 한 단락 유지.
- 문장 수: {length_rule}. 톤: {tone or "중립"}.

[절제 적용]
- 감각 2개 이하. 직접 감정 1문장 이하. 나머지는 행동으로 암시.

[출력]
- 한 단락만.
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

    # === 핵심 추가: 보정 후 태그 재정렬 → 태그 제거 ===
    # 보정 입력이 태그를 가지고 들어갔으므로, 보정 결과에도 태그가 남아있을 확률이 높다.
    ordered = _reorder_by_tags(final_text, n_frames=len(frames), date_sequence=date_sequence)
    if ordered:
        final_text = ordered
    else:
        # 태그가 유실되었거나 불완전 → 순서 보장을 위해 프레임 합성본으로 강제 교체
        stitched = compose_from_frames(analysis or {})
        if stitched:
            final_text = stitched
        # 그래도 없으면 기존 final_text 유지

    # 날짜 경계가 있는데 전환사 없음 → 한 번 더 보정
    has_break = len(_day_break_positions(date_sequence)) > 0
    if has_break and not _TIME_SHIFT_PAT.search(final_text):
        stitched = compose_from_frames(analysis or {})
        if stitched:
            final_text = stitched

    return final_text

# 과장/감상문 느낌 줄이는 표현들
TRIM_PHRASES = [
    "일상적인 분위기로 가득 차 있었다",
    "시각적으로도 즐거움을 주었다",
    "상업적인 느낌을 더했다",
]

def soften_report_tone(text: str) -> str:
    if not text:
        return text
    t = text
    for p in TRIM_PHRASES:
        t = t.replace(p, "")
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
        # 1) multipart/form-data: 원본 저장 + EXIF 시각 주입
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
                
                # photosSummary 시간
                ps_time = None
                if idx < len(photos):
                    ps = photos[idx] or {}
                    cand_ps_keys = [
                        "time", "takenAt", "timestamp", "fileCreatedAt",
                        "createdAt", "created_at",
                        "sentAt", "sent_at",
                        "messageTime", "message_time",
                        "kakaoTime", "kakao_time"
                    ]
                    ps_time_str = next((ps.get(k) for k in cand_ps_keys if ps.get(k)), None)
                    if ps_time_str:
                        ps_time = _parse_any_dt(ps_time_str)

                final_dt = exif_dt or ps_time or _dt_from_filename(orig_name)
                data_url = f"data:{f.mimetype or 'image/jpeg'};base64,{base64.b64encode(raw).decode('ascii')}"
                
                img_dict = {
                    "data": data_url,
                    "filename": orig_name,
                    "originalName": orig_name,
                    "saved_path": save_path,
                }
                
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

            # 검증 분석은 기존 구현 사용 (여기서는 analyze_images만 쓰고 싶다면 교체 가능)
            analysis = analyze_images(images, photos_summary=photos)
            category_hint = "journey_multi" if (analysis and len(analysis.get("frames") or []) > 1) else "general_single"
            draft = draft_diary(analysis, tone, category_hint)
            final_text = refine_diary(analysis, draft, tone, category_hint)
            
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
                    "debug_meta_head": debug_meta_head
                })
            return jsonify({
                "ok": True,
                "body": random.choice(FALLBACKS),
                "category": category_hint,
                "used": "fallback",
                "saved_files": saved_files,
            })

        # 2) JSON 경로: imagesMeta의 shotAt을 takenAt으로 주입
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
                        ts_ms = None
                        dt = None
                        if isinstance(shot, (int, float)):
                            shot_int = int(shot)
                            ts_ms = shot_int if shot_int > 10_000_000_000 else shot_int * 1000
                            dt = datetime.fromtimestamp(ts_ms / 1000.0)
                        elif isinstance(shot, str):
                            dt = _parse_any_dt(shot)
                            if dt:
                                ts_ms = int(dt.timestamp() * 1000)

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
                # 검증 분석은 기존 구현 사용 (여기서는 analyze_images만 쓰고 싶다면 교체 가능)
                analysis = analyze_images(images, photos_summary=photos)
                category_hint = "journey_multi" if (analysis and len(analysis.get("frames") or []) > 1) else "general_single"
                draft = draft_diary(analysis, tone, category_hint)
                final_text = refine_diary(analysis, draft, tone, category_hint)
                
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
                        "debug_meta_head": debug_meta_head
                    })
                return jsonify({
                    "ok": True,
                    "body": random.choice(FALLBACKS),
                    "category": category_hint,
                    "used": "fallback"
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
                return jsonify({
                    "ok": False,
                    "error": "rate_limit",
                    "message": msg,
                    "retry_after_ms": retry_ms
                }), 429
            except Exception as e:
                traceback.print_exc()
                category_hint = "journey_multi" if len(images) > 1 else "general_single"
                return jsonify({
                    "ok": True,
                    "body": random.choice(FALLBACKS),
                    "category": category_hint,
                    "used": "fallback",
                    "error": str(e)
                })

        # 3) 이미지 없으면 photosSummary로 최소 단서 생성
        lines: list[str] = []
        for p in photos:
            base = " ".join([
                (p.get("place") or "").strip(),
                (p.get("time") or "").strip(),
                (p.get("weather") or "").strip(),
                (p.get("desc") or "").strip(),
            ]).strip()
            base = clean_inline(base)
            if base:
                lines.append(base)

        if lines:
            text = generate_from_lines(lines, tone)
            if text:
                return jsonify({
                    "ok": True,
                    "body": text,
                    "category": decide_category_from_lines(lines),
                    "used": "summary-lines",
                    "observations": lines
                })

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
    print("===========================================\n")
    app.run(host="0.0.0.0", port=5000, debug=False)