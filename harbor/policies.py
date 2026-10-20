"""Explicit Harbor API policy changes; never run implicitly on container restart."""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

from common import ConfigError, atomic_json, integer, operation_lock, read_json, text

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConfigError('Unexpected API redirect; check HARBOR_HOSTNAME and HTTPS routing')

class API:
    def __init__(self, settings):
        user = text(os.environ, 'HARBOR_API_USERNAME', 'admin')
        password = text(os.environ, 'HARBOR_API_PASSWORD', required=True)
        self.url = settings['external_url'] + '/api/v2.0'
        self.auth = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, endpoint, value=None, allow404=False):
        headers = {'Authorization': self.auth, 'Content-Type': 'application/json', 'Accept': 'application/json'}
        request = Request(self.url + endpoint, data=json.dumps(value).encode() if value is not None else None,
                          headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as error:
            if error.code == 404 and allow404:
                return None
            raise ConfigError(f'Harbor API {method} {endpoint.split("?")[0]} failed: HTTP {error.code}') from None

def retention_policy(project, count=20, days=30):
    def rule(template, params, selector, priority):
        return {'priority': priority, 'disabled': False, 'action': 'retain', 'template': template,
                'params': params, 'tag_selectors': [selector],
                'scope_selectors': {'repository': [{'kind': 'doublestar', 'decoration': 'repoMatches', 'pattern': '**'}]}}
    dev = {'kind': 'doublestar', 'decoration': 'matches', 'pattern': 'dev-*', 'extras': '{"untagged":false}'}
    other = {'kind': 'doublestar', 'decoration': 'excludes', 'pattern': 'dev-*', 'extras': '{"untagged":true}'}
    # Harbor combines retention rules with OR. Preserve everything outside dev-*.
    return {'algorithm': 'or', 'scope': {'level': 'project', 'ref': project},
            'trigger': {'kind': 'Schedule', 'settings': {'cron': '0 0 22 * * *'}},
            'rules': [rule('always', {}, other, 1),
                      rule('latestPushedK', {'latestPushedK': count}, dev, 2),
                      rule('nDaysSinceLastPull', {'nDaysSinceLastPull': days}, dev, 3)]}

def immutable_rule(pattern):
    return {'priority': 0, 'disabled': False, 'action': 'immutable', 'template': 'immutable_template',
            'tag_selectors': [{'kind': 'doublestar', 'decoration': 'matches', 'pattern': pattern}],
            'scope_selectors': {'repository': [{'kind': 'doublestar', 'decoration': 'repoMatches', 'pattern': '**'}]}}

def apply_policies(settings, api_factory=API):
    with operation_lock('/operations'):
        if Path('/control/maintenance.json').exists():
            raise ConfigError('Maintenance is active')
        api = api_factory(settings)
        config = api.request('GET', '/configurations')
        for key, expected in {'self_registration': False, 'project_creation_restriction': 'adminonly', 'auth_mode': 'db_auth'}.items():
            if config.get(key, {}).get('value') != expected:
                raise ConfigError('Core security configuration does not match the generated defaults')
        names = text(os.environ, 'POLICY_PROJECTS', 'library,production,development').split(',')
        import re
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,127}', name) for name in names):
            raise ConfigError('POLICY_PROJECTS must contain comma-separated valid project names')
        quota = integer(os.environ, 'PROJECT_QUOTA_GIB', 20, maximum=1048576) * 1024**3
        count = integer(os.environ, 'RETENTION_KEEP_COUNT', 20)
        days = integer(os.environ, 'RETENTION_KEEP_DAYS', 30)
        for name in names:
            project = api.request('GET', '/projects/' + quote(name, safe=''), allow404=True)
            metadata = {'public': 'false', 'auto_scan': 'true', 'prevent_vul': 'true', 'severity': 'critical',
                        'enable_content_trust': 'false', 'enable_content_trust_cosign': 'false'}
            if project is None:
                api.request('POST', '/projects', {'project_name': name, 'metadata': metadata, 'storage_limit': quota})
                project = api.request('GET', '/projects/' + quote(name, safe=''))
            project_id = project['project_id']
            api.request('PUT', f'/projects/{project_id}', {'metadata': metadata, 'storage_limit': quota})
            quotas = api.request('GET', f'/quotas?reference=project&reference_id={project_id}')
            for quota_item in quotas or []:
                api.request('PUT', f'/quotas/{quota_item["id"]}', {'hard': {'storage': quota}})
            policy = retention_policy(project_id, count, days)
            retention_id = project.get('metadata', {}).get('retention_id')
            if retention_id:
                api.request('PUT', '/retentions/' + str(retention_id), policy)
            else:
                api.request('POST', '/retentions', policy)
            existing = api.request('GET', f'/projects/{project_id}/immutabletagrules?page_size=100') or []
            for pattern in ('v*', 'release-*'):
                desired = immutable_rule(pattern)
                match = next((rule for rule in existing if rule.get('tag_selectors') == desired['tag_selectors'] and
                              rule.get('scope_selectors') == desired['scope_selectors']), None)
                if match:
                    api.request('PUT', f'/projects/{project_id}/immutabletagrules/{match["id"]}', desired)
                else:
                    api.request('POST', f'/projects/{project_id}/immutabletagrules', desired)
            print('Applied private project, scan, quota, dev retention and release immutability: ' + name)
        print('Policies applied explicitly; later UI edits remain until this command is run again')

def garbage_collect(settings):
    # Persistent marker also blocks backup after a killed polling process.
    import time
    with operation_lock('/operations'):
        if Path('/control/maintenance.json').exists():
            raise ConfigError('Maintenance is active; GC skipped')
        api = API(settings)
        marker = Path('/operations/gc-active.json')
        previous = api.request('GET', '/system/gc?page_size=1&sort=-creation_time') or []
        if marker.exists():
            boundary = read_json(marker)['previous_id']
        else:
            if previous and previous[0].get('job_status', '').lower() not in ('success', 'succeed', 'error', 'failed', 'stopped', 'scheduled'):
                raise ConfigError('Another GC is already active')
            boundary = previous[0]['id'] if previous else 0
            atomic_json(marker, {'previous_id': boundary})
            # Keep marker on an ambiguous API error: re-running gc reconciles the new job.
            api.request('POST', '/system/gc/schedule', {'schedule': {'type': 'Manual'},
                        'parameters': {'delete_untagged': False, 'workers': 1}})
        deadline = time.monotonic() + 3600
        while True:
            current = api.request('GET', '/system/gc?page_size=1&sort=-creation_time') or []
            latest = current[0] if current and current[0]['id'] > boundary else None
            state = latest.get('job_status', '').lower() if latest else ''
            if state in ('success', 'succeed'):
                marker.unlink()
                print('Garbage collection completed')
                return
            if state in ('error', 'failed', 'stopped'):
                marker.unlink()
                raise ConfigError('Garbage collection failed')
            if time.monotonic() >= deadline:
                raise ConfigError('GC state requires review; backup remains blocked. Re-run gc to reconcile the same job.')
            time.sleep(5)


def monitor(settings):
    from operations import notify, check_status
    import urllib.request
    failures = []
    for name in ('database', 'registry'):
        usage = shutil.disk_usage('/backup/' + name)
        percent = round(100 * usage.used / usage.total, 1)
        if percent >= 75:
            notify(settings, {'state': 'disk-critical' if percent >= 85 else 'disk-warning',
                              'instance': settings['instance'], 'volume': name, 'percent': percent})
            if percent >= 85:
                failures.append('disk')
    marker = Path('/control/maintenance.json')
    if marker.exists():
        from datetime import datetime, timezone
        created = datetime.fromisoformat(read_json(marker)['created_at'])
        limit = integer(os.environ, 'MAINTENANCE_MAX_HOURS', 4)
        if (datetime.now(timezone.utc) - created).total_seconds() > limit * 3600:
            failures.append('maintenance-overdue')
        if failures:
            notify(settings, {'state': 'monitor-failed', 'instance': settings['instance'], 'checks': failures})
            raise ConfigError('Maintenance or disk requires operator attention')
        print('Planned maintenance active; service availability checks deferred')
        return
    host = settings['external_url'].removeprefix('https://')
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=host) as secure:
                expires = ssl.cert_time_to_seconds(secure.getpeercert()['notAfter'])
        import time
        if expires - time.time() < 14 * 86400:
            failures.append('certificate-expiry')
        with urllib.request.urlopen(settings['external_url'] + '/api/v2.0/health', timeout=20) as response:
            health = json.load(response)
            if health.get('status') != 'healthy':
                failures.append('harbor-health')
    except Exception:
        failures.append('https-or-health')
    metadata = Path('/trivy-cache/db/metadata.json')
    try:
        from datetime import datetime, timezone
        updated = datetime.fromisoformat(read_json(metadata)['UpdatedAt'].replace('Z', '+00:00'))
        limit = integer(os.environ, 'TRIVY_DB_MAX_AGE_HOURS', 48)
        if (datetime.now(timezone.utc) - updated).total_seconds() > limit * 3600:
            failures.append('trivy-db-stale')
    except Exception:
        failures.append('trivy-db-missing-or-invalid')
    try:
        check_status(settings)
    except ConfigError:
        failures.append('backup')
    if failures:
        notify(settings, {'state': 'monitor-failed', 'instance': settings['instance'], 'checks': failures})
        raise ConfigError('Monitoring checks failed: ' + ', '.join(failures))
    print('Harbor monitoring checks passed')
