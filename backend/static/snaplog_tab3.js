const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const tabs = $$('.tab');
let mapInitialized = false;
let leafletMap = null;
let markers = [];

function initMap(){
  if(mapInitialized) return;
  mapInitialized = true;
  leafletMap = L.map('map').setView([36.5,127.5],7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{ 
    attribution: '© OpenStreetMap contributors' 
  }).addTo(leafletMap);
}

// ✅ 같은 위치의 사진들을 그룹화하는 함수
function groupPhotosByLocation(photoItems) {
  const groups = {};
  
  photoItems.forEach((item, idx) => {
    if (item.gps && item.gps.latitude && item.gps.longitude) {
      // 소수점 6자리까지만 사용 (약 0.11m 정밀도)
      const key = `${item.gps.latitude.toFixed(6)},${item.gps.longitude.toFixed(6)}`;
      
      if (!groups[key]) {
        groups[key] = {
          lat: item.gps.latitude,
          lng: item.gps.longitude,
          photos: []
        };
      }
      
      groups[key].photos.push(item);
    }
  });
  
  return Object.values(groups);
}

// ✅ 슬라이드 가능한 팝업 HTML 생성
function createPhotoSlidePopup(locationGroup, entryTitle, entryDate) {
  const photos = locationGroup.photos;
  const totalPhotos = photos.length;
  
  if (totalPhotos === 1) {
    // 사진이 1장이면 기존 방식
    const item = photos[0];
    const popup = [];
    popup.push(`<b>${entryTitle || '제목 없음'}</b>`);
    popup.push(`<div style="margin-top:4px; font-size:12px; color:#666;">${entryDate || ''}</div>`);
    
    if (item.dataURL) {
      popup.push(`<img src="${item.dataURL}" style="max-width:200px; display:block; margin-top:8px; border-radius:8px;">`);
    }
    
    if (item.shotAt) {
      const shotDate = new Date(item.shotAt);
      popup.push(`<div style="margin-top:6px; font-size:11px; color:#888;">📷 ${shotDate.toLocaleString('ko-KR')}</div>`);
    }
    
    return popup.join('');
  }
  
  // 사진이 여러 장이면 슬라이드 형태
  const sliderId = 'slider-' + Math.random().toString(36).slice(2);
  
  let html = `
    <div style="width:220px;">
      <b>${entryTitle || '제목 없음'}</b>
      <div style="margin-top:4px; font-size:12px; color:#666;">${entryDate || ''}</div>
      <div style="margin-top:4px; font-size:11px; color:#888;">📍 이 위치에서 ${totalPhotos}장</div>
      
      <div style="position:relative; margin-top:8px;">
        <div id="${sliderId}" style="position:relative; overflow:hidden; border-radius:8px;">
  `;
  
  photos.forEach((item, idx) => {
    const display = idx === 0 ? 'block' : 'none';
    html += `
      <div class="slide-item" data-index="${idx}" style="display:${display};">
        <img src="${item.dataURL}" style="max-width:200px; display:block; border-radius:8px;">
    `;
    
    if (item.shotAt) {
      const shotDate = new Date(item.shotAt);
      html += `<div style="margin-top:4px; font-size:10px; color:#888;">📷 ${shotDate.toLocaleString('ko-KR')}</div>`;
    }
    
    html += `</div>`;
  });
  
  html += `
        </div>
        
        ${totalPhotos > 1 ? `
        <button onclick="window.changeSlide('${sliderId}', -1)" 
                style="position:absolute; left:0; top:50%; transform:translateY(-50%); 
                       background:rgba(0,0,0,0.5); color:white; border:none; 
                       padding:8px 12px; cursor:pointer; border-radius:4px; font-size:18px;">
          ‹
        </button>
        <button onclick="window.changeSlide('${sliderId}', 1)" 
                style="position:absolute; right:0; top:50%; transform:translateY(-50%); 
                       background:rgba(0,0,0,0.5); color:white; border:none; 
                       padding:8px 12px; cursor:pointer; border-radius:4px; font-size:18px;">
          ›
        </button>
        <div style="text-align:center; margin-top:8px; font-size:12px; color:#666;">
          <span id="${sliderId}-counter">1</span> / ${totalPhotos}
        </div>
        ` : ''}
      </div>
    </div>
  `;
  
  return html;
}

// ✅ 슬라이드 변경 함수 (전역)
window.changeSlide = function(sliderId, direction) {
  const container = document.getElementById(sliderId);
  if (!container) return;
  
  const slides = container.querySelectorAll('.slide-item');
  let currentIndex = -1;
  
  slides.forEach((slide, idx) => {
    if (slide.style.display === 'block') {
      currentIndex = idx;
      slide.style.display = 'none';
    }
  });
  
  let newIndex = currentIndex + direction;
  if (newIndex < 0) newIndex = slides.length - 1;
  if (newIndex >= slides.length) newIndex = 0;
  
  slides[newIndex].style.display = 'block';
  
  const counter = document.getElementById(sliderId + '-counter');
  if (counter) counter.textContent = newIndex + 1;
};

async function loadMarkersToMap(){
  console.log('=== loadMarkersToMap 호출됨 ===');
  
  if(!leafletMap) {
    console.log('leafletMap이 없음');
    return;
  }
  
  // 기존 마커들 제거
  markers.forEach(marker => leafletMap.removeLayer(marker));
  markers = [];
  
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

  // ✅ 같은 위치의 사진들을 그룹화
  const locationGroups = groupPhotosByLocation(currentEntry.photoItems);
  console.log('그룹화된 위치 개수:', locationGroups.length);

  locationGroups.forEach((group, idx) => {
    console.log(`위치[${idx}]: ${group.lat}, ${group.lng} - 사진 ${group.photos.length}장`);
    
    // ✅ 슬라이드 팝업 생성
    const popupHtml = createPhotoSlidePopup(group, currentEntry.title, currentEntry.date);
    
    const marker = L.marker([group.lat, group.lng])
      .bindPopup(popupHtml, { maxWidth: 250 })
      .addTo(leafletMap);
    
    markers.push(marker);
  });
  
  console.log('총 마커 개수:', markers.length);
  
  // 마커가 있으면 지도 범위 조정
  if (leafletMap && markers.length > 0) {
    try {
      const group = L.featureGroup(markers);
      leafletMap.fitBounds(group.getBounds().pad(0.25));
      console.log('지도 범위 조정 완료');
    } catch(e) {
      console.warn('fitBounds failed', e);
    }
  }
}

function isMapTabActive() {
  const mapTab = $$('.tab')[1];
  return mapTab && mapTab.classList.contains('active');
}

// 탭 클릭 이벤트
tabs.forEach((t, i) => { 
  t.addEventListener('click', () => {
    tabs.forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    
    const mapContainer = $('#mapContainer');
    
    if (i === 1) { 
      if (mapContainer) mapContainer.style.display = 'block';
      initMap();
      loadMarkersToMap();
    } else { 
      if (mapContainer) mapContainer.style.display = 'none'; 
    }
  }); 
});

window.addEventListener('entryLoaded', () => {
  console.log('entryLoaded 이벤트 발생, 지도 탭 활성화:', isMapTabActive());
  if (isMapTabActive()) {
    loadMarkersToMap();
  }
});

window.addEventListener('entrySaved', () => {
  if (mapInitialized && isMapTabActive()) {
    loadMarkersToMap();
  }
});