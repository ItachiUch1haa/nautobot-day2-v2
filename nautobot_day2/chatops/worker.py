"""
nautobot_day2 ChatOps commands — Slack today, Microsoft Teams later (both are
just adapters on top of nautobot-chatops; this code doesn't change between them).

Replaces the old chatops_onboard_addition.py draft, which was a fragment meant
to be pasted into a different file and worked by shelling out to scripts +
telling engineers to SSH in and edit credential files by hand. Everything here
calls the real onboarding functions directly (same process, same Django ORM
Nautobot itself uses) and collects credentials through a private chat prompt
instead of a channel post.

Commands:
  /nautobot onboard              — menu: new site / check creds / sync now
  /nautobot fill-creds <tenant>  — fill in missing credentials one at a time,
                                    privately (each answer is ephemeral)

New-customer setup (choosing which vendors/device types a tenant uses) still
goes through create_tenant.py's profile JSON or the CLI — that's an inherently
multi-select choice better suited to a form/file than a chat wizard. Once a
tenant profile exists, everything below works from chat.
"""

import importlib.util
import os
import sys

from nautobot.dcim.models import Location
from nautobot.extras.models import Job as JobModel
from nautobot.extras.models import JobResult
from nautobot.tenancy.models import Tenant

from nautobot_chatops.workers import subcommand_of

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # nautobot_day2/
ONBOARDING_DIR = os.path.join(PACKAGE_ROOT, "onboarding")
if ONBOARDING_DIR not in sys.path:
    sys.path.insert(0, ONBOARDING_DIR)

import credential_checker as cc  # noqa: E402  (sibling-import pattern used throughout onboarding/)

SYNC_JOB_MODULE = "nautobot_day2.jobs.sync_network_data_job"
CATEGORY_CHOICES = [
    ("all", "All — switches + firewalls + APs"),
    ("switches", "Switches only"),
    ("firewalls", "Firewalls only"),
    ("aps", "Access Points only"),
]


def _load_onboard_module():
    """Load nautobot_onboard_v2.py the same way jobs/sync_network_data_job.py
    loads sync_network_data.py — a plain module import would work too, but
    this matches the one dynamic-load convention already used in this repo."""
    spec = importlib.util.spec_from_file_location(
        "nautobot_onboard_v2", os.path.join(ONBOARDING_DIR, "nautobot_onboard_v2.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tenant_choices():
    return [(t.name, t.slug) for t in Tenant.objects.all().order_by("name")]


def _site_choices(tenant_slug):
    sites = Location.objects.filter(devices__tenant__slug=tenant_slug).distinct().order_by("name")
    return [(s.name, s.name) for s in sites]


def _profile_and_env_path(tenant_slug):
    profile_path = os.path.join(cc.LAB_PROFILES_DIR, f"{tenant_slug}.json")
    if not os.path.exists(profile_path):
        return None, None
    return cc.load_profile(profile_path), os.path.join(cc.LAB_PROFILES_DIR, f"{tenant_slug}.env")


def _write_env_value(env_path, var_name, value):
    """Write or update one KEY=VALUE line in a tenant's .env file."""
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{var_name}="):
            lines[i] = f"{var_name}={value}\n"
            break
    else:
        lines.append(f"{var_name}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


# ── /nautobot onboard ─────────────────────────────────────────────────────────

@subcommand_of("nautobot")
def onboard(dispatcher, action=None, tenant_slug=None, extra=None):
    """Onboard a new site, check tenant credentials, or trigger a device sync.

    Usage: /nautobot onboard
    """
    if not action:
        dispatcher.prompt_from_menu(
            "nautobot onboard",
            "🔧 *Nautobot — what would you like to do?*",
            [
                ("site", "Onboard a new site (tenant already exists)"),
                ("check", "Check tenant credentials"),
                ("sync", "Sync devices now"),
            ],
        )
        return False

    if action == "check":
        return _onboard_check(dispatcher, tenant_slug)
    if action == "site":
        return _onboard_site(dispatcher, tenant_slug, extra)
    if action == "sync":
        return _onboard_sync(dispatcher, tenant_slug, extra)

    dispatcher.send_error(f"Unknown action '{action}'.")
    return False


def _onboard_check(dispatcher, tenant_slug):
    if not tenant_slug:
        choices = _tenant_choices()
        if not choices:
            dispatcher.send_markdown("No tenants found in Nautobot yet.")
            return True
        dispatcher.prompt_from_menu("nautobot onboard check", "Select tenant:", choices)
        return False

    profile, env_path = _profile_and_env_path(tenant_slug)
    if not profile:
        dispatcher.send_error(
            f"No profile found for tenant '{tenant_slug}' — run create_tenant.py for it first."
        )
        return False

    expected = cc.derive_expected_vars(profile)
    values = cc.read_env_file(env_path) or {}
    missing = [v for v in expected if v not in values]
    empty = [v for v in expected if v in values and not values[v].strip()]
    outstanding = missing + empty

    if not outstanding:
        dispatcher.send_markdown(
            f"✅ *{profile['name']}* — all {len(expected)} credentials are filled in.",
            ephemeral=True,
        )
        return True

    lines = [f"⚠️ *{profile['name']}* — {len(outstanding)} of {len(expected)} credentials still need values:"]
    lines += [f"  • `{v}`" for v in outstanding]
    lines.append(f"\nFill these in now: `/nautobot fill-creds {tenant_slug}`")
    dispatcher.send_markdown("\n".join(lines), ephemeral=True)
    return True


def _onboard_site(dispatcher, tenant_slug, site_name):
    if not tenant_slug:
        choices = _tenant_choices()
        if not choices:
            dispatcher.send_markdown("No tenants found in Nautobot yet.")
            return True
        dispatcher.prompt_from_menu("nautobot onboard site", "Select tenant:", choices)
        return False

    profile, env_path = _profile_and_env_path(tenant_slug)
    if profile:
        expected = cc.derive_expected_vars(profile)
        values = cc.read_env_file(env_path) or {}
        if any(v not in values or not values[v].strip() for v in expected):
            dispatcher.send_error(
                f"'{tenant_slug}' has incomplete credentials — run "
                f"`/nautobot onboard check {tenant_slug}` before onboarding a new site."
            )
            return True

    if not site_name:
        dispatcher.send_markdown(
            "📋 Upload and validate the site's device CSV first, then reply with the "
            "site name to continue (the CSV must already exist as "
            f"`nautobot_ready_<site>.csv` — use the upload portal or "
            "`nautobot_prepare.py`).\n\n"
            "Run again as: `/nautobot onboard site " + tenant_slug + " <site-name>`"
        )
        return True

    ready_csv = os.path.join(ONBOARDING_DIR, f"nautobot_ready_{site_name}.csv")
    if not os.path.exists(ready_csv):
        dispatcher.send_error(
            f"No ready CSV found for site '{site_name}' — validate it via the upload "
            "portal or `nautobot_prepare.py` first."
        )
        return True

    mod = _load_onboard_module()
    mod.init_cache()
    rows = mod.load_csv(ready_csv)
    results = mod.process_csv(rows, dry_run=False)

    failed = [r for r in results if "FAILED" in str(r[2])]
    dispatcher.send_markdown(
        f"🏗️ *{site_name}* — {len(results) - len(failed)}/{len(results)} devices created."
        + (f" ❌ {len(failed)} failed — check the Nautobot server log for details." if failed else "")
    )

    return _onboard_sync(dispatcher, tenant_slug, site_name, "all")


def _onboard_sync(dispatcher, tenant_slug, site_name=None, category=None):
    if not tenant_slug:
        choices = _tenant_choices()
        if not choices:
            dispatcher.send_markdown("No tenants found in Nautobot yet.")
            return True
        dispatcher.prompt_from_menu("nautobot onboard sync", "Select tenant:", choices)
        return False

    tenant = Tenant.objects.filter(slug=tenant_slug).first()
    if not tenant:
        dispatcher.send_error(f"Tenant '{tenant_slug}' not found.")
        return True

    if not site_name:
        sites = _site_choices(tenant_slug)
        if not sites:
            dispatcher.send_error(f"No devices found for {tenant.name} at any site.")
            return True
        dispatcher.prompt_from_menu(
            f"nautobot onboard sync {tenant_slug}",
            "Select site:",
            sites + [("ALL", "All sites")],
        )
        return False

    if not category:
        dispatcher.prompt_from_menu(
            f"nautobot onboard sync {tenant_slug} {site_name}",
            "Sync which devices?",
            CATEGORY_CHOICES,
        )
        return False

    if site_name == "ALL":
        job_model = JobModel.objects.get(module_name=SYNC_JOB_MODULE, job_class_name="SyncAllSites")
        job_kwargs = {"tenant": tenant, "category": category, "dry_run": False}
        label = f"all sites for {tenant.name}"
    else:
        site = Location.objects.filter(name=site_name).first()
        if not site:
            dispatcher.send_error(f"Site '{site_name}' not found.")
            return True
        job_model = JobModel.objects.get(module_name=SYNC_JOB_MODULE, job_class_name="SyncNetworkData")
        job_kwargs = {"tenant": tenant, "site": site, "category": category, "dry_run": False}
        label = f"{site_name} ({tenant.name})"

    JobResult.enqueue_job(job_model, dispatcher.user, **job_kwargs)

    dispatcher.send_markdown(
        f"🚀 Dispatched sync for *{label}*, category: {category}.\n"
        f"Devices sync in parallel — check the Job's log in Nautobot for the "
        f"summary once every device finishes."
    )
    return True


# ── /nautobot fill-creds ──────────────────────────────────────────────────────

@subcommand_of("nautobot")
def fill_creds(dispatcher, tenant_slug=None, var_name=None, value=None):
    """Fill in a tenant's missing credential values one at a time, privately.

    Usage: /nautobot fill-creds <tenant-slug>
    """
    if not tenant_slug:
        choices = _tenant_choices()
        if not choices:
            dispatcher.send_markdown("No tenants found in Nautobot yet.")
            return True
        dispatcher.prompt_from_menu("nautobot fill-creds", "Select tenant:", choices)
        return False

    profile, env_path = _profile_and_env_path(tenant_slug)
    if not profile:
        dispatcher.send_error(f"No profile found for tenant '{tenant_slug}'.")
        return True

    values = cc.read_env_file(env_path) or {}

    # If we're being re-invoked with an answer for a specific var, save it first.
    if var_name and value is not None:
        _write_env_value(env_path, var_name, value)
        values[var_name] = value

    expected = cc.derive_expected_vars(profile)
    remaining = [v for v in expected if not values.get(v, "").strip()]

    if not remaining:
        dispatcher.send_markdown(
            f"✅ All credentials for *{profile['name']}* are filled in.", ephemeral=True
        )
        return True

    next_var = remaining[0]
    dispatcher.prompt_for_text(
        f"nautobot fill-creds {tenant_slug} {next_var}",
        f"Value for `{next_var}` ({len(remaining)} left for {profile['name']})",
        next_var,
    )
    return False
