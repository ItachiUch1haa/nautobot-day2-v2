"""
nautobot_prepare.py
Phase 4 — Validates and enriches engineer CSV.
Reads a simple engineer CSV + site config JSON.
Outputs nautobot_ready_<site>.csv for nautobot_onboard_v2.py.

Usage:
    python3 nautobot_prepare.py \
        --csv tests/engineer_acme_blr02.csv \
        --site-config tests/site_acme_blr02.json \
        --dry-run

    python3 nautobot_prepare.py \
        --csv tests/engineer_acme_blr02.csv \
        --site-config tests/site_acme_blr02.json
"""

import sys
import os
import csv
import json
import argparse
import ipaddress
import re
from datetime import datetime
from tabulate import tabulate

LAB_DIR       = os.path.dirname(os.path.abspath(__file__))
MANIFESTS_DIR = os.path.join(LAB_DIR, 'manifests')

sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.dirname(LAB_DIR))
from client import NautobotClient

client = NautobotClient(env_file=os.path.join(LAB_DIR, '.env'))
URL = client.url

from vendor_matrix import (
    VENDOR_MATRIX,
    get_enabled_vendors,
    get_device_types_for_vendor,
    get_access_methods,
    get_default_platform,
    get_platforms_for_combo,
    get_secrets_group_prefix,
)


# ── Alias normalization tables (P5 fix) ───────────────────────────────────────

VENDOR_ALIASES = {
    # lowercase normalizations
    'aruba':    'aruba',
    'arubaos':  'aruba',
    'aruba/hpe':'aruba',
    'hpe':      'aruba',
    'juniper':  'juniper',
    'junos':    'juniper',
    'cisco':    'cisco',
    'fortinet': 'fortinet',
    'forti':    'fortinet',
}

ROLE_ALIASES = {
    'access-switch':      'access-switch',
    'access_switch':      'access-switch',
    'accessswitch':       'access-switch',
    'access switch':      'access-switch',
    'acc-sw':             'access-switch',
    'core-switch':        'core-switch',
    'core_switch':        'core-switch',
    'coreswitch':         'core-switch',
    'core switch':        'core-switch',
    'core-sw':            'core-switch',
    'ap':                 'ap',
    'access-point':       'ap',
    'accesspoint':        'ap',
    'access point':       'ap',
    'wireless':           'ap',
    'branch-fw':          'branch-fw',
    'branch_fw':          'branch-fw',
    'firewall':           'branch-fw',
    'fw':                 'branch-fw',
    'branch-firewall':    'branch-fw',
    'wan-router':         'wan-router',
    'wan_router':         'wan-router',
    'router':             'wan-router',
    'nac':                'nac',
    'clearpass':          'nac',
    'distribution-switch':'distribution-switch',
    'dist-switch':        'distribution-switch',
    'dist_switch':        'distribution-switch',
}

MANAGED_BY_ALIASES = {
    'ssh':           'ssh',
    'ssh only':      'ssh',
    'none':          'ssh',
    '':              'ssh',
    'aruba-central': 'aruba-central',
    'aruba central': 'aruba-central',
    'central':       'aruba-central',
    'arubacentral':  'aruba-central',
    'mist':          'mist',
    'juniper mist':  'mist',
    'junipermist':   'mist',
    'mist cloud':    'mist',
    'fortimgr-api':  'fortimgr-api',
    'fortimanager':  'fortimgr-api',
    'fortimgr':      'fortimgr-api',
    'forti-mgr':     'fortimgr-api',
    'fmc-api':       'fmc-api',
    'fmc':           'fmc-api',
    'cisco fmc':     'fmc-api',
    'clearpass-api': 'clearpass-api',
    'clearpass':     'clearpass-api',
    'fortigate':     'fortigate',
    'via fortigate': 'fortigate',
    'fortigate firewall': 'fortigate',
    'forti-gate':    'fortigate',
}

# Platform alias normalization — maps common names to vendor_matrix slugs
PLATFORM_ALIASES = {
    # aruba
    'arubaos':          'arubaos',
    'aruba-os':         'arubaos',
    'aruba os':         'arubaos',
    'aos-s':            'arubaos',
    'arubaoscx':        'arubaoscx',
    'aruba-oscx':       'arubaoscx',
    'aos-cx':           'arubaoscx',
    'aoscx':            'arubaoscx',
    # juniper
    'junos':            'junos',
    'junos-srx':        'junos-srx',
    'srx':              'junos-srx',
    # cisco
    'iosxe':            'iosxe',
    'ios-xe':           'iosxe',
    'ios xe':           'iosxe',
    'ios':              'ios',
    'nxos':             'nxos',
    'nx-os':            'nxos',
    'nx os':            'nxos',
    'asa':              'asa',
    'ftd':              'ftd',
    # fortinet
    'fortios':          'fortios',
    'forti-os':         'fortios',
    'fortios-switch':   'fortios-switch',
    'fortios-ap':       'fortios-ap',
    # aruba nac
    'clearpass':        'clearpass',
}


# ── API helpers ───────────────────────────────────────────────────────────────

_nautobot_cache = {}

def get_nautobot_cache():
    """Load lookup data from Nautobot once."""
    global _nautobot_cache
    if _nautobot_cache:
        return _nautobot_cache

    def fetch_all(endpoint):
        return client.get_all(endpoint, params={'limit': 200})

    def natural_to_slug(ns):
        if not ns:
            return ''
        parts = ns.rsplit('_', 1)
        return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else ns

    tenants   = fetch_all('tenancy/tenants')
    ns_objs   = fetch_all('ipam/namespaces')
    sgs       = fetch_all('extras/secrets-groups')
    platforms = fetch_all('dcim/platforms')

    _nautobot_cache = {
        'tenant_slugs':    {natural_to_slug(t['natural_slug']): t['id'] for t in tenants},
        'tenant_names':    {t['name']: natural_to_slug(t['natural_slug']) for t in tenants},
        'namespaces':      {n['name'] for n in ns_objs},
        'secrets_groups':  {sg['name'] for sg in sgs},
        'platform_labels': {p['name']: natural_to_slug(p['natural_slug']) for p in platforms},
    }
    return _nautobot_cache


# ── Validators ────────────────────────────────────────────────────────────────

def normalize_vendor(raw):
    return VENDOR_ALIASES.get(raw.lower().strip(), None)

def normalize_role(raw):
    return ROLE_ALIASES.get(raw.lower().strip(), None)

def normalize_managed_by(raw):
    return MANAGED_BY_ALIASES.get(raw.lower().strip(), 'ssh')

def normalize_platform(raw):
    if not raw:
        return None
    return PLATFORM_ALIASES.get(raw.lower().strip(), raw.lower().strip())

def validate_ip(ip_str):
    """Validate IP/prefix format. Returns (normalized, error)."""
    try:
        net = ipaddress.ip_interface(ip_str.strip())
        return str(net), None
    except ValueError:
        return None, f"Invalid IP format: '{ip_str}' — must be x.x.x.x/prefix"

def derive_platform(vendor, device_type):
    """Derive default platform slug from vendor+device_type."""
    return get_default_platform(vendor, device_type)

def vendor_to_device_type(vendor, role):
    """Map role to device_type key used in vendor_matrix."""
    role_to_dtype = {
        'access-switch':       'switch',
        'core-switch':         'switch',
        'distribution-switch': 'switch',
        'ap':                  'ap',
        'branch-fw':           'firewall',
        'wan-router':          'switch',
        'nac':                 'nac',
    }
    return role_to_dtype.get(role)

def derive_secrets_group(vendor, role, access_method, tenant_slug):
    """Derive the secrets group name for this device."""
    dtype = vendor_to_device_type(vendor, role)
    if not dtype:
        return None
    prefix = get_secrets_group_prefix(vendor, dtype, access_method)
    if not prefix:
        return None
    return f"{prefix}-{tenant_slug}"

def validate_access_method(vendor, role, managed_by):
    """Check managed_by is valid for this vendor+role combo."""
    dtype = vendor_to_device_type(vendor, role)
    if not dtype:
        return False, f"Cannot map role '{role}' to a device type"
    methods = get_access_methods(vendor, dtype)
    if managed_by not in methods:
        valid = list(methods.keys())
        return False, f"'{managed_by}' not valid for {vendor}/{dtype} — valid: {valid}"
    return True, None

def check_duplicate_ips(rows):
    """Return set of IPs that appear more than once in the CSV."""
    seen = {}
    for i, row in enumerate(rows):
        ip = row.get('_ip_normalized', row.get('ip', ''))
        seen.setdefault(ip, []).append(i + 1)
    return {ip: lines for ip, lines in seen.items() if len(lines) > 1}

def check_nautobot_ip_exists(ip):
    """Check if IP already exists in Nautobot."""
    r = client.get('ipam/ip-addresses', params={'address': ip, 'limit': 5})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('address') == ip:
                return True
    return False


# ── Row processor ─────────────────────────────────────────────────────────────

STATUS_OK      = 'OK'
STATUS_FIXED   = 'FIXED'
STATUS_WARN    = 'WARN'
STATUS_ERROR   = 'ERROR'

def process_row(row, site_config, cache, row_num, check_nautobot=True):
    """
    Validate and enrich a single CSV row.
    Returns (enriched_row, status, issues)
    """
    issues  = []
    fixes   = []
    warns   = []
    out     = dict(row)
    status  = STATUS_OK

    tenant_slug = site_config['tenant_slug']

    # ── Vendor ────────────────────────────────────────────────────────────────
    raw_vendor  = row.get('vendor', '').strip()
    norm_vendor = normalize_vendor(raw_vendor)
    if not norm_vendor:
        issues.append(f"Unknown vendor '{raw_vendor}' — valid: {get_enabled_vendors()}")
        status = STATUS_ERROR
    elif norm_vendor != raw_vendor:
        fixes.append(f"vendor: '{raw_vendor}'→'{norm_vendor}'")
        out['vendor'] = norm_vendor
        status = max(status, STATUS_FIXED) if status == STATUS_OK else status
    else:
        out['vendor'] = norm_vendor

    # ── Role ──────────────────────────────────────────────────────────────────
    raw_role  = row.get('role', '').strip()
    norm_role = normalize_role(raw_role)
    if not norm_role:
        issues.append(f"Unknown role '{raw_role}' — valid: {list(ROLE_ALIASES.values())}")
        status = STATUS_ERROR
    elif norm_role != raw_role:
        fixes.append(f"role: '{raw_role}'→'{norm_role}'")
        out['role'] = norm_role
        status = max(status, STATUS_FIXED) if status == STATUS_OK else status
    else:
        out['role'] = norm_role

    # ── Managed by ────────────────────────────────────────────────────────────
    raw_managed  = row.get('managed_by', '').strip()
    norm_managed = normalize_managed_by(raw_managed)
    if norm_managed != raw_managed:
        fixes.append(f"managed_by: '{raw_managed}'→'{norm_managed}'")
        out['managed_by'] = norm_managed
        if status == STATUS_OK:
            status = STATUS_FIXED
    else:
        out['managed_by'] = norm_managed

    # Validate managed_by against vendor_matrix (only if vendor+role resolved)
    if norm_vendor and norm_role:
        valid_mb, mb_err = validate_access_method(norm_vendor, norm_role, norm_managed)
        if not valid_mb:
            issues.append(mb_err)
            status = STATUS_ERROR

    # ── Platform ──────────────────────────────────────────────────────────────
    raw_plat  = row.get('platform', '').strip()
    if raw_plat:
        norm_plat = normalize_platform(raw_plat)
        if norm_plat != raw_plat:
            fixes.append(f"platform: '{raw_plat}'→'{norm_plat}'")
            if status == STATUS_OK:
                status = STATUS_FIXED
        out['platform'] = norm_plat
    elif norm_vendor and norm_role:
        # Auto-derive from vendor+role
        dtype = vendor_to_device_type(norm_vendor, norm_role)
        if dtype:
            derived = derive_platform(norm_vendor, dtype)
            if derived:
                out['platform'] = derived
                fixes.append(f"platform derived: '{derived}'")
                if status == STATUS_OK:
                    status = STATUS_FIXED
            else:
                warns.append(f"Could not derive platform for {norm_vendor}/{dtype}")
                if status == STATUS_OK:
                    status = STATUS_WARN

    # ── IP ────────────────────────────────────────────────────────────────────
    raw_ip = row.get('ip', '').strip()
    if not raw_ip:
        issues.append("IP address is required")
        status = STATUS_ERROR
    else:
        norm_ip, ip_err = validate_ip(raw_ip)
        if ip_err:
            issues.append(ip_err)
            status = STATUS_ERROR
        else:
            out['ip'] = norm_ip
            out['_ip_normalized'] = norm_ip
            if check_nautobot and check_nautobot_ip_exists(norm_ip):
                warns.append(f"IP {norm_ip} already exists in Nautobot")
                if status == STATUS_OK:
                    status = STATUS_WARN

    # ── Device name ───────────────────────────────────────────────────────────
    if not row.get('device_name', '').strip():
        issues.append("device_name is required")
        status = STATUS_ERROR

    # ── Model ─────────────────────────────────────────────────────────────────
    if not row.get('model', '').strip():
        issues.append("model is required")
        status = STATUS_ERROR

    # ── Derive secrets group ──────────────────────────────────────────────────
    if norm_vendor and norm_role and norm_managed:
        sg = derive_secrets_group(norm_vendor, norm_role, norm_managed, tenant_slug)
        if sg:
            out['secrets_group'] = sg
            # Warn if secrets group doesn't exist in Nautobot yet
            if sg not in cache['secrets_groups']:
                warns.append(f"Secrets group '{sg}' not in Nautobot — run create_tenant.py first")
                if status == STATUS_OK:
                    status = STATUS_WARN
        else:
            warns.append(f"Could not derive secrets group for {norm_vendor}/{norm_role}/{norm_managed}")

    # ── Derive namespace ──────────────────────────────────────────────────────
    out['namespace'] = tenant_slug
    if tenant_slug not in cache['namespaces']:
        warns.append(f"Namespace '{tenant_slug}' not in Nautobot — run create_tenant.py first")
        if status == STATUS_OK:
            status = STATUS_WARN

    # ── Tenant slug ───────────────────────────────────────────────────────────
    out['tenant_slug'] = tenant_slug
    if tenant_slug not in cache['tenant_slugs']:
        issues.append(f"Tenant '{tenant_slug}' not found in Nautobot")
        status = STATUS_ERROR

    # ── Add site location columns ─────────────────────────────────────────────
    out['region']    = site_config['region']
    out['country']   = site_config['country']
    out['state']     = site_config['state']
    out['city']      = site_config['city']
    out['site_name'] = site_config['site_name']
    out['site_type'] = site_config['site_type']

    # ── Status default ────────────────────────────────────────────────────────
    if not out.get('status', '').strip():
        out['status'] = 'Active'

    # ── Serial cleanup ────────────────────────────────────────────────────────
    out['serial'] = row.get('serial', '').strip()

    return out, status, issues + fixes + warns


# ── Output CSV columns ────────────────────────────────────────────────────────

OUTPUT_COLS = [
    'device_name', 'role', 'vendor', 'platform', 'model', 'ip',
    'managed_by', 'serial', 'status', 'stack_group',
    'tenant_slug', 'secrets_group', 'namespace',
    'region', 'country', 'state', 'city', 'site_name', 'site_type',
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Validate and prepare site CSV for onboarding')
    parser.add_argument('--csv',         required=True, help='Engineer CSV path')
    parser.add_argument('--site-config', required=True, help='Site config JSON path')
    parser.add_argument('--dry-run',     action='store_true', help='Validate only — do not write output CSV')
    parser.add_argument('--no-nautobot', action='store_true', help='Skip Nautobot IP existence check (faster)')
    args = parser.parse_args()

    # Load site config
    try:
        with open(args.site_config) as f:
            site_config = json.load(f)
        for k in ['tenant_slug','region','country','state','city','site_name','site_type']:
            if k not in site_config:
                print(f"\nERROR: site config missing '{k}'")
                sys.exit(1)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nERROR loading site config: {e}")
        sys.exit(1)

    # Load engineer CSV
    try:
        with open(args.csv, newline='') as f:
            reader = csv.DictReader(f)
            raw_rows = [{k.strip(): (v.strip() if v else '') for k, v in r.items()}
                        for r in reader]
    except FileNotFoundError:
        print(f"\nERROR: CSV not found: {args.csv}")
        sys.exit(1)

    if not raw_rows:
        print("\nERROR: CSV is empty")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "LIVE RUN"
    print(f"\n{'='*65}")
    print(f"  nautobot_prepare.py  [{mode}]")
    print(f"  CSV         : {args.csv}")
    print(f"  Site config : {args.site_config}")
    print(f"  Site        : {site_config['site_name']} ({site_config['site_type']})")
    print(f"  Tenant      : {site_config['tenant_slug']}")
    print(f"  Location    : {site_config['region']} → {site_config['country']} → "
          f"{site_config['state']} → {site_config['city']}")
    print(f"  Devices     : {len(raw_rows)}")
    print(f"{'='*65}\n")

    # Load Nautobot cache
    print("  Loading Nautobot data...")
    try:
        cache = get_nautobot_cache()
        print(f"  Tenants: {len(cache['tenant_slugs'])} | "
              f"Namespaces: {len(cache['namespaces'])} | "
              f"Secrets groups: {len(cache['secrets_groups'])}\n")
    except Exception as e:
        print(f"  WARNING: Could not load Nautobot cache: {e}")
        cache = {'tenant_slugs': {}, 'tenant_names': {},
                 'namespaces': set(), 'secrets_groups': set(), 'platform_labels': {}}

    # Check for duplicate IPs in CSV first
    dup_ips = check_duplicate_ips(raw_rows)
    if dup_ips:
        print("  ⚠️  Duplicate IPs in CSV:")
        for ip, lines in dup_ips.items():
            print(f"    {ip} — rows {lines}")
        print()

    # Process each row
    enriched_rows  = []
    table_rows     = []
    counts         = {STATUS_OK: 0, STATUS_FIXED: 0, STATUS_WARN: 0, STATUS_ERROR: 0}

    for i, row in enumerate(raw_rows, 1):
        check_nb = not args.no_nautobot
        out, status, notes = process_row(row, site_config, cache, i, check_nautobot=check_nb)
        counts[status] += 1
        enriched_rows.append((out, status))

        status_icon = {'OK': '✅', 'FIXED': '🔧', 'WARN': '⚠️ ', 'ERROR': '❌'}[status]
        note_str = ' | '.join(notes[:2]) + ('...' if len(notes) > 2 else '')
        table_rows.append([
            i,
            row.get('device_name', ''),
            out.get('vendor', ''),
            out.get('role', ''),
            out.get('platform', ''),
            out.get('ip', ''),
            out.get('managed_by', ''),
            f"{status_icon} {status}",
            note_str,
        ])

    # Print validation table
    print(tabulate(table_rows, headers=[
        '#', 'Device', 'Vendor', 'Role', 'Platform', 'IP', 'Managed By', 'Status', 'Notes'
    ], tablefmt='simple', maxcolwidths=[4,16,8,14,14,16,14,10,40]))

    # Summary
    print(f"\n{'='*65}")
    print(f"  ✅ {counts[STATUS_OK]:3} OK   "
          f"🔧 {counts[STATUS_FIXED]:3} auto-fixed   "
          f"⚠️  {counts[STATUS_WARN]:3} warnings   "
          f"❌ {counts[STATUS_ERROR]:3} errors")
    print(f"{'='*65}")

    error_rows  = [(out, s, i+1) for i,(out,s) in enumerate(enriched_rows) if s == STATUS_ERROR]
    ready_rows  = [out for out, s in enriched_rows if s != STATUS_ERROR]

    if error_rows:
        print(f"\n  ❌ {len(error_rows)} rows have errors — they will be EXCLUDED from output:")
        for out, _, rn in error_rows:
            print(f"     Row {rn}: {out.get('device_name','?')} — fix and re-run")

    if counts[STATUS_WARN] > 0:
        print(f"\n  ⚠️  Warnings do not block output — review before onboarding.")

    if args.dry_run:
        print(f"\n  DRY RUN — no output file written.")
        print(f"  {len(ready_rows)}/{len(raw_rows)} rows would be written to nautobot_ready CSV.")
        print(f"\n  Run without --dry-run to generate the output CSV.\n")
        if counts[STATUS_ERROR] > 0:
            sys.exit(1)
        return

    if not ready_rows:
        print("\n  No valid rows — nothing to write.\n")
        sys.exit(1)

    # Write nautobot_ready CSV
    site_name  = site_config['site_name'].lower().replace(' ', '-')
    out_path   = os.path.join(LAB_DIR, f"nautobot_ready_{site_name}.csv")

    with open(out_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(ready_rows)

    print(f"\n  ✅ Output written: {out_path}")
    print(f"     {len(ready_rows)} devices ready for onboarding.\n")

    # Write manifest
    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    manifest = {
        "phase":       "prepare",
        "timestamp":   datetime.now().isoformat(),
        "input_csv":   args.csv,
        "output_csv":  out_path,
        "site":        site_config['site_name'],
        "tenant":      site_config['tenant_slug'],
        "total":       len(raw_rows),
        "ready":       len(ready_rows),
        "errors":      counts[STATUS_ERROR],
        "warnings":    counts[STATUS_WARN],
        "auto_fixed":  counts[STATUS_FIXED],
    }
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    mpath = os.path.join(MANIFESTS_DIR, f"prepare_{site_config['tenant_slug']}_{site_name}_{ts}.json")
    with open(mpath, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"  Manifest → {mpath}\n")

    if counts[STATUS_ERROR] > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
