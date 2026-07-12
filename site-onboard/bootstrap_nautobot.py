"""
bootstrap_nautobot.py
Phase 1 — Creates all base objects in Nautobot.
Reads manufacturers and platforms from vendor_matrix.py.
Idempotent — skips objects that already exist.
Run with --dry-run first.

Fixes for Nautobot 3.1.3:
  - slug filter removed from all endpoints — use name filter
  - device roles moved from dcim/device-roles to extras/roles
  - custom-fields does not support name filter — fetch all and search
"""

import sys
import argparse
import requests
import os
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

URL     = os.getenv('NAUTOBOT_URL')
TOKEN   = os.getenv('NAUTOBOT_TOKEN')
HEADERS = {
    'Authorization': f'Token {TOKEN}',
    'Content-Type':  'application/json',
    'Accept':        'application/json'
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vendor_matrix import get_all_manufacturers, get_all_platforms

# ── Static definitions ────────────────────────────────────────────────────────

TENANT_GROUPS = [
    {"name": "PBS", "slug": "pbs"},
    {"name": "MS",  "slug": "ms"},
]

# Content types for location types
# Site-level (leaf) — host devices, IPs, prefixes, racks etc.
_SITE_CT = [
    "dcim.device", "dcim.controller", "dcim.module",
    "ipam.prefix", "ipam.vlan", "ipam.vlangroup", "ipam.namespace",
    "virtualization.cluster", "circuits.circuittermination",
    "dcim.rackgroup", "dcim.rack"
]
# Intermediate — controller only
_MID_CT = ["dcim.controller"]

LOCATION_TYPES = [
    {"name": "Region",      "slug": "region",      "parent": None,       "content_types": _MID_CT},
    {"name": "Country",     "slug": "country",     "parent": "Region",   "content_types": _MID_CT},
    {"name": "State",       "slug": "state",       "parent": "Country",  "content_types": _MID_CT},
    {"name": "City",        "slug": "city",        "parent": "State",    "content_types": _MID_CT},
    {"name": "Branch",      "slug": "branch",      "parent": "City",     "content_types": _SITE_CT},
    {"name": "Campus",      "slug": "campus",      "parent": "City",     "content_types": _SITE_CT},
    {"name": "Building",    "slug": "building",    "parent": "City",     "content_types": _SITE_CT},
    {"name": "Floor",       "slug": "floor",       "parent": "Building", "content_types": _SITE_CT},
    {"name": "Store",       "slug": "store",       "parent": "City",     "content_types": _SITE_CT},
    {"name": "Data Center", "slug": "data-center", "parent": "City",     "content_types": _SITE_CT},
]

DEVICE_ROLES = [
    {"name": "access-switch",       "color": "2196f3"},
    {"name": "core-switch",         "color": "0d47a1"},
    {"name": "ap",                  "color": "4caf50"},
    {"name": "branch-fw",           "color": "f44336"},
    {"name": "wan-router",          "color": "ff9800"},
    {"name": "nac",                 "color": "9c27b0"},
    {"name": "distribution-switch", "color": "00bcd4"},
]

SERVICE_TAGS = [
    {"name": "WLAN",     "color": "4caf50"},
    {"name": "LAN",      "color": "2196f3"},
    {"name": "WAN",      "color": "ff9800"},
    {"name": "Firewall", "color": "f44336"},
    {"name": "NAC",      "color": "9c27b0"},
]

CUSTOM_FIELD = {
    "name":          "industry_vertical",
    "label":         "Industry Vertical",
    "type":          "select",
    "content_types": ["tenancy.tenant"],
    "choices": [
        "Retail", "Healthcare", "Enterprise",
        "Education", "Manufacturing", "Hospitality", "Finance"
    ]
}


# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(endpoint, params=None):
    r = requests.get(
        f'{URL}/api/{endpoint}/',
        headers=HEADERS,
        params=params,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def api_post(endpoint, data):
    r = requests.post(
        f'{URL}/api/{endpoint}/',
        headers=HEADERS,
        json=data,
        timeout=10
    )
    return r

def exists(endpoint, name):
    """
    Check if an object exists by name.
    Falls back to fetching all results and searching if name filter
    returns 400 (not supported on this endpoint).
    """
    r = requests.get(
        f'{URL}/api/{endpoint}/',
        headers=HEADERS,
        params={'name': name},
        timeout=10
    )
    if r.status_code == 400:
        # name filter not supported — fetch all and match manually
        r = requests.get(
            f'{URL}/api/{endpoint}/',
            headers=HEADERS,
            params={'limit': 200},
            timeout=10
        )
    if not r.ok:
        return False, None
    data = r.json()
    for obj in data.get('results', []):
        if obj.get('name') == name:
            return True, obj
    return False, None

def get_id(endpoint, name):
    found, obj = exists(endpoint, name=name)
    return obj['id'] if found else None

def get_role_content_types():
    """
    Device roles (access-switch, core-switch, ap, branch-fw, wan-router,
    nac, distribution-switch) are always dcim.device. Hardcoded directly --
    do NOT detect from existing extras/roles data: on a fresh DB, Nautobot's
    own built-in system roles (unrelated to device roles) can be returned
    first by extras/roles/?limit=1 and carry a different content_type,
    silently creating device roles with the wrong content_type.
    """
    return ["dcim.device"]


# ── Create functions ──────────────────────────────────────────────────────────

def create_tenant_groups(dry_run, results):
    print("\n── Tenant groups ────────────────────────────────────")
    for tg in TENANT_GROUPS:
        found, _ = exists('tenancy/tenant-groups', name=tg['name'])
        if found:
            print(f"  SKIP  {tg['name']} (already exists)")
            results.append([tg['name'], 'Tenant Group', 'skipped'])
        elif dry_run:
            print(f"  DRY   {tg['name']} (would create)")
            results.append([tg['name'], 'Tenant Group', 'would create'])
        else:
            r = api_post('tenancy/tenant-groups', tg)
            if r.status_code == 201:
                print(f"  OK    {tg['name']}")
                results.append([tg['name'], 'Tenant Group', 'created'])
            else:
                print(f"  FAIL  {tg['name']} — {r.status_code}: {r.text[:120]}")
                results.append([tg['name'], 'Tenant Group', f'FAILED {r.status_code}'])


def create_manufacturers(dry_run, results):
    print("\n── Manufacturers (from vendor_matrix) ───────────────")
    for slug, label in get_all_manufacturers().items():
        found, _ = exists('dcim/manufacturers', name=label)
        if found:
            print(f"  SKIP  {label} (already exists)")
            results.append([label, 'Manufacturer', 'skipped'])
        elif dry_run:
            print(f"  DRY   {label} (would create)")
            results.append([label, 'Manufacturer', 'would create'])
        else:
            r = api_post('dcim/manufacturers', {"name": label, "slug": slug})
            if r.status_code == 201:
                print(f"  OK    {label}")
                results.append([label, 'Manufacturer', 'created'])
            else:
                print(f"  FAIL  {label} — {r.status_code}: {r.text[:120]}")
                results.append([label, 'Manufacturer', f'FAILED {r.status_code}'])


def create_platforms(dry_run, results):
    print("\n── Platforms (from vendor_matrix) ───────────────────")
    mfr_map = get_all_manufacturers()  # slug → label

    for slug, data in get_all_platforms().items():
        found, _ = exists('dcim/platforms', name=data['label'])
        if found:
            print(f"  SKIP  {slug} (already exists)")
            results.append([slug, 'Platform', 'skipped'])
            continue

        # Look up manufacturer by its label
        mfr_label = mfr_map.get(data['manufacturer_slug'])
        mfr_id = get_id('dcim/manufacturers', name=mfr_label) if mfr_label else None

        if not mfr_id and not dry_run:
            print(f"  FAIL  {slug} — manufacturer '{mfr_label}' not found in Nautobot")
            results.append([slug, 'Platform', 'FAILED no manufacturer'])
            continue

        if dry_run:
            print(f"  DRY   {slug} → {data['label']} / {data['manufacturer_slug']}")
            results.append([slug, 'Platform', 'would create'])
        else:
            payload = {
                "name":          data['label'],
                "slug":          slug,
                "manufacturer":  mfr_id,
                "napalm_driver": data.get('napalm_driver') or ''
            }
            r = api_post('dcim/platforms', payload)
            if r.status_code == 201:
                print(f"  OK    {slug}")
                results.append([slug, 'Platform', 'created'])
            else:
                print(f"  FAIL  {slug} — {r.status_code}: {r.text[:120]}")
                results.append([slug, 'Platform', f'FAILED {r.status_code}'])


def create_location_types(dry_run, results):
    print("\n── Location types (6-level hierarchy) ───────────────")
    lt_id_cache = {}

    for lt in LOCATION_TYPES:
        found, obj = exists('dcim/location-types', name=lt['name'])
        if found:
            print(f"  SKIP  {lt['name']} (already exists)")
            lt_id_cache[lt['name']] = obj['id']
            results.append([lt['name'], 'Location Type', 'skipped'])
            continue

        payload = {
            "name":          lt['name'],
            "slug":          lt['slug'],
            "content_types": lt.get('content_types', []),
        }

        if lt['parent']:
            parent_id = (
                lt_id_cache.get(lt['parent'])
                or get_id('dcim/location-types', name=lt['parent'])
            )
            if parent_id:
                payload['parent'] = parent_id
            elif not dry_run:
                print(f"  FAIL  {lt['name']} — parent '{lt['parent']}' not found")
                results.append([lt['name'], 'Location Type', 'FAILED no parent'])
                continue

        if dry_run:
            print(f"  DRY   {lt['name']} (parent: {lt['parent'] or 'none'})")
            results.append([lt['name'], 'Location Type', 'would create'])
        else:
            r = api_post('dcim/location-types', payload)
            if r.status_code == 201:
                lt_id_cache[lt['name']] = r.json()['id']
                print(f"  OK    {lt['name']}")
                results.append([lt['name'], 'Location Type', 'created'])
            else:
                print(f"  FAIL  {lt['name']} — {r.status_code}: {r.text[:120]}")
                results.append([lt['name'], 'Location Type', f'FAILED {r.status_code}'])


def create_device_roles(dry_run, results):
    print("\n── Device roles (extras/roles) ──────────────────────")
    content_types = get_role_content_types()
    print(f"  Using content_types: {content_types}")

    for role in DEVICE_ROLES:
        found, _ = exists('extras/roles', name=role['name'])
        if found:
            print(f"  SKIP  {role['name']} (already exists)")
            results.append([role['name'], 'Device Role', 'skipped'])
        elif dry_run:
            print(f"  DRY   {role['name']} (would create)")
            results.append([role['name'], 'Device Role', 'would create'])
        else:
            payload = {
                "name":          role['name'],
                "color":         role['color'],
                "content_types": content_types
            }
            r = api_post('extras/roles', payload)
            if r.status_code == 201:
                print(f"  OK    {role['name']}")
                results.append([role['name'], 'Device Role', 'created'])
            else:
                print(f"  FAIL  {role['name']} — {r.status_code}: {r.text[:120]}")
                results.append([role['name'], 'Device Role', f'FAILED {r.status_code}'])


def create_tags(dry_run, results):
    print("\n── Service tags ─────────────────────────────────────")
    for tag in SERVICE_TAGS:
        found, _ = exists('extras/tags', name=tag['name'])
        if found:
            print(f"  SKIP  {tag['name']} (already exists)")
            results.append([tag['name'], 'Tag', 'skipped'])
        elif dry_run:
            print(f"  DRY   {tag['name']} (would create)")
            results.append([tag['name'], 'Tag', 'would create'])
        else:
            r = api_post('extras/tags', {**tag, "content_types": []})
            if r.status_code == 201:
                print(f"  OK    {tag['name']}")
                results.append([tag['name'], 'Tag', 'created'])
            else:
                print(f"  FAIL  {tag['name']} — {r.status_code}: {r.text[:120]}")
                results.append([tag['name'], 'Tag', f'FAILED {r.status_code}'])


def create_custom_field(dry_run, results):
    print("\n── Custom field (industry_vertical) ─────────────────")

    # custom-fields does not support name filter — fetch all and search
    r = requests.get(
        f'{URL}/api/extras/custom-fields/',
        headers=HEADERS,
        params={'limit': 200},
        timeout=10
    )
    existing_names = [cf['key'] for cf in r.json().get('results', [])]

    if CUSTOM_FIELD['name'] in existing_names:
        print(f"  SKIP  {CUSTOM_FIELD['name']} (already exists)")
        results.append([CUSTOM_FIELD['name'], 'Custom Field', 'skipped'])
        return

    if dry_run:
        print(f"  DRY   {CUSTOM_FIELD['name']} (would create)")
        results.append([CUSTOM_FIELD['name'], 'Custom Field', 'would create'])
        return

    payload = {
        "name":          CUSTOM_FIELD['name'],
        "label":         CUSTOM_FIELD['label'],
        "type":          CUSTOM_FIELD['type'],
        "content_types": CUSTOM_FIELD['content_types'],
    }
    r = api_post('extras/custom-fields', payload)
    if r.status_code == 201:
        cf_id = r.json()['id']
        print(f"  OK    {CUSTOM_FIELD['name']} (id: {cf_id})")
        results.append([CUSTOM_FIELD['name'], 'Custom Field', 'created'])

        print(f"        Adding {len(CUSTOM_FIELD['choices'])} choices...")
        for choice in CUSTOM_FIELD['choices']:
            rc = api_post('extras/custom-field-choices', {
                "custom_field": cf_id,
                "value":        choice
            })
            status = "OK" if rc.status_code == 201 else f"FAIL {rc.status_code}: {rc.text[:60]}"
            print(f"          {status}  {choice}")
    else:
        print(f"  FAIL  {CUSTOM_FIELD['name']} — {r.status_code}: {r.text[:120]}")
        results.append([CUSTOM_FIELD['name'], 'Custom Field', f'FAILED {r.status_code}'])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Bootstrap Nautobot base objects')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    args = parser.parse_args()

    mode = "DRY RUN — no changes will be made" if args.dry_run else "LIVE RUN"
    print(f"\n{'='*60}")
    print(f"  bootstrap_nautobot.py  [{mode}]")
    print(f"  Target: {URL}")
    print(f"{'='*60}")

    results = []
    create_tenant_groups(args.dry_run, results)
    create_manufacturers(args.dry_run, results)
    create_platforms(args.dry_run, results)
    create_location_types(args.dry_run, results)
    create_device_roles(args.dry_run, results)
    create_tags(args.dry_run, results)
    create_custom_field(args.dry_run, results)

    created = sum(1 for r in results if r[2] in ('created', 'would create'))
    skipped = sum(1 for r in results if r[2] == 'skipped')
    failed  = sum(1 for r in results if r[2].startswith('FAILED'))

    print(f"\n{'='*60}")
    print(f"  Summary: {created} {'would create' if args.dry_run else 'created'}"
          f" | {skipped} skipped | {failed} failed")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("Run without --dry-run to apply changes.\n")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
