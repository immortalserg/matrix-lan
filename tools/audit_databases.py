#!/usr/bin/env python3
"""Read-only inventory of Matrix-LAN's declared SQL backends.

Run: python3 tools/audit_databases.py
     python3 tools/audit_databases.py --json
No Docker access, credentials, runtime files, or database connections required.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "templates" / "config"
INSTALL = ROOT / "install.py"

def inventory():
    services = json.loads((ROOT / "templates" / "services.json").read_text(encoding="utf-8"))
    rows = []
    known = set()
    for path in sorted(CONFIG.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        db = doc.get("database")
        if not isinstance(db, dict):
            continue
        name = path.stem
        known.add(name)
        backend = str(db.get("type", db.get("name", "unspecified")))
        uri = str(db.get("uri", ""))
        # Do not output connection URIs: they can contain password placeholders or real credentials.
        rows.append({"component": name, "backend": backend,
                     "postgres": backend in ("postgres", "postgresql") or backend == "psycopg2",
                     "sqlite": "sqlite" in backend.lower(),
                     "config": str(path.relative_to(ROOT))})
    rows.sort(key=lambda row: row["component"])
    return {"declared_compose_services": len(services), "database_configs": rows,
            "not_assessed": sorted(services.keys())}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()
    data = inventory()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("Matrix-LAN declared database backends (read-only static audit)")
    print(f"Compose services: {data['declared_compose_services']}")
    for row in data["database_configs"]:
        print(f"  {row['component']:<16} {row['backend']:<20} {row['config']}")
    print("NOTE: Other services may store data in SQLite, JSON, or browser sessions.")
    print("NOTE: This does not verify PostgreSQL support or migration compatibility.")

if __name__ == "__main__":
    main()
