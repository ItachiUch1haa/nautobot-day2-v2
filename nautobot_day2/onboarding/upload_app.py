"""
upload_app.py
Phase 4 Web Portal — Device CSV upload and validation.
Serves on port 8081.
Dropdowns populated from live Nautobot API (Option B).
Validates against tenant profile for secrets group derivation.

Usage:
    python3 upload_app.py
    python3 upload_app.py --port 8081 --debug
"""

import sys
import os
import csv
import json
import io
import argparse
import re
from datetime import datetime
from functools import lru_cache
from flask import (
    Flask, request, jsonify, render_template,
    session, redirect, url_for, send_file
)

LAB_DIR       = os.path.dirname(os.path.abspath(__file__))
MANIFESTS_DIR = os.path.join(LAB_DIR, 'manifests')

sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.dirname(LAB_DIR))
from client import NautobotClient, NautobotAPIError

client = NautobotClient(env_file=os.path.join(LAB_DIR, '.env'))
URL = client.url

# Tenant profile JSONs live in the SAME persisted location as tenant .env
# files (create_tenant.py's LAB_PROFILES_DIR, honoring NAUTOBOT_DAY2_TENANTS_DIR)
# -- not a separate ephemeral 'profiles/' folder inside the container's
# writable layer, which would silently lose every tenant created through
# this UI on the next rebuild.
from create_tenant import LAB_PROFILES_DIR
PROFILES_DIR = LAB_PROFILES_DIR

from vendor_matrix import (
    VENDOR_MATRIX,
    get_enabled_vendors,
    get_device_types_for_vendor,
    get_access_methods,
    get_default_platform,
    get_platforms_for_combo,
    get_secrets_group_prefix,
)
from nautobot_prepare import (
    normalize_vendor, normalize_role, normalize_managed_by,
    normalize_platform, validate_ip, vendor_to_device_type,
    derive_secrets_group, validate_access_method,
)

app = Flask(__name__)
app.secret_key = os.urandom(24)


# ── Nautobot API helpers ──────────────────────────────────────────────────────

def fetch_all(endpoint, params=None):
    try:
        return client.get_all(endpoint, params=params)
    except NautobotAPIError:
        return []

def natural_to_slug(ns):
    if not ns:
        return ''
    parts = ns.rsplit('_', 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else ns


# ── API routes — data for dropdowns ──────────────────────────────────────────

@app.route('/api/tenants')
def api_tenants():
    """Return tenants from Nautobot for dropdown."""
    tenants = fetch_all('tenancy/tenants')
    result  = []
    for t in tenants:
        slug = natural_to_slug(t['natural_slug'])
        result.append({'name': t['name'], 'slug': slug})
    return jsonify(sorted(result, key=lambda x: x['name']))


@app.route('/api/vendors')
def api_vendors():
    """Return enabled vendors from vendor_matrix."""
    vendors = []
    for slug in get_enabled_vendors():
        label = VENDOR_MATRIX[slug]['label']
        vendors.append({'slug': slug, 'label': label})
    return jsonify(vendors)


@app.route('/api/roles')
def api_roles():
    """Return device roles from Nautobot filtered to our known roles."""
    our_roles = set()
    for v in VENDOR_MATRIX.values():
        for dt in v['device_types'].values():
            if dt['enabled']:
                our_roles.update(dt['roles'])

    roles = fetch_all('extras/roles')
    result = [
        {'name': r['name']}
        for r in roles
        if r['name'] in our_roles and 'dcim.device' in r.get('content_types', [])
    ]
    return jsonify(sorted(result, key=lambda x: x['name']))


@app.route('/api/platforms')
def api_platforms():
    """Return platforms from Nautobot keyed by our vendor_matrix labels."""
    from vendor_matrix import get_all_platforms
    vm_platforms = get_all_platforms()

    platforms = fetch_all('dcim/platforms')
    plat_by_label = {p['name']: natural_to_slug(p['natural_slug']) for p in platforms}

    result = []
    for slug, data in vm_platforms.items():
        nb_slug = plat_by_label.get(data['label'], slug)
        result.append({
            'slug':         slug,
            'label':        data['label'],
            'manufacturer': data['manufacturer_slug'],
        })
    return jsonify(sorted(result, key=lambda x: x['label']))


@app.route('/api/vendor-roles/<vendor>')
def api_vendor_roles(vendor):
    """Return valid roles for a vendor from vendor_matrix."""
    dtypes = get_device_types_for_vendor(vendor)
    roles  = set()
    for dt in dtypes.values():
        roles.update(dt['roles'])
    return jsonify(sorted(list(roles)))


@app.route('/api/vendor-platforms/<vendor>/<role>')
def api_vendor_platforms(vendor, role):
    """Return valid platforms for vendor+role combo."""
    dtype = vendor_to_device_type(vendor, role)
    if not dtype:
        return jsonify([])
    platforms = get_platforms_for_combo(vendor, dtype)
    default   = get_default_platform(vendor, dtype)
    result    = []
    for slug, data in platforms.items():
        result.append({
            'slug':    slug,
            'label':   data['label'],
            'default': slug == default,
        })
    return jsonify(result)


@app.route('/api/managed-by/<vendor>/<role>')
def api_managed_by(vendor, role):
    """Return valid managed_by options for vendor+role combo."""
    dtype = vendor_to_device_type(vendor, role)
    if not dtype:
        return jsonify([{'value': 'ssh', 'label': 'SSH only'}])
    methods = get_access_methods(vendor, dtype)
    result  = []
    for key, data in methods.items():
        result.append({'value': key, 'label': data['label']})
    return jsonify(result)


@app.route('/api/site-types')
def api_site_types():
    """Return site types from Nautobot location types."""
    lts = fetch_all('dcim/location-types')
    # Site-level types are those with dcim.device content type
    result = [
        lt['name'] for lt in lts
        if 'dcim.device' in lt.get('content_types', [])
        and lt['name'] not in ('Site',)  # exclude legacy
    ]
    return jsonify(sorted(result))


# ── Tenant & credentials ──────────────────────────────────────────────────────

@app.route('/api/vendor-full-selections')
def api_vendor_full_selections():
    """
    Given ?vendors=aruba,juniper, return the full selections dict (every
    enabled device_type -> every enabled access_method) for those vendors,
    ready to hand straight to /api/create-tenant. Lets the tenant-creation
    UI just be a simple set of vendor checkboxes instead of asking someone
    to pick individual access methods up front -- the actual access method
    per device gets chosen later, in the device data step.
    """
    vendors = request.args.get('vendors', '')
    vendor_list = [v.strip() for v in vendors.split(',') if v.strip()]

    selections = {}
    for v in vendor_list:
        dtypes = get_device_types_for_vendor(v)
        if not dtypes:
            continue
        selections[v] = {}
        for dtype_key in dtypes:
            methods = get_access_methods(v, dtype_key)
            selections[v][dtype_key] = list(methods.keys())

    return jsonify(selections)


@app.route('/api/tenant-profile/<slug>')
def api_tenant_profile(slug):
    """Return a previously saved tenant profile JSON, if one exists."""
    from create_tenant import validate_profile

    path = os.path.join(PROFILES_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'No profile found for this tenant'}), 404
    with open(path) as f:
        profile = json.load(f)
    return jsonify(validate_profile(profile))


@app.route('/api/create-tenant', methods=['POST'])
def api_create_tenant():
    """
    Create (or update) a tenant in Nautobot from form data, and persist the
    profile JSON so later steps (credentials, deploy) can look it up by slug.
    Body: { name, group, vertical, selections, dry_run }
    """
    from create_tenant import run_create_tenant, validate_profile

    data = request.json or {}
    for field in ('name', 'group', 'vertical', 'selections'):
        if not data.get(field):
            return jsonify({'error': f"'{field}' is required"}), 400

    dry_run = bool(data.get('dry_run', False))
    profile = {
        'name': data['name'],
        'group': data['group'],
        'vertical': data['vertical'],
        'selections': data['selections'],
    }

    try:
        result = run_create_tenant(profile, dry_run=dry_run)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not dry_run:
        os.makedirs(PROFILES_DIR, exist_ok=True)
        profile_path = os.path.join(PROFILES_DIR, f"{result['profile']['slug']}.json")
        with open(profile_path, 'w') as f:
            json.dump(result['profile'], f, indent=2)

    return jsonify({
        'slug': result['profile']['slug'],
        'created': result['created'],
        'skipped': result['skipped'],
        'failed': result['failed'],
        'results': result['results'],
    })


@app.route('/api/credential-requirements/<slug>')
def api_credential_requirements(slug):
    """
    Given a tenant's saved profile, return exactly which credential fields
    the onboarding form needs to show — derived live from vendor_matrix,
    never hardcoded.
    """
    from create_tenant import derive_objects, _infer_secret_type, validate_profile

    path = os.path.join(PROFILES_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'No profile found for this tenant'}), 404
    with open(path) as f:
        profile = json.load(f)
    profile = validate_profile(profile)

    derived = derive_objects(profile)
    suffix = profile['slug'].upper().replace('-', '_')

    fields = []
    for prefix, group_name in derived['secrets_groups'].items():
        for var_base in derived['prefix_env_vars'].get(prefix, []):
            secret_type = _infer_secret_type(var_base)
            # _infer_secret_type() correctly returns None for plain config
            # fields (BASE_URL, TYPE) that shouldn't become Nautobot Secret
            # objects -- but that exclusion is wrong for THIS route, whose
            # job is to tell the frontend what to render, not what becomes
            # a Secret. Give these a distinct 'config' marker instead of
            # skipping them entirely, so they still show up in Step 4.
            display_type = secret_type or 'config'
            fields.append({
                'var_name': f"{var_base}_{suffix}",
                'secret_type': display_type,
                'secrets_group': group_name,
                'is_sensitive': display_type in ('password', 'token', 'key'),
            })

    return jsonify({'slug': slug, 'fields': fields})


def _find_external_integration_label(base_var_name):
    """
    Given a base env var name (no tenant suffix), find the
    'external_integration' label declared for it in VENDOR_MATRIX, if
    any. Used to know which External Integration's remote_url to patch
    when a *_BASE_URL credential gets saved.
    """
    for vendor_data in VENDOR_MATRIX.values():
        for dt_data in vendor_data['device_types'].values():
            for method_data in dt_data['access_methods'].values():
                if base_var_name in method_data.get('env_vars', []):
                    label = method_data.get('external_integration')
                    if label:
                        return label
    return None


@app.route('/api/save-credentials', methods=['POST'])
def api_save_credentials():
    """
    Write submitted credential values into a tenant's .env file. Only
    variable names that are actually part of that tenant's derived
    requirements are accepted — this is not a general-purpose way to set
    arbitrary environment variables. Values are never echoed back in the
    response, logged, or included in any error message.
    Body: { "slug": "...", "credentials": { "VAR_NAME": "value", ... } }
    """
    from create_tenant import derive_objects, validate_profile, LAB_PROFILES_DIR

    data = request.json or {}
    slug = data.get('slug')
    credentials = data.get('credentials')

    if not slug:
        return jsonify({'error': "'slug' is required"}), 400
    if not isinstance(credentials, dict) or not credentials:
        return jsonify({'error': "'credentials' must be a non-empty object"}), 400

    profile_path = os.path.join(PROFILES_DIR, f"{slug}.json")
    if not os.path.exists(profile_path):
        return jsonify({'error': 'No profile found for this tenant'}), 404
    with open(profile_path) as f:
        profile = json.load(f)
    profile = validate_profile(profile)

    derived = derive_objects(profile)
    allowed_vars = set(derived['env_vars'])

    unknown = [v for v in credentials if v not in allowed_vars]
    if unknown:
        return jsonify({
            'error': 'Unrecognized variable name(s) for this tenant',
            'unknown': unknown,
        }), 400

    env_path = os.path.join(LAB_PROFILES_DIR, f"{slug}.env")
    if not os.path.exists(env_path):
        return jsonify({'error': 'Tenant env file does not exist yet — create the tenant first'}), 404

    with open(env_path) as f:
        lines = f.readlines()

    updated = []
    new_lines = []
    for line in lines:
        stripped = line.rstrip('\n')
        if '=' in stripped and not stripped.strip().startswith('#'):
            var_name = stripped.split('=', 1)[0]
            if var_name in credentials:
                new_lines.append(f"{var_name}={credentials[var_name]}\n")
                updated.append(var_name)
                continue
        new_lines.append(line)

    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    os.chmod(env_path, 0o600)

    not_found = [v for v in credentials if v not in updated]

    suffix = slug.upper().replace('-', '_')

    # Push saved values into OpenBao too -- the .env file above is kept
    # as a fallback/legacy artifact, but OpenBao is the authoritative
    # credential store going forward. Group the updated variables by
    # their secrets-group prefix (reusing derived['prefix_env_vars'],
    # the same mapping already used elsewhere in this file), then
    # merge-update each group's OpenBao secret via the existing
    # write-scoped refresher identity -- reused as-is, no new AppRole
    # needed for this path. Unlike the token-rotation use of this same
    # function, failures here are surfaced to the caller rather than
    # swallowed: this is a foreground, user-initiated save, and the
    # engineer needs to know if OpenBao didn't actually get the value.
    var_to_prefix = {}
    for _prefix, _var_bases in derived.get('prefix_env_vars', {}).items():
        for _vb in _var_bases:
            var_to_prefix[_vb] = _prefix

    by_prefix = {}
    for var_name in updated:
        if not var_name.endswith(f"_{suffix}"):
            continue
        base_var = var_name[:-len(f"_{suffix}")]
        prefix = var_to_prefix.get(base_var)
        if not prefix:
            continue
        by_prefix.setdefault(prefix, {})[var_name] = credentials[var_name]

    openbao_status = {}
    if by_prefix:
        from openbao_client import update_rotated_credential
        for prefix, group_updates in by_prefix.items():
            try:
                update_rotated_credential(slug, prefix, group_updates)
                openbao_status[prefix] = 'saved'
            except Exception as _bao_err:
                openbao_status[prefix] = f'FAILED: {_bao_err}'

    # If any saved value is a controller/API base URL, push it onto the
    # matching External Integration's remote_url too -- eliminates the
    # manual "go fix the placeholder URL in Nautobot's UI" step that
    # create_tenant.py's External Integration creation otherwise leaves
    # behind (it creates the integration with a hardcoded placeholder,
    # since the real URL isn't known until credentials are entered here).
    integration_updates = []
    for var_name in updated:
        if not var_name.endswith(f"_{suffix}"):
            continue
        base_var = var_name[:-len(f"_{suffix}")]
        if not base_var.endswith('_BASE_URL'):
            continue
        int_label = _find_external_integration_label(base_var)
        if not int_label:
            continue
        full_int_name = f"{int_label} - {slug}"
        r = client.get('extras/external-integrations', params={'name': full_int_name, 'limit': 1})
        results_list = r.json().get('results', []) if r.ok else []
        if not results_list:
            integration_updates.append({'integration': full_int_name, 'status': 'not_found'})
            continue
        int_id = results_list[0]['id']
        patch_r = client.patch(f'extras/external-integrations/{int_id}', {
            'remote_url': credentials[var_name]
        })
        integration_updates.append({
            'integration': full_int_name,
            'status': 'updated' if patch_r.status_code == 200 else f'FAILED {patch_r.status_code}'
        })

    return jsonify({
        'slug': slug,
        'updated': updated,
        'not_found_in_file': not_found,
        'integration_updates': integration_updates,
        'openbao_status': openbao_status,
    })


@app.route('/api/validate-credentials', methods=['POST'])
def api_validate_credentials():
    """
    Test that a tenant's stored credentials actually work, reusing
    vendor_test_app.py's connectivity-test functions rather than writing
    new ones. Reads real values fresh from the tenant's .env file --
    never trusts blank placeholders, never echoes values back.

    Body: { "slug": "...", "tests": [
        {"vendor": "...", "role": "...", "access_method": "...",
         "platform": "..." (optional, defaults to the combo's default),
         "ip": "..." (only needed for ssh access methods)}, ...
    ] }
    """
    from dotenv import dotenv_values
    from create_tenant import LAB_PROFILES_DIR
    from vendor_matrix import needs_enable_mode
    from vendor_test_app import test_ssh, test_mist, test_aruba_central, find_block

    data = request.json or {}
    slug = data.get('slug')
    tests = data.get('tests')
    if not slug:
        return jsonify({'error': "'slug' is required"}), 400
    if not isinstance(tests, list) or not tests:
        return jsonify({'error': "'tests' must be a non-empty list"}), 400

    env_path = os.path.join(LAB_PROFILES_DIR, f"{slug}.env")
    if not os.path.exists(env_path):
        return jsonify({'error': 'Tenant env file does not exist yet'}), 404
    env = dotenv_values(env_path)
    suffix = slug.upper().replace('-', '_')

    results = []
    for t in tests:
        vendor        = t.get('vendor', '')
        role          = t.get('role', '')
        access_method = t.get('access_method', '')
        # Strip any CIDR suffix (e.g. "172.33.1.1/24" -> "172.33.1.1") --
        # device IPs are stored with a prefix for Nautobot's IP/Prefix
        # objects, but SSH connections need a bare address. Without this,
        # netmiko/paramiko tries to resolve "x.x.x.x/24" as a hostname and
        # fails with a DNS-style error instead of a real connection attempt.
        ip            = t.get('ip', '').split('/')[0]

        dtype = vendor_to_device_type(vendor, role)
        if not dtype:
            results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                             'status': 'error', 'message': f"Cannot map role '{role}' to a device type"})
            continue

        methods = get_access_methods(vendor, dtype)
        method_def = methods.get(access_method)
        if not method_def:
            results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                             'status': 'error', 'message': f"'{access_method}' not valid for {vendor}/{dtype}"})
            continue

        if method_def.get('inherits_from'):
            results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                             'status': 'inherited',
                             'message': f"Credentials inherited from this site's {method_def['inherits_from']} -- covered by that test"})
            continue

        # Confirm required credential fields are actually filled in
        env_vars_needed = method_def.get('env_vars', [])
        full_names = [f"{v}_{suffix}" for v in env_vars_needed]
        values = {v: env.get(full, '') for v, full in zip(env_vars_needed, full_names)}
        missing = [v for v, val in values.items() if not val]
        if missing:
            results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                             'status': 'not_configured',
                             'message': f"Credentials not filled in yet: {missing}"})
            continue

        if access_method == 'ssh':
            platform = t.get('platform') or get_default_platform(vendor, dtype)
            platforms = get_platforms_for_combo(vendor, dtype)
            yaml_key = (platforms.get(platform) or {}).get('yaml_key')
            if not yaml_key:
                results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                                 'status': 'not_implemented',
                                 'message': f"No SSH command mapping yet for {vendor}/{platform}"})
                continue
            block = find_block(yaml_key)
            if not block:
                results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                                 'status': 'error', 'message': f"'{yaml_key}' not found in vendor_commands.yaml"})
                continue
            if not ip:
                results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                                 'status': 'error', 'message': "'ip' is required for ssh tests"})
                continue
            enable = ''
            if needs_enable_mode(vendor, dtype, access_method):
                enable_var = f"{method_def.get('enable_env_var','')}_{suffix}"
                enable = env.get(enable_var, '')
            user_var, pass_var = full_names[0], full_names[1]
            test_result = test_ssh(ip, yaml_key, block, env.get(user_var, ''), env.get(pass_var, ''), enable)

        elif access_method == 'mist':
            test_result = test_mist(
                token=values.get('MIST_API_TOKEN', ''),
                org_id=values.get('MIST_ORG_ID', ''),
                base_url=values.get('MIST_BASE_URL', '') or 'https://api.mist.com',
            )

        elif access_method == 'aruba-central':
            test_result = test_aruba_central(
                client_id=values.get('ARUBA_CLIENT_ID', ''),
                client_secret=values.get('ARUBA_CLIENT_SECRET', ''),
                refresh_token=values.get('ARUBA_REFRESH_TOKEN', ''),
                base_url=values.get('ARUBA_CENTRAL_BASE_URL', ''),
            )

        else:
            results.append({'vendor': vendor, 'role': role, 'access_method': access_method,
                             'status': 'not_implemented',
                             'message': f"Live test for '{access_method}' not built yet -- credentials are present, just unverified"})
            continue

        test_result['vendor'] = vendor
        test_result['role'] = role
        test_result['access_method'] = access_method
        results.append(test_result)

    return jsonify({'slug': slug, 'results': results})


@app.route('/api/validate-row', methods=['POST'])
def api_validate_row():
    """Validate a single device row in real time."""
    data = request.json
    issues, fixes, warns = [], [], []

    vendor     = normalize_vendor(data.get('vendor', ''))
    role       = normalize_role(data.get('role', ''))
    managed_by = normalize_managed_by(data.get('managed_by', ''))
    platform   = normalize_platform(data.get('platform', ''))
    ip         = data.get('ip', '').strip()
    name       = data.get('device_name', '').strip()
    tenant     = data.get('tenant_slug', '').strip()

    if not vendor:
        issues.append(f"Unknown vendor '{data.get('vendor')}'")
    if not role:
        issues.append(f"Unknown role '{data.get('role')}'")
    if not name:
        issues.append("device_name required")

    if ip:
        _, ip_err = validate_ip(ip)
        if ip_err:
            issues.append(ip_err)
    else:
        issues.append("IP address required")

    if vendor and role:
        ok, err = validate_access_method(vendor, role, managed_by)
        if not ok:
            warns.append(err)

    # Derive platform if not provided
    if not platform and vendor and role:
        dtype = vendor_to_device_type(vendor, role)
        if dtype:
            platform = get_default_platform(vendor, dtype) or ''

    # Derive secrets group
    sg = ''
    if vendor and role and managed_by and tenant:
        sg = derive_secrets_group(vendor, role, managed_by, tenant) or ''

    status = 'error' if issues else ('warn' if warns else 'ok')
    return jsonify({
        'status':         status,
        'issues':         issues,
        'warnings':       warns,
        'fixes':          fixes,
        'platform':       platform,
        'secrets_group':  sg,
        'managed_by_norm': managed_by,
        'vendor_norm':    vendor or data.get('vendor', ''),
        'role_norm':      role or data.get('role', ''),
    })


def _validate_rows(rows, tenant_slug):
    """
    Core row-validation logic (vendor/role/IP checks, access-method
    validation, secrets-group derivation, stack-grouping consistency)
    shared by both the CSV-upload path and the interactive JSON path --
    so the interactive device-entry table gets the same real,
    cross-row-aware validation instead of checking each row in total
    isolation.

    Returns (results, summary) on success, or (None, {'error': ...}) if
    the input itself was invalid (empty, missing required columns).
    """
    if not rows:
        return None, {'error': 'No rows to validate'}

    required_cols = ['device_name', 'role', 'vendor', 'model', 'ip']
    missing_cols  = [c for c in required_cols if c not in rows[0]]
    if missing_cols:
        return None, {'error': f"Missing columns: {missing_cols}"}

    results  = []
    seen_ips = {}

    # Pre-pass: map each site to its Fortinet firewall's secrets group, so
    # Fortinet APs managed "via FortiGate" inherit that firewall's
    # credentials instead of needing (and never having) their own.
    # Fortinet's own architecture makes this the only real access path --
    # a FortiAP cannot be reached independently of its controlling FortiGate.
    site_firewall_sg = {}
    for row in rows:
        v = normalize_vendor(row.get('vendor', ''))
        r = normalize_role(row.get('role', ''))
        if v == 'fortinet' and r == 'branch-fw':
            site = row.get('site', '').strip()
            mb   = normalize_managed_by(row.get('managed_by', ''))
            if site and mb and tenant_slug:
                fw_sg = derive_secrets_group(v, r, mb, tenant_slug)
                if fw_sg:
                    site_firewall_sg[site] = fw_sg

    # Pre-pass: group rows into stacks by stack_group. Only one row per
    # stack needs a management IP (the whole stack is reached through it);
    # the rest can leave IP blank. Cross-vendor stacks aren't supported --
    # you can't stack an Aruba switch with a Juniper one. vc_position is
    # taken from an explicit column if present, otherwise auto-assigned by
    # row order within the group.
    stack_groups = {}
    for idx, row in enumerate(rows):
        sg = row.get('stack_group', '').strip()
        if sg:
            stack_groups.setdefault(sg, []).append(idx)

    stack_ip_bearer    = {}
    stack_vc_position  = {}
    stack_extra_issues = {}
    stack_extra_warns  = {}

    for sg, idxs in stack_groups.items():
        if len(idxs) < 2:
            continue  # a lone row with a stack_group isn't really a stack

        vendors_in_group = set(normalize_vendor(rows[idx].get('vendor', '')) for idx in idxs)
        if len(vendors_in_group) > 1:
            for idx in idxs:
                stack_extra_issues.setdefault(idx, []).append(
                    f"Stack group '{sg}' has mixed vendors ({', '.join(sorted(vendors_in_group))}) -- not supported"
                )

        for pos, idx in enumerate(idxs, 1):
            explicit_pos = rows[idx].get('vc_position', '').strip()
            stack_vc_position[idx] = explicit_pos if explicit_pos else str(pos)

        ip_bearing_idxs = [idx for idx in idxs if rows[idx].get('ip', '').strip()]
        if len(ip_bearing_idxs) == 0:
            for idx in idxs:
                stack_extra_issues.setdefault(idx, []).append(
                    f"Stack group '{sg}' needs exactly one management IP (on the commander/master row) -- none found"
                )
        else:
            stack_ip_bearer[ip_bearing_idxs[0]] = True
            for idx in ip_bearing_idxs[1:]:
                stack_extra_warns.setdefault(idx, []).append(
                    f"Stack group '{sg}' already has a management IP on an earlier row -- this one will be ignored"
                )

    # Pre-pass: group rows into HA (redundancy) pairs by ha_group. Unlike
    # a stack, every member here keeps its OWN management IP -- each unit
    # in a firewall HA pair is independently reachable, not one shared
    # control plane. ha_priority is taken from an explicit column if
    # present (lower number = higher priority), otherwise auto-assigned by
    # row order. Cross-vendor HA pairs aren't supported, same as stacks.
    ha_groups = {}
    for idx, row in enumerate(rows):
        hg = row.get('ha_group', '').strip()
        if hg:
            ha_groups.setdefault(hg, []).append(idx)

    ha_priority        = {}
    ha_extra_issues    = {}

    for hg, idxs in ha_groups.items():
        if len(idxs) < 2:
            continue  # a lone row with an ha_group isn't really an HA pair

        vendors_in_group = set(normalize_vendor(rows[idx].get('vendor', '')) for idx in idxs)
        if len(vendors_in_group) > 1:
            for idx in idxs:
                ha_extra_issues.setdefault(idx, []).append(
                    f"HA group '{hg}' has mixed vendors ({', '.join(sorted(vendors_in_group))}) -- not supported"
                )

        for pos, idx in enumerate(idxs, 1):
            explicit_pri = rows[idx].get('ha_priority', '').strip()
            ha_priority[idx] = explicit_pri if explicit_pri else str(pos)

    for i, row in enumerate(rows, 1):
        vendor     = normalize_vendor(row.get('vendor', ''))
        role       = normalize_role(row.get('role', ''))
        managed_by = normalize_managed_by(row.get('managed_by', ''))
        platform   = normalize_platform(row.get('platform', ''))
        ip         = row.get('ip', '').strip()
        stack_group_val = row.get('stack_group', '').strip()
        is_stack_row    = stack_group_val in stack_groups and len(stack_groups[stack_group_val]) >= 2
        ha_group_val    = row.get('ha_group', '').strip()
        issues, fixes, warns = [], [], []
        issues.extend(stack_extra_issues.get(i - 1, []))
        warns.extend(stack_extra_warns.get(i - 1, []))
        issues.extend(ha_extra_issues.get(i - 1, []))
        status = 'ok'

        # Vendor
        if not vendor:
            issues.append(f"Unknown vendor '{row.get('vendor')}'")
        elif vendor != row.get('vendor', '').lower():
            fixes.append(f"vendor normalized: '{row.get('vendor')}'→'{vendor}'")

        # Role
        if not role:
            issues.append(f"Unknown role '{row.get('role')}'")
        elif role != row.get('role', ''):
            fixes.append(f"role normalized: '{row.get('role')}'→'{role}'")

        # Managed by
        if managed_by != row.get('managed_by', '').lower():
            fixes.append(f"managed_by normalized: '{row.get('managed_by')}'→'{managed_by}'")

        # Platform derivation
        if not platform and vendor and role:
            dtype = vendor_to_device_type(vendor, role)
            if dtype:
                platform = get_default_platform(vendor, dtype) or ''
                if platform:
                    fixes.append(f"platform derived: '{platform}'")

        # IP
        if ip:
            _, ip_err = validate_ip(ip)
            if ip_err:
                issues.append(ip_err)
            else:
                # Duplicate IP check within CSV
                if ip in seen_ips:
                    issues.append(f"Duplicate IP -- also on row {seen_ips[ip]}")
                else:
                    seen_ips[ip] = i
        elif is_stack_row and not stack_ip_bearer.get(i - 1, False):
            pass  # non-master stack member rows don't need their own IP
        else:
            issues.append("IP required")

        # Device name
        if not row.get('device_name', '').strip():
            issues.append("device_name required")

        # Model
        if not row.get('model', '').strip():
            issues.append("model required")

        # Validate managed_by vs vendor+role
        if vendor and role:
            ok, err = validate_access_method(vendor, role, managed_by)
            if not ok:
                warns.append(err)

        # Secrets group
        sg = ''
        if vendor == 'fortinet' and role == 'ap' and managed_by == 'fortigate':
            site = row.get('site', '').strip()
            sg = site_firewall_sg.get(site, '')
            if not sg:
                warns.append(
                    f"No Fortinet firewall found for site '{site}' in this CSV -- "
                    "cannot determine which credentials this AP should use"
                )
        elif vendor and role and managed_by and tenant_slug:
            sg = derive_secrets_group(vendor, role, managed_by, tenant_slug) or ''

        if issues:
            status = 'error'
        elif warns:
            status = 'warn'
        elif fixes:
            status = 'fixed'

        results.append({
            'row':           i,
            'device_name':   row.get('device_name', ''),
            'vendor':        vendor or row.get('vendor', ''),
            'role':          role or row.get('role', ''),
            'platform':      platform,
            'model':         row.get('model', ''),
            'serial':        row.get('serial', ''),
            'ip':            ip,
            'managed_by':    managed_by,
            'secrets_group': sg,
            'stack_group':   stack_group_val,
            'vc_position':   stack_vc_position.get(i - 1, ''),
            'ha_group':      ha_group_val,
            'ha_priority':   ha_priority.get(i - 1, ''),
            'status':        status,
            'issues':        issues,
            'warnings':      warns,
            'fixes':         fixes,
        })

    summary = {
        'total':  len(results),
        'ok':     sum(1 for r in results if r['status'] == 'ok'),
        'fixed':  sum(1 for r in results if r['status'] == 'fixed'),
        'warn':   sum(1 for r in results if r['status'] == 'warn'),
        'errors': sum(1 for r in results if r['status'] == 'error'),
        'ready':  sum(1 for r in results if r['status'] != 'error'),
    }
    return results, summary


@app.route('/api/validate-csv', methods=['POST'])
def api_validate_csv():
    """Validate an uploaded CSV file."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f           = request.files['file']
    tenant_slug = request.form.get('tenant_slug', '')
    content     = f.read().decode('utf-8')
    reader      = csv.DictReader(io.StringIO(content))
    rows        = [{k.strip(): (v.strip() if v else '') for k, v in r.items()}
                   for r in reader]

    results, summary = _validate_rows(rows, tenant_slug)
    if results is None:
        return jsonify(summary), 400
    return jsonify({'rows': results, 'summary': summary})


@app.route('/api/validate-rows', methods=['POST'])
def api_validate_rows():
    """
    Same validation as /api/validate-csv, but for rows submitted directly
    as JSON -- used by the interactive device-entry table so it gets the
    same real, cross-row-aware validation (stack grouping, cross-vendor
    checks, etc.) instead of validating each row alone.
    Body: { "rows": [...], "tenant_slug": "..." }
    """
    data        = request.json or {}
    rows        = data.get('rows', [])
    tenant_slug = data.get('tenant_slug', '')

    results, summary = _validate_rows(rows, tenant_slug)
    if results is None:
        return jsonify(summary), 400
    return jsonify({'rows': results, 'summary': summary})


OUTPUT_COLS_READY = [
    'device_name', 'role', 'vendor', 'platform', 'model', 'ip',
    'managed_by', 'serial', 'status', 'stack_group', 'vc_position',
    'ha_group', 'ha_priority',
    'tenant_slug', 'secrets_group', 'namespace',
    'region', 'country', 'state', 'city', 'site_name', 'site_type',
]


def _build_ready_rows(rows, site_config):
    """
    Shared row-shaping logic used by both /api/generate-ready-csv (writes a
    file for manual download) and /api/deploy (feeds process_csv() directly,
    in-process, no file round-trip needed). Keeping this in one place means
    the two paths can never quietly drift apart.
    """
    tenant_slug = site_config.get('tenant_slug', '')
    out_rows = []
    for row in rows:
        if row.get('status') == 'error':
            continue
        out_rows.append({
            'device_name': row.get('device_name', ''),
            'role':        row.get('role', ''),
            'vendor':      row.get('vendor', ''),
            'platform':    row.get('platform', ''),
            'model':       row.get('model', ''),
            'ip':          row.get('ip', ''),
            'managed_by':  row.get('managed_by', 'ssh'),
            'serial':      row.get('serial', ''),
            'status':      row.get('status_val', 'Active'),
            'stack_group': row.get('stack_group', ''),
            'vc_position': row.get('vc_position', ''),
            'ha_group':    row.get('ha_group', ''),
            'ha_priority': row.get('ha_priority', ''),
            'tenant_slug': tenant_slug,
            'secrets_group': row.get('secrets_group', ''),
            'namespace':   tenant_slug,
            'region':      site_config.get('region', ''),
            'country':     site_config.get('country', ''),
            'state':       site_config.get('state', ''),
            'city':        site_config.get('city', ''),
            'site_name':   site_config.get('site_name', ''),
            'site_type':   site_config.get('site_type', ''),
        })
    return out_rows


@app.route('/api/generate-ready-csv', methods=['POST'])
def api_generate_ready_csv():
    """Generate nautobot_ready CSV from validated rows + site config."""
    data        = request.json
    rows        = data.get('rows', [])
    site_config = data.get('site_config', {})
    tenant_slug = site_config.get('tenant_slug', '')

    if not rows or not site_config:
        return jsonify({'error': 'rows and site_config required'}), 400

    output_cols = OUTPUT_COLS_READY
    out_rows = _build_ready_rows(rows, site_config)

    if not out_rows:
        return jsonify({'error': 'No valid rows to export'}), 400

    # Write to file
    site_name = site_config.get('site_name', 'site').lower().replace(' ', '-')
    filename  = f"nautobot_ready_{site_name}.csv"
    filepath  = os.path.join(LAB_DIR, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=output_cols, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(out_rows)

    # Save manifest
    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    manifest = {
        "phase":      "prepare_portal",
        "timestamp":  datetime.now().isoformat(),
        "output_csv": filepath,
        "site":       site_config.get('site_name', ''),
        "tenant":     tenant_slug,
        "total":      len(rows),
        "ready":      len(out_rows),
    }
    mpath = os.path.join(MANIFESTS_DIR, f"prepare_{tenant_slug}_{site_name}_{ts}.json")
    with open(mpath, 'w') as f:
        json.dump(manifest, f, indent=2)

    return jsonify({
        'filename': filename,
        'filepath': filepath,
        'count':    len(out_rows),
        'manifest': mpath,
    })


@app.route('/api/deploy', methods=['POST'])
def api_deploy():
    """
    Create devices in Nautobot from validated rows, then optionally trigger
    the tenant-wide sync Job over Nautobot's own REST API. Reuses
    nautobot_onboard_v2.py's process_csv() directly, in-process -- no file
    round-trip needed, since it already accepts row dicts.

    Body: { "rows": [...], "site_config": {...},
            "trigger_sync": true (default), "sync_category": "all" (default) }
    """
    import nautobot_onboard_v2

    data         = request.json or {}
    rows         = data.get('rows', [])
    site_config  = data.get('site_config', {})
    trigger_sync = data.get('trigger_sync', True)
    sync_category = data.get('sync_category', 'all')
    tenant_slug  = site_config.get('tenant_slug', '')

    if not rows or not site_config:
        return jsonify({'error': 'rows and site_config required'}), 400

    ready_rows = _build_ready_rows(rows, site_config)
    if not ready_rows:
        return jsonify({'error': 'No valid rows to deploy'}), 400

    # Onboard: create devices, IPs, controllers in Nautobot
    nautobot_onboard_v2.init_cache()
    onboard_results = nautobot_onboard_v2.process_csv(ready_rows, dry_run=False)

    ok     = sum(1 for r in onboard_results if r[1] == 'OK')
    failed = sum(1 for r in onboard_results if r[1] == 'FAILED')

    response = {
        'onboard': {
            'ok': ok,
            'failed': failed,
            'total': len(ready_rows),
            'results': onboard_results,
        },
        'sync': None,
    }

    if not trigger_sync:
        return jsonify(response)

    if not tenant_slug:
        response['sync'] = {'error': "No tenant_slug in site_config -- cannot trigger sync"}
        return jsonify(response)

    # Look up the tenant's Nautobot object ID (the sync Job needs the real
    # ObjectVar reference). Nautobot's Tenant API doesn't support filtering
    # by slug -- so we read the tenant's saved profile (same file
    # create_tenant.py writes) to get its real Name, and filter by that.
    profile_path = os.path.join(PROFILES_DIR, f"{tenant_slug}.json")
    if not os.path.exists(profile_path):
        response['sync'] = {'error': f"No saved profile for tenant '{tenant_slug}' -- cannot look up its Nautobot ID"}
        return jsonify(response)
    with open(profile_path) as f:
        tenant_profile = json.load(f)
    tenant_name = tenant_profile.get('name', '')

    tr = client.get('tenancy/tenants', params={'name': tenant_name, 'limit': 1})
    tenant_results = tr.json().get('results', []) if tr.ok else []
    if not tenant_results:
        response['sync'] = {'error': f"Tenant '{tenant_name}' not found in Nautobot"}
        return jsonify(response)
    tenant_id = tenant_results[0]['id']

    # Look up the "Sync All Sites for Tenant" Job's ID by name
    jr = client.get('extras/jobs', params={'name': 'Sync All Sites for Tenant', 'limit': 1})
    job_results = jr.json().get('results', []) if jr.ok else []
    if not job_results:
        response['sync'] = {'error': "'Sync All Sites for Tenant' job not found -- is it registered?"}
        return jsonify(response)
    job_id = job_results[0]['id']

    run_resp = client.post(f'extras/jobs/{job_id}/run', {
        'data': {
            'tenant': tenant_id,
            'category': sync_category,
            'dry_run': False,
        }
    })

    if run_resp.ok:
        response['sync'] = {
            'triggered': True,
            'job_result': run_resp.json(),
        }
    else:
        response['sync'] = {
            'triggered': False,
            'error': f"HTTP {run_resp.status_code}: {run_resp.text[:300]}",
        }

    return jsonify(response)


@app.route('/api/download-ready-csv/<site_name>')
def api_download_ready_csv(site_name):
    """Download the generated nautobot_ready CSV."""
    filename = f"nautobot_ready_{site_name}.csv"
    filepath = os.path.join(LAB_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    return send_file(filepath, as_attachment=True, download_name=filename)


# ── Main portal page ──────────────────────────────────────────────────────────


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'nautobot': URL})


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Nautobot site onboarding web portal')
    parser.add_argument('--port',  type=int, default=8081)
    parser.add_argument('--host',  default='0.0.0.0')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    print(f"\n  Nautobot Onboarding Portal")
    print(f"  URL     : http://{args.host}:{args.port}")
    print(f"  Nautobot: {URL}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)
