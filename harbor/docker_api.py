"""Small Docker Engine client, used only by short-lived operations jobs."""
import http.client
import json
import socket
import struct
from urllib.parse import quote, urlencode
from common import ConfigError

class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')

class Docker:
    def __init__(self, instance):
        self.instance = instance
        self.prefix = ''
        version = self.request('GET', '/version')
        if tuple(map(int, version['ApiVersion'].split('.'))) < (1, 45):
            raise ConfigError('Docker Engine API >= 1.45 is required (volume subpaths)')
        self.prefix = '/v1.45'

    def open(self, method, endpoint, body=None, timeout=600):
        conn = UnixConnection('localhost', timeout=timeout)
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, self.prefix + endpoint, body=payload,
                     headers={'Content-Type': 'application/json'} if payload is not None else {})
        response = conn.getresponse()
        if response.status not in (200, 201, 204, 304):
            response.read()
            conn.close()
            raise ConfigError(f'Docker operation failed: {method} {endpoint.split("?")[0]} HTTP {response.status}')
        return conn, response

    def request(self, method, endpoint, body=None):
        conn, response = self.open(method, endpoint, body)
        try:
            raw = response.read()
            return json.loads(raw) if raw else None
        finally:
            conn.close()

    def containers(self):
        labels = [f'io.harbor-dokploy.instance={self.instance}', 'io.harbor-dokploy.role=workload']
        query = urlencode({'all': 'true', 'filters': json.dumps({'label': labels})})
        containers = self.request('GET', '/containers/json?' + query)
        result = {}
        projects = set()
        for container in containers:
            label = container['Labels']
            projects.add(label.get('com.docker.compose.project'))
            service = label.get('com.docker.compose.service')
            if service in result:
                raise ConfigError('Multiple containers match one Harbor service; resolve duplicate deployments')
            result[service] = container
        if len(projects) > 1:
            raise ConfigError('HARBOR_INSTANCE_ID is used by multiple Compose projects')
        return result

    def inspect(self, container):
        return self.request('GET', '/containers/' + quote(container, safe='') + '/json')

    def stop(self, container):
        self.request('POST', '/containers/' + quote(container, safe='') + '/stop?t=120')
        if self.inspect(container)['State']['Running']:
            raise ConfigError('Container did not stop')

    def start(self, container):
        self.request('POST', '/containers/' + quote(container, safe='') + '/start')

    def dump_database(self, container, output, password):
        command = ['pg_dumpall', '-h', '127.0.0.1', '-U', 'postgres', '--clean', '--if-exists']
        execution = self.request('POST', '/containers/' + container + '/exec', {
            'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
            'Cmd': command, 'Env': ['PGPASSWORD=' + password], 'User': 'postgres',
        })['Id']
        conn, response = self.open('POST', '/exec/' + execution + '/start', {'Detach': False, 'Tty': False}, timeout=3600)
        try:
            while True:
                header = response.read(8)
                if not header:
                    break
                if len(header) != 8:
                    raise ConfigError('Truncated PostgreSQL dump stream')
                stream, size = header[0], struct.unpack('>I', header[4:])[0]
                remaining = size
                while remaining:
                    chunk = response.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ConfigError('Truncated PostgreSQL dump frame')
                    if stream == 1:
                        output.write(chunk)
                    remaining -= len(chunk)
        finally:
            conn.close()
        result = self.request('GET', '/exec/' + execution + '/json')
        if result.get('Running') or result.get('ExitCode') != 0:
            raise ConfigError('PostgreSQL logical dump failed')
