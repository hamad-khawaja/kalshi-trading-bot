"""RSA-PSS authentication for Kalshi API."""

from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiAuth:
    """Generates RSA-PSS signatures for Kalshi API requests.

    Kalshi requires three headers on authenticated requests:
    - KALSHI-ACCESS-KEY: the API key ID
    - KALSHI-ACCESS-TIMESTAMP: current time in milliseconds
    - KALSHI-ACCESS-SIGNATURE: RSA-PSS signature of (timestamp + method + path)
    """

    def __init__(self, api_key_id: str, private_key_path: str):
        self.api_key_id = api_key_id
        self._private_key = self._load_private_key(private_key_path)

    @staticmethod
    def _load_private_key(path: str) -> rsa.RSAPrivateKey:
        """Load RSA private key from PEM file."""
        with open(path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(f"Expected RSA private key, got {type(key).__name__}")
        return key

    def sign(self, timestamp_ms: int, method: str, path: str) -> str:
        """Generate RSA-PSS signature for a Kalshi API request.

        The message to sign is: str(timestamp_ms) + method_uppercase + path_without_query
        Uses SHA256 with PSS padding (salt_length = DIGEST_LENGTH).
        Returns base64-encoded signature string.
        """
        path_without_query = path.split("?")[0]
        message = f"{timestamp_ms}{method.upper()}{path_without_query}"

        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def get_headers(self, method: str, path: str) -> dict[str, str]:
        """Generate the three required authentication headers."""
        timestamp_ms = int(time.time() * 1000)
        signature = self.sign(timestamp_ms, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature,
        }
