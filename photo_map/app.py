import os
import pathlib
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename

from db import Database
from exif_utils import extract_exif_metadata


BASE_DIR = pathlib.Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "photos.db"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"), template_folder=str(BASE_DIR / "templates"))
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "snaplog-secret")

    # Ensure directories exist
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "static").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "templates").mkdir(parents=True, exist_ok=True)

    # DB
    db = Database(str(DB_PATH))
    db.initialize()

    # CORS (로컬 HTML 파일에서 호출 시 편의를 위해 전체 허용)
    @app.after_request
    def add_cors_headers(resp):
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "Access-Control-Request-Headers", "*")
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return resp

    @app.route("/api/upload", methods=["POST", "OPTIONS"])
    def api_upload():
        if request.method == "OPTIONS":
            return ("", 204)
        if "photos" not in request.files:
            return jsonify({"error": "photos 필드가 필요합니다."}), 400

        files = request.files.getlist("photos")
        results = []
        for file_storage in files:
            if not file_storage or file_storage.filename == "":
                continue
            filename = secure_filename(file_storage.filename)
            stored_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}_{filename}"
            file_path = UPLOAD_DIR / stored_name
            file_storage.save(str(file_path))

            meta = extract_exif_metadata(str(file_path))
            row_id = db.insert_photo(
                original_filename=filename,
                stored_path=str(file_path.relative_to(BASE_DIR)),
                captured_at_iso=meta.datetime_iso,
                latitude=meta.latitude,
                longitude=meta.longitude,
            )
            results.append({
                "id": row_id,
                "filename": filename,
                "captured_at": meta.datetime_iso,
                "latitude": meta.latitude,
                "longitude": meta.longitude,
                "image_url": url_for("uploaded_file", filename=file_path.name, _external=True),
            })

        return jsonify({"count": len(results), "items": results})

    # Routes
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/map")
    def map_view():
        return render_template("map.html")

    @app.route("/upload", methods=["POST"])
    def upload():
        if "photos" not in request.files:
            flash("이미지 파일을 선택해 주세요.")
            return redirect(url_for("index"))

        files = request.files.getlist("photos")
        saved = 0

        for file_storage in files:
            if not file_storage or file_storage.filename == "":
                continue

            filename = secure_filename(file_storage.filename)
            stored_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}_{filename}"
            file_path = UPLOAD_DIR / stored_name
            file_storage.save(str(file_path))

            # Extract EXIF
            meta = extract_exif_metadata(str(file_path))

            # Insert DB (skip if no coordinates? we'll store anyway; API can filter)
            db.insert_photo(
                original_filename=filename,
                stored_path=str(file_path.relative_to(BASE_DIR)),
                captured_at_iso=meta.datetime_iso,
                latitude=meta.latitude,
                longitude=meta.longitude,
            )
            saved += 1

        flash(f"{saved}개 이미지를 저장했습니다.")
        return redirect(url_for("index"))

    @app.route("/api/photos")
    def api_photos():
        start = request.args.get("start")  # ISO 8601
        end = request.args.get("end")      # ISO 8601

        features = []
        for row in db.query_photos_by_date(start, end):
            # Only include points with coordinates for the map
            if row["latitude"] is None or row["longitude"] is None:
                continue

            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [row["longitude"], row["latitude"]],
                    },
                    "properties": {
                        "id": row["id"],
                        "filename": row["original_filename"],
                        "stored_path": row["stored_path"],
                        "captured_at": row["captured_at"],
                        "image_url": url_for("uploaded_file", filename=pathlib.Path(row["stored_path"]).name),
                    },
                }
            )

        return jsonify({"type": "FeatureCollection", "features": features})

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename: str):
        return send_from_directory(str(UPLOAD_DIR), filename)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)


