from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Market:
    id: str
    event_id: str
    event_title: str
    question: str
    yes_price: float
    no_price: float
    sell_yes_price: float = 0.0
    sell_no_price: float = 0.0
    volume_usd: float = 0.0
    liquidity_usd: float = 0.0
    rules: str = ""
    close_time: int | None = None
    category: str = ""
    subcategory: str = ""
    is_live: bool = False
    search_query: str = ""
    resolution_source: str = ""
    yes_label: str = "YES"
    no_label: str = "NO"
    # Timed Jupiter Up/Down events are exposed as one YES-buyable market per
    # direction. These fields preserve that execution semantics without
    # fabricating a NO quote that Jupiter does not publish.
    one_sided_yes: bool = False
    timed_direction: str = ""
    event_begin_at: int | None = None

    def text(self) -> str:
        return " ".join(x for x in (self.event_title, self.question, self.rules) if x)


@dataclass(slots=True)
class CryptoMarketSpec:
    asset: str
    comparator: str
    threshold_low: float | None
    threshold_high: float | None
    expiry_ts: int
    timezone_name: str
    settlement_kind: str
    currency: str = "USD"
    resolution_source: str = ""
    event_family: str = ""
    window_start_ts: int | None = None
    ambiguous: bool = False
    reject_reason: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceObservation:
    source: str
    value: float | None
    observed_at: str
    kind: str
    reliability: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EngineEstimate:
    engine: str
    probability_yes: float
    confidence: float
    reliability: float
    source_agreement: float
    reasoning: str
    evidence: list[str]
    observations: list[SourceObservation]
    supported: bool = True
    reject_reason: str = ""
    asset: str = ""
    volatility: float = 0.0
    liquidity: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    spread: float = 0.0
    evidence_json: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["observations"] = [o.dict() for o in self.observations]
        return raw


@dataclass(slots=True)
class Signal:
    market_id: str
    question: str
    outcome: str
    price: float
    probability: float
    confidence: float
    reliability: float
    edge: float
    score: float
    stake_usd: float
    signal_type: str
    reasoning: str
    evidence: list[str]
    source_count: int
    source_agreement: float
    asset: str = ""
    expiry: int | None = None
    resolution_source: str = ""
    volatility: float = 0.0
    liquidity: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    spread: float = 0.0
    event_family: str = ""
    evidence_json: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return asdict(self)
