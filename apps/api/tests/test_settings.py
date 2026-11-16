"""Settings defaults that the standing rules depend on."""

from revlane.config import Settings


def test_stub_mode_is_the_default_without_a_key() -> None:
    settings = Settings(_env_file=None, llm_api_key="", llm_stub=False)

    assert settings.llm_mode == "stub"


def test_live_mode_needs_a_key_and_stub_switched_off() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_stub=False)

    assert settings.llm_mode == "live"


def test_provider_live_calls_are_off_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.provider_live_calls is False
