"""
onboard_cli.py
Full onboarding orchestrator — terminal-based Block 1 through Block 6.
Tests all checkpoints end-to-end before wiring to Slack.

Usage:
    python3 onboard_cli.py
    python3 onboard_cli.py --tenant acme-retail-ltd --site Acme-BLR-03
    python3 onboard_cli.py --dry-run
"""

import sys
import os
import json
import argparse
import subprocess
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

URL     = os.getenv('NAUTOBOT_URL')
TOKEN   = os.getenv('NAUTOBOT_TOKEN')
HEADERS = {
    'Authorization': f'Token {TOKEN}',
    'Content-Type':  'application/json',
    'Accept':        'application/json'
}

LAB_DIR      = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(LAB_DIR, 'profiles')

sys.path.insert(0, LAB_DIR)
from vendor_matrix import VENDOR_MATRIX, get_enabled_vendors, get_device_types_for_vendor, get_access_methods


# ── Terminal helpers ───────────────────────────────────────────────────────────

def banner(title, char='=', width=65):
    print(f"\n{char*width}")
    print(f"  {title}")
    print(f"{char*width}")

def section(title):
    print(f"\n{'─'*65}")
    print(f"  {title}")
    print(f"{'─'*65}")

def cp(number, title):
    print(f"\n{'━'*65}")
    print(f"  ✅  CHECKPOINT {number} — {title}")
    print(f"{'━'*65}")

def ok(msg):    print(f"  ✅  {msg}")
def fail(msg):  print(f"  ❌  {msg}")
def warn(msg):  print(f"  ⚠️   {msg}")
def info(msg):  print(f"  ℹ️   {msg}")
def step(msg):  print(f"\n  →  {msg}")

def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"\n  {prompt}{suffix}: ").strip()
    return val if val else (default or '')

def choose(prompt, options, default=None):
    print(f"\n  {prompt}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if opt == default else ""
        print(f"    {i}. {opt}{marker}")
    while True:
        val = input(f"  Choice [1-{len(options)}]: ").strip()
        if not val and default:
            return default
        try:
            idx = int(val) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(options)}")

def multichoose(prompt, options):
    print(f"\n  {prompt}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    print("  Enter numbers separated by commas (e.g. 1,3) or 'all'")
    while True:
        val = input("  Choice: ").strip().lower()
        if val == 'all':
            return options[:]
        try:
            indices = [int(x.strip()) - 1 for x in val.split(',')]
            selected = [options[i] for i in indices if 0 <= i < len(options)]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("  Invalid selection — try again")

def confirm(prompt, default='y'):
    val = input(f"\n  {prompt} [{'Y/n' if default=='y' else 'y/N'}]: ").strip().lower()
    if not val:
        return default == 'y'
    return val in ('y', 'yes')


# ── Nautobot helpers ──────────────────────────────────────────────────────────

def fetch_all(endpoint):
    results, url = [], f'{URL}/api/{endpoint}/'
    while url:
        r = requests.get(url, headers=HEADERS, params={'limit': 200}, timeout=15)
        if not r.ok:
            return []
        data = r.json()
        results.extend(data.get('results', []))
        url = data.get('next')
    return results

def natural_to_slug(ns):
    if not ns:
        return ''
    parts = ns.rsplit('_', 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else ns

def get_tenants():
    tenants = fetch_all('tenancy/tenants')
    return [{'name': t['name'], 'slug': natural_to_slug(t['natural_slug'])} for t in tenants]

def run_script(script, args, dry_run=False):
    """Run a lab script as subprocess, stream output."""
    cmd = [sys.executable, os.path.join(LAB_DIR, script)] + args
    if dry_run:
        cmd.append('--dry-run')
    print()
    result = subprocess.run(cmd, cwd=LAB_DIR)
    return result.returncode == 0


# ── Block 1: Tenant resolution ────────────────────────────────────────────────

def block1_new_tenant(dry_run):
    section("BLOCK 1 — New tenant setup")

    # Basic info
    name = ask("Customer name")
    if not name:
        fail("Name required"); return None

    group    = choose("Tenant group", ["PBS", "MS"])
    vertical = choose("Industry vertical",
                      ["Retail", "Healthcare", "Enterprise",
                       "Education", "Manufacturing", "Hospitality", "Finance"])

    # Platform interview
    section("Platform discovery — what does this customer have?")

    device_types = multichoose(
        "Device types (select all that apply):",
        ["Switches", "Access Points", "Firewalls", "NAC"]
    )

    dtype_map = {
        "Switches":        "switch",
        "Access Points":   "ap",
        "Firewalls":       "firewall",
        "NAC":             "nac",
    }

    vendor_map = {
        "switch":   ["aruba", "juniper", "cisco", "fortinet"],
        "ap":       ["aruba", "juniper", "cisco", "fortinet"],
        "firewall": ["juniper", "cisco", "fortinet"],
        "nac":      ["aruba"],
    }

    selections = {}

    for dtype_label in device_types:
        dtype = dtype_map[dtype_label]
        vendors = multichoose(
            f"{dtype_label} — which vendors?",
            vendor_map.get(dtype, [])
        )

        for vendor in vendors:
            if vendor not in selections:
                selections[vendor] = {}

            # Access method selection
            available_methods = get_access_methods(vendor, dtype)
            if not available_methods:
                continue

            if len(available_methods) == 1:
                method = list(available_methods.keys())[0]
                info(f"{vendor} {dtype}: using {available_methods[method]['label']}")
                selections[vendor][dtype] = [method]
            else:
                method_labels = [
                    f"{k} ({v['label']})" for k, v in available_methods.items()
                ]
                chosen = multichoose(
                    f"{vendor} {dtype_label} — access method?",
                    method_labels
                )
                chosen_keys = []
                for c in chosen:
                    for k, v in available_methods.items():
                        if k in c or v['label'] in c:
                            chosen_keys.append(k)
                            break
                selections[vendor][dtype] = chosen_keys if chosen_keys else [list(available_methods.keys())[0]]

    if not selections:
        fail("No platforms selected"); return None

    # Build profile
    import re
    slug = re.sub(r'[^\w\s-]', '', name.lower().strip())
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')

    profile = {
        "name":       name,
        "slug":       slug,
        "group":      group,
        "vertical":   vertical,
        "selections": selections,
    }

    # Preview
    section("Tenant creation preview")
    print(f"  Name     : {name}")
    print(f"  Slug     : {slug}")
    print(f"  Group    : {group}")
    print(f"  Vertical : {vertical}")
    print(f"  Platforms:")
    for vendor, dtypes in selections.items():
        for dtype, methods in dtypes.items():
            print(f"    {vendor:10} {dtype:10} → {', '.join(methods)}")

    if not confirm("Create this tenant?"):
        info("Cancelled"); return None

    # Save profile
    profile_path = os.path.join(PROFILES_DIR, f"{slug}.json")
    os.makedirs(PROFILES_DIR, exist_ok=True)
    with open(profile_path, 'w') as f:
        json.dump(profile, f, indent=2)
    ok(f"Profile saved: {profile_path}")

    # Run create_tenant.py
    step("Running create_tenant.py...")
    success = run_script('create_tenant.py',
                         ['--profile', profile_path], dry_run=dry_run)
    if not success:
        fail("create_tenant.py failed — check output above")
        return None

    cp(1, "Tenant created")
    ok(f"Tenant: {name} ({slug})")
    ok(f"Secrets groups and integrations created from profile")

    return profile


def block1_existing_tenant():
    section("BLOCK 1 — Existing tenant")

    tenants = get_tenants()
    if not tenants:
        fail("No tenants found in Nautobot"); return None

    names = [f"{t['name']} ({t['slug']})" for t in tenants]
    chosen = choose("Select tenant:", names)
    idx    = names.index(chosen)
    tenant = tenants[idx]

    ok(f"Selected: {tenant['name']}")

    # Load profile if exists
    profile_path = os.path.join(PROFILES_DIR, f"{tenant['slug']}.json")
    if os.path.exists(profile_path):
        with open(profile_path) as f:
            profile = json.load(f)
        info(f"Profile loaded: {profile_path}")
        print(f"  Registered platforms:")
        for vendor, dtypes in profile.get('selections', {}).items():
            for dtype, methods in dtypes.items():
                print(f"    {vendor:10} {dtype:10} → {', '.join(methods)}")
    else:
        warn(f"No profile found at {profile_path}")
        warn("Platform info unavailable — credential check may be incomplete")
        profile = {"name": tenant['name'], "slug": tenant['slug'], "selections": {}}

    return profile


def block1(dry_run):
    banner("BLOCK 1 — Tenant Resolution")

    choice = choose("New or existing customer?",
                    ["New customer", "Existing customer"])

    if choice == "New customer":
        return block1_new_tenant(dry_run)
    else:
        return block1_existing_tenant()


# ── Credential checkpoint ─────────────────────────────────────────────────────

def checkpoint2_credentials(profile, dry_run):
    section("CHECKPOINT 2 — Credential verification")
    slug         = profile.get('slug', '')
    profile_path = os.path.join(PROFILES_DIR, f"{slug}.json")

    if not os.path.exists(profile_path):
        warn(f"No profile at {profile_path} — skipping credential check")
        return True

    step("Running credential_checker.py...")
    result = subprocess.run(
        [sys.executable, os.path.join(LAB_DIR, 'credential_checker.py'),
         '--profile', profile_path],
        cwd=LAB_DIR
    )

    if result.returncode != 0:
        fail("Credentials not ready")
        print(f"\n  Fill credentials in: {PROFILES_DIR}/{slug}.env")
        print(f"  Then restart nautobot-worker:")
        print(f"  sudo systemctl restart nautobot-worker")
        if not confirm("Credentials filled now? Re-check?"):
            return False
        # Re-run check
        result2 = subprocess.run(
            [sys.executable, os.path.join(LAB_DIR, 'credential_checker.py'),
             '--profile', profile_path],
            cwd=LAB_DIR
        )
        if result2.returncode != 0:
            fail("Credentials still not ready — cannot proceed to onboarding")
            return False

    cp(2, "Credentials verified")
    return True


# ── Block 2: Location Q&A ─────────────────────────────────────────────────────

def block2_location():
    banner("BLOCK 2 — Location Q&A")
    info("Collecting 6-level location hierarchy for this site")

    # Check existing locations for hints
    locs = fetch_all('dcim/locations')
    existing = [l['name'] for l in locs]

    region  = ask("Region",  "Asia-Pacific")
    country = ask("Country", "India")
    state   = ask("State / Province")
    city    = ask("City")
    name    = ask("Site name (e.g. Acme-BLR-03)")
    stype   = choose("Site type",
                     ["Branch", "Campus", "Building", "Floor", "Store", "Data Center"])

    # Check what already exists
    section("Location hierarchy preview")
    hierarchy = [
        ("Region",  region),
        ("Country", country),
        ("State",   state),
        ("City",    city),
        (stype,     name),
    ]

    for level, loc_name in hierarchy:
        status = "✅ exists" if loc_name in existing else "🔨 will create"
        print(f"  {level:12} {loc_name:30} {status}")

    if not confirm("Confirm location hierarchy?"):
        info("Re-entering location details...")
        return block2_location()

    return {
        "region":    region,
        "country":   country,
        "state":     state,
        "city":      city,
        "site_name": name,
        "site_type": stype,
    }


# ── Block 3: Device data ──────────────────────────────────────────────────────

def block3_device_data(profile, site_config):
    banner("BLOCK 3 — Device Data")

    tenant_slug = profile.get('slug', '')
    site_name   = site_config['site_name'].lower().replace(' ', '-')

    info(f"Web portal available at: http://172.32.253.22:8081")
    info(f"Or provide a CSV path directly")

    choice = choose("How to enter device data?",
                    ["Use web portal (already open in browser)",
                     "Provide CSV file path",
                     "Use existing nautobot_ready CSV (skip prepare)"])

    if choice.startswith("Use web portal"):
        # Save site config for portal context
        sc_path = os.path.join(LAB_DIR, f"site_config_{tenant_slug}.json")
        sc_data = dict(site_config)
        sc_data['tenant_slug'] = tenant_slug
        sc_data['tenant_name'] = profile.get('name', '')
        with open(sc_path, 'w') as f:
            json.dump(sc_data, f, indent=2)
        ok(f"Site config saved: {sc_path}")
        info(f"Open http://172.32.253.22:8081 and complete device entry")
        info(f"Select tenant: {profile.get('name', '')} and site: {site_config['site_name']}")
        input("\n  Press Enter when you have completed device entry in the portal...")
        # Look for the generated ready CSV
        ready_csv = os.path.join(LAB_DIR, f"nautobot_ready_{site_name}.csv")
        if not os.path.exists(ready_csv):
            csv_path = ask("Portal CSV not found — enter full path to nautobot_ready CSV")
            return csv_path if os.path.exists(csv_path) else None
        return ready_csv

    elif choice.startswith("Provide CSV"):
        csv_path = ask("Engineer CSV path",
                       f"tests/engineer_{tenant_slug}.csv")
        if not os.path.exists(csv_path):
            fail(f"File not found: {csv_path}"); return None

        # Save site config
        sc_path = os.path.join(LAB_DIR, f"site_config_{tenant_slug}.json")
        sc_data = dict(site_config)
        sc_data['tenant_slug'] = tenant_slug
        with open(sc_path, 'w') as f:
            json.dump(sc_data, f, indent=2)

        step("Running nautobot_prepare.py...")
        success = run_script('nautobot_prepare.py',
                             ['--csv', csv_path, '--site-config', sc_path])
        if not success:
            fail("Prepare failed — fix CSV errors and re-run")
            return None

        ready_csv = os.path.join(LAB_DIR, f"nautobot_ready_{site_name}.csv")
        if not os.path.exists(ready_csv):
            fail(f"Ready CSV not found: {ready_csv}"); return None
        return ready_csv

    else:
        csv_path = ask("nautobot_ready CSV path")
        return csv_path if os.path.exists(csv_path) else None


# ── Checkpoint 3: Device summary ──────────────────────────────────────────────

def checkpoint3_device_summary(ready_csv):
    import csv as csv_mod
    section("CHECKPOINT 3 — Device summary")

    with open(ready_csv) as f:
        rows = list(csv_mod.DictReader(f))

    print(f"\n  CSV     : {ready_csv}")
    print(f"  Devices : {len(rows)}")
    print()

    # Group by role/vendor
    from collections import Counter
    by_role   = Counter(r['role']   for r in rows)
    by_vendor = Counter(r['vendor'] for r in rows)

    print("  By role:")
    for role, count in sorted(by_role.items()):
        print(f"    {role:25} {count}")

    print("  By vendor:")
    for vendor, count in sorted(by_vendor.items()):
        print(f"    {vendor:25} {count}")

    print()
    # Show first few rows
    print("  Devices:")
    for r in rows[:8]:
        print(f"    {r['device_name']:18} {r['vendor']:8} {r['role']:16} {r['ip']}")
    if len(rows) > 8:
        print(f"    ... and {len(rows)-8} more")

    return confirm(f"\n  Proceed to onboard {len(rows)} devices?")


# ── Block 5: Site onboard ─────────────────────────────────────────────────────

def block5_onboard(ready_csv, dry_run):
    banner("BLOCK 5 — Site Onboard")
    step(f"Running nautobot_onboard_v2.py on {os.path.basename(ready_csv)}...")
    return run_script('nautobot_onboard_v2.py', ['--csv', ready_csv], dry_run=dry_run)


def checkpoint4_onboard_result(ready_csv):
    import csv as csv_mod
    cp(4, "Onboard result")

    with open(ready_csv) as f:
        rows = list(csv_mod.DictReader(f))

    tenant_slug = rows[0].get('tenant_slug', '')
    site_name   = rows[0].get('site_name', '')

    # Verify devices in Nautobot
    tenants = fetch_all('tenancy/tenants')
    tenant_id = next(
        (natural_to_slug(t['natural_slug']) == tenant_slug and t['id']
         for t in tenants if natural_to_slug(t['natural_slug']) == tenant_slug),
        None
    )

    if tenant_id:
        # Get tenant UUID properly
        for t in tenants:
            if natural_to_slug(t['natural_slug']) == tenant_slug:
                tenant_id = t['id']
                break

        devices = fetch_all('dcim/devices')
        tenant_devices = [d for d in devices
                          if d.get('tenant', {}) and d['tenant']['id'] == tenant_id]

        ok(f"Tenant devices in Nautobot: {len(tenant_devices)}")

    ok(f"Site: {site_name}")
    ok(f"CSV devices: {len(rows)}")
    return True


# ── Block 6: Sync ─────────────────────────────────────────────────────────────

def block6_sync(profile, site_config, dry_run):
    banner("BLOCK 6 — Live Sync")

    tenant_slug = profile.get('slug', '')
    site_name   = site_config['site_name']

    info("Make sure tenant credentials are loaded in your shell:")
    info(f"  set -a && source {PROFILES_DIR}/{tenant_slug}.env && set +a")

    choice = choose("Run sync now?",
                    ["Yes — run full sync now",
                     "Yes — run switches only",
                     "Yes — run APs only",
                     "Skip — run manually later"])

    if choice.startswith("Skip"):
        info(f"Run manually: python3 sync_network_data.py --site {site_name} --tenant {tenant_slug}")
        return True

    cat_map = {
        "Yes — run full sync now":    "all",
        "Yes — run switches only":    "switches",
        "Yes — run APs only":         "aps",
    }
    category = cat_map.get(choice, "all")

    step(f"Running sync_network_data.py --category {category}...")
    return run_script('sync_network_data.py',
                      ['--site', site_name, '--tenant', tenant_slug,
                       '--category', category],
                      dry_run=dry_run)


def checkpoint5_sync_result(profile, site_config):
    cp(5, "Sync complete")
    tenant_slug = profile.get('slug', '')
    site_name   = site_config['site_name']

    # Check manifest
    import glob
    pattern = os.path.join(LAB_DIR, 'manifests',
                           f"sync_last_{tenant_slug}_{site_name}.json")
    if os.path.exists(pattern):
        with open(pattern) as f:
            m = json.load(f)
        ok(f"Success: {m.get('success', 0)}")
        if m.get('failed', 0):
            warn(f"Failed : {m['failed']} devices")
            info("Re-run failed: python3 sync_network_data.py "
                 f"--site {site_name} --tenant {tenant_slug} --failed-only")
        else:
            ok("All devices synced successfully")
    else:
        info("No sync manifest found — check sync output above")
    return True


# ── Completion report ─────────────────────────────────────────────────────────

def completion_report(profile, site_config, ready_csv):
    banner("ONBOARDING COMPLETE", char='█')
    import csv as csv_mod

    with open(ready_csv) as f:
        rows = list(csv_mod.DictReader(f))

    print(f"""
  Tenant   : {profile.get('name', '')} ({profile.get('slug', '')})
  Site     : {site_config['site_name']} ({site_config['site_type']})
  Location : {site_config['region']} → {site_config['country']} →
             {site_config['state']} → {site_config['city']}
  Devices  : {len(rows)}
  Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

  Next steps:
    Verify in Nautobot UI : {URL.replace('/api','')}/dcim/devices/
    Re-sync any time      : python3 sync_network_data.py \\
                              --site {site_config['site_name']} \\
                              --tenant {profile.get('slug', '')}
    Add another site      : python3 onboard_cli.py \\
                              --tenant {profile.get('slug', '')}
""")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Nautobot site onboarding orchestrator')
    parser.add_argument('--tenant',  help='Skip Block 1 — use existing tenant slug')
    parser.add_argument('--site',    help='Skip Block 2 — use existing site name')
    parser.add_argument('--dry-run', action='store_true', help='Dry-run all phases')
    args = parser.parse_args()

    banner("NAUTOBOT ONBOARDING ORCHESTRATOR", char='═')
    print(f"  Target : {URL}")
    print(f"  Mode   : {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    if args.dry_run:
        warn("DRY RUN — no changes will be written to Nautobot")

    # ── BLOCK 1 ───────────────────────────────────────────────────────────────
    if args.tenant:
        # Pre-selected tenant — load profile
        profile_path = os.path.join(PROFILES_DIR, f"{args.tenant}.json")
        if os.path.exists(profile_path):
            with open(profile_path) as f:
                profile = json.load(f)
            ok(f"Using tenant: {profile['name']}")
        else:
            fail(f"Profile not found: {profile_path}")
            sys.exit(1)
    else:
        profile = block1(args.dry_run)
        if not profile:
            fail("Block 1 failed — exiting")
            sys.exit(1)

    # ── CP2: Credentials ──────────────────────────────────────────────────────
    if not checkpoint2_credentials(profile, args.dry_run):
        fail("Credential check failed — fill .env file and retry")
        sys.exit(1)

    # ── BLOCK 2 ───────────────────────────────────────────────────────────────
    site_config = block2_location()
    site_config['tenant_slug'] = profile['slug']
    site_config['tenant_name'] = profile.get('name', '')

    # ── BLOCK 3 ───────────────────────────────────────────────────────────────
    ready_csv = block3_device_data(profile, site_config)
    if not ready_csv:
        fail("Block 3 failed — no valid device data")
        sys.exit(1)

    # ── CP3: Device summary ───────────────────────────────────────────────────
    if not checkpoint3_device_summary(ready_csv):
        info("Onboarding cancelled at device summary review")
        sys.exit(0)

    # ── BLOCK 5 ───────────────────────────────────────────────────────────────
    if not block5_onboard(ready_csv, args.dry_run):
        fail("Block 5 onboard failed — check output above")
        sys.exit(1)

    checkpoint4_onboard_result(ready_csv)

    # ── BLOCK 6 ───────────────────────────────────────────────────────────────
    block6_sync(profile, site_config, args.dry_run)
    checkpoint5_sync_result(profile, site_config)

    # ── Done ──────────────────────────────────────────────────────────────────
    completion_report(profile, site_config, ready_csv)


if __name__ == '__main__':
    main()
