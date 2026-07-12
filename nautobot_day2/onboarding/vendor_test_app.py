"""
vendor_test_app.py
Standalone vendor connectivity tester — web portal on port 8082.
Test single device or batch from CSV before onboarding to Nautobot.
"""

import os
import sys
import yaml
import json
import argparse
import requests as req_lib
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

LAB_DIR   = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.environ.get(
    'VENDOR_COMMANDS_PATH',
    os.path.join(os.path.dirname(LAB_DIR), 'vendor_commands', 'vendor_commands.yaml')
)

app = Flask(__name__, template_folder=os.path.join(LAB_DIR, 'templates'))

# ── YAML loader ───────────────────────────────────────────────────────────────
_yaml_cache = None
def load_yaml():
    global _yaml_cache
    if not _yaml_cache:
        with open(YAML_PATH) as f:
            _yaml_cache = yaml.safe_load(f)
    return _yaml_cache

def find_block(vendor_key):
    data = load_yaml()
    for section, vendors in data.items():
        if vendor_key in vendors:
            block = dict(vendors[vendor_key])
            block['_section']  = section
            block['_yaml_key'] = vendor_key
            return block
    return None


# ── Facts parser ──────────────────────────────────────────────────────────────
def parse_facts(raw, yaml_key):
    facts = {}

    # API response
    if raw.get('serial') or raw.get('mac'):
        for k in ('serial','firmware','model','status','mac','ip','hostname'):
            if raw.get(k):
                facts[k] = raw[k]
        return facts

    version = raw.get('version', '')

    if yaml_key == 'fortios':
        for line in version.splitlines():
            line = line.strip()
            if line.startswith('Version:'):
                facts['firmware'] = line.replace('Version:','').strip()[:60]
            elif line.startswith('Serial-Number:'):
                facts['serial'] = line.replace('Serial-Number:','').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:','').strip()

    elif yaml_key in ('aruba_os','aruba_aoscx'):
        for line in version.splitlines():
            line = line.strip()
            if line and len(line)<20 and '.' in line and line[0].isalpha():
                facts['firmware'] = line
        sysinfo = raw.get('system_info','')
        for line in sysinfo.splitlines():
            if 'Serial Number' in line and ':' in line:
                facts['serial'] = line.split(':')[-1].strip(); break
        for line in sysinfo.splitlines():
            if 'System Name' in line and ':' in line:
                facts['hostname'] = line.split(':')[-1].strip(); break

    elif yaml_key in ('juniper_junos','juniper_srx'):
        for line in version.splitlines():
            line = line.strip()
            if line.startswith('Junos:'):
                facts['firmware'] = line.replace('Junos:','').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:','').strip()
            elif line.startswith('Model:'):
                facts['model'] = line.replace('Model:','').strip()
        chassis = raw.get('chassis_hardware','')
        for line in chassis.splitlines():
            if line.strip().startswith('Chassis'):
                parts = line.split()
                if len(parts)>=2 and parts[1]!='BUILTIN':
                    facts['serial'] = parts[1]; break

    elif yaml_key in ('cisco_ios','cisco_iosxe','cisco_nxos'):
        for line in version.splitlines():
            if 'Processor board ID' in line:
                facts['serial'] = line.split('ID')[-1].strip()
            if 'Cisco IOS' in line and 'Version' in line:
                facts['firmware'] = line.strip()[:60]

    return facts


# ── SSH tester ────────────────────────────────────────────────────────────────
def test_ssh(ip, vendor_key, block, user, password, enable=''):
    result = {
        'vendor':      vendor_key,
        'ip':          ip,
        'data_source': 'ssh',
        'commands':    {},
        'facts':       {},
        'status':      'failed',
        'message':     '',
    }

    try:
        from netmiko import ConnectHandler
        from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
    except ImportError:
        result['message'] = 'netmiko not installed'
        return result

    # TCP check
    import socket
    try:
        s = socket.create_connection((ip, 22), timeout=5)
        s.close()
    except Exception as e:
        result['message'] = f'TCP unreachable: {e}'
        result['fix_hint'] = 'Check IP address, routing, and firewall rules from this server'
        return result

    # SSH connect
    conn_params = {
        'device_type':  block.get('netmiko_device_type',''),
        'host':         ip,
        'username':     user,
        'password':     password,
        'timeout':      20,
        'auth_timeout': 20,
    }
    if enable:
        conn_params['secret'] = enable

    try:
        conn = ConnectHandler(**conn_params)
    except NetmikoAuthenticationException:
        result['message'] = 'Authentication failed — wrong username or password'
        result['fix_hint'] = 'Verify credentials by SSHing manually: ssh {}@{}'.format(user, ip)
        return result
    except NetmikoTimeoutException:
        result['message'] = 'SSH timeout — device not responding on port 22'
        return result
    except Exception as e:
        result['message'] = str(e)[:100]
        return result

    # Disable paging
    if block.get('disable_paging'):
        try:
            conn.send_command(block['disable_paging'])
        except:
            pass

    # Run commands
    commands = block.get('commands', {})
    raw_output = {}
    all_ok = True

    for cmd_name, cmd_str in commands.items():
        try:
            output = conn.send_command(cmd_str, read_timeout=30,
                                       expect_string=r'#|\$|>')
            lines  = [l for l in output.splitlines() if l.strip()]
            preview = '\n'.join(lines[:5])
            result['commands'][cmd_name] = {
                'ok':      True,
                'empty':   len(lines) == 0,
                'lines':   len(lines),
                'preview': preview,
                'cmd_str': cmd_str,
            }
            raw_output[cmd_name] = output
        except Exception as e:
            result['commands'][cmd_name] = {
                'ok':      False,
                'error':   str(e)[:100],
                'cmd_str': cmd_str,
            }
            all_ok = False

    conn.disconnect()

    # Parse facts
    result['facts'] = parse_facts(raw_output, vendor_key)

    # Status
    failed_cmds  = [k for k,v in result['commands'].items() if not v.get('ok')]
    empty_cmds   = [k for k,v in result['commands'].items() if v.get('empty')]

    if not failed_cmds:
        result['status']  = 'ready'
        result['message'] = f'All {len(commands)} commands succeeded'
        result['csv_hint'] = (
            f'Ready to onboard. Add to CSV with: '
            f'vendor={vendor_key}  platform={block.get("netmiko_device_type","")}  ip={ip}/24'
        )
    elif len(failed_cmds) < len(commands):
        result['status']  = 'partial'
        result['message'] = f'{len(failed_cmds)} commands failed: {", ".join(failed_cmds)}'
    else:
        result['status']  = 'failed'
        result['message'] = f'All commands failed'

    return result


# ── Mist API tester ───────────────────────────────────────────────────────────
def test_mist(token, org_id, base_url):
    result = {
        'vendor':      'juniper_mist_ap',
        'data_source': 'api',
        'commands':    {},
        'facts':       {},
        'status':      'failed',
        'message':     '',
    }

    # Auth
    r = req_lib.get(f'{base_url}/api/v1/self',
                    headers={'Authorization': f'Token {token}'}, timeout=10)
    if not r.ok:
        result['message'] = f'Auth failed {r.status_code} — wrong token or region'
        result['fix_hint'] = 'Try regions: api.mist.com / api.eu.mist.com / api.gc1.mist.com'
        return result

    result['commands']['auth'] = {'ok': True, 'preview': 'Authentication successful', 'cmd_str': 'GET /api/v1/self'}

    # Inventory
    r2 = req_lib.get(f'{base_url}/api/v1/orgs/{org_id}/inventory',
                     headers={'Authorization': f'Token {token}'}, timeout=10)
    if not r2.ok:
        result['message'] = f'Inventory failed {r2.status_code}'
        return result

    inventory = r2.json()
    inventory = inventory if isinstance(inventory, list) else inventory.get('results',[])
    preview   = '\n'.join([f"{d.get('name','?'):20} {d.get('model','?'):12} serial:{d.get('serial','?')}"
                           for d in inventory[:5]])
    result['commands']['inventory'] = {
        'ok': True, 'lines': len(inventory),
        'preview': preview, 'cmd_str': 'GET /api/v1/orgs/{org_id}/inventory'
    }

    # Stats
    r3 = req_lib.get(f'{base_url}/api/v1/orgs/{org_id}/stats/devices',
                     headers={'Authorization': f'Token {token}'}, timeout=10)
    if r3.ok:
        stats = r3.json()
        stats = stats if isinstance(stats, list) else stats.get('results',[])
        preview2 = '\n'.join([f"{d.get('_id','?'):20} ip:{d.get('ip','?'):15} status:{d.get('status','?')}"
                               for d in stats[:3]])
        result['commands']['stats'] = {
            'ok': True, 'lines': len(stats),
            'preview': preview2, 'cmd_str': 'GET /api/v1/orgs/{org_id}/stats/devices'
        }

    if inventory:
        d = inventory[0]
        result['facts'] = {
            'serial':   d.get('serial',''),
            'model':    d.get('model',''),
            'hostname': d.get('name',''),
        }

    result['status']   = 'ready'
    result['message']  = f'{len(inventory)} devices found in org'
    result['csv_hint'] = 'Ready. Add to CSV with: vendor=juniper_mist_ap  managed_by=mist'
    return result


# ── Aruba Central tester ──────────────────────────────────────────────────────
def test_aruba_central(client_id, client_secret, refresh_token, base_url):
    result = {
        'vendor':      'aruba_ap_central_api',
        'data_source': 'api',
        'commands':    {},
        'facts':       {},
        'status':      'failed',
        'message':     '',
    }

    # Token exchange
    r = req_lib.post(f'{base_url}/oauth2/token',
                     params={'client_id': client_id, 'client_secret': client_secret,
                             'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                     timeout=15)
    if not r.ok:
        result['message'] = f'Token exchange failed {r.status_code}'
        result['fix_hint'] = 'Generate fresh token: Aruba Central → API Gateway → My Apps & Tokens → Download Token'
        return result

    token_data   = r.json()
    access_token = token_data.get('access_token','')
    new_refresh  = token_data.get('refresh_token','')

    result['commands']['token_exchange'] = {
        'ok': True,
        'preview': f"access_token obtained (expires {token_data.get('expires_in',0)//3600}h)\nnew refresh_token: {new_refresh[:30]}...",
        'cmd_str': 'POST /oauth2/token'
    }

    if new_refresh and new_refresh != refresh_token:
        result['fix_hint'] = f'New refresh_token generated — update env: ARUBA_REFRESH_TOKEN_<SLUG>={new_refresh}'

    # Get APs
    r2 = req_lib.get(f'{base_url}/monitoring/v2/aps',
                     headers={'Authorization': f'Bearer {access_token}'},
                     params={'limit': 20}, timeout=15)
    if not r2.ok:
        result['message'] = f'AP list failed {r2.status_code}'
        return result

    aps     = r2.json().get('aps', [])
    preview = '\n'.join([f"{ap.get('name','?'):20} {ap.get('model','?'):12} "
                         f"serial:{ap.get('serial','?'):15} status:{ap.get('status','?')}"
                         for ap in aps[:5]])
    result['commands']['ap_list'] = {
        'ok': True, 'lines': len(aps),
        'preview': preview, 'cmd_str': 'GET /monitoring/v2/aps'
    }

    if aps:
        result['facts'] = {
            'serial':   aps[0].get('serial',''),
            'model':    aps[0].get('model',''),
            'hostname': aps[0].get('name',''),
            'status':   aps[0].get('status',''),
        }

    result['status']   = 'ready'
    result['message']  = f'{len(aps)} APs found'
    result['csv_hint'] = 'Ready. Add to CSV with: vendor=aruba_ap_central_api  managed_by=aruba-central'
    return result


# ── API routes ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('vendor_test.html')

@app.route('/api/vendors')
def api_vendors():
    data    = load_yaml()
    vendors = {}
    for section, vmap in data.items():
        for key, block in vmap.items():
            vendors[key] = {
                'section':       section,
                'data_source':   block.get('data_source','ssh'),
                'api_type':      block.get('api_type',''),
                'driver':        block.get('netmiko_device_type') or block.get('api_type',''),
                'command_count': len(block.get('commands', block.get('api_endpoints',{}))),
            }
    return jsonify(vendors)

@app.route('/api/test-device', methods=['POST'])
def api_test_device():
    data       = request.json
    vendor_key = data.get('vendor','')
    if not vendor_key:
        return jsonify({'status':'error','message':'vendor required'}), 400

    block = find_block(vendor_key)
    if not block:
        return jsonify({'status':'error',
                        'message':f'Vendor {vendor_key!r} not found in YAML'}), 404

    ds = block.get('data_source','ssh')

    if ds == 'ssh':
        result = test_ssh(
            ip        = data.get('ip',''),
            vendor_key= vendor_key,
            block     = block,
            user      = data.get('user',''),
            password  = data.get('password',''),
            enable    = data.get('enable',''),
        )
    elif block.get('api_type') == 'juniper_mist':
        result = test_mist(
            token    = data.get('token',''),
            org_id   = data.get('org_id',''),
            base_url = data.get('base_url','https://api.mist.com'),
        )
    elif block.get('api_type') == 'aruba_central':
        result = test_aruba_central(
            client_id     = data.get('client_id',''),
            client_secret = data.get('client_secret',''),
            refresh_token = data.get('refresh_token',''),
            base_url      = data.get('base_url',''),
        )
    else:
        result = {'status':'skip',
                  'message':f'API type {block.get("api_type","")} test not yet implemented',
                  'vendor': vendor_key}

    result['device_name'] = data.get('device_name', data.get('ip', vendor_key))
    return jsonify(result)

@app.route('/health')
def health():
    return jsonify({'status':'ok','port':8082,'yaml':YAML_PATH})


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8082)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    print(f"\n  Vendor Connection Tester")
    print(f"  URL  : http://{args.host}:{args.port}")
    print(f"  YAML : {YAML_PATH}\n")

    app.run(host=args.host, port=args.port, debug=False)
