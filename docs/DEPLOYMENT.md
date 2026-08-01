# Deployment

## Local Windows deployment

Recommended for first validation because MT5 and Traid run on the same machine.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-live.txt
Copy-Item .env.example .env
python -m traid_live.cli serve --host 127.0.0.1 --port 8000
```

Serve the dashboard separately:

```powershell
python -m http.server 3000 -d dashboard
```

## Mobile access on a private network

The dashboard is responsive and supports iOS/Android safe areas. To use it from a phone without exposing MT5 publicly:

1. Keep Traid and MT5 on the Windows host.
2. Join the host and phone to the same trusted LAN or Tailscale network.
3. Bind the API/dashboard only to the private interface.
4. use HTTPS through a private reverse proxy where possible.
5. Set `TRAID_CORS_ORIGINS` to the exact dashboard origin.

## Split production deployment

For remote use, separate the public web/API host from the Windows MT5 bridge. Keep the bridge private and authenticated. A production implementation should expose narrow bridge operations rather than the full MT5 process.

## Persistence

`data/traid.db` uses SQLite WAL. Back up the main database together with its WAL state using a SQLite-aware backup operation. Do not commit it to Git.

For multiple API replicas, move forecasts, journals, settings, order idempotency, and audit records to PostgreSQL. Move sessions, forecast jobs, and live coordination to Redis/a task queue.

## Process supervision

Run the API and dashboard/reverse proxy under Windows Task Scheduler, NSSM, systemd (for non-MT5 services), or another supervisor. Ensure graceful restarts and verify that application-managed trailing resumes.

## Observability

Capture:

- API health and latency;
- market quote age;
- model inference duration;
- forecast queue depth;
- MT5 connectivity and trade permission;
- trailing-worker heartbeat;
- rejected/failed order details;
- database backup status.

Do not log passwords, session tokens, or full credential-bearing environment values.
