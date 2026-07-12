# =============================================================================
# NEW: /nautobot onboard  — Unified onboarding workflow
# NEW: /nautobot credential-check — Tenant credential status
# Added: 2026-07-02
# =============================================================================

# ── Helpers ───────────────────────────────────────────────────────────────────

def _nb_get_all(endpoint, params=None):
    """Fetch all pages from Nautobot API."""
    results, url = [], f"{NAUTOBOT_URL}/api/{endpoint}/"
    p = dict(params or {}); p["limit"] = 200
    while url:
        try:
            req = urllib.request.Request(
                url + ("?" + urllib.parse.urlencode(p) if p else ""),
                headers=NAUTOBOT_HEADERS
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results.extend(data.get("results", []))
            url = data.get("next"); p = {}
        except Exception:
            break
    return results


def _nb_slug(natural_slug):
    """Strip 4-char suffix from natural_slug."""
    parts = natural_slug.rsplit("_", 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 4 else natural_slug


def _get_tenants():
    """Return list of (slug, name) from Nautobot."""
    tenants = _nb_get_all("tenancy/tenants")
    result = []
    for t in tenants:
        slug = _nb_slug(t.get("natural_slug", ""))
        result.append((slug, t["name"]))
    return sorted(result, key=lambda x: x[1])


def _check_creds(tenant_slug):
    """
    Read /etc/nautobot/tenants/<slug>.env and check for empty vars.
    Returns (ok: bool, summary: str, empty_list: list)
    """
    env_path = f"{TENANT_DIR}/{tenant_slug}.env"
    if not os.path.exists(env_path):
        return False, f"No env file found: `{env_path}`", []
    total, empty = 0, []
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k:
                    total += 1
                    if not v.strip():
                        empty.append(k)
    except Exception as e:
        return False, f"Cannot read env file: {e}", []
    if total == 0:
        return False, "Env file has no variables", []
    if empty:
        return False, f"{len(empty)}/{total} vars not filled", empty
    return True, f"All {total} credentials ready", []


def _list_csvs(tenant_slug):
    """List nautobot_ready CSVs — tenant-filtered if possible, else all."""
    try:
        all_csvs = sorted([
            f for f in os.listdir(CSV_UPLOAD_PATH)
            if f.lower().startswith("nautobot_ready_") and f.lower().endswith(".csv")
        ])
        filtered = [f for f in all_csvs if tenant_slug.replace("-", "_") in f.lower()
                    or tenant_slug in f.lower()]
        return filtered if filtered else all_csvs
    except Exception:
        return []


def _count_csv_devices(filepath):
    """Return device count and site name from a ready CSV."""
    try:
        import io as _io
        with open(filepath, "rb") as f:
            text = f.read().decode("utf-8", errors="replace").lstrip("\ufeff")
        rows = list(csv.DictReader(_io.StringIO(text)))
        site = rows[0].get("site_name", "unknown") if rows else "unknown"
        return len(rows), site
    except Exception:
        return 0, "unknown"


def _run_onboard_sync(dispatcher, tenant_slug, site_name):
    """Background thread: run sync after onboard completes."""
    try:
        env = _load_tenant_envs()
        result = subprocess.run(
            [PYTHON_BIN, SYNC_SCRIPT,
             "--site", site_name,
             "--tenant", tenant_slug],
            capture_output=True, text=True, timeout=600,
            env=env, cwd=CSV_UPLOAD_PATH,
        )
        out = result.stdout or ""

        # Parse summary line
        ok_count = fail_count = 0
        for line in out.splitlines():
            if "Total:" in line and "✅" in line:
                import re as _re
                m = _re.search(r"✅\s+(\d+)", line)
                if m: ok_count = int(m.group(1))
                m = _re.search(r"❌\s+(\d+)", line)
                if m: fail_count = int(m.group(1))

        if result.returncode == 0:
            dispatcher.send_markdown(
                f"✅ *Phase 6 — Sync complete*\n\n"
                f"✅ Synced : `{ok_count}` devices\n"
                f"❌ Failed : `{fail_count}` devices\n\n"
                f"🔗 View: `{NAUTOBOT_URL}/dcim/devices/`\n"
                + (f"\nℹ️ Re-sync failed devices: `/nautobot sync-network`"
                   if fail_count else "")
            )
        else:
            dispatcher.send_markdown(
                f"⚠️ *Phase 6 — Sync had issues*\n\n"
                f"✅ Synced: `{ok_count}` | ❌ Failed: `{fail_count}`\n\n"
                f"Fix credentials and re-run: `/nautobot sync-network`"
            )
    except subprocess.TimeoutExpired:
        dispatcher.send_markdown(
            f"⏰ *Sync timed out* — run manually:\n`/nautobot sync-network`"
        )
    except Exception as e:
        dispatcher.send_markdown(
            f"❌ *Sync error:* `{str(e)[:200]}`\n"
            f"Run manually: `/nautobot sync-network`"
        )


# =============================================================================
# /nautobot onboard
# State machine via action param:
#   None           → Step 1: main menu
#   "new"          → instructions to run create-tenant first
#   "site"         → Step 2: tenant dropdown
#   "site" + value → Step 3: cred check + CSV dropdown
#   "csv:<slug>"   → Step 4: run onboard + sync in background
#   "check"        → credential check tenant dropdown
#   "check" + value→ show credential status
# =============================================================================
@subcommand_of("nautobot")
def onboard(dispatcher, action=None, value=None):
    """Unified site onboarding workflow — tenant select, cred check, onboard, sync.

    Guides through the full onboarding flow in one command.
    For new tenants, run /nautobot create-tenant first.

    Usage: /nautobot onboard
    """

    # ── Step 1: main menu ─────────────────────────────────────────────────────
    if not action:
        dispatcher.prompt_from_menu(
            "nautobot onboard",
            "🔧 *Nautobot — Site Onboarding*\n\nWhat would you like to do?",
            [
                ("site",  "Onboard a new site  (tenant already exists)"),
                ("check", "Check tenant credentials"),
                ("new",   "New customer — create tenant first"),
            ],
        )
        return False

    # ── New tenant instructions ───────────────────────────────────────────────
    if action == "new":
        dispatcher.send_markdown(
            "🏢 *New Customer — Steps*\n\n"
            "*1. Create tenant:*\n"
            "Run `/nautobot create-tenant` → select group → type name\n\n"
            "*2. Fill credentials (server access needed):*\n"
            f"`sudo nano {TENANT_DIR}/<slug>.env`\n"
            f"Then: `sudo systemctl restart nautobot nautobot-worker nautobot-slack`\n\n"
            "*3. Prepare site CSV:*\n"
            f"Upload at `{UPLOAD_PORTAL}` → validate → generate\n\n"
            "*4. Come back here:*\n"
            "Run `/nautobot onboard` → *Onboard a new site*"
        )
        return True

    # ── Credential check ──────────────────────────────────────────────────────
    if action == "check":
        if not value:
            tenants = _get_tenants()
            if not tenants:
                dispatcher.send_markdown("❌ No tenants found in Nautobot.")
                return True
            dispatcher.prompt_from_menu(
                "nautobot onboard check",
                "🔑 *Credential Check* — Select tenant:",
                tenants,
            )
            return False

        ok, summary, empty = _check_creds(value)
        icon   = "✅" if ok else "❌"
        detail = ""
        if empty:
            shown  = empty[:10]
            detail = "\n\n*Empty variables:*\n" + "\n".join(f"  `{v}`" for v in shown)
            if len(empty) > 10:
                detail += f"\n  ...and {len(empty)-10} more"
        fix = ""
        if not ok:
            fix = (
                f"\n\n*Fix:*\n"
                f"1. `sudo nano {TENANT_DIR}/{value}.env`\n"
                f"2. Fill all empty values\n"
                f"3. `sudo systemctl restart nautobot nautobot-worker nautobot-slack`"
            )
        dispatcher.send_markdown(
            f"{icon} *Credentials — `{value}`*\n\n{summary}{detail}{fix}"
        )
        return True

    # ── Site onboard: Step 2 — tenant dropdown ────────────────────────────────
    if action == "site" and not value:
        tenants = _get_tenants()
        if not tenants:
            dispatcher.send_markdown("❌ No tenants found in Nautobot.")
            return True
        dispatcher.prompt_from_menu(
            "nautobot onboard site",
            "🏢 *Onboard New Site* — Select tenant:",
            tenants,
        )
        return False

    # ── Site onboard: Step 3 — cred check + CSV dropdown ─────────────────────
    if action == "site" and value:
        tenant_slug = value
        ok, cred_msg, empty = _check_creds(tenant_slug)
        cred_icon   = "✅" if ok else "⚠️"

        # Cred detail for warning
        cred_detail = ""
        if not ok and empty:
            shown       = empty[:5]
            cred_detail = "\n⚠️ Empty: " + ", ".join(f"`{v}`" for v in shown)
            if len(empty) > 5:
                cred_detail += f" +{len(empty)-5} more"

        csvs = _list_csvs(tenant_slug)
        if not csvs:
            dispatcher.send_markdown(
                f"{cred_icon} *Credentials:* {cred_msg}{cred_detail}\n\n"
                f"📂 *No ready CSVs found for `{tenant_slug}`*\n\n"
                f"Prepare the site CSV first:\n`{UPLOAD_PORTAL}`\n\n"
                f"Then run `/nautobot onboard` again."
            )
            return True

        dispatcher.prompt_from_menu(
            f"nautobot onboard csv:{tenant_slug}",
            f"{cred_icon} *Credentials:* {cred_msg}{cred_detail}\n\n"
            f"📋 Select site CSV to onboard:",
            [(f, f) for f in csvs],
        )
        return False

    # ── Site onboard: Step 4 — run onboard + sync ─────────────────────────────
    if action and action.startswith("csv:"):
        tenant_slug = action[4:]
        csv_file    = value

        if not csv_file:
            dispatcher.send_markdown("❌ No CSV selected.")
            return True

        file_path = os.path.join(CSV_UPLOAD_PATH, csv_file) \
            if not csv_file.startswith("/") else csv_file

        if not os.path.exists(file_path):
            dispatcher.send_markdown(f"❌ File not found: `{file_path}`")
            return True

        device_count, site_name = _count_csv_devices(file_path)

        # Final cred warning — don't block, just inform
        ok, cred_msg, _ = _check_creds(tenant_slug)
        cred_warn = ""
        if not ok:
            cred_warn = (
                f"\n⚠️ *Credentials not fully set* — onboard will succeed "
                f"but sync may fail.\nFill `{TENANT_DIR}/{tenant_slug}.env` "
                f"before running sync.\n"
            )

        dispatcher.send_markdown(
            f"🚀 *Starting onboard workflow*\n\n"
            f"Tenant  : `{tenant_slug}`\n"
            f"Site    : `{site_name}`\n"
            f"CSV     : `{csv_file}`\n"
            f"Devices : `{device_count}`\n"
            f"{cred_warn}\n"
            f"⏳ *Phase 5 — Creating devices...* _(this may take a few minutes)_"
        )

        # ── Phase 5 ───────────────────────────────────────────────────────────
        try:
            result = subprocess.run(
                [PYTHON_BIN, ONBOARD_SCRIPT, "--csv", file_path],
                capture_output=True, text=True, timeout=600,
            )
            output = result.stdout or ""

            # Parse nautobot_onboard_v2.py summary line
            import re as _re
            ok_count = fail_count = 0
            for line in output.splitlines():
                m = _re.search(r"Succeeded\s*:\s*(\d+)", line)
                if m: ok_count = int(m.group(1))
                m = _re.search(r"Failed\s*:\s*(\d+)", line)
                if m: fail_count = int(m.group(1))

            if result.returncode != 0:
                dispatcher.send_markdown(
                    f"❌ *Phase 5 failed*\n\n"
                    f"✅ Created: `{ok_count}` | ❌ Failed: `{fail_count}`\n\n"
                    f"Check: `sudo journalctl -u nautobot-worker -n 50`"
                )
                return True

            dispatcher.send_markdown(
                f"✅ *Phase 5 complete*\n\n"
                f"✅ Created : `{ok_count}` devices\n"
                f"❌ Failed  : `{fail_count}` devices\n\n"
                f"⏳ *Phase 6 — Syncing network data...* _(running in background)_"
            )

        except subprocess.TimeoutExpired:
            dispatcher.send_markdown(
                f"⏰ *Phase 5 timed out* after 10 minutes.\n"
                f"Check: `sudo journalctl -u nautobot-worker -n 50`"
            )
            return True
        except Exception as e:
            dispatcher.send_markdown(f"❌ *Phase 5 error:* `{str(e)[:200]}`")
            return True

        # ── Phase 6 in background ─────────────────────────────────────────────
        threading.Thread(
            target=_run_onboard_sync,
            args=(dispatcher, tenant_slug, site_name),
            daemon=True,
        ).start()
        return True

    # Unknown state
    dispatcher.send_markdown(
        f"❌ Unknown action: `{action}`\n"
        f"Run `/nautobot onboard` to restart."
    )
    return True


# =============================================================================
# /nautobot credential-check — standalone credential check per tenant
# =============================================================================
@subcommand_of("nautobot")
def credential_check(dispatcher, tenant_slug=None):
    """Check if tenant credentials are filled in the env file.

    Usage: /nautobot credential-check [tenant-slug]
    """
    if not tenant_slug:
        tenants = _get_tenants()
        if not tenants:
            dispatcher.send_markdown("❌ No tenants found in Nautobot.")
            return True
        dispatcher.prompt_from_menu(
            "nautobot credential-check",
            "🔑 *Credential Check* — Select tenant:",
            tenants,
        )
        return False

    ok, summary, empty = _check_creds(tenant_slug)
    icon = "✅" if ok else "❌"

    detail = ""
    if empty:
        detail = "\n\n*Empty variables:*\n" + "\n".join(f"  `{v}`" for v in empty[:15])
        if len(empty) > 15:
            detail += f"\n  ...and {len(empty)-15} more"

    fix = ""
    if not ok:
        fix = (
            f"\n\n*Fix:*\n"
            f"1. `sudo nano {TENANT_DIR}/{tenant_slug}.env`\n"
            f"2. Fill all empty values\n"
            f"3. `sudo systemctl restart nautobot nautobot-worker nautobot-slack`\n"
            f"4. Re-run `/nautobot credential-check` to verify"
        )

    dispatcher.send_markdown(
        f"{icon} *Credential Check — `{tenant_slug}`*\n\n"
        f"Status : {summary}"
        f"{detail}"
        f"{fix}"
    )
    return True
