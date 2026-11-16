"""Where single sign-on and a second factor will plug in.

Decision 4 in docs/decisions-needed.md: email and password in the first
version, with SSO and MFA stubbed behind an interface. These are the
interfaces. The sign-in route goes through both, so adding a real provider
changes this file and the settings, not the route.
"""

from typing import Protocol
from uuid import UUID


class NotConfigured(RuntimeError):
    pass


class IdentityProvider(Protocol):
    name: str

    def start(self, email: str) -> str:
        """Where to send someone to sign in with this provider."""


class SecondFactor(Protocol):
    def required(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Does this person have to give a second factor?"""

    def verify(self, tenant_id: UUID, user_id: UUID, code: str) -> bool:
        """Is the code they gave the right one?"""


class NoSingleSignOn:
    """The stub: no tenant has single sign-on yet."""

    name = "none"

    def start(self, email: str) -> str:
        raise NotConfigured("Single sign-on is not set up. Sign in with your email and password.")


class NoSecondFactor:
    """The stub: nobody is asked for a second factor yet."""

    def required(self, tenant_id: UUID, user_id: UUID) -> bool:
        return False

    def verify(self, tenant_id: UUID, user_id: UUID, code: str) -> bool:
        return False


def single_sign_on() -> IdentityProvider:
    return NoSingleSignOn()


def second_factor() -> SecondFactor:
    return NoSecondFactor()
