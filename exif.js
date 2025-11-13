document.addEventListener('DOMContentLoaded', () => {
  const fileInput = document.getElementById('file');
  const preview = document.getElementById('preview');

  fileInput.addEventListener('change', handleFiles);

  async function handleFiles(files) {
  // 기존에 저장된 데이터 불러오기
  let photoMeta = JSON.parse(localStorage.getItem("photoMeta")) || [];

  for (const file of files) {
    const exifData = await getExif(file);

    const info = {
      name: file.name,
      date: exifData.date || null,
      lat: exifData.lat || null,
      lon: exifData.lon || null,
    };

    // 새로운 파일 정보 추가
    photoMeta.push(info);
  }

  // localStorage에 다시 저장
  localStorage.setItem("photoMeta", JSON.stringify(photoMeta));

  console.log("📸 전체 사진 메타데이터:", photoMeta);
}

  function getExifData(file) {
    return new Promise((resolve) => {
      EXIF.getData(file, function () {
        const date = EXIF.getTag(this, 'DateTimeOriginal');
        const lat = convertGPS(EXIF.getTag(this, 'GPSLatitude'), EXIF.getTag(this, 'GPSLatitudeRef'));
        const lon = convertGPS(EXIF.getTag(this, 'GPSLongitude'), EXIF.getTag(this, 'GPSLongitudeRef'));
        resolve({ date, lat, lon });
      });
    });
  }

  // GPS 좌표 변환 함수
  function convertGPS(gps, ref) {
    if (!gps) return null;
    const d = gps[0];
    const m = gps[1];
    const s = gps[2];
    let coord = d + m / 60 + s / 3600;
    if (ref === 'S' || ref === 'W') coord = -coord;
    return coord;
  }
});
