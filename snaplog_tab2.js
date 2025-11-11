const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const tabs = $$('.tab');
let mapInitialized = false;
let leafletMap = null;
let markerCluster = null;

function initMap(){
  if(mapInitialized) return;
  mapInitialized = true;
  leafletMap = L.map('map').setView([36.5,127.5],7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{ 
    attribution: '© OpenStreetMap contributors' 
  }).addTo(leafletMap);
  markerCluster = L.markerClusterGroup();
  leafletMap.addLayer(markerCluster);
}

async function loadMarkersToMap(){
    console.log('=== loadMarkersToMap 호출됨 ===');
    
    if(!markerCluster) {
      console.log('markerCluster가 없음');
      return;
    }
    
    markerCluster.clearLayers();
    
    // ✅ 현재 보고 있는 일기만 가져오기
    const currentEntry = window.snaplogAPI?.getCurrentEntry?.();
    console.log('currentEntry:', currentEntry);
    
    if (!currentEntry) {
      console.log('선택된 일기가 없음');
      return;
    }
    
    if (!currentEntry.photoItems || !currentEntry.photoItems.length) {
      console.log('photoItems가 없음 또는 비어있음');
      return;
    }
    
    console.log('photoItems 개수:', currentEntry.photoItems.length);
  
    // ✅ 현재 일기의 photoItems만 표시
    currentEntry.photoItems.forEach((item, idx) => {
      console.log(`photoItem[${idx}]:`, item.gps);
      
      if (item.gps && item.gps.latitude && item.gps.longitude) {
        const popup = [];
        popup.push(`<b>${currentEntry.title || '제목 없음'}</b>`);
        popup.push(`<div style="margin-top:4px; font-size:12px; color:#666;">${currentEntry.date || ''}</div>`);
        
        if (item.dataURL) {
          popup.push(`<img src="${item.dataURL}" style="max-width:200px; display:block; margin-top:8px; border-radius:8px;">`);
        }
        
        if (item.shotAt) {
          const shotDate = new Date(item.shotAt);
          popup.push(`<div style="margin-top:6px; font-size:11px; color:#888;">📷 ${shotDate.toLocaleString('ko-KR')}</div>`);
        }
        
        const m = L.marker([item.gps.latitude, item.gps.longitude])
          .bindPopup(popup.join(''));
        markerCluster.addLayer(m);
        console.log('마커 추가됨:', item.gps.latitude, item.gps.longitude);
      }
    });
    
    console.log('총 마커 개수:', markerCluster.getLayers().length);
    
    if (leafletMap && markerCluster.getLayers().length) {
      try { 
        leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); 
        console.log('지도 범위 조정 완료');
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
      initMap(); // ✅ 지도 초기화 (최초 1회만)
      loadMarkersToMap(); // ✅ 매번 마커 다시 로드
    } else { 
      if (mapContainer) mapContainer.style.display = 'none'; 
    }
  }); 
});

// ✅ 일기 선택 변경 시 지도 업데이트 (최근 기록이나 캘린더에서 보기 클릭 시)
window.addEventListener('entryLoaded', () => {
  // 지도 탭이 활성화되어 있으면 마커 업데이트
  const mapTab = $$('.tab')[1];
  if (mapTab && mapTab.classList.contains('active')) {
    loadMarkersToMap();
  }
});

// 일기 저장/삭제 시 지도 업데이트
window.addEventListener('entrySaved', () => {
  const mapTab = $$('.tab')[1];
  if (mapInitialized && mapTab && mapTab.classList.contains('active')) {
    loadMarkersToMap();
  }
});