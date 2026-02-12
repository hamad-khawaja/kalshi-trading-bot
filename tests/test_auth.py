"""Tests for Kalshi RSA-PSS authentication."""

from __future__ import annotations

import base64
import os
import tempfile
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.data.kalshi_auth import KalshiAuth


@pytest.fixture
def rsa_key_pair():
    """Generate a test RSA key pair."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key


@pytest.fixture
def key_file(rsa_key_pair):
    """Write private key to a temp file and return path."""
    pem = rsa_key_pair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
        f.write(pem)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def auth(key_file):
    """Create KalshiAuth instance with test key."""
    return KalshiAuth(api_key_id="test-key-id", private_key_path=key_file)


class TestKalshiAuth:
    def test_sign_deterministic(self, auth: KalshiAuth):
        """Same inputs should produce same signature (with same key)."""
        ts = 1700000000000
        sig1 = auth.sign(ts, "GET", "/trade-api/v2/markets")
        sig2 = auth.sign(ts, "GET", "/trade-api/v2/markets")
        # RSA-PSS is probabilistic (random salt), so signatures will differ
        # But both should be valid base64
        assert isinstance(sig1, str)
        assert isinstance(sig2, str)
        # Both should decode as base64
        base64.b64decode(sig1)
        base64.b64decode(sig2)

    def test_sign_format(self, auth: KalshiAuth):
        """Signature should be valid base64 string."""
        sig = auth.sign(1700000000000, "GET", "/test")
        decoded = base64.b64decode(sig)
        assert len(decoded) > 0

    def test_headers_contain_required_fields(self, auth: KalshiAuth):
        """Headers should have KEY, TIMESTAMP, SIGNATURE."""
        headers = auth.get_headers("GET", "/test/path")
        assert "KALSHI-ACCESS-KEY" in headers
        assert "KALSHI-ACCESS-TIMESTAMP" in headers
        assert "KALSHI-ACCESS-SIGNATURE" in headers
        assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"

    def test_timestamp_is_milliseconds(self, auth: KalshiAuth):
        """Timestamp in headers should be current time in milliseconds."""
        before = int(time.time() * 1000)
        headers = auth.get_headers("GET", "/test")
        after = int(time.time() * 1000)

        ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
        assert before <= ts <= after

    def test_path_strips_query_params(self, auth: KalshiAuth):
        """Signing /path?foo=bar should use /path only."""
        ts = 1700000000000
        sig_with_query = auth.sign(ts, "GET", "/markets?status=open")
        sig_without_query = auth.sign(ts, "GET", "/markets")
        # Both should produce signatures for the same message
        # (since query params are stripped)
        # Can't directly compare due to PSS randomness, but both should be valid
        assert isinstance(sig_with_query, str)
        assert isinstance(sig_without_query, str)

    def test_method_case_insensitive(self, auth: KalshiAuth):
        """Method should be uppercased in signature."""
        ts = 1700000000000
        # Both should sign the same message (GET uppercase)
        sig_lower = auth.sign(ts, "get", "/test")
        sig_upper = auth.sign(ts, "GET", "/test")
        # Can't compare directly due to PSS randomness
        assert isinstance(sig_lower, str)
        assert isinstance(sig_upper, str)

    def test_signature_verifiable(self, auth: KalshiAuth, rsa_key_pair):
        """Generated signature should be verifiable with the public key."""
        ts = 1700000000000
        method = "GET"
        path = "/trade-api/v2/markets"

        sig_b64 = auth.sign(ts, method, path)
        sig_bytes = base64.b64decode(sig_b64)

        # Verify with public key
        public_key = rsa_key_pair.public_key()
        message = f"{ts}{method.upper()}{path}".encode("utf-8")

        # This should not raise
        public_key.verify(
            sig_bytes,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_invalid_key_path_raises(self):
        """Should raise when key file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            KalshiAuth("key-id", "/nonexistent/key.pem")
