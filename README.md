# Dokploy Harbor

**A production-oriented Harbor deployment for Dokploy.**

Deploy Harbor on a single Docker host with persistent storage, configurable project policies, vulnerability scanning, and optional encrypted S3 backups. Manage deployment and operations through Dokploy.

[![Harbor v2.15.4](https://img.shields.io/badge/Harbor-v2.15.4-60B932)](https://github.com/goharbor/harbor/releases/tag/v2.15.4)
[![Platform linux/amd64](https://img.shields.io/badge/platform-linux%2Famd64-2496ED)](#requirements)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED)](compose.yaml)
[![Dokploy](https://img.shields.io/badge/deploy-Dokploy-111827)](https://dokploy.com/)

**English** · [Türkçe](README.tr.md)

[Quick start](#quick-start) · [Configuration](#configuration) · [Backups](#backups) · [Restore](#restore) · [Operations](#operations) · [Development](#development)

## What is included

- **Dokploy deployment:** a Compose stack with an initialization container that runs the official Harbor preparation tool.
- **Reproducible images:** upstream image digests, startup commands, and the Harbor configuration template are tracked together.
- **Persistent data:** named volumes preserve registry storage, PostgreSQL, cache state, configuration, and internal keys across deployments.
- **Project policies:** an explicit command applies private projects, quotas, scan-on-push, vulnerability restrictions, retention, and immutable release tags.
- **Optional backups:** Restic encrypts consistent backups to S3-compatible storage. Disable the workflow with ``BACKUP_ENABLED=false``.
- **Operations:** maintenance and recovery tools, health monitoring, private metrics, log rotation, and CI checks.

This is an independent deployment package for [Harbor](https://goharbor.io/) and [Dokploy](https://dokploy.com/). It targets a **single host**; maintenance and host failures can interrupt service. High availability is outside its scope.

## Architecture

~~~mermaid
flowchart TD
    client[Docker clients and browser] -->|HTTPS| traefik[Dokploy / Traefik]
    traefik -->|HTTP :8080| proxy[Harbor proxy]
    proxy --> portal[Portal]
    proxy --> core[Core API]
    core --> registry[Registry]
    core --> db[(PostgreSQL)]
    core --> cache[(Redis / Valkey)]
    core --> jobs[Jobservice]
    jobs --> trivy[Trivy]
    registry --> data[(Persistent image storage)]
~~~

Traefik handles public TLS and certificate renewal. Only the proxy service joins the Traefik network. Internal services do not publish host ports.

## Requirements

| Component | Baseline |
|---|---|
| Host | Linux, amd64 |
| Docker Engine | 27+ |
| Docker Compose | 2.35+ |
| Dokploy | Docker Compose builds and volume subpaths must be supported and preserved |
| Harbor resources | Start with 4 vCPU, 8 GB RAM, and at least 160 GB SSD |
| Network | A dedicated domain pointing to the deployment host; HTTP/HTTPS reachable for the selected certificate flow |

Reserve additional resources for Dokploy and other applications. Size storage and concurrency limits against your workload. Node.js 22+ is needed for repository maintenance tools, not for the deployed Harbor services.

## Quick start

### 1. Connect the repository

Create a Compose service in Dokploy and connect this Git repository. Use **Docker Compose**, with Compose Path set to **./compose.yaml**. Keep automatic production deployments disabled initially.

Builds run from this repository; no manual Harbor preparation command over SSH is required.

### 2. Set environment variables

Use [.env.example](.env.example) as the full configuration reference. For an initial deployment with backups disabled:

~~~dotenv
HARBOR_INSTANCE_ID=harbor-prod
HARBOR_HOSTNAME=harbor.example.com
HARBOR_ADMIN_PASSWORD=
HARBOR_DB_PASSWORD=
BACKUP_ENABLED=false
~~~

Replace the example domain and fill both passwords with different, randomly generated values of at least 20 characters. The hostname must contain no scheme, port, or path. Follow Compose .env quoting rules for special characters, and keep real credentials out of Git.

Keep `HARBOR_INSTANCE_ID` stable after installation: it is the permanent volume prefix and must be unique across deployments.

The package defaults to **``BACKUP_ENABLED=true``**. Set it explicitly to false when deploying without S3 credentials.

### 3. Add the domain in Dokploy

Point your DNS record to the server, then add:

| Dokploy field | Value |
|---|---|
| Host | The same domain as `HARBOR_HOSTNAME` |
| Service | **proxy** |
| Container port | **8080** |
| Path | / |
| HTTPS | Enabled |
| Certificate | Let's Encrypt, or your configured certificate |

Enable HTTP-to-HTTPS redirection. In Preview Compose, confirm that proxy retains both the Harbor network and the Traefik network. Set `DOKPLOY_NETWORK` if your Traefik network has a different name. No host port mapping is needed.

Use a dedicated root domain, such as https://harbor.example.com. Path-based hosting under /harbor is unsupported. Harbor handles registry authentication; adding proxy Basic Auth or ForwardAuth can break Docker clients.

### 4. Deploy and sign in

Click Deploy. The expected sequence is:

1. harbor-init completes successfully with exit code 0.
2. PostgreSQL, cache, and Harbor services become healthy.
3. The HTTPS endpoint becomes available.

Sign in as **admin**, using `HARBOR_ADMIN_PASSWORD`. Check deployment and initialization logs if startup fails. Detailed prepare errors are stored privately in the operations volume as prepare-error.log; redact sensitive content before sharing it.

### 5. Choose how to manage project policies

You can create projects and configure them in the Harbor UI. The optional policies command applies this repository's preset to projects listed in `POLICY_PROJECTS`; it is **not required for Harbor to run**.

To use the preset, set `HARBOR_API_PASSWORD` to the current admin password in Dokploy, redeploy, and run this in the operations container terminal or a Compose Job:

~~~sh
python3 /app/operations.py policies
~~~

Defaults target library, production, and development. Only listed projects are managed. Re-running the command reapplies their settings; it never runs automatically on restart.

### 6. Verify Docker access

~~~sh
docker login harbor.example.com
~~~

Push and pull a test image in your own project, verify its digest, and inspect the scan result. Use project-scoped robot accounts with limited permissions and an expiry for CI/CD.

## Configuration

| Variable | Default / purpose |
|---|---|
| `HARBOR_INSTANCE_ID` | Required; permanent, unique volume prefix |
| `HARBOR_HOSTNAME` | Required; also generates the public HTTPS external URL |
| `HARBOR_ADMIN_PASSWORD` | First-install password only; changing it does not rotate an existing account |
| `HARBOR_DB_PASSWORD` | Database initialization and Harbor connection password; existing databases require SQL rotation |
| `BACKUP_ENABLED` | true; controls automated backup behavior and overdue-backup checks |
| `DOKPLOY_NETWORK` | dokploy-network |
| `PROJECT_QUOTA_GIB` | 20 GiB; default project quota and the policy command's quota |
| `POLICY_PROJECTS` | library,production,development |
| `RETENTION_KEEP_COUNT` / `RETENTION_KEEP_DAYS` | 20 / 30; development artifact retention |
| `JOB_MAX_WORKERS` | 5 |
| `DB_MAX_OPEN_CONNS` / `POSTGRES_MAX_CONNECTIONS` | 100 / 256; reserve capacity for both core and exporter pools |
| `ALLOW_PRIVATE_NETWORK_ACCESS` | false; controls webhook access to private networks |
| `*_MEMORY_LIMIT` / `*_CPU_LIMIT` | Per-service resource limits |
| `HARBOR_HTTP_PROXY` / `HARBOR_HTTPS_PROXY` / `HARBOR_NO_PROXY` | Outbound proxy configuration |

All variables and resource defaults are documented in [.env.example](.env.example). Harbor release changes are made through the version lock and update tool, rather than an arbitrary environment override.

### Security and policy defaults

From first startup, self-registration is disabled, authentication uses local accounts, and project creation is restricted to administrators. These settings are managed through core configuration.

The optional policy preset:

- Makes listed projects private, enables scan-on-push, and blocks pulls with critical vulnerabilities.
- Applies the configured project quota.
- Protects v* and release-* tags from overwrites through immutability rules.
- Retains the latest 20 dev-* artifacts **or** those pulled within 30 days; rules use Harbor's OR semantics. Other tags and untagged artifacts are retained.
- Schedules retention daily at **22:00 UTC**. Image signature enforcement is disabled initially.

Trivy is enabled with registry certificate verification and vulnerability database updates. Policies can be managed manually in the UI instead of using the preset. The initial library project's visibility is changed by the policy command, not by initialization.

## Backups

### Enable encrypted S3 backups

~~~dotenv
BACKUP_ENABLED=true
RESTIC_REPOSITORY=s3:https://s3.example.com/backup-bucket/harbor-prod
RESTIC_PASSWORD=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
~~~

Fill the credentials and a separate Restic encryption password of at least 20 characters. Use storage independent of the Harbor host and retain the encryption password outside that host.

1. Redeploy after saving the settings.
2. Once, initialize a **new** repository in an operations Compose Job:

   ~~~sh
   python3 /app/operations.py init-repository
   ~~~

3. Copy [scripts/dokploy-job.sh](scripts/dokploy-job.sh) into a Dokploy Server Job on the Harbor host. Set the correct instance ID and `ACTION=backup`. For the local Dokploy host, use a Dokploy Server Job; for a remote host, use a Server Job.
4. Schedule it daily, for example **0 3 * * *** with Europe/Istanbul, or **0 0 * * *** with UTC. Verify the next execution time in Dokploy.
5. Run it manually and inspect snapshots:

   ~~~sh
   python3 /app/operations.py snapshots
   ~~~

Do not initialize an existing Restic repository again. Default retention keeps **7 daily, 4 weekly, and 6 monthly** backups, scoped to this instance's Harbor backups.

### Consistency and service interruption

The job locks operations, stops writers, creates a PostgreSQL logical dump, cleanly stops the data services, and backs up registry data, PostgreSQL, cache state, job data, configuration, and internal keys as one encrypted set.

**Harbor remains offline until the Restic upload completes.** The first full backup can take time. Services are restarted before retention/pruning. Re-downloadable Trivy databases and reports are excluded. Restore staging requires additional local disk space.

Normal failures attempt service recovery. A forcibly terminated job leaves a durable maintenance record; run the Server Job with `ACTION=recover` to recover the recorded containers. Short-lived operational helpers receive Docker socket access; resident Harbor and operations containers do not mount the socket.

### Disable backups

Set **``BACKUP_ENABLED=false``** and redeploy:

- Scheduled and manual backup calls return disabled/skipped.
- No backup lock, S3 request, service shutdown, or backup pruning is performed.
- S3 credentials are optional, and overdue-backup checks are skipped.
- Existing backups are preserved. Listing snapshots and restoring remain available when credentials are supplied.

Let an active backup finish before applying the change. To re-enable backups, restore the credentials, set the flag to true, redeploy, and verify a successful backup. Do not reinitialize an existing repository.

Daily backups target **RPO ≤24 hours** and **RTO ≤4 hours**; validate both with your actual data and a restore rehearsal. They do not apply when backups are disabled.

## Restore

Restores require **empty destination data and configuration volumes**. Existing data is never automatically deleted.

1. Create a separate Dokploy Compose application with a new, unique instance ID. Use the **exact Harbor version in the snapshot** and Compose Path **./compose.restore.yaml**.
2. Configure the destination hostname and backup credentials. `BACKUP_ENABLED` may be false. Deploy; this starts recovery initialization and operations only, without Harbor workloads.
3. List snapshots in the operations container and select an explicit snapshot ID.
4. Adapt the Server Job template to the destination instance and `ACTION=restore`. Append **`--snapshot SNAPSHOT_ID --confirm-target DESTINATION_INSTANCE_ID`** after the action in its final docker run command.
5. Run the job. It verifies the snapshot, version, and empty targets, then restores ownership and permissions. Snapshots containing symlinks are refused. Use a new empty target after an interrupted restore.
6. Set the **original database password** from the snapshot, change Compose Path to **./compose.yaml**, and deploy. Initialization preserves restored keys while generating the destination configuration.
7. Configure HTTPS routing and verify accounts, permissions, projects, and artifact digests before moving clients. Configure backups separately for the new instance.

## Operations

Run these commands in the operations container through Dokploy Compose Jobs:

| Task | Command | Suggested frequency |
|---|---|---|
| Apply the optional preset | `python3 /app/operations.py policies` | Explicitly, when desired |
| Garbage collection | `python3 /app/operations.py gc` | Weekly, apart from backups |
| Monitoring | `python3 /app/operations.py monitor` | Every 15 minutes |
| Backup status | `python3 /app/operations.py check` | On demand |

GC shares the operation lock with backup and keeps a durable marker if polling is interrupted. Re-run gc to reconcile that job. Avoid a separate automatic GC schedule in Harbor that bypasses this coordination.

Monitoring checks disk usage (75% warning / 85% critical), Harbor health, trusted HTTPS, certificates with less than 14 days remaining, missing or stale Trivy data (48 hours), and overdue enabled backups (26 hours). Availability checks are deferred during planned maintenance; maintenance exceeding 4 hours is reported. Thresholds are configurable in .env.example.

Set `NOTIFICATION_WEBHOOK` and optionally `NOTIFICATION_BEARER_TOKEN` for JSON notifications. The webhook must use HTTPS. Trivy database warnings before the first image scan can be resolved by completing a scan.

Metrics are available privately through **proxy:9090/metrics**. Adapt [the Prometheus configuration](monitoring/prometheus.yml) and [alert example](monitoring/alerts.yml) to an existing monitoring service attached to the Harbor network. Logs use Docker's rotating local driver and are accessible through Dokploy.

### Change configuration or upgrade

Backup/notification/operation settings can be changed by saving Environment and redeploying without regenerating Harbor service configuration. If an operation holds the lock, retry deployment after it finishes.

For changes to Harbor configuration, hostname, database, workers, outbound proxy, default quota, or release:

1. Obtain a consistent backup for rollback.
2. Run the Server Job with `ACTION=maintenance`. It stops Harbor workloads and leaves operations running.
3. Update settings or Git revision, then deploy. Initialization preserves internal keys and regenerates configuration.
4. Verify health, login, and push/pull. Preserve volumes throughout the process.

Use `ACTION=recover` to cancel an unchanged maintenance session. Complete interrupted preparation or restoration through initialization/redeployment. Database/schema rollback may require restoring the matching database, registry, and keys together with the previous images.

Rotate the admin password in Harbor and update `HARBOR_API_PASSWORD`. Changing `HARBOR_ADMIN_PASSWORD` affects first installation only. Database password rotation requires coordinating the PostgreSQL role password with `HARBOR_DB_PASSWORD` during maintenance; an environment edit alone does not rotate it.

## Development

~~~sh
# Check locked images, template integrity, and generated Compose structure
node tools/check.mjs

# Inspect the latest stable Harbor release without modifying files
node tools/update-images.mjs --check

# Refresh Harbor images, startup metadata, template, and Compose definitions
node tools/update-images.mjs

# Linux Python 3.10+: standard-library unit tests
python3 -m unittest discover -s tests -v

# Run the tests in Docker
sh tools/test.sh
~~~

Review release changes and supported upstream upgrade paths before deployment. Editing the Compose generator requires regenerating files with `node tools/render-compose.mjs`.

The [GitHub Actions workflow](.github/workflows/validate.yml) defines structural checks, unit tests, and Compose validation. Manual Workflow Dispatch with integration enabled runs the Docker smoke test on an isolated runner. Before production use, validate trusted TLS, real push/pull, permissions, scanner behavior, resource limits, and an S3 restore when backups are enabled. See the [validation record](VALIDATION.md) for recorded checks and limits.

## References and attribution

- [Harbor installation and configuration](https://goharbor.io/docs/2.15.0/install-config/)
- [Harbor releases](https://github.com/goharbor/harbor/releases)
- [Dokploy Docker Compose](https://docs.dokploy.com/docs/core/docker-compose)
- [Dokploy domain routing](https://docs.dokploy.com/docs/core/docker-compose/domains)
- [Dokploy scheduled jobs](https://docs.dokploy.com/docs/core/schedule-jobs)

The vendored Harbor configuration template retains its upstream attribution and Apache 2.0 license. See [vendor/NOTICE.md](vendor/NOTICE.md) and [vendor/HARBOR-LICENSE](vendor/HARBOR-LICENSE).
