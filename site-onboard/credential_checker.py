"""
credential_checker.py
Verifies all required env vars for a tenant are present
and non-empty in the tenant's .env file.

Usage:
    python3 credential_checker.py --profile profiles/acme-retail.json
    python3 credential_checker.py --profile profiles/acme-retail.json --show-values
    python3 credential_checker.py --list-tenants

Returns:
    exit 0 — all vars present and non-empty
    exit 1 — one or more vars missing or empty
"""

import sys
import os
import json
import argparse
import re
from tabulate import tabulate
from dotenv import load_dotenv, dotenv_values

LAB_PROFILES_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')
LAB_MANIFESTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifests')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vendor_matrix import get_env_vars, needs_enable_mode, VENDOR_MATRIX


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def load_profile(path):
    with open(path) as f:
        profile = json.load(f)
    if 'slug' not in profile:
        profile['slug'] = slugify(profile['name'])
    return profile


def derive_expected_vars(profile):
    """
    Derive the exact list of env vars expected for this tenant
    based on their selections in the profile.
    Same logic as create_tenant.py — single source of truth.
    """
    slug   = profile['slug']
    suffix = slug.upper().replace('-', '_')
    env_vars = set()

    for vendor, device_types in profile['selections'].items():
        for device_type, access_methods in device_types.items():
            for access_method in access_methods:
                for var in get_env_vars(vendor, device_type, access_method):
                    env_vars.add(f"{var}_{suffix}")
                if needs_enable_mode(vendor, device_type, access_method):
                    enable_var = (
                        VENDOR_MATRIX[vendor]['device_types'][device_type]
                        ['access_methods'][access_method].get('enable_env_var')
                    )
                    if enable_var:
                        env_vars.add(f"{enable_var}_{suffix}")

    return sorted(env_vars)


def read_env_file(env_path):
    """
    Read .env file and return dict of key → value.
    Uses dotenv_values which does NOT load into os.environ.
    """
    if not os.path.exists(env_path):
        return None
    return dotenv_values(env_path)


def mask_value(val):
    """Mask credential value for display — show first 2 chars only."""
    if not val:
        return ''
    if len(val) <= 4:
        return '*' * len(val)
    return val[:2] + '*' * (len(val) - 2)


# ── Core check ────────────────────────────────────────────────────────────────

def check_credentials(profile, show_values=False):
    slug     = profile['slug']
    env_path = os.path.join(LAB_PROFILES_DIR, f"{slug}.env")

    print(f"\n{'='*60}")
    print(f"  credential_checker.py")
    print(f"  Tenant   : {profile['name']} ({slug})")
    print(f"  Env file : {env_path}")
    print(f"{'='*60}")

    # ── Check env file exists ─────────────────────────────────
    if not os.path.exists(env_path):
        print(f"\n  ERROR  Env file not found: {env_path}")
        print(f"         Run create_tenant.py first to generate the template.\n")
        return False

    env_values   = read_env_file(env_path)
    expected     = derive_expected_vars(profile)
    rows         = []
    all_ok       = True
    missing      = []
    empty        = []
    present      = []

    print(f"\n  Checking {len(expected)} expected variables:\n")

    for var in expected:
        val = env_values.get(var, None)

        if val is None:
            status = 'MISSING'
            display = '—'
            all_ok = False
            missing.append(var)
        elif val.strip() == '':
            status = 'EMPTY'
            display = '(not filled)'
            all_ok = False
            empty.append(var)
        else:
            status = 'OK'
            display = mask_value(val) if not show_values else val
            present.append(var)

        rows.append([
            '✓' if status == 'OK' else '✗',
            var,
            status,
            display if status == 'OK' else ''
        ])

    print(tabulate(rows, headers=['', 'Variable', 'Status', 'Value'], tablefmt='simple'))

    # ── Summary ───────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  {len(present)} OK  |  {len(empty)} empty  |  {len(missing)} missing")

    if missing:
        print(f"\n  MISSING — these vars are not in the env file:")
        for v in missing:
            print(f"    {v}")
        print(f"\n  Fix: add them to {env_path}")

    if empty:
        print(f"\n  EMPTY — these vars exist but have no value:")
        for v in empty:
            print(f"    {v}")
        print(f"\n  Fix: fill values in {env_path}")

    if all_ok:
        print(f"\n  RESULT: ALL CREDENTIALS READY")
        print(f"          Safe to proceed with site onboarding.")
    else:
        print(f"\n  RESULT: NOT READY — fill credentials before onboarding.")

    print(f"{'='*60}\n")
    return all_ok


# ── List tenants ──────────────────────────────────────────────────────────────

def list_tenants():
    """Show all tenant profiles and their credential status."""
    profiles_dir = LAB_PROFILES_DIR
    json_files   = [
        f for f in os.listdir(profiles_dir)
        if f.endswith('.json') and not f.startswith('_')
    ]

    if not json_files:
        print("\n  No tenant profiles found in", profiles_dir)
        return

    rows = []
    for fname in sorted(json_files):
        path = os.path.join(profiles_dir, fname)
        try:
            profile  = load_profile(path)
            slug     = profile['slug']
            env_path = os.path.join(profiles_dir, f"{slug}.env")

            if not os.path.exists(env_path):
                status = 'NO ENV FILE'
            else:
                expected   = derive_expected_vars(profile)
                env_values = read_env_file(env_path)
                empty_vars = [
                    v for v in expected
                    if not env_values.get(v, '').strip()
                ]
                status = 'READY' if not empty_vars else f'{len(empty_vars)} vars empty'

            rows.append([
                profile['name'],
                slug,
                profile.get('group', '—'),
                profile.get('vertical', '—'),
                len(derive_expected_vars(profile)),
                status
            ])
        except Exception as e:
            rows.append([fname, '—', '—', '—', '—', f'ERROR: {e}'])

    print(f"\n{'='*60}")
    print(f"  Tenant profiles in {profiles_dir}")
    print(f"{'='*60}\n")
    print(tabulate(rows, headers=[
        'Tenant', 'Slug', 'Group', 'Vertical', 'Vars', 'Status'
    ], tablefmt='simple'))
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Verify tenant credentials are filled in env file'
    )
    parser.add_argument('--profile',      help='Path to tenant profile JSON')
    parser.add_argument('--show-values',  action='store_true',
                        help='Show actual credential values (use carefully)')
    parser.add_argument('--list-tenants', action='store_true',
                        help='List all tenant profiles and their credential status')
    args = parser.parse_args()

    if args.list_tenants:
        list_tenants()
        return

    if not args.profile:
        parser.print_help()
        sys.exit(1)

    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"\nERROR loading profile: {e}")
        sys.exit(1)

    ok = check_credentials(profile, show_values=args.show_values)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
