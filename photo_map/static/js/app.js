/* global L */

(function () {
  const mapEl = document.getElementById('map');
  if (!mapEl) return;

  const map = L.map('map').setView([37.5665, 126.9780], 6); // Korea default
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const cluster = L.markerClusterGroup();
  map.addLayer(cluster);

  function toIsoLocal(datetimeLocalValue) {
    if (!datetimeLocalValue) return null;
    // datetime-local returns 'YYYY-MM-DDTHH:mm' in local time, make ISO string
    const dt = new Date(datetimeLocalValue);
    return dt.toISOString();
  }

  async function loadData() {
    const startVal = document.getElementById('start').value;
    const endVal = document.getElementById('end').value;
    const params = new URLSearchParams();
    const startIso = toIsoLocal(startVal);
    const endIso = toIsoLocal(endVal);
    if (startIso) params.set('start', startIso);
    if (endIso) params.set('end', endIso);

    const resp = await fetch('/api/photos?' + params.toString());
    const geojson = await resp.json();

    cluster.clearLayers();
    const layer = L.geoJSON(geojson, {
      onEachFeature: (feature, layer) => {
        const p = feature.properties || {};
        const img = p.image_url ? `<div><img src="${p.image_url}" alt="photo" style="max-width:200px;max-height:150px;border-radius:6px"/></div>` : '';
        const time = p.captured_at ? `<div>촬영: ${new Date(p.captured_at).toLocaleString()}</div>` : '';
        const name = p.filename ? `<div>파일: ${p.filename}</div>` : '';
        layer.bindPopup(`${img}${name}${time}`);
      }
    });
    cluster.addLayer(layer);

    try {
      // fit bounds if possible
      const bounds = layer.getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.1));
      }
    } catch (e) {}
  }

  document.getElementById('apply').addEventListener('click', loadData);
  // initial load without filters
  loadData();
})();


