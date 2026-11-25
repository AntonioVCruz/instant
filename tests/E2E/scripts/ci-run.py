#!/usr/bin/env python3
"""Manual CI lifecycle. Raw logs and fixture credentials stay outside artifacts."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[2]


def paths():
    result = {}
    for key, variable in [('state', 'INSTANT_E2E_STATE_DIR'), ('fixture', 'INSTANT_E2E_FIXTURE_DIR'),
                          ('private', 'INSTANT_E2E_PRIVATE_DIR'), ('artifacts', 'INSTANT_E2E_ARTIFACT_DIR')]:
        path = Path(os.environ[variable]).resolve()
        if path.is_relative_to(REPO) or path == Path(path.anchor):
            raise ValueError('CI directories must be private paths outside the checkout')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        result[key] = path
    if len(set(result.values())) != 4 or any(a.is_relative_to(b) for a in result.values()
                                            for b in result.values() if a != b):
        raise ValueError('CI directories must be distinct and must not contain one another')
    if os.environ.get('INSTANT_E2E_PROJECT') != 'instant-e2e':
        raise ValueError('Expanded fixture requires the instant-e2e Compose project')
    return result


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def command(args, log, *, timeout=300):
    # Build output, CLI output and subprocess errors can contain credentials.
    with log.open('a', encoding='utf-8') as output:
        result = subprocess.run(args, cwd=REPO, stdout=output, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Private command failed; exit ' + str(result.returncode))


def fixture(action, p):
    command([sys.executable, str(SCRIPTS / 'fixture-expanded.py'), action,
             '--state-dir', str(p['fixture'])], p['private'] / ('fixture-' + action + '.log'))


def accounts(p):
    users = json.loads((p['fixture'] / 'accounts.json').read_text())
    if set(users) != set('ABC'):
        raise ValueError('Expected three disposable accounts')
    for user in users.values():
        if not re.fullmatch(r'phase3-[a-f0-9]{32}@instant-e2e\.example', user['email']):
            raise ValueError('Unexpected account scope')
        for field in ['user_id', 'app_id', 'admin_token', 'dashboard_token']:
            if str(uuid.UUID(user[field])) != user[field]:
                raise ValueError('Invalid fixture identifier')
    return users


def api(user, endpoint, body=None):
    request = urllib.request.Request('http://127.0.0.1:18888' + endpoint,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', 'app-id': user['app_id'],
                 'Authorization': 'Bearer ' + user['admin_token']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        return json.load(response)


def session_counts(users):
    counts = {}
    for key, user in users.items():
        sql = ("SELECT count(*) FROM instant_user_refresh_tokens t JOIN instant_users u ON u.id=t.user_id "
               "WHERE u.id='" + user['user_id'] + "'::uuid AND u.email='" + user['email'] + "';")
        result = subprocess.run(['docker', 'exec', 'instant-e2e-postgres-1', 'psql', '-U', 'instant',
                                 '-d', 'instant', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql],
                                capture_output=True, text=True, timeout=30, check=True)
        counts[key] = int(result.stdout.strip())
    return counts


def postcheck(users, p):
    findings = {}
    for key, user in users.items():
        names = ['e2e_notes', 'e2e_people', 'e2e_related', 'e2e_types', 'e2e_required']
        rows = api(user, '/admin/query', {'query': {name: {} for name in names}})
        schema = api(user, '/admin/schema')['schema']['blobs']
        findings[key] = {'leftover_rows': sum(len(rows[name]) for name in names),
                         'temporary_namespaces': sum(name.startswith('e2e_temp_') for name in schema)}
    seed = json.loads((p['fixture'] / 'rename-seed.json').read_text())
    findings['rename_deleted'] = seed['namespace'] not in api(users['A'], '/admin/schema')['schema']['blobs']
    return findings


def publish_junit(raw, target):
    """Rebuild JUnit from outcomes only; never publish step messages or system-out."""
    root = ET.parse(raw).getroot()
    cases = list(root.iter('testcase'))
    failures = sum(bool(list(case.iter('failure')) or list(case.iter('error'))) for case in cases)
    skipped = sum(bool(list(case.iter('skipped'))) for case in cases)
    suite = ET.Element('testsuite', name='Instant Explorer', tests=str(len(cases)),
                       failures=str(failures), errors='0', skipped=str(skipped))
    for index, case in enumerate(cases, 1):
        item = ET.SubElement(suite, 'testcase', name=f'Explorer result {index}', classname='instant.explorer')
        if list(case.iter('failure')) or list(case.iter('error')):
            ET.SubElement(item, 'failure', message='See testRigor run for the failing step')
        elif list(case.iter('skipped')):
            ET.SubElement(item, 'skipped')
    ET.ElementTree(suite).write(target, encoding='utf-8', xml_declaration=True)
    return len(cases) == 10 and failures == 0 and skipped == 0


def preflight(p):
    if sys.platform != 'linux':
        raise RuntimeError('CI lifecycle requires Linux')
    memory = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text())[1]) * 1024
    if memory < 12 * 1024**3 or shutil.disk_usage(REPO).free < 25 * 1024**3:
        raise RuntimeError('Runner requires at least 12 GiB RAM and 25 GiB free disk')
    for tool in ['docker', 'curl', 'git', 'flock', 'bash', 'npm']:
        if not shutil.which(tool):
            raise RuntimeError('Missing required runner tool: ' + tool)
    command(['docker', 'info'], p['private'] / 'docker-info.log')
    command(['docker', 'compose', 'version'], p['private'] / 'compose-version.log')
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    if source != os.environ['E2E_COMMIT']:
        raise RuntimeError('Checkout does not match requested commit')
    harness = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((REPO / 'tests/E2E').rglob('*'))
               if path.is_file() and '__pycache__' not in path.parts}
    workflow = REPO / '.github/workflows/e2e-tests.yml'
    harness[str(workflow.relative_to(REPO))] = hashlib.sha256(workflow.read_bytes()).hexdigest()
    write(p['artifacts'] / 'provenance.json', {'source_sha': source, 'harness_sha': source,
          'cli_version': '3.0.0', 'harness_hashes': harness, 'memory_bytes': memory})


def prepare(p):
    command(['bash', str(SCRIPTS / 'install-instant.sh'), 'install'],
            p['private'] / 'install.log', timeout=5900)
    fixture('prepare', p)
    fixture('check', p)
    # Only public image metadata is exported; never inspect container environments.
    images = {}
    sha = os.environ['E2E_COMMIT'][:12]
    for name, tag in [('server', sha), ('www', sha), ('storage', '9e49d5e7a648')]:
        result = subprocess.check_output(['docker', 'image', 'inspect', f'instant-e2e/{name}:{tag}',
                                          '--format', '{{.Id}} {{json .RepoDigests}}'], text=True)
        images[name] = result.strip()
    build_log = (p['private'] / 'install.log').read_text(errors='replace')
    base_refs = sorted(set(re.findall(r'\bFROM\s+([A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64})', build_log)))
    write(p['artifacts'] / 'images.json', {'built_images': images, 'resolved_build_bases': base_refs})


def run(p):
    users = accounts(p)
    before = session_counts(users)
    logs, processes = [], []
    cli_exit = None
    try:
        for key in 'ABC':
            log = (p['private'] / ('inbox-' + key + '.log')).open('w')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, str(SCRIPTS / 'code-inbox.py'),
                '--config', str(p['fixture'] / ('inbox-' + key + '.json'))], stdout=log, stderr=log))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for port, process in zip(range(13001, 13004), processes):
            for attempt in range(30):
                if process.poll() is not None:
                    raise RuntimeError('Login inbox exited')
                try:
                    # No code has been requested yet: a 503 proves the inbox is listening.
                    opener.open(f'http://127.0.0.1:{port}/code', timeout=2).close()
                    break
                except urllib.error.HTTPError as error:
                    if error.code == 503:
                        break
                    raise
                except OSError:
                    if attempt == 29:
                        raise RuntimeError('Login inbox readiness timed out') from None
                    time.sleep(1)
        command(['curl', '--noproxy', '*', '--connect-timeout', '5', '--max-time', '20',
                 '--fail', '--silent', '--show-error', 'http://localhost:13000/dash',
                 '--output', str(p['private'] / 'dashboard.html')], p['private'] / 'direct.log')
        html = (p['private'] / 'dashboard.html').read_text()
        if '__NEXT_DATA__' not in html and 'self.__next_f' not in html:
            raise RuntimeError('Direct origin did not serve dashboard HTML')
        observer_log = (p['private'] / 'observer.log').open('w')
        logs.append(observer_log)
        observer = subprocess.Popen([sys.executable, str(SCRIPTS / 'observe-types.py'), '--state-dir',
            str(p['fixture']), '--output', str(p['private'] / 'typed.json'), '--duration', '3600'],
            stdout=observer_log, stderr=observer_log)
        processes.append(observer)
        case_files = sorted((REPO / 'tests/E2E/test-cases').glob('*.yaml'))
        rule_files = sorted((REPO / 'tests/E2E/rules').glob('*.txt'))
        if len(case_files) != 10 or len(rule_files) != 4:
            raise RuntimeError('Expected ten cases and four rules; review CI scope before changing it')
        # No settings update or authoritative replacement. Files can still update saved objects.
        args = ['testrigor', 'test-suite', 'run', os.environ['TESTRIGOR_SUITE_ID'], '--localhost',
                '--verbose', '--url', 'http://localhost:13000/dash?t=explorer&ns=e2e_notes',
                '--test-cases-path', 'tests/E2E/test-cases/*.yaml', '--rules-path', 'tests/E2E/rules/*.txt',
                '--variables-path', str(p['fixture'] / 'variables.json'),
                '--labels', 'instant-explorer,' + ','.join(path.stem for path in rule_files),
                '--branch', os.environ['E2E_BRANCH'], '--commit', os.environ['E2E_COMMIT'],
                '--junit-report-save-path', str(p['private'] / 'junit.xml')]
        with (p['private'] / 'cli.log').open('w') as output:
            # Synchronous run; no automatic retries or second run after an infrastructure failure.
            cli_exit = subprocess.run(args, cwd=REPO, stdout=output, stderr=subprocess.STDOUT).returncode
        write(p['artifacts'] / 'cli-result.json', {'exit_code': cli_exit})
        junit_ok = publish_junit(p['private'] / 'junit.xml', p['artifacts'] / 'junit.xml')
        typed = json.loads((p['private'] / 'typed.json').read_text())
        typed_ok = any(all(row[k] for k in ['number_zero', 'boolean_false', 'explicit_null']) for row in typed)
        after = session_counts(users)
        delta = {key: after[key] - before[key] for key in 'ABC'}
        findings = postcheck(users, p)
        clean = all(findings[key] == {'leftover_rows': 0, 'temporary_namespaces': 0} for key in 'ABC')
        log = (p['private'] / 'cli.log').read_text(errors='replace')
        infrastructure_error = bool(re.search(r'\[502\]|HTTP\s+502|\b429\b.*(?:error|limit)|too many requests', log, re.I))
        oracles = {'junit_ok': junit_ok, 'typed_ok': typed_ok, 'session_delta': delta,
                   'logout_ok': delta == {'A': 3, 'B': 3, 'C': 3}, 'postcheck': findings,
                   'infrastructure_error': infrastructure_error}
        write(p['artifacts'] / 'oracles.json', oracles)
        links = re.findall(r'https://app\.testrigor\.com/test-suites/[A-Za-z0-9_-]+/runs/[A-Za-z0-9_-]+', log)
        write(p['artifacts'] / 'runs.json', {'urls': sorted(set(links))})
        if cli_exit:
            return cli_exit if 0 < cli_exit < 256 else 1
        if not (junit_ok and typed_ok and clean and findings['rename_deleted']
                and oracles['logout_ok'] and not infrastructure_error):
            raise RuntimeError('CLI passed but independent acceptance checks failed')
        # No fallback cleanup can turn dirty data into a passing result. Restore the intentional control only.
        fixture('clean', p)
        fixture('check', p)
        write(p['artifacts'] / 'accepted.json', {'passed': True})
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for log in logs:
            log.close()
        # Preserve a nonzero CLI status even when JUnit or an oracle is missing.
        if cli_exit:
            raise SystemExit(cli_exit if 0 < cli_exit < 256 else 1)


def finish(p):
    errors = []
    accepted = (p['artifacts'] / 'accepted.json').exists()
    if not accepted:
        # Fail closed: export only fixed classifications of container messages, never bodies,
        # URLs, SQL, one-time codes, IDs or unknown future credential formats.
        with (p['artifacts'] / 'failure-container-logs.txt').open('w') as output:
            for service in ['server', 'www', 'postgres', 'minio']:
                try:
                    result = subprocess.run(['docker', 'logs', '--tail', '200', f'instant-e2e-{service}-1'],
                                            capture_output=True, text=True, timeout=20)
                    (p['private'] / (service + '.log')).write_text(result.stdout + result.stderr)
                    categories = Counter()
                    for line in (result.stdout + result.stderr).splitlines():
                        matches = [name for name in ['error', 'exception', 'timeout', 'refused', 'killed', 'ready', 'started']
                                   if re.search(r'\b' + name + r'\b', line, re.I)]
                        categories.update(matches or ['message-withheld'])
                    output.write(service + ': ' + json.dumps(dict(categories), sort_keys=True) + '\n')
                except (OSError, subprocess.SubprocessError):
                    output.write(service + ': logs unavailable\n')
    if (p['fixture'] / 'accounts.json').exists():
        try:
            fixture('revoke-sessions', p)
            if session_counts(accounts(p)) != {'A': 1, 'B': 1, 'C': 1}:
                raise RuntimeError('Browser sessions remain')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            errors.append('session-cleanup-failed')
    if (p['state'] / '.env').exists():
        try:
            command(['docker', 'compose', '--env-file', str(p['state'] / '.env'), '-p', 'instant-e2e',
                     '-f', str(REPO / 'tests/E2E/compose.yaml'), 'down', '--volumes', '--remove-orphans'],
                    p['private'] / 'teardown.log')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            errors.append('compose-cleanup-failed')
    write(p['artifacts'] / 'cleanup.json', {'passed': not errors, 'errors': errors})
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('Explorer E2E: ' + ('accepted' if accepted else 'not accepted') +
                         '; cleanup ' + ('failed' if errors else 'completed') + '. See sanitized artifact.\n')
    if errors:
        raise RuntimeError('CI cleanup failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'prepare', 'run', 'finish'])
    args = parser.parse_args()
    os.umask(0o077)
    p = paths()
    result = globals()[args.action](p)
    return result or 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ET.ParseError):
        # Exception details can include private commands, response bodies or paths.
        print('Explorer CI stage failed; inspect sanitized evidence and testRigor run.', file=sys.stderr)
        raise SystemExit(1)
