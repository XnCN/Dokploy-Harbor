import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harbor'))
sys.path.insert(0, '/app')
from common import ConfigError, atomic_json
from docker_api import Docker
import operations as ops
from policies import retention_policy, immutable_rule

class FakeDocker:
    def __init__(self, failure=None):
        self.failure = failure
        self.events = []
        self.items = {name: {'Id': name + '-id', 'State': 'running'} for name in ops.STOP_ORDER}
    def containers(self):
        return {name: dict(item) for name, item in self.items.items()}
    def inspect(self, cid):
        name = cid.removesuffix('-id')
        return {'State': {'Running': self.items[name]['State'] == 'running', 'Health': {'Status': 'healthy'},
                          'ExitCode': 0, 'OOMKilled': False}}
    def stop(self, cid):
        name = cid.removesuffix('-id')
        self.events.append(('stop', name))
        if self.failure == name:
            raise ConfigError('Simulated stop failure')
        self.items[name]['State'] = 'exited'
    def start(self, cid):
        name = cid.removesuffix('-id')
        self.events.append(('start', name))
        self.items[name]['State'] = 'running'
    def dump_database(self, cid, output, password):
        self.events.append(('dump', cid))
        assert password == 'literal$pass#with=equals'
        if self.failure == 'dump':
            raise ConfigError('Simulated dump failure')
        output.write(b'-- complete logical dump')

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.operations = self.base / 'operations'
        self.control = self.base / 'control'
        self.backup = self.base / 'backup'
        for directory in (self.operations, self.control, self.backup / 'config/db'):
            directory.mkdir(parents=True, exist_ok=True)
        (self.backup / 'config/db/env').write_text('POSTGRES_PASSWORD=literal$pass#with=equals\n')
        atomic_json(self.operations / 'images.lock.json', {'harbor_version': 'v2.15.4'})
        self.patchers = [patch.object(ops, 'OPERATIONS', self.operations), patch.object(ops, 'CONTROL', self.control),
                         patch.object(ops, 'BACKUP', self.backup)]
        for item in self.patchers:
            item.start()
        self.settings = {'instance': 'harbor-test', 'backup_enabled': True, 'harbor_version': 'v2.15.4',
                         'health_timeout': 1, 'keep_daily': 7, 'keep_weekly': 4, 'keep_monthly': 6,
                         'notification_webhook': '', 'backup_max_age_hours': 26}
    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()
    def restic(self, settings, args):
        if args[0] == 'backup':
            return b'{"message_type":"summary","snapshot_id":"abcdef1234"}\n'
        return b'{}'

    def test_disabled_backup_has_no_side_effects(self):
        with patch.object(ops, 'operation_lock', side_effect=AssertionError('Lock must not be used')), \
             patch.object(ops, 'restic', side_effect=AssertionError('S3 must not be used')):
            value = ops.backup({**self.settings, 'backup_enabled': False},
                               docker_factory=lambda instance: self.fail('Docker must not be used'))
        self.assertEqual(value, 'skipped')
        self.assertFalse((self.control / 'maintenance.json').exists())
        self.assertFalse((self.operations / 'last-backup.json').exists())

    def test_success_is_cold_and_restarts_dependencies_first(self):
        docker = FakeDocker()
        with patch.object(ops, 'restic', side_effect=self.restic) as restic:
            self.assertEqual(ops.backup(self.settings, lambda instance: docker), 'abcdef1234')
        self.assertLess(docker.events.index(('stop', 'core')), docker.events.index(('dump', 'postgresql-id')))
        self.assertLess(docker.events.index(('dump', 'postgresql-id')), docker.events.index(('stop', 'postgresql')))
        self.assertEqual([name for action, name in docker.events if action == 'start'], list(reversed(ops.STOP_ORDER)))
        self.assertTrue(all(item['State'] == 'running' for item in docker.items.values()))
        self.assertEqual(json.loads((self.operations / 'last-backup.json').read_text())['state'], 'success')
        self.assertFalse((self.backup / 'dump/postgres.sql').exists())
        self.assertFalse((self.control / 'maintenance.json').exists())
        self.assertEqual(restic.call_args_list[-1].args[1][0], 'forget')

    def test_dump_and_upload_failures_restore_services(self):
        for stage in ('dump', 'upload', 'core'):
            with self.subTest(stage=stage):
                docker = FakeDocker(stage if stage != 'upload' else None)
                def restic(settings, args):
                    if stage == 'upload' and args[0] == 'backup':
                        raise ConfigError('Simulated backend failure')
                    return self.restic(settings, args)
                with patch.object(ops, 'restic', side_effect=restic), self.assertRaises(ConfigError):
                    ops.backup(self.settings, lambda instance: docker)
                self.assertTrue(all(item['State'] == 'running' for item in docker.items.values()))
                self.assertEqual(json.loads((self.operations / 'last-backup.json').read_text())['state'], 'failed')
                self.assertFalse((self.control / 'maintenance.json').exists())

    def test_missing_repository_does_not_stop_services(self):
        docker = FakeDocker()
        with patch.object(ops, 'restic', side_effect=ConfigError('Repository inaccessible')), self.assertRaises(ConfigError):
            ops.backup(self.settings, lambda instance: docker)
        self.assertEqual(docker.events, [])

    def test_existing_maintenance_prevents_backup(self):
        atomic_json(self.control / 'maintenance.json', {'kind': 'restore'})
        with patch.object(ops, 'restic') as restic, self.assertRaises(ConfigError):
            ops.backup(self.settings)
        restic.assert_not_called()

    def test_restart_failure_keeps_recovery_record(self):
        docker = FakeDocker()
        with patch.object(ops, 'restic', side_effect=self.restic), \
             patch.object(ops, 'restart', side_effect=ConfigError('Restart failed')), self.assertRaises(ConfigError):
            ops.backup(self.settings, lambda instance: docker)
        record = json.loads((self.control / 'maintenance.json').read_text())
        self.assertEqual(record['kind'], 'backup-recovery')
        self.assertEqual(record['containers']['postgresql'], 'postgresql-id')

    def test_disabled_backup_status_needs_no_previous_snapshot(self):
        ops.check_status({**self.settings, 'backup_enabled': False})

    def test_restore_requires_explicit_target_confirmation(self):
        with patch.object(ops, 'Docker', side_effect=AssertionError('No Docker before confirmation')), self.assertRaises(ConfigError):
            ops.restore(self.settings, 'abcdef1234', 'wrong-target')

    def test_restore_refuses_existing_workload(self):
        with patch.object(ops, 'Docker', return_value=FakeDocker()), self.assertRaises(ConfigError):
            ops.restore(self.settings, 'abcdef1234', 'harbor-test')

    def test_restore_refuses_nonempty_volumes(self):
        for name in ops.DATA_NAMES:
            (self.backup / name).mkdir(parents=True, exist_ok=True)
        docker = FakeDocker()
        docker.items.clear()
        # config/db/env already exists: recovery must not overwrite it.
        with patch.object(ops, 'Docker', return_value=docker), patch.object(ops, 'restic') as restic, self.assertRaises(ConfigError):
            ops.restore(self.settings, 'abcdef1234', 'harbor-test')
        restic.assert_not_called()

    def test_interrupted_backup_recovery_keeps_record_during_restart(self):
        docker = FakeDocker()
        for item in docker.items.values():
            item['State'] = 'exited'
        record = {'kind': 'backup', 'containers': {name: item['Id'] for name, item in docker.items.items()}}
        atomic_json(self.control / 'maintenance.json', record)
        def restart(client, containers, timeout):
            self.assertTrue((self.control / 'maintenance.json').exists())
            self.assertTrue((self.control / 'resuming').exists())
            self.assertEqual(set(containers), set(docker.items))
        with patch.object(ops, 'Docker', return_value=docker), patch.object(ops, 'restart', side_effect=restart):
            ops.recover(self.settings)
        self.assertFalse((self.control / 'maintenance.json').exists())
        self.assertFalse((self.control / 'resuming').exists())

    def test_persistent_gc_record_blocks_backup_before_s3_or_docker(self):
        atomic_json(self.operations / 'gc-active.json', {'previous_id': 1})
        with patch.object(ops, 'restic') as restic, self.assertRaises(ConfigError):
            ops.backup(self.settings, lambda instance: self.fail('Docker must not be used'))
        restic.assert_not_called()


class StreamTests(unittest.TestCase):
    def test_dump_demultiplexes_stdout_and_rejects_truncation(self):
        def frame(kind, body):
            return bytes([kind, 0, 0, 0]) + struct.pack('>I', len(body)) + body
        docker = object.__new__(Docker)
        class Connection:
            def close(self): pass
        docker.request = lambda method, endpoint, body=None: {'Id': 'exec-id', 'ExitCode': 0, 'Running': False}
        docker.open = lambda *a, **k: (Connection(), io.BytesIO(frame(1, b'SQL') + frame(2, b'warning')))
        output = io.BytesIO()
        docker.dump_database('container', output, 'private')
        self.assertEqual(output.getvalue(), b'SQL')
        docker.open = lambda *a, **k: (Connection(), io.BytesIO(frame(1, b'SQL')[:-1]))
        with self.assertRaises(ConfigError):
            docker.dump_database('container', io.BytesIO(), 'private')

class PolicyTests(unittest.TestCase):
    def test_retention_preserves_non_development_artifacts(self):
        policy = retention_policy(7)
        self.assertEqual(policy['algorithm'], 'or')
        self.assertEqual(policy['scope']['ref'], 7)
        self.assertEqual(policy['rules'][0]['template'], 'always')
        self.assertEqual(policy['rules'][0]['tag_selectors'][0]['decoration'], 'excludes')
        self.assertEqual(policy['rules'][0]['tag_selectors'][0]['pattern'], 'dev-*')
        self.assertEqual(policy['rules'][1]['params'], {'latestPushedK': 20})
        self.assertEqual(policy['rules'][2]['params'], {'nDaysSinceLastPull': 30})

    def test_each_immutable_rule_has_one_selector(self):
        self.assertEqual(len(immutable_rule('v*')['tag_selectors']), 1)

class PolicyApplicationTests(unittest.TestCase):
    def test_repeated_application_does_not_duplicate_rules_or_retention(self):
        import contextlib
        import os
        import policies
        class FakeAPI:
            def __init__(self):
                self.project = {'project_id': 7, 'metadata': {}}
                self.rules = []
                self.created_retentions = 0
            def request(self, method, endpoint, value=None, allow404=False):
                if endpoint == '/configurations':
                    return {key: {'value': val} for key, val in {'self_registration': False,
                            'project_creation_restriction': 'adminonly', 'auth_mode': 'db_auth'}.items()}
                if method == 'GET' and endpoint == '/projects/production':
                    return self.project
                if endpoint.startswith('/quotas'):
                    return [{'id': 11}] if method == 'GET' else None
                if method == 'POST' and endpoint == '/retentions':
                    self.created_retentions += 1
                    self.project['metadata']['retention_id'] = '15'
                if 'immutabletagrules' in endpoint:
                    if method == 'GET':
                        return self.rules
                    if method == 'POST':
                        self.rules.append({**value, 'id': len(self.rules) + 1})
                return None
        api = FakeAPI()
        with patch.dict(os.environ, {'POLICY_PROJECTS': 'production'}, clear=True), \
             patch.object(policies, 'operation_lock', return_value=contextlib.nullcontext()), \
             patch.object(policies.Path, 'exists', return_value=False):
            policies.apply_policies({'external_url': 'https://registry.company.test'}, lambda settings: api)
            policies.apply_policies({'external_url': 'https://registry.company.test'}, lambda settings: api)
        self.assertEqual(api.created_retentions, 1)
        self.assertEqual(len(api.rules), 2)

class DockerSelectionTests(unittest.TestCase):
    def test_duplicate_instance_projects_are_rejected(self):
        docker = object.__new__(Docker)
        docker.instance = 'harbor-test'
        docker.request = lambda method, endpoint: [
            {'Id': 'a', 'Labels': {'com.docker.compose.project': 'one', 'com.docker.compose.service': 'core'}},
            {'Id': 'b', 'Labels': {'com.docker.compose.project': 'two', 'com.docker.compose.service': 'registry'}},
        ]
        with self.assertRaises(ConfigError):
            docker.containers()
