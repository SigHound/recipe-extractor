# Recipe Extractor

Full-stack scaffold: **FastAPI** + **PostgreSQL** + **Vite/React (TypeScript)**. Docker Compose runs everything behind **nginx** on port **8080** (API proxied at `/api`).

## Features (so far)

- **POST `/api/extract`** — Fetch a public `http`/`https` URL (with basic SSRF checks), parse **schema.org `Recipe`** JSON-LD when present, otherwise fall back to **Open Graph / `<title>`** (ingredients/steps often empty in fallback). Fetches use **[curl_cffi](https://github.com/lexiforest/curl_cffi)** with **Chrome TLS impersonation** so more sites (e.g. Serious Eats) allow the request; plain script User-Agents often get **HTTP 402/403** from bot protection.
- **POST `/api/recipes`** — Save the current extraction to Postgres.
- **GET `/api/recipes`** — List saved recipes (JSON).
- **POST `/api/nutrition`** — Optional nutrition estimates from ingredient lines. **USDA FoodData Central** is used first if **`USDA_API_KEY`** is set ([free API key](https://fdc.nal.usda.gov/api-key-signup)); otherwise **Edamam** if **`EDAMAM_APP_ID`** and **`EDAMAM_APP_KEY`** are set ([free developer tier](https://developer.edamam.com/edamam-nutrition-api)). USDA matches each ingredient to a database food (top search hit) and sums nutrients; Edamam analyzes the list as one recipe. Without keys, the UI explains what to configure.

After changing Python dependencies, rebuild the API image: `docker compose build api` (or `docker compose up --build`).

## Quick start (Docker)

```bash
docker compose up --build
```

- App: http://localhost:8080  
- API docs: http://localhost:8080/docs  
- API direct: http://localhost:8000/docs  

**Config:** Put shared secrets in **`backend/.env`** (copy from `backend/.env.example`). Docker’s `api` service loads that file via `env_file`, and local `uvicorn` reads the same path — you do **not** need a separate repo-root `.env` for `USDA_API_KEY` / Edamam. Compose still overrides `DATABASE_URL` and `CORS_ORIGINS` inside the container so the API talks to the `db` service.

## Local development (no Docker for Node/Python)

1. Start Postgres (or use Docker only for DB: `docker compose up -d db`).
2. Backend: copy `backend/.env.example` to `backend/.env`, adjust `DATABASE_URL`, then:

   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

3. Frontend:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

Vite proxies `/api` and `/docs` to `http://127.0.0.1:8000`.

## Frontend version

The UI shows **Frontend vX.Y.Z** and a **build timestamp** (set when you run `npm run build` or rebuild the Docker `web` image). Bump the patch (or minor) version in `frontend/package.json` whenever you ship a meaningful change so you can tell builds apart; the timestamp changes on every build even if you forget to bump.

## Project layout

- `backend/app` — FastAPI app, SQLAlchemy models, API routes  
- `frontend` — React UI  
- `docker-compose.yml` — `db`, `api`, `web` (nginx + static build)

Default Postgres credentials in Compose: user `recipe`, password `recipe`, database `recipe` (change for production).
