"""
vendor_matrix.py
Single source of truth for all supported vendor/device/access combinations.
All other scripts import from here — nothing is hardcoded elsewhere.
Set enabled=False to defer a combination without removing it.
"""

VENDOR_MATRIX = {
    "aruba": {
        "label": "Aruba / HPE",
        "manufacturer_slug": "aruba",
        "device_types": {
            "switch": {
                "enabled": True,
                "roles": ["access-switch", "core-switch"],
                "platforms": {
                    "arubaos": {
                        "label": "ArubaOS (AOS-S)",
                        "napalm_driver": "",
                        "default": True
                    },
                    "arubaoscx": {
                        "label": "ArubaOS-CX (AOS-CX)",
                        "napalm_driver": "",
                        "default": False
                    }
                },
                "access_methods": {
                    "aruba-central": {
                        "enabled": True,
                        "label": "Aruba Central API",
                        "secrets_group_prefix": "aruba-central-api",
                        "env_vars": ["ARUBA_CLIENT_ID", "ARUBA_CLIENT_SECRET", "ARUBA_REFRESH_TOKEN", "ARUBA_CENTRAL_BASE_URL", "ARUBA_CENTRAL_TYPE"],
                        "external_integration": "Aruba Central",
                        "sync_handler": "aruba_central_switch_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "aruba-ssh",
                        "env_vars": ["ARUBA_SSH_USER", "ARUBA_SSH_PASS"],
                        "sync_handler": "aruba_switch_ssh"
                    }
                },
                "default_access": "ssh"
            },
            "ap": {
                "enabled": True,
                "roles": ["ap"],
                "platforms": {
                    "arubaos": {
                        "label": "ArubaOS AP",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "aruba-central": {
                        "enabled": True,
                        "label": "Aruba Central API",
                        "secrets_group_prefix": "aruba-central-api",
                        "env_vars": ["ARUBA_CLIENT_ID", "ARUBA_CLIENT_SECRET", "ARUBA_REFRESH_TOKEN", "ARUBA_CENTRAL_BASE_URL", "ARUBA_CENTRAL_TYPE"],
                        "external_integration": "Aruba Central",
                        "sync_handler": "aruba_central_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH only",
                        "secrets_group_prefix": "aruba-ssh",
                        "env_vars": ["ARUBA_SSH_USER", "ARUBA_SSH_PASS"],
                        "sync_handler": "aruba_switch_ssh"
                    }
                },
                "default_access": "aruba-central"
            },
            "nac": {
                "enabled": True,
                "roles": ["nac"],
                "platforms": {
                    "clearpass": {
                        "label": "Aruba ClearPass",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "clearpass-api": {
                        "enabled": True,
                        "label": "ClearPass API",
                        "secrets_group_prefix": "aruba-clearpass-api",
                        "env_vars": ["ARUBA_CLEARPASS_API_TOKEN", "ARUBA_CLEARPASS_BASE_URL"],
                        "external_integration": "ClearPass",
                        "sync_handler": "aruba_clearpass_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH only",
                        "secrets_group_prefix": "aruba-ssh",
                        "env_vars": ["ARUBA_SSH_USER", "ARUBA_SSH_PASS"],
                        "sync_handler": "aruba_switch_ssh"
                    }
                },
                "default_access": "clearpass-api"
            }
        }
    },

    "juniper": {
        "label": "Juniper Networks",
        "manufacturer_slug": "juniper",
        "device_types": {
            "switch": {
                "enabled": True,
                "roles": ["access-switch", "core-switch"],
                "platforms": {
                    "junos": {
                        "label": "Junos",
                        "napalm_driver": "junos",
                        "default": True
                    }
                },
                "access_methods": {
                    "mist": {
                        "enabled": True,
                        "label": "Mist Cloud API",
                        "secrets_group_prefix": "juniper-mist-api",
                        "env_vars": ["MIST_API_TOKEN", "MIST_ORG_ID"],
                        "external_integration": "Mist Cloud",
                        "sync_handler": "juniper_mist_switch_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "juniper-ssh",
                        "env_vars": ["JUNIPER_SSH_USER", "JUNIPER_SSH_PASS"],
                        "sync_handler": "juniper_switch_ssh"
                    }
                },
                "default_access": "ssh"
            },
            "ap": {
                "enabled": True,
                "roles": ["ap"],
                "platforms": {
                    "junos": {
                        "label": "Junos AP (Mist)",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "mist": {
                        "enabled": True,
                        "label": "Mist Cloud API",
                        "secrets_group_prefix": "juniper-mist-api",
                        "env_vars": ["MIST_API_TOKEN", "MIST_ORG_ID"],
                        "external_integration": "Mist Cloud",
                        "sync_handler": "juniper_mist_api"
                    }
                },
                "default_access": "mist"
            },
            "firewall": {
                "enabled": True,
                "roles": ["branch-fw"],
                "platforms": {
                    "junos-srx": {
                        "label": "Junos SRX",
                        "napalm_driver": "junos",
                        "default": True
                    }
                },
                "access_methods": {
                    "mist": {
                        "enabled": True,
                        "label": "Mist Cloud API",
                        "secrets_group_prefix": "juniper-mist-api",
                        "env_vars": ["MIST_API_TOKEN", "MIST_ORG_ID"],
                        "external_integration": "Mist Cloud",
                        "sync_handler": "juniper_mist_firewall_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "juniper-ssh",
                        "env_vars": ["JUNIPER_SSH_USER", "JUNIPER_SSH_PASS"],
                        "sync_handler": "juniper_firewall_ssh"
                    }
                },
                "default_access": "ssh"
            }
        }
    },

    "cisco": {
        "label": "Cisco Systems",
        "manufacturer_slug": "cisco",
        "device_types": {
            "switch": {
                "enabled": True,
                "roles": ["access-switch", "core-switch"],
                "platforms": {
                    "iosxe": {
                        "label": "IOS-XE",
                        "napalm_driver": "ios",
                        "default": True
                    },
                    "ios": {
                        "label": "IOS",
                        "napalm_driver": "ios",
                        "default": False
                    },
                    "nxos": {
                        "label": "NX-OS",
                        "napalm_driver": "nxos",
                        "default": False
                    }
                },
                "access_methods": {
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "cisco-ssh",
                        "env_vars": ["CISCO_SSH_USER", "CISCO_SSH_PASS"],
                        "enable_mode": False,
                        "enable_env_var": "CISCO_ENABLE_PASS",
                        "sync_handler": "cisco_switch_ssh"
                    }
                },
                "default_access": "ssh"
            },
            "ap": {
                "enabled": False,
                "roles": ["ap"],
                "platforms": {
                    "aireos": {
                        "label": "AireOS (WLC)",
                        "napalm_driver": "",
                        "default": True
                    },
                    "catalyst-wireless": {
                        "label": "Catalyst Wireless (DNAC)",
                        "napalm_driver": "",
                        "default": False
                    }
                },
                "access_methods": {
                    "dnac": {
                        "enabled": False,
                        "label": "DNA Center API",
                        "secrets_group_prefix": "cisco-dnac-api",
                        "env_vars": ["CISCO_DNAC_API_TOKEN", "CISCO_DNAC_BASE_URL"],
                        "external_integration": "Cisco DNAC",
                        "sync_handler": "cisco_dnac_api"
                    },
                    "wlc-ssh": {
                        "enabled": False,
                        "label": "WLC SSH",
                        "secrets_group_prefix": "cisco-ssh",
                        "env_vars": ["CISCO_WLC_SSH_USER", "CISCO_WLC_SSH_PASS"],
                        "sync_handler": "cisco_wlc_ssh"
                    }
                },
                "default_access": "dnac"
            },
            "firewall": {
                "enabled": True,
                "roles": ["branch-fw"],
                "platforms": {
                    "asa": {
                        "label": "ASA",
                        "napalm_driver": "",
                        "default": True
                    },
                    "ftd": {
                        "label": "FTD",
                        "napalm_driver": "",
                        "default": False
                    }
                },
                "access_methods": {
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "cisco-ssh",
                        "env_vars": ["CISCO_SSH_USER", "CISCO_SSH_PASS"],
                        "enable_mode": False,
                        "enable_env_var": "CISCO_ENABLE_PASS",
                        "sync_handler": "cisco_firewall_ssh"
                    },
                    "fmc-api": {
                        "enabled": True,
                        "label": "FMC API",
                        "secrets_group_prefix": "cisco-fmc-api",
                        "env_vars": ["CISCO_FMC_API_TOKEN", "CISCO_FMC_BASE_URL"],
                        "external_integration": "Cisco FMC",
                        "sync_handler": "cisco_fmc_api"
                    }
                },
                "default_access": "ssh"
            }
        }
    },

    "fortinet": {
        "label": "Fortinet",
        "manufacturer_slug": "fortinet",
        "device_types": {
            "switch": {
                "enabled": True,
                "roles": ["access-switch", "core-switch"],
                "platforms": {
                    "fortios-switch": {
                        "label": "FortiOS Switch",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "fortinet-ssh",
                        "env_vars": ["FORTINET_SSH_USER", "FORTINET_SSH_PASS"],
                        "sync_handler": "fortinet_switch_ssh"
                    },
                    "fortimgr-api": {
                        "enabled": True,
                        "label": "FortiManager API",
                        "secrets_group_prefix": "fortinet-manager-api",
                        "env_vars": [
                            "FORTINET_MGR_API_TOKEN",
                            "FORTINET_MGR_BASE_URL",
                            "FORTINET_MGR_ADOM"
                        ],
                        "external_integration": "FortiManager",
                        "sync_handler": "fortinet_manager_api"
                    }
                },
                "default_access": "ssh"
            },
            "ap": {
                "enabled": True,
                "roles": ["ap"],
                "platforms": {
                    "fortios-ap": {
                        "label": "FortiOS AP",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "fortimgr-api": {
                        "enabled": True,
                        "label": "FortiManager API",
                        "secrets_group_prefix": "fortinet-manager-api",
                        "env_vars": [
                            "FORTINET_MGR_API_TOKEN",
                            "FORTINET_MGR_BASE_URL",
                            "FORTINET_MGR_ADOM"
                        ],
                        "external_integration": "FortiManager",
                        "sync_handler": "fortinet_manager_api"
                    },
                    "ssh": {
                        "enabled": True,
                        "label": "SSH only",
                        "secrets_group_prefix": "fortinet-ssh",
                        "env_vars": ["FORTINET_SSH_USER", "FORTINET_SSH_PASS"],
                        "sync_handler": "fortinet_switch_ssh"
                    }
                },
                "default_access": "fortimgr-api"
            },
            "firewall": {
                "enabled": True,
                "roles": ["branch-fw"],
                "platforms": {
                    "fortios": {
                        "label": "FortiOS",
                        "napalm_driver": "",
                        "default": True
                    }
                },
                "access_methods": {
                    "ssh": {
                        "enabled": True,
                        "label": "SSH",
                        "secrets_group_prefix": "fortinet-ssh",
                        "env_vars": ["FORTINET_SSH_USER", "FORTINET_SSH_PASS"],
                        "sync_handler": "fortinet_firewall_ssh"
                    },
                    "fortimgr-api": {
                        "enabled": True,
                        "label": "FortiManager API",
                        "secrets_group_prefix": "fortinet-manager-api",
                        "env_vars": [
                            "FORTINET_MGR_API_TOKEN",
                            "FORTINET_MGR_BASE_URL",
                            "FORTINET_MGR_ADOM"
                        ],
                        "external_integration": "FortiManager",
                        "sync_handler": "fortinet_manager_api"
                    }
                },
                "default_access": "ssh"
            }
        }
    }
}


# ── Helper functions used by all downstream scripts ──────────────────────────

def get_enabled_vendors():
    """Returns list of vendor slugs that have at least one enabled device type."""
    return [v for v, data in VENDOR_MATRIX.items()
            if any(dt["enabled"] for dt in data["device_types"].values())]


def get_device_types_for_vendor(vendor_slug):
    """Returns enabled device types for a vendor."""
    vendor = VENDOR_MATRIX.get(vendor_slug)
    if not vendor:
        return {}
    return {k: v for k, v in vendor["device_types"].items() if v["enabled"]}


def get_access_methods(vendor_slug, device_type):
    """Returns enabled access methods for a vendor+device_type combo."""
    try:
        methods = VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"]
        return {k: v for k, v in methods.items() if v["enabled"]}
    except KeyError:
        return {}


def get_platforms_for_combo(vendor_slug, device_type):
    """Returns all platforms for a vendor+device_type combo."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["platforms"]
    except KeyError:
        return {}


def get_default_platform(vendor_slug, device_type):
    """Returns the default platform slug for a vendor+device_type combo."""
    platforms = get_platforms_for_combo(vendor_slug, device_type)
    for slug, data in platforms.items():
        if data.get("default"):
            return slug
    return list(platforms.keys())[0] if platforms else None


def get_secrets_group_prefix(vendor_slug, device_type, access_method):
    """Returns secrets group prefix for a given combo."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"][access_method]["secrets_group_prefix"]
    except KeyError:
        return None


def get_external_integration_name(vendor_slug, device_type, access_method):
    """Returns external integration name if this access method needs one."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"][access_method].get("external_integration")
    except KeyError:
        return None


def get_env_vars(vendor_slug, device_type, access_method):
    """Returns list of env var base names needed for this combo."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"][access_method]["env_vars"]
    except KeyError:
        return []


def get_all_platforms():
    """Returns flat list of all platform slugs across all enabled combos."""
    platforms = {}
    for vendor_slug, vendor_data in VENDOR_MATRIX.items():
        for dt_key, dt_data in vendor_data["device_types"].items():
            if not dt_data["enabled"]:
                continue
            for plat_slug, plat_data in dt_data["platforms"].items():
                platforms[plat_slug] = {
                    "label": plat_data["label"],
                    "napalm_driver": plat_data.get("napalm_driver", ""),
                    "manufacturer_slug": vendor_data["manufacturer_slug"],
                    "vendor": vendor_slug
                }
    return platforms


def get_all_manufacturers():
    """Returns flat list of manufacturer slugs across all vendors."""
    return {
        data["manufacturer_slug"]: data["label"]
        for data in VENDOR_MATRIX.values()
    }


def get_roles_for_vendor(vendor_slug):
    """Returns all roles used by a vendor across device types."""
    roles = set()
    vendor = VENDOR_MATRIX.get(vendor_slug, {})
    for dt_data in vendor.get("device_types", {}).values():
        if dt_data["enabled"]:
            roles.update(dt_data["roles"])
    return list(roles)


def get_sync_handler(vendor_slug, device_type, access_method):
    """Returns the sync handler name for a combo."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"][access_method]["sync_handler"]
    except KeyError:
        return None


def needs_enable_mode(vendor_slug, device_type, access_method):
    """Returns True if this SSH connection needs enable mode."""
    try:
        return VENDOR_MATRIX[vendor_slug]["device_types"][device_type]["access_methods"][access_method].get("enable_mode", False)
    except KeyError:
        return False


if __name__ == "__main__":
    from tabulate import tabulate

    print("\n=== VENDOR MATRIX — enabled combinations ===\n")
    rows = []
    for vendor_slug, vendor_data in VENDOR_MATRIX.items():
        for dt_key, dt_data in vendor_data["device_types"].items():
            status = "enabled" if dt_data["enabled"] else "DEFERRED"
            platforms = ", ".join(dt_data["platforms"].keys())
            methods = ", ".join(
                k for k, v in dt_data["access_methods"].items() if v["enabled"]
            )
            rows.append([vendor_slug, dt_key, status, platforms, methods])

    print(tabulate(rows, headers=[
        "Vendor", "Device type", "Status", "Platforms", "Access methods"
    ], tablefmt="simple"))

    print("\n=== ALL PLATFORMS to create in Nautobot ===\n")
    plat_rows = []
    for slug, data in get_all_platforms().items():
        plat_rows.append([slug, data["label"], data["manufacturer_slug"], data["napalm_driver"] or "—"])
    print(tabulate(plat_rows, headers=["Slug", "Label", "Manufacturer", "NAPALM driver"], tablefmt="simple"))

    print("\n=== MANUFACTURERS ===\n")
    for slug, label in get_all_manufacturers().items():
        print(f"  {slug:15} {label}")

    print()
