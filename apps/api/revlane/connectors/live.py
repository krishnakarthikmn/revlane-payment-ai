"""Live calls to a provider's sandbox. Off unless PROVIDER_LIVE_CALLS is true.

Rule 10: only sandbox or synthetic data. The transport refuses any host that
is not on the sandbox list, and each connector checks that its credentials
are test credentials. Rule 5: these are read calls only.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from revlane.vault.base import CredentialRef, Vault

# Razorpay has one host; its test mode is chosen by the key (rzp_test_...).
SANDBOX_HOSTS = frozenset({"api.razorpay.com", "test.payu.in"})
TIMEOUT_SECONDS = 20


class LiveCallsDisabled(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Live provider calls are off. Set PROVIDER_LIVE_CALLS=true to allow them.")


class ProviderError(RuntimeError):
    """The provider's sandbox refused the call or could not be reached."""


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> object: ...


class SandboxTransport:
    """HTTPS to a sandbox host, and nowhere else."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> object:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in SANDBOX_HOSTS:
            raise PermissionError(f"{parts.hostname} is not a provider sandbox host.")
        if params:
            url = f"{url}{'&' if parts.query else '?'}{urllib.parse.urlencode(params)}"
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                reply: object = json.loads(response.read(), parse_float=Decimal)
                return reply
        except urllib.error.HTTPError as error:
            # The body may echo what was sent; only the status leaves this function.
            raise ProviderError(f"{parts.hostname} answered {error.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError(f"{parts.hostname} could not be read") from error


@dataclass
class LiveAccess:
    """What a connector needs to call a sandbox: the flag, a transport, and
    credentials that stay in the vault."""

    enabled: bool
    transport: Transport
    vault: Vault
    ref: CredentialRef | None = None
    # Credentials a person has just entered, before `authorize` stores them.
    entered: dict[str, str] | None = None

    def require_enabled(self) -> None:
        if not self.enabled:
            raise LiveCallsDisabled

    def credentials(self) -> dict[str, str]:
        if self.entered is not None:
            return self.entered
        if self.ref is None:
            raise PermissionError("No credentials are stored for this provider.")
        stored = json.loads(self.vault.get(self.ref))
        return {str(key): str(value) for key, value in stored.items()}

    def store(self, tenant_id: UUID, name: str) -> CredentialRef:
        if self.entered is None:
            raise PermissionError("There are no new credentials to store.")
        self.ref = self.vault.put(tenant_id, name, json.dumps(self.entered))
        self.entered = None
        return self.ref


def basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
