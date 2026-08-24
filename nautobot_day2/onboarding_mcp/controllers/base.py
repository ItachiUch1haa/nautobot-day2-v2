"""
APControllerClient ABC -- the wireless-side analog of vendor_commands.yaml
+ Nornir-dispatch already used for SSH devices (architecture doc §5). One
implementation per controller type; set_ap_controller/scan_ap_controller
(tools_schema.py) only ever talk to this interface, never a specific
adapter class directly.
"""
from abc import ABC, abstractmethod


class ControllerAuthError(Exception):
    """Raised by test_connection()/discover_aps() when the controller rejects the given credentials."""


class APControllerClient(ABC):
    """Common interface every AP controller adapter (local SSH master, Meraki, Mist, Aruba Central, other) implements."""

    @abstractmethod
    def test_connection(self):
        """Return True if the controller is reachable and the given credentials authenticate; raise ControllerAuthError otherwise."""
        ...

    @abstractmethod
    def discover_aps(self, filters):
        """Return a list of candidate APs: [{name, model, mac, current_ip, site_label, raw}]."""
        ...

    @abstractmethod
    def required_credential_fields(self):
        """Return the list of credential field names set_ap_controller must collect for this controller type."""
        ...
