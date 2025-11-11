"""Snaplog server – 3단계(분석→초안→보정)로 20~30대 자연체 일기 생성"""

from __future__ import annotations
import os, re, json, random, traceback, time
from threading import Lock
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI, RateLimitError
from datetime import datetime

# ---------------- Flask ----------------
app = Flask(__name__)
CORS(app)

# ---------------- OpenAI ----------------
API_KEY = os.getenv("OPENAI_API_KEY")
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
        # 최소 호출 간격 확보
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

# ----------------교체 유틸 함수 추가----------------
def replace_proper_nouns_if_no_visible_text(analysis: dict, draft: str) -> str:
    """
    visible_text나 명확한 증거가 없을 경우,
    초안 내 음식/지명 고유명사를 일반어로 교체한다.
    """
    if not analysis or not draft:
        return draft

    frames = analysis.get("frames") or []
    any_visible_text = any(f.get("visible_text", "").strip() for f in frames)
    if any_visible_text:
        return draft  # 실제 글자가 보였다면 그대로 둠

    # 교체 사전
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
def _parse_any_dt(x: str) -> datetime | None:
    if not x:
        return None
    x = str(x).strip()
    # 'Z' 표기 보정
    if x.endswith("Z"):
        x = x[:-1] + "+00:00"
    fmts = (
        # ISO 계열
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        # 국내·카톡 계열
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d.",
        "%Y.%m.%d. %H:%M:%S",
        "%Y.%m.%d. %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        # 하이픈 구분(파일명식)
        "%Y-%m-%d-%H-%M-%S",
        "%Y.%m.%d-%H-%M-%S",
        # EXIF
        "%Y:%m:%d %H:%M:%S",
        # 파일명 스냅샷류
        "%Y%m%d_%H%M%S",
        "%Y%m%d%H%M%S",
        # 무타임존
        "%Y-%m-%d %H:%M:%S.%f",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(x, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(x)
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

# ---------------- 1) 분석: 이미지 → 구조화 JSON ----------------
def analyze_images(images: list[str] | list[dict], photos_summary: list[dict] | None = None) -> dict | None:
    """
    당신은 사진을 세밀하게 분석하는 도우미입니다.
    각 사진에서 보이는 내용(음식, 배경, 사람 등)을 요약하고, 
    텍스트(메뉴판, 상표, 라벨 등)가 실제로 **보이는지 여부와 내용**을 명시적으로 기술하세요.
    그리고 각 사진에 대해 실내/실외, 시간단서, 장소단서, 흐름단서를 추출하세요.

    [최소 수정 추가]
    - 정렬 타임스탬프 우선순위:
      images[i].takenAt|timestamp|time|fileCreatedAt → photosSummary[i].time|takenAt|timestamp|fileCreatedAt → filename(name, originalName) → EXIF
    - ordering_debug, date_sequence(ISO) 저장
    """
    if not images:
        return None
    # ===== 내부 import =====
    from PIL import Image
    from PIL.ExifTags import TAGS
    import io
    import base64

    def extract_image_metadata(image_data: str) -> dict:
        """base64 이미지에서 촬영 시간 추출 (최후의 보조 수단)"""
        try:
            if image_data.startswith("data:image"):
                image_data = image_data.split(",")[1]
            img_bytes = base64.b64decode(image_data)
            img = Image.open(io.BytesIO(img_bytes))
            exif_data = getattr(img, "_getexif", lambda: None)() or {}
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ("DateTimeOriginal", "DateTime"):
                    dt = _parse_any_dt(str(value))
                    if dt:
                        return {"datetime": dt, "date": dt.date(), "time": dt.time()}
            # PNG info 등
            info = getattr(img, "info", {}) or {}
            for k in ("Creation Time", "date:create", "date:modify"):
                v = info.get(k)
                if v:
                    dt = _parse_any_dt(str(v))
                    if dt:
                        return {"datetime": dt, "date": dt.date(), "time": dt.time()}
            return {}
        except Exception as e:
            print(f"메타데이터 추출 실패: {e}")
            return {}

    # ---- 다양한 소스에서 시간 수집 ----
    images_with_time = []
    ordering_debug = []
    for idx, img in enumerate(images[:MAX_IMAGES]):
        # 원본 데이터 URL
        if isinstance(img, dict):
            img_data = img.get("data") or img.get("url") or ""
            # 1) 클라이언트 제공 시각
            cand_img_keys = ["takenAt", "timestamp", "time", "fileCreatedAt"]
            client_ts = next((img.get(k) for k in cand_img_keys if img.get(k)), None)
            dt = _parse_any_dt(client_ts) if client_ts else None
            src = "client" if dt else "unknown"

            # 2) 파일명에서 파싱
            if dt is None:
                name = img.get("filename") or img.get("name") or img.get("originalName") or ""
                dt_name = _dt_from_filename(name)
                if dt_name:
                    dt = dt_name
                    src = "filename"
        else:
            img_data = img
            dt = None
            src = "unknown"

        # 3) photosSummary 보조
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

        # 4) EXIF/PNG 메타
        if dt is None and isinstance(img_data, str) and img_data:
            meta = extract_image_metadata(img_data)
            if meta.get("datetime"):
                dt = meta["datetime"]
                src = "exif"

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

    # 시간 정보가 있는 것은 시간순 정렬, 없는 것은 원래 순서 유지
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
        "}"
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
        # 안전 정리
        frames = data.get("frames") or []
        for f in frames:
            f["summary"] = clean_inline(f.get("summary",""))
            f["elements"] = [clean_inline(x) for x in (f.get("elements") or []) if x]

        # 날짜 시퀀스와 디버그 저장
        data["date_sequence"] = date_info_iso
        data["ordering_debug"] = ordering_debug
        return data
    except Exception as e:
        print("분석 JSON 파싱 실패:", e)
        return None

# ---------------- 2) 초안: 20~30대 자연체로 일기 작성 ----------------
def draft_diary(analysis: dict | None, tone: str, category_hint: str) -> str:
    if not analysis:
        return ""

    frames = analysis.get("frames") or []
    global_info = analysis.get("global") or {}
    date_sequence = analysis.get("date_sequence") or []

    # 날짜 변화 감지
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
                        "position": i + 1,   # 경계 다음 프레임(1-based)
                        "days_diff": days_diff
                    })

    # ----- 타임라인 버킷 계산: D1, D2, ... -----
    n = len(frames)
    boundaries = [1] + [dc["position"] for dc in date_changes] + ([n+1] if n else [1])
    buckets = []
    for bi in range(len(boundaries)-1):
        s = boundaries[bi]
        e = boundaries[bi+1]-1
        if s <= e:
            buckets.append((f"D{bi+1}", s, e))

    # 프레임 인덱스 -> 버킷 라벨, 버킷 -> 인덱스 목록
    idx_to_bucket = {}
    bucket_to_indices = {}
    for label, s, e in buckets:
        bucket_to_indices[label] = list(range(s, e+1))
        for k in range(s, e+1):
            idx_to_bucket[k] = label

    # 타임라인 헤더
    if buckets:
        tl_parts = [f"{label}: {s}-{e}" if s != e else f"{label}: {s}" for label, s, e in buckets]
        timeline_header = "[타임라인] " + " | ".join(tl_parts)
        order_header = "[서술 순서] " + " → ".join(l for l,_,__ in buckets) + " (각 구간 내부는 번호 오름차순)"
        comp_parts = [f"{label}={','.join(str(i) for i in bucket_to_indices[label])}" for label,_,__ in buckets]
        composition_header = "[구간 구성] " + " | ".join(comp_parts)
    else:
        timeline_header = ""
        order_header = ""
        composition_header = ""

    # 관찰 단서
    bullets = []
    for f in frames:
        idx = int(f.get("index") or 0)
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
            label = idx_to_bucket.get(idx, "")
            prefix = f"[{label}] " if label else ""
            bullets.append(prefix + f"- {idx}번: " + " ".join(parts))

    dom_time = global_info.get("dominant_time","불명")
    movement = global_info.get("movement","불명")
    header = f"[흐름] 시각:{dom_time} 이동:{movement}"
    for extra in (timeline_header, order_header, composition_header):
        if extra:
            header += "\n" + extra

    # 문장 수 규칙
    length_rule = "5~7문장" if (category_hint == "journey_multi" or len(frames) > 1) else "3~4문장"

    # 고정 프롬프트 문구는 변경하지 않음
    sys = (
        "당신은 20~30대가 쓰는 한국어 일기를 잘 쓰는 작가입니다. "
        "설명문이 아니라 '말하듯' 씁니다. 자연스러운 회상체로, 과장 없이 간결하게."
        "감각과 감정은 최소한만 씁니다. 과장, 의성어, 비유 금지."
    )
    user = f"""
아래 관찰 단서를 바탕으로 20~30대 자연체 일기를 **한 단락**으로 작성하세요.

{header}
[관찰]
{os.linesep.join(bullets) if bullets else "- 단서 적음"}
{("\n[시간 흐름 정보]\n" + os.linesep.join(
    (f"- {dc['position']}번 사진부터: 다음 날" if dc["days_diff"]==1 else f"- {dc['position']}번 사진부터: {dc['days_diff']}일 후")
    for dc in date_changes
)) if date_changes else ""}

[출발 규칙]
- 과거형 유지. 1인칭 체험이 드러나되 '나는' 생략.
- 문장 수: {length_rule}. 짧한 문장 1–2개 포함.
- **중요**: 시간 흐름 정보에 날짜 변화가 명시되어 있으면, 해당 위치에서 **반드시** '다음 날', '이틀 뒤', '며칠 후' 등의 표현을 사용해 날짜가 바뀌었음을 명확히 표현할 것.
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

[작성 규칙 – 20~30대 자연체]
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
- **날짜 변화 필수 표현**: 시간이 건너뛰었으므로, 해당 부분에서 반드시 시간 변화를 명시할 것.
- {length_rule} 준수.
- 톤: {tone or "중립"} (과장 금지, 담백하게).
**예시 (날짜가 바뀐 경우):**
"크리스마스 트리 옆에서 따뜻한 음료를 마셨다. 실내가 포근했다. 강가로 나가 야경을 봤고, 바람이 차갑게 불었다. 다음 날, 단풍이 든 거리를 걷다가 작은 식당에 들렀다. 스시를 먹고 나와 광장을 지나쳤다."
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
    draft = (r.choices[0].message.content or "").strip()
    draft = clean_inline(draft)
    draft = replace_proper_nouns_if_no_visible_text(analysis, draft)
    return draft

# ---------------- 3) 보정: 리듬/어조/반복 정리 ----------------
def refine_diary(analysis: dict | None, draft: str, tone: str, category_hint: str) -> str:
    if not draft:
        return ""

    frames = analysis.get("frames") or [] if analysis else []
    length_rule = "5~7문장" if (category_hint == "journey_multi" or len(frames) > 1) else "3~4문장"
    date_sequence = analysis.get("date_sequence") or [] if analysis else []

    # 날짜 변화 감지
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

    # ----- 타임라인 버킷 계산 -----
    n = len(frames)
    boundaries = [1] + [dc["position"] for dc in date_changes] + ([n+1] if n else [1])
    buckets = []
    for bi in range(len(boundaries)-1):
        s = boundaries[bi]
        e = boundaries[bi+1]-1
        if s <= e:
            buckets.append((f"D{bi+1}", s, e))

    if buckets:
        tl_lines = [f"- {label}: {s}-{e}" if s!=e else f"- {label}: {s}" for label, s, e in buckets]
        order_line = "- 서술 순서: " + " → ".join(l for l,_,__ in buckets) + " (각 구간 내부는 번호 오름차순)"
        comp_lines = ["- " + f"{label}={','.join(str(i) for i in range(s, e+1))}" for label, s, e in buckets]
        timeline_block = "[타임라인]\n" + "\n".join(tl_lines + [order_line] + comp_lines)
    else:
        timeline_block = ""

    sys = "당신은 말하듯 쓰는 텍스트를 다듬는 한국어 에디터입니다."
    user = f"""
{timeline_block}

[초안]
{draft}

[보정 지침]
- **1인칭 체험체 + 과거형** 유지. 관찰 표현(눈에 들어왔다/보였다)은 행동 표현(잠시 바라봤다/앞에 있었다)로 정리.
- 직접 체험 시점으로 전환하라. "나는"이나 "주어"를 직접적으로 쓰지 않고도, 주체의 **행위**가 자연스럽게 드러나게 표현해주세요.
- 장면 간의 **맥락 연결어**(그때 / 잠시 후 / 그러다 / 한참 뒤 등)를 자연스럽게 추가해 시간 흐름을 암시하라.
- 감정은 한순간이 아니라 **시간 속에서 변화**하는 느낌으로 조정하라.
- **중요**: 초안에 날짜 변화 표현('다음 날', '며칠 후')이 없다면, 지금 추가해야 함. 시간 흐름 정보를 참고해 적절한 위치에 날짜 변화를 명확히 삽입할 것.
- 날짜 변화가 있는 경우 '다음 날', '며칠 후' 같은 시간 연결어를 자연스럽게 포함하라.
- 사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.
- 감정 변화의 원인이 있어야 한다.
- 사실과 다른 고유명사(요리명·지명) 금지. 보이지 않으면 일반어 유지.
- 너무 딱딱한 명사구 연쇄, '일상적인 풍경' 같은 추상 표현은 구체로 치환하거나 제거.
- 문장 길이와 어미를 다양화. '~있었다' 반복을 줄이고 필요한 곳만 남김.
- 감정의 포화가 되지 않도록 한 요소만 남기고 나머지는 암시로 처리해라.
- 과장/비유/메타표현 금지 유지. 한 단락 유지.
- **문장 수: {length_rule}. 톤: {tone or "중립"}**.중요.

[절제 적용]
- 감각 언급 이 2개 초과 시 초과분 삭제.
- 감정 직접 표현은 1문장 이하. 나머지는 행동으로 암시.
- 금지어 제거: 지글지글, 노릇노릇, 바삭, 촉촉, 입안 가득, 코끝, 스며들다, 감돌다, 간질이다, 한껏, 가득, 벅차다, 특별했다, 미소가 지어졌다.
- 비유·수사 제거. 추상어('일상적인 풍경/특별한 시간')는 구체로 치환하거나 삭제.

[출력]
- 한 단락만. 불필요한 수식어 축소. 관찰 나열 금지.
**날짜 변화 확인**: 초안에 날짜 변화가 명시되어 있는지 확인. 없으면 '다음 날' 또는 '며칠 후' 표현을 반드시 추가.

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

# 과장/감상문 느낌 줄이는 표현들(최종 보정에서 완화)
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
- 말하듯. 행동+감각 중심. 단순 '좋았다' 대신 조명/온도/식감 등으로 감정 암시.
- '~있었다' 반복 줄이기. 작은 행동과 감각 단서를 섞기.  
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
    "별스러운 건 없었지만, 손끝에 남은 촉감이 오래 갔다.",
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
        data = request.get_json(silent=True) or {}
        tone = data.get("tone") or "중립"
        images = (data.get("images") or [])[:MAX_IMAGES]
        photos = data.get("photosSummary") or []

        print("[auto-diary] images:", len(images), "photosSummary:", len(photos))

        # 1) 이미지가 있는 경우: 분석 → 초안 → 보정
        if images:
            try:
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

        # 2) 이미지 없으면 photosSummary로 최소 단서 생성
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