synthdesk v0 freeze

date (utc): 2025-12-22

component role
- consumes facts and emits intents only

explicit guarantees
- immutability of consumed facts
- deterministic intent emission from replayable inputs
- replayability of intent outputs

explicit non-goals
- no trading
- no execution
- no optimization
- no learning

no semantic changes allowed without version bump
