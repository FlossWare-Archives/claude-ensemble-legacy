# Gateway liveness, readiness, and configuration

## Liveness versus readiness

- GET /api/v1/health is a liveness check. HTTP 200 means the gateway process is responding; it does not claim downstream services are available.
- GET /api/v1/ready is a readiness check. It probes Graph liveness and Memory readiness through the same forwarding boundary used for service requests. It returns HTTP 200 only when Graph returns a successful health response and Memory reports that its initial search index is ready; otherwise it returns HTTP 503 and a per-dependency result.
- GET /api/v1/graph/health and GET /api/v1/memory/health remain service-specific liveness probes. Memory's GET /api/v1/memory/ready reports search-index warm-up state independently.

Memory remains live while the initial search index warms. Its readiness response reports `warming`, `ready`, `degraded`, `failed`, or `timed_out`, along with indexed/expected file counts and elapsed time. `ENSEMBLE_MEMORY_WARMUP_TIMEOUT_SECONDS` defaults to 30 seconds; exceeding the bound keeps Memory not-ready and reports a diagnostic reason. A warm-up that eventually completes successfully can transition to ready.

Readiness is a point-in-time probe, not a guarantee that a dependency will remain available after the response. It can take up to the upstream request timeout per unavailable dependency.

## Installer success

server/install.sh first enables and starts claude-ensemble.service, then waits up to 20 seconds for /api/v1/ready. It exits nonzero if the unit is inactive or if Graph and Memory do not become ready within that window. The top-level install.sh also verifies that all managed systemd units are active; that unit-state check does not replace the gateway readiness probe.

The installer readiness URL currently assumes the service template's loopback address and port: 127.0.0.1:8080.

## Configuration precedence and loopback boundary

The systemd unit template loads ~/.config/claude-ensemble/environment with EnvironmentFile and then declares ENSEMBLE_HTTP_HOST=127.0.0.1, ENSEMBLE_HTTP_PORT=8080, ENSEMBLE_GRAPH_URL=http://127.0.0.1:8766, and ENSEMBLE_MEMORY_URL=http://127.0.0.1:8767 as later Environment= entries. Those explicit unit entries take precedence; putting these four variables in the environment file does not override them.

This is intentional today: the canonical gateway binds to loopback, and the Graph/Memory endpoints match the local service units. The host must remain loopback-only. Changing ports or service URLs requires updating the unit template and the installer's readiness URL together; do not expose the gateway on a public interface to make a health probe pass.

Other environment variables not explicitly set by the unit can be supplied in the optional environment file. After changing unit configuration, run systemctl --user daemon-reload and restart the service.
