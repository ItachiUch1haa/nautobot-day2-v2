# Graph Report - nautobot  (2026-08-24)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 863 nodes · 1456 edges · 48 communities (40 shown, 8 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 100 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `242bbf81`
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
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47

## God Nodes (most connected - your core abstractions)
1. `ControllerAuthError` - 20 edges
2. `OnboardingSession` - 19 edges
3. `main()` - 17 edges
4. `APControllerClient` - 16 edges
5. `sync_device()` - 16 edges
6. `process_csv()` - 16 edges
7. `NautobotClient` - 15 edges
8. `block1_new_tenant()` - 15 edges
9. `process_row()` - 14 edges
10. `run_diagnostic_commands()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `add_static_device()` --uses--> `StaticDeviceValidationError`  [INFERRED]
  nautobot_day2/onboarding_mcp/tools_schema.py → nautobot_day2/onboarding_mcp/intake/static_device.py
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/onboard_cli.py → nautobot_day2/client.py
- `fetch_all()` --uses--> `NautobotAPIError`  [INFERRED]
  nautobot_day2/onboarding/upload_app.py → nautobot_day2/client.py
- `get_device_context()` --uses--> `NautobotClient`  [INFERRED]
  nautobot_day2/broker/core.py → nautobot_day2/client.py
- `ArubaCentralClient` --uses--> `ControllerAuthError`  [INFERRED]
  nautobot_day2/onboarding_mcp/controllers/aruba_central_client.py → nautobot_day2/onboarding_mcp/controllers/base.py

## Import Cycles
- None detected.

## Communities (48 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (59): _create_mgmt_interface_and_link_ip(), deploy_device(), DeployError, Exception, deploy_site's Nautobot object-creation step (architecture doc §7, §9). Reuses…, Create one queued device (static or controller-managed AP) in Nautobot. Returns…, Trigger the SyncNetworkData Job the exact same way upload_app.py's /api/deploy…, Raised when a queued device can't be created in Nautobot. (+51 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (58): ask(), banner(), block1(), block1_existing_tenant(), block1_new_tenant(), block2_location(), block3_device_data(), block5_onboard() (+50 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (39): get_logger(), NautobotAPIError, NautobotClient, Exception, nautobot_day2.client Shared Nautobot REST client — one place for config, auth,…, Send a DELETE request to a Nautobot API endpoint., GET a full URL Nautobot handed back (e.g. a hyperlinked `primary_ip4.url`),…, Fetch every page of a Nautobot list endpoint; return the combined results. (+31 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (50): api_create_tenant(), api_deploy(), api_download_ready_csv(), api_generate_ready_csv(), api_managed_by(), api_platforms(), api_roles(), api_save_credentials() (+42 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (45): api_get_all(), api_patch(), api_post(), auto_link_controller_integration(), build_location_hierarchy(), get_or_create_controller(), get_or_create_controller_group(), get_or_create_device() (+37 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (42): APDiscoveryError, Exception, scan_ap_controller -> select_discovered_aps (architecture doc §4). Tags each…, Raised when select_discovered_aps references an ap_id not present in the most…, Assign a stable ap_id (MAC if present, else scan-order index) to each raw…, Given the most recent scan's tagged candidates and a list of ap_ids to select,…, select(), tag_candidates() (+34 more)

### Community 6 - "Community 6"
Cohesion: 0.08
Nodes (31): fixture, connect(), IllegalTransitionError, OnboardingSession, Exception, Session state machine for onboarding-mcp -- architecture doc §3 (states) and §4…, Return the session's current state + full pending batch (get_session_status —…, Validate that `tool_name` is legal from this session's current state, apply… (+23 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (37): custom_route, device_info(), diagnose(), diagnose_batch(), health(), metrics(), route, broker/api_server.py Agent Broker — REST API wrapper. Thin HTTP layer over… (+29 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (39): api_post(), association_exists(), create_external_integrations(), create_namespace(), create_secrets_groups(), create_tenant_record(), _existing_env_vars(), exists_by_name() (+31 more)

### Community 9 - "Community 9"
Cohesion: 0.09
Nodes (31): _cache_key(), Exception, Per-site concurrency guard for fanned-out device tasks. Nautobot's Celery…, Raised when a site's concurrency slot can't be acquired right now., Acquire one of `max_concurrent` concurrency slots for `site_key` (e.g. "acme-…, site_slot(), SiteAtCapacity, _load_sync() (+23 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (29): api_get(), api_post(), create_custom_field(), create_device_roles(), create_location_types(), create_manufacturers(), create_platforms(), create_tags() (+21 more)

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (27): fail(), find_block(), hdr(), info(), list_vendors(), load_yaml(), main(), ok() (+19 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (24): api_validate_credentials(), Test that a tenant's stored credentials actually work, reusing…, api_test_device(), api_vendors(), find_block(), health(), index(), load_yaml() (+16 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (11): ABC, Aruba Central controller adapter (architecture doc §5). DEVIATION FROM THE…, APControllerClient, APControllerClient ABC -- the wireless-side analog of vendor_commands.yaml +…, Common interface every AP controller adapter (local SSH master, Meraki, Mist,…, Return True if the controller is reachable and the given credentials…, Return a list of candidate APs: [{name, model, mac, current_ip, site_label,…, Return the list of credential field names set_ap_controller must collect for… (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.14
Nodes (17): derive_objects(), Walk the selections and derive: - secrets_groups : { prefix → full_name } -…, derive_expected_vars(), Derive the exact list of env vars expected for this tenant based on their…, get_all_platforms(), get_env_vars(), get_external_integration_name(), get_secrets_group_prefix() (+9 more)

### Community 15 - "Community 15"
Cohesion: 0.23
Nodes (16): fill_creds(), _load_onboard_module(), onboard(), _onboard_check(), _onboard_site(), _onboard_sync(), _profile_and_env_path(), nautobot_day2 ChatOps commands — Slack today, Microsoft Teams later (both are… (+8 more)

### Community 16 - "Community 16"
Cohesion: 0.19
Nodes (15): check_credentials(), list_tenants(), load_profile(), main(), mask_value(), credential_checker.py Verifies all required env vars for a tenant are present…, Show all tenant profiles and their credential status., Parses CLI args and runs either tenant listing or credential checking. (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.18
Nodes (11): JobHookReceiver, CatalogShadowIP, Meta, Catalogs the shadow IP the moment a new real IP is created (via onboarding…, Catalog a shadow IP for a newly created real IP, and point primary_ip4 at it., Declares the job hook's display name shown in Nautobot's job list., Meta, OnboardSite (+3 more)

### Community 18 - "Community 18"
Cohesion: 0.18
Nodes (13): api_get_data(), _aruba_central_get_token(), _find_or_create_software_version(), sync_network_data.py Phase 6 — Sync live network data into Nautobot. YAML-…, # NOTE: a params={'mac_address__n': ''} filter was tried first and, Find or create a SoftwareVersion object for this platform+version.…, Write INVENTORY facts (firmware, MAC, management IP) to their real Nautobot…, Simulated API response per API-type block. (+5 more)

### Community 19 - "Community 19"
Cohesion: 0.19
Nodes (12): CredentialWriteError, Exception, deploy_site's credential-writing step (architecture doc §7, §9). Reuses…, Write a controller's credentials straight to OpenBao at…, Raised when a static device or controller credential can't be resolved/written., Static devices (firewall/sdwan/switch) collected via add_static_device only…, Ensure each pending static device's SecretsGroup exists (via create_tenant.py's…, _static_device_access_method() (+4 more)

### Community 20 - "Community 20"
Cohesion: 0.20
Nodes (8): ControllerAuthError, Exception, Raised by test_connection()/discover_aps() when the controller rejects the…, MerakiClient, AP discovery against the Meraki Dashboard API., meraki collects api_key, org_id, network_id (architecture doc §5)., Confirm the API key authenticates by fetching the organization's own record., GET /organizations/{org_id}/devices filtered to productType=wireless, mapped to…

### Community 21 - "Community 21"
Cohesion: 0.18
Nodes (12): api_get_all(), get_active_status_id(), get_devices(), get_devices_for_site(), init_cache(), natural_to_slug(), Fetch all paginated results from a Nautobot API endpoint., Return the ID of the 'Active' status, caching it after the first lookup. (+4 more)

### Community 22 - "Community 22"
Cohesion: 0.17
Nodes (12): api_patch(), api_post(), _find_interface(), Create a new object at a Nautobot API endpoint., Update an existing object at a Nautobot API endpoint by ID., Create or update device interfaces in Nautobot, returning the count written., Find interface ID by name with fallback normalization: - Exact match:…, Create cables in Nautobot from LLDP neighbor data. 3-level matching: 1.… (+4 more)

### Community 23 - "Community 23"
Cohesion: 0.18
Nodes (12): extract_facts(), extract_interfaces(), extract_lldp(), _find_site_firewall_ip(), _parse_wtp_status_blocks(), Parse LLDP neighbor output into structured list. Returns list of dicts:…, For Fortinet AP rows using the fortinet_ap_ssh fallback: a real FortiAP has no…, Splits `get wireless-controller wtp-status` output into one dict per managed… (+4 more)

### Community 24 - "Community 24"
Cohesion: 0.20
Nodes (7): React to a real IPAddress creation by computing and cataloging its shadow IP., Scheduled Job (schedule via Nautobot's Job Scheduling UI, 10-15 min per…, compute_real_ip(), compute_shadow_ip(), Offset-preserving shadow IP calculation -- the only math in the shadow IP…, Return the shadow IP that corresponds to a real IP, given the real and shadow…, Inverse of compute_shadow_ip -- used by the reconciliation job's reverse…

### Community 25 - "Community 25"
Cohesion: 0.24
Nodes (8): REST-triggerable wrapper around site_onboarding.onboard_site(). Exists because…, Validate and create the real/shadow Prefix pair, logging the result., onboard_site(), Exception, Hook point for the existing per-site onboarding flow: given a site's real…, Raised when a real/shadow CIDR pair fails validation before onboarding a site., Call this once per site as part of the existing onboarding flow, before device…, ShadowIPValidationError

### Community 26 - "Community 26"
Cohesion: 0.22
Nodes (7): Meta, MistSyncJob, Job, Juniper Mist → Nautobot Sync Job Syncs all devices from a Mist org into…, Create or update a single device., Sync all devices from Juniper Mist into Nautobot., Declares the job's display name and description shown in the Nautobot job list.

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (5): ArubaCentralClient, AP discovery against the Aruba Central API., aruba_central collects base_url, client_id, client_secret, refresh_token…, Confirm the refresh token exchanges for a valid access token., GET /monitoring/v2/aps, optionally scoped to a site/group filter, mapped to the…

### Community 28 - "Community 28"
Cohesion: 0.24
Nodes (5): LocalSSHMasterClient, Confirm the controller is reachable and the credentials authenticate, by…, Run this platform's discovery command and parse the output into the common…, AP discovery against a local wireless controller reachable over SSH., local_ssh_master collects mgmt_ip, username, password_or_key (architecture doc…

### Community 29 - "Community 29"
Cohesion: 0.20
Nodes (10): load_last_failed(), main(), print_results(), Print a formatted summary table of sync results, per-block stats, and failures;…, Return the set of device names that failed in the last sync manifest for this…, Write the sync results as both a latest-run manifest and a timestamped history…, Parse CLI args and run the network data sync for the given site/tenant,…, Resolve (platform_slug, role_name, sg_name) → (section, yaml_key). Priority: 1.… (+2 more)

### Community 30 - "Community 30"
Cohesion: 0.22
Nodes (5): MistClient, AP discovery against the Juniper Mist API., mist collects base_url, api_token, org_id, site_id (architecture doc §5)., Confirm the token authenticates by fetching the site's own record., GET /api/v1/sites/{site_id}/devices?type=ap, mapped to the common candidate…

### Community 31 - "Community 31"
Cohesion: 0.22
Nodes (5): OtherGenericClient, Placeholder adapter for a controller type with no dedicated implementation yet., other' accepts whatever keys the caller's config blob already contains — no…, No generic connectivity check exists for an unknown adapter — always fails…, Placeholder — raises until a real adapter is implemented for this…

### Community 32 - "Community 32"
Cohesion: 0.22
Nodes (5): Holds the outcome, writes, and extracted data for one device's sync., Mark this result as a successful sync., Mark this result as skipped with reason r., Mark this result as failed with error type t, message m, and optional fix f., SyncResult

### Community 33 - "Community 33"
Cohesion: 0.39
Nodes (7): _api_token(), _base_url(), get_dhcp_leases(), get_ippools(), FortiOS REST API client for the FortiGate NVA that performs the shadow-IP NAT…, Return {mac: current_real_ip} for every active DHCP lease in the given VDOM., Return a list of (start, end) address ranges for every firewall IP pool…

### Community 34 - "Community 34"
Cohesion: 0.33
Nodes (6): Derive credentials from secrets_group name + env vars., resolve_creds(), fetch_openbao_secret(), _openbao_login(), Shared OpenBao KV v2 client, used by both sync_network_data.py (the…, Fetch a secret from OpenBao KV v2 using AppRole auth. The AppRole login is…

### Community 35 - "Community 35"
Cohesion: 0.29
Nodes (6): Meta, Job, Reconcile each device's real IP against the FortiGate NVA's live DHCP leases,…, Declares the job's display name and description shown in the Nautobot job list., For every customer namespace with a configured VDOM, catch and correct real-IP…, ReconcileDeviceIPs

### Community 36 - "Community 36"
Cohesion: 0.33
Nodes (6): Generate realistic fake command output keyed by yaml_key. Output format:…, Manually send a command and read output, handling '-- MORE --' pagination by…, Run SSH commands against a device. SIMULATED=True: returns fake output per…, _send_command_paginated(), _sim_output(), ssh_get_data()

### Community 37 - "Community 37"
Cohesion: 0.40
Nodes (4): NautobotDay2Config, Nautobot Day 2 Operations — customer onboarding and network sync automation., App configuration that registers the Nautobot Day 2 Operations app with…, NautobotAppConfig

### Community 38 - "Community 38"
Cohesion: 0.50
Nodes (3): _parse_aruba_ap_database(), local_ssh_master controller adapter -- a local wireless controller (Aruba…, Parse Aruba `show ap database` text output into the common candidate shape.…

### Community 39 - "Community 39"
Cohesion: 0.50
Nodes (4): get_yaml_block(), load_vendor_commands(), Load and cache the vendor_commands.yaml file, returning the cached copy on…, Return the YAML block for (section, yaml_key). None if not found.

## Knowledge Gaps
- **4 isolated node(s):** `deploy-prod.sh script`, `deploy-staging.sh script`, `rollback-prod.sh script`, `nautobot-day2`
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `api_validate_credentials()` connect `Community 12` to `Community 0`, `Community 3`, `Community 14`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `NautobotClient` connect `Community 2` to `Community 7`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `ControllerAuthError` connect `Community 20` to `Community 5`, `Community 38`, `Community 13`, `Community 27`, `Community 28`, `Community 30`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `ControllerAuthError` (e.g. with `ArubaCentralClient` and `LocalSSHMasterClient`) actually correct?**
  _`ControllerAuthError` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `OnboardingSession` (e.g. with `test_full_happy_path_static_device()` and `test_hard_sequencing_rule_device_intake_unreachable_without_set_site()`) actually correct?**
  _`OnboardingSession` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `deploy-prod.sh script`, `deploy-staging.sh script`, `rollback-prod.sh script` to the rest of the system?**
  _4 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.05552617662612375 - nodes in this community are weakly interconnected._