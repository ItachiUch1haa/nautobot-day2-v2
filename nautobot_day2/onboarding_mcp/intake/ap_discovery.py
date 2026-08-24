"""
scan_ap_controller -> select_discovered_aps (architecture doc §4). Tags
each raw discover_aps() candidate with a stable ap_id so a later
select_discovered_aps call can reference it, and turns a selection into
pending-batch entries tagged controller_managed=True (architecture doc §5)
for the eventual generic controller-redirect refactor (Phase 5) to key off.

Confirmed decision: re-running scan_ap_controller does not clear anything
already select_discovered_aps'd — that's handled by tools_schema.py /
the session state machine simply never clearing pending_devices on a
scan_ap_controller call (it's a no-mutate, stays-in-CONTROLLER_SCAN
transition); this module only needs to not assume the pending batch is
empty when producing new selections.
"""


class APDiscoveryError(Exception):
    """Raised when select_discovered_aps references an ap_id not present in the most recent scan."""


def tag_candidates(raw_candidates):
    """Assign a stable ap_id (MAC if present, else scan-order index) to each raw discover_aps() candidate."""
    tagged = []
    for i, candidate in enumerate(raw_candidates):
        ap_id = candidate.get("mac") or f"idx-{i}"
        tagged.append({**candidate, "ap_id": ap_id})
    return tagged


def select(scanned_candidates, ap_ids, controller_ref):
    """
    Given the most recent scan's tagged candidates and a list of ap_ids
    to select, return pending-batch entries for each — tagged
    controller_managed=True and pointing at controller_ref (the
    credential reference key written by deploy/credential_writer.py, not
    the secret itself). Raises APDiscoveryError if any ap_id doesn't
    match the most recent scan (e.g. a stale id from before a re-scan).
    """
    by_id = {c["ap_id"]: c for c in scanned_candidates}
    missing = [ap_id for ap_id in ap_ids if ap_id not in by_id]
    if missing:
        raise APDiscoveryError(f"ap_id(s) not found in the most recent scan: {missing}")

    selected = []
    for ap_id in ap_ids:
        c = by_id[ap_id]
        selected.append({
            "role": "ap",
            "name": c["name"],
            "model": c.get("model"),
            "mac": c.get("mac"),
            "current_ip": c.get("current_ip"),
            "site_label": c.get("site_label"),
            "controller_managed": True,
            "managing_controller": controller_ref,
        })
    return selected
