# Graph Report - nautobot  (2026-08-24)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 479 nodes · 925 edges · 33 communities (25 shown, 8 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 68 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e2590a72`
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
- Community 30
- Community 31
- Community 32

## God Nodes (most connected - your core abstractions)
1. `main()` - 16 edges
2. `NautobotClient` - 15 edges
3. `process_csv()` - 15 edges
4. `sync_device()` - 15 edges
5. `run_diagnostic_commands()` - 14 edges
6. `block1_new_tenant()` - 14 edges
7. `process_row()` - 14 edges
8. `_validate_rows()` - 13 edges
9. `api_validate_credentials()` - 12 edges
10. `run_create_tenant()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/onboard_cli.py → nautobot_day2/client.py
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/upload_app.py → nautobot_day2/client.py
- `get_device_context()` --uses--> `NautobotClient`  [INFERRED]
  nautobot_day2/broker/core.py → nautobot_day2/client.py
- `init_cache()` --calls--> `get_all_platforms()`  [INFERRED]
  nautobot_day2/onboarding/nautobot_onboard_v2.py → nautobot_day2/onboarding/vendor_matrix.py
- `derive_objects()` --calls--> `get_env_vars()`  [INFERRED]
  nautobot_day2/onboarding/create_tenant.py → nautobot_day2/onboarding/vendor_matrix.py

## Import Cycles
- None detected.

## Communities (33 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (34): _cache_key(), Exception, Per-site concurrency guard for fanned-out device tasks. Nautobot's Celery…, Raised when a site's concurrency slot can't be acquired right now., Acquire one of `max_concurrent` concurrency slots for `site_key` (e.g. "acme-…, site_slot(), SiteAtCapacity, Meta (+26 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (38): api_get_all(), api_patch(), api_post(), auto_link_controller_integration(), build_location_hierarchy(), get_or_create_controller(), get_or_create_controller_group(), get_or_create_device() (+30 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (34): custom_route, device_info(), diagnose(), diagnose_batch(), health(), metrics(), route, broker/api_server.py Agent Broker — REST API wrapper. Thin HTTP layer over… (+26 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (35): api_post(), association_exists(), create_external_integrations(), create_namespace(), create_secrets_groups(), create_tenant_record(), derive_objects(), _existing_env_vars() (+27 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (19): get_logger(), NautobotAPIError, NautobotClient, Exception, nautobot_day2.client Shared Nautobot REST client — one place for config, auth,…, GET a full URL Nautobot handed back (e.g. a hyperlinked `primary_ip4.url`),…, Fetch every page of a Nautobot list endpoint; return the combined results., Return an endpoint's result count, or None if the request failed. (+11 more)

### Community 5 - "Community 5"
Cohesion: 0.22
Nodes (31): ask(), banner(), block1(), block1_existing_tenant(), block1_new_tenant(), block2_location(), block3_device_data(), block5_onboard() (+23 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (30): api_create_tenant(), api_deploy(), api_download_ready_csv(), api_generate_ready_csv(), api_platforms(), api_roles(), api_site_types(), api_tenant_profile() (+22 more)

### Community 7 - "Community 7"
Cohesion: 0.15
Nodes (26): check_duplicate_ips(), check_nautobot_ip_exists(), derive_secrets_group(), get_nautobot_cache(), main(), normalize_managed_by(), normalize_platform(), normalize_role() (+18 more)

### Community 8 - "Community 8"
Cohesion: 0.17
Nodes (19): api_get_all(), get_active_status_id(), get_devices(), get_devices_for_site(), get_yaml_block(), init_cache(), load_last_failed(), load_vendor_commands() (+11 more)

### Community 9 - "Community 9"
Cohesion: 0.27
Nodes (16): api_post(), create_custom_field(), create_device_roles(), create_location_types(), create_manufacturers(), create_platforms(), create_tags(), create_tenant_groups() (+8 more)

### Community 10 - "Community 10"
Cohesion: 0.18
Nodes (17): check_credentials(), derive_expected_vars(), list_tenants(), load_profile(), main(), mask_value(), credential_checker.py Verifies all required env vars for a tenant are present…, Show all tenant profiles and their credential status. (+9 more)

### Community 11 - "Community 11"
Cohesion: 0.23
Nodes (16): fill_creds(), _load_onboard_module(), onboard(), _onboard_check(), _onboard_site(), _onboard_sync(), _profile_and_env_path(), nautobot_day2 ChatOps commands — Slack today, Microsoft Teams later (both are… (+8 more)

### Community 12 - "Community 12"
Cohesion: 0.30
Nodes (16): fail(), find_block(), hdr(), info(), list_vendors(), load_yaml(), main(), ok() (+8 more)

### Community 13 - "Community 13"
Cohesion: 0.26
Nodes (14): api_validate_credentials(), Test that a tenant's stored credentials actually work, reusing…, api_test_device(), api_vendors(), find_block(), health(), index(), load_yaml() (+6 more)

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (11): api_vendors(), Return enabled vendors from vendor_matrix., get_all_platforms(), get_enabled_vendors(), get_secrets_group_prefix(), vendor_matrix.py Single source of truth for all supported vendor/device/access…, Returns list of vendor slugs that have at least one enabled device type., Returns secrets group prefix for a given combo. (+3 more)

### Community 15 - "Community 15"
Cohesion: 0.20
Nodes (10): api_managed_by(), api_vendor_full_selections(), api_vendor_roles(), Return valid roles for a vendor from vendor_matrix., Return valid managed_by options for vendor+role combo., Given ?vendors=aruba,juniper, return the full selections dict (every enabled…, get_access_methods(), get_device_types_for_vendor() (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.29
Nodes (8): derive_platform(), Derive default platform slug from vendor+device_type., api_vendor_platforms(), Return valid platforms for vendor+role combo., get_default_platform(), get_platforms_for_combo(), Returns all platforms for a vendor+device_type combo., Returns the default platform slug for a vendor+device_type combo.

### Community 17 - "Community 17"
Cohesion: 0.25
Nodes (8): api_get_data(), _aruba_central_get_token(), Simulated API response per API-type block., Exchange refresh_token for access_token. Auto-saves new refresh_token back to…, Fetch data from cloud API. SIMULATED_OVERRIDE controls per-vendor live/sim…, _sim_api_output(), Merge-update specific fields in an existing OpenBao secret — used for…, update_rotated_credential()

### Community 18 - "Community 18"
Cohesion: 0.29
Nodes (8): api_patch(), extract_interfaces(), _find_site_firewall_ip(), For Fortinet AP rows using the fortinet_ap_ssh fallback: a real FortiAP has no…, Parse vendor-specific interface output into structured list. Returns list of…, sync_device(), write_facts(), write_interfaces()

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (6): Derive credentials from secrets_group name + env vars., resolve_creds(), fetch_openbao_secret(), _openbao_login(), Shared OpenBao KV v2 client, used by both sync_network_data.py (the…, Fetch a secret from OpenBao KV v2 using AppRole auth. The AppRole login is…

### Community 20 - "Community 20"
Cohesion: 0.33
Nodes (6): extract_facts(), extract_lldp(), _parse_wtp_status_blocks(), Parse LLDP neighbor output into structured list. Returns list of dicts:…, Splits `get wireless-controller wtp-status` output into one dict per managed…, Extract device facts from SSH command output or API response. API responses…

### Community 21 - "Community 21"
Cohesion: 0.33
Nodes (6): Generate realistic fake command output keyed by yaml_key. Output format:…, Manually send a command and read output, handling '-- MORE --' pagination by…, Run SSH commands against a device. SIMULATED=True: returns fake output per…, _send_command_paginated(), _sim_output(), ssh_get_data()

### Community 22 - "Community 22"
Cohesion: 0.40
Nodes (5): api_post(), _find_interface(), Find interface ID by name with fallback normalization: - Exact match:…, Create cables in Nautobot from LLDP neighbor data. 3-level matching: 1.…, write_cables()

### Community 24 - "Community 24"
Cohesion: 0.50
Nodes (3): NautobotDay2Config, Nautobot Day 2 Operations — customer onboarding and network sync automation., NautobotAppConfig

### Community 25 - "Community 25"
Cohesion: 0.50
Nodes (4): _find_or_create_software_version(), Find or create a SoftwareVersion object for this platform+version.…, Write INVENTORY facts (firmware, MAC, management IP) to their real Nautobot…, write_inventory_objects()

## Knowledge Gaps
- **5 isolated node(s):** `Meta`, `deploy-prod.sh script`, `deploy-staging.sh script`, `rollback-prod.sh script`, `nautobot-day2`
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `NautobotClient` connect `Community 4` to `Community 2`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Why does `api_validate_credentials()` connect `Community 13` to `Community 6`, `Community 7`, `Community 10`, `Community 15`, `Community 16`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Why does `get_device_context()` connect `Community 2` to `Community 4`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **What connects `Meta`, `deploy-prod.sh script`, `deploy-staging.sh script` to the rest of the system?**
  _5 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.07419712070874862 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.09446693657219973 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.08708708708708708 - nodes in this community are weakly interconnected._