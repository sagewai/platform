"""Tests for sagewai.fleet.mtls — mTLS stubs."""

from __future__ import annotations

from sagewai.fleet.mtls import MTLSConfig, MTLSVerifier


# ------------------------------------------------------------------
# MTLSConfig
# ------------------------------------------------------------------


class TestMTLSConfig:
    def test_defaults(self):
        config = MTLSConfig()
        assert config.ca_cert_path is None
        assert config.server_cert_path is None
        assert config.server_key_path is None
        assert config.require_client_cert is False
        assert config.allowed_cn_patterns == []

    def test_is_configured_false_when_empty(self):
        config = MTLSConfig()
        assert config.is_configured() is False

    def test_is_configured_false_partial(self):
        config = MTLSConfig(ca_cert_path="/ca.pem", server_cert_path="/srv.pem")
        assert config.is_configured() is False

    def test_is_configured_true(self):
        config = MTLSConfig(
            ca_cert_path="/ca.pem",
            server_cert_path="/srv.pem",
            server_key_path="/srv-key.pem",
        )
        assert config.is_configured() is True

    def test_allowed_cn_patterns(self):
        config = MTLSConfig(
            ca_cert_path="/ca.pem",
            server_cert_path="/srv.pem",
            server_key_path="/key.pem",
            allowed_cn_patterns=["*.workers.acme.com", "fleet-*"],
        )
        assert len(config.allowed_cn_patterns) == 2


# ------------------------------------------------------------------
# MTLSVerifier
# ------------------------------------------------------------------


class TestMTLSVerifier:
    def _full_config(self, require: bool = True) -> MTLSConfig:
        return MTLSConfig(
            ca_cert_path="/ca.pem",
            server_cert_path="/srv.pem",
            server_key_path="/key.pem",
            require_client_cert=require,
        )

    def test_is_enabled_false_when_not_configured(self):
        verifier = MTLSVerifier(MTLSConfig())
        assert verifier.is_enabled() is False

    def test_is_enabled_false_when_not_required(self):
        config = self._full_config(require=False)
        verifier = MTLSVerifier(config)
        assert verifier.is_enabled() is False

    def test_is_enabled_true(self):
        verifier = MTLSVerifier(self._full_config())
        assert verifier.is_enabled() is True

    def test_verify_client_cert_returns_none_when_disabled(self):
        verifier = MTLSVerifier(MTLSConfig())
        result = verifier.verify_client_cert("-----BEGIN CERTIFICATE-----")
        assert result is None

    def test_verify_client_cert_stub_returns_none(self):
        verifier = MTLSVerifier(self._full_config())
        result = verifier.verify_client_cert("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----")
        assert result is None

    def test_extract_cn_stub_returns_none(self):
        verifier = MTLSVerifier(self._full_config())
        assert verifier.extract_cn("-----BEGIN CERTIFICATE-----") is None

    def test_config_property(self):
        config = self._full_config()
        verifier = MTLSVerifier(config)
        assert verifier.config is config

    def test_is_configured_requires_all_paths(self):
        """Verify that missing any single path makes is_configured False."""
        base = {
            "ca_cert_path": "/ca.pem",
            "server_cert_path": "/srv.pem",
            "server_key_path": "/key.pem",
        }
        for key in base:
            partial = {k: v for k, v in base.items() if k != key}
            config = MTLSConfig(**partial)
            assert config.is_configured() is False, f"Should be False without {key}"
