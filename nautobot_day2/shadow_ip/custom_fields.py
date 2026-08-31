"""
One-time bootstrap for the custom fields this package's data model needs.
Mirrors onboarding/bootstrap_nautobot.py's create_custom_field() pattern
exactly -- same idempotent REST-based creation, same script shape -- run
once via `python3 custom_fields.py [--dry-run]` as part of this build's
new INSTALL.md phase, same as bootstrap_nautobot.py is for Phase 14.

LIVE-VERIFIED on the lab server (Nautobot 2.3.16): the "object" custom
field type used below for nat_shadow_prefix/mapped_shadow_ip/
managing_controller does not exist on this Nautobot version at all --
confirmed against the live `OPTIONS /api/extras/custom-fields/` schema,
whose `type` enum is only text/integer/boolean/date/url/select/
multi-select/json/markdown (object/multi-object custom field types were
added in a later Nautobot release than what's deployed here). All three
fields are now `type: "text"` storing the target object's UUID as a
plain string instead -- this is not a workaround, it's what every
consumer already did: every job (CatalogShadowIP, ReconcileDeviceIPs,
ValidateVIPCoverage, etc.) only ever reads/writes these via
`custom_field_data["field_name"] = str(other_obj.id)` and
`Model.objects.get(id=that_string)`, through the Django ORM directly,
never through the REST API's object-type serialization. The only thing
lost is Nautobot's built-in clickable-reference UI/validation for an
"object" field -- functionally these behave identically for this
codebase's own use.

LIVE-VERIFIED, found while debugging why a real device's shadow IP never
got linked: this script's POST payload only ever sent "name", assuming
that determines the field's `key` (the identifier used inside
custom_field_data JSON). On this Nautobot version it does not -- `key` is
the real field, and when omitted, Nautobot auto-generates it by
slugifying `label` instead, silently ignoring "name". This worked by
sheer coincidence for every field whose label happens to slugify to the
same string as its intended key (e.g. "FortiGate VDOM" -> fortigate_vdom)
-- but "real_ip"'s label, "Real IP (denormalized)", slugified to
`real_ip_denormalized`, a completely different key than any code in this
package actually reads or writes. Confirmed directly: Nautobot logged
`Unknown field key 'real_ip' in custom field data` the first time
real code tried to set it, and two stray `real_ip_denormalized[_2]`
fields exist from earlier runs repeatedly re-attempting creation (the
"already exists" check below compares against `field["name"]`, which
never matched the wrong key those earlier runs actually got assigned).
Fixed by sending `key` explicitly instead of relying on label slugging --
this is the actual identifier that matters; `label` is now free to be
any human-readable text without affecting correctness. The two stray
`real_ip_denormalized[_2]` fields from before this fix are harmless,
unused cruft -- safe to delete from Nautobot's UI (Extensibility ->
Custom Fields) if you want to tidy up, not required for correctness.
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
        "type": "text",
        "content_types": ["ipam.prefix"],
    },
    {
        "name": "mapped_shadow_ip",
        "label": "Mapped Shadow IP",
        "type": "text",
        "content_types": ["ipam.ipaddress"],
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
        "type": "text",
        "content_types": ["dcim.device"],
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

        # key is the real identifier (see module docstring for why this
        # can't be left to Nautobot's label-slugging); name is kept too in
        # case it's meaningful on some other Nautobot version, but key is
        # what actually determines correctness here.
        payload = {k: v for k, v in field.items() if k != "name"} | {
            "name": field["name"],
            "key": field["name"],
        }
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
