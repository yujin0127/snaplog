(function(){
  "use strict";

  // -------------------
  // IndexedDB helper
  // -------------------
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

  async function getAllFromIDB(){
      const db = await openDB();
      return new Promise((resolve,reject)=>{
          const tx = db.transaction('entries','readonly');
          const req = tx.objectStore('entries').getAll();
          req.onsuccess = ()=>{ resolve(req.result); db.close(); };
          req.onerror = ()=>{ reject(req.error); db.close(); };
      });
  }

  // -------------------
  // $ helper
  // -------------------
  const $ = sel => document.querySelector(sel);
  const $$ = sel => document.querySelectorAll(sel);

  // -------------------
  // Tabs & Map
  // -------------------
  const tabs = $$('.tab');
  let mapInitialized = false;
  let leafletMap = null;
  let markerCluster = null;

  function isThisMonth(dateStr) {
      if (!dateStr) return false;
      const d = new Date(dateStr);
      const now = new Date();
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  }

  async function loadMarkersToMap(){
      if(!markerCluster) return;
      markerCluster.clearLayers();

      let entriesArr = [];
      try {
        entriesArr = await getAllFromIDB();
      } catch(e) { 
        console.warn('loadMarkersToMap idb failed', e); 
      }

      // 이번 달 데이터만 표시
      const thisMonthEntries = entriesArr.filter(ent => isThisMonth(ent.date));

      thisMonthEntries.forEach(ent => {
        if (!ent.photoItems || !ent.photoItems.length) return;

        ent.photoItems.forEach(item => {
          if (item.gps && item.gps.latitude && item.gps.longitude) {
            const popup = [];
            popup.push(`<b>${ent.title || '제목 없음'}</b>`);
            popup.push(`<div style="margin-top:4px; font-size:12px; color:#666;">${ent.date || ''}</div>`);

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
          }
        });
      });

      if (leafletMap && markerCluster.getLayers().length) {
        try { 
          leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); 
        } catch(e) { console.warn('fitBounds failed', e); }
      }
  }

  function initMapAndLoad(){
      if(mapInitialized) return;
      mapInitialized = true;

      const container = document.getElementById('allMapContainer');
      if(container) container.style.display = 'block'; // 로드 시 바로 표시

      leafletMap = L.map('allMap').setView([36.5, 127.5], 7);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { 
          attribution: '© OpenStreetMap contributors' 
      }).addTo(leafletMap);

      markerCluster = L.markerClusterGroup();
      leafletMap.addLayer(markerCluster);

      loadMarkersToMap();
  }

  // -------------------
  // DOMContentLoaded
  // -------------------
  window.addEventListener("DOMContentLoaded", () => {
      initMapAndLoad();
  });

  // -------------------
  // Tabs 클릭 이벤트
  // -------------------
  tabs.forEach((t, i) => { 
      t.addEventListener('click', () => {
          tabs.forEach(x => x.classList.remove('active'));
          t.classList.add('active');

          const mapContainer = $('#allMapContainer');

          if(i === 1){ // 지도 탭
              if(mapContainer) mapContainer.style.display = 'block';
              if(mapInitialized && leafletMap) leafletMap.invalidateSize();
          } else {
              if(mapContainer) mapContainer.style.display = 'none';
          }
      }); 
  });

  // -------------------
  // 일기 저장/삭제 시 지도 업데이트
  // -------------------
  window.addEventListener('entrySaved', () => {
      if(mapInitialized) loadMarkersToMap();
  });

})();
