# Glossary & Key Concepts

Domain concepts a new reader needs before the other pages fully make
sense. Grouped roughly by topic; alphabetical within each group.

## Nautobot data model

- **Tenant** — a Nautobot object representing one customer. Every
  device, site, and prefix onboarded through this platform belongs to
  exactly one tenant.
- **Namespace** — Nautobot's IPAM container for IP address space.
  Nautobot Day2 creates one namespace per tenant, so two customers can
  both legitimately use overlapping private IP ranges (e.g. two
  customers both on `10.0.0.0/8`) without colliding — this is why
  namespace resolution (finding the *right* namespace, not just *a*
  namespace) matters throughout the codebase.
- **Location / Location hierarchy** — Nautobot's site/place model. This
  platform builds a consistent Region → Country → State → City → Site
  hierarchy for every site it onboards, regardless of which onboarding
  surface created it.
- **Secrets Group** — a Nautobot object that names *which* credential a
  device should use, without storing the credential itself; the actual
  value lives in OpenBao, addressed by a path derived from the tenant
  and the secrets group's prefix.
- **External Integration** — Nautobot's model for "this device/site is
  managed through a third-party controller" (Mist, Aruba Central,
  Meraki) — carries the controller's base URL and links back to the
  credential needed to reach it.
- **Job / Job Hook** — Nautobot's built-in async task types.
  A **Job** is triggered explicitly (UI click, schedule, API call) — e.g.
  `SyncNetworkData`, `OnboardSite`. A **Job Hook** fires automatically in
  reaction to a data change on a specific model — e.g. `CatalogShadowIP`
  fires every time a real device `IPAddress` is created or updated,
  with no explicit trigger needed.

## Shadow IP / VIP tracking

- **Real IP / real prefix** — the IP address and subnet a customer's
  device actually has configured on its own interface, from the
  customer's own private address space.
- **Shadow IP / shadow prefix** — the corresponding address in a shared,
  RFC 6598-style address space that the platform (and every internal
  consumer — sync engine, Agent Broker) actually uses to reach the
  device, because the real address isn't directly routable from the
  MSP's side of the network.
- **Static NAT (offset-preserving)** — the FortiGate NVA's NAT
  configuration maps each real IP to its shadow IP by preserving the
  same host-bit offset within each prefix (e.g. the 10th host in the
  real `/24` maps to the 10th host in the shadow `/24`). This is why the
  shadow-IP computation is pure arithmetic (`shadow_math.py`) rather than
  a lookup table — the mapping is derived, not stored.
- **FortiGate NVA** — the shared Network Virtual Appliance performing
  this static NAT for potentially many customers at once. Distinct from
  a customer's *own* FortiGate firewall (if they use Fortinet as their
  own vendor) — the NVA is the MSP's shared NAT layer, not customer
  equipment.
- **VIP (Virtual IP)** — FortiOS's own term for a configured static-NAT
  mapping object on the firewall itself. `ValidateVIPCoverage` compares
  Nautobot's record of the shadow-prefix mapping against the live VIP
  object on the FortiGate, to catch the two ever drifting apart.
- **`CatalogShadowIP`** — the Job Hook that keeps this all automatic:
  the moment a device's real `IPAddress` is created or updated in
  Nautobot, it computes the corresponding shadow IP, creates/links the
  shadow `IPAddress` record, and points the device's `primary_ip4` at
  the shadow address (not the real one) — so anything that later reads
  "this device's primary IP" gets an address it can actually reach.

## Onboarding pipeline

- **Tenant profile** — a JSON document (per tenant) recording which
  vendors/device-types/access-methods that tenant uses; drives which
  Secrets Groups and External Integrations `create_tenant.py` creates,
  and which credential fields are required.
- **Ready CSV** — the normalized, validated device-data shape
  (`nautobot_ready_<site>.csv`) that `nautobot_onboard_v2.py` actually
  consumes to create devices — the wizard's raw uploaded/entered rows get
  transformed into this shape before deploy.
- **Static device vs. controller-managed device** — a *static* device
  (firewall, switch, router) is onboarded with a fixed management IP
  entered directly; a *controller-managed* device (an AP under Meraki,
  Mist, or Aruba Central) is discovered through its controller's own API
  instead, often without a known IP until later (`DiscoverNewDevices`
  picks it up once the controller's DHCP lease shows it).
- **Idempotent** — used throughout this platform's documentation to mean
  "safe to run again against the same input" — re-running an onboarding
  step against an already-onboarded tenant/site finds and reuses the
  existing objects instead of erroring or duplicating them.

## Credential handling

- **OpenBao** — the credential store. An open-source (Linux Foundation)
  fork of HashiCorp Vault, functionally equivalent for this platform's
  purposes, chosen specifically to avoid Vault's post-fork BSL license.
- **AppRole** — OpenBao's machine-to-machine authentication method: a
  `role_id` (effectively public, safe to hardcode in a compose file) plus
  a `secret_id` (the actual credential, kept in `.env`, never committed)
  together authenticate as a named identity with a specific policy
  attached.
- **KV v2** — OpenBao's versioned key-value secrets engine; this
  platform's credentials live under `kv/data/tenants/<tenant-slug>/<secrets-group-prefix>`.
- **Unseal key / root token** — OpenBao-specific bootstrapping concepts.
  The unseal key decrypts OpenBao's storage on every restart (it is
  *not* a login credential — it's a physical/administrative control,
  typically held by an operator, not baked into any running service).
  The root token is a break-glass, unrestricted-access credential
  normally used only during initial setup; every live service
  authenticates via its own AppRole instead, never the root token.

## Vendor / connectivity

- **`vendor_matrix.py`** — the single source of truth mapping every
  supported vendor + device-type + access-method combination to its
  Nautobot platform, its NAPALM driver (where applicable), which env
  vars its credentials need, and its secrets-group prefix. Every other
  component reads from here rather than hardcoding vendor logic.
- **`vendor_commands.yaml`** — the actual SSH command strings / API
  endpoint paths per vendor+platform that the sync engine and Agent
  Broker dispatch and parse. Adding a new vendor's commands is a data
  change here, not a code change.
- **Nornir** — the Python network-automation framework the Agent Broker
  uses to dispatch commands (via its Netmiko plugin for SSH, or a direct
  `requests` call for API-managed vendors) — chosen for its consistent
  task/result abstraction across both dispatch styles.
