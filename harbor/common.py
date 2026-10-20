"""Shared validation and persistent state. No Docker or network side effects on import."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

class ConfigError(ValueError):
    pass

def boolean(env, name, default=False):
    value = env.get(name, str(default)).lower()
    if value not in ('true', 'false'):
        raise ConfigError(f'{name} must be true or false')
    return value == 'true'

def text(env, name, default='', required=False):
    value = env.get(name, default)
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise ConfigError(f'{name} must be a single line')
    if required and not value:
        raise ConfigError(f'{name} is required')
    return value

def integer(env, name, default, minimum=1, maximum=100000):
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        raise ConfigError(f'{name} must be an integer') from None
    if not minimum <= value <= maximum:
        raise ConfigError(f'{name} must be between {minimum} and {maximum}')
    return value

def settings(env):
    instance = text(env, 'HARBOR_INSTANCE_ID', required=True)
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,47}', instance):
        raise ConfigError('HARBOR_INSTANCE_ID: use 3-48 lowercase letters, digits or hyphens')
    host = text(env, 'HARBOR_HOSTNAME', required=True)
    if (len(host) > 253 or '.' not in host or
            not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in host.split('.')) or
            re.fullmatch(r'[0-9.]+', host) or host in ('harbor.example.com', 'reg.mydomain.com')):
        raise ConfigError('HARBOR_HOSTNAME must be your lowercase FQDN, without scheme, port or path')
    enabled = boolean(env, 'BACKUP_ENABLED', True)
    backup = {k: text(env, k) for k in (
        'RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_REGION')}
    if enabled:
        for key in ('RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'):
            if not backup[key]:
                raise ConfigError(f'{key} is required when BACKUP_ENABLED=true')
        repo = backup['RESTIC_REPOSITORY']
        parsed = urlsplit(repo.removeprefix('s3:'))
        if (not repo.startswith('s3:https://') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path in ('', '/')):
            raise ConfigError('RESTIC_REPOSITORY must be s3:https://endpoint/bucket/path without credentials or query')
        if len(backup['RESTIC_PASSWORD']) < 20:
            raise ConfigError('RESTIC_PASSWORD must contain at least 20 characters')
    webhook = text(env, 'NOTIFICATION_WEBHOOK')
    if webhook and not webhook.startswith('https://'):
        raise ConfigError('NOTIFICATION_WEBHOOK must use HTTPS')
    return {
        'instance': instance, 'external_url': f'https://{host}', 'backup_enabled': enabled,
        'restic_env': backup,
        'keep_daily': integer(env, 'BACKUP_KEEP_DAILY', 7),
        'keep_weekly': integer(env, 'BACKUP_KEEP_WEEKLY', 4),
        'keep_monthly': integer(env, 'BACKUP_KEEP_MONTHLY', 6),
        'backup_max_age_hours': integer(env, 'BACKUP_MAX_AGE_HOURS', 26),
        'health_timeout': integer(env, 'HEALTH_TIMEOUT_SECONDS', 300, maximum=3600),
        'notification_webhook': webhook,
        'notification_token': text(env, 'NOTIFICATION_BEARER_TOKEN'),
    }

def read_json(path):
    return json.loads(Path(path).read_text())

def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

@contextlib.contextmanager
def operation_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'operation.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError('Another initialization, backup, policy or maintenance operation is running') from None
        try:
            yield None
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
