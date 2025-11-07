// 셀렉터 유틸 (jQuery 대체)
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
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{ attribution: '© OpenStreetMap contributors' }).addTo(leafletMap);
  markerCluster = L.markerClusterGroup();
  leafletMap.addLayer(markerCluster);
  loadMarkersToMap();
}
async function loadMarkersToMap(){
  if(!markerCluster) return;
  markerCluster.clearLayers();
  // merge entries from IDB too
  let entriesArr = JSON.parse(localStorage.getItem('entries') || '[]');
  try{
    const idbEntries = await getAllFromIDB();
    if(idbEntries && idbEntries.length){
      const byId = new Map(entriesArr.map(e=>[e.id,e]));
      idbEntries.forEach(e=> byId.set(e.id,e));
      entriesArr = Array.from(byId.values());
    }
  }catch(e){ console.warn('loadMarkersToMap idb merge failed', e); }

  entriesArr.forEach(ent=>{
    if(ent.latitude!==null && ent.latitude!==undefined && ent.longitude!==null && ent.longitude!==undefined){
      const popup = [];
      popup.push(`<b>${ent.title || ''}</b>`);
      if(ent.photo) popup.push(`<img src="${ent.photo}" style="max-width:160px; display:block; margin-top:6px">`);
      if(ent.exifDate) popup.push(`<div style="margin-top:6px">${new Date(ent.exifDate).toLocaleString('ko-KR')}</div>`);
      const m = L.marker([ent.latitude, ent.longitude]).bindPopup(popup.join('\n'));
      markerCluster.addLayer(m);
    }
  });
  if(leafletMap && markerCluster.getLayers().length){
    try{ leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); }catch(e){}
  }
}
tabs.forEach((t,i)=>{ t.addEventListener('click', ()=>{
  tabs.forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  // show/hide mapContainer when Map tab (index 1) selected
  const mapContainer = $('#mapContainer');
  if(i===1){ if(mapContainer) mapContainer.style.display='block'; initMapAndLoad(); } else { if(mapContainer) mapContainer.style.display='none'; }
}); });