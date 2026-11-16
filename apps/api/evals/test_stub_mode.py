"""The one property every evaluation relies on: with no key set, the suite
runs against the deterministic stub."""

from revlane.config import Settings


def test_eval_suite_runs_in_stub_mode_without_a_key() -> None:
    assert Settings(_env_file=None, llm_api_key="").llm_mode == "stub"


def test_the_default_client_is_the_stub() -> None:
    from revlane.llm.client import get_client

    assert get_client(Settings(_env_file=None, llm_api_key="")).name == "stub"
