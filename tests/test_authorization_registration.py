"""Unit tests for Synapse registration and interrupted-registration recovery."""
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "services" / "authorization"
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
os.environ.setdefault("MATRIX_LAN_DSN", "postgresql://unused")
os.environ.setdefault("SYNAPSE_REGISTRATION_SECRET", "test-secret")
os.environ.setdefault("MATRIX_SERVER_NAME", "localhost")
import api


class RegistrationTests(unittest.TestCase):
    @patch.object(api, "request")
    def test_new_registration(self, request):
        request.side_effect = [
            (200, {"nonce": "abc"}),
            (200, {"user_id": "@alice:localhost", "access_token": "token"}),
        ]
        result = api.register_synapse("alice", "long-password")
        self.assertEqual(result["user_id"], "@alice:localhost")
        body = request.call_args_list[1].args[1]
        import hashlib
        import hmac
        expected = hmac.new(b"test-secret", b"abc\\x00alice\\x00long-password\\x00notadmin", hashlib.sha1).hexdigest()
        self.assertEqual(body["mac"], expected)

    @patch.object(api, "request")
    def test_recovery_requires_password_proof(self, request):
        request.side_effect = [
            (200, {"nonce": "abc"}),
            (400, {"errcode": "M_USER_IN_USE"}),
            (403, {"errcode": "M_FORBIDDEN"}),
        ]
        with self.assertRaisesRegex(api.RegistrationError, "already exists"):
            api.register_synapse("alice", "wrong-password")

    @patch.object(api, "request")
    def test_recovery_existing_account(self, request):
        request.side_effect = [
            (200, {"nonce": "abc"}),
            (400, {"errcode": "M_USER_IN_USE"}),
            (200, {"user_id": "@alice:localhost", "access_token": "recovered"}),
        ]
        result = api.register_synapse("alice", "correct-password")
        self.assertEqual(result["access_token"], "recovered")

    @patch.object(api, "request")
    def test_recovery_rejects_other_server(self, request):
        request.side_effect = [
            (200, {"nonce": "abc"}),
            (400, {"errcode": "M_USER_IN_USE"}),
            (200, {"user_id": "@alice:other", "access_token": "bad"}),
        ]
        with self.assertRaisesRegex(api.RegistrationError, "Unexpected"):
            api.register_synapse("alice", "correct-password")


if __name__ == "__main__":
    unittest.main()
