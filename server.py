"""Snaplog server – 3단계(분석→초안→보정) + 교차검증(모델 이중생성)"""

from __future__ import annotations
import os, re, json, random, traceback, time, io, base64, uuid
from threading import Lock
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI, RateLimitError
from openai import APIConnectionError, APITimeoutError
from datetime import datetime, timedelta  # [추가] timedelta
from werkzeug.utils import secure_filename

# ---------------- Flask ---------------

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
MODERATION_MODEL = os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest")  # [추가] 텍스트 모더레이션

MAX_IMAGES   = 5
THROTTLE_SECONDS = float(os.getenv("OPENAI_THROTTLE_SECONDS", "0.5"))
MAX_WAIT_SECONDS = float(os.getenv("OPENAI_MAX_WAIT_SECONDS", "30"))
REQUEST_TIMEOUT = float(os.getenv("OPENAI_REQUEST_TIMEOUT", "30"))
_last_call_ts = 0.0
_throttle_lock = Lock()

# === Call-budget switches (추가) ===
STAGE1_TOP_N = int(os.getenv("SNAPLOG_STAGE1_TOPN", "5"))  # Stage1에 투입할 최대 이미지 수 (<= MAX_IMAGES)
ALT_SKIP_IF_LOW_FOOD = int(os.getenv("SNAPLOG_ALT_SKIP_IF_LOW_FOOD", "1"))  # 음식 가능성 낮으면 ALT 스킵
ALT_LOW_FOOD_THRESH = float(os.getenv("SNAPLOG_LOW_FOOD_THRESH", "0.4"))    # 0~1 사이, 낮을수록 ALT 더 자주 스킵
REFINE_SKIP_IF_SHORT = int(os.getenv("SNAPLOG_REFINE_SKIP_IF_SHORT", "1"))  # 초안이 짧으면 보정 스킵
REFINE_MIN_CHARS = int(os.getenv("SNAPLOG_REFINE_MIN_CHARS", "280"))        # 이 길이 미만이면 보정 생략

def throttled_chat_completion(**kwargs):
    global _last_call_ts
    backoff = THROTTLE_SECONDS
    last_error: Exception | None = None
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
                # 요청 타임아웃 명시
                resp = client.chat.completions.create(timeout=REQUEST_TIMEOUT, **kwargs)
                _last_call_ts = time.monotonic()
                return resp
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
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
    raise RuntimeError("Rate limit/timeout exhausted")

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

    # 교체 사전 (필요시 확장 가능)
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

# ---- 보고서형/나열/오타/시제/시점/시점/리듬/감정 교정 ----
GENERIC_LIST_RE = re.compile(r"(국|찌개|탕|면|밥|반찬|김치)(?:[ ,과와및]+(국|찌개|탕|면|밥|반찬|김치))+", re.U)
def simplify_food_enumeration(text: str) -> str:
    if not text: return text
    return GENERIC_LIST_RE.sub("반찬 몇 가지", text)

# ---------------- 카테고리 ----------------
FOOD_RE = re.compile(r"(음식|식당|카페|요리|coffee|cafe|cake|bread|meal|lunch|dinner|brunch|dessert|커피|빵|케이크|디저트)", re.I)
def decide_category_from_lines(lines: list[str]) -> str:
    if len(lines) == 1:
        return "food_single" if FOOD_RE.search(lines[0]) else "general_single"
    return "journey_multi"

# ============ 다양한 시각 포맷 파서 ============
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

# ============ 파일명에서 날짜/시간 추출 ============
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

# ============ EXIF 메타데이터 추출 (bytes 기준) ============
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

# ---------------- [추가] 날짜 기준 시프트 유틸 ----------------
def _parse_date_only(s: str | None):
    if not s:
        return None
    try:
        dt = _parse_any_dt(s)
        return dt.date() if dt else None
    except Exception:
        return None

def _shift_date_sequence(orig_seq: list[str], target_date_str: str) -> list[str]:
    """
    orig_seq의 첫 유효 날짜를 anchor로 하여 상대 일수 차이를 유지한 채
    전체 시퀀스를 target_date로 평행 이동한다.
    """
    td = _parse_date_only(target_date_str)
    if not td or not orig_seq:
        return orig_seq

    def _to_date(x):
        try:
            return datetime.fromisoformat(str(x)).date()
        except Exception:
            return None

    base = None
    for d in orig_seq:
        dd = _to_date(d)
        if dd:
            base = dd
            break

    if base is None:
        return [td.isoformat() for _ in orig_seq]

    deltas = []
    for d in orig_seq:
        dd = _to_date(d)
        deltas.append((dd - base).days if dd else 0)

    return [(td + timedelta(days=k)).isoformat() for k in deltas]

# ---------------- 분석 결과 후처리: 음식 후보 융합 ----------------
def fuse_food_candidates(analysis: dict) -> dict:
    fused = {}  # name -> {'score_sum':..., 'hits':..., 'evidence':set()}
    frames = (analysis or {}).get("frames") or []
    for f in frames:
        fs = f.get("food_structured") or {}
        for c in fs.get("main_dish_candidates") or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            conf = float(c.get("confidence") or 0.0)
            ev = tuple(c.get("evidence") or [])
            if name not in fused:
                fused[name] = {"score_sum": 0.0, "hits": 0, "evidence": set()}
            fused[name]["score_sum"] += conf
            fused[name]["hits"] += 1
            fused[name]["evidence"].update(ev)
    out = []
    for name, v in fused.items():
        out.append({
            "name": name,
            "global_conf": v["score_sum"] / max(v["hits"], 1),
            "frames_support": v["hits"],
            "evidence_uniq": sorted(v["evidence"]),
        })
    out.sort(key=lambda x: (x["global_conf"], x["frames_support"]), reverse=True)
    return {"global_candidates": out}

# --------- 음식 가능성 스코어러 (추가) ----------
def _food_likelihood_score(analysis: dict | None) -> float:
    """
    0.0~1.0. 프레임의 has_food, elements, food_fusion를 종합해 간단 스코어.
    - 다중사진에서도 계산은 하지만, 다중사진에서는 food_structured를 사용하지 않으므로
      'ALT 스킵 여부' 판단에만 쓴다.
    """
    if not analysis:
        return 0.0
    frames = analysis.get("frames") or []
    if not frames:
        return 0.0

    # 1) has_food 비율
    has_food_ratio = sum(1 for f in frames if f.get("has_food") is True) / max(len(frames), 1)

    # 2) elements 키워드 힌트
    KW = ("음료","컵","케이크","빵","디저트","접시","그릇","젓가락","포크","샐러드","초밥","라벨","메뉴")
    kw_hits = 0
    total_items = 0
    for f in frames:
        els = f.get("elements") or []
        total_items += len(els)
        kw_hits += sum(1 for x in els if any(k in x for k in KW))
    kw_ratio = (kw_hits / total_items) if total_items else 0.0

    # 3) food_fusion 신뢰도
    fusion = (analysis or {}).get("food_fusion") or {}
    cands = fusion.get("global_candidates") or []
    top_conf = cands[0]["global_conf"] if cands else 0.0

    # 가중 평균(단순)
    return 0.5 * has_food_ratio + 0.3 * kw_ratio + 0.2 * float(top_conf)

# --------- 안전 필터 (추가) ----------
def is_content_safe_for_diary(analysis: dict | None) -> tuple[bool, dict]:
    """
    분석 결과를 바탕으로 일기 텍스트 생성 전, 안전성 체크.
    - frames.summary / elements / visible_text를 모아서 모더레이션 API에 보냄
    - 문제가 있으면 False 반환
    """
    if not analysis:
        return True, {"reason": "no_analysis"}

    frames = analysis.get("frames") or []
    if not frames:
        return True, {"reason": "no_frames"}

    texts: list[str] = []
    for f in frames:
        s = f.get("summary")
        if isinstance(s, str):
            texts.append(s)
        els = f.get("elements")
        if isinstance(els, list):
            texts.extend(str(x) for x in els if x)
        vt = f.get("visible_text")
        if isinstance(vt, str):
            texts.append(vt)

    joined = " ".join(clean_inline(x) for x in texts if x).strip()
    if not joined:
        return True, {"reason": "empty_text"}

    try:
        resp = client.moderations.create(
            model=MODERATION_MODEL,
            input=joined[:4000],
        )
        result = resp.results[0]
        flagged = bool(getattr(result, "flagged", False))
        categories = getattr(result, "categories", {})
        return (not flagged), {
            "flagged": flagged,
            "categories": categories,
        }
    except Exception as e:
        # 모더레이션 실패 시에는 일단 통과시키되, 디버그 정보만 남김
        return True, {"error": str(e)}

# --------- 음식-dominant multi 판별 (추가) ----------
def is_food_dominant_multi(analysis: dict | None) -> bool:
    if not analysis:
        return False
    frames = analysis.get("frames") or []
    if len(frames) < 2:
        return False

    has_food_ratio = sum(1 for f in frames if f.get("has_food") is True) / len(frames)
    places = { (f.get("place_hint") or "").strip() for f in frames if f.get("place_hint") }
    place_variety = len(places)
    movement = (analysis.get("global") or {}).get("movement")

    return (
        has_food_ratio >= 0.8 and
        place_variety <= 2 and
        movement in (None, "", "없음", "불명")
    )

# --------- multi 음식 세트용 food_structured 보강 (추가) ----------
def enrich_food_structured_for_multi(analysis: dict | None,
                                     images: list | None = None,
                                     photos_summary: list | None = None) -> dict | None:
    """
    다중 이미지 세트 중 음식-dominant인 경우,
    각 has_food 프레임에 대해 단일 이미지 분석을 재사용해 food_structured/visible_text를 채운다.
    - 기존 analyze_images의 단일 이미지 분기(프롬프트)를 그대로 재사용하기 위해
      내부적으로 analyze_images([data_url])를 호출한다.
    - prompts, 기존 로직을 변경하지 않고 '추가 호출'만 수행.
    """
    if not analysis:
        return analysis
    frames = analysis.get("frames") or []
    if not frames:
        return analysis

    sorted_images = analysis.get("sorted_images") or []
    if not sorted_images or len(sorted_images) != len(frames):
        return analysis

    for idx, f in enumerate(frames):
        if not f.get("has_food"):
            continue
        if f.get("food_structured"):
            continue
        if idx >= len(sorted_images):
            continue

        img_data_url = sorted_images[idx]
        try:
            sub_analysis = analyze_images([img_data_url], photos_summary=None)
        except Exception as e:
            print(f"[enrich_food_structured_for_multi] sub analyze_images 실패 idx={idx}: {e}")
            continue

        if not sub_analysis:
            continue
        sub_frames = sub_analysis.get("frames") or []
        if not sub_frames:
            continue
        sub_f = sub_frames[0]
        fs = sub_f.get("food_structured")
        if fs:
            f["food_structured"] = fs
        if not f.get("visible_text") and sub_f.get("visible_text"):
            f["visible_text"] = sub_f["visible_text"]

    analysis["food_fusion"] = fuse_food_candidates(analysis)
    return analysis

# ---------------- 1) 분석: 이미지 → 구조화 JSON ----------------
def analyze_images(images: list[str] | list[dict], photos_summary: list[dict] | None = None) -> dict | None:
    """
    당신은 사진을 세밀하게 분석하는 도우미입니다.
    각 사진에서 보이는 내용(음식, 배경, 사람 등)을 요약하고,
    텍스트(메뉴판, 상표, 라벨 등)가 실제로 보이는지 여부와 내용을 명시적으로 기술하세요.
    그리고 각 사진에 대해 실내/실외, 시간단서, 장소단서, 흐름단서를 추출하세요.
    """
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

    # 단일/다중 분기: 단일은 food_structured 포함, 다중은 제외
    if len(sorted_images) == 1:
        prompt = (
            "아래 이미지를 **추측 없이** 관찰해 JSON으로 요약하세요.\n"
            "- 메타표현(사진/이미지/촬영/물건 등) 금지, 파일명/날짜 언급 금지\n"
            "- 성별·인원수 추정 금지, 불확실하면 생략\n"
            "- 각 사진에 대해: 핵심 한줄(summary), 보이는 요소(elements), 실내/실외(indoor_outdoor), 시간단서(time_hint: 오전/오후/저녁/밤 등), 장소단서(place_hint: 보이면 한 단어), 공간관계(space_relations: 배경·거리감·시선방향 등 간략히), 흐름단서(flow: 이동/머무름 등)\n"
            "- '보이는 것만' 간단히\n"
            "- 평가/추상 포현 금지 : '식사가 준비되어 있었다/식욕을 자극'같은 해석 문구 금지. 보이는 사실만.\n"
            "- 음식·장소 **고유명사(메뉴/지명)**는 **보일 때만** 기록.\n"
            "- 음식 인식은 **보이는 형상·색·토핑·용기·재료** 근거로만 판단. 추측 금지.\n"
            "- 야외/가정/카페 추측 금지. 보이는 단서만 사용.\n"
            "- 한식 상차림이나 반찬류는 '반찬' 표기. 명확한 명칭이 보이면 그대로 사용.\n"
            "- visible_text: 사진 안에 실제로 보이는 글자. 없으면 빈 문자열.\n"
            "- has_food이 false면 food_structured를 **생략**.\n"
            "- has_food이 true라도 비어 있는 배열/필드는 생략하고 필요한 항목만 기록.\n"
            "- main_dish_candidates는 상위 1개, evidence는 최대 2개 문장.\n\n"
            "JSON 형식:\n"
            "{\n"
            "  \"frames\": [\n"
            "    {\n"
            "      \"index\": 1,\n"
            "      \"summary\": \"...\",                       \n"
            "      \"elements\": [\"...\"],                    \n"
            "      \"indoor_outdoor\": \"indoor|outdoor|unknown\",\n"
            "      \"time_hint\": \"오전|정오|오후|저녁|밤|불명\",\n"
            "      \"place_hint\": \"보이면 한 단어\",\n"
            "      \"space_relations\": \"최대 20자\",\n"
            "      \"visible_text\": \"보이는 텍스트(없으면 빈 문자열)\",\n"
            "      \"flow\": \"이동|머무름|불명\",\n"
            "      \"has_food\": true|false,\n"
            "      \"food_structured\": {\n"
            "        \"serving_style\": \"단품|덮밥|비빔|국물|사이드|불명\",\n"
            "        \"starch_base\": \"밥|면|떡|빵|없음|불명\",\n"
            "        \"container\": \"접시|그릇|트레이|도시락|불명\",\n"
            "        \"sauce\": {\"present\": true|false, \"color\": \"빨강|갈색|노랑|초록|검정|흰색|투명|불명\", \"form\": \"코팅|웅덩이|곁들임|국물|불명\"},\n"
            "        \"shape_cues\": [\"예: 원통형\", \"예: 면발\"],\n"
            "        \"surface_cues\": [\"예: 유광 소스\", \"예: 튀김옷\"],\n"
            "        \"ingredients_visible\": [\"예: 가지\", \"예: 양파\"],\n"
            "        \"main_dish_candidates\": [\n"
            "          {\n"
            "            \"name\": \"후보명(메뉴판에 보이면 그대로)\",\n"
            "            \"confidence\": 0.0,\n"
            "            \"evidence\": [\"형상 단서 1\", \"색/용기 단서 1\"]\n"
            "          }\n"
            "        ]\n"
            "      }\n"
            "    }\n"
            "  ],\n"
            "  \"global\": {\"dominant_time\": \"오전|정오|오후|저녁|밤|불명\", \"movement\": \"있음|없음|불명\"}\n"
            "}\n"
            "**중요**: 입력된 이미지 순서는 **촬영시각 오름차순**입니다. 그 순서를 그대로 frames에 반영하세요.\n"
            "**빈 문자열/빈 배열/빈 객체는 출력하지 마세요. 불명/false 값의 키는 생략하세요.**"
        )
        max_tok = 900
    else:
        # 다중 사진: food_structured 완전 제외
        prompt = (
            "아래 이미지를 **추측 없이** 관찰해 JSON으로 요약하세요.\n"
            "- 메타표현(사진/이미지/촬영/물건 등) 금지, 파일명/날짜 언급 금지\n"
            "- 성별·인원수 추정 금지, 불확실하면 생략\n"
            "- 각 사진에 대해 summary, elements, indoor_outdoor, time_hint, place_hint, space_relations, visible_text, flow, has_food만 출력\n"
            "- 음식·장소 고유명사는 보일 때만 기록\n"
            "- visible_text는 실제 보이는 글자만\n"
            "- **food_structured는 어느 사진에서도 출력하지 마세요**\n\n"
            "JSON 형식:\n"
            "{\n"
            "  \"frames\": [\n"
            "    {\n"
            "      \"index\": 1,\n"
            "      \"summary\": \"...\",\n"
            "      \"elements\": [\"...\"],\n"
            "      \"indoor_outdoor\": \"indoor|outdoor|unknown\",\n"
            "      \"time_hint\": \"오전|정오|오후|저녁|밤|불명\",\n"
            "      \"place_hint\": \"보이면 한 단어\",\n"
            "      \"space_relations\": \"최대 20자\",\n"
            "      \"visible_text\": \"보이는 텍스트(없으면 빈 문자열)\",\n"
            "      \"flow\": \"이동|머무름|불명\",\n"
            "      \"has_food\": true|false\n"
            "    }\n"
            "  ],\n"
            "  \"global\": {\"dominant_time\": \"오전|정오|오후|저녁|밤|불명\", \"movement\": \"있음|없음|불명\"}\n"
            "}\n"
            "**중요**: 입력된 이미지 순서는 **촬영시각 오름차순**입니다. 그 순서를 그대로 frames에 반영하세요.\n"
            "**빈 문자열/빈 배열/빈 객체는 출력하지 마세요. 불명/false 값의 키는 생략하세요.**"
        )
        max_tok = 800

    content = [{"type":"text","text": prompt}]
    for data_url in sorted_images:
        url = data_url if isinstance(data_url, str) and data_url.startswith("data:image") else f"data:image/jpeg;base64,{data_url}"
        detail = "high" if len(sorted_images) == 1 else "low"
        content.append({"type":"image_url","image_url":{"url": url, "detail": detail}})

    r = throttled_chat_completion(
        model=MODEL_VISION,
        temperature=0.0,
        max_tokens=max_tok,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": content}
        ]
    )

    # [추가] Vision 단계 내장 content_filter 감지
    try:
        finish_reason = r.choices[0].finish_reason
    except Exception:
        finish_reason = None
    if finish_reason == "content_filter":
        return {"unsafe": True, "reason": "content_filter"}

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
        data["sorted_images"] = sorted_images  # [추가] multi-enrich용
        # --- 글로벌 음식 후보 융합 추가 ---
        data["food_fusion"] = fuse_food_candidates(data)
        return data
    except Exception as e:
        raw = r.choices[0].message.content if r and r.choices else ""
        if raw:
            snippet = raw[:2000]
            print("분석 JSON 파싱 실패: 원문 스니펫 ->", snippet)
            print("분석 JSON 파싱 실패: 원문 repr ->", repr(snippet))
        print("분석 JSON 파싱 실패:", e)
        return None

# ---------------- 2) 초안 ----------------
def draft_diary(analysis: dict | None, tone: str, category_hint: str, text_model: str = MODEL_TEXT) -> str:
    """
    핵심: 설명문이 아니라 '말하듯' 쓰기. 짧고 긴 문장 섞기.
    '30대 일기 톤.
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

        unknown_time_flags = any((f.get("time_hint") or "불명") == "불명" for f in frames)
        if unknown_time_flags:
            bullets.append("- [경고] 일부 프레임 time_hint=불명. 이 프레임들에서는 시간단어를 생성하지 마라.")
        # ---------- 여기부터 음식 후보/재료 단서 주입 ----------
        fs = f.get("food_structured") or {}
        cands = (fs.get("main_dish_candidates") or [])
        top = cands[0] if cands else {}
        conf = float(top.get("confidence") or 0.0)
        name = (top.get("name") or "").strip()
        ings = ", ".join(fs.get("ingredients_visible") or [])
        vt = (f.get("visible_text") or "").strip()

        # 음식 프레임이면 글씨는 일기 단서로 쓰지 않고,
        # 음식명/재료만 단서로 사용
        if f.get("has_food") is True:
            if name and conf >= 0.75:
                parts.append(f"#{name}")
            elif ings:
                parts.append(f"[재료:{ings}]")
        else:
            # 음식이 아닌 프레임에서만 visible_text를 힌트로 전달
            if vt:
                parts.append(f"[텍스트:{vt}]")
        # ---------- 음식 단서 주입 끝 ----------
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

아래 관찰 단서를 바탕으로 20~30대 자연체 일기를 한 단락으로 작성하세요.

{header}
[관찰]
{os.linesep.join(bullets) if bullets else "- 단서 적음"}
{date_context}

[출발 규칙]

과거형 유지. 1인칭 체험이 드러나되 '나는' 생략.

첫 문장은 '행동 1문장'으로 시작. 대상 나열 또는 관찰 서술 금지.

첫 문장에 시간단서(time_hint: 오전/오후/저녁/밤 등)이 포함되면 안된다.

단순히 장면을 묘사하지 말고, 경험과 행동을 중심으로 써주세요.

문장 수: {length_rule}. 짧은 문장 1—2개 포함.

중요: 시간 흐름 정보에 날짜 변화가 명시되어 있으면, 해당 위치에서 반드시 '다음 날', '이틀 뒤', '사흘 뒤', 'N일 뒤' 등으로 날짜 전환을 표시.

문장 간에는 반드시 시간·공간 연결어를 넣는다.

[리듬 규칙 — 강제]

연속 단문 금지: 마침표 기준 12자 이하 문장이 2회 연속이면, 다음 문장은 25자 이상 복합문으로 쓴다.

복합문 최소 2개: 접속어(그래서/때문에/덕분에/하지만/그리고/그러다가 등)나 관계절(…한 …)을 포함한 25자 이상 문장을 최소 2개 포함한다.

단문 최소 2개: 6~12자 사이의 짧은 행동 문장을 최소 2개 섞는다.

시작·중간·마침 변주: 시작은 행동 단문, 중간에는 복합문 중심, 마침은 짧은 정리 문장으로 리듬을 닫는다.

동일 종결어 3회 연속 금지: ‘~했다.’가 3회 연속이면 세 번째 문장은 이유절을 포함한 복합문으로 바꾼다.

[시간표현 규칙 — 강제]

첫 문장은 시간단어(오전/정오/오후/저녁/밤)로 시작하면 안 된다. 문장 시작에 시간단어가 오면 전체 응답이 무효다. 행동으로 시작하라.

어떤 문장에도 time_hint가 '불명'인 프레임에서 시간단어를 만들지 마라.

time_hint가 있을 때만 해당 프레임 문장 중간에 짧게 넣을 수 있다.

시제는 전부 과거형으로 통일한다. 진행형·현재 완료형 금지.'먹으며~퍼졌다' 같은 진행 + 과거 혼용이 나오면 재작성한다.

연결어에 시간어가 포함되어도 문장 첫머리 시간어 금지 규칙은 유지한다.

[절제 규칙]

금지구 : '테이블 위에 ~ 준비되어 있었다", "가지런히 놓인 모습", "눈에 들어왔다", "향이 ~식욕을 자극했다.", "조용히 앉아", "한 입 먹고 나니 마음이 ~졌다","생각에 잠겼다",

위 표현들은 의미를 보존해 행동으로 치환.

감각 언급은 최대 2개. 미각·후각 중 1개 + 온도·촉각 중 1개만 허용.

감정 문장 최대 1개. '기뻤다/즐거웠다/특별했다/괜히' 등 직접 감정어 금지. 행동으로 암시.

의성어·과장 표현 금지: 지글지글/바삭/촉촉/입안 가득/코끝/스며들다/감돌다/간질이다/한껏/가득/벅차다/특별했다/미소가 지어졌다.

비유 금지. 수식어는 짧게.


[경험 중심]

단순히 장면을 묘사하지 말고, 그 순간의 경험과 행동을 중심으로 써주세요.

'나는' 같은 주어를 직접 쓰지 않아도, 주체의 행동이 자연스럽게 드러나야 합니다.

시각적 묘사만 나열하지 말고, 후각·식감·촉각·온도감·질감 같은 보조 감각을 섞으세요.

그러나 주요 감각(청각, 미각) 한 두개만 남기고 나머지는 암시로 처리해야 합니다.

감정이 드러날 때는 왜 그런 감정이 생겼는지 구체적인 이유를 함께 표현하세요.

그리고 감정을 결과로 두지 말고, 행위나 침묵으로 암시를 하도록 합니다.

문장 리듬이 단조로워지지 않도록 짧은 문장과 묘사 문장을 교차해 변주하세요.

한 두 문장은 짧게 끊고, 중간에 호흡을 만들어 줘야 합니다.

'테이블/음식/향'같은 보편 명사는 가능한 한 행동, 사물 상호작용으로 대체.

관찰동사(보였다/눈에 들어왔다/보였던)는 금지. 동일 정보는 '무엇을 했는지'로 표현.

감각->감정의 짧은 인과를 각 전환부에 1회 이상 넣는다.

같은 패턴 반복 금지.예를 들어 ~했다, ~했다가 3회 이상 연속되면 다음 문장은 복합문으로 쓴다.


[사실 일치]

음식·장소 고유명사는 보일 때만 사용.

보이지 않으면 절대 추측하거나 대체 이름을 만들지 말 것.

한식 반찬류는 '반찬', 단품 요리는 '요리' 정도로만 표현.

사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.


[음식명 사용 규칙]

각 프레임의 food_structured.main_dish_candidates에서 최상위 후보의 confidence≥0.75면 그 음식 정식 명칭 사용 가능

0.5≤confidence<0.75면 명칭 대신 재료 기반 표현만 사용

confidence<0.5면 일반어('요리','반찬')만 사용.

visible_text에 메뉴명이 실제로 보이면 confidence와 무관하게 그 표기 그대로 사용.


[작성 규칙 — 20~30대 자연체]

첫 문장은 고정되어있지 않다. 맥락과 감각을 순서로 배치한다.

그리고 첫 문장은 절대 관찰하는 내용이 들어가면 안된다. 경험에 대한 내용이 들어가야 된다.

시각적 묘사만 나열하지 말고, 후각·식감·촉각·온도감·질감 같은 보조 감각을 섞으세요.

모든 문장은 과거형으로 통일. 중요함.

직접 체험 시점으로 전환하라. 행동이 서술적이지 않고 체험적이게 해야한다. 행위 중심의 문장과 감정,생각을 섞되 절제.

중요 "나는"이나 "주어"를 직접적으로 쓰지 않고도, 주체의 행위가 자연스럽게 드러나게 표현해주세요.

말하듯 써라. 보고/하고/느낀 것을 직접 행위 중심 문장으로 바꿔가며 짧고 긴 문장 섞어 표현.

'~있었다'만 반복하지 말고, 다양한 표현들로로 변주하라.

감정 변화의 원인이 있어야 한다.

감정은 직접 말하기보다 '조금/잠깐/괜히' 같은 부사로 은은히.

음식 사진의 감각은 구체적 감각으로 암시. 그리고 감정은 있으나 원인과 연결되어야 한다.

하지만 추상적인 감각은 금지. 구체적인 감각으로 암시.

메타표현(사진/이미지/촬영 등) 금지, 파일명/날짜 금지.

성별·인원수 추정 금지, 관계/거리감은 간접적으로.

너무 길어지지 않게 문장의 리듬을 다양하게 사용해야 함. 짧은 문장과 묘사 중심 문장을 교차시켜야 함. 감정의 고저가 느껴져야 한다.

'~하며 ~퍼졌다, ~하고 ~스쳤다'처럼 동시진행+결과 구조를 사용하지 않는다. 원인과 결과를 분리해 과거형 두 문장으로 쓴다.

문장 수: {length_rule}. 짧은 문장 1—2개 포함. 길이 분포: 6~12자 단문≥2, 25자 이상 복합문≥2.

톤: {tone or "중립"} (과장 금지, 담백하게).


[위반 시 재작성]

출력이 시간단어로 시작하거나 관찰 나열로 시작하면 즉시 재작성하라. 첫 문장은 행동이어야 한다.


[출력 서식 강화]

프레임 i에 대응하는 문장은 반드시 <f{{i}}>로 시작해 </f{{i}}>로 끝냅니다.

같은 프레임의 여러 문장은 하나의 태그 안에 포함해도 됩니다.

태그는 출력에만 쓰이며 최종 결과에서 제거됩니다.

금지구가 생성될 경우 같은 의미를 '행동'으로 치환해 다시 작성.

각<f{{i}}>...</f{{1}}>블록의 첫 문장은 행동으로 시작하고, 두 번째 문장에서만 감각·감정·결과를 연결한다.

모든 <f{{i}}>블록 사이에는 연결어 1개 이상을 둔다.
"""
    r = throttled_chat_completion(
        model=text_model,
        temperature=0.20,
        top_p=0.9,
        max_tokens=600,
        messages=[
            {"role":"system","content": sys},
            {"role":"user","content": user}
        ]
    )
    draft = (r.choices[0].message.content or "").strip()

    draft = clean_inline(draft)
    draft = replace_proper_nouns_if_no_visible_text(analysis, draft)
    draft = simplify_food_enumeration(draft)  # 필요 시 제거 또는 조건부 실행
    draft = soften_report_tone(draft)

    # 태그 기반 재배열 시도
    reordered = _reorder_by_tags(draft, n_frames=len(frames), date_sequence=date_sequence)
    if reordered:
        draft = reordered
    else:
        draft = _TAG_RE.sub("", draft)
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
    length_rule = "54문장"

    sys = "당신은 말하듯 쓰는 텍스트를 다듬는 한국어 에디터입니다."
    user = f"""

[초안]
{draft}

[보정 지침]

1인칭 체험체 + 과거형 유지. 관찰 표현(눈에 들어왔다/보였다)은 행동 표현(잠시 바라봤다/앞에 있었다)로 정리.

직접 체험 시점으로 전환하라. 행동이 서술적이지 않고 체험적이게 해야한다. 행위 중심의 문장과 감정,생각을 섞되 절제.

중요 "나는"이나 "주어"를 직접적으로 쓰지 않고도, 주체의 행위가 자연스럽게 드러나게 표현해주세요.

장면 간의 맥락 연결어를 자연스럽게 추가해 시간 흐름을 암시하라.

감정은 한순간이 아니라 시간 속에서 변화하는 느낌으로 조정하라.

음식명 사용은 분석 단계의 기준을 따름: visible_text 있거나 top.conf≥0.75일 때만 명칭, 아니면 재료표현.

사실과 다른 고유명사(요리명·지명) 금지. 보이지 않으면 일반어 유지.

사람이 보이지 않으면 군중 묘사 금지. 소리·냄새 생성 금지.

감정을 구체 감각으로 자연화. 리듬 단조는 문장 길이 변주로 보정.

감정 변화의 원인이 있어야 한다. 감정의 원인과 맥락이 있어야 하므로 짧게라도 이유,상황을 제시해야 한다.

너무 딱딱한 명사구 연쇄, '일상적인 풍경' 같은 추상 표현은 구체로 치환하거나 제거.

문장 길이와 어미를 다양화. '~있었다' 반복을 줄이고 유사한 단어를 사용하며 변화시키거나 필요한 곳만 남김.

감정의 포화가 되지 않도록 한 요소만 남기고 나머지는 암시로 처리해라.

감각적 묘사가 일정한 패턴으로 나오지 않게 리듬을 조정하고 문장 호흡을 다르게 구성하라.

과장/비유/메타표현 금지 유지. 한 단락 유지.

금지구 발견 시 반드시 같은 의미를 '행동'으로 치환해 다시 작성.

각 문장에 대해 "원인->결과"가 드러나는지 점검하고, 누락 시 덕분에/그래서/때문에를 이용해 한 문장을 추가하거나 재배치한다.

문단 전체에 하나의 미세한 감정 변화를 깔고, 첫·중간·마침 문장에 그 변화가 이어지도록 접속부를 보정한다.

현재형·진행형 발견 시 전부 과거형으로 통일한다. 혼용이 보이면 해당 문장 묶음을 두 문장 과거형으로 분해한다.

문장 수: {length_rule}. 톤: {tone or "중립"}.중요.

'[경고]' 표시가 있으면 해당 제약을 절대 위반하지 마라.


[강제 보정 — 시간·연결어]

문장 시작의 시간단어(오전/정오/오후/저녁/밤)를 제거하고 행동으로 치환하라.

time_hint가 '불명'인 프레임에서 생성된 시간단어를 모두 삭제하라.

행동과 감정 사이에 맥락에 맞는 연결어를 사용하라. 맥락이 어색하면 안된다.

감정은 한 문장, 동사/부사 기반의 약한 표현만 유지하라.

프레임 전환마다 그 후/잠시 뒤/이어/다시/곳을 옮겨 중 1개 이상을 삽입한다. 누락 시 자동 삽입하고 리듬을 해치면 위치를 조정한다.

[강제 보정 — 리듬]

12자 이하 단문이 2회 연속이면, 이어지는 문장을 25자 이상 복합문으로 재작성한다.

접속어가 들어간 25자 이상 문장을 최소 2개 유지한다(그래서/때문에/덕분에/하지만/그리고/그러다가 등).

동일 어미 반복 제어: '~했다.' 3회 연속 금지. 세 번째는 이유·조건·대조 접속을 포함해 변형한다.

문단 종료는 10~16자 짧은 문장으로 마무리한다.

[절제 적용]

'[경고]' 표시가 있으면 해당 제약을 절대 위반하지 마라.

감각 언급 총 2개 초과 시 초과분 삭제.

감정 직접 표현은 1문장 이하. 나머지는 행동으로 암시.

관찰 중심이 아닌 실제 감정의 원인인과 맥락이 있어야한다. 따라서 감정의 변화가 느껴져야 한다.

금지어 제거: 지글지글, 노릇노릇, 바삭, 촉촉, 입안 가득, 코끝, 스며들다, 감돌다, 간질이다, 한껏, 가득, 벅차다, 특별했다, 미소가 지어졌다.

비유·수사 제거. 추상어('일상적인 풍경/특별한 시간')는 구체로 치환하거나 삭제.

보이지 않으면 절대 추측하거나 대체 이름을 만들지 말 것.

패턴 중복 제어:~했다.가 3회 연속이면 네 번째 문장은 이유절 포함 복합문으로 재작성한다.


[출력]

한 단락만. 불필요한 수식어 축소. 관찰 나열 금지.
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

# 금지구 확장
TRIM_PHRASES = [
    "일상적인 분위기로 가득 차 있었다",
    "시각적으로도 즐거움을 주었다",
    "상업적인 느낌을 더했다",
    "가지런히 놓인 모습이",
    "식욕을 자극",
    "편안함을 가져다주었다",
    "글자가 눈에 띄었다.",
    "속도가 느려졌다",
]

# 패턴 치환 추가
REPORT_PATTERNS = [
    (r"테이블 위에 [^\.]+ 준비되어 있었다", "앞으로 당겨 놓고 자리를 정리했다"),
    (r"눈에 들어왔다", "앞으로 당겨 살폈다"),
    (r"향이 [^\.]+ 자극[^\.]*", "그릇 가까이에서 김이 올랐다"),
    (r"한 입 먹고 나니 [^\.]+", "한 입 먹고 속도가 느려졌다"),
]
def soften_report_tone(text: str) -> str:
    if not text:
        return text
    t = text
    for p in TRIM_PHRASES:
        t = t.replace(p, "")
    for pat, rep in REPORT_PATTERNS:
        t = re.sub(pat, rep, t)
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

말하듯 써라. 짧고 긴 문장 섞기.

시제는 모두 과거형으로 통일.

행동+감각 중심. 과장 금지.

메타표현·날짜·파일명 금지. 성별/인원수 추정 금지.

54문장 준수.

톤: {tone or "중립"}.

한 단락만 출력.
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
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
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
            target_date = (request.form.get("targetDate") or "").strip()  # [추가]
            photos = json.loads(request.form.get("photosSummary") or "[]")
            files = request.files.getlist("images")

            # Stage1 투입 이미지 수 컷 (추가)
            files = files[:min(len(files), STAGE1_TOP_N, MAX_IMAGES)]

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

            # [추가] Vision 단계 content_filter에 걸린 경우 바로 차단
            if analysis and analysis.get("unsafe"):
                return jsonify({
                    "ok": True,
                    "body": "부적절한 내용이 감지되어 일기를 생성하지 않았습니다.",
                    "category": "general_single",
                    "used": "unsafe_filtered_vision",
                    "moderation": analysis,
                })

            if target_date:  # [추가]
                try:
                    analysis["date_sequence"] = _shift_date_sequence(analysis.get("date_sequence") or [], target_date)
                    analysis["date_anchor"] = {"mode": "user_target", "target_date": target_date}
                except Exception as _e:
                    analysis["date_anchor_error"] = str(_e)

            # [추가] 안전성 필터
            is_safe, mod_debug = is_content_safe_for_diary(analysis)
            if not is_safe:
                return jsonify({
                    "ok": True,
                    "body": "부적절한 내용이 감지되어 일기를 생성하지 않았습니다.",
                    "category": "general_single",
                    "used": "unsafe_filtered",
                    "moderation": mod_debug,
                })

            frames_len = len((analysis or {}).get("frames") or [])

            if analysis and frames_len > 1 and is_food_dominant_multi(analysis):
                try:
                    analysis = enrich_food_structured_for_multi(analysis, images=images, photos_summary=photos)
                    category_hint = "food_multi"
                except Exception as _e:
                    analysis["food_multi_enrich_error"] = str(_e)
                    category_hint = "journey_multi"
            else:
                category_hint = "journey_multi" if (analysis and frames_len > 1) else "general_single"

            # --- ALT 교차검증 스킵 판단 (추가) ---
            food_score = _food_likelihood_score(analysis)
            use_alt = True
            if ALT_SKIP_IF_LOW_FOOD and (food_score < ALT_LOW_FOOD_THRESH):
                use_alt = False

            if use_alt:
                # 교차검증 단계
                selected_draft, cv_debug = select_draft_via_cross_validation(analysis, tone, category_hint)
            else:
                # ALT 스킵: 기본 모델 한 번만 호출
                selected_draft = draft_diary(analysis, tone, category_hint, text_model=MODEL_TEXT)
                cv_debug = {"used": "primary_only", "reason": "low_food_likelihood", "food_score": food_score}

            # --- 보정 단계 조건부 스킵 (추가) ---
            if REFINE_SKIP_IF_SHORT and len((selected_draft or "").strip()) < REFINE_MIN_CHARS:
                final_text = selected_draft
                if isinstance(cv_debug, dict):
                    cv_debug["refine"] = "skipped_short_draft"
            else:
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
                    "food_fusion": (analysis or {}).get("food_fusion", {}),  # 추가 노출
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
        target_date = (data.get("targetDate") or "").strip()  # [추가]
        images_raw = (data.get("images") or [])[:MAX_IMAGES]
        # Stage1 투입 이미지 수 컷 (추가)
        images_raw = images_raw[:min(len(images_raw), STAGE1_TOP_N, MAX_IMAGES)]
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

                # [추가] Vision 단계 content_filter에 걸린 경우 바로 차단
                if analysis and analysis.get("unsafe"):
                    return jsonify({
                        "ok": True,
                        "body": "부적절한 내용이 감지되어 일기를 생성하지 않았습니다.",
                        "category": "general_single",
                        "used": "unsafe_filtered_vision",
                        "moderation": analysis,
                    })

                if target_date:  # [추가]
                    try:
                        analysis["date_sequence"] = _shift_date_sequence(analysis.get("date_sequence") or [], target_date)
                        analysis["date_anchor"] = {"mode": "user_target", "target_date": target_date}
                    except Exception as _e:
                        analysis["date_anchor_error"] = str(_e)

                # [추가] 안전성 필터
                is_safe, mod_debug = is_content_safe_for_diary(analysis)
                if not is_safe:
                    return jsonify({
                        "ok": True,
                        "body": "부적절한 내용이 감지되어 일기를 생성하지 않았습니다.",
                        "category": "general_single",
                        "used": "unsafe_filtered",
                        "moderation": mod_debug,
                    })

                frames_len = len((analysis or {}).get("frames") or [])

                if analysis and frames_len > 1 and is_food_dominant_multi(analysis):
                    try:
                        analysis = enrich_food_structured_for_multi(analysis, images=images, photos_summary=photos)
                        category_hint = "food_multi"
                    except Exception as _e:
                        analysis["food_multi_enrich_error"] = str(_e)
                        category_hint = "journey_multi"
                else:
                    category_hint = "journey_multi" if (analysis and frames_len > 1) else "general_single"

                # --- ALT 교차검증 스킵 판단 (추가) ---
                food_score = _food_likelihood_score(analysis)
                use_alt = True
                if ALT_SKIP_IF_LOW_FOOD and (food_score < ALT_LOW_FOOD_THRESH):
                    use_alt = False

                if use_alt:
                    # 교차검증 단계
                    selected_draft, cv_debug = select_draft_via_cross_validation(analysis, tone, category_hint)
                else:
                    # ALT 스킵: 기본 모델 한 번만 호출
                    selected_draft = draft_diary(analysis, tone, category_hint, text_model=MODEL_TEXT)
                    cv_debug = {"used": "primary_only", "reason": "low_food_likelihood", "food_score": food_score}

                # --- 보정 단계 조건부 스킵 (추가) ---
                if REFINE_SKIP_IF_SHORT and len((selected_draft or "").strip()) < REFINE_MIN_CHARS:
                    final_text = selected_draft
                    if isinstance(cv_debug, dict):
                        cv_debug["refine"] = "skipped_short_draft"
                else:
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
                        "food_fusion": (analysis or {}).get("food_fusion", {}),  # 추가 노출
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

# # ---------------- 실행 ----------------
# if __name__ == "__main__":
#     print("\n===========================================")
#     print("서버 시작 → http://127.0.0.1:5000")
#     print("ALT_TEXT_MODEL =", ALT_TEXT_MODEL)
#     print("===========================================\n")
#     app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))  # ← 10000으로 변경!
    print(f"\n{'='*60}")
    print(f"서버 시작 → 0.0.0.0:{port}")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
