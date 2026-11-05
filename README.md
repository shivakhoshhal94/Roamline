# Roamline

Personalized travel discovery and itinerary planning.

Travel planning often starts with scattered ideas, too many tabs, and generic lists. Roamline is intended to help travelers decide where to go and shape their days around their interests, pace, and comfort preferences.

## Two ways to begin

- **Discover a destination:** you know your dates and preferences, but have not chosen where to travel.
- **Plan an existing destination:** you know where you are going and want help choosing experiences and organizing your days.

## Current status

### Implemented

- Next.js landing page, a staged **Traveler Profile / Preference Intake** workflow at `/discover`, and an honest `/plan` route shell.
- `POST /api/v1/traveler-profile/validate` validates and returns a normalized canonical traveler profile without persistence.
- An editable, human-readable preference summary after successful backend validation.
- FastAPI `/health` liveness and `/ready` PostgreSQL connectivity check (503 when unavailable).
- Environment-based settings and explicit CORS allowlist.
- Typed frontend API boundary for health/readiness and profile validation, with timeouts and field-level validation errors.
- One canonical traveler profile designed for both future destination discovery and itinerary planning; no recommendation behavior or profile storage.
- SQLAlchemy engine lifecycle, Alembic scaffolding, development Compose, and foundational CI/tests.

![Traveler preference intake on the running Roamline application](docs/images/traveler-preferences.png)

### Planned

Personalized destination ranking, multi-day itineraries, geographic/travel-time constraints, weather awareness, recommendation explanations, and replanning. Retrieval or orchestration tools will be considered only when a concrete feature requires them. None of these capabilities are implemented yet.

## Architecture

```mermaid
flowchart LR
    Web[Next.js / TypeScript] -->|Typed HTTP client: profile validation and health| API[FastAPI]
    API -->|SQLAlchemy / Psycopg: SELECT 1| DB[(PostgreSQL)]
```

The landing page is static; the intake form calls the validation API in the browser. Database access currently exists only for readiness; no application tables are created. The server owns its connection pool through FastAPI lifespan and disposes it on shutdown. Liveness never depends on database availability.

## Tech stack

Python 3.12+, FastAPI, Pydantic, pydantic-settings, SQLAlchemy 2.x, Psycopg 3, PostgreSQL 17, Alembic, pytest; Next.js App Router, React, TypeScript, ESLint; Docker Compose and GitHub Actions. Node.js 24 LTS is used for frontend development and CI. Frontend dependency versions are pinned with an npm lockfile; Python dependencies have compatible major-version bounds.

## Repository structure

```text
backend/
  app/api/            Operational endpoints and profile validation
  app/core/           Configuration
  app/db/             Engine creation
  app/domain/         Traveler preference schemas
  app/main.py         Application factory and lifecycle
  tests/              Unit and optional PostgreSQL integration checks
  alembic/            Migration environment; no domain migrations yet
  pyproject.toml
  Dockerfile
frontend/
  app/                Landing page, discovery intake, plan shell, shared styles
  components/         Shared preference controls, intake, summary, journey cards
  lib/                Typed API client, profile contract, and form validation
  tests/              Node built-in tests; no added test dependency
  public/
  Dockerfile
.github/workflows/ci.yml
docs/images/          Real application screenshots
.env.example
docker-compose.yml
```

## Local development

Prerequisites: Python 3.12+, Node.js 24 with npm, and a running PostgreSQL instance.

1. Copy `.env.example` to `.env` locally. Replace the placeholder password and use matching credentials for your database. Do not commit this file. `DATABASE_URL` must use `postgresql+psycopg://` and URL-encode special characters in credentials. For native development, use `localhost` as the database hostname.
2. Install and start the backend from the repository root:

   ```sh
   python -m venv backend/.venv
   # macOS/Linux:
   source backend/.venv/bin/activate
   # Windows PowerShell instead:
   # backend\.venv\Scripts\Activate.ps1
   python -m pip install -e "./backend[dev]"
   uvicorn app.main:create_app --factory --reload
   ```

   Settings read `.env` from the current working directory. API docs: <http://localhost:8000/docs>. Liveness: <http://localhost:8000/health>; readiness: <http://localhost:8000/ready>.
3. In another terminal:

   ```sh
   cd frontend
   npm ci
   # Set NEXT_PUBLIC_API_URL=http://localhost:8000 in frontend/.env.local
   # (or export it in your shell) when using the API client.
   npm run dev
   ```

   Open <http://localhost:3000>. Next.js does not automatically read the root `.env`. The public API URL is visible to browsers; never put secrets there. Production builds embed NEXT_PUBLIC values, so set the URL before building.

### Validation

```sh
# From backend, with the virtual environment activated:
python -m pytest
# From frontend:
npm test
npm run lint
npm run build
# From repository root (safe placeholders suffice for config validation):
docker compose --env-file .env.example config --quiet
git diff --check
```

The current npm audit reports a moderate development-tool advisory in the transitive minimatch dependency used by TypeScript ESLint (eight affected package entries). The suggested automatic fix downgrades the Next.js lint configuration across a major version, so it has not been applied. Track an upstream compatible fix. No production dependency is listed in this finding.

Backend tests cover profile normalization, date/duration consistency, duplicate interests, enums, accessibility boundaries, API responses, POST CORS preflight, readiness, liveness independence, configuration, and lifecycle. Frontend tests use Node’s built-in test runner for form validation and typed API success/error behavior. Browser interactions should also be checked against the running stack; Chrome DevTools MCP is optional development tooling, not an application dependency. Set `TEST_DATABASE_URL` to a disposable PostgreSQL URL to enable the real connectivity test; otherwise it is explicitly skipped. CI provides PostgreSQL and runs that integration test.

## Docker development

1. Copy `.env.example` to `.env`, choose a local password, and set `DATABASE_URL` to use **db** as hostname, not localhost. Keep the credentials consistent with `POSTGRES_*`. Keep `NEXT_PUBLIC_API_URL=http://localhost:8000` for requests from your browser.
2. Run:

   ```sh
   docker compose up --build
   ```

3. Open <http://localhost:3000>. Ports 3000, 8000, and 5432 bind only to loopback. PostgreSQL data lives in the `postgres_data` named volume. Backend and frontend source directories are mounted for development reload; rebuild after dependency/configuration changes.
4. Stop with `docker compose down`. This retains database data. `docker compose down --volumes` deletes it; only use that when you intend to reset your local database.

Database, backend readiness, and frontend HTTP health checks gate startup. The Dockerfiles target development, not hardened production deployment. No credentials are baked into images or provided as Compose defaults.

## Migrations and domain decisions

Alembic reads the same environment settings as the application. From `backend`, with `DATABASE_URL` exported, use `alembic upgrade head`. There are no migrations or domain tables yet. Create explicit migrations when persistence is justified; autogeneration will need declarative model metadata wired into `alembic/env.py` first.

`TravelerProfile` is a validation-only contract, not a database entity. One profile will support both future user journeys. Interests and hobbies share an extensible list of `{name, importance}` objects; names are whitespace-normalized and case-folded before duplicate detection. Other preferences use small enums, with `null` meaning no preference.

The contract includes:

- `travel_context`: optional city/country and dates, `duration_days` (1–365), and `flexible_dates`. Supply a duration or both dates. Both arrival and departure days count; paired dates derive duration or must match a supplied duration. A single date may be combined with a duration.
- `interests`: up to 30 weighted interests; importance is `low`, `moderate`, or `high`. Names and origin fields are limited to 80 characters.
- `travel_style`, `activity_level`, `walking_preference`, `weather_preference`, `weather_dislikes`, `crowd_preference`, and `trip_pace`.
- `accessibility`: travel-oriented requirement choices and an optional 500-character single-line note. No diagnoses or medical-history fields are collected. Unknown fields and control characters in free text are rejected.

### Profile validation API

`POST /api/v1/traveler-profile/validate` accepts a profile and returns the complete normalized profile with HTTP 200. Invalid input returns FastAPI’s HTTP 422 `detail` entries with field locations. It performs no database queries and does not save or log profile contents.

```json
{
  "travel_context": { "duration_days": 7, "flexible_dates": true },
  "interests": [{ "name": "photography", "importance": "high" }],
  "travel_style": "balanced",
  "weather_preference": "mild",
  "accessibility": { "requirements": ["avoid_many_stairs"] }
}
```

Open `/discover` to complete four progressive stages: trip context, interests, rhythm, and comfort. Fields use native accessible controls, preserve draft values while moving Back/Continue, and show inline errors. Backend validation is authoritative. A successful response displays a normalized summary with an Edit option. State exists only in memory; leaving or refreshing loses it. Nothing is saved in browser storage or PostgreSQL. `/plan` remains a shell and can later reuse the same contract and preference controls.

## Engineering approach

Keep implemented behavior separate from product intent. Prefer clear module boundaries, explicit configuration, meaningful tests, and standard maintained libraries. Introduce persistence, integrations, and abstractions only when an implemented user journey needs them. The coastal landing-page illustration is CSS artwork, not a destination photo or an application screenshot.

## Recommended next milestone

Review the intake contract with real travel scenarios, then define whether users need to save and revisit a profile. Introduce profile persistence only after its lifecycle and privacy requirements are clear. Destination recommendations and itinerary generation remain separate future milestones.
