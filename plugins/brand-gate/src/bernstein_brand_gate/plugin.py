"""Bernstein's public pre-tool hook; no configuration means no action."""

import os
from pathlib import Path

from bernstein.plugins import hookimpl
from bernstein.plugins.manager import HookBlockingError

from bernstein_brand_gate.config import load_config
from bernstein_brand_gate.gate import check


class BrandGatePlugin:
    @hookimpl
    def on_pre_tool_use(self, session_id: str, tool: str, tool_input: dict) -> None:
        try:
            value = os.environ.get("BERNSTEIN_BRAND_GATE_CONFIG")
            config = load_config(Path(value) if value else None)
            if config is None or tool not in config.tools:
                return
            target = tool_input.get(config.tools[tool]) if isinstance(tool_input, dict) else None
            receipt = check(target, config, session_id=session_id, tool=tool)
        except Exception:
            raise HookBlockingError("brand_gate", "check or journal unavailable; action blocked") from None
        if receipt["decision"] != "allow":
            raise HookBlockingError("brand_gate", "domain check blocked the action; inspect journal receipt")
