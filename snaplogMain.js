(function(){
    "use strict";
  
    // ================== 설정 ==================
    const API_URL = "https://snaplog.onrender.com/api/auto-diary";
    const FOOD_HINTS = [
      "food","meal","lunch","dinner","breakfast","cafe","coffee","cake","bread",
      "noodle","ramen","pizza","burger","pasta","sushi","식당","밥","점심","저녁",
      "아침","카페","커피","케이크","빵","라면","피자","버거","파스타","스시"
    ];
    const MAX_UPLOAD = 5;

    let toggleAutoModal = () => {};
          
    
      // ================== 유틸 ==================
      const $ = (s, p = document) => p.querySelector(s);
      const $$ = (s, p = document) => Array.from(p.querySelectorAll(s));
      function saveLS(k, v) {
          try {
          localStorage.setItem(k, JSON.stringify(v));
          } catch (e) {
          console.warn("save fail", k, e);
          }
      }
      function loadLS(k, f) {
          try {
          const v = localStorage.getItem(k);
          return v ? JSON.parse(v) : f;
          } catch (e) {
          return f;
          }
      }
      function newId() {
          return Math.random().toString(36).slice(2) + Date.now().toString(36);
      }
      function getMonthKey(d) {
          return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
      }
      function parseYMD(s) {
          const [y, m, d] = (s || "").split("-").map((x) => parseInt(x, 10));
          return { y, m, d };
      }
      function formatDate(d) {
          return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      }
  
      // 이미지 축소(JPEG) → dataURL
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
          const w = img.naturalWidth,
          h = img.naturalHeight;
          const ratio = w > h ? maxSide / w : maxSide / h;
          const nw = ratio < 1 ? Math.round(w * ratio) : w;
          const nh = ratio < 1 ? Math.round(h * ratio) : h;
          const canvas = document.createElement("canvas");
          canvas.width = nw;
          canvas.height = nh;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, nw, nh);
          return canvas.toDataURL("image/jpeg", quality);
      }
  
      // ================== IndexedDB helper ==================
      function openDB(){
          return new Promise((resolve,reject)=>{
          const r = indexedDB.open('snaplog-db',1);
          r.onupgradeneeded = ()=>{ 
              const db = r.result; 
              if(!db.objectStoreNames.contains('entries')) 
              db.createObjectStore('entries',{keyPath:'id'});
          };
          r.onsuccess = ()=>resolve(r.result);
          r.onerror = ()=>reject(r.error);
          });
      }
  
      async function saveEntryToIDB(entry){
          const db = await openDB();
          return new Promise((resolve,reject)=>{
          const tx = db.transaction('entries','readwrite');
          tx.objectStore('entries').put(entry);
          tx.oncomplete = ()=>{ resolve(true); db.close(); };
          tx.onerror = ()=>{ reject(tx.error); db.close(); };
          });
      }
  
      async function getAllFromIDB(){
          const db = await openDB();
          return new Promise((resolve,reject)=>{
          const tx = db.transaction('entries','readonly');
          const req = tx.objectStore('entries').getAll();
          req.onsuccess = ()=>{ resolve(req.result); db.close(); };
          req.onerror = ()=>{ reject(req.error); db.close(); };
          });
      }
  
      async function deleteEntryFromIDB(id){
          const db = await openDB();
          return new Promise((resolve,reject)=>{
          const tx = db.transaction('entries','readwrite');
          tx.objectStore('entries').delete(id);
          tx.oncomplete = ()=>{ resolve(true); db.close(); };
          tx.onerror = ()=>{ reject(tx.error); db.close(); };
          });
      }
  
      // ================== 상태 ==================
      const state = {
          entries: [], 
          cursor: null,
          cal: new Date(),
          selectedDate: new Date(),
          tempPhotos: [],
          tempNames: [],
          repIndex: 0,
          viewIndex: 0,
          tone: "중립",
          photoItems: [],
          theme: loadLS("theme", "light"),
          currentDateEntries: [],
          currentDateEntryIndex: -1,
      };
  
  
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
          if (total <= 1) return "오후";
          const ratio = idx / (total - 1);
          if (ratio < 0.25) return "오전";
          if (ratio < 0.5) return "정오";
          if (ratio < 0.75) return "오후";
          return "저녁";
      }
  
      // ================== Summary ==================
      function buildPhotosSummary(state) {
          const total = state.tempPhotos.length;
          const ymd = formatDate(state.selectedDate);
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
      async function callAutoDiaryAPI(images, photosSummary, tone, imagesMeta) {
          if (!API_URL) return null;
          const ctrl = new AbortController();
          const t = setTimeout(() => ctrl.abort("timeout"), 90000);
  
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
          try {
              data = JSON.parse(text);
          } catch (e) {
              console.error("JSON parse error:", e);
          }
          if (!r.ok) {
              const msg =
              data?.error || data?.message || `HTTP ${r.status}: ` + text.slice(0, 200);
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
          const first = s(a[0].place)
              ? `${s(a[0].place)}에서 ${t0}에 하루를 열었다.`
              : `${t0}에 하루를 열었다.`;
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
          const p = photosSummary[0] || {
              place: "",
              time: "오후",
              weather: "",
              desc: "",
          };
          const tpart = s(p.time).split(" ").pop();
          const first = s(p.place)
              ? `${s(p.place)}에서 ${tpart}에 잠시 멈췄다.`
              : `${tpart}에 잠시 멈췄다.`;
          const parts = [first];
          if (s(p.desc)) parts.push(`${s(p.desc)}이 눈에 들어왔다.`);
          parts.push("숨을 고르니 공간의 결이 또렷해졌다.");
          parts.push("짧은 고요가 오늘의 끝을 부드럽게 덮었다.");
          return parts.slice(0, 4).join("\n");
          }
      }
  
  
      // ================== 테마 ==================
      function applyTheme(t) {
          document.documentElement.setAttribute("data-theme", t);
          saveLS("theme", t);
      }
  
      // ================== 선택된 날짜 표시 ==================
      function updateSelectedDateDisplay() {
          const dateEl = $("#selectedDate");
          if (dateEl) {
              const d = state.selectedDate;
              dateEl.textContent = `(${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일)`;
          }
      }
    
      // ✅ 추가: 선택된 날짜의 일기 목록 업데이트 및 개수 표시
      function updateCurrentDateEntries() {
        const key = formatDate(state.selectedDate);
        // 해당 날짜의 모든 일기를 시간순으로 정렬 (최신순)
        state.currentDateEntries = state.entries
            .filter((e) => e.date === key)
            .sort((a, b) => (b.tn || 0) - (a.tn || 0));
        
        // 현재 cursor에 해당하는 일기의 인덱스 찾기
        if (state.cursor) {
            const idx = state.currentDateEntries.findIndex((e) => e.id === state.cursor);
            state.currentDateEntryIndex = idx >= 0 ? idx : -1;
        } else {
            state.currentDateEntryIndex = -1;
        }
        
        // 일기 개수 표시 업데이트 (일기 현재 번호/전체 개수 형식)
        const countEl = $("#diaryCount");
        if (countEl) {
            const total = state.currentDateEntries.length;
            if (total === 0) {
                countEl.textContent = "일기 0";
            } else if (state.currentDateEntryIndex >= 0 && state.currentDateEntryIndex < total) {
                // ✅ 수정: 일기 현재 일기 번호/전체 개수 형식으로 표시
                // 인덱스 0 (가장 최신) = 일기 total/total, 인덱스 1 = 일기 (total-1)/total, ...
                const currentNum = total - state.currentDateEntryIndex;
                countEl.textContent = `일기 ${currentNum}/${total}`;
            } else {
                countEl.textContent = `일기 ${total}`;
            }
        }
        
        // 이전/다음 버튼 활성화 상태 및 숨김 처리 업데이트
        const prevBtn = $("#prevDiary");
        const nextBtn = $("#nextDiary");
        if (prevBtn) {
            // ✅ 수정: ◀ 버튼 = 더 최신 일기로 이동 (인덱스 감소)
            // 인덱스 0(가장 최신 일기)일 때 숨김 처리
            const shouldHide = state.currentDateEntryIndex <= 0 || 
                               state.currentDateEntries.length === 0;
            prevBtn.style.display = shouldHide ? "none" : "inline-flex";
            prevBtn.disabled = shouldHide;
        }
        if (nextBtn) {
            // ✅ 수정: ▶ 버튼 = 더 오래된 일기로 이동 (인덱스 증가)
            // 마지막 인덱스(가장 오래된 일기)일 때 숨김 처리
            const shouldHide = state.currentDateEntryIndex < 0 || 
                               state.currentDateEntryIndex >= state.currentDateEntries.length - 1 ||
                               state.currentDateEntries.length === 0;
            nextBtn.style.display = shouldHide ? "none" : "inline-flex";
            nextBtn.disabled = shouldHide;
        }
    }

       // ================== 렌더러 ==================
      function renderStats() {
          try {
          const all = state.entries.length;
          const monthKey = getMonthKey(new Date());
          const month = state.entries.filter((e) =>
              (e.date || "").startsWith(monthKey)
          ).length;
          const photos = state.entries.filter((e) =>
              Array.isArray(e.photos) ? e.photos.length : e.photo ? 1 : 0
          ).length;
          const a = $("#statAll"),
              m = $("#statMonth"),
              p = $("#statPhotos");
          if (a) a.textContent = `전체 ${all}`;
          if (m) m.textContent = `이번 달 ${month}`;
          if (p) p.textContent = `사진 ${photos}`;
          } catch (e) {
          console.warn("renderStats error", e);
          }
      }
  
      // 최근 일기 최근 작성/저장된 순
      function renderRecent() {
        try {
            const box = $("#recent");
            if (!box) return;
            box.innerHTML = "";

            // 저장된 날짜 기준 내림차순 정렬
            const sortedEntries = state.entries
              .slice()
              .sort((a, b) => (b.tn || 0) - (a.tn || 0)); // 최신 저장 순

            sortedEntries.slice(0, 50).forEach((e) => {
                const it = document.createElement("div");
                it.className = "item";
    
                const left = document.createElement("div");
                left.innerHTML = `<div><strong>${e.title || "제목 없음"}</strong></div><div class="small">${e.date}</div>`;
    
                const right = document.createElement("button");
                right.className = "btn ghost";
                right.textContent = state.cursor === e.id ? "닫기" : "보기";
                right.onclick = () => {
                    if (state.cursor === e.id) {
                        resetComposer();
                        renderRecent();
                    } else {
                        loadEntry(e.id);
                        renderRecent();
                    }
                };
    
                it.append(left, right);
                box.appendChild(it);
            });
        } catch (e) {
            console.warn("renderRecent error", e);
        }
    }
    
      
      function renderCalendar() {
          try {
          const cal = $("#calendar");
          if (!cal) return;
          cal.innerHTML = "";
          const ym = $("#ym");
          const cur = new Date(state.cal.getFullYear(), state.cal.getMonth(), 1);
          if (ym)
              ym.textContent = `${cur.getFullYear()}년 ${String(
              cur.getMonth() + 1
              ).padStart(2, "0")}월`;
  
          const daysHeader = ["일", "월", "화", "수", "목", "금", "토"];
          daysHeader.forEach((d) => {
              const h = document.createElement("div");
              h.className = "cell head";
              h.textContent = d;
              cal.appendChild(h);
          });
  
          const firstDay = new Date(
              cur.getFullYear(),
              cur.getMonth(),
              1
          ).getDay();
          const lastDate = new Date(
              cur.getFullYear(),
              cur.getMonth() + 1,
              0
          ).getDate();
  
          for (let i = 0; i < firstDay; i++) {
              const e = document.createElement("div");
              e.className = "cell head";
              e.style.visibility = "hidden";
              cal.appendChild(e);
          }
  
          const saved = new Set(
              state.entries
              .filter((e) => {
                  if (!e.date) return false;
                  const { y, m } = parseYMD(e.date);
                  return y === cur.getFullYear() && m === cur.getMonth() + 1;
              })
              .map((e) => parseYMD(e.date).d)
          );
  
          const today = new Date();
          const selectedKey = formatDate(state.selectedDate);
          
          for (let d = 1; d <= lastDate; d++) {
              const cell = document.createElement("div");
              cell.className = "cell";
              cell.textContent = String(d);
              
              if (saved.has(d)) cell.classList.add("saved");
              
              if (
              d === today.getDate() &&
              cur.getMonth() === today.getMonth() &&
              cur.getFullYear() === today.getFullYear()
              )
              cell.classList.add("today");
              
              const cellDate = formatDate(new Date(cur.getFullYear(), cur.getMonth(), d));
              if (cellDate === selectedKey) {
                  cell.classList.add("selected");
              }
              
              cell.onclick = () => {
                state.selectedDate = new Date(cur.getFullYear(), cur.getMonth(), d);
                updateSelectedDateDisplay();
                
                const key = formatDate(state.selectedDate);
                // ✅ 수정: 먼저 해당 날짜의 일기 목록을 만들고, 가장 최신 일기(마지막)를 선택
                const dateEntries = state.entries
                    .filter((e) => e.date === key)
                    .sort((a, b) => (b.tn || 0) - (a.tn || 0));
                
                if (dateEntries.length > 0) {
                    // 가장 최신 일기(첫 번째, 인덱스 0)를 선택
                    state.cursor = dateEntries[0].id;
                    state.currentDateEntryIndex = 0;
                } else {
                    state.cursor = null;
                    state.currentDateEntryIndex = -1;
                    resetComposer();
                }
                reflectCurrent();
                renderCalendar();
                
                // ✅ 추가: 선택된 날짜의 일기 목록 업데이트 (버튼 상태 포함)
                updateCurrentDateEntries();
                
                // ✅ 추가: 일기 로드 이벤트 발생
                window.dispatchEvent(new CustomEvent('entryLoaded'));
            };
              cal.appendChild(cell);
          }
          } catch (e) {
          console.warn("renderCalendar error", e);
          }
      }
  
      function reflectCurrent() {
          try {
          const img = $("#preview");
          const ph = $("#previewWrap .ph");
          const pw = $("#previewWrap");
          if (!state.cursor) {
              if (img) {
              img.src = "";
              img.style.display = "none";
              }
              if (ph) ph.style.display = "grid";
              if (pw) pw.classList.remove("has-image");
              $("#text").value = "";
              const ti = $("#title");
              if (ti) ti.value = "";
              state.tempPhotos = [];
              state.tempNames = [];
              state.photoItems = [];
              state.repIndex = 0;
              state.viewIndex = 0;
              renderGallery();
              return;
          }
          const e = state.entries.find((x) => x.id === state.cursor);
          if (!e) return;
          state.tempPhotos = Array.isArray(e.photos)
              ? e.photos.slice(0, MAX_UPLOAD)
              : e.photo
              ? [e.photo]
              : [];
          state.tempNames = e.notes?.map((n) => n?.desc || "") || [];
          state.photoItems = e.photoItems || [];
          state.repIndex = e.repIndex || 0;
          state.viewIndex = state.repIndex;
          if (state.tempPhotos.length && img) {
              img.onload = () => {
              img.style.display = "block";
              if (ph) ph.style.display = "none";
              if (pw) pw.classList.add("has-image");
              };
              img.src = state.tempPhotos[state.repIndex];
          } else {
              if (img) {
              img.src = "";
              img.style.display = "none";
              }
              if (ph) ph.style.display = "grid";
              if (pw) pw.classList.remove("has-image");
          }
          $("#text").value = e.body || "";
          const ti = $("#title");
          if (ti) ti.value = e.title || "";
          renderGallery();
          } catch (e) {
          console.warn("reflectCurrent error", e);
          }
      }
  
      // function loadEntry(id) {
      //     state.cursor = id;
      //     const entry = state.entries.find(e => e.id === id);
      //     if (entry && entry.date) {
      //         const [y, m, d] = entry.date.split("-").map(x => parseInt(x, 10));
      //         state.selectedDate = new Date(y, m - 1, d);
      //         state.cal = new Date(y, m - 1, 1);
      //         updateSelectedDateDisplay();
      //         renderCalendar();
      //     }
      //     reflectCurrent();
      // }
  
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
              const im = document.createElement("img");
              im.src = src;
              const x = document.createElement("div");
              x.className = "x";
              x.textContent = "×";
              x.onclick = () => {
              state.tempPhotos.splice(i, 1);
              if (state.tempNames) state.tempNames.splice(i, 1);
              if (state.photoItems) state.photoItems.splice(i, 1);
              if (state.repIndex >= state.tempPhotos.length)
                  state.repIndex = Math.max(0, state.tempPhotos.length - 1);
              renderGallery();
              updatePreviewFromView();
              };
              const badge = document.createElement("div");
              badge.className = "badge";
              badge.textContent = i === state.repIndex ? "대표사진" : "대표로";
              badge.onclick = () => {
              state.repIndex = i;
              state.viewIndex = i;
              updatePreviewFromView();
              renderGallery();
              };
              t.append(im, x, badge);
              thumbs.appendChild(t);
          });
          } catch (e) {
          console.warn("renderGallery error", e);
          }
      }
  
      function updatePreviewFromView() {
          const img = $("#preview");
          const ph = $("#previewWrap .ph");
          const pw = $("#previewWrap");
          const rep = state.tempPhotos[state.viewIndex];
          if (rep && img) {
          img.onload = () => {
              img.style.display = "block";
              if (ph) ph.style.display = "none";
              if (pw) pw.classList.add("has-image");
          };
          img.src = rep;
          } else {
          if (img) {
              img.src = "";
              img.style.display = "none";
          }
          if (ph) ph.style.display = "grid";
          if (pw) pw.classList.remove("has-image");
          }
      }
  
      function resetComposer() {
          state.cursor = null;
          state.tempPhotos = [];
          state.tempNames = [];
          state.photoItems = [];
          state.repIndex = 0;
          state.viewIndex = 0;
          const img = $("#preview");
          const ph = $("#previewWrap .ph");
          const pw = $("#previewWrap");
          const ta = $("#text");
          const ti = $("#title");
          const fi = $("#file");
          if (fi) fi.value = "";
          if (img) {
          img.src = "";
          img.style.display = "none";
          }
          if (ph) ph.style.display = "grid";
          if (pw) pw.classList.remove("has-image");
          if (ta) ta.value = "";
          if (ti) ti.value = "";
          renderGallery();
      }
  
      function renderAll() {
        renderStats();
        renderRecent();
        renderCalendar();
        reflectCurrent();
        updateSelectedDateDisplay();
        // ✅ 추가: 선택된 날짜의 일기 목록 업데이트
        updateCurrentDateEntries();
      }
  
      async function loadEntriesToState(){
    state.entries = await getAllFromIDB();
    renderAll();
    
    // ✅ URL에서 edit 파라미터 확인
    const urlParams = new URLSearchParams(window.location.search);
    const editId = urlParams.get('edit');
    
    console.log('🔍 edit 파라미터:', editId);
    console.log('🔍 전체 일기 개수:', state.entries.length);
    
    if (editId) {
        window.history.replaceState({}, '', './index.html');
        
        const entry = state.entries.find(e => e.id === editId);
        console.log('🔍 찾은 일기:', entry);
        
        if (entry) {
            console.log('✅ 일기를 찾았습니다!');
            loadEntry(editId);
            
            const intro = $("#intro");
            const app = $("#app");
            if (intro) intro.style.display = "none";
            if (app) app.style.display = "block";
        } else {
            console.log('❌ 해당 ID의 일기를 찾을 수 없습니다');
        }
    }
}
  
      // ================== 초기 바인딩 ==================
      window.addEventListener("DOMContentLoaded", () => {
          const autoModal = $("#autoModal");
          const autoModalText = autoModal ? autoModal.querySelector(".modal-text") : null;
          toggleAutoModal = (show, text) => {
              if (!autoModal) return;
              if (text && autoModalText) autoModalText.textContent = text;
              if (show) {
                  autoModal.classList.add("active");
              } else {
                  autoModal.classList.remove("active");
              }
          };
          // 테마
          applyTheme(state.theme);
          const darkToggleApp = $("#darkToggleApp");
          if (darkToggleApp) {
          darkToggleApp.checked = state.theme === "dark";
          darkToggleApp.addEventListener("change", () => {
              state.theme = darkToggleApp.checked ? "dark" : "light";
              applyTheme(state.theme);
          });
          }

      // 인트로 → 앱 전환 초기 한 번만 뜨게
      const intro = $("#intro");
      const app = $("#app");
      const startBtn = $("#startBtn");
      const hasVisited = loadLS("hasVisited", false);
      
      if (hasVisited) {
        if (intro) intro.style.display = "none";
        if (app) app.style.display = "block";
        renderAll();
      } else {
        if (intro) intro.style.display = "block";
        if (app) app.style.display = "none";
      }
      
      if (startBtn) {
        startBtn.addEventListener("click", () => {
          try {
            resetComposer();
            saveLS("hasVisited", true);
            if (intro) intro.style.display = "none";
            if (app) app.style.display = "block";
            renderAll();
          } catch (e) {
            console.warn("startBtn error", e);
          }
        });
      }
  
      try {
        renderCalendar();
      } catch (e) {
        console.warn(e);
      }
  
      const prevM = $("#prevM"),
        nextM = $("#nextM");
      if (prevM)
        prevM.addEventListener("click", () => {
          state.cal = new Date(
            state.cal.getFullYear(),
            state.cal.getMonth() - 1,
            1
          );
          renderCalendar();
        });
      if (nextM)
        nextM.addEventListener("click", () => {
          state.cal = new Date(
            state.cal.getFullYear(),
            state.cal.getMonth() + 1,
            1
          );
          renderCalendar();
        });
  
      const cameraTile = $("#cameraTile");
      if (cameraTile) cameraTile.addEventListener("click", () => $("#file").click());
  
      const fileInput = $("#file");
      if (fileInput) {
        fileInput.addEventListener("change", async (ev) => {
          const files = Array.from(ev.target.files || []);
          if (!files.length) return;
          const remain = MAX_UPLOAD - state.photoItems.length;
          const pick = files.slice(0, remain);
  
          for (const f of pick) {
            let shotAtMs = null;
            let gps = null;
            try {
              const exif = await exifr.parse(f, {
                tiff: true,
                ifd0: true,
                exif: true,
                gps: true,
              });
              
              const dt = exif?.DateTimeOriginal || exif?.CreateDate || exif?.ModifyDate;
              shotAtMs = dt ? +dt : f.lastModified || null;
              
              if (exif?.latitude && exif?.longitude) {
                gps = {
                  latitude: exif.latitude,
                  longitude: exif.longitude,
                };
              }
            } catch (e) {
              console.warn("exif parse fail", e);
            }
            if (!shotAtMs) shotAtMs = f.lastModified || Date.now();
  
            try {
              const durl = await downscaleToDataURL(f, 1280, 0.8);
              state.photoItems.push({
                dataURL: durl,
                name: f.name || "",
                shotAt: shotAtMs,
                gps: gps,
              });
            } catch (e) {
              console.warn("resize fail", e);
            }
          }
  
          state.photoItems.sort((a, b) => (a.shotAt || 0) - (b.shotAt || 0));
          state.tempPhotos = state.photoItems.map((p) => p.dataURL);
          state.tempNames = state.photoItems.map((p) => p.name);
          state.repIndex = 0;
          state.viewIndex = 0;
          renderGallery();
          updatePreviewFromView();
  
          console.log("photoItems with EXIF:", state.photoItems.map((p) => ({ 
            shotAt: p.shotAt, 
            name: p.name,
            gps: p.gps 
          })));
          
          fileInput.value = "";
        });
      }
  
      $('#saveBtn').addEventListener('click', async ()=>{
          try{
          const body = ($('#text')?.value || '').trim();
          const title = ($('#title')?.value || '제목 없음').slice(0,20);
          if(!body){ alert('내용이 비어있습니다'); return; }
  
          const entry = {
              id: state.cursor || newId(),
              title,
              body,
              photo: state.tempPhotos[state.repIndex] || '',
              photos: state.tempPhotos.slice(0, MAX_UPLOAD),
              photoItems: state.photoItems.slice(0, MAX_UPLOAD),
              repIndex: state.repIndex,
              date: formatDate(state.selectedDate),
              ts: state.selectedDate.getTime(),
              tn: Date.now() // ✅ 선택 날짜가 아니라 작성된 현재 시간
          };
  
          await saveEntryToIDB(entry);
          state.cursor = entry.id;
          state.entries = await getAllFromIDB();
          renderAll();
          
          window.dispatchEvent(new CustomEvent('entrySaved'));
          
          alert('저장되었습니다.');
          }catch(e){
          console.error(e);
          alert('저장 중 오류 발생');
          }
      });
    
      $('#delBtn').addEventListener('click', async ()=>{
          if(!state.cursor){ alert('삭제할 일기를 선택하세요'); return; }
          if(!confirm('정말 삭제하시겠습니까?')) return;
          try{
            await deleteEntryFromIDB(state.cursor);
            state.entries = await getAllFromIDB();
            state.cursor = null;
            renderAll();
            
            window.dispatchEvent(new CustomEvent('entrySaved'));
            
            alert('삭제되었습니다.');
          }catch(e){
            console.error(e);
            alert('삭제 중 오류 발생');
          }
      });
  

      // ✅ 추가: 이전 일기 버튼 클릭 핸들러 (◀ = 더 최신 일기로 이동)
      const prevDiaryBtn = $("#prevDiary");
      if (prevDiaryBtn) {
          prevDiaryBtn.addEventListener('click', () => {
              // ✅ 수정: ◀ 버튼 = 인덱스 감소 (더 최신 일기로)
              // 인덱스 0(가장 최신 일기)가 아니고, 일기 목록이 있을 때만 이동
              if (state.currentDateEntryIndex > 0 && 
                  state.currentDateEntries.length > 0 &&
                  state.currentDateEntryIndex < state.currentDateEntries.length) {
                  state.currentDateEntryIndex--;
                  const entry = state.currentDateEntries[state.currentDateEntryIndex];
                  if (entry) {
                      state.cursor = entry.id;
                      reflectCurrent();
                      updateCurrentDateEntries();
                      // ✅ 일기 로드 이벤트 발생 (지도 업데이트용)
                      window.dispatchEvent(new CustomEvent('entryLoaded'));
                  }
              }
          });
      }

      // ✅ 추가: 다음 일기 버튼 클릭 핸들러 (▶ = 더 오래된 일기로 이동)
      const nextDiaryBtn = $("#nextDiary");
      if (nextDiaryBtn) {
          nextDiaryBtn.addEventListener('click', () => {
              // ✅ 수정: ▶ 버튼 = 인덱스 증가 (더 오래된 일기로)
              // 마지막 인덱스가 아니고, 일기 목록이 있을 때만 이동
              if (state.currentDateEntryIndex >= 0 && 
                  state.currentDateEntryIndex < state.currentDateEntries.length - 1 &&
                  state.currentDateEntries.length > 0) {
                  state.currentDateEntryIndex++;
                  const entry = state.currentDateEntries[state.currentDateEntryIndex];
                  if (entry) {
                      state.cursor = entry.id;
                      reflectCurrent();
                      updateCurrentDateEntries();
                      window.dispatchEvent(new CustomEvent('entryLoaded'));
                  }
              }
          });
      }

      // ✅ 추가: 일기쓰기 버튼 클릭 핸들러
      const newDiaryBtn = $("#newDiaryBtn");
      if (newDiaryBtn) {
          newDiaryBtn.addEventListener('click', () => {
              // 새 일기 작성 모드로 전환
              state.cursor = null;
              state.currentDateEntryIndex = -1;
              resetComposer();
              updateCurrentDateEntries();
          });
      }

      $('#navPrev').addEventListener('click', ()=>{ 
        if(state.tempPhotos.length){ 
          state.viewIndex = (state.viewIndex - 1 + state.tempPhotos.length) % state.tempPhotos.length; 
          updatePreviewFromView(); 
        } 
      });
      
      $('#navNext').addEventListener('click', ()=>{ 
        if(state.tempPhotos.length){ 
          state.viewIndex = (state.viewIndex + 1) % state.tempPhotos.length; 
          updatePreviewFromView(); 
        } 
      });

     // const urlParams = new URLSearchParams(window.location.search);
     //  const editId = urlParams.get('edit');
     //  if (editId) {
     //    window.history.replaceState({}, '', './index.html');
        
     //    loadEntriesToState().then(() => {
     //      const entry = state.entries.find(e => e.id === editId);
     //      if (entry) {
     //        loadEntry(editId);
            
     //        const intro = $("#intro");
     //        const app = $("#app");
     //        if (intro) intro.style.display = "none";
     //        if (app) app.style.display = "block";
     //      }
     //    });
     //  } else {
     //    loadEntriesToState();
     //  }
          loadEntriesToState();
    });
  
    // ✅ loadEntry 함수를 여기로 이동 (IIFE 안쪽, window.addEventListener 밖)
    function loadEntry(id) {
      state.cursor = id;
      const entry = state.entries.find(e => e.id === id);
      if (entry && entry.date) {
          const [y, m, d] = entry.date.split("-").map(x => parseInt(x, 10));
          state.selectedDate = new Date(y, m - 1, d);
          state.cal = new Date(y, m - 1, 1);
          updateSelectedDateDisplay();
          renderCalendar();
      }
      reflectCurrent();
      
      // ✅ 추가: 선택된 날짜의 일기 목록 업데이트
      updateCurrentDateEntries();

      // ✅ 일기 로드 이벤트 발생
      window.dispatchEvent(new CustomEvent('entryLoaded'));
    }

    // ✅ 전역으로 노출 (IIFE 맨 아래, 닫는 괄호 바로 위)
    window.snaplogAPI = {
      getAllFromIDB: getAllFromIDB,
      getEntries: () => state.entries,
      getCurrentEntry: () => {
        if (!state.cursor) return null;
        return state.entries.find(e => e.id === state.cursor);
      }
    };
  
     // 자동생성
     const autoBtn = $("#autoBtn");
     if (autoBtn && !autoBtn.dataset.autoBound) {
       autoBtn.dataset.autoBound = "1";
       autoBtn.addEventListener("click", async () => {
         // photoItems 기준으로 진행
         if (!state.photoItems || state.photoItems.length === 0) {
           alert("직접 입력하시거나 사진을 넣어주세요");
           return;
         }
 
         // 서버로 보낼 데이터 구성
         const images = state.photoItems
           .slice(0, MAX_UPLOAD)
           .map((p) => p.dataURL);
         const imagesMeta = state.photoItems
           .slice(0, MAX_UPLOAD)
           .map((p) => ({ shotAt: p.shotAt }));
         const photosSummary = buildPhotosSummary(state);
         const tone = state.tone || "중립";
 
         toggleAutoModal(true, "자동생성 중...");
         try {
           const api = await callAutoDiaryAPI(
             images,
             photosSummary,
             tone,
             imagesMeta
           );
           if (!api) {
             return;
           }
           const category = classifyCategory(state.tempNames || []);
           const resultText =
             api.body || fallbackGenerate(photosSummary, category);
           const ta = $("#text");
           if (ta) ta.value = resultText;
         } finally {
           toggleAutoModal(false);
         }
       });
     }
     
  })(); // ✅ IIFE 닫는 괄호
