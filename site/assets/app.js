/* global L */

const COLORS = {
  Flight: '#60a5fa',
  Train: '#34d399',
  Hotel: '#fbbf24',
  Other: '#a78bfa'
};

function colorForType(t) {
  return COLORS[t] || COLORS.Other;
}

function hashColor(str) {
  // deterministic bright-ish colors
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const hue = Math.abs(h) % 360;
  return `hsl(${hue} 70% 60%)`;
}

function uniq(arr) {
  return [...new Set(arr.filter(Boolean))];
}

function fmtDate(s) {
  if (!s) return '';
  // input is ISO-ish (YYYY-MM-DDTHH:mm:ssZ or local). We keep it simple.
  try { return new Date(s).toISOString().slice(0, 10); } catch { return s; }
}

async function loadJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
  return await res.json();
}

function makeSelect(selectEl, options, {labelAll='All'} = {}) {
  selectEl.innerHTML = '';
  const all = document.createElement('option');
  all.value = '';
  all.textContent = labelAll;
  selectEl.appendChild(all);
  for (const opt of options) {
    const o = document.createElement('option');
    o.value = opt;
    o.textContent = opt;
    selectEl.appendChild(o);
  }
}

function featurePassesFilters(f, filters) {
  const p = f.properties || {};
  if (filters.year && String(p.year) !== String(filters.year)) return false;
  if (filters.type && String(p.type) !== String(filters.type)) return false;
  return true;
}

function buildTripGroupCard(g) {
  // g = {tripName, start, end, events:[...]}
  const wrap = document.createElement('div');
  wrap.className = 'trip';

  const details = document.createElement('details');
  details.open = false;

  const summary = document.createElement('summary');
  summary.className = 'title';
  summary.textContent = `${g.tripName || 'Trip'} (${fmtDate(g.start)} → ${fmtDate(g.end)})`;

  const meta = document.createElement('div');
  meta.className = 'meta';
  const segs = (g.events || []).reduce((acc, e) => acc + (e.segmentCount || 0), 0);
  meta.innerHTML = `Items: <code>${(g.events||[]).length}</code> • Segments: <code>${segs}</code>`;

  const list = document.createElement('div');
  list.className = 'meta';
  list.style.marginTop = '8px';
  list.innerHTML = (g.events || []).map(e => {
    const t = e.type || 'Other';
    return `<div style="margin:6px 0; padding-left:8px; border-left:3px solid ${colorForType(t)}">
      <div><code>${t}</code> — ${e.title || ''}</div>
      <div style="color: var(--muted)"> ${fmtDate(e.start)} → ${fmtDate(e.end)} • segments: ${e.segmentCount || 0}</div>
    </div>`;
  }).join('');

  details.appendChild(summary);
  details.appendChild(meta);
  details.appendChild(list);
  wrap.appendChild(details);
  return wrap;
}

(async function main() {
  const map = L.map('map', { worldCopyJump: true }).setView([20, 0], 2);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap &copy; CARTO'
  }).addTo(map);

  const geo = await loadJSON('./trips.geojson');
  const summary = await loadJSON('./summary.json');

  const yearSelect = document.getElementById('yearSelect');
  const typeSelect = document.getElementById('typeSelect');
  const colorSelect = document.getElementById('colorSelect');
  const resetBtn = document.getElementById('resetBtn');
  const statsEl = document.getElementById('stats');
  const tripList = document.getElementById('tripList');

  const allYears = uniq(geo.features.map(f => f.properties && f.properties.year)).sort((a,b)=>a-b);
  const allTypes = uniq(geo.features.map(f => f.properties && f.properties.type)).sort();
  makeSelect(yearSelect, allYears, {labelAll:'All years'});
  makeSelect(typeSelect, allTypes, {labelAll:'All types'});

  const markers = L.markerClusterGroup({ maxClusterRadius: 40 });
  const linesLayer = L.layerGroup();
  map.addLayer(linesLayer);
  map.addLayer(markers);

  function render() {
    const filters = { year: yearSelect.value, type: typeSelect.value };

    linesLayer.clearLayers();
    markers.clearLayers();
    tripList.innerHTML = '';

    let visibleSegments = 0;
    let visibleTrips = new Set();

    const colorMode = colorSelect ? colorSelect.value : 'type';

    for (const f of geo.features) {
      if (!featurePassesFilters(f, filters)) continue;
      const p = f.properties || {};

      if (f.geometry.type === 'LineString') {
        visibleSegments += 1;
        visibleTrips.add(p.tripGroupKey || p.tripKey);
        const latlngs = f.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
        const stroke = (colorMode === 'trip') ? hashColor(String(p.tripGroupKey || p.tripKey || 'trip')) : colorForType(p.type);
        L.polyline(latlngs, {
          color: stroke,
          weight: 2,
          opacity: 0.85
        }).bindPopup(`${p.tripName ? `<b>${p.tripName}</b><br/>` : ''}${p.type}: ${p.fromLabel} → ${p.toLabel}<br/>${fmtDate(p.departure)} → ${fmtDate(p.arrival)}`)
          .addTo(linesLayer);
      }

      if (f.geometry.type === 'Point') {
        const latlng = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
        const stroke = (colorMode === 'trip') ? hashColor(String(p.tripGroupKey || p.tripKey || 'trip')) : colorForType(p.type);
        const m = L.circleMarker(latlng, {
          radius: 4,
          color: stroke,
          weight: 1,
          fillOpacity: 0.8
        }).bindPopup(`${p.tripName ? `<b>${p.tripName}</b><br/>` : ''}${p.label || 'Stop'}<br/>${p.type || ''}`);
        markers.addLayer(m);
      }
    }

    // Trip group cards
    const tripGroups = (summary.tripGroups || [])
      .filter(g => (!filters.year || String(g.year) === String(filters.year)))
      .map(g => {
        if (!filters.type) return g;
        const evs = (g.events || []).filter(e => String(e.type) === String(filters.type));
        return { ...g, events: evs };
      })
      .filter(g => (g.events || []).length > 0)
      .sort((a,b) => (a.start||'').localeCompare(b.start||''));

    for (const g of tripGroups) tripList.appendChild(buildTripGroupCard(g));

    statsEl.textContent = `Trips: ${tripGroups.length} • Segments: ${visibleSegments} • Data files: ${summary.sourceFiles}`;
  }

  yearSelect.addEventListener('change', render);
  typeSelect.addEventListener('change', render);
  if (colorSelect) colorSelect.addEventListener('change', render);
  resetBtn.addEventListener('click', () => { yearSelect.value=''; typeSelect.value=''; if (colorSelect) colorSelect.value='type'; render(); });

  render();
})();
