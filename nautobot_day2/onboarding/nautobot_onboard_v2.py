"""
nautobot_onboard_v2.py
Phase 5 — Creates devices and full location hierarchy in Nautobot.
Reads nautobot_ready_<site>.csv produced by nautobot_prepare.py.

P6 fix: 6-level hierarchy (Region→Country→State→City→Site type)
Auto-links controllers to External Integrations — no manual UI step.
Idempotent — skips objects that already exist.

Usage:
    python3 nautobot_onboard_v2.py --csv tests/test_site.csv --dry-run
    python3 nautobot_onboard_v2.py --csv tests/test_site.csv
"""

import sys
import os
import csv
import json
import argparse
import ipaddress
from datetime import datetime
from tabulate import tabulate

LAB_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_MANIFESTS_DIR = os.path.join(LAB_DIR, 'manifests')

sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.dirname(LAB_DIR))
from vendor_matrix import VENDOR_MATRIX
from client import NautobotClient

client = NautobotClient(env_file=os.path.join(LAB_DIR, '.env'))
URL = client.url

# Global cache — populated once at startup
_C = {}


# ── API helpers ───────────────────────────────────────────────────────────────
# Thin wrappers around the shared NautobotClient so the lookups below (which
# call these by name) don't need to change.

def api_get_all(endpoint, params=None):
    return client.get_all(endpoint, params=params)

def api_post(endpoint, data):
    return client.post(endpoint, data)

def api_patch(endpoint, obj_id, data):
    return client.patch(f'{endpoint}/{obj_id}', data)


# ── Startup cache ─────────────────────────────────────────────────────────────

def natural_slug_to_slug(natural_slug):
    """Strip the _xxxx suffix from natural_slug to recover the original slug."""
    if not natural_slug:
        return ''
    parts = natural_slug.rsplit('_', 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else natural_slug


def init_cache():
    """Pre-fetch all lookup tables — one API call per type, then dict lookups."""
    print("  Loading cache...")

    statuses = api_get_all('extras/statuses')
    _C['statuses'] = {s['name']: s['id'] for s in statuses}

    loctypes = api_get_all('dcim/location-types')
    _C['location_types'] = {lt['name']: lt['id'] for lt in loctypes}

    platforms = api_get_all('dcim/platforms')
    _C['platforms_by_name'] = {p['name']: p['id'] for p in platforms}
    # Also map by our vendor_matrix slug via the label we created
    from vendor_matrix import get_all_platforms
    vm_platforms = get_all_platforms()
    _C['platforms'] = {}
    for slug, data in vm_platforms.items():
        label = data['label']
        pid = _C['platforms_by_name'].get(label)
        if pid:
            _C['platforms'][slug] = pid

    roles = api_get_all('extras/roles')
    _C['roles'] = {r['name']: r['id'] for r in roles}

    mfrs = api_get_all('dcim/manufacturers')
    _C['manufacturers'] = {m['name']: m['id'] for m in mfrs}

    namespaces = api_get_all('ipam/namespaces')
    _C['namespaces'] = {n['name']: n['id'] for n in namespaces}

    tenants = api_get_all('tenancy/tenants')
    _C['tenants'] = {}
    for t in tenants:
        # slug field removed in 3.1.3 — derive from natural_slug
        slug = natural_slug_to_slug(t.get('natural_slug', ''))
        if slug:
            _C['tenants'][slug] = t['id']

    sgs = api_get_all('extras/secrets-groups')
    _C['secrets_groups'] = {sg['name']: sg['id'] for sg in sgs}

    ext_ints = api_get_all('extras/external-integrations')
    _C['ext_integrations'] = {ei['name']: ei['id'] for ei in ext_ints}

    # Validate required objects exist
    missing = []
    for s in ['Active']:
        if s not in _C['statuses']:
            missing.append(f"Status '{s}'")
    for lt in ['Region', 'Country', 'State', 'City']:
        if lt not in _C['location_types']:
            missing.append(f"Location type '{lt}'")
    if missing:
        print(f"\n  ERROR — missing required objects (run bootstrap first):")
        for m in missing:
            print(f"    {m}")
        sys.exit(1)

    print(f"  Statuses       : {len(_C['statuses'])}")
    print(f"  Location types : {len(_C['location_types'])}")
    print(f"  Platforms      : {len(_C['platforms'])}")
    print(f"  Roles          : {len(_C['roles'])}")
    print(f"  Manufacturers  : {len(_C['manufacturers'])}")
    print(f"  Namespaces     : {len(_C['namespaces'])}")
    print(f"  Tenants        : {len(_C['tenants'])}")
    print(f"  Secrets groups : {len(_C['secrets_groups'])}")
    print(f"  Ext integrations: {len(_C['ext_integrations'])}")


# ── Location ──────────────────────────────────────────────────────────────────

def get_or_create_location(name, type_name, parent_id, status_id, dry_run):
    """Find or create a single location level. Returns (id, status_msg)."""
    type_id = _C['location_types'].get(type_name)
    if not type_id:
        return None, f"FAILED: location type '{type_name}' not in Nautobot"

    # Search by name -- verify BOTH location_type AND parent match, since
    # Nautobot's actual uniqueness constraint is (parent, name) together.
    # Checking name+type alone can miss a real pre-existing match whenever
    # two locations share a name+type but sit under different parents,
    # which then surfaces later as a confusing 400 "must make a unique
    # set" error at creation time instead of a clean "already exists".
    params = {'name': name, 'limit': 50}
    r = client.get('dcim/locations', params=params)
    if r.ok:
        for obj in r.json().get('results', []):
            obj_parent_id = (obj.get('parent') or {}).get('id')
            parent_matches = (
                (parent_id is None and obj_parent_id is None) or
                (parent_id is not None and obj_parent_id == parent_id)
            )
            if (obj.get('name') == name and
                    obj.get('location_type', {}).get('id') == type_id and
                    parent_matches):
                return obj['id'], 'exists'

    if dry_run:
        return f'DRY:{name}', 'would create'

    payload = {
        "name":          name,
        "location_type": type_id,
        "status":        {"id": status_id},
    }
    if parent_id and not str(parent_id).startswith('DRY:'):
        payload["parent"] = {"id": parent_id}

    r = api_post('dcim/locations', payload)
    if r.status_code == 201:
        return r.json()['id'], 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


def build_location_hierarchy(row, status_id, dry_run):
    """
    Build Region→Country→State→City→Site hierarchy.
    Returns (site_id, log_lines).
    """
    log   = []
    chain = [
        ('Region',          row['region'],    None),
        ('Country',         row['country'],   None),
        ('State',           row['state'],     None),
        ('City',            row['city'],      None),
        (row['site_type'],  row['site_name'], None),
    ]

    parent_id = None
    site_id   = None

    for level, name, _ in chain:
        loc_id, msg = get_or_create_location(
            name, level, parent_id, status_id, dry_run)
        log.append((level, name, msg))

        if not loc_id:
            return None, log

        # For dry-run, keep going but use None for real parent
        if not str(loc_id).startswith('DRY:'):
            parent_id = loc_id
            site_id   = loc_id
        else:
            parent_id = None
            site_id   = None

    return site_id, log


# ── Device type ───────────────────────────────────────────────────────────────

def get_or_create_device_type(model, vendor_slug, dry_run, dt_cache):
    """Find or create a device type (manufacturer + model)."""
    cache_key = f"{vendor_slug}|{model}"
    if cache_key in dt_cache:
        return dt_cache[cache_key], 'cached'

    vendor_label = VENDOR_MATRIX.get(vendor_slug, {}).get('label', vendor_slug)
    mfr_id = _C['manufacturers'].get(vendor_label)
    if not mfr_id:
        return None, f"FAILED: manufacturer '{vendor_label}' not found"

    # Search by model — model filter may not be supported, try then fall back
    r = client.get('dcim/device-types', params={'model': model, 'limit': 50})
    if r.status_code == 400:
        r = client.get('dcim/device-types', params={'limit': 200})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('model') == model:
                dt_cache[cache_key] = obj['id']
                return obj['id'], 'exists'

    if dry_run:
        dt_cache[cache_key] = f'DRY:{model}'
        return f'DRY:{model}', 'would create'

    slug = (model.lower()
            .replace(' ', '-').replace('/', '-')
            .replace('_', '-').replace('.', '-'))
    slug = '-'.join(filter(None, slug.split('-')))

    r = api_post('dcim/device-types', {
        "model":        model,
        "slug":         slug,
        "manufacturer": {"id": mfr_id}
    })
    if r.status_code == 201:
        new_id = r.json()['id']
        dt_cache[cache_key] = new_id
        return new_id, 'created'
    # slug collision — try with suffix
    if r.status_code == 400 and 'slug' in r.text:
        import hashlib
        slug2 = slug + '-' + hashlib.md5(model.encode()).hexdigest()[:4]
        r2 = api_post('dcim/device-types', {
            "model":        model,
            "slug":         slug2,
            "manufacturer": {"id": mfr_id}
        })
        if r2.status_code == 201:
            new_id = r2.json()['id']
            dt_cache[cache_key] = new_id
            return new_id, 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


# ── IP helpers ────────────────────────────────────────────────────────────────

def parent_prefix_str(ip_with_prefix):
    """Derive /24 (or shorter) parent network from an IP/prefix."""
    net = ipaddress.ip_network(ip_with_prefix, strict=False)
    pl  = min(net.prefixlen, 24)
    return str(ipaddress.ip_network(f"{net.network_address}/{pl}", strict=False))


def get_or_create_prefix(ip_with_prefix, namespace_id, tenant_id, status_id, dry_run,
                          pfx_cache):
    """Ensure parent /24 prefix exists."""
    prefix_str = parent_prefix_str(ip_with_prefix)
    cache_key  = f"{namespace_id}|{prefix_str}"

    if cache_key in pfx_cache:
        return pfx_cache[cache_key], 'cached'

    # Search existing
    r = client.get('ipam/prefixes', params={'prefix': prefix_str, 'limit': 50})
    if r.status_code == 400:
        r = client.get('ipam/prefixes', params={'limit': 200})
    if r.ok:
        for obj in r.json().get('results', []):
            if (obj.get('prefix') == prefix_str and
                    obj.get('namespace', {}).get('id') == namespace_id):
                pfx_cache[cache_key] = obj['id']
                return obj['id'], 'exists'

    if dry_run:
        pfx_cache[cache_key] = f'DRY:{prefix_str}'
        return f'DRY:{prefix_str}', 'would create'

    payload = {
        "prefix":    prefix_str,
        "namespace": {"id": namespace_id},
        "status":    {"id": status_id},
        "type":      "network",
    }
    if tenant_id:
        payload["tenant"] = {"id": tenant_id}

    r = api_post('ipam/prefixes', payload)
    if r.status_code == 201:
        new_id = r.json()['id']
        pfx_cache[cache_key] = new_id
        return new_id, 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


def get_or_create_ip(address, namespace_id, tenant_id, status_id, prefix_id, dry_run):
    """Create IP address if it doesn't exist."""
    # Search existing
    r = client.get('ipam/ip-addresses', params={'address': address, 'limit': 20})
    if r.status_code == 400:
        r = client.get('ipam/ip-addresses', params={'limit': 200})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('address') == address:
                return obj['id'], 'exists'

    if dry_run:
        return f'DRY:{address}', 'would create'

    payload = {
        "address": address,
        "status":  {"id": status_id},
        "type":    "host",
    }
    if prefix_id and not str(prefix_id).startswith('DRY:'):
        payload["parent"] = {"id": prefix_id}
    if tenant_id:
        payload["tenant"] = {"id": tenant_id}

    r = api_post('ipam/ip-addresses', payload)
    if r.status_code == 201:
        return r.json()['id'], 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


# ── Device ────────────────────────────────────────────────────────────────────

def get_or_create_virtual_chassis(name, dry_run, vc_cache):
    """
    Find or create a VirtualChassis by name. Represents a real physical
    stack (Aruba backplane stacking / VSF) as ONE logical device made of
    N physical members -- not to be confused with a firewall HA pair,
    which uses Nautobot's separate DeviceRedundancyGroup model instead
    since each unit there keeps its own independent identity.
    """
    if name in vc_cache:
        return vc_cache[name], 'cached'

    r = client.get('dcim/virtual-chassis', params={'name': name, 'limit': 10})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('name') == name:
                vc_cache[name] = obj['id']
                return obj['id'], 'exists'

    if dry_run:
        vc_cache[name] = f'DRY:{name}'
        return f'DRY:{name}', 'would create'

    r = api_post('dcim/virtual-chassis', {"name": name})
    if r.status_code == 201:
        new_id = r.json()['id']
        vc_cache[name] = new_id
        return new_id, 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


def get_or_create_device(row, site_id, dt_id, role_id, platform_id,
                          tenant_id, status_id, sg_id, dry_run,
                          vc_id=None, vc_position=None):
    """Find or create a device. If vc_id is given, links this device
    into that VirtualChassis at vc_position (a real stack member)."""
    # Check if already exists
    r = client.get('dcim/devices', params={'name': row['device_name'], 'limit': 10})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('name') == row['device_name']:
                return obj['id'], 'exists'

    if dry_run:
        return f"DRY:{row['device_name']}", 'would create'

    if not all([site_id, dt_id, role_id, status_id]):
        return None, (f"FAILED: missing required IDs — "
                      f"site={site_id} dt={dt_id} role={role_id}")

    payload = {
        "name":        row['device_name'],
        "device_type": {"id": dt_id},
        "role":        {"id": role_id},
        "status":      {"id": status_id},
        "location":    {"id": site_id},
    }
    if platform_id:
        payload["platform"] = {"id": platform_id}
    if tenant_id:
        payload["tenant"] = {"id": tenant_id}
    if row.get('serial', '').strip():
        payload["serial"] = row['serial'].strip()
    if sg_id:
        payload["secrets_group"] = {"id": sg_id}
    if vc_id and not str(vc_id).startswith('DRY:'):
        payload["virtual_chassis"] = {"id": vc_id}
        if vc_position:
            payload["vc_position"] = int(vc_position)

    r = api_post('dcim/devices', payload)
    if r.status_code == 201:
        return r.json()['id'], 'created'
    return None, f"FAILED {r.status_code}: {r.text[:120]}"


def set_primary_ip(device_id, ip_id, dry_run):
    """
    Nautobot 3.1.3 IP assignment flow:
    1. Get or create mgmt0 interface on device
    2. POST ipam/ip-address-to-interface/ to link IP to interface
    3. PATCH device primary_ip4
    Note: assigned_object_type/id fields removed in 3.1.3 — use ip-address-to-interface endpoint.
    """
    if dry_run or not device_id or not ip_id:
        return 'would set'
    if str(device_id).startswith('DRY:') or str(ip_id).startswith('DRY:'):
        return 'would set'

    # Step 1 — get or create mgmt0 interface
    r = client.get('dcim/interfaces', params={'device_id': device_id, 'name': 'mgmt0', 'limit': 5})
    intf_id = None
    if r.ok and r.json().get('count', 0) > 0:
        intf_id = r.json()['results'][0]['id']
    else:
        r2 = client.post('dcim/interfaces', {
                               "device":    {"id": device_id},
                               "name":      "mgmt0",
                               "type":      "1000base-t",
                               "mgmt_only": True,
                               "status":    {"id": _C['statuses']['Active']},
                           })
        if r2.status_code == 201:
            intf_id = r2.json()['id']
        else:
            return f"FAILED creating mgmt interface: {r2.status_code}: {r2.text[:80]}"

    # Step 2 — link IP to interface via ip-address-to-interface
    # Always POST — if mapping exists Nautobot returns 400 unique constraint which we ignore
    r4 = client.post('ipam/ip-address-to-interface', {
                           "ip_address": {"id": ip_id},
                           "interface":  {"id": intf_id},
                           "is_primary": True,
                       })
    if not r4.ok and 'already exists' not in r4.text and r4.status_code != 400:
        return f"FAILED linking IP to interface: {r4.status_code}: {r4.text[:80]}"

    # Step 3 — set primary_ip4 on device
    r5 = api_patch('dcim/devices', device_id, {"primary_ip4": {"id": ip_id}})
    return 'set' if r5.ok else f"FAILED setting primary: {r5.status_code}: {r5.text[:80]}"


# ── Controller ────────────────────────────────────────────────────────────────

# Maps managed_by value → External Integration prefix
MANAGED_BY_INTEGRATION = {
    'aruba-central': 'Aruba Central',
    'mist':          'Mist Cloud',
    'fortimgr-api':  'FortiManager',
    'fmc-api':       'Cisco FMC',
    'clearpass-api': 'ClearPass',
}


def get_or_create_controller(name, site_id, tenant_id, status_id,
                               dry_run, ctrl_cache):
    """Find or create a controller for a site+managed_by combination.
    Note: role is intentionally omitted — controllers use null role in Nautobot 3.1.3.
    """
    if name in ctrl_cache:
        return ctrl_cache[name], 'cached'

    r = client.get('dcim/controllers', params={'name': name, 'limit': 10})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('name') == name:
                ctrl_cache[name] = obj['id']
                return obj['id'], 'exists'

    if dry_run:
        ctrl_cache[name] = f'DRY:{name}'
        return f'DRY:{name}', 'would create'

    payload = {
        "name":   name,
        "status": {"id": status_id},
    }
    if site_id and not str(site_id).startswith('DRY:'):
        payload["location"] = {"id": site_id}
    if tenant_id:
        payload["tenant"] = {"id": tenant_id}

    r = api_post('dcim/controllers', payload)
    if r.status_code == 201:
        new_id = r.json()['id']
        ctrl_cache[name] = new_id
        return new_id, 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


def auto_link_controller_integration(controller_id, int_name, dry_run):
    """Link controller to External Integration."""
    if dry_run or str(controller_id).startswith('DRY:'):
        return f'would link → {int_name}'
    int_id = _C['ext_integrations'].get(int_name)
    if not int_id:
        return f'SKIP: "{int_name}" not found in Nautobot'
    r = api_patch('dcim/controllers', controller_id,
                  {"external_integration": {"id": int_id}})
    return 'linked' if r.ok else f"FAILED {r.status_code}: {r.text[:80]}"


def get_or_create_controller_group(name, controller_id, tenant_id,
                                    dry_run, grp_cache):
    """Find or create a controller managed device group."""
    if name in grp_cache:
        return grp_cache[name], 'cached'

    r = client.get('dcim/controller-managed-device-groups', params={'name': name, 'limit': 10})
    if r.ok:
        for obj in r.json().get('results', []):
            if obj.get('name') == name:
                grp_cache[name] = obj['id']
                return obj['id'], 'exists'

    if dry_run or str(controller_id).startswith('DRY:'):
        grp_cache[name] = f'DRY:{name}'
        return f'DRY:{name}', 'would create'

    payload = {
        "name":       name,
        "controller": {"id": controller_id},
        "weight":     1000,
    }
    if tenant_id:
        payload["tenant"] = {"id": tenant_id}

    r = api_post('dcim/controller-managed-device-groups', payload)
    if r.status_code == 201:
        new_id = r.json()['id']
        grp_cache[name] = new_id
        return new_id, 'created'
    return None, f"FAILED {r.status_code}: {r.text[:100]}"


def link_device_to_group(device_id, group_id, dry_run):
    if dry_run or str(device_id).startswith('DRY:') or str(group_id).startswith('DRY:'):
        return 'would link'
    r = api_patch('dcim/devices', device_id,
                  {"controller_managed_device_group": {"id": group_id}})
    return 'linked' if r.ok else f"FAILED {r.status_code}: {r.text[:80]}"


# ── CSV loader ────────────────────────────────────────────────────────────────

REQUIRED_COLS = [
    'device_name', 'role', 'vendor', 'platform', 'model', 'ip',
    'managed_by', 'tenant_slug', 'secrets_group', 'namespace',
    'region', 'country', 'state', 'city', 'site_name', 'site_type',
]

def load_csv(path):
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        rows   = list(reader)
    if not rows:
        raise ValueError("CSV is empty")
    rows = [{k.strip(): (v.strip() if v else '') for k, v in row.items()}
            for row in rows]
    missing = [c for c in REQUIRED_COLS if c not in rows[0]]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    return rows


# ── Main processor ────────────────────────────────────────────────────────────

def process_csv(rows, dry_run):
    active_id  = _C['statuses']['Active']
    loc_cache  = {}   # site_key → site_id
    dt_cache   = {}   # vendor|model → device_type_id
    pfx_cache  = {}   # namespace|prefix → prefix_id
    ctrl_cache = {}   # controller_name → controller_id
    grp_cache  = {}   # group_name → group_id
    vc_cache   = {}   # vc_name → virtual_chassis_id
    results    = []

    print(f"\n  Processing {len(rows)} devices...\n")

    # Pre-pass: create one VirtualChassis per (site, stack_group) group
    # present in this batch, so device creation below can link members to
    # it. A lone row with a stack_group value isn't treated as a real
    # stack -- needs 2+ members to mean anything.
    vc_lookup = {}
    vc_master_candidate = {}
    stack_rows = {}
    for row in rows:
        sg = row.get('stack_group', '').strip()
        if not sg:
            continue
        site_key = f"{row['region']}|{row['country']}|{row['state']}|{row['city']}|{row['site_name']}"
        stack_rows.setdefault((site_key, sg), []).append(row)

    for (site_key, sg), members in stack_rows.items():
        if len(members) < 2:
            continue
        vc_name = f"{members[0]['site_name']}-{sg}"
        vc_id, vc_msg = get_or_create_virtual_chassis(vc_name, dry_run, vc_cache)
        if vc_msg in ('created', 'would create'):
            print(f"  + virtual chassis: {vc_name} ({vc_msg})")
        vc_lookup[(site_key, sg)] = vc_id

    for i, row in enumerate(rows, 1):
        name = row['device_name']
        ok   = True
        print(f"  [{i:03}/{len(rows):03}] {name}")

        # ── Location hierarchy ─────────────────────────────────────
        site_key = f"{row['region']}|{row['country']}|{row['state']}|{row['city']}|{row['site_name']}"
        if site_key not in loc_cache:
            site_id, loc_log = build_location_hierarchy(row, active_id, dry_run)
            loc_cache[site_key] = site_id
            for level, loc_name, msg in loc_log:
                if msg in ('created', 'would create'):
                    print(f"         + {level}: {loc_name} ({msg})")
                elif msg.startswith('FAILED'):
                    print(f"         ! {level}: {loc_name} — {msg}")
        site_id = loc_cache[site_key]

        # ── Lookup cached IDs ──────────────────────────────────────
        role_id   = _C['roles'].get(row['role'])
        plat_id   = _C['platforms'].get(row['platform'])
        tenant_id = _C['tenants'].get(row['tenant_slug'])
        ns_id     = _C['namespaces'].get(row['namespace'])
        sg_id     = _C['secrets_groups'].get(row['secrets_group'])

        # Warn on missing optional IDs
        if not plat_id:
            print(f"         ! platform '{row['platform']}' not found — skipping")
        if not sg_id:
            print(f"         ! secrets group '{row['secrets_group']}' not found — skipping")

        # Fail on missing required IDs
        if not role_id:
            print(f"         ! FAIL: role '{row['role']}' not found")
            results.append([name, 'FAILED', f"role '{row['role']}' not found"])
            continue
        if not tenant_id:
            print(f"         ! FAIL: tenant '{row['tenant_slug']}' not found")
            results.append([name, 'FAILED', f"tenant '{row['tenant_slug']}' not found"])
            continue
        if not ns_id:
            print(f"         ! FAIL: namespace '{row['namespace']}' not found")
            results.append([name, 'FAILED', f"namespace '{row['namespace']}' not found"])
            continue

        # ── Device type ────────────────────────────────────────────
        dt_id, dt_msg = get_or_create_device_type(
            row['model'], row['vendor'], dry_run, dt_cache)
        if dt_msg in ('created', 'would create'):
            print(f"         + device type: {row['model']} ({dt_msg})")
        if not dt_id or dt_msg.startswith('FAILED'):
            print(f"         ! FAIL device type: {dt_msg}")
            results.append([name, 'FAILED', f"device type: {dt_msg}"])
            continue

        # ── Stack membership (VirtualChassis) ──────────────────────
        sg_stack   = row.get('stack_group', '').strip()
        vc_id      = None
        vc_position = row.get('vc_position', '').strip() or None
        if sg_stack:
            vc_id = vc_lookup.get((site_key, sg_stack))

        # ── Prefix ────────────────────────────────────────────────
        # Skipped for stack member rows with no IP of their own -- the
        # whole stack is reached through the master's IP, so there's
        # nothing to create here for these rows.
        pfx_id = None
        if row['ip'].strip():
            pfx_id, pfx_msg = get_or_create_prefix(
                row['ip'], ns_id, tenant_id, active_id, dry_run, pfx_cache)
            if pfx_msg in ('created', 'would create'):
                print(f"         + prefix: {parent_prefix_str(row['ip'])} ({pfx_msg})")

        # ── IP address ────────────────────────────────────────────
        ip_id = None
        if row['ip'].strip():
            ip_id, ip_msg = get_or_create_ip(
                row['ip'], ns_id, tenant_id, active_id, pfx_id, dry_run)
            if ip_msg in ('created', 'would create'):
                print(f"         + IP: {row['ip']} ({ip_msg})")
            if ip_msg.startswith('FAILED'):
                print(f"         ! IP warning: {ip_msg}")

        # ── Device ────────────────────────────────────────────────
        dev_id, dev_msg = get_or_create_device(
            row, site_id, dt_id, role_id, plat_id,
            tenant_id, active_id, sg_id, dry_run,
            vc_id=vc_id, vc_position=vc_position)
        if dev_msg.startswith('FAILED'):
            print(f"         ! FAIL device: {dev_msg}")
            results.append([name, 'FAILED', dev_msg])
            continue
        print(f"         {'DRY ' if dry_run else 'OK  '} device ({dev_msg})")

        # ── Primary IP ────────────────────────────────────────────
        # Only set for rows that actually have their own IP -- stack
        # member rows without one correctly have nothing to link here.
        if ip_id:
            ip_link = set_primary_ip(dev_id, ip_id, dry_run)
            if 'FAILED' in str(ip_link):
                print(f"         ! primary IP: {ip_link}")
            # The row that actually carries the stack's management IP is
            # the provisioning-time default for VirtualChassis.master --
            # real Commander election happens on the hardware itself, so
            # sync-time discovery (Phase C) is what keeps this accurate
            # if reality differs.
            if vc_id and sg_stack:
                vc_master_candidate[(site_key, sg_stack)] = dev_id

        # ── Controller (API-managed devices) ──────────────────────
        managed_by = row.get('managed_by', '').strip()
        if managed_by in MANAGED_BY_INTEGRATION:
            int_prefix = MANAGED_BY_INTEGRATION[managed_by]
            ctrl_name  = f"{int_prefix}-{row['site_name']}"
            int_name   = f"{int_prefix} - {row['tenant_slug']}"

            # Controller
            ctrl_id, ctrl_msg = get_or_create_controller(
                ctrl_name, site_id, tenant_id, active_id,
                dry_run, ctrl_cache)
            if ctrl_msg in ('created', 'would create'):
                print(f"         + controller: {ctrl_name} ({ctrl_msg})")
                # Auto-link to external integration
                lnk = auto_link_controller_integration(ctrl_id, int_name, dry_run)
                print(f"           → integration: {int_name} ({lnk})")

            # Controller group
            grp_name = f"{ctrl_name}-group"
            grp_id, grp_msg = get_or_create_controller_group(
                grp_name, ctrl_id, tenant_id, dry_run, grp_cache)
            if grp_msg in ('created', 'would create'):
                print(f"         + ctrl group: {grp_name} ({grp_msg})")

            # Link device to group
            lnk = link_device_to_group(dev_id, grp_id, dry_run)
            if 'FAILED' in str(lnk):
                print(f"         ! device→group: {lnk}")

        results.append([name, 'OK', 'would create' if dry_run else dev_msg])

    # Post-pass: set each VirtualChassis's master to the device that
    # carried the stack's management IP.
    if not dry_run:
        for (site_key, sg_stack), master_dev_id in vc_master_candidate.items():
            vc_id = vc_lookup.get((site_key, sg_stack))
            if not vc_id or str(vc_id).startswith('DRY:'):
                continue
            r = client.patch(f'dcim/virtual-chassis/{vc_id}', {
                "master": {"id": master_dev_id}
            })
            if r.status_code == 200:
                print(f"  + virtual chassis master set: {sg_stack}")
            else:
                print(f"  ! virtual chassis master FAILED for {sg_stack}: {r.status_code}")

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Onboard devices into Nautobot')
    parser.add_argument('--csv',     required=True, help='Path to nautobot_ready CSV')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    args = parser.parse_args()

    try:
        rows = load_csv(args.csv)
    except (FileNotFoundError, ValueError) as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    mode       = "DRY RUN — no changes will be made" if args.dry_run else "LIVE RUN"
    site_name  = rows[0].get('site_name', 'unknown')
    tenant     = rows[0].get('tenant_slug', 'unknown')

    print(f"\n{'='*60}")
    print(f"  nautobot_onboard_v2.py  [{mode}]")
    print(f"  Site     : {site_name}")
    print(f"  Tenant   : {tenant}")
    print(f"  Devices  : {len(rows)}")
    print(f"  Target   : {URL}")
    print(f"{'='*60}\n")

    init_cache()
    results = process_csv(rows, args.dry_run)

    ok     = sum(1 for r in results if r[1] == 'OK')
    failed = sum(1 for r in results if r[1] == 'FAILED')

    print(f"\n{'='*60}")
    print(f"  Summary: {ok} OK | {failed} failed | {len(rows)} total")
    print(f"{'='*60}")

    if failed:
        print(f"\n  Failed devices:")
        for r in results:
            if r[1] == 'FAILED':
                print(f"    {r[0]:30} {r[2]}")

    if args.dry_run:
        print("\n  Run without --dry-run to apply changes.\n")
        return

    # Save manifest
    manifest = {
        "phase":     "onboard",
        "timestamp": datetime.now().isoformat(),
        "site":      site_name,
        "tenant":    tenant,
        "total":     len(rows),
        "ok":        ok,
        "failed":    failed,
        "failed_devices": [r[0] for r in results if r[1] == 'FAILED']
    }
    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(LAB_MANIFESTS_DIR, f"onboard_{tenant}_{site_name}_{ts}.json")
    os.makedirs(LAB_MANIFESTS_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  Manifest → {path}\n")

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
