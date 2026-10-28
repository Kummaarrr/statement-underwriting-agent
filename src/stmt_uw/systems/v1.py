"""v1 as an evaluated system: the agent with a configuration (variants for the ablation)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from stmt_uw.llm import CallInfo
from stmt_uw.schema import Assessment
from stmt_uw.v1.agent import Config, analyse_record

VARIANTS = {
    "v1": Config(),
    "v1-pdf": Config(digital_mode="pdf"),  # read digital pages as PDF instead of text
    "v1-noheal": Config(heal_attempts=0),
    "v1-norules": Config(use_rules=False),
    "v1-noforensics": Config(use_forensics=False),
    "v1-noescalate": Config(escalate_model=None),
}
SYSTEM = ""  # (no single prompt: page and labelling prompts live in the v1 modules)


def make(variant: str) -> Any:
    class _System:
        name = variant
        SYSTEM = ""

        @staticmethod
        def run(record: dict[str, Any], model: str) -> tuple[Assessment, CallInfo]:
            cfg = replace(VARIANTS[variant], model=model)
            result = analyse_record(record, cfg)
            usage: dict[str, int] = {}
            for c in result.calls:
                for k, v in c.usage.items():
                    usage[k] = usage.get(k, 0) + v
            cached = bool(result.calls) and all(c.cached for c in result.calls)
            info = CallInfo(model, usage, result.cost_usd, result.seconds, cached)
            return result.assessment, info

    return _System
