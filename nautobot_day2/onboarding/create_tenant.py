"""
create_tenant.py
Phase 3 — Creates per-tenant objects in Nautobot.
Reads tenant_profile.json — creates ONLY what that tenant needs.
Idempotent — skips objects that already exist.

Usage:
    python3 create_tenant.py --profile profiles/acme-retail.json --dry-run
    python3 create_tenant.py --profile profiles/acme-retail.json
"""

import sys
import json
import argparse
import os
import re
from datetime import datetime
from tabulate import tabulate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vendor_matrix import (
    get_secrets_group_prefix,
    get_external_integration_name,
    get_env_vars,
    needs_enable_mode,
    VENDOR_MATRIX
)
from client import NautobotClient

client = NautobotClient(env_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
URL = client.url

LAB_PROFILES_DIR  = os.environ.get("NAUTOBOT_DAY2_TENANTS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles'))
LAB_MANIFESTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifests')


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')

def api_post(endpoint, data):
    return client.post(endpoint, data)

def exists_by_name(endpoint, name):
    return client.find_by_name(endpoint, name)

def get_id_by_name(endpoint, name):
    return client.get_id_by_name(endpoint, name)


# ── Profile ───────────────────────────────────────────────────────────────────

def validate_profile(profile):
    """
    Ensure a profile dict has everything create_tenant needs, regardless of
    whether it came from a JSON file (CLI) or was built in-memory by a caller
    like the onboarding web app. Adds 'slug' if missing. Mutates and returns
    the same dict.
    """
    for field in ['name', 'group', 'vertical', 'selections']:
        if field not in profile:
            raise ValueError(f"Profile missing required field: '{field}'")
    if 'slug' not in profile:
        profile['slug'] = slugify(profile['name'])
    return profile


def load_profile(path):
    with open(path) as f:
        profile = json.load(f)
    return validate_profile(profile)


def run_create_tenant(profile, dry_run=False):
    """
    Programmatic entry point — the same work main() does, minus argparse,
    sys.exit, and CLI-only printing. Callers (e.g. the onboarding web app)
    pass a profile dict directly; this validates it, runs every creation
    step in order, and returns a structured summary instead of relying on
    stdout.

    Returns:
        {
          "profile": <validated profile dict, with 'slug' filled in>,
          "derived": <output of derive_objects()>,
          "results": [[name, kind, status], ...],
          "created": int, "skipped": int, "failed": int,
        }
    """
    profile = validate_profile(dict(profile))
    derived = derive_objects(profile)
    results = []

    create_tenant_record(profile, dry_run, results)
    create_namespace(profile, dry_run, results)
    create_secrets_groups(profile, derived, dry_run, results)
    create_external_integrations(profile, derived, dry_run, results)
    write_env_file(profile, derived, dry_run, results)

    if not dry_run:
        save_manifest(profile, derived)

    created = sum(1 for r in results if r[2] in ('created', 'would create'))
    skipped = sum(1 for r in results if r[2] == 'skipped')
    failed  = sum(1 for r in results if r[2].startswith('FAILED'))

    return {
        "profile": profile,
        "derived": derived,
        "results": results,
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }


# ── Derive exact objects needed from profile selections ───────────────────────

def derive_objects(profile):
    """
    Walk the selections and derive:
      - secrets_groups  : { prefix → full_name }
      - integrations    : { full_name → linked_sg_name }
      - env_vars        : sorted list of var names for this tenant
      - prefix_env_vars : { prefix → [base_var_name, ...] } — feeds real
                          Nautobot Secret objects for each group (see
                          create_secrets_groups)
    Nothing is hardcoded — everything comes from vendor_matrix.
    """
    slug   = profile['slug']
    suffix = slug.upper().replace('-', '_')

    secrets_groups   = {}   # keyed by prefix to auto-deduplicate
    integrations     = {}   # keyed by full name to auto-deduplicate
    env_vars         = set()
    prefix_env_vars  = {}   # keyed by prefix → ordered, deduped base var names

    for vendor, device_types in profile['selections'].items():
        for device_type, access_methods in device_types.items():
            for access_method in access_methods:

                # ── Secrets group ──────────────────────────────────────
                prefix = get_secrets_group_prefix(vendor, device_type, access_method)
                if prefix and prefix not in secrets_groups:
                    secrets_groups[prefix] = f"{prefix}-{slug}"

                # ── External integration ───────────────────────────────
                int_name = get_external_integration_name(vendor, device_type, access_method)
                if int_name:
                    full_int = f"{int_name} - {slug}"
                    if full_int not in integrations:
                        sg_name = f"{prefix}-{slug}" if prefix else None
                        integrations[full_int] = sg_name

                # ── Env vars ───────────────────────────────────────────
                base_vars = get_env_vars(vendor, device_type, access_method)
                for var in base_vars:
                    env_vars.add(f"{var}_{suffix}")
                if prefix:
                    bucket = prefix_env_vars.setdefault(prefix, [])
                    for var in base_vars:
                        if var not in bucket:
                            bucket.append(var)

                # ── Enable mode extra var ──────────────────────────────
                if needs_enable_mode(vendor, device_type, access_method):
                    enable_var = (
                        VENDOR_MATRIX[vendor]['device_types'][device_type]
                        ['access_methods'][access_method].get('enable_env_var')
                    )
                    if enable_var:
                        env_vars.add(f"{enable_var}_{suffix}")
                        if prefix:
                            bucket = prefix_env_vars.setdefault(prefix, [])
                            if enable_var not in bucket:
                                bucket.append(enable_var)

    return {
        'secrets_groups':  secrets_groups,
        'integrations':    integrations,
        'env_vars':        sorted(env_vars),
        'prefix_env_vars': prefix_env_vars,
    }


# ── Create functions ──────────────────────────────────────────────────────────

def create_tenant_record(profile, dry_run, results):
    print("\n── Tenant record ────────────────────────────────────")
    name = profile['name']

    found, obj = exists_by_name('tenancy/tenants', name)
    if found:
        print(f"  SKIP  {name} (already exists)")
        results.append([name, 'Tenant', 'skipped'])
        return obj['id']

    group_id = get_id_by_name('tenancy/tenant-groups', profile['group'])
    if not group_id and not dry_run:
        print(f"  FAIL  {name} — group '{profile['group']}' not found")
        results.append([name, 'Tenant', 'FAILED no group'])
        return None

    if dry_run:
        print(f"  DRY   {name}")
        print(f"        slug     : {profile['slug']}")
        print(f"        group    : {profile['group']}")
        print(f"        vertical : {profile['vertical']}")
        results.append([name, 'Tenant', 'would create'])
        return None

    r = api_post('tenancy/tenants', {
        "name":         name,
        "slug":         profile['slug'],
        "tenant_group": group_id,
        "custom_fields": {"industry_vertical": profile['vertical']}
    })
    if r.status_code == 201:
        tid = r.json()['id']
        print(f"  OK    {name} (id: {tid})")
        results.append([name, 'Tenant', 'created'])
        return tid
    else:
        print(f"  FAIL  {name} — {r.status_code}: {r.text[:120]}")
        results.append([name, 'Tenant', f'FAILED {r.status_code}'])
        return None


def create_namespace(profile, dry_run, results):
    print("\n── IP namespace ─────────────────────────────────────")
    name = profile['slug']

    found, obj = exists_by_name('ipam/namespaces', name)
    if found:
        print(f"  SKIP  {name} (already exists)")
        results.append([name, 'IP Namespace', 'skipped'])
        return obj['id']

    if dry_run:
        print(f"  DRY   {name}")
        results.append([name, 'IP Namespace', 'would create'])
        return None

    r = api_post('ipam/namespaces', {
        "name":        name,
        "description": f"IP namespace for {profile['name']}"
    })
    if r.status_code == 201:
        nid = r.json()['id']
        print(f"  OK    {name} (id: {nid})")
        results.append([name, 'IP Namespace', 'created'])
        return nid
    else:
        print(f"  FAIL  {name} — {r.status_code}: {r.text[:120]}")
        results.append([name, 'IP Namespace', f'FAILED {r.status_code}'])
        return None


def _infer_secret_type(var_base):
    """
    Guess a Nautobot SecretsGroupAssociation secret_type from an env var's
    base name (e.g. 'ARUBA_SSH_PASS' -> 'password'). Returns None for
    vars that are plain config rather than a credential (e.g. BASE_URL) —
    those stay in the tenant .env file only, they don't belong in Secrets.
    """
    v = var_base.upper()
    if v.endswith('_ID'):
        return 'key'
    if 'PASS' in v:
        return 'password'
    if 'USER' in v:
        return 'username'
    if 'TOKEN' in v:
        return 'token'
    if 'SECRET' in v:
        return 'secret'
    return None


def get_or_create_secret(var_name, dry_run, results):
    """Find or create a Nautobot Secret backed by the environment-variable
    provider, so it resolves to the real value at runtime without Nautobot
    ever storing the value itself."""
    found, obj = exists_by_name('extras/secrets', var_name)
    if found:
        return obj['id'], False

    if dry_run:
        return None, True

    r = api_post('extras/secrets', {
        "name":     var_name,
        "provider": "environment-variable",
        "parameters": {"variable": var_name},
    })
    if r.status_code == 201:
        return r.json()['id'], True
    print(f"  FAIL  secret {var_name} — {r.status_code}: {r.text[:120]}")
    results.append([var_name, 'Secret', f'FAILED {r.status_code}'])
    return None, False


def association_exists(sg_id, access_type, secret_type):
    r = client.get('extras/secrets-groups-associations', params={
        'secrets_group': sg_id, 'access_type': access_type,
        'secret_type': secret_type, 'limit': 1,
    })
    return r.ok and r.json().get('count', 0) > 0


def create_secrets_groups(profile, derived, dry_run, results):
    print("\n── Secrets groups ───────────────────────────────────")
    if not derived['secrets_groups']:
        print("  (none needed for this tenant's selections)")
        return

    suffix = profile['slug'].upper().replace('-', '_')

    for prefix, group_name in derived['secrets_groups'].items():
        found, sg_obj = exists_by_name('extras/secrets-groups', group_name)
        if found:
            print(f"  SKIP  {group_name}")
            results.append([group_name, 'Secrets Group', 'skipped'])
        elif dry_run:
            print(f"  DRY   {group_name}")
            results.append([group_name, 'Secrets Group', 'would create'])
            sg_obj = None
        else:
            r = api_post('extras/secrets-groups', {"name": group_name})
            if r.status_code == 201:
                sg_obj = r.json()
                print(f"  OK    {group_name}")
                results.append([group_name, 'Secrets Group', 'created'])
            else:
                print(f"  FAIL  {group_name} — {r.status_code}: {r.text[:120]}")
                results.append([group_name, 'Secrets Group', f'FAILED {r.status_code}'])
                sg_obj = None

        if not sg_obj:
            continue

        # Wire real, env-var-backed Secrets into the group so
        # SecretsGroup.get_secret_value() actually resolves a credential
        # at runtime instead of the group being an empty shell.
        for var_base in derived['prefix_env_vars'].get(prefix, []):
            secret_type = _infer_secret_type(var_base)
            if not secret_type:
                continue  # plain config (e.g. BASE_URL) — not a credential

            var_name = f"{var_base}_{suffix}"

            if dry_run:
                print(f"  DRY   secret {var_name} ({secret_type}) -> {group_name}")
                results.append([var_name, 'Secret', 'would create'])
                continue

            secret_id, created = get_or_create_secret(var_name, dry_run, results)
            if not secret_id:
                continue
            print(f"  {'OK   ' if created else 'SKIP '} secret {var_name}")
            results.append([var_name, 'Secret', 'created' if created else 'skipped'])

            if association_exists(sg_obj['id'], 'Generic', secret_type):
                # Nautobot allows only one secret per (access_type, secret_type)
                # per group — e.g. a login password and an enable password
                # can't both be 'Generic'/'password' in the same group.
                print(f"  SKIP  {group_name} already has a '{secret_type}' secret — "
                      f"'{var_name}' stays available via the tenant .env file only")
                results.append([var_name, 'Secrets Group Association', 'skipped (slot taken)'])
                continue

            r = api_post('extras/secrets-groups-associations', {
                "secrets_group": sg_obj['id'],
                "secret":        secret_id,
                "access_type":   "Generic",
                "secret_type":   secret_type,
            })
            if r.status_code == 201:
                print(f"  OK    linked {var_name} ({secret_type}) -> {group_name}")
                results.append([f"{group_name} -> {var_name}", 'Secrets Group Association', 'created'])
            else:
                print(f"  FAIL  link {var_name} -> {group_name} — {r.status_code}: {r.text[:120]}")
                results.append([f"{group_name} -> {var_name}", 'Secrets Group Association', f'FAILED {r.status_code}'])


def create_external_integrations(profile, derived, dry_run, results):
    print("\n── External integrations ────────────────────────────")
    if not derived['integrations']:
        print("  (none needed for this tenant's selections)")
        return

    for int_name, sg_name in derived['integrations'].items():
        found, _ = exists_by_name('extras/external-integrations', int_name)
        if found:
            print(f"  SKIP  {int_name}")
            results.append([int_name, 'External Integration', 'skipped'])
            continue

        if dry_run:
            print(f"  DRY   {int_name}")
            print(f"        linked to: {sg_name}")
            results.append([int_name, 'External Integration', 'would create'])
            continue

        sg_id = get_id_by_name('extras/secrets-groups', sg_name) if sg_name else None
        payload = {
            "name":       int_name,
            "remote_url": "https://placeholder.example.com",
        }
        if sg_id:
            payload["secrets_group"] = sg_id

        r = api_post('extras/external-integrations', payload)
        if r.status_code == 201:
            print(f"  OK    {int_name}")
            results.append([int_name, 'External Integration', 'created'])
        else:
            print(f"  FAIL  {int_name} — {r.status_code}: {r.text[:120]}")
            results.append([int_name, 'External Integration', f'FAILED {r.status_code}'])


def write_env_file(profile, derived, dry_run, results):
    print("\n── Env file template ────────────────────────────────")
    env_path = os.path.join(LAB_PROFILES_DIR, f"{profile['slug']}.env")

    if os.path.exists(env_path):
        print(f"  SKIP  {env_path} (already exists)")
        results.append([env_path, 'Env File', 'skipped'])
        return

    if dry_run:
        print(f"  DRY   {env_path}")
        print(f"        {len(derived['env_vars'])} variables:")
        for var in derived['env_vars']:
            print(f"          {var}=")
        results.append([env_path, 'Env File', 'would create'])
        return

    lines = [
        f"# Credentials for: {profile['name']}",
        f"# Tenant slug    : {profile['slug']}",
        f"# Generated      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Fill all values before running sync",
        "",
    ]

    current_vendor = None
    for var in derived['env_vars']:
        vendor_prefix = var.split('_')[0]
        if vendor_prefix != current_vendor:
            if current_vendor:
                lines.append("")
            lines.append(f"# ── {vendor_prefix} ──────────────────────────────────")
            current_vendor = vendor_prefix
        lines.append(f"{var}=")

    lines.append("")

    with open(env_path, 'w') as f:
        f.write('\n'.join(lines))

    print(f"  OK    {env_path}")
    print(f"        {len(derived['env_vars'])} variables written")
    results.append([env_path, 'Env File', 'created'])


def save_manifest(profile, derived):
    manifest = {
        "phase":     "create_tenant",
        "timestamp": datetime.now().isoformat(),
        "tenant": {
            "name":     profile['name'],
            "slug":     profile['slug'],
            "group":    profile['group'],
            "vertical": profile['vertical'],
        },
        "created": {
            "secrets_groups":        list(derived['secrets_groups'].values()),
            "external_integrations": list(derived['integrations'].keys()),
            "env_vars":              derived['env_vars'],
            "env_file":              os.path.join(LAB_PROFILES_DIR, f"{profile['slug']}.env"),
        },
        "selections": profile['selections']
    }
    path = os.path.join(LAB_MANIFESTS_DIR, f"tenant_{profile['slug']}.json")
    os.makedirs(LAB_MANIFESTS_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  Manifest → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Create per-tenant objects in Nautobot')
    parser.add_argument('--profile', required=True, help='Path to tenant profile JSON')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    args = parser.parse_args()

    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"\nERROR loading profile: {e}")
        sys.exit(1)

    derived = derive_objects(profile)

    mode = "DRY RUN — no changes will be made" if args.dry_run else "LIVE RUN"
    print(f"\n{'='*60}")
    print(f"  create_tenant.py  [{mode}]")
    print(f"  Tenant   : {profile['name']} ({profile['slug']})")
    print(f"  Group    : {profile['group']}  |  Vertical: {profile['vertical']}")
    print(f"  Target   : {URL}")
    print(f"{'='*60}")
    print(f"\n  Derived from selections:")
    print(f"    Secrets groups       : {len(derived['secrets_groups'])}")
    for sg in derived['secrets_groups'].values():
        print(f"      → {sg}")
    print(f"    External integrations: {len(derived['integrations'])}")
    for ei in derived['integrations']:
        print(f"      → {ei}")
    print(f"    Env vars in template : {len(derived['env_vars'])}")

    summary = run_create_tenant(profile, dry_run=args.dry_run)

    print(f"\n{'='*60}")
    print(f"  Summary: {summary['created']} {'would create' if args.dry_run else 'created'}"
          f" | {summary['skipped']} skipped | {summary['failed']} failed")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("Run without --dry-run to apply changes.\n")
    if summary['failed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
