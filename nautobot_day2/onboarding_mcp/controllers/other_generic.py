"""
"other" controller type (architecture doc §5) -- accepts a free-form JSON
blob, validated only for required-key presence, not parsed. A placeholder
for future adapters, not a working one: discover_aps() always raises
NotImplementedError by default. Governance note (architecture doc §11,
open decision, not resolved here): decide who is allowed to add a real
adapter here and what review it gets, given it will hold live credentials.
"""
from controllers.base import APControllerClient


class OtherGenericClient(APControllerClient):
    """Placeholder adapter for a controller type with no dedicated implementation yet."""

    def __init__(self, connection_type, config):
        if connection_type not in ("ssh", "api"):
            raise ValueError(f"connection_type must be 'ssh' or 'api', got {connection_type!r}")
        if not isinstance(config, dict) or not config:
            raise ValueError("'other' controller type requires a non-empty config JSON blob")
        self.connection_type = connection_type
        self.config = config

    def required_credential_fields(self):
        """'other' accepts whatever keys the caller's config blob already contains — no fixed field list."""
        return list(self.config.keys())

    def test_connection(self):
        """No generic connectivity check exists for an unknown adapter — always fails until a real implementation is supplied."""
        raise NotImplementedError(
            "controller_type='other' has no working test_connection() — "
            "supply a real adapter for this vendor before using it."
        )

    def discover_aps(self, filters):
        """Placeholder — raises until a real adapter is implemented for this connection_type/config shape."""
        raise NotImplementedError(
            "controller_type='other' has no working discover_aps() — "
            "supply a real adapter for this vendor before using it."
        )
