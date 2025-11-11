(function () {
  "use strict";

  // ================== 설정 ==================
  const API_URL = "http://127.0.0.1:5000/api/auto-diary";
  const FOOD_HINTS = [
    "food","meal","lunch","dinner","breakfast","cafe","coffee","cake","bread","noodle","ramen","pizza","burger","pasta","sushi",
    "식당","밥","점심","저녁","아침","카페","커피","케이크","빵","라면","피자","버거","파스타","스시",
  ];
  const MAX_UPLOAD = 5;

  // ================== 유틸 ==================
  const $ = (s, p = document) => p.querySelector(s);
  const $$ = (s, p = document) => Array.from(p.querySelectorAll(s));
  function saveLS(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } }
  function loadLS(k, f) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : f; } catch { return f; } }
  function newId() { return Math.random().toString(36).slice(2) + Date.now().toString(36); }
  function getMonthKey(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function parseYMD(s) { const [y, m, d] = (s || "").split("-").map((x) => parseInt(x, 10)); return { y, m, d }; }

  // --- [추가] exifr 동적 로더 + 원본 base64 변환 + ISO 포맷터 ---
  let exifrReady = null;
  function loadExifr() {
    if (exifrReady) return exifrReady;
    exifrReady = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://unpkg.com/exifr/dist/full.umd.js";
      s.onload = () => resolve(true);
      s.onerror = () => reject(new Error("exifr load failed"));
      document.head.appendChild(s);
    });
    return exifrReady;
  }
  async function fileToBase64Original(file) {
    const buf = await file.arrayBuffer(); // 원본 바이트 보존
    let binary = "";
    const bytes = new Uint8Array(buf);
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return "data:" + (file.type || "image/jpeg") + ";base64," + btoa(binary);
  }
  function toISOish(dt) {
    const z = (n) => String(n).padStart(2, "0");
    return `${dt.getFullYear()}-${z(dt.getMonth() + 1)}-${z(dt.getDate())} ${z(dt.getHours())}:${z(dt.getMinutes())}:${z(dt.getSeconds())}`;
  }

  // ================== IndexedDB ==================
  const HAS_INDEXED_DB = typeof indexedDB !== "undefined";
  const IDB_DB_NAME = "snaplog-db";
  const IDB_STORE_NAME = "entries";
  function openIDB() {
    if (!HAS_INDEXED_DB) return Promise.reject(new Error("indexedDB not supported"));
    return new Promise((resolve, reject) => {
      try {
        const req = indexedDB.open(IDB_DB_NAME, 1);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains(IDB_STORE_NAME)) {
            db.createObjectStore(IDB_STORE_NAME, { keyPath: "id" });
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error || new Error("indexedDB open failed"));
      } catch (e) { reject(e); }
    });
  }
  async function saveEntryToIDB(entry) {
    if (!HAS_INDEXED_DB || !entry) return false;
    const db = await openIDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, "readwrite");
      const store = tx.objectStore(IDB_STORE_NAME);
      store.put(entry);
      tx.oncomplete = () => { db.close(); resolve(true); };
      tx.onabort = tx.onerror = () => { const err = tx.error || new Error("idb save failed"); db.close(); reject(err); };
    }).catch(() => false);
  }
  async function getAllFromIDB() {
    if (!HAS_INDEXED_DB) return [];
    const db = await openIDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, "readonly");
      const store = tx.objectStore(IDB_STORE_NAME);
      const req = store.getAll();
      req.onsuccess = () => { resolve(req.result || []); };
      req.onerror = () => { reject(req.error || new Error("idb getAll failed")); };
      tx.oncomplete = () => db.close();
      tx.onabort = tx.onerror = () => { db.close(); };
    }).catch(() => []);
  }
  async function deleteEntryFromIDB(id) {
    if (!HAS_INDEXED_DB) return false;
    const db = await openIDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, "readwrite");
      const store = tx.objectStore(IDB_STORE_NAME);
      store.delete(id);
      tx.oncomplete = () => { db.close(); resolve(true); };
      tx.onabort = tx.onerror = () => { const err = tx.error || new Error("idb delete failed"); db.close(); reject(err); };
    }).catch(() => false);
  }
  function shrinkEntryForLocal(entry) {
    if (!entry) return entry;
    const { photos, photo, ...rest } = entry;
    return { ...rest, photo: "", photos: [] };
  }
  if (typeof window !== "undefined") {
    if (!window.getAllFromIDB) window.getAllFromIDB = getAllFromIDB;
    if (!window.deleteEntryFromIDB) window.deleteEntryFromIDB = deleteEntryFromIDB;
    if (!window.saveEntryToIDB) window.saveEntryToIDB = saveEntryToIDB;
  }

  // 이미지 축소(JPEG) → dataURL (미리보기용)
  async function downscaleToDataURL(file, maxSide = 1280, quality = 0.8) {
    const img = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => {
        const i = new Image();
        i.onload = () => res(i);
        i.onerror = rej;
        i.src = fr.result;
      };
      fr.onerror = rej;
      fr.readAsDataURL(file);
    });
    const w = img.naturalWidth, h = img.naturalHeight;
    const ratio = w > h ? maxSide / w : maxSide / h;
    const nw = ratio < 1 ? Math.round(w * ratio) : w;
    const nh = ratio < 1 ? Math.round(h * ratio) : h;
    const canvas = document.createElement("canvas");
    canvas.width = nw; canvas.height = nh;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, nw, nh);
    return canvas.toDataURL("image/jpeg", quality);
  }

  // ================== 상태 ==================
  const state = {
    entries: loadLS("entries", []),
    theme: loadLS("theme", "light"),
    cursor: null,
    cal: new Date(),
    tempPhotos: [],
    tempNames: [],
    repIndex: 0,
    viewIndex: 0,
    tone: "중립",
    // 변경: 서버 전송용 원본 + takenAt 포함
    photoItems: [
      /* { previewDataURL, originalDataURL, name, takenAtStr, shotAt } */
    ],
    uploadToken: 0,
  };

  async function hydrateEntriesFromIDB() {
    try {
      const idbEntries = await getAllFromIDB();
      if (!idbEntries || !idbEntries.length) return;
      const merged = new Map();
      state.entries.forEach((e) => merged.set(e.id, e));
      idbEntries.forEach((e) => merged.set(e.id, e));
      state.entries = Array.from(merged.values());
      renderAll();
    } catch {}
  }

  // ================== 분류/라벨 ==================
  function classifyCategory(fileNames) {
    if (!fileNames || !fileNames.length) return "general_single";
    if (fileNames.length === 1) {
      const n = (fileNames[0] || "").toLowerCase();
      const isFood = FOOD_HINTS.some((k) => n.includes(k));
      return isFood ? "food_single" : "general_single";
    }
    return "journey_multi";
  }
  function normalizeTimeLabel(idx, total) {
    if (total <= 1) return "불명";
    const ratio = idx / (total - 1);
    if (ratio < 0.25) return "오전";
    if (ratio < 0.5) return "정오";
    if (ratio < 0.75) return "오후";
    return "저녁";
  }

  // ================== Summary ==================
  function buildPhotosSummary(state) {
    const total = state.tempPhotos.length;
    const now = new Date();
    const ymd = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const arr = [];
    for (let i = 0; i < total; i++) {
      arr.push({
        place: "",
        time: `${ymd} ${normalizeTimeLabel(i, total)}`,
        weather: "",
        desc: "",
      });
    }
    return arr;
  }

  // ================== API ==================
  const AUTO_API_TIMEOUT_MS = 90000;
  async function callAutoDiaryAPI(images, photosSummary, tone, imagesMeta) {
    if (!API_URL) return null;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort("timeout"), AUTO_API_TIMEOUT_MS);
    const payload = { tone, images, photosSummary, imagesMeta };
    try {
      const r = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
      clearTimeout(t);
      const text = await r.text();
      let data = null;
      try { data = JSON.parse(text); } catch (e) { console.error("JSON parse error:", e); }
      if (!r.ok) {
        const msg = data?.error || data?.message || `HTTP ${r.status}: ` + text.slice(0, 200);
        alert("자동생성 실패: " + msg);
        return null;
      }
      if (data && data.ok) return data;
      alert("자동생성 실패: " + (data?.error || "unknown"));
      return null;
    } catch (e) {
      clearTimeout(t);
      alert("자동생성 서버 응답이 없습니다: " + e);
      return null;
    }
  }

  // ================== Fallback ==================
  function fallbackGenerate(photosSummary, cat) {
    const n = photosSummary.length;
    const s = (x) => x || "";
    if (cat === "journey_multi" && n >= 2) {
      const a = photosSummary;
      const t0 = s(a[0].time).split(" ").pop();
      const first = s(a[0].place) ? `${s(a[0].place)}에서 ${t0}에 하루를 열었다.` : `${t0}에 하루를 열었다.`;
      const parts = [first];
      for (let i = 1; i < n - 1; i++) {
        const seg = [];
        if (s(a[i].place)) seg.push(`${s(a[i].place)}로 옮기며`);
        if (s(a[i].desc)) seg.push(`${s(a[i].desc)}을 지나쳤다`);
        parts.push(seg.length ? seg.join(" ") + "." : "잠시 걸음을 늦췄다.");
      }
      parts.push(`${s(a[n - 1].place) || "주변"}의 빛이 천천히 바뀌었다.`);
      parts.push("남은 소리와 온기가 조용히 정리되었다.");
      return parts.slice(0, 7).join("\n");
    } else {
      const p = photosSummary[0] || { place: "", time: "오후", weather: "", desc: "" };
      const tpart = s(p.time).split(" ").pop();
      const first = s(p.place) ? `${s(p.place)}에서 ${tpart}에 잠시 멈췄다.` : `${tpart}에 잠시 멈췄다.`;
      const parts = [first];
      if (s(p.desc)) parts.push(`${s(p.desc)}이 눈에 들어왔다.`);
      parts.push("숨을 고르니 공간의 결이 또렷해졌다.");
      parts.push("짧은 고요가 오늘의 끝을 부드럽게 덮었다.");
      return parts.slice(0, 4).join("\n");
    }
  }

  // ================== 테마 ==================
  function applyTheme(t) { document.documentElement.setAttribute("data-theme", t); saveLS("theme", t); }

  // ================== 렌더러 ==================
  function renderStats() {
    try {
      const all = state.entries.length;
      const monthKey = getMonthKey(new Date());
      const month = state.entries.filter((e) => (e.date || "").startsWith(monthKey)).length;
      const photos = state.entries.filter((e) => Array.isArray(e.photos) ? e.photos.length : e.photo ? 1 : 0).length;
      const a = $("#statAll"), m = $("#statMonth"), p = $("#statPhotos");
      if (a) a.textContent = `전체 ${all}`;
      if (m) m.textContent = `이번 달 ${month}`;
      if (p) p.textContent = `사진 ${photos}`;
    } catch {}
  }
  function renderRecent() {
    try {
      const box = $("#recent");
      if (!box) return;
      box.innerHTML = "";
      state.entries.slice().reverse().slice(0, 50).forEach((e) => {
        const it = document.createElement("div");
        it.className = "item";
        const left = document.createElement("div");
        left.innerHTML = `<div><strong>${e.title || "제목 없음"}</strong></div><div class="small">${e.date}</div>`;
        const right = document.createElement("button");
        right.className = "btn ghost";
        right.textContent = state.cursor === e.id ? "닫기" : "보기";
        right.onclick = () => {
          if (state.cursor === e.id) { resetComposer(); renderRecent(); }
          else { loadEntry(e.id); renderRecent(); }
        };
        it.append(left, right);
        box.appendChild(it);
      });
    } catch {}
  }
  function renderCalendar() {
    try {
      const cal = $("#calendar");
      if (!cal) return;
      cal.innerHTML = "";
      const ym = $("#ym");
      const cur = new Date(state.cal.getFullYear(), state.cal.getMonth(), 1);
      if (ym) ym.textContent = `${cur.getFullYear()}년 ${String(cur.getMonth() + 1).padStart(2, "0")}월`;
      const daysHeader = ["일","월","화","수","목","금","토"];
      daysHeader.forEach((d) => { const h = document.createElement("div"); h.className = "cell head"; h.textContent = d; cal.appendChild(h); });
      const firstDay = new Date(cur.getFullYear(), cur.getMonth(), 1).getDay();
      const lastDate = new Date(cur.getFullYear(), cur.getMonth() + 1, 0).getDate();
      for (let i = 0; i < firstDay; i++) { const e = document.createElement("div"); e.className = "cell head"; e.style.visibility = "hidden"; cal.appendChild(e); }
      const saved = new Set(state.entries.filter((e) => {
        if (!e.date) return false;
        const { y, m } = parseYMD(e.date);
        return y === cur.getFullYear() && m === cur.getMonth() + 1;
      }).map((e) => parseYMD(e.date).d));
      const today = new Date();
      for (let d = 1; d <= lastDate; d++) {
        const cell = document.createElement("div");
        cell.className = "cell"; cell.textContent = String(d);
        if (saved.has(d)) cell.classList.add("saved");
        if (d === today.getDate() && cur.getMonth() === today.getMonth() && cur.getFullYear() === today.getFullYear()) cell.classList.add("today");
        cell.onclick = () => {
          state.cal = new Date(cur.getFullYear(), cur.getMonth(), d);
          const key = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
          const hit = state.entries.find((e) => e.date === key);
          state.cursor = hit ? hit.id : null;
          reflectCurrent();
        };
        cal.appendChild(cell);
      }
    } catch {}
  }
  function reflectCurrent() {
    try {
      const img = $("#preview");
      const ph = $("#previewWrap .ph");
      const pw = $("#previewWrap");
      if (!state.cursor) {
        if (img) { img.src = ""; img.style.display = "none"; }
        if (ph) ph.style.display = "grid";
        if (pw) pw.classList.remove("has-image");
        $("#text").value = "";
        const ti = $("#title"); if (ti) ti.value = "";
        state.tempPhotos = []; state.tempNames = []; state.repIndex = 0; state.viewIndex = 0;
        renderGallery(); return;
      }
      const e = state.entries.find((x) => x.id === state.cursor);
      if (!e) return;
      state.tempPhotos = Array.isArray(e.photos) ? e.photos.slice(0, MAX_UPLOAD) : e.photo ? [e.photo] : [];
      state.tempNames = e.notes?.map((n) => n?.desc || "") || [];
      state.repIndex = e.repIndex || 0; state.viewIndex = state.repIndex;
      if (state.tempPhotos.length && img) {
        img.onload = () => { img.style.display = "block"; if (ph) ph.style.display = "none"; if (pw) pw.classList.add("has-image"); };
        img.src = state.tempPhotos[state.repIndex];
      } else {
        if (img) { img.src = ""; img.style.display = "none"; }
        if (ph) ph.style.display = "grid";
        if (pw) pw.classList.remove("has-image");
      }
      $("#text").value = e.body || "";
      const ti = $("#title"); if (ti) ti.value = e.title || "";
      renderGallery();
    } catch {}
  }
  function loadEntry(id) { state.cursor = id; reflectCurrent(); }
  function renderGallery() {
    try {
      const cnt = $("#camCount");
      const thumbs = $("#thumbs");
      if (!thumbs) return;
      if (cnt) cnt.textContent = String(state.tempPhotos.length);
      thumbs.innerHTML = "";
      state.tempPhotos.forEach((src, i) => {
        const t = document.createElement("div");
        t.className = "thumb";
        const im = document.createElement("img"); im.src = src;
        const x = document.createElement("div"); x.className = "x"; x.textContent = "×";
        x.onclick = () => {
          state.photoItems.splice(i, 1);
          state.tempPhotos.splice(i, 1);
          if (state.tempNames) state.tempNames.splice(i, 1);
          if (state.repIndex >= state.tempPhotos.length) state.repIndex = Math.max(0, state.tempPhotos.length - 1);
          state.viewIndex = Math.min(state.viewIndex, state.tempPhotos.length - 1);
          if (state.viewIndex < 0) state.viewIndex = 0;
          renderGallery(); updatePreviewFromView();
        };
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = i === state.repIndex ? "대표사진" : "대표로";
        badge.onclick = () => { state.repIndex = i; state.viewIndex = i; updatePreviewFromView(); renderGallery(); };
        t.append(im, x, badge);
        thumbs.appendChild(t);
      });
    } catch {}
  }
  function updatePreviewFromView() {
    const img = $("#preview");
    const ph = $("#previewWrap .ph");
    const pw = $("#previewWrap");
    const rep = state.tempPhotos[state.viewIndex];
    if (rep && img) {
      img.onload = () => { img.style.display = "block"; if (ph) ph.style.display = "none"; if (pw) pw.classList.add("has-image"); };
      img.src = rep;
    } else {
      if (img) { img.src = ""; img.style.display = "none"; }
      if (ph) ph.style.display = "grid";
      if (pw) pw.classList.remove("has-image");
    }
  }
  function resetComposer() {
    state.cursor = null;
    state.tempPhotos = []; state.tempNames = [];
    state.repIndex = 0; state.viewIndex = 0;
    state.photoItems = []; state.uploadToken += 1;
    const img = $("#preview"); const ph = $("#previewWrap .ph"); const pw = $("#previewWrap");
    const ta = $("#text"); const ti = $("#title"); const fi = $("#file");
    if (fi) fi.value = "";
    if (img) { img.src = ""; img.style.display = "none"; }
    if (ph) ph.style.display = "grid";
    if (pw) pw.classList.remove("has-image");
    if (ta) ta.value = "";
    if (ti) ti.value = "";
    renderGallery();
  }
  function renderAll() { renderStats(); renderRecent(); renderCalendar(); reflectCurrent(); }

  // ================== 초기 바인딩 ==================
  window.addEventListener("DOMContentLoaded", () => {
    applyTheme(state.theme);
    const darkToggleApp = $("#darkToggleApp");
    if (darkToggleApp) {
      darkToggleApp.checked = state.theme === "dark";
      darkToggleApp.addEventListener("change", () => {
        state.theme = darkToggleApp.checked ? "dark" : "light";
        applyTheme(state.theme);
      });
    }

    const autoModal = $("#autoModal");
    const autoModalText = autoModal ? autoModal.querySelector(".modal-text") : null;
    const toggleAutoModal = (show, text) => {
      if (!autoModal) return;
      if (text && autoModalText) autoModalText.textContent = text;
      if (show) autoModal.classList.add("active");
      else autoModal.classList.remove("active");
    };

    const intro = $("#intro"); const app = $("#app"); const startBtn = $("#startBtn");
    if (intro) intro.style.display = "block";
    if (app) app.style.display = "none";
    if (startBtn) {
      startBtn.addEventListener("click", () => {
        resetComposer();
        if (intro) intro.style.display = "none";
        if (app) app.style.display = "block";
        renderAll();
      });
    }
    try { renderCalendar(); } catch {}

    const prevM = $("#prevM"), nextM = $("#nextM");
    if (prevM) prevM.addEventListener("click", () => { state.cal = new Date(state.cal.getFullYear(), state.cal.getMonth() - 1, 1); renderCalendar(); });
    if (nextM) nextM.addEventListener("click", () => { state.cal = new Date(state.cal.getFullYear(), state.cal.getMonth() + 1, 1); renderCalendar(); });

    const cameraTile = $("#cameraTile");
    if (cameraTile) cameraTile.addEventListener("click", () => $("#file").click());

    // 파일 업로드: EXIF 촬영시각 추출(takenAt) + 원본 base64(서버용) + 축소본(미리보기)
    const fileInput = $("#file");
    if (fileInput) {
      fileInput.addEventListener("change", async (ev) => {
        const token = ++state.uploadToken;
        const files = Array.from(ev.target.files || []);
        if (!files.length) return;
        const remain = MAX_UPLOAD - state.photoItems.length;
        const pick = files.slice(0, remain);

        // exifr 로드
        try { await loadExifr(); } catch (e) { console.warn("exifr load failed", e); }

        for (const f of pick) {
          let takenAtStr = null;
          let shotAtMs = null;

          // EXIF → takenAt
          try {
            if (window.exifr) {
              const exif = await window.exifr.parse(f, { tiff: true, ifd0: true, exif: true });
              const dt = exif?.DateTimeOriginal || exif?.CreateDate || exif?.ModifyDate || null;
              if (dt instanceof Date) {
                shotAtMs = +dt;
                takenAtStr = toISOish(dt);
              }
            }
          } catch (e) { console.warn("exif parse fail", e); }

          if (!shotAtMs) shotAtMs = f.lastModified || Date.now();
          if (!takenAtStr) {
            const dt = new Date(shotAtMs);
            takenAtStr = toISOish(dt);
          }

          // 서버 전송용: 원본 base64
          let originalDataURL = "";
          try {
            originalDataURL = await fileToBase64Original(f); // EXIF 보존
          } catch (e) { console.warn("fileToBase64Original fail", e); continue; }

          // 미리보기용: 축소본
          let previewDataURL = "";
          try {
            previewDataURL = await downscaleToDataURL(f, 1280, 0.8);
          } catch (e) { console.warn("downscale fail", e); previewDataURL = originalDataURL; }

          if (token !== state.uploadToken) continue;
          state.photoItems.push({
            previewDataURL,
            originalDataURL,
            name: f.name || "",
            takenAtStr,
            shotAt: shotAtMs,
          });
        }

        if (token !== state.uploadToken) return;

        // 촬영시각 정렬
        state.photoItems.sort((a, b) => (a.shotAt || 0) - (b.shotAt || 0));
        state.tempPhotos = state.photoItems.map((p) => p.previewDataURL);
        state.tempNames = state.photoItems.map((p) => p.name);
        state.repIndex = 0; state.viewIndex = 0;
        renderGallery(); updatePreviewFromView();

        console.log("photoItems:", state.photoItems.map((p) => ({
          name: p.name, takenAt: p.takenAtStr, shotAt: p.shotAt
        })));
      });
    }

    // 자동생성
    const autoBtn = $("#autoBtn");
    if (autoBtn) {
      autoBtn.addEventListener("click", async () => {
        if (!state.photoItems || state.photoItems.length === 0) {
          alert("직접 입력하시거나 사진을 넣어주세요");
          return;
        }

        // 서버에는 원본 + takenAt 포함 객체로 전송
        const images = state.photoItems.slice(0, MAX_UPLOAD).map((p) => ({
          data: p.originalDataURL,
          takenAt: p.takenAtStr,
          filename: p.name || ""
        }));

        // imagesMeta는 유지(호환)
        const imagesMeta = state.photoItems.slice(0, MAX_UPLOAD).map((p) => ({
          shotAt: p.shotAt
        }));

        const photosSummary = buildPhotosSummary(state);
        const tone = state.tone || "중립";

        toggleAutoModal(true, "자동생성 중...");
        try {
          const api = await callAutoDiaryAPI(images, photosSummary, tone, imagesMeta);
          if (!api) return;
          const category = classifyCategory(state.tempNames || []);
          const resultText = api.body || fallbackGenerate(photosSummary, category);
          const ta = $("#text"); if (ta) ta.value = resultText;
        } finally { toggleAutoModal(false); }
      });
    }

    // 저장
    const saveBtn = $("#saveBtn");
    if (saveBtn) {
      saveBtn.addEventListener("click", async (ev) => {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }
        const body = ($("#text")?.value || "").trim();
        if (!body) { alert("일기 내용이 비어 있습니다."); return; }

        const ti = $("#title");
        const title = (ti && ti.value ? ti.value : "제목 없음").slice(0, 20);
        const now = new Date();

        const entry = {
          id: state.cursor || newId(),
          title,
          body,
          photo: state.tempPhotos[state.repIndex] || "",
          photos: state.tempPhotos.slice(0, MAX_UPLOAD),
          repIndex: state.repIndex,
          notes: buildPhotosSummary(state),
          tone: state.tone || "중립",
          date: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`,
          ts: now.getTime(),
        };

        const previousEntries = state.entries.slice();
        const idx = state.entries.findIndex((e) => e.id === entry.id);
        if (idx >= 0) state.entries[idx] = entry; else state.entries.push(entry);

        const metaEntries = state.entries.map(shrinkEntryForLocal);
        const stored = saveLS("entries", metaEntries);
        if (!stored) {
          state.entries = previousEntries;
          alert("로컬 저장 공간이 부족해 저장에 실패했습니다. 다른 기록을 정리한 뒤 다시 시도해 주세요.");
          return;
        }

        try { await saveEntryToIDB(entry); } catch {}

        state.cursor = entry.id;
        state.photoItems = entry.photos.map((dataURL, i) => ({
          // 저장 후 편집 복귀용. 원본은 다시 업로드 시 재계산됨.
          previewDataURL: dataURL,
          originalDataURL: dataURL,
          name: state.tempNames[i] || "",
          takenAtStr: state.photoItems[i]?.takenAtStr || toISOish(now),
          shotAt: state.photoItems[i]?.shotAt || now.getTime(),
        }));
        renderRecent(); renderStats(); renderCalendar(); reflectCurrent();
      });
    }

    // 삭제
    const delBtn = $("#delBtn");
    if (delBtn) {
      delBtn.addEventListener("click", async () => {
        if (!state.cursor) { resetComposer(); return; }
        const targetId = state.cursor;
        state.entries = state.entries.filter((e) => e.id !== targetId);
        saveLS("entries", state.entries.map(shrinkEntryForLocal));
        try { await deleteEntryFromIDB(targetId); } catch {}
        resetComposer(); renderAll();
      });
    }

    // 캐러셀
    const prev = $("#navPrev"), next = $("#navNext");
    if (prev) prev.addEventListener("click", () => {
      if (state.tempPhotos.length) {
        state.viewIndex = (state.viewIndex - 1 + state.tempPhotos.length) % state.tempPhotos.length;
        updatePreviewFromView();
      }
    });
    if (next) next.addEventListener("click", () => {
      if (state.tempPhotos.length) {
        state.viewIndex = (state.viewIndex + 1) % state.tempPhotos.length;
        updatePreviewFromView();
      }
    });

    hydrateEntriesFromIDB();
  });
})();