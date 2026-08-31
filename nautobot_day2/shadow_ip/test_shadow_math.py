"""
Unit tests for shadow_math.py -- pure ipaddress arithmetic, no Django/Nautobot
needed. Run directly (`python3 test_shadow_math.py`) rather than via `pytest`
package collection: pytest can't import anything under nautobot_day2/ without
Nautobot installed, since nautobot_day2/__init__.py imports nautobot.apps at
module load time (a pre-existing repo constraint, same one
onboarding_mcp/session/test_state_machine.py works around the same way).
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from shadow_math import compute_real_ip, compute_shadow_ip, range_from_prefix, range_size  # noqa: E402


class _FakePrefix:
    def __init__(self, prefix):
        self.prefix = prefix


def test_compute_shadow_ip_preserves_offset():
    real_prefix = _FakePrefix("192.168.2.0/24")
    shadow_prefix = _FakePrefix("100.64.10.0/24")
    assert compute_shadow_ip("192.168.2.42", real_prefix, shadow_prefix) == "100.64.10.42"
    assert compute_shadow_ip("192.168.2.0", real_prefix, shadow_prefix) == "100.64.10.0"
    assert compute_shadow_ip("192.168.2.255", real_prefix, shadow_prefix) == "100.64.10.255"


def test_compute_real_ip_is_inverse():
    real_prefix = _FakePrefix("192.168.2.0/24")
    shadow_prefix = _FakePrefix("100.64.10.0/24")
    assert compute_real_ip("100.64.10.42", real_prefix, shadow_prefix) == "192.168.2.42"


def test_roundtrip_arbitrary_offsets():
    real_prefix = _FakePrefix("10.5.0.0/22")
    shadow_prefix = _FakePrefix("100.64.20.0/22")
    for host in ("10.5.0.1", "10.5.1.200", "10.5.3.254"):
        shadow = compute_shadow_ip(host, real_prefix, shadow_prefix)
        assert compute_real_ip(shadow, real_prefix, shadow_prefix) == host


def test_range_from_prefix_matches_fortios_shape():
    assert range_from_prefix("100.64.10.0/24") == "100.64.10.1-100.64.10.254"
    assert range_from_prefix("192.168.2.0/24") == "192.168.2.1-192.168.2.254"


def test_range_size_counts_inclusive():
    assert range_size("100.64.10.1-100.64.10.254") == 254
    assert range_size("100.64.10.5-100.64.10.5") == 1


def test_range_size_flags_unequal_vip_ranges():
    # Guards ValidateVIPCoverage's range-size-equality check (architecture doc §4/§6.5):
    # a mismatched extip/mappedip size would silently break the offset formula above.
    extip_size = range_size("100.64.10.1-100.64.10.254")
    mappedip_size = range_size("192.168.2.1-192.168.2.100")
    assert extip_size != mappedip_size


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and isinstance(v, types.FunctionType)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  OK    {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__} — {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
