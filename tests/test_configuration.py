import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harbor'))
sys.path.insert(0, '/app')
from common import ConfigError, atomic_json, boolean, operation_lock, settings
from initialize import build_config, launcher, parse_literal_env

BASE = {'HARBOR_INSTANCE_ID': 'harbor-test', 'HARBOR_HOSTNAME': 'registry.company.test',
        'HARBOR_ADMIN_PASSWORD': 'admin-with-20-characters', 'HARBOR_DB_PASSWORD': 'database-with-20-characters',
        'BACKUP_ENABLED': 'false'}
TEMPLATE = {'database': {}, 'jobservice': {}, 'log': {'local': {}}, 'trivy': {}}

class ConfigurationTests(unittest.TestCase):
    def test_disabled_backup_accepts_no_credentials(self):
        self.assertFalse(settings(BASE)['backup_enabled'])

    def test_enabled_backup_requires_each_credential(self):
        env = {**BASE, 'BACKUP_ENABLED': 'true'}
        for key, value in [('RESTIC_REPOSITORY', 's3:https://backup.company.test/bucket/harbor'),
                           ('RESTIC_PASSWORD', 'a-long-encryption-passphrase'), ('AWS_ACCESS_KEY_ID', 'key'),
                           ('AWS_SECRET_ACCESS_KEY', 'secret')]:
            with self.assertRaisesRegex(ConfigError, key):
                settings(env)
            env[key] = value
        self.assertTrue(settings(env)['backup_enabled'])

    def test_rejects_unsafe_repository_urls(self):
        env = {**BASE, 'BACKUP_ENABLED': 'true', 'RESTIC_PASSWORD': 'a-long-encryption-passphrase',
               'AWS_ACCESS_KEY_ID': 'key', 'AWS_SECRET_ACCESS_KEY': 'secret'}
        for repo in ['s3:http://host/bucket', 's3:https://user:pass@host/bucket', 's3:https://host/bucket?token=secret']:
            with self.subTest(repo=repo), self.assertRaises(ConfigError):
                settings({**env, 'RESTIC_REPOSITORY': repo})

    def test_invalid_hostname_and_instance(self):
        for host in ['localhost', '127.0.0.1', 'https://registry.company.test', 'registry.company.test/path',
                     'registry.company.test:443', 'harbor.example.com', 'a..test', 'HOST.company.test']:
            with self.subTest(host=host), self.assertRaises(ConfigError):
                settings({**BASE, 'HARBOR_HOSTNAME': host})
        for instance in ['../data', 'harbor_prod', 'Xprod', 'ab']:
            with self.subTest(instance=instance), self.assertRaises(ConfigError):
                settings({**BASE, 'HARBOR_INSTANCE_ID': instance})

    def test_booleans_are_strict(self):
        for value in ['yes', '1', '', 'false ']:
            with self.assertRaises(ConfigError):
                boolean({'FLAG': value}, 'FLAG')

    def test_no_newline_in_secret(self):
        with self.assertRaises(ConfigError):
            build_config({**BASE, 'HARBOR_DB_PASSWORD': 'long-password\nINJECT=value'}, TEMPLATE)

    def test_security_defaults_and_proxy_url(self):
        conf = build_config(BASE, TEMPLATE)
        self.assertNotIn('https', conf)
        self.assertEqual(conf['external_url'], 'https://registry.company.test')
        self.assertFalse(conf['network']['allow_private_network_access'])
        self.assertFalse(conf['trivy']['insecure'])
        self.assertFalse(conf['trivy']['skip_update'])
        self.assertFalse(conf['x-dokploy-security']['self_registration'])
        self.assertEqual(conf['x-dokploy-security']['storage_per_project'], 20 * 1024**3)
        self.assertEqual(TEMPLATE['database'], {})

    def test_special_characters_remain_literal(self):
        value = "space $TOKEN $(touch /tmp/injected) # \" ' \\ ="
        parsed = parse_literal_env('PASSWORD=' + value + '\nEMPTY=\nURL=a=b#c\n')
        self.assertEqual(parsed['PASSWORD'], value)
        script = launcher(parsed, ['/some executable', 'literal;$HOME'])
        line = next(line for line in script.splitlines() if line.startswith('export PASSWORD='))
        self.assertEqual(shlex.split(line), ['export', 'PASSWORD=' + value])
        self.assertIn('exec ', script)
        self.assertNotIn('eval ', script)
        self.assertEqual(parsed['URL'], 'a=b#c')

    def test_invalid_generated_keys_are_rejected(self):
        for value in ['BAD-KEY=x', 'export X=x', 'NO_EQUALS']:
            with self.assertRaises(ConfigError):
                parse_literal_env(value)

    def test_connection_pool_validation(self):
        with self.assertRaises(ConfigError):
            build_config({**BASE, 'DB_MAX_IDLE_CONNS': '101', 'DB_MAX_OPEN_CONNS': '10'}, TEMPLATE)

    def test_backup_flag_does_not_change_harbor_config(self):
        self.assertEqual(build_config(BASE, TEMPLATE), build_config({**BASE, 'BACKUP_ENABLED': 'true'}, TEMPLATE))

    def test_atomic_json_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'settings.json'
            atomic_json(target, {'value': 'private'})
            self.assertEqual(json.loads(target.read_text()), {'value': 'private'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_operation_lock_rejects_overlap_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            with operation_lock(temp):
                with self.assertRaises(ConfigError):
                    with operation_lock(temp):
                        pass
            with operation_lock(temp):
                pass

    def test_launcher_shell_does_not_execute_password(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'injected'
            value = '$(touch ' + str(marker) + ') # dollar $VALUE quote \"'
            script = launcher({'TEST_PASSWORD': value}, [sys.executable, '-c',
                'import os; print(os.environ["TEST_PASSWORD"])'])
            # Gate paths are replaced only in the test; production paths stay fixed.
            ready = Path(temp) / 'ready'
            ready.touch()
            script = script.replace('/run/harbor-control/ready', str(ready))
            result = subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.rstrip('\n'), value)
            self.assertFalse(marker.exists())

    def test_database_limit_reserves_both_connection_pools(self):
        with self.assertRaisesRegex(ConfigError, 'POSTGRES_MAX_CONNECTIONS'):
            build_config({**BASE, 'DB_MAX_OPEN_CONNS': '150', 'POSTGRES_MAX_CONNECTIONS': '256'}, TEMPLATE)
