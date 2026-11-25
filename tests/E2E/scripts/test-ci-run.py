#!/usr/bin/env python3
"""Offline regression checks for CI gating, privacy, oracles and exit handling."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('ci_run', Path(__file__).with_name('ci-run.py'))
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.p = {key: self.root / key for key in ['state', 'fixture', 'private', 'artifacts']}
        for path in self.p.values():
            path.mkdir()

    def test_workflow_gate_skips_unconfigured_or_untrusted_execution(self):
        workflow = (ci.REPO / '.github/workflows/e2e-tests.yml').read_text()
        code = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        base = {'CI_TOKEN': 'synthetic-token', 'SUITE_ID': 'test-suite', 'EVENT_NAME': 'workflow_dispatch',
                'REF_NAME': 'refs/heads/main', 'DEFAULT_BRANCH': 'main',
                'REPOSITORY': 'owner/repo', 'REPOSITORY_OWNER': 'owner', 'PR_HEAD_REPOSITORY': '',
                'PR_AUTHOR': '', 'PR_BASE': '', 'PR_DRAFT': ''}
        owner_pr = {'EVENT_NAME': 'pull_request', 'REF_NAME': 'refs/pull/1/merge',
                    'PR_HEAD_REPOSITORY': 'owner/repo', 'PR_AUTHOR': 'owner', 'PR_BASE': 'main', 'PR_DRAFT': 'false'}
        scenarios = [({}, True), ({'CI_TOKEN': ''}, False), ({'SUITE_ID': ''}, False),
                     ({'SUITE_ID': 'invalid suite!'}, False), ({'REF_NAME': 'refs/heads/feature'}, False),
                     ({'EVENT_NAME': 'pull_request', 'REF_NAME': 'refs/pull/1/merge'}, False),
                     ({'EVENT_NAME': 'pull_request_target'}, False),
                     ({'DEFAULT_BRANCH': 'trunk', 'REF_NAME': 'refs/heads/trunk'}, True),
                     (owner_pr, True), ({**owner_pr, 'PR_HEAD_REPOSITORY': 'outsider/repo'}, False),
                     ({**owner_pr, 'PR_AUTHOR': 'outsider'}, False), ({**owner_pr, 'PR_DRAFT': 'true'}, False),
                     ({**owner_pr, 'PR_BASE': 'feature'}, False), ({**owner_pr, 'CI_TOKEN': ''}, False)]
        for index, (changes, expected) in enumerate(scenarios):
            with self.subTest(changes=changes):
                output, summary = self.root / f'output-{index}', self.root / f'summary-{index}'
                env = {**os.environ, **base, **changes, 'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)}
                subprocess.run([ci.sys.executable, '-c', code], env=env, check=True)
                self.assertEqual(output.read_text().strip(), 'enabled=' + str(expected).lower())
                self.assertNotIn('synthetic-token', output.read_text() + summary.read_text())

    def test_junit_exports_outcomes_without_private_messages(self):
        raw = self.root / 'raw.xml'
        raw.write_text('<testsuite><testcase name="secret-account"><failure message="secret-token">'
                       '123456</failure><system-out>private body</system-out></testcase></testsuite>')
        target = self.root / 'public.xml'
        self.assertFalse(ci.publish_junit(raw, target))
        public = target.read_text()
        for private in ['secret-account', 'secret-token', '123456', 'private body']:
            self.assertNotIn(private, public)
        self.assertEqual(ET.parse(target).getroot().get('failures'), '1')

    def test_junit_requires_ten_executed_passing_results(self):
        raw, target = self.root / 'raw.xml', self.root / 'public.xml'
        for count, child, expected in [(10, '', True), (9, '', False), (11, '', False),
                                        (10, '<skipped/>', False), (10, '<error/>', False)]:
            with self.subTest(count=count, child=child):
                raw.write_text('<testsuites><testsuite>' + ('<testcase>' + child + '</testcase>') * count
                               + '</testsuite></testsuites>')
                self.assertEqual(ci.publish_junit(raw, target), expected)

    def test_nested_or_checkout_artifact_paths_rejected(self):
        env = dict(zip(['INSTANT_E2E_STATE_DIR', 'INSTANT_E2E_FIXTURE_DIR', 'INSTANT_E2E_PRIVATE_DIR',
                         'INSTANT_E2E_ARTIFACT_DIR'], map(str, self.p.values())))
        env['INSTANT_E2E_PROJECT'] = 'instant-e2e'
        with patch.dict(os.environ, env):
            self.assertEqual(ci.paths(), self.p)
            with patch.dict(os.environ, {'INSTANT_E2E_ARTIFACT_DIR': str(self.p['private'] / 'public')}):
                with self.assertRaises(ValueError):
                    ci.paths()
            with patch.dict(os.environ, {'INSTANT_E2E_ARTIFACT_DIR': str(ci.REPO / 'tmp')}):
                with self.assertRaises(ValueError):
                    ci.paths()

    def mocked_run(self, cli_exit=0, *, dirty=False, typed=True, logout=True, junit=True):
        ci.write(self.p['private'] / 'typed.json', [{'number_zero': typed, 'boolean_false': True, 'explicit_null': True}])
        if junit:
            (self.p['private'] / 'junit.xml').write_text('<testsuite>' + '<testcase/>' * 10 + '</testsuite>')
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        findings = {key: {'leftover_rows': int(dirty), 'temporary_namespaces': 0} for key in 'ABC'}
        findings['rename_deleted'] = True
        env = {'TESTRIGOR_SUITE_ID': 'test-suite', 'E2E_BRANCH': 'main', 'E2E_COMMIT': 'a' * 40}

        def fake_command(args, log, **kwargs):
            (self.p['private'] / 'dashboard.html').write_text('__NEXT_DATA__')

        self.fixture_mock = Mock()
        with patch.dict(os.environ, env), patch.object(ci, 'accounts', return_value={}), \
             patch.object(ci, 'session_counts', side_effect=[dict.fromkeys('ABC', 1),
                 {'A': 4 if logout else 5, 'B': 4, 'C': 4}]), \
             patch.object(ci.subprocess, 'Popen', return_value=process), \
             patch.object(ci.subprocess, 'run', return_value=Mock(returncode=cli_exit)), \
             patch.object(ci.urllib.request, 'build_opener', return_value=Mock()), \
             patch.object(ci, 'command', side_effect=fake_command), \
             patch.object(ci, 'fixture', self.fixture_mock), patch.object(ci, 'postcheck', return_value=findings):
            return ci.run(self.p)

    def test_green_cli_requires_independent_oracles(self):
        for options in [{'dirty': True}, {'typed': False}, {'logout': False}]:
            with self.subTest(options=options):
                with self.assertRaises(RuntimeError):
                    self.mocked_run(**options)
                self.assertFalse((self.p['artifacts'] / 'accepted.json').exists())
                self.fixture_mock.assert_not_called()

    def test_accepted_run_restores_control_after_clean_oracles(self):
        self.assertEqual(self.mocked_run(), 0)
        self.assertTrue((self.p['artifacts'] / 'accepted.json').exists())
        self.assertEqual([call.args[0] for call in self.fixture_mock.call_args_list], ['clean', 'check'])

    def test_original_cli_failure_survives_missing_junit(self):
        with self.assertRaises(SystemExit) as error:
            self.mocked_run(cli_exit=17, junit=False)
        self.assertEqual(error.exception.code, 17)
        self.assertEqual(json.loads((self.p['artifacts'] / 'cli-result.json').read_text())['exit_code'], 17)

    def test_finish_attempts_compose_cleanup_when_session_cleanup_fails(self):
        (self.p['fixture'] / 'accounts.json').write_text('{}')
        (self.p['state'] / '.env').write_text('PRIVATE=secret')
        logs = Mock(stdout='ERROR password=secret code=123456\nReady private-url', stderr='')
        with patch.object(ci.subprocess, 'run', return_value=logs), \
             patch.object(ci, 'fixture', side_effect=RuntimeError('private detail')), \
             patch.object(ci, 'command') as teardown:
            with self.assertRaises(RuntimeError):
                ci.finish(self.p)
        teardown.assert_called_once()
        self.assertIn('--volumes', teardown.call_args.args[0])
        public = (self.p['artifacts'] / 'failure-container-logs.txt').read_text()
        for private in ['secret', '123456', 'private-url']:
            self.assertNotIn(private, public)
        self.assertIn('error', public)
        self.assertFalse(json.loads((self.p['artifacts'] / 'cleanup.json').read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
