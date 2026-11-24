"""Pure configuration transformations for the unified bridge deployment."""
import copy
import re
from urllib.parse import urlsplit

class BridgeLayout:
    def __init__(self, registry):
        self.container = registry["container"]
        self.bridges = registry["bridges"]
        ports = set()
        for name, spec in self.bridges.items():
            port = spec["port"]
            if not re.fullmatch(r"[a-z][a-z0-9]*", name):
                raise ValueError("Invalid bridge name")
            if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
                raise ValueError("Invalid or duplicate bridge port")
            if spec["data_dir"] != "/data/bridges/" + name:
                raise ValueError("Invalid bridge data directory")
            ports.add(port)

    def address(self, name):
        return f"http://{self.container}:{self.bridges[name]['port']}"

    def registration(self, name, original):
        result = copy.deepcopy(original)
        result["url"] = self.address(name)
        return result

    def config(self, name, original):
        result = copy.deepcopy(original)
        app = result["appservice"]
        app["address"] = self.address(name)
        app["port"] = self.bridges[name]["port"]
        app["hostname"] = "0.0.0.0"
        database = result.get("database", {})
        uri = database.get("uri", "")
        # Preserve SQLite query parameters and leave network DSNs untouched.
        if isinstance(uri, str):
            for prefix in ("file:/data/", "/data/"):
                if uri.startswith(prefix):
                    relative = uri[len(prefix):]
                    if relative.startswith("bridges/"):
                        if not relative.startswith("bridges/" + name + "/"):
                            raise ValueError("Database path belongs to another bridge")
                        break
                    path = relative.split("?", 1)[0]
                    if not path or any(part in ("", ".", "..") for part in path.split("/")) or "%" in path:
                        raise ValueError("Unsafe database path")
                    scheme = "file:" if prefix.startswith("file:") else ""
                    database["uri"] = scheme + self.bridges[name]["data_dir"] + "/" + relative
                    break
        return result

    def rewrite_upstreams(self, text):
        # Match complete service:port authorities, not arbitrary substrings.
        for name, spec in self.bridges.items():
            pattern = r"(?<![\w.-])" + re.escape(spec["legacy_service"]) + r":\d+(?![\w.-])"
            text = re.sub(pattern, f"{self.container}:{spec['port']}", text)
        return text
