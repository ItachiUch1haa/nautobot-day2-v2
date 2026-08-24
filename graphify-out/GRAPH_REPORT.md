# Graph Report - nautobot  (2026-08-24)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 609 nodes · 1056 edges · 30 communities (23 shown, 7 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 68 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9e5f3130`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29

## God Nodes (most connected - your core abstractions)
1. `main()` - 17 edges
2. `sync_device()` - 16 edges
3. `process_csv()` - 16 edges
4. `NautobotClient` - 15 edges
5. `block1_new_tenant()` - 15 edges
6. `run_diagnostic_commands()` - 14 edges
7. `process_row()` - 14 edges
8. `_validate_rows()` - 13 edges
9. `run_create_tenant()` - 12 edges
10. `api_validate_credentials()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/onboard_cli.py → nautobot_day2/client.py
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/upload_app.py → nautobot_day2/client.py
- `get_device_context()` --uses--> `NautobotClient`  [INFERRED]
  nautobot_day2/broker/core.py → nautobot_day2/client.py
- `block1_new_tenant()` --calls--> `get_access_methods()`  [INFERRED]
  nautobot_day2/onboarding/onboard_cli.py → nautobot_day2/onboarding/vendor_matrix.py
- `api_save_credentials()` --calls--> `derive_objects()`  [INFERRED]
  nautobot_day2/onboarding/upload_app.py → nautobot_day2/onboarding/create_tenant.py

## Import Cycles
- None detected.

## Communities (30 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (58): ask(), banner(), block1(), block1_existing_tenant(), block1_new_tenant(), block2_location(), block3_device_data(), block5_onboard() (+50 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (56): api_post(), association_exists(), create_external_integrations(), create_namespace(), create_secrets_groups(), create_tenant_record(), derive_objects(), _existing_env_vars() (+48 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (50): api_create_tenant(), api_deploy(), api_download_ready_csv(), api_generate_ready_csv(), api_managed_by(), api_platforms(), api_roles(), api_save_credentials() (+42 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (38): _cache_key(), Exception, Per-site concurrency guard for fanned-out device tasks. Nautobot's Celery…, Raised when a site's concurrency slot can't be acquired right now., Acquire one of `max_concurrent` concurrency slots for `site_key` (e.g. "acme-…, site_slot(), SiteAtCapacity, Meta (+30 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (45): api_get_all(), api_patch(), api_post(), auto_link_controller_integration(), build_location_hierarchy(), get_or_create_controller(), get_or_create_controller_group(), get_or_create_device() (+37 more)

### Community 5 - "Community 5"
Cohesion: 0.06
Nodes (30): get_logger(), NautobotAPIError, NautobotClient, Exception, nautobot_day2.client Shared Nautobot REST client — one place for config, auth,…, Send a DELETE request to a Nautobot API endpoint., GET a full URL Nautobot handed back (e.g. a hyperlinked `primary_ip4.url`),…, Fetch every page of a Nautobot list endpoint; return the combined results. (+22 more)

### Community 6 - "Community 6"
Cohesion: 0.08
Nodes (37): custom_route, device_info(), diagnose(), diagnose_batch(), health(), metrics(), route, broker/api_server.py Agent Broker — REST API wrapper. Thin HTTP layer over… (+29 more)

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (39): check_duplicate_ips(), check_nautobot_ip_exists(), derive_platform(), derive_secrets_group(), get_nautobot_cache(), main(), normalize_managed_by(), normalize_platform() (+31 more)

### Community 8 - "Community 8"
Cohesion: 0.10
Nodes (33): fill_creds(), _load_onboard_module(), onboard(), _onboard_check(), _onboard_site(), _onboard_sync(), _profile_and_env_path(), nautobot_day2 ChatOps commands — Slack today, Microsoft Teams later (both are… (+25 more)

### Community 9 - "Community 9"
Cohesion: 0.12
Nodes (29): api_get(), api_post(), create_custom_field(), create_device_roles(), create_location_types(), create_manufacturers(), create_platforms(), create_tags() (+21 more)

### Community 10 - "Community 10"
Cohesion: 0.14
Nodes (27): fail(), find_block(), hdr(), info(), list_vendors(), load_yaml(), main(), ok() (+19 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (24): api_validate_credentials(), Test that a tenant's stored credentials actually work, reusing…, api_test_device(), api_vendors(), find_block(), health(), index(), load_yaml() (+16 more)

### Community 12 - "Community 12"
Cohesion: 0.17
Nodes (15): api_patch(), api_post(), _find_interface(), sync_network_data.py Phase 6 — Sync live network data into Nautobot. YAML-…, Create a new object at a Nautobot API endpoint., Update an existing object at a Nautobot API endpoint by ID., Create or update device interfaces in Nautobot, returning the count written., Find interface ID by name with fallback normalization: - Exact match:… (+7 more)

### Community 13 - "Community 13"
Cohesion: 0.18
Nodes (12): api_get_all(), get_active_status_id(), get_devices(), get_devices_for_site(), init_cache(), natural_to_slug(), Fetch all paginated results from a Nautobot API endpoint., Return the ID of the 'Active' status, caching it after the first lookup. (+4 more)

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (12): extract_facts(), extract_interfaces(), extract_lldp(), _find_site_firewall_ip(), _parse_wtp_status_blocks(), Parse LLDP neighbor output into structured list. Returns list of dicts:…, For Fortinet AP rows using the fortinet_ap_ssh fallback: a real FortiAP has no…, Splits `get wireless-controller wtp-status` output into one dict per managed… (+4 more)

### Community 15 - "Community 15"
Cohesion: 0.20
Nodes (10): load_last_failed(), main(), print_results(), Print a formatted summary table of sync results, per-block stats, and failures;…, Return the set of device names that failed in the last sync manifest for this…, Write the sync results as both a latest-run manifest and a timestamped history…, Parse CLI args and run the network data sync for the given site/tenant,…, Resolve (platform_slug, role_name, sg_name) → (section, yaml_key). Priority: 1.… (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.22
Nodes (5): Holds the outcome, writes, and extracted data for one device's sync., Mark this result as a successful sync., Mark this result as skipped with reason r., Mark this result as failed with error type t, message m, and optional fix f., SyncResult

### Community 17 - "Community 17"
Cohesion: 0.25
Nodes (8): api_get_data(), _aruba_central_get_token(), Simulated API response per API-type block., Exchange refresh_token for access_token. Auto-saves new refresh_token back to…, Fetch data from cloud API. SIMULATED_OVERRIDE controls per-vendor live/sim…, _sim_api_output(), Merge-update specific fields in an existing OpenBao secret — used for…, update_rotated_credential()

### Community 18 - "Community 18"
Cohesion: 0.33
Nodes (6): Derive credentials from secrets_group name + env vars., resolve_creds(), fetch_openbao_secret(), _openbao_login(), Shared OpenBao KV v2 client, used by both sync_network_data.py (the…, Fetch a secret from OpenBao KV v2 using AppRole auth. The AppRole login is…

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (6): Generate realistic fake command output keyed by yaml_key. Output format:…, Manually send a command and read output, handling '-- MORE --' pagination by…, Run SSH commands against a device. SIMULATED=True: returns fake output per…, _send_command_paginated(), _sim_output(), ssh_get_data()

### Community 20 - "Community 20"
Cohesion: 0.40
Nodes (4): NautobotDay2Config, Nautobot Day 2 Operations — customer onboarding and network sync automation., App configuration that registers the Nautobot Day 2 Operations app with…, NautobotAppConfig

### Community 21 - "Community 21"
Cohesion: 0.50
Nodes (4): _find_or_create_software_version(), Find or create a SoftwareVersion object for this platform+version.…, Write INVENTORY facts (firmware, MAC, management IP) to their real Nautobot…, write_inventory_objects()

### Community 22 - "Community 22"
Cohesion: 0.50
Nodes (4): get_yaml_block(), load_vendor_commands(), Load and cache the vendor_commands.yaml file, returning the cached copy on…, Return the YAML block for (section, yaml_key). None if not found.

## Knowledge Gaps
- **4 isolated node(s):** `deploy-prod.sh script`, `deploy-staging.sh script`, `rollback-prod.sh script`, `nautobot-day2`
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `api_validate_credentials()` connect `Community 11` to `Community 1`, `Community 2`, `Community 7`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Why does `NautobotClient` connect `Community 5` to `Community 6`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `SyncResult` connect `Community 16` to `Community 12`, `Community 14`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **What connects `deploy-prod.sh script`, `deploy-staging.sh script`, `rollback-prod.sh script` to the rest of the system?**
  _4 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.08065458796025717 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.056261343012704176 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.0611764705882353 - nodes in this community are weakly interconnected._