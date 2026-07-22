"""
sync_network_data.py
Phase 6 — Sync live network data into Nautobot.
YAML-driven: all SSH commands and API endpoints come from vendor_commands.yaml.
Same file used by production — no divergence.

Dispatch flow:
  device → platform slug + role + secrets_group
        → resolve_vendor() → (section, yaml_key)
        → load YAML block → ssh_get_data() or api_get_data()
        → parse output → write to Nautobot

SIMULATED = True  → fake command output, real Nautobot writes (testing)
SIMULATED = False → real SSH/API connections (production)

Usage:
    python3 sync_network_data.py --site Acme-BLR-01 --tenant acme-retail-ltd
    python3 sync_network_data.py --site Acme-BLR-01 --tenant acme-retail-ltd --category switches
    python3 sync_network_data.py --site Acme-BLR-01 --tenant acme-retail-ltd --failed-only
    python3 sync_network_data.py --site Acme-BLR-01 --tenant acme-retail-ltd --dry-run
"""

import sys
import os
import json
import yaml
import argparse
import requests
from datetime import datetime
from tabulate import tabulate

LAB_DIR       = os.path.dirname(os.path.abspath(__file__))
MANIFESTS_DIR = os.path.join(LAB_DIR, 'manifests')

# Same YAML path as production — single source of truth
VENDOR_COMMANDS_PATH = os.environ.get(
    'VENDOR_COMMANDS_PATH',
    os.path.join(os.path.dirname(LAB_DIR), 'vendor_commands', 'vendor_commands.yaml')
)

sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.dirname(LAB_DIR))
from vendor_matrix import VENDOR_MATRIX
from client import NautobotClient
from nautobot_day2.openbao_client import fetch_openbao_secret

client = NautobotClient(env_file=os.path.join(LAB_DIR, '.env'))
URL   = client.url
TOKEN = client.token

# ── Global flag — flip to False per vendor when real devices ready ────────────
SIMULATED = True

# ── Per-vendor override — set False when real device ready ───────────────────
SIMULATED_OVERRIDE = {
    'fortios':            False,  # FortiGate live SSH
    'fortinet_ap_ssh':    False,  # FortiAP live SSH
    'fortinet_switch':    False,  # FortiSwitch live SSH
    'aruba_os':           False,  # Aruba AOS switch live SSH
    'aruba_aoscx':        False,  # Aruba AOS-CX switch live SSH
    'aruba_ap_aos10':     False,  # Aruba AP standalone SSH
    'aruba_ap_central_api': False, # Aruba Central API
    'juniper_junos':      False,  # Juniper EX switch live SSH
    'juniper_srx':        False,  # Juniper SRX firewall live SSH
    'juniper_mist_ap':    False,  # Juniper Mist API
    'cisco_iosxe':        False,  # Cisco IOS-XE live SSH
    'cisco_ios':          False,  # Cisco IOS live SSH
    'cisco_nxos':         False,  # Cisco NX-OS live SSH
    'cisco_asa':          False,  # Cisco ASA live SSH
    'cisco_ftd':          False,  # Cisco FTD live SSH
}

# ── Category → secrets group prefixes ────────────────────────────────────────
CATEGORY_MAP = {
    'switches':  ['aruba-ssh', 'juniper-ssh', 'cisco-ssh', 'fortinet-ssh'],
    'firewalls': ['fortinet-ssh', 'juniper-ssh', 'cisco-ssh',
                  'cisco-fmc-api', 'fortinet-manager-api'],
    'aps':       ['aruba-central-api', 'juniper-mist-api',
                  'fortinet-manager-api', 'aruba-ssh'],
    'nac':       ['aruba-clearpass-api'],
    'all':       None,
}

# ── Platform → (yaml_section, yaml_key) ──────────────────────────────────────
# Keyed by EXACT derived slug from Nautobot natural_slug (strip _xxxx suffix).
# Includes both old lab platforms and new bootstrap platforms.
PLATFORM_MAP = {
    # ── Old lab platforms (pre-existing in Nautobot) ──────────────────────
    'aruba-aos':          ('switches',      'aruba_os'),
    'aruba-aoscx':        ('switches',      'aruba_aoscx'),
    'junos':              ('switches',      'juniper_junos'),
    'juniper-mist':       ('access_points', 'juniper_mist_ap'),
    'fortios':            ('firewalls',     'fortios'),
    # ── New platforms from bootstrap ──────────────────────────────────────
    'arubaos-aos-s':      ('switches',      'aruba_os'),    # matches "ArubaOS (AOS-S)" label's real natural_slug
    'arubaos-ap':         ('switches',      'aruba_os'),    # role override → AP section
    'arubaos-cx-aos-cx':  ('switches',      'aruba_aoscx'),
    'aruba-clearpass':    ('access_points', 'aruba_clearpass_api'),
    'asa':                ('firewalls',     'cisco_asa'),
    'ftd':                ('firewalls',     'cisco_ftd'),
    'ios-xe':             ('switches',      'cisco_iosxe'),
    'ios':                ('switches',      'cisco_ios'),
    'nx-os':              ('switches',      'cisco_nxos'),
    'fortios-ap':         ('access_points', 'fortinet_ap_ssh'),
    'fortios-switch':     ('switches',      'fortinet_switch'),
    'junos-ap-mist':      ('access_points', 'juniper_mist_ap'),
    'junos-srx':          ('firewalls',     'juniper_srx'),
}

# Role override — same platform slug, different section based on role
# Covers cases where platform slug is ambiguous (e.g. arubaos-ap = switch OR ap)
ROLE_OVERRIDE = {
    # Aruba — old lab slug
    ('aruba-aos',         'ap'):            ('access_points', 'aruba_ap_aos10'),
    ('aruba-aos',         'access-switch'): ('switches',      'aruba_os'),
    ('aruba-aos',         'core-switch'):   ('switches',      'aruba_os'),
    ('aruba-aos',         'nac'):           ('access_points', 'aruba_clearpass_api'),
    # Aruba — new bootstrap slug
    ('arubaos-ap',        'ap'):            ('access_points', 'aruba_ap_aos10'),
    ('arubaos-ap',        'access-switch'): ('switches',      'aruba_os'),
    ('arubaos-ap',        'core-switch'):   ('switches',      'aruba_os'),
    ('arubaos-ap',        'nac'):           ('access_points', 'aruba_clearpass_api'),
    # Juniper — old lab slug
    ('junos',             'ap'):            ('access_points', 'juniper_mist_ap'),
    ('junos',             'access-switch'): ('switches',      'juniper_junos'),
    ('junos',             'core-switch'):   ('switches',      'juniper_junos'),
    ('junos',             'branch-fw'):     ('firewalls',     'juniper_srx'),
    # Juniper — new bootstrap slugs
    ('junos-ap-mist',     'ap'):            ('access_points', 'juniper_mist_ap'),
    ('juniper-mist',      'ap'):            ('access_points', 'juniper_mist_ap'),
    ('junos-srx',         'branch-fw'):     ('firewalls',     'juniper_srx'),
}

# Secrets group prefix → API override
# When managed_by is API-based, use API block regardless of platform
SG_API_OVERRIDE = {
    'aruba-central-api':    ('access_points', 'aruba_ap_central_api'),
    'juniper-mist-api':     ('access_points', 'juniper_mist_ap'),
    'fortinet-manager-api': ('access_points', 'fortinet_ap_manager_api'),
    'cisco-fmc-api':        ('firewalls',     'cisco_fmc_api'),
    'aruba-clearpass-api':  ('access_points', 'aruba_clearpass_api'),
}


# ── Error types ───────────────────────────────────────────────────────────────
ERR_AUTH        = 'auth_failure'
ERR_TIMEOUT     = 'timeout'
ERR_UNREACHABLE = 'unreachable'
ERR_PLATFORM    = 'platform_mismatch'
ERR_API_SCOPE   = 'api_scope'
ERR_NOT_FOUND   = 'device_not_found'
ERR_UNKNOWN     = 'unknown'

ERROR_FIXES = {
    ERR_AUTH:        'Check env var in tenant .env → restart nautobot-worker → re-run sync',
    ERR_TIMEOUT:     'Verify device reachable: ping <ip> from lab server',
    ERR_UNREACHABLE: 'Check IP, routing, firewall rules from lab server to device',
    ERR_PLATFORM:    'Update platform in Nautobot UI → DCiM → Devices → Edit → Platform',
    ERR_API_SCOPE:   'Check API token permissions — may need additional scopes',
    ERR_NOT_FOUND:   'Device not registered in cloud controller — register first',
    ERR_UNKNOWN:     'Check sync log for full error details',
}


# ── SyncResult ────────────────────────────────────────────────────────────────
class SyncResult:
    def __init__(self, device_name, yaml_key):
        self.device_name = device_name
        self.yaml_key    = yaml_key
        self.status      = 'pending'
        self.error_type  = None
        self.error_msg   = ''
        self.fix         = ''
        self.interfaces  = []
        self.neighbors   = []
        self.facts       = {}
        self.writes      = {'interfaces': 0, 'facts': 0, 'cables': 0}
        self.simulated   = SIMULATED_OVERRIDE.get(yaml_key, SIMULATED)

    def success(self):   self.status = 'success'
    def skip(self, r):   self.status = 'skipped';  self.error_msg = r
    def fail(self, t, m, f=''):
        self.status     = 'failed'
        self.error_type = t
        self.error_msg  = m
        self.fix        = f


# ── YAML loader ───────────────────────────────────────────────────────────────
_vendor_cmds_cache = None

def load_vendor_commands():
    global _vendor_cmds_cache
    if _vendor_cmds_cache:
        return _vendor_cmds_cache
    with open(VENDOR_COMMANDS_PATH) as f:
        _vendor_cmds_cache = yaml.safe_load(f)
    return _vendor_cmds_cache

def get_yaml_block(section, yaml_key):
    """Return the YAML block for (section, yaml_key). None if not found."""
    cmds = load_vendor_commands()
    return cmds.get(section, {}).get(yaml_key)


# ── Vendor resolver ───────────────────────────────────────────────────────────
def resolve_vendor(platform_slug, role_name, sg_name):
    """
    Resolve (platform_slug, role_name, sg_name) → (section, yaml_key).

    Priority:
    1. Secrets group prefix → API override (managed_by drives this)
    2. Role override (same platform, role determines section)
    3. Platform map (default)
    """
    # 1. API override from secrets group prefix
    sg_prefix = sg_name.rsplit('-', 1)[0] if sg_name else ''
    # Match longest prefix first
    for prefix, result in SG_API_OVERRIDE.items():
        if sg_name.startswith(prefix + '-') or sg_name == prefix:
            return result

    # 2. Role override
    key = (platform_slug, role_name)
    if key in ROLE_OVERRIDE:
        return ROLE_OVERRIDE[key]

    # 3. Platform map
    if platform_slug in PLATFORM_MAP:
        return PLATFORM_MAP[platform_slug]

    return None, None


# ── Credential resolver ───────────────────────────────────────────────────────
def resolve_creds(sg_name, tenant_slug):
    """Derive credentials from secrets_group name + env vars."""
    suffix = tenant_slug.upper().replace('-', '_')

    prefix_map = {
        'aruba-ssh':            {'user': f'ARUBA_SSH_USER_{suffix}',
                                 'password': f'ARUBA_SSH_PASS_{suffix}'},
        # Aruba Central OAuth2
        # Classic Central: client_id + client_secret + refresh_token → access_token (2hr)
        # GreenLake:       client_id + client_secret → access_token directly
        # ARUBA_CENTRAL_TYPE = "classic" or "greenlake"
        'aruba-central-api':    {'client_id':     f'ARUBA_CLIENT_ID_{suffix}',
                                 'client_secret': f'ARUBA_CLIENT_SECRET_{suffix}',
                                 'refresh_token': f'ARUBA_REFRESH_TOKEN_{suffix}',
                                 'base_url':      f'ARUBA_CENTRAL_BASE_URL_{suffix}',
                                 'central_type':  f'ARUBA_CENTRAL_TYPE_{suffix}'},
        'aruba-clearpass-api':  {'token': f'ARUBA_CLEARPASS_API_TOKEN_{suffix}',
                                 'base_url': f'ARUBA_CLEARPASS_BASE_URL_{suffix}'},
        'juniper-ssh':          {'user': f'JUNIPER_SSH_USER_{suffix}',
                                 'password': f'JUNIPER_SSH_PASS_{suffix}'},
        'juniper-mist-api':     {'token':    f'MIST_API_TOKEN_{suffix}',
                                 'org_id':   f'MIST_ORG_ID_{suffix}',
                                 'base_url': f'MIST_BASE_URL_{suffix}'},
        'cisco-ssh':            {'user': f'CISCO_SSH_USER_{suffix}',
                                 'password': f'CISCO_SSH_PASS_{suffix}',
                                 'enable': f'CISCO_ENABLE_PASS_{suffix}'},
        'cisco-fmc-api':        {'token': f'CISCO_FMC_API_TOKEN_{suffix}',
                                 'base_url': f'CISCO_FMC_BASE_URL_{suffix}'},
        'fortinet-ssh':         {'user': f'FORTINET_SSH_USER_{suffix}',
                                 'password': f'FORTINET_SSH_PASS_{suffix}'},
        'fortinet-manager-api': {'token': f'FORTINET_MGR_API_TOKEN_{suffix}',
                                 'base_url': f'FORTINET_MGR_BASE_URL_{suffix}',
                                 'adom': f'FORTINET_MGR_ADOM_{suffix}'},
    }

    for prefix, var_map in prefix_map.items():
        if sg_name.startswith(prefix + '-') or sg_name == prefix:
            secret_data = fetch_openbao_secret(tenant_slug.lower(), prefix)
            creds = {k: secret_data.get(v, '') for k, v in var_map.items()}
            creds['_prefix']  = prefix
            creds['_var_map'] = var_map
            return creds

    return {'_prefix': 'unknown', '_var_map': {}}


# ── Simulated output generators ───────────────────────────────────────────────
# Each returns a dict matching the YAML commands structure for that block.
# When SIMULATED=False, replace with real SSH/API calls.

def _sim_output(yaml_key, device_name):
    """
    Generate realistic fake command output keyed by yaml_key.
    Output format: {command_name: "raw text output as string"}
    This is what ssh_get_data() would return from a real device.
    """
    sim_map = {
        # ── SWITCHES ─────────────────────────────────────────────────────
        'aruba_os': {
            'version':       f"Image stamp:    /flash/primary/os/ArubaOS KB.16.10.0013\nHW Model Number:  J9729A\nSerial Number:    SG{device_name[-4:].upper()}",
            'interfaces':    f"1        100/1000T  Yes    Up    1000  Full  1       No",
            'ip_interfaces': f"VLAN1   10.20.1.1   255.255.255.0   Yes  Manual  Up",
            'lldp_neighbors':f"1        {device_name}-core  1/49  {device_name}-core",
            'vlans':         "1    DEFAULT_VLAN      Active   1-24\n10   MGMT             Active   25",
        },
        'aruba_aoscx': {
            'version':       f"ArubaOS-CX 10.10.1020\nPlatform: 6300M\nSerial: SG{device_name[-4:].upper()}",
            'interfaces':    f"1/1/1  up    up    1000M   full   1    native",
            'ip_interfaces': f"vlan1  10.20.1.2/24  up  up  vlan",
            'lldp_neighbors':f"1/1/1   {device_name}-core   1/1/48   --   120   core-sw",
            'vlans':         "1    DEFAULT     active  no reason\n10   MGMT        active  no reason",
        },
        'juniper_junos': {
            'version':       f"Junos: 21.4R3.15\nModel: EX2300-24T\nHostname: {device_name}",
            'interfaces':    f"ge-0/0/0   up    up\nge-0/0/1   up    up\nme0        up    up",
            'ip_interfaces': f"me0.0     10.20.1.3    24",
            'lldp_neighbors':f"ge-0/0/0  --  aa:bb:cc:dd:ee:ff  ge-0/0/48  core-sw",
            'vlans':         "default  0    ge-0/0/0-23\nMGMT     10   me0",
        },
        'cisco_iosxe': {
            'version':       f"Cisco IOS XE Software, Version 17.9.4\nProcessor board ID SG{device_name[-4:].upper()}",
            'interfaces':    f"Gi1/0/1   connected  1    a-full a-1G  10/100/1000BaseTX",
            'ip_interfaces': f"Vlan1   10.20.1.4   YES manual up   up",
            'lldp_neighbors':f"System Name: core-sw\nLocal Intf: Gi1/0/48\nPort id: Gi1/0/1",
            'cdp_neighbors': f"Device ID: core-sw\nInterface: GigabitEthernet1/0/48\nPort ID: GigabitEthernet1/0/1",
            'vlans':         "1    default   active\n10   MGMT      active",
        },
        'cisco_ios': {
            'version':       f"Cisco IOS Software, Version 15.2(7)E5\nProcessor board ID SG{device_name[-4:].upper()}",
            'interfaces':    f"Fa0/1   connected   1    a-full  a-100  10/100BaseTX",
            'ip_interfaces': f"Vlan1   10.20.1.5   YES manual up   up",
            'lldp_neighbors':f"System Name: core-sw\nLocal Intf: Gi0/1",
            'cdp_neighbors': f"Device ID: core-sw\nInterface: GigabitEthernet0/1",
            'vlans':         "1    default   active\n10   MGMT      active",
        },
        'cisco_nxos': {
            'version':       f"Cisco Nexus Operating System (NX-OS) Software\nProcessor Board ID SG{device_name[-4:].upper()}",
            'interfaces':    f"Eth1/1   connected   trunk  full   10G    10Gbase-SR",
            'ip_interfaces': f"Vlan1   10.20.1.6   YES manual up   up",
            'lldp_neighbors':f"System Name: core-sw\nLocal Intf: Eth1/48",
            'cdp_neighbors': f"Device ID: core-sw\nInterface: Ethernet1/48",
            'vlans':         "1    default   active\n10   MGMT      active",
        },
        'fortinet_switch': {
            'version':       f"Version: FortiSwitchOS v7.2.5\nSerial-Number: FS{device_name[-4:].upper()}",
            'interfaces':    f"port1  1    ethernet  up",
            'ip_interfaces': f"port1  10.20.1.7  255.255.255.0  up",
            'lldp_neighbors':f"port1  aa:bb:cc:dd:ee:ff  port24  core-fw",
            'vlans':         "1   default   -",
        },
        # ── ACCESS POINTS ─────────────────────────────────────────────────
        'aruba_ap_aos10': {
            'version':       f"ArubaOS 10.4.0.0\nAP Model: AP-515\nSerial: AP{device_name[-4:].upper()}",
            'interfaces':    f"eth0 is up, line protocol is up\nHardware is 2.5 Gigabit Ethernet",
            'lldp_neighbors':f"eth0  aa:bb:cc:dd:ee:ff  active  GigabitEthernet1/0/2  120",
            'lldp_detail':   f"AP-BLR-01  eth0  aa:bb:cc:dd:ee:ff  Gi1/0/2  - - 1 - access-sw - 192.168.1.1",
        },
        'fortinet_ap_ssh': {
            'version':       f"Version: FortiOS v7.2.5\nSerial-Number: FAP{device_name[-4:].upper()}",
            'interfaces':    f"== [wan] ==\nip: 10.20.1.10\nstatus: up",
            'lldp_neighbors':f"0 port 'wan' 120 mac aa:bb:cc:dd:ee:ff chassis 1 port 'Gi1/0/3' system 'access-sw'",
        },
        # ── FIREWALLS ─────────────────────────────────────────────────────
        'fortios': {
            'version':       f"Version: FortiGate-60F v7.2.5\nSerial-Number: FG{device_name[-4:].upper()}",
            'interfaces':    f"== [wan1] ==\nip: 0.0.0.0 0.0.0.0\nstatus: up\ntype: physical",
            'lldp_neighbors':f"0 port 'lan' 120 mac aa:bb:cc:dd:ee:ff chassis 1 port 'Gi1/0/1' system 'access-sw'",
            'routing_table': f"S*   0.0.0.0/0 [10/0] via 192.168.1.1",
            'lldp_detail':   f"lldprx.neighbor.0.port.id.data: Gi1/0/1\nlldprx.neighbor.0.system.name.data: access-sw",
        },
        'juniper_srx': {
            'version':       f"Junos: 22.2R1.9\nModel: SRX300\nHostname: {device_name}",
            'interfaces':    f"ge-0/0/0   up    up\nge-0/0/1   up    up\nfxp0       up    up",
            'ip_interfaces': f"fxp0.0   10.20.1.20   24",
            'lldp_neighbors':f"ge-0/0/1  --  aa:bb:cc:dd:ee:ff  ge-0/0/1  access-sw",
            'routing_table': f"0.0.0.0/0    S    5/1  10/0  192.168.1.1  ge-0/0/0",
        },
        'cisco_asa': {
            'version':       f"Cisco Adaptive Security Appliance Software Version 9.18.3\nSerial Number: AS{device_name[-4:].upper()}",
            'interfaces':    f"Interface GigabitEthernet0/0 outside, is up, line protocol is up",
            'ip_interfaces': f"outside  10.20.1.30  255.255.255.0  CONFIG",
            'lldp_neighbors':f"System Name: access-sw\nLocal Intf: GigabitEthernet0/1",
            'routing_table': f"S*  0.0.0.0 0.0.0.0 via 192.168.1.1",
        },
        'cisco_ftd': {
            'version':       f"Cisco FTD Version 7.2.5\nSerial Number: FT{device_name[-4:].upper()}",
            'interfaces':    f"Interface GigabitEthernet0/0, is up",
            'ip_interfaces': f"GigabitEthernet0/0  10.20.1.31  255.255.255.0",
            'lldp_neighbors':f"System Name: access-sw\nLocal Intf: GigabitEthernet0/1",
            'routing_table': f"S  0.0.0.0/0 via 192.168.1.1",
        },
    }
    return sim_map.get(yaml_key, {})


def _sim_api_output(yaml_key, device_name):
    """Simulated API response per API-type block."""
    api_map = {
        'aruba_ap_central_api': {
            'serial':     f'CAP{device_name[-4:].upper()}',
            'firmware':   '10.4.0.0',
            'status':     'Up',
            'model':      'AP-515',
            'clients':    12,
            'neighbors':  [{'local_port': 'eth0', 'remote_device': 'access-sw',
                            'remote_port': 'Gi1/0/2'}],
        },
        'juniper_mist_ap': {
            'serial':     f'MST{device_name[-4:].upper()}',
            'firmware':   '0.14.28884',
            'status':     'connected',
            'model':      'AP43',
            'clients':    8,
            'org_id':     'sim-org-id',
        },
        'fortinet_ap_manager_api': {
            'serial':     f'FAP{device_name[-4:].upper()}',
            'status':     'Connected',
            'model':      'FAP-231F',
            'adom':       'root',
        },
        'cisco_fmc_api': {
            'version':    '7.2.5',
            'serial':     f'FMC{device_name[-4:].upper()}',
            'status':     'Registered',
        },
        'aruba_clearpass_api': {
            'version':    '6.11.2',
            'serial':     f'CPS{device_name[-4:].upper()}',
            'role':       'Publisher',
            'endpoints':  42,
        },
    }
    return api_map.get(yaml_key, {'status': 'simulated', 'serial': device_name})


# ── SSH data fetcher ──────────────────────────────────────────────────────────

def _send_command_paginated(conn, cmd_str, more_prompt='-- MORE --',
                             max_iterations=60, read_delay=0.3):
    """
    Manually send a command and read output, handling '-- MORE --'
    pagination by sending Space, without relying on privileged-mode
    paging-disable commands (e.g. 'terminal length 0') that may require
    enable mode not available/configured on all devices.
    Used as a fallback when the normal Netmiko connection (which may
    auto-attempt enable mode) fails.
    """
    import time
    conn.write_channel(cmd_str + '\n')
    time.sleep(1)
    output = ''
    for _ in range(max_iterations):
        chunk = conn.read_channel()
        output += chunk
        if more_prompt in chunk:
            conn.write_channel(' ')
            time.sleep(read_delay)
        elif conn.base_prompt and conn.base_prompt in chunk and more_prompt not in chunk:
            break
        else:
            time.sleep(read_delay)
    lines = output.splitlines()
    cleaned = [l for l in lines if cmd_str not in l and more_prompt not in l]
    if cleaned and conn.base_prompt and cleaned[-1].strip().startswith(conn.base_prompt):
        cleaned = cleaned[:-1]
    return '\n'.join(cleaned)


def ssh_get_data(device_ip, yaml_block, device_name, creds, dry_run):
    """
    Run SSH commands against a device.
    SIMULATED=True: returns fake output per yaml_key.
    SIMULATED=False: real netmiko connection (to be wired).
    Returns dict: {command_name: raw_output_string}
    """
    yaml_key = yaml_block.get('_yaml_key', '')
    commands = yaml_block.get('commands', {})

    if dry_run:
        return {cmd: f'[DRY RUN — would run: {cmd_str}]'
                for cmd, cmd_str in commands.items()}

    # Per-vendor SIMULATED override (module-level SIMULATED_OVERRIDE)
    sim = SIMULATED_OVERRIDE.get(yaml_key, SIMULATED)

    if sim:
        return _sim_output(yaml_key, device_name)

    # ── Real SSH ──────────────────────────────────────────────────────────
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
    device_conn = {
        'device_type': yaml_block['netmiko_device_type'],
        'host':        device_ip,
        'username':    creds.get('user', ''),
        'password':    creds.get('password', ''),
        'secret':      creds.get('enable', ''),
        'timeout':     30,
        'auth_timeout': 30,
    }
    results = {}
    used_manual_pagination = False
    try:
        conn = ConnectHandler(**device_conn)
    except NetmikoAuthenticationException as e:
        raise Exception(f"AUTH_FAILURE: {e}")
    except NetmikoTimeoutException as e:
        raise Exception(f"TIMEOUT: {e}")
    except Exception as connect_err:
        fallback_conn = {k: v for k, v in device_conn.items() if k != 'secret'}
        fallback_conn['auto_connect'] = False
        try:
            conn = ConnectHandler(**fallback_conn)
            conn.establish_connection()
            conn._test_channel_read()
            conn.set_base_prompt()
            used_manual_pagination = True
        except Exception as fallback_err:
            raise Exception(f"SSH_ERROR (primary: {connect_err} | fallback: {fallback_err})")
    try:
        if not used_manual_pagination and yaml_block.get('disable_paging') and yaml_block['disable_paging']:
            conn.send_command(yaml_block['disable_paging'])
        try:
            conn.clear_buffer()
        except Exception:
            pass
        for cmd_name, cmd_str in commands.items():
            try:
                if used_manual_pagination:
                    out = _send_command_paginated(conn, cmd_str)
                else:
                    out = conn.send_command_timing(
                        cmd_str,
                        delay_factor=2,
                        strip_prompt=True,
                        strip_command=True,
                    )
                results[cmd_name] = out
            except Exception as cmd_err:
                results[cmd_name] = f"ERROR: {cmd_err}"
        conn.disconnect()
    except NetmikoAuthenticationException as e:
        raise Exception(f"AUTH_FAILURE: {e}")
    except NetmikoTimeoutException as e:
        raise Exception(f"TIMEOUT: {e}")
    except Exception as e:
        raise Exception(f"SSH_ERROR: {e}")
    return results


def _aruba_central_get_token(creds, tenant_slug=''):
    """
    Exchange refresh_token for access_token.
    Auto-saves new refresh_token back to env file + os.environ.
    """
    import re as _re
    r = requests.post(
        f"{creds['base_url']}/oauth2/token",
        params={
            'client_id':     creds.get('client_id',''),
            'client_secret': creds.get('client_secret',''),
            'grant_type':    'refresh_token',
            'refresh_token': creds.get('refresh_token',''),
        },
        timeout=15
    )
    if not r.ok:
        raise Exception(f"Aruba Central token exchange failed: {r.status_code} {r.text[:100]}")

    token_data   = r.json()
    access_token = token_data.get('access_token', '')
    new_refresh  = token_data.get('refresh_token', '')
    old_refresh  = creds.get('refresh_token', '')

    # Auto-save if refresh_token changed
    if new_refresh and new_refresh != old_refresh and tenant_slug:
        suffix  = tenant_slug.upper().replace('-', '_')
        env_key = f'ARUBA_REFRESH_TOKEN_{suffix}'

        # Update os.environ immediately
        os.environ[env_key] = new_refresh
        creds['refresh_token'] = new_refresh

        # Update env file on disk. Prefer the real persisted location
        # (Django's PLUGINS_CONFIG, honoring NAUTOBOT_DAY2_TENANTS_DIR)
        # over the old bare-metal-era paths, which don't exist in the
        # Docker deployment and would silently swallow this write.
        try:
            from django.conf import settings
            tenants_dir = settings.PLUGINS_CONFIG.get("nautobot_day2", {}).get("tenants_dir")
        except Exception:
            tenants_dir = None
        env_paths = []
        if tenants_dir:
            env_paths.append(f'{tenants_dir}/{tenant_slug}.env')
        env_paths.append(f'{LAB_DIR}/profiles/{tenant_slug}.env')
        for env_path in env_paths:
            if not os.path.exists(env_path):
                continue
            try:
                with open(env_path, 'r') as f:
                    content = f.read()
                pattern = rf'^({_re.escape(env_key)}=).*$'
                if _re.search(pattern, content, _re.MULTILINE):
                    content = _re.sub(pattern, rf'\g<1>{new_refresh}',
                                      content, flags=_re.MULTILINE)
                else:
                    content += f'\n{env_key}={new_refresh}\n'
                with open(env_path, 'w') as f:
                    f.write(content)
                print(f"  ✅ refresh_token auto-saved → {env_path}")
            except PermissionError:
                print(f"  ⚠️  Cannot write {env_path} — run with sudo")
            except Exception as e:
                print(f"  ⚠️  {env_path}: {e}")

    return access_token


def api_get_data(yaml_block, device_name, creds, dry_run):
    """
    Fetch data from cloud API.
    SIMULATED_OVERRIDE controls per-vendor live/sim mode.
    Returns dict of API response data.
    """
    yaml_key = yaml_block.get('_yaml_key', '')
    api_type = yaml_block.get('api_type', '')

    if dry_run:
        return {'_dry_run': True, 'yaml_key': yaml_key}

    sim = SIMULATED_OVERRIDE.get(yaml_key, SIMULATED)
    if sim:
        return _sim_api_output(yaml_key, device_name)

    # ── Juniper Mist API ──────────────────────────────────────────────────
    if api_type == 'juniper_mist':
        token    = creds.get('token', '')
        org_id   = creds.get('org_id', '')
        base_url = creds.get('base_url', 'https://api.mist.com')
        api_h    = {'Authorization': f'Token {token}'}

        # Use inventory for serial/model (most complete)
        inv_r = requests.get(
            f'{base_url}/api/v1/orgs/{org_id}/inventory',
            headers=api_h, timeout=15
        )
        if not inv_r.ok:
            raise Exception(f"Mist inventory error: {inv_r.status_code} {inv_r.text[:100]}")

        inventory = inv_r.json()
        inventory = inventory if isinstance(inventory, list) else inventory.get('results', [])
        device = next((d for d in inventory
                       if d.get('name','').lower() == device_name.lower()), None)
        if not device and inventory:
            device = inventory[0]
        if not device:
            return {'_no_device': True}

        site_id   = device.get('site_id','')
        device_id = device.get('id','')

        # Use stats for IP/uptime/status
        stats_r = requests.get(
            f'{base_url}/api/v1/orgs/{org_id}/stats/devices',
            headers=api_h, timeout=15
        )
        stats = {}
        if stats_r.ok:
            stats_list = stats_r.json()
            stats_list = stats_list if isinstance(stats_list, list) else stats_list.get('results', [])
            stats = next((s for s in stats_list
                          if s.get('_id','') == device.get('mac','')), {})

        result = {
            'name':     device.get('name',''),
            'model':    device.get('model',''),
            'serial':   device.get('serial',''),
            'firmware': device.get('version', stats.get('version','')),
            'status':   'connected' if device.get('connected') else 'disconnected',
            'mac':      device.get('mac',''),
            'ip':       stats.get('ip',''),
            'uptime':   stats.get('uptime', 0),
            'site_id':  site_id,
            'device_id': device_id,
        }

        # LLDP neighbors
        if site_id and device_id:
            r2 = requests.get(
                f'{base_url}/api/v1/sites/{site_id}/devices/{device_id}/lldp_neighbors',
                headers=api_h, timeout=15
            )
            if r2.ok:
                result['lldp_neighbors'] = r2.json()
        return result

    # ── Aruba Central API ─────────────────────────────────────────────────
    if api_type == 'aruba_central':
        base_url = creds.get('base_url', '')
        token    = _aruba_central_get_token(creds)
        api_h    = {'Authorization': f'Bearer {token}'}

        r = requests.get(
            f'{base_url}/monitoring/v2/aps',
            headers=api_h,
            params={'limit': 100},
            timeout=15
        )
        if not r.ok:
            raise Exception(f"Aruba Central API error: {r.status_code} {r.text[:100]}")

        all_aps = r.json().get('aps', [])
        ap = next((a for a in all_aps
                   if a.get('name','').lower() == device_name.lower()), None)
        if not ap and all_aps:
            ap = all_aps[0]
        if not ap:
            return {'_no_device': True}

        result = {
            'name':     ap.get('name',''),
            'model':    ap.get('model',''),
            'serial':   ap.get('serial',''),
            'firmware': ap.get('firmware_version',''),
            'status':   ap.get('status',''),
            'mac':      ap.get('macaddr',''),
            'ip':       ap.get('ip_address',''),
            'clients':  ap.get('client_count', 0),
        }
        serial = ap.get('serial','')
        if serial:
            r2 = requests.get(
                f'{base_url}/monitoring/v1/aps/{serial}/neighbors',
                headers=api_h, timeout=15
            )
            if r2.ok:
                result['lldp_neighbors'] = r2.json()
        return result

    # Unknown API type
    return _sim_api_output(yaml_key, device_name)


def extract_facts(raw_output, yaml_key, device_name):
    """
    Extract device facts from SSH command output or API response.
    API responses have direct fields (serial, model, firmware).
    SSH responses need parsing from version command text.
    """
    facts = {'hostname': device_name}

    # ── API response — direct fields present ─────────────────────────────
    if raw_output.get('serial') or raw_output.get('mac'):
        facts['serial']      = raw_output.get('serial', '')
        facts['firmware']    = raw_output.get('firmware', '')
        facts['model']       = raw_output.get('model', '')
        facts['status']      = raw_output.get('status', '')
        facts['mac']         = raw_output.get('mac', '')
        facts['ip']          = raw_output.get('ip', '')
        facts['uptime']      = raw_output.get('uptime', 0)
        facts['clients']     = raw_output.get('clients', 0)
        facts['down_reason'] = raw_output.get('down_reason', '')
        facts['group']       = raw_output.get('group', '')
        facts['radios']      = raw_output.get('radios', '')
        return facts

    # ── SSH response — parse version command output ───────────────────────
    version_out = raw_output.get('version', '')
    facts['raw_version'] = version_out[:300]

    if yaml_key == 'fortios':
        for line in version_out.splitlines():
            line = line.strip()
            if line.startswith('Version:'):
                facts['firmware'] = line.replace('Version:', '').strip()
            elif line.startswith('Serial-Number:'):
                facts['serial'] = line.replace('Serial-Number:', '').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:', '').strip()

    elif yaml_key in ('aruba_os', 'aruba_aoscx'):
        # Firmware from show version
        for line in version_out.splitlines():
            line = line.strip()
            if line and not line.startswith('/') and not line.startswith('Boot')                     and not line.startswith('Image') and len(line) < 20                     and '.' in line and line[0].isalpha():
                facts['firmware'] = line  # e.g. KB.16.11.0013

        # Serial from show system information
        sysinfo = raw_output.get('system_info', '')
        for line in sysinfo.splitlines():
            line = line.strip()
            if 'Serial Number' in line and ':' in line:
                serial = line.split(':')[-1].strip()
                if serial:
                    facts['serial'] = serial
                    break  # use first member serial

        # Hostname from system info
        for line in sysinfo.splitlines():
            if 'System Name' in line and ':' in line:
                facts['hostname'] = line.split(':')[-1].strip()
                break

    elif yaml_key in ('juniper_junos', 'juniper_srx'):
        # Parse show version
        for line in version_out.splitlines():
            line = line.strip()
            if line.startswith('Junos:'):
                facts['firmware'] = line.replace('Junos:', '').strip()
            elif line.startswith('Hostname:'):
                facts['hostname'] = line.replace('Hostname:', '').strip()
            elif line.startswith('Model:'):
                facts['model'] = line.replace('Model:', '').strip()

        # Serial from show chassis hardware
        # Line format: "Chassis   <blank>  <blank>  FE1824AX0446  EX4100-F-48P"
        chassis_out = raw_output.get('chassis_hardware', '')
        for line in chassis_out.splitlines():
            if line.strip().startswith('Chassis'):
                parts = line.split()
                # Serial is 4th column (index 3) when present
                if len(parts) >= 2:
                    serial = parts[1]
                    if len(serial) > 5 and serial != 'BUILTIN':
                        facts['serial'] = serial
                        break

    elif yaml_key in ('cisco_ios', 'cisco_iosxe', 'cisco_nxos'):
        for line in version_out.splitlines():
            line = line.strip()
            if 'Version' in line and 'Cisco' in line:
                facts['firmware'] = line.strip()
            elif 'Processor board ID' in line:
                facts['serial'] = line.split('ID')[-1].strip()

    else:
        for line in version_out.splitlines():
            line_lower = line.lower()
            if 'serial' in line_lower and ':' in line:
                facts['serial'] = line.split(':')[-1].strip()
            if 'version' in line_lower and ':' in line:
                facts['firmware'] = line.split(':')[-1].strip()

    return facts


def extract_interfaces(raw_output, yaml_key):
    """
    Parse vendor-specific interface output into structured list.
    Returns list of dicts: {name, type, status, description, ip}
    Skip internal/loopback/management-only interfaces.
    """
    interfaces = []

    # ── FortiGate ─────────────────────────────────────────────────────────
    if yaml_key == 'fortios':
        intf_out = raw_output.get('interfaces', '')
        # Each interface block: "== [ portX ]\nname: portX   ip: ...   status: up   type: physical"
        current = {}
        for line in intf_out.splitlines():
            line = line.strip()
            if line.startswith('== ['):
                if current.get('name'):
                    interfaces.append(current)
                current = {}
            elif line.startswith('name:'):
                # Parse key=value pairs from the line
                for part in line.split('   '):
                    part = part.strip()
                    if ':' in part:
                        k, _, v = part.partition(':')
                        current[k.strip()] = v.strip()
        if current.get('name'):
            interfaces.append(current)

        result = []
        skip_types = {'tunnel', 'loopback', 'vdom-link', 'aggregate'}
        for intf in interfaces:
            name   = intf.get('name','')
            status = intf.get('status','')
            ip     = intf.get('ip','')
            itype  = intf.get('type','physical')
            if itype in skip_types:
                continue
            if name in ('ha', 'ssl.root'):
                continue
            # Map to Nautobot interface type
            nb_type = '1000base-t'
            if name.startswith('x'):
                nb_type = '10gbase-x-sfpp'
            elif 'aggregate' in intf.get('aggregate','') or itype == 'aggregate':
                nb_type = 'lag'
            result.append({
                'name':    name,
                'type':    nb_type,
                'enabled': status == 'up',
                'ip':      ip if ip and ip != '0.0.0.0 0.0.0.0' else '',
                'description': intf.get('alias',''),
            })
        return result

    # ── Aruba AOS ─────────────────────────────────────────────────────────
    elif yaml_key in ('aruba_os', 'aruba_aoscx'):
        intf_out = raw_output.get('interfaces', '')
        result   = []
        import re
        for line in intf_out.splitlines():
            # Real port lines have | separator and port name like "1/1", "1/1-Trk1", "2/8*"
            if '|' not in line:
                continue
            left, _, right = line.partition('|')
            left_parts  = left.split()
            right_parts = right.split()
            if not left_parts:
                continue
            port = left_parts[0].rstrip('*')
            # Must match Aruba port format: digit/digit or digit/digit-TrkN
            if not re.match(r'^\d+(/\d+)?(-Trk\d+)?$', port):
                continue
            # Port type from second column
            port_type_raw = left_parts[1] if len(left_parts) > 1 else ''
            # Status: right side columns are: Alert Enabled Status Mode
            # "No  Yes  Up  1000FDx" or "No  Yes  Down  ."
            status = 'down'
            if len(right_parts) >= 3:
                status = 'up' if right_parts[2].lower() == 'up' else 'down'
            # Map to Nautobot interface type
            nb_type = '1000base-t'
            if '1000SX' in port_type_raw or '1000LX' in port_type_raw:
                nb_type = '1000base-x-sfp'
            elif 'SFP+SR' in port_type_raw or '10GigFD' in port_type_raw or 'SFP+' in port_type_raw:
                nb_type = '10gbase-x-sfpp'
            result.append({
                'name':    port,
                'type':    nb_type,
                'enabled': status == 'up',
                'ip':      '',
                'description': '',
            })
        # Add SVI/IP interfaces from show ip interface brief
        ip_out = raw_output.get('ip_interfaces', '')
        for line in ip_out.splitlines():
            parts = line.split()
            # SVI lines: "VLAN1  172.33.1.2/24  ..."
            if len(parts) >= 2 and parts[0].upper().startswith('VLAN') and '/' in parts[1]:
                result.append({
                    'name':    parts[0],
                    'type':    'virtual',
                    'enabled': True,
                    'ip':      parts[1],
                    'description': 'SVI',
                })
        return result

    # ── Juniper ────────────────────────────────────────────────────────────
    elif yaml_key in ('juniper_junos', 'juniper_srx'):
        intf_out = raw_output.get('interfaces', '')
        result   = []
        # Physical interfaces only — skip subinterfaces (contain dot)
        # Skip internal: pfe-, pfh-, bme, cbp, dsc, esi, fti, gre, ipip, lsi, mtun, pimd, pime, pip, tap, vtep
        skip_prefixes = ('pfe-','pfh-','bme','cbp','dsc','esi','fti','gre','ipip',
                         'lsi','mtun','pimd','pime','pip','tap','vtep','jsrv','lo0')
        seen = set()
        for line in intf_out.splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            # Skip subinterfaces (ge-0/0/0.0)
            if '.' in name:
                continue
            # Skip internal
            if any(name.startswith(p) for p in skip_prefixes):
                continue
            if name in seen:
                continue
            seen.add(name)
            if len(parts) < 3:
                continue
            admin  = parts[1]
            link   = parts[2]
            status = 'up' if link == 'up' else 'down'
            # Map type
            nb_type = '1000base-t'
            if name.startswith('xe-') or name.startswith('et-'):
                nb_type = '10gbase-x-sfpp'
            elif name.startswith('ae'):
                nb_type = 'lag'
            elif name.startswith('irb'):
                nb_type = 'virtual'
            elif name.startswith('vme'):
                continue  # skip
            result.append({
                'name':    name,
                'type':    nb_type,
                'enabled': status == 'up',
                'ip':      '',
                'description': '',
            })
        # Get IPs from irb interfaces
        for line in intf_out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == 'irb.0':
                # Next parts may have inet IP
                pass
            if len(parts) >= 3 and parts[0] == 'inet' and '/' in parts[1]:
                # IP address line following irb
                pass
        return result

    return []


def extract_lldp(raw_output, yaml_key):
    """
    Parse LLDP neighbor output into structured list.
    Returns list of dicts:
      {local_port, remote_system, remote_port, remote_ip, remote_chassis}
    Only returns entries with a real port name (ge-x/x/x, 1/x, portX etc.)
    """
    neighbors = []

    # ── Juniper: tabular one-line-per-neighbor ────────────────────────────
    # Header: "Local Interface  Parent Interface  Chassis Id  Port info  System Name"
    # Data  : "ge-0/0/2  -  38:c0:ea:8b:05:c0  eth0  Day2-FortiAP"
    if yaml_key in ('juniper_junos', 'juniper_srx'):
        lldp_out = raw_output.get('lldp_neighbors', '')
        for line in lldp_out.splitlines():
            line = line.strip()
            if not line or line.startswith('Local') or line.startswith('-'):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            local_port = parts[0]
            # Must be a real interface
            if not (local_port.startswith('ge-') or local_port.startswith('xe-') or
                    local_port.startswith('et-') or local_port.startswith('ae')):
                continue
            # parts: [local, parent(-), chassis_id, port_info, system_name...]
            if parts[1] == '-':
                chassis_id  = parts[2]
                remote_port = parts[3]
                remote_sys  = ' '.join(parts[4:]) if len(parts) > 4 else ''
            else:
                chassis_id  = parts[1]
                remote_port = parts[2]
                remote_sys  = ' '.join(parts[3:]) if len(parts) > 3 else ''
            neighbors.append({
                'local_port':    local_port,
                'remote_system': remote_sys.strip(),
                'remote_port':   remote_port,
                'remote_ip':     '',
                'remote_chassis': chassis_id,
            })

    # ── Aruba: multi-line block format ────────────────────────────────────
    # "  Local Port   : 1/1"
    # "  SysName      : DC-Access"
    # "  PortDescr    : 1/23"
    # "  Address      : 172.33.1.3"
    # "---..." separator between blocks
    elif yaml_key in ('aruba_os', 'aruba_aoscx'):
        lldp_out = raw_output.get('lldp_neighbors', '')
        current  = {}
        for line in lldp_out.splitlines():
            line_stripped = line.strip()
            # New block starts with "Local Port"
            if 'Local Port' in line and ':' in line:
                if current.get('local_port') and current.get('remote_system'):
                    neighbors.append(current)
                port_val = line.split(':', 1)[-1].strip()
                current  = {
                    'local_port':     port_val,
                    'remote_system':  '',
                    'remote_port':    '',
                    'remote_ip':      '',
                    'remote_chassis': '',
                }
            elif 'SysName' in line and ':' in line and current:
                current['remote_system'] = line.split(':', 1)[-1].strip()
            elif 'PortDescr' in line and ':' in line and current:
                current['remote_port'] = line.split(':', 1)[-1].strip()
            elif 'Address' in line and ':' in line and current:
                # Only grab IPv4 addresses
                val = line.split(':', 1)[-1].strip()
                if val and val[0].isdigit():
                    current['remote_ip'] = val
            elif 'ChassisId' in line and ':' in line and current:
                current['remote_chassis'] = line.split(':', 1)[-1].strip()
        # Add last block
        if current.get('local_port') and current.get('remote_system'):
            neighbors.append(current)

    # ── FortiGate: diagnose lldprx neighbor summary ───────────────────────
    # Format (when LLDP enabled):
    # "Interface  Chassis-id   Port-id    System-name   Capabilities  TTL"
    # "port2      b4:b2:e9:..  17         DC-Core       B             120"
    elif yaml_key == 'fortios':
        lldp_out = raw_output.get('lldp_neighbors', '')
        for line in lldp_out.splitlines():
            line = line.strip()
            if not line or 'Interface' in line or 'error' in line.lower():
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            local_port = parts[0]
            # Must start with port/x/ha/mgmt
            if not (local_port.startswith('port') or local_port.startswith('x') or
                    local_port in ('ha','mgmt')):
                continue
            chassis_id  = parts[1] if len(parts) > 1 else ''
            remote_port = parts[2] if len(parts) > 2 else ''
            remote_sys  = parts[3] if len(parts) > 3 else ''
            neighbors.append({
                'local_port':    local_port,
                'remote_system': remote_sys,
                'remote_port':   remote_port,
                'remote_ip':     '',
                'remote_chassis': chassis_id,
            })

    return neighbors


def extract_neighbors(raw_output, yaml_key):
    """Extract LLDP/CDP neighbors from raw output."""
    lldp_out = raw_output.get('lldp_neighbors', '')
    neighbors = []
    for line in lldp_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            neighbors.append({
                'local_port':    parts[0],
                'remote_device': parts[-1] if len(parts) > 1 else '',
                'remote_port':   parts[2] if len(parts) > 2 else '',
            })
    return neighbors


# ── Nautobot writer ───────────────────────────────────────────────────────────
def api_get_all(endpoint, params=None):
    return client.get_all(endpoint, params=params)

def api_post(endpoint, data):
    return client.post(endpoint, data)

def api_patch(endpoint, obj_id, data):
    return client.patch(f'{endpoint}/{obj_id}', data)

_status_cache = {}
def get_active_status_id():
    if 'active' not in _status_cache:
        statuses = api_get_all('extras/statuses')
        _status_cache['active'] = next(
            (s['id'] for s in statuses if s['name'] == 'Active'), None)
    return _status_cache['active']

def write_interfaces(device_id, interfaces, dry_run):
    if dry_run: return len(interfaces)
    written, active_id = 0, get_active_status_id()
    for intf in interfaces:
        name = intf['name']
        r = client.get('dcim/interfaces', params={'device_id': device_id, 'name': name, 'limit': 5})
        existing = next((o for o in r.json().get('results', []) if o['name'] == name), None)
        payload = {
            'device':      {'id': device_id},
            'name':        name,
            'type':        intf.get('type', '1000base-t'),
            'status':      {'id': active_id},
            'enabled':     intf.get('enabled', True),
            'description': intf.get('description', ''),
        }
        if name == 'mgmt0':  # skip — already created by onboard
            continue
        r2 = api_patch('dcim/interfaces', existing['id'], payload) if existing \
             else api_post('dcim/interfaces', payload)
        if r2.status_code in (200, 201):
            written += 1
    return written

def _assign_ip_to_interface(ip_addr, intf_id, device_id):
    """Create IP address and link it to interface if not already existing."""
    # Check if IP already exists
    r = client.get('ipam/ip-addresses', params={'address': ip_addr, 'limit': 1})
    if r.ok and r.json().get('count', 0) > 0:
        ip_id = r.json()['results'][0]['id']
    else:
        # Create IP
        statuses = client.get('extras/statuses', params={'limit': 200})
        active_id = next((s['id'] for s in statuses.json().get('results',[])
                          if s['name'] == 'Active'), None) if statuses.ok else None
        r2 = api_post('ipam/ip-addresses', {
            'address': ip_addr,
            'status':  {'id': active_id} if active_id else None,
        })
        if not r2 or not r2.ok:
            return
        ip_id = r2.json().get('id')

    if not ip_id:
        return

    # Link to interface
    api_post('ipam/ip-address-to-interface', {
        'ip_address': {'id': ip_id},
        'interface':  {'id': intf_id},
    })


def _find_interface(device_id, port_name):
    """
    Find interface ID by name with fallback normalization:
    - Exact match: "1/1-Trk1", "ge-0/0/1", "port2"
    - Strip trunk suffix: "1/1-Trk1" → "1/1"
    - Strip member prefix: "1/23" → "23" (for 2530 single-member)
    - Prefix search: find any interface starting with port_name
    """
    def lookup(name):
        r = client.get('dcim/interfaces', params={'device_id': device_id, 'name': name, 'limit': 1})
        if r.ok and r.json().get('count', 0) > 0:
            return r.json()['results'][0]['id']
        return None

    # 1. Exact match
    intf_id = lookup(port_name)
    if intf_id:
        return intf_id

    # 2. Strip trunk suffix: "1/1-Trk1" → "1/1"
    if '-' in port_name:
        intf_id = lookup(port_name.split('-')[0])
        if intf_id:
            return intf_id

    # 3. Strip member prefix: "1/23" → "23" (Aruba 2530 uses single numbers)
    if '/' in port_name:
        last_part = port_name.split('/')[-1]
        intf_id = lookup(last_part)
        if intf_id:
            return intf_id

    # 4. Prefix search — find interface starting with port_name
    r = client.get('dcim/interfaces', params={'device_id': device_id, 'name__isw': port_name, 'limit': 1})
    if r.ok and r.json().get('count', 0) > 0:
        return r.json()['results'][0]['id']

    return None


def write_cables(device_id, device_name, lldp_neighbors, dry_run):
    """
    Create cables in Nautobot from LLDP neighbor data.
    3-level matching:
      1. lldp_hostname custom field (most reliable)
      2. remote_ip vs primary_ip4
      3. Nautobot device name (partial match)
    Returns count of cables created.
    """
    if dry_run or not lldp_neighbors:
        return 0

    # Build device lookup maps
    r = client.get('dcim/devices', params={'limit': 500})
    all_devs = r.json().get('results', []) if r.ok else []

    # Map 1: lldp_hostname → device_id
    by_lldp_hostname = {}
    for d in all_devs:
        cf = d.get('custom_fields', {}) or {}
        lldp_h = cf.get('lldp_hostname', '')
        if lldp_h:
            by_lldp_hostname[lldp_h.lower()] = d['id']

    # Map 2: primary IP → device_id
    by_ip = {}
    for d in all_devs:
        ip_obj  = d.get('primary_ip4') or {}
        ip_url  = ip_obj.get('url', '')
        if ip_url:
            r_ip = client.get_absolute(ip_url)
            if r_ip.ok:
                addr = r_ip.json().get('address', '').split('/')[0]
                if addr:
                    by_ip[addr] = d['id']

    # Map 3: device name → device_id
    by_name = {d['name'].lower(): d['id'] for d in all_devs}

    # Get existing cables to avoid duplicates
    r2 = client.get('dcim/cables', params={'limit': 500})
    existing_cables = set()
    for cable in (r2.json().get('results', []) if r2.ok else []):
        for term in cable.get('a_terminations', []) + cable.get('b_terminations', []):
            existing_cables.add(term.get('object_id', ''))

    # Get Connected status
    statuses = client.get('extras/statuses', params={'limit': 200})
    connected_id = next((s['id'] for s in statuses.json().get('results', [])
                         if s['name'] == 'Connected'), None) if statuses.ok else None

    created = 0
    for nbr in lldp_neighbors:
        local_port  = nbr.get('local_port', '').strip()
        remote_sys  = nbr.get('remote_system', '').strip()
        remote_port = nbr.get('remote_port', '').strip()
        remote_ip   = nbr.get('remote_ip', '').strip()

        if not local_port:
            continue

        # ── Find remote device — 3-level match ───────────────────────────
        remote_dev_id = None

        # Level 1: lldp_hostname custom field
        if remote_sys:
            remote_dev_id = by_lldp_hostname.get(remote_sys.lower())

        # Level 2: primary IP
        if not remote_dev_id and remote_ip:
            remote_dev_id = by_ip.get(remote_ip)

        # Level 3: device name partial match
        if not remote_dev_id and remote_sys:
            remote_dev_id = by_name.get(remote_sys.lower())
            if not remote_dev_id:
                for dev_name, dev_id in by_name.items():
                    if remote_sys.lower() in dev_name or dev_name in remote_sys.lower():
                        remote_dev_id = dev_id
                        break

        if not remote_dev_id:
            continue

        # ── Get local interface — try exact then normalized ──────────────
        local_intf_id = _find_interface(device_id, local_port)
        if not local_intf_id or local_intf_id in existing_cables:
            continue

        # ── Get remote interface — try exact then normalized ──────────────
        remote_intf_id = None
        if remote_port:
            remote_intf_id = _find_interface(remote_dev_id, remote_port)
        if not remote_intf_id or remote_intf_id in existing_cables:
            continue

        # ── Create cable — Nautobot 3.1.x API format ─────────────────────
        cable_payload = {
            'termination_a_type': 'dcim.interface',
            'termination_a_id':   local_intf_id,
            'termination_b_type': 'dcim.interface',
            'termination_b_id':   remote_intf_id,
        }
        if connected_id:
            cable_payload['status'] = {'id': connected_id}

        r5 = api_post('dcim/cables', cable_payload)
        if r5 and r5.ok:
            created += 1
            existing_cables.add(local_intf_id)
            existing_cables.add(remote_intf_id)

    return created


def write_facts(device_id, facts, dry_run):
    if dry_run: return 1

    updates = {
        'custom_fields': {
            'last_network_data_sync': datetime.now().date().isoformat()
        }
    }

    # Serial
    serial = facts.get('serial', '')
    if serial and not serial.startswith('SIM-'):
        updates['serial'] = serial

    # Store lldp_hostname for cable topology matching
    hostname = facts.get('hostname', '')
    if hostname and hostname != device_id:
        updates.setdefault('custom_fields', {})['lldp_hostname'] = hostname

    # Comments — store firmware + status + AP-specific fields
    comments_parts = []
    if facts.get('firmware'):
        comments_parts.append(f"Firmware: {facts['firmware']}")
    if facts.get('status'):
        comments_parts.append(f"Status: {facts['status']}")
    if facts.get('mac'):
        comments_parts.append(f"MAC: {facts['mac']}")
    if facts.get('ip'):
        comments_parts.append(f"Mgmt IP: {facts['ip']}")
    if facts.get('uptime'):
        hours = int(facts['uptime']) // 3600
        comments_parts.append(f"Uptime: {hours}h")
    if facts.get('clients') is not None and facts['clients'] != 0:
        comments_parts.append(f"Clients: {facts['clients']}")
    if facts.get('down_reason'):
        comments_parts.append(f"Down reason: {facts['down_reason']}")
    if facts.get('group'):
        comments_parts.append(f"Group: {facts['group']}")
    if facts.get('radios'):
        comments_parts.append(f"Radios: {facts['radios']}")
    if comments_parts:
        updates['comments'] = ' | '.join(comments_parts)

    r = api_patch('dcim/devices', device_id, updates)
    return 1 if r.ok else 0


# ── Cache + device fetcher ────────────────────────────────────────────────────
_C = {}

def init_cache():
    print('  Loading Nautobot cache...')
    sgs       = api_get_all('extras/secrets-groups')
    roles     = api_get_all('extras/roles')
    platforms = api_get_all('dcim/platforms')
    statuses  = api_get_all('extras/statuses')
    _C['sg_id_to_name']    = {sg['id']: sg['name'] for sg in sgs}
    _C['role_id_to_name']  = {r['id']: r['name'] for r in roles}
    _C['plat_id_to_slug']  = {}
    for p in platforms:
        ns = p.get('natural_slug', '')
        slug = ns.rsplit('_', 1)[0] if '_' in ns and len(ns.rsplit('_',1)[-1])==4 else ns
        _C['plat_id_to_slug'][p['id']] = slug
    _C['status_active'] = next((s['id'] for s in statuses if s['name']=='Active'), None)
    print(f'  Secrets groups: {len(_C["sg_id_to_name"])} | '
          f'Roles: {len(_C["role_id_to_name"])} | '
          f'Platforms: {len(_C["plat_id_to_slug"])}')

def natural_to_slug(ns):
    if not ns: return ''
    parts = ns.rsplit('_', 1)
    return parts[0] if len(parts)==2 and len(parts[1])==4 else ns

def get_devices(site_name, tenant_slug, category, failed_only, last_failed):
    r = client.get('dcim/locations', params={'name': site_name, 'limit': 10})
    site_id = next((l['id'] for l in r.json().get('results',[])
                    if l['name']==site_name), None)
    if not site_id:
        print(f'  ERROR: Site "{site_name}" not found'); return []

    tenants = api_get_all('tenancy/tenants')
    tenant_id = next((t['id'] for t in tenants
                      if natural_to_slug(t['natural_slug'])==tenant_slug), None)
    if not tenant_id:
        print(f'  ERROR: Tenant "{tenant_slug}" not found'); return []

    devices = api_get_all('dcim/devices', params={
        'location': site_id, 'tenant_id': tenant_id})

    enriched = []
    for d in devices:
        sg_id   = (d.get('secrets_group') or {}).get('id')
        role_id = (d.get('role') or {}).get('id')
        plat_id = (d.get('platform') or {}).get('id')
        d['_sg_name']     = _C['sg_id_to_name'].get(sg_id, '') if sg_id else ''
        d['_role_name']   = _C['role_id_to_name'].get(role_id, '') if role_id else ''
        d['_plat_slug']   = _C['plat_id_to_slug'].get(plat_id, '') if plat_id else ''
        d['_tenant_slug'] = tenant_slug

        # Category filter
        if category != 'all':
            allowed = CATEGORY_MAP.get(category, [])
            if allowed and not any(d['_sg_name'].startswith(p) for p in allowed):
                continue

        if failed_only and d['name'] not in last_failed:
            continue

        enriched.append(d)
    return enriched


# ── Main sync loop ────────────────────────────────────────────────────────────
def sync_device(device, dry_run):
    name        = device['name']
    sg_name     = device['_sg_name']
    role_name   = device['_role_name']
    plat_slug   = device['_plat_slug']
    tenant_slug = device['_tenant_slug']

    # Get device IP
    ip_obj  = device.get('primary_ip4') or {}
    ip_url  = ip_obj.get('url', '')
    device_ip = ''
    if ip_url:
        r = client.get_absolute(ip_url)
        if r.ok:
            device_ip = r.json().get('address', '').split('/')[0]

    # Resolve YAML block
    section, yaml_key = resolve_vendor(plat_slug, role_name, sg_name)
    result = SyncResult(name, yaml_key or 'unknown')

    if not section or not yaml_key:
        result.skip(f'No YAML block for platform={plat_slug} role={role_name} sg={sg_name}')
        return result

    yaml_block = get_yaml_block(section, yaml_key)
    if not yaml_block:
        result.skip(f'YAML block {section}/{yaml_key} not found in vendor_commands.yaml')
        return result

    # Inject yaml_key for sim output lookup
    yaml_block = dict(yaml_block)
    yaml_block['_yaml_key'] = yaml_key

    data_source = yaml_block.get('data_source', 'ssh')
    creds       = resolve_creds(sg_name, tenant_slug)

    # Credential check
    if not SIMULATED and not dry_run:
        missing = [v for k, v in creds.get('_var_map', {}).items()
                   if not creds.get(k)]
        if missing:
            _tenants_dir = os.environ.get("NAUTOBOT_DAY2_TENANTS_DIR", f'{LAB_DIR}/profiles')
            result.fail(ERR_AUTH,
                        f'Missing env vars: {missing}',
                        f'Fill {missing} in {_tenants_dir}/{tenant_slug}.env')
            return result

    # Fetch data
    if data_source == 'ssh':
        try:
            raw = ssh_get_data(device_ip, yaml_block, name, creds, dry_run)
        except Exception as _ssh_err:
            _e = str(_ssh_err)
            if "AUTH_FAILURE" in _e:
                result.fail(ERR_AUTH, _e[:200], ERROR_FIXES[ERR_AUTH])
            elif "TIMEOUT" in _e:
                result.fail(ERR_TIMEOUT, _e[:200], ERROR_FIXES[ERR_TIMEOUT])
            else:
                result.fail(ERR_UNKNOWN, _e[:200], ERROR_FIXES[ERR_UNKNOWN])
            return result
    else:
        try:
            raw = api_get_data(yaml_block, name, creds, dry_run)
        except Exception as _api_err:
            result.fail(ERR_UNKNOWN, str(_api_err)[:200], ERROR_FIXES[ERR_UNKNOWN])
            return result

    if not raw:
        result.fail(ERR_UNKNOWN, 'No data returned from device', ERROR_FIXES[ERR_UNKNOWN])
        return result

    # Extract structured data
    facts      = extract_facts(raw, yaml_key, name)
    interfaces = extract_interfaces(raw, yaml_key)
    neighbors  = extract_lldp(raw, yaml_key)

    result.facts      = facts
    result.interfaces = interfaces
    result.neighbors  = neighbors

    # Write to Nautobot
    dev_id = device['id']
    result.writes['interfaces'] = write_interfaces(dev_id, interfaces, dry_run)
    result.writes['cables'] = write_cables(dev_id, device['name'], neighbors, dry_run)
    result.writes['facts']      = write_facts(dev_id, facts, dry_run)
    result.success()
    return result


# ── Reporter ──────────────────────────────────────────────────────────────────
def print_results(results, dry_run):
    print(f"\n{'='*70}")
    print(f"  {'SYNC RESULTS (DRY RUN)' if dry_run else 'SYNC RESULTS'}")
    print(f"{'='*70}\n")

    rows = []
    for r in results:
        icon = {'success':'✅','failed':'❌','skipped':'⚠️ '}.get(r.status,'?')
        sim  = ' [sim]' if r.simulated and r.status=='success' else ''
        rows.append([
            r.device_name,
            r.yaml_key or '—',
            f'{icon} {r.status}{sim}',
            f"i:{r.writes.get('interfaces',0)} f:{r.writes.get('facts',0)} c:{r.writes.get('cables',0)}",
            r.error_msg[:50] if r.error_msg else '',
        ])

    print(tabulate(rows, headers=['Device','YAML Block','Status','Writes','Error'],
                   tablefmt='simple'))

    # Handler summary
    by_block = {}
    for r in results:
        k = r.yaml_key or 'unknown'
        by_block.setdefault(k, {'ok':0,'fail':0,'skip':0})
        by_block[k][{'success':'ok','failed':'fail','skipped':'skip'}.get(r.status,'skip')] += 1

    print(f"\n  YAML block summary:")
    for block, stats in by_block.items():
        print(f"    {block:35} ✅ {stats['ok']:2}  ❌ {stats['fail']:2}  ⚠️  {stats['skip']:2}")

    failed = [r for r in results if r.status=='failed']
    if failed:
        print(f"\n  ❌ {len(failed)} devices need attention:\n")
        for r in failed:
            print(f"    {r.device_name}")
            print(f"      YAML block : {r.yaml_key}")
            print(f"      Error      : {r.error_type} — {r.error_msg}")
            fix = r.fix or ERROR_FIXES.get(r.error_type, '')
            if fix: print(f"      Fix        : {fix}")
            print()

    total   = len(results)
    success = sum(1 for r in results if r.status=='success')
    fail    = sum(1 for r in results if r.status=='failed')
    skip    = sum(1 for r in results if r.status=='skipped')
    print(f"\n  {'='*70}")
    print(f"  Total: {total}  ✅ {success}  ❌ {fail}  ⚠️  {skip}")
    print(f"  {'='*70}\n")
    return fail > 0


# ── Manifest ──────────────────────────────────────────────────────────────────
def load_last_failed(tenant, site):
    path = os.path.join(MANIFESTS_DIR, f'sync_last_{tenant}_{site}.json')
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f).get('failed_devices', []))
    return set()

def save_manifest(tenant, site, results):
    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    failed = [r.device_name for r in results if r.status=='failed']
    manifest = {
        'phase':          'sync',
        'timestamp':      datetime.now().isoformat(),
        'tenant':         tenant,
        'site':           site,
        'yaml_path':      VENDOR_COMMANDS_PATH,
        'simulated':      SIMULATED,
        'total':          len(results),
        'success':        sum(1 for r in results if r.status=='success'),
        'failed':         len(failed),
        'skipped':        sum(1 for r in results if r.status=='skipped'),
        'failed_devices': failed,
    }
    path = os.path.join(MANIFESTS_DIR, f'sync_last_{tenant}_{site}.json')
    with open(path, 'w') as f: json.dump(manifest, f, indent=2)
    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    hist = os.path.join(MANIFESTS_DIR, f'sync_{tenant}_{site}_{ts}.json')
    with open(hist, 'w') as f: json.dump(manifest, f, indent=2)
    return path


# ── Main ──────────────────────────────────────────────────────────────────────
def get_devices_for_site(site_name, tenant_slug, category='all'):
    """
    Public wrapper for Job classes to get device list.
    Initialises cache and returns device dicts ready for sync_device().
    """
    init_cache()
    return get_devices(site_name, tenant_slug, category, False, set())


def main():
    parser = argparse.ArgumentParser(description='Sync network data into Nautobot (YAML-driven)')
    parser.add_argument('--site',        required=True)
    parser.add_argument('--tenant',      required=True)
    parser.add_argument('--category',    default='all',
                        choices=['all','switches','firewalls','aps','nac'])
    parser.add_argument('--failed-only', action='store_true')
    parser.add_argument('--dry-run',     action='store_true')
    args = parser.parse_args()

    mode = 'DRY RUN' if args.dry_run else ('SIMULATED' if SIMULATED else 'LIVE')
    print(f"\n{'='*70}")
    print(f"  sync_network_data.py  [{mode}]")
    print(f"  Site       : {args.site}")
    print(f"  Tenant     : {args.tenant}")
    print(f"  Category   : {args.category}")
    print(f"  YAML       : {VENDOR_COMMANDS_PATH}")
    print(f"  Target     : {URL}")
    print(f"{'='*70}\n")

    # Validate YAML loads
    try:
        cmds = load_vendor_commands()
        total_blocks = sum(len(v) for v in cmds.values())
        print(f"  YAML loaded: {total_blocks} vendor blocks "
              f"({', '.join(f'{s}:{len(v)}' for s,v in cmds.items())})\n")
    except Exception as e:
        print(f"  ERROR loading YAML: {e}"); return

    init_cache()

    last_failed = load_last_failed(args.tenant, args.site) if args.failed_only else set()
    if args.failed_only:
        print(f"  Failed-only: {len(last_failed)} devices from last run\n")

    devices = get_devices(args.site, args.tenant, args.category,
                          args.failed_only, last_failed)
    if not devices:
        print(f"  No devices found for site='{args.site}' tenant='{args.tenant}'")
        return

    print(f"  Found {len(devices)} devices\n")

    results = []
    for device in devices:
        name     = device['name']
        sg       = device['_sg_name']
        role     = device['_role_name']
        plat     = device['_plat_slug']
        section, yaml_key = resolve_vendor(plat, role, sg)
        print(f"  → {name:20} plat:{plat:15} role:{role:15} → {section or 'NONE'}/{yaml_key or 'NONE'}")
        result = sync_device(device, args.dry_run)
        icon = {'success':'✅','failed':'❌','skipped':'⚠️ '}.get(result.status,'?')
        sim  = ' (sim)' if result.simulated else ''
        print(f"     {icon} {result.status}{sim} — "
              f"interfaces:{result.writes['interfaces']} "
              f"facts:{result.writes['facts']}")
        if result.error_msg:
            print(f"     ⚡ {result.error_type or ''}: {result.error_msg[:80]}")
        results.append(result)

    has_failures = print_results(results, args.dry_run)

    if not args.dry_run:
        path = save_manifest(args.tenant, args.site, results)
        print(f"  Manifest → {path}\n")

    import sys; sys.exit(1 if has_failures else 0)


if __name__ == '__main__':
    main()
