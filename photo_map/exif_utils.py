from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from PIL import Image, ExifTags


@dataclass
class PhotoMeta:
    datetime_iso: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]


def _convert_gps_coord(values, ref: str) -> Optional[float]:
    try:
        d = float(values[0][0]) / float(values[0][1])
        m = float(values[1][0]) / float(values[1][1])
        s = float(values[2][0]) / float(values[2][1])
        dec = d + (m / 60.0) + (s / 3600.0)
        if ref in ["S", "W"]:
            dec = -dec
        return dec
    except Exception:
        return None


def _parse_datetime(dt_str: str) -> Optional[str]:
    # EXIF DateTimeOriginal usually like "2023:10:31 14:52:01"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.isoformat()
        except Exception:
            continue
    return None


def extract_exif_metadata(path: str) -> PhotoMeta:
    dt_iso: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    try:
        with Image.open(path) as img:
            exif = img._getexif() or {}
            if exif:
                tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}

                # DateTimeOriginal or DateTime
                for k in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    if k in tag_map and isinstance(tag_map[k], str):
                        dt_iso = _parse_datetime(tag_map[k])
                        if dt_iso:
                            break

                # GPS Info
                gps_info = tag_map.get("GPSInfo")
                if gps_info:
                    # GPS tags are keyed by numeric IDs; map them
                    gps_tag_map = {}
                    for gk, gv in gps_info.items():
                        name = ExifTags.GPSTAGS.get(gk, gk)
                        gps_tag_map[name] = gv

                    if (
                        "GPSLatitude" in gps_tag_map
                        and "GPSLatitudeRef" in gps_tag_map
                        and "GPSLongitude" in gps_tag_map
                        and "GPSLongitudeRef" in gps_tag_map
                    ):
                        lat = _convert_gps_coord(gps_tag_map["GPSLatitude"], gps_tag_map["GPSLatitudeRef"])  # type: ignore[arg-type]
                        lon = _convert_gps_coord(gps_tag_map["GPSLongitude"], gps_tag_map["GPSLongitudeRef"])  # type: ignore[arg-type]
    except Exception:
        # On any error, return whatever we have (likely Nones)
        pass

    return PhotoMeta(datetime_iso=dt_iso, latitude=lat, longitude=lon)


