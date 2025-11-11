# Copilot instructions for Snaplog

Quick, focused notes to help an AI agent be productive in this repository.

- Project layout (important files/directories):
  - `server.py` (root) — Flask service that implements the OpenAI-backed auto-diary pipeline. Key functions: `analyze_images`, `draft_diary`, `refine_diary`. Main API: `POST /api/auto-diary`, `GET /health`.
  - `backend/main.py` — FastAPI service that talks to Azure Cosmos DB (`COSMOS_URL`, `COSMOS_KEY`). Endpoints: `POST /diary`, `GET /diaries`.
  - `photo_map/` — Flask app for photo uploads and map features. Key files: `photo_map/app.py` (create_app / upload endpoints), `photo_map/db.py` (SQLite `photos` table schema).
  - `image_diary/` — small Flask app that reads EXIF and generates a folium map; contains `add` and `map` routes.
  - static HTML/JS at repo root (e.g., `Snaplog_test4.html`, `Snaplog_map.html`) used for simple frontends.

- Big-picture architecture and data flow:
  - There are multiple small services (Flask and FastAPI) rather than a monolith. The OpenAI-powered diary pipeline runs in `server.py` and expects base64/data-URL images (or JSON photo summaries) as input.
  - Photo ingestion happens in `photo_map/app.py`: uploads saved to `photo_map/uploads` with a timestamped filename pattern, EXIF is extracted (`exif_utils.py` or `exif.js` helpers), then persisted to a local SQLite DB (`photo_map/photos.db`). The frontend then fetches `/api/photos` to build map features.
  - `backend/main.py` is a separate service using Azure Cosmos DB for storing generic diary objects (different from the local SQLite used by `photo_map`).

- Environment variables and secrets (required/used in code):
  - `OPENAI_API_KEY` — required by `server.py` (OpenAI client). Without it the server raises at startup.
  - `OPENAI_THROTTLE_SECONDS`, `OPENAI_MAX_WAIT_SECONDS` — optional throttling controls used by `server.py`'s API calls.
  - `COSMOS_URL`, `COSMOS_KEY` — required by `backend/main.py` (Cosmos DB connection).
  - `FLASK_SECRET_KEY` — optionally used by `photo_map` and other Flask apps.
  - `PORT` — used by some apps to choose the HTTP port.

- How to run (PowerShell examples, adjust venv as needed):
  - Run the OpenAI diary service (root Flask):
    python server.py
    - serves `http://0.0.0.0:5000`, API at `/api/auto-diary`.
  - Run the backend FastAPI (Cosmos-backed):
    uvicorn backend.main:app --reload --port 8000
  - Run the photo-map app (local SQLite):
    python photo_map/app.py
  - Run the image-diary demo app:
    python image_diary/app.py

- Notable code patterns and conventions to follow when editing:
  - CORS is intentionally permissive in several places for local HTML usage — be careful if tightening origins.
  - Photo filenames: stored as `{UTC_TIMESTAMP}_{secure_filename}` in `photo_map/uploads` (see `photo_map/app.py`).
  - DB separation: local photo metadata uses SQLite (`photo_map/db.py`), while cross-user diary storage uses Cosmos DB in `backend/main.py`.
  - OpenAI usage in `server.py` expects either `data:image/...` URLs or raw base64 image strings; there is a `MAX_IMAGES` cap and a throttling wrapper `throttled_chat_completion` — preserve that throttling if you change the OpenAI call site.

- Integration points to be careful about:
  - `server.py` serializes the model's JSON into `analysis` objects with `frames` and `global` keys. Other code relies on that shape (see calls to `analysis.get("frames")`).
  - `photo_map/app.py` returns geoJSON-like FeatureCollections at `/api/photos` used by the map frontend; keep the `image_url` and `properties` keys stable.
  - `backend/main.py` expects a Cosmos container partition key of `/userId` — migrations/changes to partitioning require updating container creation logic.

- Quick examples (in-repo pointers):
  - To find the diary pipeline: open `server.py` and inspect `analyze_images -> draft_diary -> refine_diary`.
  - To see the SQLite schema: `photo_map/db.py` (table `photos` with latitude/longitude, stored_path, captured_at).
  - To reproduce uploads: check `photo_map/app.py` `@app.route('/api/upload')` and follow the saved file naming and EXIF extraction using `exif_utils.py`.

- Tests and build: none discovered. There is no `requirements.txt` or CI config in repo; prefer running the apps in an isolated virtualenv and installing packages referenced by imports (Flask, FastAPI, uvicorn, openai, azure-cosmos, exifread, geopy, folium, pydantic).

If anything above is unclear, tell me which service or file you want expanded and I will update this file accordingly.
