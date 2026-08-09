"""
Regression test for the 2026-08-09 audit finding: the demo-mode topbar label
was a hardcoded literal ("domus_01 - demo") instead of the configured
INSTANCE_LABEL, so every DEMO_MODE instance displayed the same name
regardless of how it was actually configured (see templates/base.html).

Sets INSTANCE_LABEL directly on app.config after create_app() rather than via
env var + config.py reload (the pattern conftest.py's other fixtures use) -
config.py is user-owned and gitignored, and not every checkout's copy is
guaranteed to define INSTANCE_LABEL (app.py falls back to a default via
getattr when it doesn't), so routing through it would make this test's
outcome depend on something outside the code path actually under test.
"""


def test_demo_topbar_shows_default_instance_label_not_hardcoded_literal(demo_client):
    html = demo_client.get("/").get_data(as_text=True)
    assert "res domus - demo" in html
    assert "domus_01" not in html


def test_demo_topbar_respects_custom_instance_label(demo_client):
    demo_client.application.config["INSTANCE_LABEL"] = "my second instance"
    html = demo_client.get("/").get_data(as_text=True)
    assert "my second instance - demo" in html
    assert "domus_01" not in html
