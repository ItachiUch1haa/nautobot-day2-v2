"""
Unit tests for state_machine.py — run with `pytest` locally, no Redis or
Nautobot needed (FakeStore below is a plain in-memory dict). Covers the
brief's Phase 2 requirements: illegal-transition rejection with a clear
error (not a stack trace), and the hard sequencing rule from architecture
doc §8 (DEVICE_INTAKE unreachable until set_site has completed).
"""
import pytest

from state_machine import (
    CONTROLLER_SCAN,
    DEVICE_INTAKE,
    DONE,
    IllegalTransitionError,
    INIT,
    OnboardingSession,
    REVIEW,
    SITE_RESOLUTION,
    TENANT_RESOLUTION,
)


class FakeStore:
    """In-memory stand-in for the redis get/set interface OnboardingSession expects."""

    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = value


@pytest.fixture
def store():
    return FakeStore()


def test_new_session_starts_in_init(store):
    session = OnboardingSession.create(store)
    assert session.get_status()["state"] == INIT


def test_start_onboarding_moves_to_tenant_resolution(store):
    session = OnboardingSession.create(store)
    data = session.transition("start_onboarding")
    assert data["state"] == TENANT_RESOLUTION


def test_full_happy_path_static_device(store):
    session = OnboardingSession.create(store)
    session.transition("start_onboarding")
    session.transition("set_tenant", lambda d: {**d, "tenant": "acme"})
    assert session.get_status()["state"] == SITE_RESOLUTION

    session.transition("set_site", lambda d: {**d, "site": "hq"})
    assert session.get_status()["state"] == DEVICE_INTAKE

    session.transition(
        "add_static_device",
        lambda d: {**d, "pending_devices": d["pending_devices"] + [{"hostname": "fw1"}]},
    )
    assert session.get_status()["state"] == DEVICE_INTAKE
    assert len(session.get_status()["pending_devices"]) == 1

    session.transition("review_pending_batch")
    assert session.get_status()["state"] == REVIEW

    session.transition("deploy_site")
    assert session.get_status()["state"] == DONE


def test_hard_sequencing_rule_device_intake_unreachable_without_set_site(store):
    """
    architecture doc §8: DEVICE_INTAKE must be unreachable until set_site
    has completed. A session that only got as far as set_tenant (state
    SITE_RESOLUTION) must not be able to call add_static_device.
    """
    session = OnboardingSession.create(store)
    session.transition("start_onboarding")
    session.transition("set_tenant", lambda d: {**d, "tenant": "acme"})
    assert session.get_status()["state"] == SITE_RESOLUTION

    with pytest.raises(IllegalTransitionError):
        session.transition("add_static_device")

    # confirm nothing was mutated by the rejected call
    assert session.get_status()["state"] == SITE_RESOLUTION
    assert session.get_status()["pending_devices"] == []


def test_illegal_transition_has_clear_message_not_a_crash(store):
    session = OnboardingSession.create(store)
    with pytest.raises(IllegalTransitionError) as exc_info:
        session.transition("deploy_site")  # INIT -> deploy_site is illegal
    assert "deploy_site" in str(exc_info.value)
    assert "INIT" in str(exc_info.value)


def test_set_ap_controller_requires_device_intake(store):
    session = OnboardingSession.create(store)
    with pytest.raises(IllegalTransitionError):
        session.transition("set_ap_controller")


def test_scan_ap_controller_is_rerunnable_and_preserves_selection(store):
    """
    Confirmed decision: re-running scan_ap_controller preserves APs
    already queued via select_discovered_aps in this session.
    """
    session = OnboardingSession.create(store)
    session.transition("start_onboarding")
    session.transition("set_tenant")
    session.transition("set_site")
    session.transition("set_ap_controller")
    assert session.get_status()["state"] == CONTROLLER_SCAN

    session.transition(
        "select_discovered_aps",
        lambda d: {**d, "pending_devices": d["pending_devices"] + [{"name": "ap1"}]},
    )
    assert session.get_status()["state"] == DEVICE_INTAKE
    assert len(session.get_status()["pending_devices"]) == 1

    # engineer goes back to set_ap_controller / re-scans — pending batch
    # from the earlier selection must still be there
    session.transition("set_ap_controller")
    session.transition("scan_ap_controller")  # re-runnable, stays in CONTROLLER_SCAN
    assert session.get_status()["state"] == CONTROLLER_SCAN
    assert len(session.get_status()["pending_devices"]) == 1


def test_remove_pending_device_stays_in_review(store):
    session = OnboardingSession.create(store)
    session.transition("start_onboarding")
    session.transition("set_tenant")
    session.transition("set_site")
    session.transition(
        "add_static_device",
        lambda d: {**d, "pending_devices": [{"hostname": "fw1"}, {"hostname": "fw2"}]},
    )
    session.transition("review_pending_batch")

    session.transition(
        "remove_pending_device",
        lambda d: {**d, "pending_devices": [dev for dev in d["pending_devices"] if dev["hostname"] != "fw1"]},
    )
    assert session.get_status()["state"] == REVIEW
    assert len(session.get_status()["pending_devices"]) == 1


def test_session_not_found_for_unknown_id(store):
    from state_machine import SessionNotFoundError

    session = OnboardingSession(store, session_id="does-not-exist")
    with pytest.raises(SessionNotFoundError):
        session.get_status()
