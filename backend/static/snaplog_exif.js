// ---------- EXIF helper (returns {dateTaken, latitude, longitude}) ----------
function toNumber(value) {
  if (value && typeof value === 'object' && 'numerator' in value && 'denominator' in value) {
    return value.numerator / value.denominator;
  }
  return Number(value);
}
function dmsToDecimal(arr){ if(!Array.isArray(arr)||arr.length<3) return null; const d=toNumber(arr[0]), m=toNumber(arr[1]), s=toNumber(arr[2]); if(Number.isNaN(d)||Number.isNaN(m)||Number.isNaN(s)) return null; return d+m/60+s/3600; }
function readArrayBuffer(file){ return new Promise((resolve,reject)=>{ const r=new FileReader(); r.onerror=()=>reject(new Error('read arraybuffer fail')); r.onload=()=>resolve(r.result); r.readAsArrayBuffer(file); }); }
async function getExifFromFile(file){
  if(!file) return { dateTaken: null, latitude: null, longitude: null };
  try{
    const buffer = await readArrayBuffer(file);
    try{
      const tags = EXIF.readFromBinaryFile(buffer);
      let latitude=null, longitude=null, dateTaken=null;
      if(tags){
        const dateStr = tags.DateTimeOriginal || tags.DateTime;
        if(dateStr){ const parts = dateStr.split(' '); if(parts.length===2){ const [datePart,timePart]=parts; const [y,m,d]=datePart.split(':').map(Number); const [hh,mm,ss]=timePart.split(':').map(Number); if(!Number.isNaN(y)) dateTaken=new Date(y,m-1,d,hh||0,mm||0,ss||0).toISOString(); } }
        if(tags.GPSLatitude && tags.GPSLongitude){ const lat=dmsToDecimal(tags.GPSLatitude); const lon=dmsToDecimal(tags.GPSLongitude); const latRef=tags.GPSLatitudeRef||'N'; const lonRef=tags.GPSLongitudeRef||'E'; if(lat!==null && lon!==null){ latitude = lat*(latRef==='S'?-1:1); longitude = lon*(lonRef==='W'?-1:1); } }
      }
      if(!dateTaken) dateTaken = new Date().toISOString();
      if(latitude===null||longitude===null){ latitude=null; longitude=null; }
      return { dateTaken, latitude, longitude };
    }catch(e){
      // fallback to EXIF.getData
      return await new Promise((resolve)=>{
        try{
          EXIF.getData(file, function(){
            let latitude=null, longitude=null, dateTaken=null;
            const latTag = EXIF.getTag(this,'GPSLatitude'); const lonTag = EXIF.getTag(this,'GPSLongitude'); const latRef = EXIF.getTag(this,'GPSLatitudeRef')||'N'; const lonRef = EXIF.getTag(this,'GPSLongitudeRef')||'E';
            if(latTag && lonTag){ const lat=dmsToDecimal(latTag); const lon=dmsToDecimal(lonTag); if(lat!==null && lon!==null){ latitude = lat*(latRef==='S'?-1:1); longitude = lon*(lonRef==='W'?-1:1); } }
            const dateStr = EXIF.getTag(this,'DateTimeOriginal')||EXIF.getTag(this,'DateTime'); if(dateStr){ const parts=dateStr.split(' '); if(parts.length===2){ const [datePart,timePart]=parts; const [y,m,d]=datePart.split(':').map(Number); const [hh,mm,ss]=timePart.split(':').map(Number); if(!Number.isNaN(y)) dateTaken=new Date(y,m-1,d,hh||0,mm||0,ss||0).toISOString(); } }
            if(!dateTaken) dateTaken = new Date().toISOString(); if(latitude===null||longitude===null){ latitude=null; longitude=null; }
            resolve({ dateTaken, latitude, longitude });
          });
        }catch(ex){ resolve({ dateTaken: new Date().toISOString(), latitude:null, longitude:null }); }
      });
    }
  }catch(err){ console.warn('exif read overall failed',err); return { dateTaken: new Date().toISOString(), latitude:null, longitude:null }; }
}

// ---------- IndexedDB helpers (global) ----------
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
async function getAllFromIDB(){
  const db = await openDB();
  return new Promise((resolve,reject)=>{
    const tx = db.transaction('entries','readonly');
    const cur = tx.objectStore('entries').getAll();
    cur.onsuccess = ()=>{ resolve(cur.result); db.close(); };
    cur.onerror = ()=>{ reject(cur.error); db.close(); };
  });
}