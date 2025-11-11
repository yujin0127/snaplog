// ---------- snaplog_tab.js ----------

// 셀렉터 유틸 (jQuery 대체)
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// --- 탭 전환 및 지도 초기화 ---
const tabs = $$('.tab');
let mapInitialized = false;
let leafletMap = null;
let markerCluster = null;

// 지도 초기화
function initMapAndLoad() {
    if (mapInitialized) return;
    mapInitialized = true;

    // 지도 생성
    leafletMap = L.map('map').setView([36.5, 127.5], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
    }).addTo(leafletMap);

    // 클러스터 그룹 생성
    markerCluster = L.markerClusterGroup();
    leafletMap.addLayer(markerCluster);

    loadMarkersToMap();
}

// IndexedDB + LocalStorage에서 마커 불러오기
async function loadMarkersToMap() {
    if (!markerCluster) return;

    markerCluster.clearLayers();

    // LocalStorage entries
    let entriesArr = JSON.parse(localStorage.getItem('entries') || '[]');

    // IndexedDB entries
    try {
        const idbEntries = await getAllFromIDB();
        if (idbEntries && idbEntries.length) {
            // 중복 ID 처리
            const byId = new Map(entriesArr.map(e => [e.id, e]));
            idbEntries.forEach(e => byId.set(e.id, e));
            entriesArr = Array.from(byId.values());
        }
    } catch (e) {
        console.warn('loadMarkersToMap idb merge failed', e);
    }

    // 마커 추가
    entriesArr.forEach(ent => {
        if (ent.latitude != null && ent.longitude != null) {
            const popup = [];
            popup.push(`<b>${ent.title || ''}</b>`);
            if (ent.photo) popup.push(`<img src="${ent.photo}" style="max-width:160px; display:block; margin-top:6px">`);
            if (ent.exifDate) popup.push(`<div style="margin-top:6px">${new Date(ent.exifDate).toLocaleString('ko-KR')}</div>`);

            const m = L.marker([ent.latitude, ent.longitude])
                .bindPopup(popup.join('\n'));
            markerCluster.addLayer(m);
        }
    });

    // 마커가 있으면 지도 범위 조정
    if (leafletMap && markerCluster.getLayers().length) {
        try { leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); } catch (e) {}
    }
}

// --- 탭 클릭 이벤트 ---
tabs.forEach((t, i) => {
    t.addEventListener('click', () => {
        tabs.forEach(x => x.classList.remove('active'));
        t.classList.add('active');

        const mapContainer = $('#mapContainer');
        if (i === 1) { // Map 탭 선택
            if (mapContainer) mapContainer.style.display = 'block';
            initMapAndLoad();
        } else {
            if (mapContainer) mapContainer.style.display = 'none';
        }
    });
});

// --- 다크모드 토글 ---
const darkToggle = $('#darkToggleApp');
if (darkToggle) {
    darkToggle.addEventListener('change', () => {
        document.documentElement.dataset.theme = darkToggle.checked ? 'dark' : 'light';
    });
}

// --- 달력, 최근 기록 등은 다른 JS에서 처리 ---
