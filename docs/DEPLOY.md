# Deploying a public demo

The constraint that shapes everything below: **this app needs the TimescaleDB
extension**, not just PostgreSQL. Hypertables, `time_bucket`, continuous
aggregates and the compression policy all fail on stock Postgres, so the
managed-Postgres tier of most platforms is not usable here.

## Setup: Render (app) + Tiger Cloud (database)

Both have a no-card starting point: Render's free web service plan, and Tiger
Cloud's (Timescale's) free trial.

### 1. Database (Tiger Cloud)

1. Create a service at https://console.cloud.timescale.com (Development, no
   replicas, no connection pooler). Download the credentials file it offers:
   the password is shown only once.
2. Open **Data view**, connect to the service, paste the whole of
   `schema.sql`, select all, and run it. Run it **once**: it drops every table
   first. Later schema changes arrive as files in `migrations/`, which the app
   applies itself on every start.
3. Check: `SELECT count(*) FROM assets;` returns 25.

### 2. App (Render)

1. Sign up at https://render.com with GitHub.
2. **New → Blueprint**, pick this repository. Render reads `render.yaml`.
3. Fill in the three values it asks for, from the credentials file:
   `DB_HOST`, `DB_PORT`, `DB_PASS`. Everything else is preset, and
   `FLASK_SECRET_KEY` is generated for you.
4. Apply. The first deploy builds the Docker image and starts
   `scripts/render_start.sh`, which:
   - applies pending migrations,
   - seeds 90 days of demo prices if the database has none,
   - starts the limit-order/expiry scheduler,
   - starts gunicorn.
5. Open the URL Render shows (`https://<service>.onrender.com`).

The free plan sleeps after about 15 minutes without traffic; the next visit
takes 30 to 60 seconds to wake it.

### 3. Continuous deployment

`render.yaml` sets `autoDeploy: false`, so Render never deploys on its own.
Releases come from the `deploy` job in `.github/workflows/ci.yml`, which runs
on every push to `main` only after lint, tests, security checks and the Docker
smoke test have all passed. It:

1. calls Render's deploy hook for exactly that commit;
2. polls `/healthz` until it reports that commit as its `version`
   (Render sets `RENDER_GIT_COMMIT` in the container), failing the run if
   that does not happen within 15 minutes.

Render switches traffic to a new deploy only once its health check passes, so
a broken release never replaces the working one.

One-time setup on GitHub, under **Settings → Secrets and variables →
Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `RENDER_DEPLOY_HOOK_URL` | Render dashboard → the service → Settings → Deploy Hook |
| Variable | `APP_URL` | The service URL, e.g. `https://temporal-trading.onrender.com` |

Until both exist, the deploy job skips itself with a notice.

To roll back, open the service's **Events** in Render and redeploy an earlier
deploy.

### Settings that matter in a real deployment

| Setting | Value | Why |
|---|---|---|
| `SESSION_COOKIE_SECURE` | `true` | Render terminates TLS; the local default is `false` only because localhost is plain HTTP |
| `TRUST_PROXY_HEADERS` | `true` | Render's proxy sets `X-Forwarded-For`. Without this every request appears to come from the proxy and the rate limiter throttles all users as one. Only ever set this **behind** a proxy |
| `PGSSLMODE` | `require` | Tiger Cloud accepts TLS connections only |
| `RATELIMIT_STORAGE_URI` | Redis, if scaled past one instance | In-process counters are per-instance |

The free plan has no separate background-worker service, so the scheduler
runs inside the web container next to a single gunicorn process. On a paid
plan, move `python -m backend.worker` to its own Render background worker.

## A note on the demo account

A public demo that lets anyone register will accumulate junk accounts.
Registration is already rate limited to 5 accounts per hour per IP; reset the
data on a schedule if needed.

## Once it is live

Add the link to the top of the README, above the badges:

```markdown
**[Live demo](https://<service>.onrender.com)**
```
