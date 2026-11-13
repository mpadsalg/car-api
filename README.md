# car-api

FastAPI service (Python 3.13, uv, SQLAlchemy 2).

```bash
UV_PYTHON_DOWNLOADS=never uv sync --frozen --python 3.13
cp .env.example .env         # then set CARAPI_API_KEYS (see Auth); loads seed/cars.csv
uv run --no-sync carapi      # dev server on :8000 (reload)
uv run --no-sync pytest
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
```

## Config
Env vars (prefix `CARAPI_`, or `.env` — see `.env.example`):

| Var | Default |
|---|---|
| `CARAPI_APP_NAME` | `car-api` |
| `CARAPI_DATABASE_URL` | `sqlite+pysqlite:///:memory:` (in-memory, lost on restart) |
| `CARAPI_SQL_ECHO` | `false` |
| `CARAPI_LOG_LEVEL` | `INFO` |
| `CARAPI_API_KEYS` | empty — comma-separated; **empty ⇒ every write is `401`** |
| `CARAPI_RATE_LIMIT_ENABLED` | `true` |
| `CARAPI_RATE_LIMIT_REQUESTS` | `60` |
| `CARAPI_RATE_LIMIT_WINDOW_SECONDS` | `60` |
| `CARAPI_SEED_FILE` | unset — CSV loaded on startup (atomic; bad/missing file ⇒ startup fails) |
| `CARAPI_IMPORT_MAX_BYTES` | `5000000` |
| `CARAPI_IMPORT_MAX_ROWS` | `10000` |

Tables are created on startup via `Base.metadata.create_all`.

## Endpoints
- `GET /health` — liveness
- `GET /health/db` — DB check (`503` if down)
- `POST /cars` — create (`201` + `Location`; `409` duplicate VIN; `422` invalid)
- `GET /cars` — search/filter/sort/paginate (see below)
- `POST /cars/import` — bulk CSV import (key required; see below)
- `GET /cars/export.csv` — CSV download of matching cars (public)
- `GET /cars/{id}` — get (`404` if missing)
- `PATCH /cars/{id}` — partial update (only `color` may be set to `null`)
- `DELETE /cars/{id}` — delete (`204`)

### Car
| Field | Rules |
|---|---|
| `vin` | 17 chars, `A-H J-N P R-Z 0-9` (no I/O/Q), trimmed + upper-cased, unique |
| `make`, `model` | 1–50 chars |
| `year` | 1886 … current year + 1 |
| `price` | ≥ 0, max 2 decimals; stored as integer cents (exact) |
| `mileage` | 0 … 5,000,000 |
| `color` | optional, 1–30 chars |
| `status` | `available` (default) \| `reserved` \| `sold` |
| `created_at`, `updated_at` | server-set, UTC |

### `GET /cars` query params
| Param | Behaviour |
|---|---|
| `q` | case-insensitive substring over make/model/vin/color (`%`/`_` literal) |
| `make`, `model`, `color` | exact, case-insensitive |
| `status` | repeatable: `?status=available&status=reserved` |
| `year_min/max`, `price_min/max`, `mileage_min/max` | inclusive; `min > max` → `422` |
| `sort` | comma list, `-` = desc, e.g. `-price,year`. Fields: `id make model year price mileage created_at updated_at`. `id` tie-breaker appended |
| `page` | ≥ 1 (default 1) |
| `size` | 1–100 (default 20) |

Unknown params → `422`. Response:
```json
{"items": [...], "total": 6, "page": 1, "size": 20, "pages": 1}
```

## Auth
No keys ship with the repo. Generate one and put it in `.env` (git-ignored):
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # -> CARAPI_API_KEYS=<value>
export CARAPI_KEY=<same value>                                   # for the curl examples
```
`POST`/`PATCH`/`DELETE /cars*` require `X-API-Key: <one of CARAPI_API_KEYS>` (constant-time compare).
Missing/invalid → `401` + `WWW-Authenticate: ApiKey`. Auth runs before lookup, so `401` never leaks whether an id exists.
Reads and `/health*` are public. Swagger UI (`/docs`) has an **Authorize** button.

## Rate limiting
In-memory sliding window, `CARAPI_RATE_LIMIT_REQUESTS` per `CARAPI_RATE_LIMIT_WINDOW_SECONDS`.
- Bucket = valid API key (per key), otherwise client IP (invalid keys share the IP bucket, so rotating bogus keys doesn't help).
- Exempt: `/health*`, `/docs`, `/redoc`, `/openapi.json`.
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (s); `429` adds `Retry-After`.
- Per process; resets on restart. Behind a proxy the client IP is the proxy's (`X-Forwarded-For` is not trusted).

## Errors
Every error (incl. 404 route, 405, 422, 429, unhandled 500) has one shape:
```json
{"error": {"code": "not_found", "message": "Car 999 not found", "request_id": "…"}}
```
`422` adds `details: [{"loc": [...], "msg": "...", "type": "..."}]`. Codes: `bad_request unauthorized forbidden not_found method_not_allowed conflict validation_error rate_limited internal_error service_unavailable`. 500s never expose exception text.

## Request IDs & logging
`X-Request-ID` is echoed if it matches `[A-Za-z0-9._-]{1,128}`, otherwise a uuid4 hex is generated. It's returned on every response, embedded in error bodies, and prefixed on every `carapi.*` log line:
```
2026-11-12 20:31:38,573 INFO [b8eb86ce…] carapi.access: POST /cars 201 3.6ms client=127.0.0.1
```

## CSV import / export
Columns: `vin,make,model,year,price,mileage[,color,status]` — any order, header case-insensitive, UTF-8 (BOM ok).
`id,created_at,updated_at` are accepted and ignored, so an export re-imports as-is.

```bash
# import (body is the raw CSV, Content-Type text/csv)
curl -XPOST 'localhost:8000/cars/import?mode=partial' \
  -H "X-API-Key: $CARAPI_KEY" -H 'content-type: text/csv' --data-binary @seed/cars.csv

# export — same filters/sort as GET /cars, no page/size
curl -OJ 'localhost:8000/cars/export.csv?make=honda&sort=-price'
```

| Import outcome | Status |
|---|---|
| `mode=atomic` (default), all rows valid | `200` report |
| `mode=atomic`, any row invalid | `422`, nothing written, report in `error.details` |
| `mode=partial` | `200`, good rows written, bad rows in `errors` |
| bad encoding / header / malformed CSV | `400` |
| over `IMPORT_MAX_BYTES` or `IMPORT_MAX_ROWS` | `413` |
| Content-Type not `text/csv`/`application/csv` | `415` |

Report: `{"mode","rows","created","failed","errors":[{"line","vin","errors":[{"field","msg"}]}],"errors_truncated"}` (first 1000 errors).
`line` is the physical line in the file. Duplicate VINs are caught within the file and against the DB.

Export cells in `make/model/color` starting with `= + - @` are prefixed with `'` (spreadsheet formula injection); import strips it.

`seed/cars.csv` — 25 cars across 13 makes; VINs have valid ISO 3779 check digits and model-year codes.
