"""
vendor_test.py
Pre-onboarding connectivity and command test tool.
Tests SSH/API connection for a device before adding to Nautobot.
Validates credentials, runs commands, previews parsed data.

Usage:
    python3 vendor_test.py --ip 172.33.1.1 --vendor fortios --user admin --password Admin@123
    python3 vendor_test.py --ip 172.33.1.5 --vendor juniper_junos --user root --password Admin@123
    python3 vendor_test.py --vendor juniper_mist_ap --token <token> --org-id <org_id> --base-url https://api.eu.mist.com
    python3 vendor_test.py --vendor aruba_ap_central_api --client-id xxx --client-secret xxx --refresh-token xxx --base-url https://api-ap.central.arubanetworks.com
    python3 vendor_test.py --list-vendors
"""

import sys
import os
import argparse
import json
import yaml
import requests
from datetime import datetime

LAB_DIR       = os.path.dirname(os.path.abspath(__file__))
YAML_PATH     = os.environ.get(
    'VENDOR_COMMANDS_PATH',
    os.path.join(os.path.dirname(LAB_DIR), 'vendor_commands', 'vendor_commands.yaml')
)

sys.path.insert(0, LAB_DIR)

# ── Terminal helpers ──────────────────────────────────────────────────────────
def ok(msg):   print(f"  ✅  {msg}")
def fail(msg): print(f"  ❌  {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def info(msg): print(f"  ℹ️   {msg}")
def hdr(msg):  print(f"\n  {'─'*60}\n  {msg}\n  {'─'*60}")


# ── YAML loader ───────────────────────────────────────────────────────────────
def load_yaml():
    with open(YAML_PATH) as f:
        return yaml.safe_load(f)

def find_block(vendor_key):
    """Find vendor block across all sections."""
    data = load_yaml()
    for section, vendors in data.items():
        if vendor_key in vendors:
            block = vendors[vendor_key]
            block['_section']  = section
            block['_yaml_key'] = vendor_key
            return block
    return None

def list_vendors():
    data = load_yaml()
    print(f"\n  {'─'*65}")
    print(f"  Available vendors in vendor_commands.yaml")
    print(f"  {'─'*65}")
    for section, vendors in data.items():
        print(f"\n  [{section.upper()}]")
        for key, block in vendors.items():
            src    = block.get('data_source','?')
            driver = block.get('netmiko_device_type') or block.get('api_type','?')
            cmds   = len(block.get('commands', block.get('api_endpoints',{})))
            print(f"    {key:35} {src:4}  driver:{driver:20} commands:{cmds}")
    print()


# ── SSH test ──────────────────────────────────────────────────────────────────
def test_ssh(ip, vendor_key, block, user, password, enable=''):
    hdr(f"SSH CONNECTION TEST — {vendor_key} @ {ip}")

    try:
        from netmiko import ConnectHandler
        from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
    except ImportError:
        fail("netmiko not installed — run: pip install netmiko")
        return False

    device_type = block.get('netmiko_device_type','')
    if not device_type:
        fail(f"No netmiko_device_type in YAML block for {vendor_key}")
        return False

    info(f"Device type : {device_type}")
    info(f"Host        : {ip}:22")
    info(f"Username    : {user}")

    # Step 1: TCP connectivity
    import socket
    try:
        sock = socket.create_connection((ip, 22), timeout=5)
        sock.close()
        ok("TCP port 22 reachable")
    except Exception as e:
        fail(f"TCP port 22 unreachable: {e}")
        fail("Fix: check IP, routing, firewall rules")
        return False

    # Step 2: SSH auth + connection
    conn_params = {
        'device_type': device_type,
        'host':        ip,
        'username':    user,
        'password':    password,
        'timeout':     20,
        'auth_timeout': 20,
    }
    if enable:
        conn_params['secret'] = enable

    try:
        conn = ConnectHandler(**conn_params)
        ok(f"SSH authenticated successfully")
    except NetmikoAuthenticationException:
        fail("Authentication failed — wrong username or password")
        return False
    except NetmikoTimeoutException:
        fail("SSH timeout — device reachable but not responding")
        return False
    except Exception as e:
        fail(f"SSH connection error: {e}")
        return False

    # Step 3: disable paging
    disable_paging = block.get('disable_paging','')
    if disable_paging:
        try:
            conn.send_command(disable_paging)
            ok(f"Paging disabled: {disable_paging}")
        except:
            warn("Could not disable paging — output may be truncated")

    # Step 4: run each command and preview output
    commands = block.get('commands', {})
    hdr(f"COMMAND OUTPUT PREVIEW ({len(commands)} commands)")

    results  = {}
    all_ok   = True
    for cmd_name, cmd_str in commands.items():
        try:
            output = conn.send_command(
                cmd_str, read_timeout=30, expect_string=r'#|\$|>'
            )
            lines  = [l for l in output.splitlines() if l.strip()]
            status = 'OK' if lines else 'EMPTY'
            if status == 'OK':
                ok(f"{cmd_name:20} ({len(lines)} lines) ← {cmd_str}")
                # Preview first 3 lines
                for line in lines[:3]:
                    print(f"              {line[:80]}")
                if len(lines) > 3:
                    print(f"              ... ({len(lines)-3} more lines)")
            else:
                warn(f"{cmd_name:20} returned empty output ← {cmd_str}")
            results[cmd_name] = output
        except Exception as e:
            fail(f"{cmd_name:20} FAILED: {e}")
            all_ok = False
            results[cmd_name] = ''

    conn.disconnect()

    # Step 5: parse facts
    hdr("PARSED FACTS")
    facts = parse_facts_preview(results, vendor_key)
    for k, v in facts.items():
        if v:
            ok(f"{k:15}: {v}")
        else:
            warn(f"{k:15}: not found")

    # Step 6: summary
    hdr("SUMMARY")
    if all_ok:
        ok(f"All {len(commands)} commands succeeded")
        ok(f"Device is READY to add to Nautobot and sync")
        print(f"\n  Add to engineer CSV as:")
        print(f"    vendor_key : {vendor_key}")
        print(f"    platform   : {block.get('netmiko_device_type','')}")
        print(f"    ip         : {ip}/24")
    else:
        warn("Some commands failed — check output above")
        warn("Device may still work for partial sync")

    return all_ok


# ── API tests ─────────────────────────────────────────────────────────────────
def test_mist_api(token, org_id, base_url):
    hdr(f"JUNIPER MIST API TEST — {base_url}")

    info(f"Base URL : {base_url}")
    info(f"Org ID   : {org_id}")
    info(f"Token    : {token[:20]}...")

    # Step 1: auth check
    r = requests.get(
        f'{base_url}/api/v1/self',
        headers={'Authorization': f'Token {token}'},
        timeout=10
    )
    if not r.ok:
        fail(f"Auth failed: {r.status_code} {r.text[:100]}")
        fail("Check token and base_url region (api.mist.com / api.eu.mist.com / api.gc1.mist.com)")
        return False
    ok(f"Authentication successful")
    user_data = r.json()
    info(f"Account  : {user_data.get('name','?')} / {user_data.get('email','?')}")

    # Step 2: list orgs
    for priv in user_data.get('privileges', []):
        if priv.get('scope') == 'org':
            info(f"Org      : {priv.get('name','?')}  id:{priv.get('org_id','?')}")

    # Step 3: get inventory
    r2 = requests.get(
        f'{base_url}/api/v1/orgs/{org_id}/inventory',
        headers={'Authorization': f'Token {token}'},
        timeout=10
    )
    if not r2.ok:
        fail(f"Inventory failed: {r2.status_code}")
        return False

    inventory = r2.json()
    inventory = inventory if isinstance(inventory, list) else inventory.get('results', [])
    ok(f"Inventory: {len(inventory)} devices")
    for d in inventory[:5]:
        print(f"    {d.get('name','?'):20} {d.get('model','?'):15} serial:{d.get('serial','?')}")

    # Step 4: get stats
    r3 = requests.get(
        f'{base_url}/api/v1/orgs/{org_id}/stats/devices',
        headers={'Authorization': f'Token {token}'},
        timeout=10
    )
    if r3.ok:
        stats = r3.json()
        stats = stats if isinstance(stats, list) else stats.get('results', [])
        ok(f"Stats    : {len(stats)} devices with live data")
        for d in stats[:3]:
            status = 'up' if d.get('status') == 'connected' else 'down'
            print(f"    {d.get('_id','?'):20} ip:{d.get('ip','?'):15} status:{status}")

    hdr("SUMMARY")
    ok("Mist API is working correctly")
    ok("Device is READY to sync via Mist API")
    print(f"\n  Add to engineer CSV as:")
    print(f"    vendor_key : juniper_mist_ap")
    print(f"    managed_by : mist")
    return True


def test_aruba_central_api(client_id, client_secret, refresh_token, base_url):
    hdr(f"ARUBA CENTRAL API TEST — {base_url}")

    info(f"Base URL      : {base_url}")
    info(f"Client ID     : {client_id[:15]}...")
    info(f"Refresh token : {refresh_token[:15]}...")

    # Step 1: exchange refresh token
    r = requests.post(
        f'{base_url}/oauth2/token',
        params={
            'client_id':     client_id,
            'client_secret': client_secret,
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
        },
        timeout=15
    )
    if not r.ok:
        fail(f"Token exchange failed: {r.status_code} {r.text[:200]}")
        fail("Generate fresh token from: Aruba Central → API Gateway → My Apps & Tokens → Download Token")
        return False

    token_data    = r.json()
    access_token  = token_data.get('access_token','')
    new_refresh   = token_data.get('refresh_token','')
    ok(f"Token exchange successful (expires in {token_data.get('expires_in',0)//3600}h)")

    if new_refresh and new_refresh != refresh_token:
        warn(f"New refresh_token generated — update your .env file:")
        warn(f"  ARUBA_REFRESH_TOKEN_<SLUG>={new_refresh}")

    # Step 2: get APs
    r2 = requests.get(
        f'{base_url}/monitoring/v2/aps',
        headers={'Authorization': f'Bearer {access_token}'},
        params={'limit': 20},
        timeout=15
    )
    if not r2.ok:
        fail(f"AP list failed: {r2.status_code} {r2.text[:100]}")
        return False

    aps = r2.json().get('aps', [])
    ok(f"APs found: {len(aps)}")
    for ap in aps[:5]:
        status = ap.get('status','?')
        print(f"    {ap.get('name','?'):20} {ap.get('model','?'):12} "
              f"serial:{ap.get('serial','?'):15} status:{status}")

    # Step 3: get switches
    r3 = requests.get(
        f'{base_url}/monitoring/v1/switches',
        headers={'Authorization': f'Bearer {access_token}'},
        params={'limit': 10},
        timeout=15
    )
    if r3.ok:
        switches = r3.json().get('switches', [])
        if switches:
            ok(f"Switches: {len(switches)}")

    hdr("SUMMARY")
    ok("Aruba Central API is working correctly")
    ok("Devices are READY to sync via Aruba Central API")
    print(f"\n  Add to engineer CSV as:")
    print(f"    vendor_key : aruba_ap_central_api")
    print(f"    managed_by : aruba-central")
    return True


# ── Facts preview parser ──────────────────────────────────────────────────────
def parse_facts_preview(raw, yaml_key):
    """Quick facts extraction for preview."""
    facts = {'serial': '', 'firmware': '', 'hostname': '', 'model': ''}
    version = raw.get('version', '')

    if yaml_key == 'fortios':
        for line in version.splitlines():
            line = line.strip()
            if line.startswith('Version:'):
                facts['firmware'] = line.replace('Version:','').strip()[:50]
            elif line.startswith('Serial-Number:'):
                facts['serial'] = line.replace('Serial-Number:','').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:','').strip()

    elif yaml_key in ('aruba_os', 'aruba_aoscx'):
        for line in version.splitlines():
            line = line.strip()
            if line and len(line) < 20 and '.' in line and line[0].isalpha():
                facts['firmware'] = line
        sysinfo = raw.get('system_info', '')
        for line in sysinfo.splitlines():
            if 'Serial Number' in line and ':' in line:
                facts['serial'] = line.split(':')[-1].strip()
                break
            if 'System Name' in line and ':' in line:
                facts['hostname'] = line.split(':')[-1].strip()

    elif yaml_key in ('juniper_junos', 'juniper_srx'):
        for line in version.splitlines():
            line = line.strip()
            if line.startswith('Junos:'):
                facts['firmware'] = line.replace('Junos:','').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:','').strip()
            elif line.startswith('Model:'):
                facts['model'] = line.replace('Model:','').strip()
        chassis = raw.get('chassis_hardware', '')
        for line in chassis.splitlines():
            if line.strip().startswith('Chassis'):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != 'BUILTIN':
                    facts['serial'] = parts[1]
                    break

    elif yaml_key in ('cisco_ios', 'cisco_iosxe', 'cisco_nxos'):
        for line in version.splitlines():
            if 'Processor board ID' in line:
                facts['serial'] = line.split('ID')[-1].strip()
            if 'Cisco IOS' in line and 'Version' in line:
                facts['firmware'] = line.strip()[:60]

    return facts


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Test vendor connectivity and command parsing before onboarding'
    )
    parser.add_argument('--list-vendors',    action='store_true', help='List all vendor blocks in YAML')
    parser.add_argument('--vendor',          help='Vendor key (e.g. fortios, aruba_os, juniper_mist_ap)')
    parser.add_argument('--ip',              help='Device IP address')
    parser.add_argument('--user',            help='SSH username')
    parser.add_argument('--password',        help='SSH password')
    parser.add_argument('--enable',          default='', help='Enable/secret password (Cisco)')
    parser.add_argument('--token',           help='API token (Mist)')
    parser.add_argument('--org-id',          help='Org ID (Mist)')
    parser.add_argument('--base-url',        help='API base URL')
    parser.add_argument('--client-id',       help='OAuth client ID (Aruba Central)')
    parser.add_argument('--client-secret',   help='OAuth client secret (Aruba Central)')
    parser.add_argument('--refresh-token',   help='OAuth refresh token (Aruba Central)')
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"  vendor_test.py — Pre-onboarding connectivity tester")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}")

    if args.list_vendors:
        list_vendors()
        return

    if not args.vendor:
        parser.print_help()
        return

    block = find_block(args.vendor)
    if not block:
        fail(f"Vendor '{args.vendor}' not found in YAML")
        info("Run: python3 vendor_test.py --list-vendors")
        return

    info(f"Found YAML block: {block['_section']}/{args.vendor}")
    data_source = block.get('data_source','ssh')

    if data_source == 'ssh':
        if not args.ip:
            fail("--ip required for SSH vendors")
            return
        if not args.user:
            fail("--user required for SSH vendors")
            return
        if not args.password:
            fail("--password required for SSH vendors")
            return
        test_ssh(args.ip, args.vendor, block, args.user, args.password, args.enable)

    elif data_source == 'api':
        api_type = block.get('api_type','')
        if api_type == 'juniper_mist':
            if not all([args.token, args.org_id, args.base_url]):
                fail("--token, --org-id, --base-url required for Mist API")
                return
            test_mist_api(args.token, args.org_id, args.base_url)
        elif api_type == 'aruba_central':
            if not all([args.client_id, args.client_secret, args.refresh_token, args.base_url]):
                fail("--client-id, --client-secret, --refresh-token, --base-url required")
                return
            test_aruba_central_api(
                args.client_id, args.client_secret,
                args.refresh_token, args.base_url
            )
        else:
            warn(f"API type '{api_type}' test not yet implemented")

    print()

if __name__ == '__main__':
    main()
