#!/usr/bin/env python3
"""
preflight_check.py

Phase 2 health check for the Nautobot onboarding pipeline.
Read-only -- makes no changes. Rewritten to match the current
lab-validated script set (replaces the legacy version that checked
for old scripts/services no longer in use).

Run from inside the onboarding directory (lab or prod - path-agnostic).
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from client import NautobotClient

client = NautobotClient(env_file=os.path.join(SCRIPT_DIR, '.env'))
URL = client.url
TOKEN = client.token

VENDOR_COMMANDS_PATH = os.environ.get(
    'VENDOR_COMMANDS_PATH',
    os.path.join(os.path.dirname(SCRIPT_DIR), 'vendor_commands', 'vendor_commands.yaml')
)

MANIFESTS_DIR = os.path.join(SCRIPT_DIR, 'manifests')
PROFILES_DIR = os.path.join(SCRIPT_DIR, 'profiles')
TENANTS_DIR = '/etc/nautobot/tenants'

REQUIRED_SCRIPTS = [
    'bootstrap_nautobot.py',
    'create_tenant.py',
    'nautobot_prepare.py',
    'nautobot_onboard_v2.py',
    'sync_network_data.py',
    'upload_app.py',
    'vendor_test.py',
    'vendor_test_app.py',
    'vendor_matrix.py',
    'credential_checker.py',
    'onboard_cli.py',
    'engineer_template.csv',
]

REQUIRED_SERVICES = ['nautobot', 'nautobot-worker']
OPTIONAL_SERVICES = ['nautobot-upload', 'nautobot-vendor-test']

results = {'passed': 0, 'failed': 0, 'checks': []}


def check(label, ok, detail=''):
    results['checks'].append({'label': label, 'ok': ok, 'detail': detail})
    if ok:
        results['passed'] += 1
        print(f"  \u2705 {label:38}\u2192  {detail}")
    else:
        results['failed'] += 1
        print(f"  \u274c {label:38}\u2192  {detail}")


def section(title):
    print(f"\n\u2500\u2500 {title} " + "\u2500" * max(0, 50 - len(title)))


def systemctl_active(service):
    try:
        r = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() == 'active'
    except Exception:
        return False


def port_reachable(port):
    try:
        r = requests.get(f'http://127.0.0.1:{port}/', timeout=3)
        return r.status_code < 500
    except Exception:
        return False


def api_count(path, params=None):
    try:
        r = client.get(path, params=params or {})
        if r.ok:
            return r.json().get('count', 0)
        return None
    except Exception:
        return None


def main():
    print("=" * 60)
    print("  Nautobot Preflight Check -- Phase 2")
    print(f"  {datetime.now(timezone.utc).isoformat()}")
    print(f"  Target: {URL}")
    print(f"  Script dir: {SCRIPT_DIR}")
    print("=" * 60)

    section("Nautobot API")
    count = api_count('extras/roles')
    check('API reachable', count is not None, f'HTTP {"200" if count is not None else "unreachable"}')
    check('API token valid', bool(TOKEN) and count is not None, 'token accepted' if count is not None else 'check NAUTOBOT_TOKEN in .env')

    section("Base Objects (Phase 1 bootstrap)")
    tg = api_count('tenancy/tenant-groups')
    check('Tenant Groups', tg is not None and tg > 0, f'{tg} found' if tg else '0 found -- run bootstrap_nautobot.py')
    plat = api_count('dcim/platforms')
    check('Platforms', plat is not None and plat > 0, f'{plat} found' if plat else '0 found')
    manu = api_count('dcim/manufacturers')
    check('Manufacturers', manu is not None and manu > 0, f'{manu} found' if manu else '0 found')
    roles = api_count('extras/roles')
    check('Roles', roles is not None and roles > 0, f'{roles} found' if roles else '0 found')
    lt = api_count('dcim/location-types')
    check('Location Types', lt is not None and lt >= 6, f'{lt} found' if lt else '0 found')
    tags = api_count('extras/tags')
    check('Tags', tags is not None and tags > 0, f'{tags} found' if tags else '0 found')

    section("Required Scripts")
    for fname in REQUIRED_SCRIPTS:
        fpath = os.path.join(SCRIPT_DIR, fname)
        exists = os.path.isfile(fpath)
        size_kb = f'{os.path.getsize(fpath) // 1024}KB' if exists else 'missing'
        check(fname, exists, size_kb)

    section("Directories & Permissions")
    check('onboarding dir exists', os.path.isdir(SCRIPT_DIR), SCRIPT_DIR)
    check('onboarding dir writable', os.access(SCRIPT_DIR, os.W_OK), SCRIPT_DIR)
    check('manifests dir exists', os.path.isdir(MANIFESTS_DIR), MANIFESTS_DIR)
    check('profiles dir exists', os.path.isdir(PROFILES_DIR), PROFILES_DIR)

    section("Vendor Commands YAML")
    check('vendor_commands.yaml exists', os.path.isfile(VENDOR_COMMANDS_PATH), VENDOR_COMMANDS_PATH)

    section("Systemd Services")
    for svc in REQUIRED_SERVICES:
        check(svc, systemctl_active(svc), 'active' if systemctl_active(svc) else 'not active')
    for svc in OPTIONAL_SERVICES:
        active = systemctl_active(svc)
        check(f'{svc} (optional)', active, 'active' if active else 'not running')

    section("Web Apps")
    check('Upload app (8081)', port_reachable(8081), 'reachable' if port_reachable(8081) else 'not reachable')
    check('Vendor test app (8082)', port_reachable(8082), 'reachable' if port_reachable(8082) else 'not reachable')

    section("Tenant Credentials")
    tenants_exist = os.path.isdir(TENANTS_DIR)
    check('Tenant dir exists', tenants_exist, TENANTS_DIR)
    if tenants_exist:
        try:
            env_files = [f for f in os.listdir(TENANTS_DIR) if f.endswith('.env')]
            check('At least one tenant .env', len(env_files) > 0, f'{len(env_files)} found')
        except PermissionError:
            check('At least one tenant .env', False, f'permission denied reading {TENANTS_DIR} (expected if not running as root/noc-ubuntu group)')

    total = results['passed'] + results['failed']
    section("Summary")
    print(f"  Score  : {results['passed']}/{total}")
    print(f"  Passed : {results['passed']}")
    print(f"  Failed : {results['failed']}")

    pct = results['passed'] / total if total else 0
    if pct >= 0.95:
        print("  \U0001F7E2 Ready for onboarding")
    elif pct >= 0.85:
        print("  \U0001F7E1 Minor issues, review before proceeding")
    else:
        print("  \U0001F534 Critical issues -- fix failures first")

    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    manifest_path = os.path.join(MANIFESTS_DIR, 'preflight_manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump({
            'phase': 'preflight',
            'generated': datetime.now(timezone.utc).isoformat(),
            'passed': results['passed'],
            'failed': results['failed'],
            'total': total,
            'checks': results['checks'],
        }, f, indent=2)
    print(f"\n\U0001F4C4 Manifest written: {manifest_path}")

    sys.exit(0 if results['failed'] == 0 else 1)


if __name__ == '__main__':
    main()
