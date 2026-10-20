"""Backup, maintenance and recovery. Credentials are read from a private volume."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from common import ConfigError, atomic_json, operation_lock, read_json
from docker_api import Docker

OPERATIONS = Path('/operations')
CONTROL = Path('/control')
BACKUP = Path('/backup')
DATA_NAMES = ('data', 'database', 'registry', 'redis', 'jobs', 'config')
# Reverse for startup: dependency services first, traffic entry last.
STOP_ORDER = ('proxy', 'exporter', 'jobservice', 'trivy-adapter', 'core', 'registryctl', 'registry', 'portal', 'redis', 'postgresql')

def now():
    return datetime.now(timezone.utc).isoformat()

def load_settings():
    return read_json(OPERATIONS / 'settings.json')

def restic(settings, args, capture=True):
    environment = dict(os.environ)
    environment.update({k: v for k, v in settings['restic_env'].items() if v})
    environment['RESTIC_CACHE_DIR'] = '/tmp/restic-cache'
    result = subprocess.run(['restic', *args], env=environment, stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE)
    if result.returncode:
        # Backend errors can contain signed URLs. Keep credentials and stderr out of public logs.
        raise ConfigError(f'Restic {args[0]} failed with exit code {result.returncode}; verify backend access and repository health')
    return result.stdout or b''

def notify(settings, event):
    print(json.dumps(event, sort_keys=True), flush=True)
    webhook = settings.get('notification_webhook')
    if not webhook:
        return
    headers = {'Content-Type': 'application/json'}
    if settings.get('notification_token'):
        headers['Authorization'] = 'Bearer ' + settings['notification_token']
    try:
        with urlopen(Request(webhook, data=json.dumps(event).encode(), headers=headers), timeout=15) as response:
            response.read(4096)
    except Exception:
        print('Notification delivery failed', file=sys.stderr)

def status(settings, state, **extra):
    event = {'state': state, 'instance': settings['instance'], 'finished_at': now(), **extra}
    atomic_json(OPERATIONS / 'last-backup.json', event)
    notify(settings, event)
    return event

def maintenance(kind, **extra):
    atomic_json(CONTROL / 'maintenance.json', {'kind': kind, 'created_at': now(), **extra}, mode=0o644)

def running(containers):
    return {name: item for name, item in containers.items() if item['State'] == 'running'}

def restart(docker, containers, timeout):
    failures = []
    for name in reversed(STOP_ORDER):
        if name in containers:
            try:
                docker.start(containers[name]['Id'])
                deadline = time.monotonic() + timeout
                while True:
                    state = docker.inspect(containers[name]['Id'])['State']
                    health = state.get('Health', {}).get('Status', 'healthy')
                    if state['Running'] and health == 'healthy':
                        break
                    if time.monotonic() >= deadline:
                        raise ConfigError('Health check timed out')
                    time.sleep(2)
            except Exception:
                failures.append(name)
    if failures:
        raise ConfigError('Failed to restart healthy services: ' + ', '.join(failures))

def backup(settings, docker_factory=Docker):
    if not settings['backup_enabled']:
        print('backup: disabled/skipped', flush=True)
        return 'skipped'
    path = OPERATIONS / 'last-backup.json'
    previous = path.read_bytes() if path.exists() else None
    try:
        return _backup(settings, docker_factory)
    except Exception as error:
        current = path.read_bytes() if path.exists() else None
        if previous == current:
            status(settings, 'preflight-failed', error=type(error).__name__)
        raise

def _backup(settings, docker_factory=Docker):
    # This guard precedes locks, Docker access, repository requests and maintenance.
    if not settings['backup_enabled']:
        print('backup: disabled/skipped', flush=True)
        return 'skipped'
    with operation_lock(OPERATIONS):
        if (CONTROL / 'maintenance.json').exists():
            raise ConfigError('Maintenance is already active; backup was not started')
        if (OPERATIONS / 'gc-active.json').exists():
            raise ConfigError('A GC operation needs completion/reconciliation before backup')
        # Fail before downtime if the repository is unavailable or not initialized.
        restic(settings, ['cat', 'config'])
        docker = docker_factory(settings['instance'])
        containers = running(docker.containers())
        required = {'proxy', 'core', 'registry', 'postgresql', 'redis', 'jobservice'}
        if not required.issubset(containers):
            raise ConfigError('Harbor is not fully running; backup was not started')
        for name, item in containers.items():
            state = docker.inspect(item['Id'])['State']
            if state.get('Health', {}).get('Status', 'healthy') != 'healthy':
                raise ConfigError('Harbor is unhealthy; backup was not started')
        record = {name: item['Id'] for name, item in containers.items()}
        maintenance('backup', containers=record)
        error = None
        snapshot = None
        try:
            for name in STOP_ORDER:
                if name not in ('postgresql', 'redis') and name in containers:
                    docker.stop(containers[name]['Id'])
            dump = BACKUP / 'dump'
            dump.mkdir(parents=True, exist_ok=True)
            dump_file = dump / 'postgres.sql'
            from initialize import parse_literal_env
            password = parse_literal_env((BACKUP / 'config/db/env').read_text())['POSTGRES_PASSWORD']
            with dump_file.open('wb') as output:
                os.chmod(dump_file, 0o600)
                docker.dump_database(containers['postgresql']['Id'], output, password)
            docker.stop(containers['redis']['Id'])
            docker.stop(containers['postgresql']['Id'])
            # A forced kill cannot produce an accepted cold database backup.
            for name in ('postgresql', 'redis'):
                stopped = docker.inspect(containers[name]['Id'])['State']
                if stopped.get('OOMKilled') or stopped.get('ExitCode') == 137:
                    raise ConfigError('A data service was forcibly killed; cold backup refused')
            manifest_dir = BACKUP / 'manifest'
            manifest_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(manifest_dir / 'snapshot.json', {
                'harbor_version': settings['harbor_version'], 'instance': settings['instance'],
                'created_at': now(), 'format': 1,
            })
            shutil.copy2(OPERATIONS / 'images.lock.json', manifest_dir / 'images.lock.json')
            args = ['backup', '--json', '--host', settings['instance'], '--tag', 'harbor', '--tag', settings['instance']]
            args += [str(BACKUP / name) for name in (*DATA_NAMES, 'dump', 'manifest')]
            raw = restic(settings, args)
            for line in raw.splitlines():
                item = json.loads(line)
                if item.get('message_type') == 'summary':
                    snapshot = item.get('snapshot_id')
            if not snapshot:
                raise ConfigError('Restic did not return a completed snapshot')
        except BaseException as exc:
            error = exc
        finally:
            # Keep the durable marker on abrupt death; recover command reads exact container IDs.
            (CONTROL / 'resuming').touch(mode=0o644)
            try:
                restart(docker, containers, settings['health_timeout'])
            except Exception as exc:
                maintenance('backup-recovery', containers=record)
                error = exc
            else:
                (CONTROL / 'maintenance.json').unlink(missing_ok=True)
            finally:
                (CONTROL / 'resuming').unlink(missing_ok=True)
            (BACKUP / 'dump/postgres.sql').unlink(missing_ok=True)
        if error:
            status(settings, 'failed', error=type(error).__name__, snapshot=snapshot)
            raise ConfigError('Backup or service recovery failed; inspect last-backup status and run recover if required') from error
        # Pruning is performed after service recovery, under the same operation lock.
        try:
            restic(settings, ['forget', '--prune', '--host', settings['instance'], '--tag', 'harbor',
                              '--keep-daily', str(settings['keep_daily']), '--keep-weekly', str(settings['keep_weekly']),
                              '--keep-monthly', str(settings['keep_monthly'])])
        except Exception:
            status(settings, 'retention-failed', snapshot=snapshot)
            raise
        status(settings, 'success', snapshot=snapshot)
        return snapshot

def begin_maintenance(settings):
    with operation_lock(OPERATIONS):
        if (CONTROL / 'maintenance.json').exists():
            raise ConfigError('Maintenance already active; do not overwrite recovery state')
        docker = Docker(settings['instance'])
        containers = running(docker.containers())
        maintenance('reconfigure', containers={n: c['Id'] for n, c in containers.items()})
        for name in STOP_ORDER:
            if name in containers:
                docker.stop(containers[name]['Id'])
        print('Workload stopped. Update Environment and redeploy from Dokploy; preserve all volumes.')

def recover(settings):
    with operation_lock(OPERATIONS):
        marker = CONTROL / 'maintenance.json'
        if not marker.exists():
            raise ConfigError('No recovery marker exists')
        record = read_json(marker)
        if record['kind'] not in ('backup', 'backup-recovery', 'reconfigure'):
            raise ConfigError('This maintenance state needs restore/redeployment, not automatic recovery')
        docker = Docker(settings['instance'])
        available = docker.containers()
        containers = {}
        for name, container_id in record.get('containers', {}).items():
            if name not in available or available[name]['Id'] != container_id:
                raise ConfigError('Containers changed since maintenance; resolve from Dokploy before recovery')
            containers[name] = available[name]
        (CONTROL / 'resuming').touch(mode=0o644)
        try:
            restart(docker, containers, settings['health_timeout'])
        except Exception:
            raise
        else:
            marker.unlink()
        finally:
            (CONTROL / 'resuming').unlink(missing_ok=True)
        print('Recorded services recovered')

def restore(settings, snapshot, confirm):
    if confirm != settings['instance']:
        raise ConfigError('Pass --confirm-target with the exact destination HARBOR_INSTANCE_ID')
    if not snapshot or not all(c in '0123456789abcdef' for c in snapshot) or len(snapshot) < 8:
        raise ConfigError('Use an explicit Restic snapshot ID (at least 8 hex characters)')
    with operation_lock(OPERATIONS):
        docker = Docker(settings['instance'])
        if docker.containers():
            raise ConfigError('Restore requires compose.restore.yaml with no Harbor workload containers')
        for name in DATA_NAMES:
            if any((BACKUP / name).iterdir()):
                raise ConfigError('Restore target volumes must be empty; existing data will never be deleted')
        stage = Path('/restore')
        stage.mkdir(parents=True, exist_ok=True)
        if any(stage.iterdir()):
            raise ConfigError('Restore staging volume is not empty; inspect it before retrying')
        maintenance('restore-in-progress')
        restic(settings, ['restore', snapshot, '--target', str(stage), '--verify'])
        source = stage / 'backup'
        manifest = read_json(source / 'manifest/snapshot.json')
        if manifest.get('format') != 1 or manifest['harbor_version'] != settings['harbor_version']:
            raise ConfigError('Restore first with the exact Harbor version recorded in the snapshot')
        for item in source.rglob('*'):
            if item.is_symlink():
                raise ConfigError('Symlinks are not supported in restore sets; review the snapshot manually')
        def copy_owned(src, dst):
            shutil.copy2(src, dst)
            stat = os.stat(src)
            os.chown(dst, stat.st_uid, stat.st_gid)
            return dst
        for name in DATA_NAMES:
            src = source / name
            if not src.is_dir():
                raise ConfigError('Snapshot is missing a required data directory')
        for name in DATA_NAMES:
            src, dst = source / name, BACKUP / name
            shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=copy_owned)
            for item in [src, *src.rglob('*')]:
                if item.is_dir():
                    target = dst / item.relative_to(src)
                    stat = item.stat()
                    os.chown(target, stat.st_uid, stat.st_gid)
                    os.chmod(target, stat.st_mode & 0o777)
        atomic_json(OPERATIONS / 'images.lock.json', read_json(source / 'manifest/images.lock.json'))
        maintenance('restore', source_instance=manifest['instance'], snapshot=snapshot)
        print('Restore completed. Set the original DB password, switch to compose.yaml and redeploy; then verify artifact digests.')

def check_status(settings):
    if not settings['backup_enabled']:
        print('backup: disabled/skipped')
        return
    path = OPERATIONS / 'last-backup.json'
    if not path.exists():
        raise ConfigError('No successful backup recorded')
    value = read_json(path)
    age = datetime.now(timezone.utc) - datetime.fromisoformat(value['finished_at'])
    if value['state'] != 'success' or age.total_seconds() > settings['backup_max_age_hours'] * 3600:
        notify(settings, {'state': 'backup-overdue-or-failed', 'instance': settings['instance']})
        raise ConfigError('Backup failed or is overdue')
    print(json.dumps(value))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['idle', 'enabled', 'backup', 'init-repository', 'snapshots', 'check',
                                          'maintenance', 'recover', 'restore', 'policies', 'gc', 'monitor'])
    parser.add_argument('--snapshot')
    parser.add_argument('--confirm-target')
    args = parser.parse_args()
    if args.command == 'idle':
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
        while True:
            time.sleep(3600)
    settings = load_settings()
    if args.command == 'enabled':
        print('true' if settings['backup_enabled'] else 'false')
        return
    if args.command == 'backup':
        backup(settings)
    elif args.command == 'init-repository':
        if not settings['backup_enabled']:
            print('backup: disabled/skipped')
            return
        with operation_lock(OPERATIONS):
            restic(settings, ['init'])
            print('Encrypted Restic repository initialized')
    elif args.command == 'snapshots':
        print(restic(settings, ['snapshots', '--json', '--tag', 'harbor']).decode())
    elif args.command == 'check':
        check_status(settings)
    elif args.command == 'maintenance':
        begin_maintenance(settings)
    elif args.command == 'recover':
        recover(settings)
    elif args.command == 'restore':
        restore(settings, args.snapshot, args.confirm_target)
    else:
        from policies import apply_policies, garbage_collect, monitor
        {'policies': apply_policies, 'gc': garbage_collect, 'monitor': monitor}[args.command](settings)

def interrupted(signum, frame):
    raise InterruptedError('Operation interrupted')

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Exceptions from urllib/Restic must not leak request URLs or authentication values.
        print('Operation failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
