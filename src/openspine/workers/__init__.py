"""Background workers — the embedding worker and reconciliation jobs.

Each worker is a separate process. The embedding worker consumes the event
bus and upserts vectors into Qdrant; the reconciliation job replays events
that failed to index.

Lands in v0.1 §4.5 (event bus + embedding pipeline).
"""
