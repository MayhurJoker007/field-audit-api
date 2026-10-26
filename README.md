# Field Audit API

![CI](https://github.com/MayhurJoker007/field-audit-api/actions/workflows/ci.yml/badge.svg)

Backend for field reps auditing retail outlets: a rep visits a spaza shop or tavern, answers a short
questionnaire (is the fridge there, how much of it is our stock, is the core range on the shelf), takes photos,
and submits from their phone.

The interesting part isn't storing the audit. It's deciding whether to **trust** it. Field data gets faked or
fumbled in predictable ways, and this API catches the common ones.

**Stack:** FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL (SQLite for local dev and tests), PyJWT, Docker,
GitHub Actions.

## What it checks

| Check | Why |
|---|---|
| **Score calculated on the server** from raw answers, with every answer type and range validated | the app shows a score too, but a client-side number can't go into a client report |
| **Geofence**: GPS more than 200 m from the outlet → `outside_geofence` | audits "done" from the car park, or from home |
| **Reused photo**: same SHA-256 already attached to another audit → `reused_photo`, linked to the original | the classic way to fake a visit |
| **Late upload**: visit more than 2 days before upload → `late_upload` | memory-based answers are less reliable |
| **Repeat visit**: same rep, same outlet, same day → `repeat_visit_same_day` | usually a mistake, sometimes padding numbers |
| **Visit date in the future** → rejected | phone clock wrong |
| **Photo type from the file bytes**, not the filename or header; 8 MB limit | renamed files and junk uploads |

Flagged audits aren't rejected. They're saved and listed at `GET /audits?flagged=true` for a supervisor
to review. Rejecting them outright would just teach reps to work around the checks.

**Retries are safe.** Phones on 3G time out and resend. Each audit carries a `client_ref` generated on the
phone, and a unique constraint on (rep, client_ref) means a resend returns the original audit instead of creating
a second one, even when both requests arrive at the same time. Photo uploads are idempotent by hash.

**Access:** reps only see their own audits. Supervisors see everything and get the outlet score report (average,
latest, and trend since the previous visit).

## Running

```
docker compose up
```

That starts Postgres, runs the migrations and serves on http://localhost:8000. Interactive docs are at `/docs`.
To create demo users and outlets:

```
docker compose exec api python seed.py
```

Without Docker:

```
pip install -r requirements-dev.txt
alembic upgrade head
python seed.py
uvicorn app.main:app --reload
```

Demo logins are `thabo` / `field-demo-1`, `lerato` / `field-demo-2`, and `supervisor` / `field-demo-3`.

## Endpoints

| | | |
|---|---|---|
| POST | `/token` | log in (OAuth2 password form), returns a JWT |
| GET | `/outlets/nearby?lat=&lng=&radius_m=` | outlets around the rep, nearest first |
| POST | `/audits` | submit an audit |
| POST | `/audits/{id}/photos` | attach a photo |
| GET | `/audits?flagged=&outlet_id=` | list audits |
| GET | `/reports/outlet-scores` | per-outlet scores and trend (supervisors) |

## Tests

```
pytest
```

17 tests: the scoring rules and validation, auth, idempotent resubmits, each flag, reused photos across reps,
file type sniffing, rep and supervisor visibility, and the score report. CI also runs the Alembic migrations up,
seeds and back down against a real Postgres service container.
