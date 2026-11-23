#!/usr/bin/env python3
"""Basic regression checks for the read-only database inventory."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from audit_databases import inventory

class DatabaseInventoryTests(unittest.TestCase):
    def test_known_postgres_services(self):
        rows = {r["component"]: r for r in inventory()["database_configs"]}
        for name in ("synapse", "whatsapp", "telegram", "linkedin"):
            self.assertTrue(rows[name]["postgres"], name)

    def test_known_sqlite_services(self):
        rows = {r["component"]: r for r in inventory()["database_configs"]}
        for name in ("facebook", "instagram", "email", "twitter", "tiktok"):
            self.assertTrue(rows[name]["sqlite"], name)

    def test_inventory_does_not_include_credentials(self):
        rows = inventory()["database_configs"]
        for row in rows:
            self.assertNotIn("uri", row)
            self.assertNotIn("password", row)

if __name__ == "__main__":
    unittest.main()
