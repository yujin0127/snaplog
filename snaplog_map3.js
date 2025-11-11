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
    // 유틸리티
    // -------------------
    function parseYMD(s) {
        const [y, m, d] = (s || "").split("-").map((x) => parseInt(x, 10));
        return { y, m, d };
    }
    
    function formatDate(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }

    function getMonthKey(d) {
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
    }
  
    // -------------------
    // 상태
    // -------------------
    const mapState = {
      startDate: null,  // 전체 기간으로 시작
      endDate: null,
      hashtag: '',
      allHashtags: new Set(),
      entries: [] // 전체 일기 목록
    };

    // 캘린더 상태
    let calYear = new Date().getFullYear();
    let calMonth = new Date().getMonth();
  
    // -------------------
    // Tabs & Map
    // -------------------
    const tabs = $$('.tab');
    let mapInitialized = false;
    let leafletMap = null;
    let markerCluster = null;
    let pathPolylines = [];
  
    // 날짜별 색상 (7일 주기)
    const dayColors = [
      '#FF6B6B', // 일요일 - 빨강
      '#FF8E53', // 토요일 - 주황
      '#FFD93D', // 월요일 - 노랑
      '#6BCF7F', // 화요일 - 초록
      '#4ECDC4', // 수요일 - 청록
      '#45B7D1', // 목요일 - 파랑
      '#9B59B6', // 금요일 - 보라
    ];
  
    function getDayColor(dateStr) {
      const d = new Date(dateStr);
      return dayColors[d.getDay()];
    }
  
    // 해시태그 추출
    function extractHashtags(text) {
      if (!text) return [];
      const matches = text.match(/#[\w가-힣]+/g);
      return matches ? matches.map(tag => tag.toLowerCase()) : [];
    }
  
    // 기간 필터링 (전체 기간이 기본값)
    function isInDateRange(dateStr) {
      if (!dateStr) return false;
      
      // 시작일과 종료일이 모두 없으면 전체 표시
      if (!mapState.startDate && !mapState.endDate) {
        return true;
      }
      
      const d = new Date(dateStr);
      if (mapState.startDate && d < new Date(mapState.startDate)) return false;
      if (mapState.endDate && d > new Date(mapState.endDate)) return false;
      return true;
    }
  
    // 해시태그 필터링
    function hasHashtag(entry) {
      if (!mapState.hashtag) return true;
      const tags = extractHashtags(entry.body);
      return tags.includes(mapState.hashtag.toLowerCase());
    }

    // -------------------
    // 통계 렌더링
    // -------------------
    function renderStats() {
      try {
        const all = mapState.entries.length;
        const monthKey = getMonthKey(new Date());
        const month = mapState.entries.filter((e) =>
          (e.date || "").startsWith(monthKey)
        ).length;
        const photos = mapState.entries.filter((e) =>
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

    // -------------------
    // 최근 검색 기록
    // -------------------
    function saveSearchToHistory() {
      // 전체 기간 + 해시태그 없음은 저장하지 않음
      if (!mapState.startDate && !mapState.endDate && !mapState.hashtag) return;
      
      const search = {
        startDate: mapState.startDate,
        endDate: mapState.endDate,
        hashtag: mapState.hashtag,
        timestamp: Date.now()
      };

      let history = JSON.parse(localStorage.getItem('snaplog-search-history') || '[]');
      
      // 중복 제거
      history = history.filter(h => 
        !(h.startDate === search.startDate && 
          h.endDate === search.endDate && 
          h.hashtag === search.hashtag)
      );
      
      history.unshift(search);
      history = history.slice(0, 10); // 최대 10개
      
      localStorage.setItem('snaplog-search-history', JSON.stringify(history));
      renderRecentSearches();
    }

    function renderRecentSearches() {
      const container = $('#recent');
      if (!container) return;

      const history = JSON.parse(localStorage.getItem('snaplog-search-history') || '[]');
      
      if (history.length === 0) {
        container.innerHTML = '<div style="font-size:13px; color:#999; padding:8px;">검색 기록이 없습니다</div>';
        return;
      }

      container.innerHTML = '';
      
      history.forEach((search, idx) => {
        const item = document.createElement('div');
        item.className = 'item';
        item.style.cssText = 'cursor:pointer; padding:8px 10px; border-radius:6px; margin-bottom:4px; transition:background 0.2s;';
        
        const parts = [];
        if (search.startDate || search.endDate) {
          const start = search.startDate ? search.startDate.slice(5) : '시작';
          const end = search.endDate ? search.endDate.slice(5) : '끝';
          parts.push(`📅 ${start} ~ ${end}`);
        }
        if (search.hashtag) {
          parts.push(`🏷️ ${search.hashtag}`);
        }
        
        item.innerHTML = `<div style="font-size:13px;">${parts.join(' • ')}</div>`;
        
        item.addEventListener('mouseenter', () => {
          item.style.background = 'rgba(74, 144, 226, 0.1)';
        });
        item.addEventListener('mouseleave', () => {
          item.style.background = '';
        });
        
        item.addEventListener('click', () => {
          mapState.startDate = search.startDate;
          mapState.endDate = search.endDate;
          mapState.hashtag = search.hashtag;
          
          $('#mapStartDate').value = search.startDate || '';
          $('#mapEndDate').value = search.endDate || '';
          $('#mapHashtag').value = search.hashtag || '';
          
          loadMarkersToMap();
        });
        
        container.appendChild(item);
      });
    }
  
    // -------------------
    // 캘린더 렌더링 (snaplog3.js 기반)
    // -------------------
    function renderCalendar() {
      try {
        const cal = $('#calendar');
        const ym = $('#ym');
        if (!cal || !ym) return;

        const cur = new Date(calYear, calMonth, 1);
        ym.textContent = `${cur.getFullYear()}년 ${String(cur.getMonth() + 1).padStart(2, "0")}월`;

        cal.innerHTML = "";

        // 요일 헤더
        const daysHeader = ["일", "월", "화", "수", "목", "금", "토"];
        daysHeader.forEach((d) => {
          const h = document.createElement("div");
          h.className = "cell head";
          h.textContent = d;
          cal.appendChild(h);
        });

        const firstDay = new Date(cur.getFullYear(), cur.getMonth(), 1).getDay();
        const lastDate = new Date(cur.getFullYear(), cur.getMonth() + 1, 0).getDate();

        // 빈 칸
        for (let i = 0; i < firstDay; i++) {
          const e = document.createElement("div");
          e.className = "cell head";
          e.style.visibility = "hidden";
          cal.appendChild(e);
        }

        // 저장된 날짜 추출
        const saved = new Set(
          mapState.entries
            .filter((e) => {
              if (!e.date) return false;
              const { y, m } = parseYMD(e.date);
              return y === cur.getFullYear() && m === cur.getMonth() + 1;
            })
            .map((e) => parseYMD(e.date).d)
        );

        const today = new Date();

        // 날짜 셀 생성
        for (let d = 1; d <= lastDate; d++) {
          const cell = document.createElement("div");
          cell.className = "cell";
          cell.textContent = String(d);

          // 일기가 있는 날짜 표시
          if (saved.has(d)) cell.classList.add("saved");

          // 오늘 날짜 표시
          if (
            d === today.getDate() &&
            cur.getMonth() === today.getMonth() &&
            cur.getFullYear() === today.getFullYear()
          ) {
            cell.classList.add("today");
          }

          // 클릭 이벤트: 해당 날짜로 필터링
          cell.onclick = () => {
            const dateKey = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
            
            // 해당 날짜에 일기가 있으면 필터링
            if (saved.has(d)) {
              mapState.startDate = dateKey;
              mapState.endDate = dateKey;
              mapState.hashtag = '';
              
              $('#mapStartDate').value = dateKey;
              $('#mapEndDate').value = dateKey;
              $('#mapHashtag').value = '';
              
              loadMarkersToMap();
              saveSearchToHistory();
            }
          };

          cal.appendChild(cell);
        }
      } catch (e) {
        console.warn("renderCalendar error", e);
      }
    }

    // 캘린더 이전/다음 달
    function setupCalendarNav() {
      const prevBtn = $('#prevM');
      const nextBtn = $('#nextM');

      if (prevBtn) {
        prevBtn.addEventListener('click', () => {
          calMonth--;
          if (calMonth < 0) {
            calMonth = 11;
            calYear--;
          }
          renderCalendar();
        });
      }

      if (nextBtn) {
        nextBtn.addEventListener('click', () => {
          calMonth++;
          if (calMonth > 11) {
            calMonth = 0;
            calYear++;
          }
          renderCalendar();
        });
      }
    }

    // -------------------
    // 빠른 필터 버튼
    // -------------------
    function setupQuickFilters() {
      const filterContainer = document.createElement('div');
      filterContainer.style.cssText = 'display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px;';
      
      const quickFilters = [
        { label: '전체', start: null, end: null },
        { label: '올해', start: `${new Date().getFullYear()}-01-01`, end: `${new Date().getFullYear()}-12-31` },
        { label: '이번 달', start: null, end: null, isMonth: true },
        { label: '지난 달', start: null, end: null, isLastMonth: true }
      ];

      quickFilters.forEach(filter => {
        const btn = document.createElement('button');
        btn.className = 'btn ghost';
        btn.textContent = filter.label;
        btn.style.cssText = 'padding:4px 12px; font-size:12px;';
        
        btn.addEventListener('click', () => {
          if (filter.isMonth) {
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            mapState.startDate = `${year}-${month}-01`;
            const lastDay = new Date(year, now.getMonth() + 1, 0).getDate();
            mapState.endDate = `${year}-${month}-${String(lastDay).padStart(2, '0')}`;
          } else if (filter.isLastMonth) {
            const now = new Date();
            const year = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear();
            const month = now.getMonth() === 0 ? 12 : now.getMonth();
            mapState.startDate = `${year}-${String(month).padStart(2, '0')}-01`;
            const lastDay = new Date(year, month, 0).getDate();
            mapState.endDate = `${year}-${String(month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;
          } else {
            mapState.startDate = filter.start;
            mapState.endDate = filter.end;
          }
          
          mapState.hashtag = '';
          
          $('#mapStartDate').value = mapState.startDate || '';
          $('#mapEndDate').value = mapState.endDate || '';
          $('#mapHashtag').value = '';
          
          loadMarkersToMap();
          if (mapState.startDate || mapState.endDate) {
            saveSearchToHistory();
          }
        });
        
        filterContainer.appendChild(btn);
      });

      // 연도 선택
      const yearSelect = document.createElement('select');
      yearSelect.style.cssText = 'padding:4px 8px; border:1px solid #ddd; border-radius:6px; font-size:12px;';
      
      const currentYear = new Date().getFullYear();
      const years = mapState.entries.map(e => {
        if (!e.date) return null;
        return parseInt(e.date.split('-')[0]);
      }).filter(y => y !== null);
      
      const uniqueYears = [...new Set(years)].sort((a, b) => b - a);
      
      const defaultOption = document.createElement('option');
      defaultOption.value = '';
      defaultOption.textContent = '연도 선택';
      yearSelect.appendChild(defaultOption);
      
      uniqueYears.forEach(year => {
        const option = document.createElement('option');
        option.value = year;
        option.textContent = `${year}년`;
        yearSelect.appendChild(option);
      });

      yearSelect.addEventListener('change', (e) => {
        if (!e.target.value) return;
        const year = e.target.value;
        mapState.startDate = `${year}-01-01`;
        mapState.endDate = `${year}-12-31`;
        mapState.hashtag = '';
        
        $('#mapStartDate').value = mapState.startDate;
        $('#mapEndDate').value = mapState.endDate;
        $('#mapHashtag').value = '';
        
        loadMarkersToMap();
        saveSearchToHistory();
      });

      filterContainer.appendChild(yearSelect);

      // 월 선택
      const monthSelect = document.createElement('select');
      monthSelect.style.cssText = 'padding:4px 8px; border:1px solid #ddd; border-radius:6px; font-size:12px;';
      
      const monthDefault = document.createElement('option');
      monthDefault.value = '';
      monthDefault.textContent = '월 선택';
      monthSelect.appendChild(monthDefault);
      
      for (let m = 1; m <= 12; m++) {
        const option = document.createElement('option');
        option.value = m;
        option.textContent = `${m}월`;
        monthSelect.appendChild(option);
      }

      monthSelect.addEventListener('change', (e) => {
        if (!e.target.value) return;
        const year = yearSelect.value || currentYear;
        const month = String(e.target.value).padStart(2, '0');
        mapState.startDate = `${year}-${month}-01`;
        const lastDay = new Date(year, e.target.value, 0).getDate();
        mapState.endDate = `${year}-${month}-${String(lastDay).padStart(2, '0')}`;
        mapState.hashtag = '';
        
        $('#mapStartDate').value = mapState.startDate;
        $('#mapEndDate').value = mapState.endDate;
        $('#mapHashtag').value = '';
        
        loadMarkersToMap();
        saveSearchToHistory();
      });

      filterContainer.appendChild(monthSelect);

      // 필터 컨트롤 앞에 삽입
      const mapFilterArea = document.querySelector('#allMapContainer > div:first-child');
      if (mapFilterArea) {
        mapFilterArea.insertBefore(filterContainer, mapFilterArea.firstChild);
      }
    }
  
    // 경로 그리기
    function drawPathForHashtag(entries, hashtag) {
      // 기존 경로 제거
      pathPolylines.forEach(line => leafletMap.removeLayer(line));
      pathPolylines = [];
  
      // 해시태그가 있는 항목만 필터링
      const filtered = entries.filter(ent => {
        const tags = extractHashtags(ent.body);
        return tags.includes(hashtag.toLowerCase());
      });
  
      if (filtered.length < 2) return;
  
      // 날짜별로 그룹화
      const byDate = {};
      filtered.forEach(ent => {
        if (!ent.photoItems || !ent.photoItems.length) return;
        ent.photoItems.forEach(item => {
          if (item.gps && item.gps.latitude && item.gps.longitude) {
            const date = ent.date;
            if (!byDate[date]) byDate[date] = [];
            byDate[date].push({
              lat: item.gps.latitude,
              lng: item.gps.longitude,
              shotAt: item.shotAt,
              entry: ent,
              item: item
            });
          }
        });
      });
  
      // 각 날짜별로 경로 그리기
      Object.keys(byDate).sort().forEach(date => {
        const points = byDate[date].sort((a, b) => (a.shotAt || 0) - (b.shotAt || 0));
        if (points.length < 2) return;
  
        const coords = points.map(p => [p.lat, p.lng]);
        const color = getDayColor(date);
  
        const polyline = L.polyline(coords, {
          color: color,
          weight: 3,
          opacity: 0.7,
          dashArray: '10, 5'
        }).addTo(leafletMap);
  
        pathPolylines.push(polyline);
  
        // 순서 번호 마커 추가
        points.forEach((p, idx) => {
          const numberIcon = L.divIcon({
            className: 'number-marker',
            html: `<div style="
              background-color: ${color};
              color: white;
              width: 28px;
              height: 28px;
              border-radius: 50%;
              display: flex;
              align-items: center;
              justify-content: center;
              font-weight: bold;
              font-size: 14px;
              border: 2px solid white;
              box-shadow: 0 2px 4px rgba(0,0,0,0.3);
            ">${idx + 1}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14]
          });
  
          const marker = L.marker([p.lat, p.lng], { icon: numberIcon });
          
          const popup = [];
          popup.push(`<b>${p.entry.title || '제목 없음'}</b>`);
          popup.push(`<div style="margin-top:4px; font-size:12px; color:#666;">${date} - ${idx + 1}번째</div>`);
          
          if (p.item.dataURL) {
            popup.push(`<img src="${p.item.dataURL}" style="max-width:200px; display:block; margin-top:8px; border-radius:8px;">`);
          }
          
          if (p.shotAt) {
            const shotDate = new Date(p.shotAt);
            popup.push(`<div style="margin-top:6px; font-size:11px; color:#888;">📷 ${shotDate.toLocaleString('ko-KR')}</div>`);
          }
  
          marker.bindPopup(popup.join(''));
          markerCluster.addLayer(marker);
        });
      });
    }
  
    // 마커 로드
    async function loadMarkersToMap(){
      if(!markerCluster) return;
      
      // 기존 마커와 경로 제거
      markerCluster.clearLayers();
      pathPolylines.forEach(line => leafletMap.removeLayer(line));
      pathPolylines = [];

      let entriesArr = [];
      try {
        entriesArr = await getAllFromIDB();
        mapState.entries = entriesArr; // 상태 업데이트
      } catch(e) { 
        console.warn('loadMarkersToMap idb failed', e); 
      }

      // 통계 & 캘린더 업데이트
      renderStats();
      renderCalendar();

      // 모든 해시태그 수집 (전체 데이터에서)
      mapState.allHashtags.clear();
      entriesArr.forEach(ent => {
        if (!ent.body) return;
        const tags = extractHashtags(ent.body);
        console.log('Entry:', ent.date, 'Tags:', tags); // 디버깅
        tags.forEach(tag => mapState.allHashtags.add(tag));
      });
      console.log('All hashtags:', Array.from(mapState.allHashtags)); // 디버깅
      updateHashtagList();
  
        // 필터링
        const filtered = entriesArr.filter(ent => {
          return isInDateRange(ent.date) && hasHashtag(ent);
        });
  
        // 해시태그 검색 시 경로 그리기
        if (mapState.hashtag) {
          drawPathForHashtag(entriesArr, mapState.hashtag);
        } else {
          // 일반 마커 표시
          filtered.forEach(ent => {
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
  
                const color = getDayColor(ent.date);
                const colorIcon = L.divIcon({
                  className: 'color-marker',
                  html: `<div style="
                    background-color: ${color};
                    width: 12px;
                    height: 12px;
                    border-radius: 50%;
                    border: 2px solid white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.3);
                  "></div>`,
                  iconSize: [12, 12],
                  iconAnchor: [6, 6]
                });
  
                const m = L.marker([item.gps.latitude, item.gps.longitude], { icon: colorIcon })
                            .bindPopup(popup.join(''));
                markerCluster.addLayer(m);
              }
            });
          });
        }
  
        if (leafletMap && markerCluster.getLayers().length) {
          try { 
            leafletMap.fitBounds(markerCluster.getBounds().pad(0.25)); 
          } catch(e) { console.warn('fitBounds failed', e); }
        }
    }
  
    // 해시태그 목록 업데이트
    function updateHashtagList() {
      const container = $('#mapHashtagList');
      if (!container) return;
  
      container.innerHTML = '';
      
      if (mapState.allHashtags.size === 0) {
        container.innerHTML = '<div style="font-size:12px; color:#999;">해시태그가 없습니다</div>';
        return;
      }
  
      Array.from(mapState.allHashtags).sort().forEach(tag => {
        const badge = document.createElement('span');
        badge.style.cssText = `
          padding: 4px 10px;
          background: ${mapState.hashtag === tag ? '#4A90E2' : '#e9ecef'};
          color: ${mapState.hashtag === tag ? 'white' : '#495057'};
          border-radius: 12px;
          font-size: 12px;
          cursor: pointer;
          transition: all 0.2s;
        `;
        badge.textContent = tag;
        badge.onclick = () => {
          mapState.hashtag = mapState.hashtag === tag ? '' : tag;
          $('#mapHashtag').value = mapState.hashtag;
          loadMarkersToMap();
          saveSearchToHistory();
        };
        container.appendChild(badge);
      });
    }
  
    // 지도 초기화
    function initMapAndLoad(){
        if(mapInitialized) return;
        mapInitialized = true;
  
        const container = document.getElementById('allMapContainer');
        if(container) container.style.display = 'block';
  
        leafletMap = L.map('allMap').setView([36.5, 127.5], 7);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { 
            attribution: '© OpenStreetMap contributors' 
        }).addTo(leafletMap);
  
        markerCluster = L.markerClusterGroup();
        leafletMap.addLayer(markerCluster);
  
        loadMarkersToMap().then(() => {
          setupQuickFilters(); // 데이터 로드 후 필터 생성
        });
    }
  
    // -------------------
    // DOMContentLoaded
    // -------------------
    window.addEventListener("DOMContentLoaded", () => {
        initMapAndLoad();
        setupCalendarNav();
        renderRecentSearches();
  
        // 필터 버튼
        const filterBtn = $('#mapFilterBtn');
        if (filterBtn) {
          filterBtn.addEventListener('click', () => {
            mapState.startDate = $('#mapStartDate').value;
            mapState.endDate = $('#mapEndDate').value;
            mapState.hashtag = $('#mapHashtag').value.trim();
            if (mapState.hashtag && !mapState.hashtag.startsWith('#')) {
              mapState.hashtag = '#' + mapState.hashtag;
            }
            loadMarkersToMap();
            saveSearchToHistory();
          });
        }
  
        // 초기화 버튼
        const resetBtn = $('#mapResetBtn');
        if (resetBtn) {
          resetBtn.addEventListener('click', () => {
            mapState.startDate = null;
            mapState.endDate = null;
            mapState.hashtag = '';
            $('#mapStartDate').value = '';
            $('#mapEndDate').value = '';
            $('#mapHashtag').value = '';
            loadMarkersToMap();
          });
        }
  
        // Enter 키로 검색
        const hashtagInput = $('#mapHashtag');
        if (hashtagInput) {
          hashtagInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
              filterBtn.click();
            }
          });
        }
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