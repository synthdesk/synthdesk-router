"""
Event Type Namespace (Authoritative)

This file is law. Changes require explicit decision.

Rule: No string literals for event_type anywhere else in the system.

Namespace structure: <domain>.<event>
"""

# Market domain
MARKET_REGIME = "market.regime"
MARKET_REGIME_CHANGE = "market.regime_change"
# Trade prints (DOCTRINE: TRADE_PRINT_INGESTOR_V0)
# Added 2026-01-29
# Factual record of executed trades from exchange.
# One event = one execution. No aggregation.
# side = aggressor side (buyer lifted ask / seller hit bid).
MARKET_TRADE = "market.trade"
# Candle (OHLCV bar) from exchange or listener aggregation.
# Added 2026-01-29
MARKET_CANDLE = "market.candle"

# Router domain
ROUTER_PERMISSION = "router.permission"
ROUTER_INTENT = "router.intent"
ROUTER_INTENT_WEAK = "router.intent_weak"
ROUTER_INTENT_SHADOW = "router.intent_shadow"
ROUTER_VETO = "router.veto"
ROUTER_HEARTBEAT = "router.heartbeat"
ROUTER_HEALTH_SUMMARY = "router.health_summary"
# Portfolio summary event (cross-asset sizing observability)
# Added 2026-02-26
ROUTER_PORTFOLIO_V0 = "router.portfolio.v0"
ROUTER_AUTHORITY_DEMOTION = "router.authority_demotion"
ROUTER_SPEECH_V1 = "router.speech.v1"
# Decision event: explains surface gate evaluation (DOCTRINE: VETO_TIMESCALE)
# Added 2026-01-23
ROUTER_DECISION_V1 = "router.decision.v1"

# Listener domain
LISTENER_START = "listener.start"
LISTENER_STOP = "listener.stop"
LISTENER_CRASH = "listener.crash"
LISTENER_MISSING_OBSERVATION = "listener.missing_observation"
LISTENER_METRICS_INVALID = "listener.metrics_invalid"
LISTENER_TIMESTAMP_NON_MONOTONIC = "listener.timestamp_non_monotonic"
LISTENER_VOL_WINDOW_INVALID = "listener.vol_window_invalid"
LISTENER_DOWNTIME = "listener.downtime"

# Invariant domain
INVARIANT_VIOLATION = "invariant.violation"
INVARIANT_SUMMARY = "invariant.summary"
INVARIANT_RESULT = "invariant.result"
INVARIANT_DRIFT_WARNING = "invariant.drift_warning"
INVARIANT_DRIFT_CRITICAL = "invariant.drift_critical"

# Agency domain (future)
AGENCY_DECISION = "agency.decision"
AGENCY_POLICY_PROPOSAL = "agency.policy_proposal"
AGENCY_POLICY_VETO = "agency.policy_veto"
AGENCY_DECISION_RESOLVED = "agency.decision_resolved"

# Spectral domain (DOCTRINE: SPECTRAL_INVARIANTS)
# Added 2026-01-21
SPECTRAL_EMIT = "spectral.emit"
SPECTRAL_DRIFT = "spectral.drift"
SPECTRAL_ALERT = "spectral.alert"

# Risk domain (evidence layer)
# Added 2026-01-21, contract: docs/EVIDENCE_DRAWDOWN_v1.md
RISK_DRAWDOWN = "risk.drawdown"

# Episode state domain (DOCTRINE: DRAWDOWN_EPISODE_STATE_MACHINE_V2)
# Added 2026-01-25, restored 2026-01-29
# Edge-triggered state transitions for drawdown/recovery episodes.
# States: flat, drawdown.active, recovery.eligible, recovery.active,
#         recovery.complete, recovery.timeout
RISK_EPISODE_STATE = "risk.episode_state"
RISK_EPISODE_CLUSTER = "risk.episode_cluster"
RISK_RECOVERY_ASYMMETRY = "risk.recovery_asymmetry"

# Regime domain (adjudication layer)
# Added 2026-01-21, contract: docs/SOAK_REGIME_HARVEST_v1.md
REGIME_TAG = "regime.tag"

# Veto Surface domain (time-scale aware risk surfaces)
# Added 2026-01-23, doctrine: docs/DOCTRINE_VETO_TIMESCALE.md
# Evidence: EVT-0, EVT-1A, EVT-1B (gate2_final_2026-01-20)
RISK_VETO_SURFACE_REGIME_V1 = "risk.veto_surface.regime.v1"
RISK_VETO_SURFACE_MICRO_V1 = "risk.veto_surface.micro.v1"

# Coherence domain (coordination scalar, NOT predictor)
# Added 2026-01-24, doctrine: docs/DOCTRINE_COHERENCE_DCI.md
# DCI conditions epistemic admissibility, not expected value
COHERENCE_DCI = "coherence.dci"
COHERENCE_DCI_INVARIANT_VIOLATION = "coherence.dci.invariant_violation"
COHERENCE_STATE = "coherence.state"

# State Diff domain (legibility layer)
# Added 2026-01-24
STATE_DIFF = "state.diff"

# Temporal domain (DOCTRINE: TEMPORAL_PRIMITIVES_V0)
# Added 2026-01-24
# Temporal primitives measure dynamic properties of state transitions.
# They are meta-observations (observations about observations).
# Raw primitives:
TEMPORAL_ONSET = "temporal.onset"           # Delay from eligible to active
TEMPORAL_DURATION = "temporal.duration"     # Time continuously in state
TEMPORAL_SLOPE = "temporal.slope"           # Rate of change within state
TEMPORAL_HALFLIFE = "temporal.halflife"     # Time to 50% reversion
TEMPORAL_PERSISTENCE = "temporal.persistence"  # Survival probability at tau
TEMPORAL_HYSTERESIS = "temporal.hysteresis" # Enter/exit threshold gap (definition-time)
# Derived composites:
TEMPORAL_ORDERING = "temporal.ordering"     # Cross-subject ordering (derived)

# Perception domain (DOCTRINE: PERCEPTUAL_PRIMITIVES_V0)
# Added 2026-01-25
# Perceptual primitives describe structure without implying tradability.
# They are NON-ACTIONABLE by constitution.
# Layer: perception (observation about observation)
PERCEPTION_PRIMITIVE = "perception.primitive"

# CSDI (Cross-Scale Disagreement Index) - added 2026-01-25
# Measures disagreement between time horizons.
PERCEPTION_CSDI = "perception.csdi"

# Descriptor Entropy (DOCTRINE: DESCRIPTOR_ENTROPY_NONINFERENCE_V0)
# Added 2026-01-26
# Measures perceptual degeneracy via Shannon entropy over joint descriptor state.
# Detects null-attractor / descriptor collapse conditions.
# NON-ACTIONABLE: entropy does NOT imply tradability.
PERCEPTION_DESCRIPTOR_ENTROPY = "perception.descriptor_entropy"
PERCEPTION_CONCENTRATION_DYNAMICS = "perception.concentration_dynamics"
PERCEPTION_INVALIDATION_GEOMETRY = "perception.invalidation_geometry"
PERCEPTION_PHASE_LAG = "perception.phase_lag"

# Exploit domain (downstream of epistemic stack)
# Added 2026-01-27, doctrine: XEXEC_CONSTITUTION.md
# INVARIANT: xexec may consume but NEVER modify the epistemic stack.
# One-way causality: epistemic -> exploit only.
EXPLOIT_ORDER_INTENT = "exploit.order_intent"  # Entry decision with sizing
EXPLOIT_FILL = "exploit.fill"                  # Entry/exit/addon fills
EXPLOIT_POSITION = "exploit.position"          # Position snapshots
EXPLOIT_ATTRIBUTION = "exploit.attribution"    # Closed trade attribution for learning
EXPLOIT_ABSTENTION = "exploit.abstention"      # Decision not to trade (near-miss tracking)
EXPLOIT_HEARTBEAT = "exploit.heartbeat"        # Liveness proof

# Pre-edge domain (shadow-only perception, DOCTRINE: FAILED_CONTINUATION_V0)
# Added 2026-01-27
# Detects moves that should extend but don't. NON-ACTIONABLE.
PRE_EDGE_ATTEMPT = "pre_edge.attempt"

# Convexity domain (DOCTRINE: CONVEXITY_DESCRIPTOR_V0)
# Added 2026-01-27
# Scalar descriptor of defined-downside / unbounded-upside conditions.
CONVEXITY_POTENTIAL = "convexity.potential"

# Micro rhythm domain (DOCTRINE: MICRO_RHYTHM_V0)
# Added 2026-01-27
# Pressure imbalance from tick-level rhythm. No directional claims.
MICRO_RHYTHM_IMBALANCE = "micro_rhythm.imbalance"

# Fragility domain (DOCTRINE: STATE_FRAGILITY_V0)
# Added 2026-01-27
# Regime transition risk from entropy + variance-coherence divergence.
STATE_FRAGILITY = "state.fragility"

# Edge decay domain (DOCTRINE: EDGE_DECAY_V0)
# Added 2026-01-27
# IC halflife and survival curves per regime/asset.
EDGE_DECAY_ESTIMATE = "edge_decay.estimate"

# Tension rank domain (DOCTRINE: TENSION_RANK_V0)
# Added 2026-01-27
# Cross-asset priority ordering. L2 comparative descriptor.
TENSION_RANK = "tension.rank"

# Perception: price liveness (infrastructure telemetry, NOT epistemic)
# Added 2026-01-28
# Fact pulse: "a price was seen at time t from venue v".
# Not a harness observer. No predicate semantics.
# Consumers: FeedStaleTracker (infra health).
PERCEPTION_PRICE_LIVENESS = "perception.price_liveness"

# Contradiction domain (observer harness emissions)
# Added 2026-01-28 (tranche-1)
CONTRADICTION_EXIT_V0 = "contradiction.exit.v0"
CONTRADICTION_LEADERLAG_V0 = "contradiction.leaderlag.v0"
CONTRADICTION_QUALITY_V0 = "contradiction.quality.v0"
CONTRADICTION_CYCLE_V0 = "contradiction.cycle.v0"
CONTRADICTION_DWELL_V0 = "contradiction.dwell.v0"
# Contradiction morphology domain (EBC v0.1)
# Added 2026-01-28
# Semantic classification of contradiction state: none | degenerate | structural | opportunity_candidate.
# Derived read-only from StateSnapshot. NON-ACTIONABLE (epistemic enrichment only).
CONTRADICTION_CLASSIFIED = "contradiction.classified"

# Observer domain (harness infrastructure events)
# Added 2026-01-28 (tranche-1)
OBSERVER_EPISODE = "observer.episode"
OBSERVER_PERCEPTUAL = "observer.perceptual"
OBSERVER_MICRO_GEOMETRY_SNAPSHOT_V0 = "observer.micro_geometry.snapshot.v0"
OBSERVER_MICRO_GEOMETRY_SUMMARY_V0 = "observer.micro_geometry.summary.v0"
OBSERVER_COLLAPSE_ALARM = "observer.collapse_alarm"
OBSERVER_RETIRED = "observer.retired"
# Orderbook conditional geometry (reduced OB snapshots for offline analysis)
# Added 2026-01-29
OBSERVER_ORDERBOOK_CONDITIONAL_GEOMETRY_V0 = "observer.orderbook.conditional_geometry.v0"

# Participation domain (DOCTRINE: PARTICIPATION_STATE_V0)
# Added 2026-01-29
# Detects market participation density normalization.
# Gate for post-contradiction action admissibility.
# NON-ACTIONABLE: state descriptor only.
OBSERVER_PARTICIPATION_REACTIVATED = "observer.participation.reactivated"
OBSERVER_PARTICIPATION_DEACTIVATED = "observer.participation.deactivated"
OBSERVER_PARTICIPATION_SURGE_V1 = "observer.participation.surge_v1"

# Technical Analysis domain (DOCTRINE: TA_PREEDGE_V0)
# Added 2026-01-29
# Non-actionable structural / volatility / flow descriptors.
# Expand perception without implying tradability.
# Layer: perception (observatory membrane).
TA_STRUCTURE_VIOLATION = "ta.structure_violation"
TA_VOLATILITY_PRESSURE = "ta.volatility_pressure"
TA_FLOW_IMBALANCE = "ta.flow_imbalance"
TA_VOLATILITY_FAILED = "ta.volatility_failed"
TA_EXTREME_STALL = "ta.extreme_stall"
TA_PRE_EDGE_PRESSURE = "ta.pre_edge_pressure"

# Perception: Asymmetry Persistence (DOCTRINE: PERCEPTUAL_LAYER_AP_V1)
# Added 2026-01-29
# Measures duration of sustained microstructure imbalance.
# Temporal persistence applied to orderbook asymmetry.
# NON-ACTIONABLE: describes durability of state, not correctness.
PERCEPTION_AP_V1 = "perception.ap_v1"

# Governance domain (evidence lifecycle)
# Added 2026-01-29
# Emitted when an evidence window expires without renewal.
GOVERNANCE_EVIDENCE_EXPIRED = "governance.evidence_expired"
