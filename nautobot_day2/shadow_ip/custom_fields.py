"""
One-time bootstrap for the custom fields this package's data model needs.
Mirrors onboarding/bootstrap_nautobot.py's create_custom_field() pattern
exactly -- same idempotent REST-based creation, same script shape -- run
once via `python3 custom_fields.py [--dry-run]` as part of this build's
new INSTALL.md phase, same as bootstrap_nautobot.py is for Phase 14.

PENDING LIVE VERIFICATION: the "object" type fields (nat_shadow_prefix,
mapped_shadow_ip, managing_controller) need their target model specified
in whatever shape Nautobot's actual /api/extras/custom-fields/ schema
expects for a cross-model reference -- verify the payload below against
the running server's OPTIONS/schema for that endpoint before relying on
it; adjust CUSTOM_FIELDS' payloads if the live API rejects the guessed key.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from client import NautobotClient  # noqa: E402

client = NautobotClient()
URL = client.url

# ── Field definitions ─────────────────────────────────────────────────────────
# Shadow IP spec §1.5, plus fortigate_vip_name/fortigate_tunnel_name (VIP
# Management architecture doc §3.5, set on the shadow Prefix and customer
# Namespace respectively -- fortigate_vdom stays at Namespace level per that
# doc's own recommendation, already the convention in this codebase), plus
# this build's controller_managed/managing_controller (architecture doc §5)
# added to the same one-time bootstrap.

CUSTOM_FIELDS = [
    {
        "name": "nat_shadow_prefix",
        "label": "NAT Shadow Prefix",
        "type": "object",
        "content_types": ["ipam.prefix"],
        "object_field_content_type": "ipam.prefix",
    },
    {
        "name": "mapped_shadow_ip",
        "label": "Mapped Shadow IP",
        "type": "object",
        "content_types": ["ipam.ipaddress"],
        "object_field_content_type": "ipam.ipaddress",
    },
    {
        "name": "real_ip",
        "label": "Real IP (denormalized)",
        "type": "text",
        "content_types": ["ipam.ipaddress"],
    },
    {
        "name": "fortigate_vdom",
        "label": "FortiGate VDOM",
        "type": "text",
        "content_types": ["ipam.namespace"],
    },
    {
        "name": "fortigate_vip_name",
        "label": "FortiGate VIP Name",
        "type": "text",
        "content_types": ["ipam.prefix"],
    },
    {
        "name": "fortigate_tunnel_name",
        "label": "FortiGate Tunnel Name",
        "type": "text",
        "content_types": ["ipam.namespace"],
    },
    {
        "name": "controller_managed",
        "label": "Controller Managed",
        "type": "boolean",
        "content_types": ["dcim.device"],
    },
    {
        "name": "managing_controller",
        "label": "Managing Controller",
        "type": "object",
        "content_types": ["dcim.device"],
        "object_field_content_type": "dcim.device",
    },
]


def api_get(endpoint, params=None):
    """Performs a GET request against the given endpoint and returns the parsed JSON response."""
    r = client.get(endpoint, params=params)
    r.raise_for_status()
    return r.json()


def api_post(endpoint, data):
    """Performs a POST request against the given endpoint and returns the raw response."""
    return client.post(endpoint, data)


def create_custom_fields(dry_run, results):
    """Creates every custom field in CUSTOM_FIELDS that doesn't already exist."""
    print("\n── Custom fields (shadow IP + controller-managed) ───")

    existing = api_get("extras/custom-fields", params={"limit": 200})
    existing_keys = [cf["key"] for cf in existing.get("results", [])]

    for field in CUSTOM_FIELDS:
        if field["name"] in existing_keys:
            print(f"  SKIP  {field['name']} (already exists)")
            results.append([field["name"], "Custom Field", "skipped"])
            continue

        if dry_run:
            print(f"  DRY   {field['name']} (would create, type={field['type']})")
            results.append([field["name"], "Custom Field", "would create"])
            continue

        payload = {k: v for k, v in field.items() if k != "name"} | {"name": field["name"]}
        r = api_post("extras/custom-fields", payload)
        if r.status_code == 201:
            print(f"  OK    {field['name']} (id: {r.json()['id']})")
            results.append([field["name"], "Custom Field", "created"])
        else:
            print(f"  FAIL  {field['name']} — {r.status_code}: {r.text[:160]}")
            results.append([field["name"], "Custom Field", f"FAILED {r.status_code}"])

    return results


def main():
    """Parses CLI args and runs the custom-field bootstrap against the target Nautobot instance."""
    parser = argparse.ArgumentParser(
        description="Bootstrap the shadow IP + controller-managed custom fields"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    args = parser.parse_args()

    print(f"Target Nautobot: {URL}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    results = []
    create_custom_fields(args.dry_run, results)

    print("\n── Summary ────────────────────────────────────────────")
    for row in results:
        print(f"  {row[0]:30s} {row[1]:15s} {row[2]}")


if __name__ == "__main__":
    main()
