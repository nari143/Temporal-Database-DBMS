# Deploying a public demo

The README's screenshots show the app running, but a link a reader can click
is worth more than any screenshot. This is the shortest path to one.

The constraint that shapes every option below: **this app needs the TimescaleDB
extension**, not just PostgreSQL. Hypertables, `time_bucket`, continuous
aggregates, and the compression policy all fail on stock Postgres, so the
managed-Postgres tier of most platforms is not usable here.

## Recommended: Fly.io app + Timescale Cloud database

Timescale Cloud has a free trial and gives a real TimescaleDB instance; Fly
runs the container this repo already builds.

`fly.toml` in the repository root already holds the app configuration: the
web and worker process groups, the `/healthz` check, HTTPS-only cookies, and
a `release_command` that applies pending migrations before each release.

```bash
# 1. Create the database at https://console.cloud.timescale.com
#    Copy the connection details it gives you.

# 2. Pick an app name and put it in fly.toml (`app = "..."`), then register
#    the app without deploying, so secrets can be set first.
fly launch --no-deploy --copy-config --name <your-app-name>

# 3. Point it at the database and set the session key.
fly secrets set \
  DB_HOST=<host>.tsdb.cloud.timescale.com \
  DB_PORT=<port> \
  DB_NAME=tsdb \
  DB_USER=tsdbadmin \
  DB_PASS='<password>' \
  FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# 4. Apply the baseline schema ONCE. schema.sql drops every table, so never
#    run it again on this database; later changes arrive as migrations,
#    which the release_command applies automatically on every deploy.
psql "postgresql://tsdbadmin:<password>@<host>:<port>/tsdb?sslmode=require" \
  -v ON_ERROR_STOP=1 -f schema.sql

# 5. Ship it.
fly deploy
```

Then seed some price history so the charts are not empty:

```bash
fly ssh console -C "python -m scripts.seed_demo --days 90"
```

### Settings that matter in a real deployment

These differ from the local defaults for a reason:

| Setting | Value | Why |
|---|---|---|
| `SESSION_COOKIE_SECURE` | `true` | Fly terminates TLS; the local default is `false` only because localhost is plain HTTP |
| `TRUST_PROXY_HEADERS` | `true` | Fly's proxy sets `X-Forwarded-For`. Without this every request appears to come from the proxy and the rate limiter throttles all users as one. Only ever set this **behind** a proxy that overwrites the header — see the Security section of the README |
| `ENABLE_SCHEDULER` | `false` on web | Each gunicorn worker is a separate process and would otherwise run every job N times. Run the worker as its own Fly process group |
| `RATELIMIT_STORAGE_URI` | Redis, if scaled past one machine | In-process counters are per-machine, so N machines means N times the allowance |

The background jobs already run as their own `worker` process group in
`fly.toml`, so limit-order matching and order expiry run exactly once no
matter how many web machines are serving traffic.

## Continuous deployment from GitHub Actions

`.github/workflows/ci.yml` has a `deploy` job. On every push to `main` (or
`master`) it waits for all four checks (lint, tests, security, Docker smoke
test) to pass, then:

1. runs `flyctl deploy`, whose `release_command` applies pending migrations
   first. A failing migration aborts the release and the old version keeps
   serving;
2. rolls the new version out machine by machine, stopping if `/healthz`
   fails;
3. polls `https://<app>.fly.dev/healthz` and fails the run if the live app
   is not healthy.

Pull requests and other branches never deploy. Until the token below exists,
the job skips itself with a notice instead of failing.

One-time setup, after the manual steps above have worked once:

```bash
# Create a deploy-only token for this app
fly tokens create deploy
```

Add it on GitHub under **Settings → Secrets and variables → Actions → New
repository secret**, named `FLY_API_TOKEN`. The job runs in a `production`
environment, which GitHub creates on first use; adding required reviewers to
that environment turns each release into one-click approval (continuous
delivery) instead of fully automatic continuous deployment.

To roll back a bad release:

```bash
fly releases --image          # find the previous image
fly deploy --image <that image>
```

## A note on the demo account

A public demo that lets anyone register will accumulate junk accounts. Two
options that both work with what is already here:

- Publish one shared read-only-ish account in the README and lower
  `MAX_DEPOSIT`, accepting that visitors can trade with it.
- Leave registration open — it is already rate limited to 5 accounts per hour
  per IP — and reset the database on a schedule with `schema.sql`.

## Once it is live

Add the link to the top of the README, above the badges:

```markdown
**[Live demo](https://<your-app-name>.fly.dev)** · demo / demo-password
```

That single line is the highest-value edit in the whole repository for a
reader deciding in thirty seconds whether to keep reading.
