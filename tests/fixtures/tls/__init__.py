"""TLS test certificates and utilities.

This package generates test TLS certificates on-the-fly to avoid storing
private keys in version control (which triggers security scanners).

WARNING: These certificates are for testing only!
Do not use in production environments.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Final

# Import the generator
from tests.fixtures.tls.cert_generator import generate_test_certificates

# Global cache for generated certificates
_CERT_CACHE: dict[str, Path] | None = None
_TEMP_DIR: Path | None = None


def _get_or_generate_certs() -> dict[str, Path]:
    """Get cached certificates or generate new ones."""
    global _CERT_CACHE, _TEMP_DIR

    if _CERT_CACHE is None:
        _TEMP_DIR = Path(tempfile.mkdtemp(prefix="fraudstream_test_tls_"))
        _CERT_CACHE = generate_test_certificates(_TEMP_DIR)

        # Register cleanup on exit
        atexit.register(_cleanup_certs)

    return _CERT_CACHE


def _cleanup_certs() -> None:
    """Clean up temporary certificate files."""
    global _TEMP_DIR
    if _TEMP_DIR and _TEMP_DIR.exists():
        shutil.rmtree(_TEMP_DIR, ignore_errors=True)


def get_tls_cert_path(name: str) -> Path:
    """Get path to a TLS certificate file.

    Certificates are generated on first call and cached for the process lifetime.

    Args:
        name: Certificate file name (e.g., 'ca-cert.pem', 'ca_cert')

    Returns:
        Path to the certificate file

    Raises:
        ValueError: If the certificate name is not recognized
    """
    # Normalize name (support both 'ca-cert.pem' and 'ca_cert')
    name_map = {
        "ca-cert.pem": "ca_cert",
        "ca-key.pem": "ca_key",
        "server-cert.pem": "server_cert",
        "server-key.pem": "server_key",
        "client-cert.pem": "client_cert",
        "client-key.pem": "client_key",
        # Also allow the underscore versions
        "ca_cert": "ca_cert",
        "ca_key": "ca_key",
        "server_cert": "server_cert",
        "server_key": "server_key",
        "client_cert": "client_cert",
        "client_key": "client_key",
    }

    normalized = name_map.get(name)
    if normalized is None:
        raise ValueError(f"Unknown certificate name: {name}")

    certs = _get_or_generate_certs()
    return certs[normalized]


# Backwards compatibility: TLS_DIR points to temp dir
TLS_DIR: Final[Path] = Path(tempfile.gettempdir()) / "fraudstream_test_tls"


__all__ = ["TLS_DIR", "get_tls_cert_path", "generate_test_certificates"]
