"""Cost estimate of a call before it happens, the same for every model.

The node passes every measure it knows (call, seconds, characters); the registry price picks
its own unit, so the node never needs to know how a model bills.
"""

from __future__ import annotations

import math

from pipeline.registry import ModelSpec


def cost(spec: ModelSpec, *, calls: int = 1, seconds: float = 0.0, chars: int = 0) -> float:
    p = spec.price
    if p.usd == 0:
        return 0.0
    units = {"call": calls, "second": seconds, "1k_chars": chars / 1000}[p.unit]
    billed = math.ceil(units / p.bill_step - 1e-9) * p.bill_step if p.unit == "second" else units
    return round(p.usd * billed, 6)
