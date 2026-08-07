# Architecture — Flow Diagrams

Companion to `02-COMPONENTS.md` (what each piece does) and
`04-COMPONENT-PATHS.md` (where each piece lives). This doc shows how the
pieces connect and the order things happen in, for each of the three
distinct flows this codebase supports: **onboarding**, **day-2 sync**,
and **ad-hoc agent troubleshooting**.

---

## 1. System-level component map

```mermaid
flowchart TB
    subgraph Core["Nautobot core — system of record"]
        NB["Nautobot web\n(REST API + UI + Jobs)"]
        PG[("Postgres")]
        RD[("Redis\n(cache + Celery broker +\nconcurrency counters)")]
        CW["Celery worker(s)\nqueue: nautobot_day2_sync"]
    end

    subgraph Cred["Credential store"]
        BAO[("OpenBao\n(KV v2, AppRole auth)")]
        ENV["Tenant .env files\n(legacy fallback)"]
    end

    subgraph Onb["Onboarding"]
        WIZ["Web wizard\nupload_app.py :8081"]
        CHAT["ChatOps\n/nautobot ..."]
        CLI["onboard_cli.py\n(terminal, lab use)"]
    end

    subgraph Broker["Agent Broker"]
        API["REST API\napi_server.py :8082"]
        MCP["MCP server\nmcp_server.py :8090"]
        CORE["broker/core.py\n(shared logic)"]
    end

    subgraph Devices["Customer network"]
        DEV["Switches / Firewalls\n(SSH via Netmiko)"]
        CLOUD["Vendor cloud APIs\n(Mist, Aruba Central)"]
    end

    WIZ -->|REST| NB
    WIZ --> BAO
    WIZ -.writes fallback.-> ENV
    CHAT --> NB
    CLI --> NB
    CLI --> ENV

    NB --- PG
    NB --- RD
    CW --- RD
    CW -->|reads facts/writes| NB
    CW --> BAO
    CW -->|SSH/API| DEV
    CW --> CLOUD

    API --> CORE
    MCP --> CORE
    CORE -->|read-only lookup| NB
    CORE -->|fetch credential| BAO
    CORE -->|Nornir dispatch| DEV
    CORE --> CLOUD

    Agent["External AI agent\n(troubleshooting)"] --> API
    Agent --> MCP
    Human["NOC engineer"] --> API
```

**Key structural point**: the Agent Broker (`core.py`) and the sync engine
(`sync_network_data.py`) both resolve *device → vendor block → credential
→ dispatch* the same way, but they are triggered differently — the sync
engine runs on a schedule/Job trigger and writes results back to
Nautobot; the broker runs on-demand per request and returns raw output to
whoever asked. Neither one owns the other; `broker/core.py` imports
several resolver functions directly from `sync_network_data.py` to avoid
re-implementing vendor/credential resolution twice.

---

## 2. Onboarding flow (new customer or new site)

```mermaid
sequenceDiagram
    participant E as Engineer
    participant W as Wizard (upload_app.py)
    participant CT as create_tenant.py
    participant VM as vendor_matrix.py
    participant N as Nautobot
    participant B as OpenBao
    participant OB as nautobot_onboard_v2.py

    E->>W: Step 1 — tenant name, vendors, site
    W->>CT: run_create_tenant(profile)
    CT->>VM: derive required objects for these vendors
    CT->>N: create Tenant, Namespace, Secrets Groups,\nExternal Integrations
    CT-->>W: profile JSON (saved to tenants_dir)

    E->>W: Step 2 — device rows (table or CSV)
    E->>W: Step 3 — validate
    W->>W: cross-row checks (IP dup, stack/HA grouping,\nvendor/access-method match)

    E->>W: Step 4 — enter credentials
    W->>B: write credential values (KV v2, per secrets-group prefix)
    W->>N: patch controller base_url onto External Integration

    E->>W: Step 5 — test credentials
    W->>B: fetch just-saved values
    W->>W: real SSH login / API token check per vendor

    E->>W: Step 6 — deploy
    W->>OB: process_csv(ready_rows)
    OB->>N: create Locations, Devices, IPs,\nVirtualChassis / DeviceRedundancyGroup
    W->>N: POST /api/extras/jobs/<SyncNetworkData-id>/run/
    N->>N: dispatch Celery fan-out (see flow 3 below)
```

## 3. Day-2 sync flow (scheduled or on-demand)

```mermaid
flowchart LR
    A["Trigger:\nJobs UI / scheduler /\nChatOps / wizard deploy"] --> B["SyncNetworkData or\nSyncAllSites Job.run()"]
    B --> C["Resolve device list\n(get_devices_for_site)"]
    C --> D["group() — one\nsync_device_task\nper device"]
    D --> E{"Per-site\nconcurrency slot\navailable?\n(site_slot)"}
    E -- no --> R1["SiteAtCapacity —\nretry in 15s,\nup to 20x"]
    E -- yes --> F["Load tenant .env\n(fresh, overrides)"]
    F --> G["Fetch/resolve credential\nfrom OpenBao"]
    G --> H["resolve_vendor() →\nvendor_commands.yaml block"]
    H --> I{"SSH or API?"}
    I -- SSH --> J["Netmiko dispatch\n(ssh_get_data)"]
    I -- API --> K["Vendor cloud API\n(api_get_data)"]
    J --> L["Parse output →\nwrite_facts() to Nautobot"]
    K --> L
    L --> M{"Transient failure?\n(TIMEOUT/SSH_ERROR)"}
    M -- yes, retries left --> N2["retry with backoff\n15s * 2^n"]
    M -- no / terminal --> O["Return result dict\n(status, writes, neighbors)"]
    N2 --> F
    O --> P["chord callback:\nsync_summary_callback\n— runs once ALL\ndevice tasks in batch finish"]
    P --> Q["write_cables()\nfor every device's\nLLDP neighbors\n(batched — no race)"]
    Q --> S["One summary\nJobLogEntry on the\ndispatching Job's result"]
```

**Why the batched cable step matters**: cable creation depends on the
`lldp_hostname` custom field that `write_facts()` sets *per device*. If
cables were created inside `sync_device_task` itself (per device, as it
finishes), two devices that are each other's LLDP neighbor would only get
cabled if they happened to finish in a lucky order. Deferring it to the
chord callback — which only runs after every task in the batch has
completed — removes that race entirely.

## 4. Agent Broker flow (ad-hoc troubleshooting)

```mermaid
flowchart LR
    A["NOC engineer or\nexternal AI agent"] --> B["REST POST /diagnose\nor MCP run_command"]
    B --> C{"⚠️ Command allowlist?\n— NOT YET BUILT,\nsee gaps doc"}
    C -.would reject.-> R["Logged, nothing sent\n(not implemented today)"]
    C --> D["get_device_context():\nlook up device in Nautobot\n(tenant, IP, platform,\nrole, secrets_group)"]
    D --> E["fetch_device_credential():\nOpenBao read,\nsecrets_group → KV path"]
    E --> F{"API-managed\n(Mist/Aruba Central)\nor SSH?"}
    F -- SSH --> G["Nornir + Netmiko\nsend_command"]
    F -- API --> H["Nornir task wrapping\na direct requests call\n(org_id auto-substituted)"]
    G --> I["Raw output returned\nto caller"]
    H --> I
```

**Special case — Fortinet APs**: a real FortiAP has no independently
reachable SSH server (confirmed against real hardware — a genuine TCP
timeout). Both the sync engine and the broker redirect the connection
target to the AP's site's controlling FortiGate firewall's IP when the
resolved vendor block is `fortinet_ap_ssh`.

## 5. Per-tenant production topology

For a multi-customer MSP deployment, the shared/stateful core is **not**
duplicated per tenant, but the Agent Broker **is** — it's the one
component that actually reaches into a live customer network, so it gets
both a network boundary and a credential-scope boundary per tenant. Full
detail and rationale: `deploy/PRODUCTION_GUIDE.md` §1–§4 (which contains
the authoritative version of this diagram).

```mermaid
flowchart TB
    subgraph Shared["Shared cluster — one for the whole MSP"]
        PG[("Postgres")]
        RD[("Redis")]
        NB["Nautobot web"]
        CW["Celery workers\n(nautobot_day2_sync)"]
        BAO["OpenBao server\n(one server, per-tenant AppRoles)"]
        UW["Onboarding wizard"]
        CO["ChatOps"]
    end
    subgraph TenantA["Per-tenant — Customer A"]
        BA["Agent Broker (REST+MCP)\nscoped to A's network + AppRole"]
    end
    subgraph TenantB["Per-tenant — Customer B"]
        BB["Agent Broker (REST+MCP)\nscoped to B's network + AppRole"]
    end
    NB --- PG
    CW --- PG
    CW --- RD
    CW --> BAO
    UW --> NB
    UW --> BAO
    CO --> NB
    BA --> NB
    BA --> BAO
    BA -.-> DevA["Customer A devices"]
    BB --> NB
    BB --> BAO
    BB -.-> DevB["Customer B devices"]
```
