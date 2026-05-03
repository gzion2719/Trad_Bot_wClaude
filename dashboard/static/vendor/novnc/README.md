# noVNC vendor bundle

This directory holds the noVNC HTML5 VNC client used by the gateway console
(`/console.html`). It is **not committed to the repo** — it is fetched at
deploy time by `deploy/setup.sh` (and can be re-fetched by re-running setup).

Pinning is mandatory for security and reproducibility. The pin lives in
`deploy/setup.sh` as the `NOVNC_VERSION` and `NOVNC_SHA256` shell variables.

## Why not commit it?

- ~3MB of vendored JS bloats every git clone
- Updates would need a separate review cycle for a maintenance-only bump
- We already require deploy access (root on VPS) to bring up the dashboard;
  fetching at deploy time adds no new trust assumptions

## Why not load from a CDN?

- Stronger threat model: a CDN compromise (or DNS hijack) would deliver
  attacker JS into a page that has trading authority via the gateway VNC
- SRI hashes mitigate but require maintaining the integrity attribute
  alongside the version pin — the deploy-time fetch is simpler and locks
  the blob to disk

## Fetching manually (dev / testing)

```bash
NOVNC_VERSION=1.5.0
NOVNC_SHA256=<see deploy/setup.sh for the canonical pinned hash>
curl -fsSL -o /tmp/novnc.tgz \
    "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz"
echo "${NOVNC_SHA256}  /tmp/novnc.tgz" | sha256sum -c -
mkdir -p dashboard/static/vendor/novnc
tar -xzf /tmp/novnc.tgz --strip-components=1 \
    -C dashboard/static/vendor/novnc \
    "noVNC-${NOVNC_VERSION}/app" \
    "noVNC-${NOVNC_VERSION}/core" \
    "noVNC-${NOVNC_VERSION}/vendor" \
    "noVNC-${NOVNC_VERSION}/vnc.html" \
    "noVNC-${NOVNC_VERSION}/vnc_lite.html"
rm /tmp/novnc.tgz
```

## CSP note

The console page (`dashboard/static/console.html`) overrides the default
strict CSP because noVNC needs to construct WebGL and websocket contexts.
The override allows `script-src 'self'` and `connect-src 'self' wss:` only —
no `'unsafe-inline'`, no `'unsafe-eval'`, no remote origins.
