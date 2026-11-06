"""The credentials each of Control's callers presents, for tests.

`WEB_TOKEN` is set in the environment by `conftest.py` so every OIDC
`Settings` a test builds expects it, and sent by every client a test builds,
so the identity boundary stays under test rather than switched off. The other
two are Chat's and the workers', which reach different routes.
"""

from primer_service.credentials import WEB_TOKEN_HEADER

WEB_TOKEN = "test-web-token"  # noqa: S105 - a test value
FORWARDED = {WEB_TOKEN_HEADER: WEB_TOKEN}
SERVICE_TOKEN = "test-service-token"  # noqa: S105 - a test value
WORKER_TOKEN = "test-worker-token"  # noqa: S105 - a test value
