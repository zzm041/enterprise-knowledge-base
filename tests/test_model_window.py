import pytest

from scripts.model_window import Window


def test_revoked_shared_window_has_no_legacy_execution_path():
    assert not hasattr(Window, "_unreleased_run")
    with pytest.raises(RuntimeError, match="UPSTREAM_READ_ONLY_PLAN_REQUIRED"):
        Window().run()
