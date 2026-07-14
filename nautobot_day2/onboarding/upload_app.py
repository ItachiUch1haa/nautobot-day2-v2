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
PROFILES_DIR  = os.path.join(LAB_DIR, 'profiles')
MANIFESTS_DIR = os.path.join(LAB_DIR, 'manifests')

sys.path.insert(0, LAB_DIR)
sys.path.insert(0, os.path.dirname(LAB_DIR))
from client import NautobotClient, NautobotAPIError

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
            if not secret_type:
                continue
            fields.append({
                'var_name': f"{var_base}_{suffix}",
                'secret_type': secret_type,
                'secrets_group': group_name,
                'is_sensitive': secret_type in ('password', 'token', 'key'),
            })

    return jsonify({'slug': slug, 'fields': fields})


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

    return jsonify({
        'slug': slug,
        'updated': updated,
        'not_found_in_file': not_found,
    })


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

    if not rows:
        return jsonify({'error': 'CSV is empty'}), 400

    required_cols = ['device_name', 'role', 'vendor', 'model', 'ip']
    missing_cols  = [c for c in required_cols if c not in rows[0]]
    if missing_cols:
        return jsonify({'error': f"Missing columns: {missing_cols}"}), 400

    results  = []
    seen_ips = {}

    for i, row in enumerate(rows, 1):
        vendor     = normalize_vendor(row.get('vendor', ''))
        role       = normalize_role(row.get('role', ''))
        managed_by = normalize_managed_by(row.get('managed_by', ''))
        platform   = normalize_platform(row.get('platform', ''))
        ip         = row.get('ip', '').strip()
        issues, fixes, warns = [], [], []
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
                    issues.append(f"Duplicate IP — also on row {seen_ips[ip]}")
                else:
                    seen_ips[ip] = i
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
        if vendor and role and managed_by and tenant_slug:
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
            'ip':            ip,
            'managed_by':    managed_by,
            'secrets_group': sg,
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
    return jsonify({'rows': results, 'summary': summary})


@app.route('/api/generate-ready-csv', methods=['POST'])
def api_generate_ready_csv():
    """Generate nautobot_ready CSV from validated rows + site config."""
    data        = request.json
    rows        = data.get('rows', [])
    site_config = data.get('site_config', {})
    tenant_slug = site_config.get('tenant_slug', '')

    if not rows or not site_config:
        return jsonify({'error': 'rows and site_config required'}), 400

    output_cols = [
        'device_name', 'role', 'vendor', 'platform', 'model', 'ip',
        'managed_by', 'serial', 'status',
        'tenant_slug', 'secrets_group', 'namespace',
        'region', 'country', 'state', 'city', 'site_name', 'site_type',
    ]

    out_rows = []
    for row in rows:
        if row.get('status') == 'error':
            continue
        out = {
            'device_name': row.get('device_name', ''),
            'role':        row.get('role', ''),
            'vendor':      row.get('vendor', ''),
            'platform':    row.get('platform', ''),
            'model':       row.get('model', ''),
            'ip':          row.get('ip', ''),
            'managed_by':  row.get('managed_by', 'ssh'),
            'serial':      row.get('serial', ''),
            'status':      row.get('status_val', 'Active'),
            'tenant_slug': tenant_slug,
            'secrets_group': row.get('secrets_group', ''),
            'namespace':   tenant_slug,
            'region':      site_config.get('region', ''),
            'country':     site_config.get('country', ''),
            'state':       site_config.get('state', ''),
            'city':        site_config.get('city', ''),
            'site_name':   site_config.get('site_name', ''),
            'site_type':   site_config.get('site_type', ''),
        }
        out_rows.append(out)

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
