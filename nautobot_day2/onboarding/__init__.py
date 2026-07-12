"""
Site/tenant onboarding pipeline and vendor sync engine.

These modules are run both as standalone scripts (each adds its own
directory to sys.path and imports siblings like `vendor_matrix` directly)
and loaded dynamically by nautobot_day2.jobs.sync_network_data_job. Keep
that sibling-import style intact when editing — it's what lets a single
copy of this code run both ways without a separate packaging step.
"""
