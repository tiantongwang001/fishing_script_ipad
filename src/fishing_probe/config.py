"""Per-machine configuration; never guess real device identifiers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    udid: str
    appium_url: str = "http://127.0.0.1:4723"
    wda_mode: str = "xcode"
    wda_bundle_id: str = ""
    game_bundle_id: str = ""
    expected_ipados: str = ""
    derived_data_path: str = ""
    wda_bundle_id_suffix: str | None = None

    def __post_init__(self) -> None:
        if self.wda_mode not in {"xcode", "preinstalled"}:
            raise ValueError("wda_mode must be 'xcode' or 'preinstalled'")
        try:
            parsed = urlsplit(self.appium_url)
            local_url = (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port is not None and 1 <= parsed.port <= 65535
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment
                and self.appium_url == self.appium_url.strip()
                and not any(char in self.appium_url for char in "\\\r\n\t")
            )
        except (ValueError, TypeError, AttributeError):
            local_url = False
        if not local_url:
            raise ValueError("Appium URL must use HTTP, a localhost address and explicit port, without credentials/query/fragment")
        if self.wda_bundle_id_suffix is not None and not isinstance(self.wda_bundle_id_suffix, str):
            raise ValueError("wda_bundle_id_suffix must be a string or null")
        if not isinstance(self.derived_data_path, str):
            raise ValueError("derived_data_path must be a path string")


def load(path: str | Path = "config.json") -> Config:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing. Copy config.example.json and enter verified local values.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = Config.__dataclass_fields__.keys()
    return Config(**{key: payload[key] for key in allowed if key in payload})
