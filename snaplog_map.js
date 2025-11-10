(function(){
    
    
  
    function applyTheme(t){ document.documentElement.setAttribute('data-theme', t); saveLS('theme', t); }
  
    function parseYMD(s){ const [y,m,d] = (s||"").split("-").map(x=>parseInt(x,10)); return {y,m,d}; }
  
    // ---------- rendering ----------
    function renderStats(){
      const all = state.entries.length;
      const monthKey = getMonthKey(new Date());
      const month = state.entries.filter(e=> (e.date||"").startsWith(monthKey)).length;
      const photos = state.entries.filter(e=> Array.isArray(e.photos) ? e.photos.length : (e.photo?1:0)).length;
      $('#statAll').textContent = `전체 ${all}`;
      $('#statMonth').textContent = `이번 달 ${month}`;
      $('#statPhotos').textContent = `사진 ${photos}`;
    }
  
    function renderRecent(){
      const box = $('#recent'); if(!box) return;
      box.innerHTML='';
      state.entries.slice().reverse().slice(0,50).forEach(e=>{
        const it=document.createElement('div'); it.className='item';
        const left=document.createElement('div');
        left.innerHTML = `<div><strong>${e.title || '제목 없음'}</strong></div><div class="small">${e.date}</div>`;
        const right=document.createElement('button'); right.className='btn ghost'; right.textContent=(state.cursor===e.id?'닫기':'보기');
        right.onclick=()=>{ if(state.cursor===e.id){ resetComposer(); renderRecent(); } else { loadEntry(e.id); renderRecent(); } };
        it.append(left,right); box.appendChild(it);
      });
    }
  
    function renderCalendar(){
      const cal = $('#calendar'); if(!cal) return;
      cal.innerHTML='';
      const ym = $('#ym');
      const cur = new Date(state.cal.getFullYear(), state.cal.getMonth(), 1);
      if(ym) ym.textContent = `${cur.getFullYear()}년 ${String(cur.getMonth()+1).padStart(2,'0')}월`;
  
      const daysHeader = ['일','월','화','수','목','금','토'];
      daysHeader.forEach(d=>{ const h=document.createElement('div'); h.className='cell head'; h.textContent=d; cal.appendChild(h); });
  
      const firstDay = new Date(cur.getFullYear(), cur.getMonth(), 1).getDay();
      const lastDate = new Date(cur.getFullYear(), cur.getMonth()+1, 0).getDate();
  
      for(let i=0;i<firstDay;i++){ const e=document.createElement('div'); e.className='cell head'; e.style.visibility='hidden'; cal.appendChild(e); }
  
      // Use entry.date (Y-M-D) to avoid timezone issues
      const saved = new Set(state.entries.filter(e=>{
        if(!e.date) return false;
        const {y,m} = parseYMD(e.date);
        return y===cur.getFullYear() && m===cur.getMonth()+1;
      }).map(e=> parseYMD(e.date).d));
  
      const today=new Date();
      for(let d=1; d<=lastDate; d++){
        const cell=document.createElement('div'); cell.className='cell'; cell.textContent=String(d);
        if(saved.has(d)) cell.classList.add('saved');
        if(d===today.getDate() && cur.getMonth()===today.getMonth() && cur.getFullYear()===today.getFullYear()) cell.classList.add('today');
        cell.onclick=()=>{ state.cal = new Date(cur.getFullYear(), cur.getMonth(), d);
          const key = `${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
          const hit = state.entries.find(e=>e.date===key); if(hit){ state.cursor=hit.id; } else { state.cursor=null; }
          reflectCurrent();
        };
        cal.appendChild(cell);
      }
    }
  
    function reflectCurrent(){
      const img=$('#preview'); const ph=$('#previewWrap .ph'); const pw=$('#previewWrap');
      if(!state.cursor){
        if(img){ img.src=''; img.style.display='none'; } if(ph){ ph.style.display='grid'; } if(pw){ pw.classList.remove('has-image'); }
        $('#text').value=''; const ti=$('#title'); if(ti) ti.value=''; state.tempPhotos=[]; state.tempFileObjects=[]; state.repIndex=0; state.viewIndex=0; renderGallery(); return;
      }
      const e = state.entries.find(x=>x.id===state.cursor); if(!e) return;
      state.tempPhotos = Array.isArray(e.photos)? e.photos.slice(0,10) : (e.photo ? [e.photo] : []);
      // We don't have file objects for saved entries; tempFileObjects will only be for newly uploaded files before save
      state.repIndex = e.repIndex || 0; state.viewIndex = state.repIndex;
      if(state.tempPhotos.length && img){
        img.onload=()=>{ img.style.display='block'; if(ph) ph.style.display='none'; if(pw) pw.classList.add('has-image'); };
        img.src = state.tempPhotos[state.repIndex];
      }else{
        if(img){ img.src=''; img.style.display='none'; } if(ph){ ph.style.display='grid'; } if(pw){ pw.classList.remove('has-image'); }
      }
      $('#text').value = e.body || ''; const ti=$('#title'); if(ti) ti.value = e.title || '';
      renderGallery();
    }
  
    function loadEntry(id){
      state.cursor = id;
      reflectCurrent();
    }
  
    function renderGallery(){
      const cnt=$('#camCount'); const thumbs=$('#thumbs');
      if(!thumbs) return;
      if(cnt) cnt.textContent = String(state.tempPhotos.length);
      thumbs.innerHTML='';
      state.tempPhotos.forEach((src, i)=>{
        const t=document.createElement('div'); t.className='thumb';
        const im=document.createElement('img'); im.src=src;
        const x=document.createElement('div'); x.className='x'; x.textContent='×';
        x.onclick=()=>{ state.tempPhotos.splice(i,1); state.tempFileObjects.splice(i,1); if(state.repIndex>=state.tempPhotos.length) state.repIndex=Math.max(0,state.tempPhotos.length-1); renderGallery(); updatePreviewFromView(); };
        const badge=document.createElement('div'); badge.className='badge'; badge.textContent = (i===state.repIndex)?'대표사진':'대표로';
        badge.onclick=()=>{ state.repIndex=i; state.viewIndex=i; updatePreviewFromView(); renderGallery(); };
        t.append(im,x,badge);
        thumbs.appendChild(t);
      });
    }
  
    function updatePreviewFromView(){
      const img=$('#preview'); const ph=$('#previewWrap .ph'); const pw=$('#previewWrap');
      const rep = state.tempPhotos[state.viewIndex];
      if(rep && img){
        img.onload=()=>{ img.style.display='block'; if(ph) ph.style.display='none'; if(pw) pw.classList.add('has-image'); };
        img.src = rep;
      }else{
        if(img){ img.src=''; img.style.display='none'; } if(ph){ ph.style.display='grid'; } if(pw){ pw.classList.remove('has-image'); }
      }
    }
  
    // ---------- actions ----------
    function resetComposer(){
      state.cursor=null; state.tempPhotos=[]; state.tempFileObjects=[]; state.repIndex=0; state.viewIndex=0;
      const img=$('#preview'); const ph=$('#previewWrap .ph'); const pw=$('#previewWrap');
      const ta=$('#text'); const ti=$('#title'); const fi=$('#file');
      if(fi) fi.value=''; if(img){ img.src=''; img.style.display='none'; } if(ph){ ph.style.display='grid'; } if(pw){ pw.classList.remove('has-image'); }
      if(ta) ta.value=''; if(ti) ti.value='';
      renderGallery();
    }
  
    function renderAll(){ renderStats(); renderRecent(); renderCalendar(); reflectCurrent(); }
  
    // ---------- event wiring (single binding) ----------
    window.addEventListener('load', ()=>{
      // theme
      applyTheme(state.theme);
      const darkToggleApp = $('#darkToggleApp');
      if(darkToggleApp){ darkToggleApp.checked = state.theme==='dark'; darkToggleApp.addEventListener('change', ()=>{ state.theme = darkToggleApp.checked?'dark':'light'; applyTheme(state.theme); }); }
  
      // intro switch
      $('#intro').style.display='block'; $('#app').style.display='none';
      async function mergeEntriesFromIDB(){
        try{
          const idb = await getAllFromIDB();
          if(idb && idb.length){
            const byId = new Map((state.entries||[]).map(e=>[e.id,e]));
            idb.forEach(e=> byId.set(e.id,e));
            state.entries = Array.from(byId.values());
            saveLS('entries', state.entries);
          }
        }catch(e){ console.warn('mergeEntriesFromIDB failed', e); }
      }
  
      $('#startBtn').addEventListener('click', async ()=>{ resetComposer(); $('#intro').style.display='none'; $('#app').style.display='block'; await mergeEntriesFromIDB(); renderAll(); });
  
      // month nav
      $('#prevM').addEventListener('click', ()=>{ state.cal = new Date(state.cal.getFullYear(), state.cal.getMonth()-1, 1); renderCalendar(); });
      $('#nextM').addEventListener('click', ()=>{ state.cal = new Date(state.cal.getFullYear(), state.cal.getMonth()+1, 1); renderCalendar(); });
  
      // camera tile
      $('#cameraTile').addEventListener('click', ()=> $('#file').click());
  
      // file input multiple -- keep file objects for EXIF and dataURLs for preview
      // $('#file').addEventListener('change', ev=>{
      //   const files = Array.from(ev.target.files || []); if(!files.length) return;
      //   const remain = 10 - state.tempPhotos.length; const pick = files.slice(0, remain);
      //   let loaded = 0;
      //   pick.forEach((f, idx)=>{
      //     const reader = new FileReader();
      //     reader.onload = e=>{ state.tempPhotos.push(String(e.target.result)); state.tempFileObjects.push(f); loaded++; if(loaded===pick.length){ state.repIndex = 0; state.viewIndex = 0; renderGallery(); updatePreviewFromView(); } };
      //     reader.readAsDataURL(f);
      //   });
      // });
  
      // file input multiple -- keep file objects for EXIF and dataURLs for preview
      $('#file').addEventListener('change', ev=>{
        const files = Array.from(ev.target.files || []); if(!files.length) return;
        const remain = 10 - state.tempPhotos.length; const pick = files.slice(0, remain);
        let loaded = 0;
        pick.forEach((f, idx)=>{
          const reader = new FileReader();
          reader.onload = e=>{ state.tempPhotos.push(String(e.target.result)); state.tempFileObjects.push(f); loaded++; if(loaded===pick.length){ state.repIndex = 0; state.viewIndex = 0; renderGallery(); updatePreviewFromView(); } };
          reader.readAsDataURL(f);
        });
      });
  
      // auto text
      $('#autoBtn').addEventListener('click', ()=>{ if(!$('#text').value){ $('#text').value='구름이 얇아지고 공기가 가벼워졌다. 오늘은 조용히 흐르는 리듬.'; } });
  
      // save - extended: extract EXIF and attempt localStorage save, compress if needed, fallback to IndexedDB
      $('#saveBtn').addEventListener('click', async ()=>{
        try{
          const body = ($('#text')?.value || '').trim();
          const ti=$('#title'); const title = (ti && ti.value ? ti.value : '제목 없음').slice(0,20);
          if(!body){ alert('일기 내용이 비어 있음.'); return; }
  
          // extract exif from representative file object if exists
          const repFile = state.tempFileObjects[state.repIndex] || null;
          let exif = { dateTaken: null, latitude: null, longitude: null };
          if(repFile){
            exif = await getExifFromFile(repFile);
          }
  
          const now = new Date();
          const entry = {
            id: state.cursor || newId(),
            title,
            body,
            photo: state.tempPhotos[state.repIndex] || '',
            photos: state.tempPhotos.slice(0,10),
            repIndex: state.repIndex,
            date: `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`,
            ts: now.getTime(),
            // EXIF fields
            exifDate: exif.dateTaken || null,
            latitude: (exif.latitude !== undefined ? exif.latitude : null),
            longitude: (exif.longitude !== undefined ? exif.longitude : null),
            // track storage location
            storedInIDB: false
          };
  
          // attempt to save to localStorage; if quota error, try compressing image, then finally save into IndexedDB
          async function trySaveToLocal(entriesArr){
            try{
              localStorage.setItem('entries', JSON.stringify(entriesArr));
              return true;
            }catch(err){
              console.warn('localStorage setItem failed', err);
              return false;
            }
          }
  
          // compress a dataURL image using canvas
          function compressDataUrl(dataUrl, maxWidth=1280, quality=0.7){
            return new Promise((resolve,reject)=>{
              const img = new Image();
              img.onload = ()=>{
                const canvas = document.createElement('canvas');
                let w = img.width, h = img.height;
                if(w>maxWidth){ h = Math.round(h*(maxWidth/w)); w = maxWidth; }
                canvas.width = w; canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img,0,0,w,h);
                try{ const out = canvas.toDataURL('image/jpeg', quality); resolve(out); }catch(e){ reject(e); }
              };
              img.onerror = ()=>reject(new Error('image load failed'));
              img.src = dataUrl;
            });
          }
  
          // IndexedDB helpers
          function openDB(){
            return new Promise((resolve,reject)=>{
              const r = indexedDB.open('snaplog-db',1);
              r.onupgradeneeded = ()=>{ const db=r.result; if(!db.objectStoreNames.contains('entries')) db.createObjectStore('entries',{keyPath:'id'}); };
              r.onsuccess = ()=>resolve(r.result);
              r.onerror = ()=>reject(r.error);
            });
          }
          async function saveEntryToIDB(ent){
            const db = await openDB();
            return new Promise((resolve,reject)=>{
              const tx = db.transaction('entries','readwrite');
              const store = tx.objectStore('entries');
              store.put(ent);
              tx.oncomplete = ()=>{ resolve(true); db.close(); };
              tx.onerror = ()=>{ reject(tx.error); db.close(); };
            });
          }
          async function getAllFromIDB(){ const db = await openDB(); return new Promise((resolve,reject)=>{ const tx=db.transaction('entries','readonly'); const cur = tx.objectStore('entries').getAll(); cur.onsuccess=()=>{ resolve(cur.result); db.close(); }; cur.onerror=()=>{ reject(cur.error); db.close(); }; }); }
  
          // try naive localStorage save first
          let entriesArr = JSON.parse(localStorage.getItem('entries') || '[]');
          const idx = entriesArr.findIndex(e=>e.id===entry.id);
          if(idx>=0) entriesArr[idx]=entry; else entriesArr.push(entry);
          let ok = await trySaveToLocal(entriesArr);
          if(!ok){
            // try compression attempts on photos (if present)
            const attempts = [{w:1280,q:0.7},{w:800,q:0.6},{w:600,q:0.5},{w:400,q:0.4}];
            let saved=false;
            for(const a of attempts){
              try{
                if(entry.photo){
                  const compressed = await compressDataUrl(entry.photo, a.w, a.q);
                  entry.photo = compressed; entry.photos = entry.photos.map((p,i)=> i===entry.repIndex? compressed : p);
                }
                // rebuild entriesArr with updated entry
                entriesArr = JSON.parse(localStorage.getItem('entries') || '[]');
                const j = entriesArr.findIndex(e=>e.id===entry.id);
                if(j>=0) entriesArr[j]=entry; else entriesArr.push(entry);
                ok = await trySaveToLocal(entriesArr);
                if(ok){ saved=true; break; }
              }catch(e){ console.warn('compress attempt failed',a,e); }
            }
            if(!ok){
              // last resort: save the full entry into IndexedDB and store a lightweight marker in localStorage
              try{
                entry.storedInIDB = true;
                await saveEntryToIDB(entry);
                // store lightweight representation in localStorage (metadata only)
                const lightweight = Object.assign({}, entry, { photo: null, photos: [], storedInIDB:true });
                entriesArr = JSON.parse(localStorage.getItem('entries') || '[]');
                const j2 = entriesArr.findIndex(e=>e.id===lightweight.id);
                if(j2>=0) entriesArr[j2]=lightweight; else entriesArr.push(lightweight);
                try{ localStorage.setItem('entries', JSON.stringify(entriesArr)); }catch(e){ console.warn('could not write lightweight marker to localStorage',e); }
                ok = true;
              }catch(idbErr){
                console.error('Failed to save to IDB as well', idbErr);
                ok = false;
              }
            }
          }
  
          // finish
          if(ok){
            state.cursor = entry.id; renderRecent(); renderStats(); renderCalendar(); reflectCurrent();
          }else{
            alert('저장이 실패했습니다. 브라우저 저장소가 부족합니다.');
          }
        }catch(e){
          console.error(e);
          alert('저장 중 오류가 발생했어.');
        }
      });
  
  // ✅ IndexedDB에서 특정 entry 삭제 + 목록 갱신
  async function deleteEntryFromIDB(id){
    try {
      const db = await openDB();
      const tx = db.transaction('entries', 'readwrite');
      tx.objectStore('entries').delete(id);
      await new Promise((resolve, reject)=>{
        tx.oncomplete = resolve;
        tx.onerror = ()=>reject(tx.error);
      });
      db.close();
  
      // 삭제 후 localStorage 동기화 (entries에서도 제거)
      state.entries = state.entries.filter(e => e.id !== id);
      saveLS('entries', state.entries);
  
      // UI 새로고침
      resetComposer();
      renderAll();
      alert('삭제되었습니다.');
    } catch(err){
      console.error('deleteEntryFromIDB failed', err);
      alert('삭제 중 오류가 발생했습니다.');
    }
  }
  
  // ✅ delete button event
  $('#delBtn').addEventListener('click', async ()=>{
    if(!state.cursor){
      alert('삭제할 일기를 선택하세요.');
      return;
    }
    if(!confirm('정말 삭제하시겠습니까?')) return;
    await deleteEntryFromIDB(state.cursor);
  });
     
      // carousel
      $('#navPrev').addEventListener('click', ()=>{ if(state.tempPhotos.length){ state.viewIndex = (state.viewIndex - 1 + state.tempPhotos.length) % state.tempPhotos.length; updatePreviewFromView(); } });
      $('#navNext').addEventListener('click', ()=>{ if(state.tempPhotos.length){ state.viewIndex = (state.viewIndex + 1) % state.tempPhotos.length; updatePreviewFromView(); } });
    });
  })();