"""TLS test certificates and utilities.

This package contains test TLS certificates for security testing:
- ca-cert.pem: Test Certificate Authority certificate
- ca-key.pem: Test CA private key (keep secure)
- server-cert.pem: Test server certificate signed by CA
- server-key.pem: Test server private key
- client-cert.pem: Test client certificate for mTLS
- client-key.pem: Test client private key

WARNING: These certificates are for testing only!
Do not use in production environments.
"""

from pathlib import Path

TLS_DIR = Path(__file__).parent

def get_tls_cert_path(name: str) -> Path:
    """Get path to a TLS certificate file.
    
    Args:
        name: Certificate file name (e.g., 'ca-cert.pem')
        
    Returns:
        Path to the certificate file
    """
    return TLS_DIR / name

__all__ = ["TLS_DIR", "get_tls_cert_path"]
