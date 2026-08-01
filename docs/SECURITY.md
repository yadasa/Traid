# Security

## Secrets

MT5 login, password, server, calendar credentials, and API secrets belong only in backend environment variables or a secret manager. Never put them in `dashboard/`, browser storage, Git history, screenshots, or logs.

## Authentication

The included session layer is intended for a private/single-user deployment. It supports PBKDF2-SHA256 password hashes, expiring bearer sessions, viewer/admin roles, legacy API-key clients, and basic login/order rate limits.

For an internet-facing or multi-user service, use a maintained identity provider with MFA, durable shared sessions, revocation, password recovery, and role administration.

## Network layout

Recommended production layout:

```text
Browser ──HTTPS── Web/API service ──private network── Windows MT5 bridge
```

Do not expose the MT5 terminal or its Python bridge directly to the public internet. Tailscale or another private network is appropriate between the web service and Windows host.

## HTTP hardening

- HTTPS only.
- Exact `TRAID_CORS_ORIGINS`; never `*` with credentials.
- Reverse-proxy request limits.
- Secure headers and CSP.
- Restricted firewall rules.
- Short session TTLs.
- Separate read-only and administrator accounts.
- Back up the SQLite database and protect backup access.

## Execution safety

- Keep trading disabled by default.
- Use a demo account first.
- Set strict lot/position/drawdown limits.
- Require Stop Loss.
- Use a unique magic number.
- Preserve audit logs.
- Test restart reconciliation and trailing behavior.
- Verify the emergency switch before live use.

## Browser storage

The dashboard stores layout preferences in `localStorage` and the session token in `sessionStorage`. MT5 credentials are never sent to or stored by the browser.

## Limitations

The in-memory session registry is cleared when the service restarts and is not suitable for multiple API instances. Migrate sessions and rate limits to Redis or an identity platform when scaling.
