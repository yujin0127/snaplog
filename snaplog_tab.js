// ✅ $ 함수 제거 (snaplog3.js에서 이미 선언됨)
// const $ = (sel) => document.querySelector(sel);
// const $$ = (sel) => document.querySelectorAll(sel);

// ✅ 전역 $ 함수 사용
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// tabs: simple switch, Map tab shows mapContainer and initializes the map with clustered markers
const tabs = $$('.tab');
let mapInitialized = false;
let leafletMap = null;
let markerCluster = null;

function initMapAndLoad(){
  if(mapInitialized) return;
  mapInitialized = true;
  leafletMap = L.map('map').setView([36.5,127.5],7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{ 
    attribution: '© OpenStreetMap contributors' 
  }).addTo(leafletMap);
  markerCluster = L.markerClusterGroup();
  leafletMap.addLayer(markerCluster);
  loadMarkersToMap();
}

async function loadMarkersToMap(){
  if(!markerCluster) return;
  markerCluster.clearLayers();
  
  let entriesArr = [];
  try {
    // ✅ window.snaplogAPI 사용
    if (window.snaplogAPI && typeof window.snaplogAPI.getAllFromIDB === 'function') {
      entriesArr = await window.snaplogAPI.getAllFromIDB();
    }
  } catch(e) { 
    console.warn('loadMarkersToMap idb failed', e); 
  }

  // photoItems에서 GPS 정보 추출
  entriesArr.forEach(ent => {
    if (!ent.photoItems || !ent.photoItems.length) return;
    
    ent.photoItems.forEach((item, idx) => {
      if (item.gps && item.gps.latitude && item.gps.longitude) {
        const popup = [];
        popup.push(`<b>${ent.title || '제목 없음'}</b>`);
        popup.push(`<div style="margin-top:4px; font-size:12px; color:#666;">${ent.date || ''}</div>`);
        
        // 해당 사진 표시
        if (item.dataURL) {
          popup.push(`<img src="${item.dataURL}" style="max-width:200px; display:block; margin-top:8px; border-radius:8px;">`);
        }
        
        // 촬영시각 표시
        if (item.shotAt) {
          const shotDate = new Date(item.shotAt);
          popup.push(`<div style="margin-top:6px; font-size:11px; color:#888;">📷 ${shotDate.toLocaleString('ko-KR')}</div>`);
        }
        
        const m = L.marker([item.gps.latitude, item.gps.longitude])
          .bindPopup(popup.join(''));
        markerCluster.addLayer(m);
      }
    });
  });
  
  // 마커가 있으면 지도 범위 조정
  if (leafletMap && markerCluster.getLayers().length) {
    try { 
      leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); 
    } catch(e) {
      console.warn('fitBounds failed', e);
    }
  }
}

// 탭 클릭 이벤트
tabs.forEach((t, i) => { 
  t.addEventListener('click', () => {
    tabs.forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    
    const mapContainer = $('#mapContainer');
    
    // 지도 탭 (index 1)
    if (i === 1) { 
      if (mapContainer) mapContainer.style.display = 'block'; 
      initMapAndLoad(); 
    } else { 
      if (mapContainer) mapContainer.style.display = 'none'; 
    }
  }); 
});

// 일기 저장/삭제 시 지도 업데이트
window.addEventListener('entrySaved', () => {
  if (mapInitialized) {
    loadMarkersToMap();
  }
});