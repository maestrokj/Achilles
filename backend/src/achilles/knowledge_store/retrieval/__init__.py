"""Retrieval primitives over the projections, all behind one ACL pre-filter.

lexical (FTS) · sql (bounded filters) · graph (recursive CTE) · vector (ANN,
stage 4) — plus rank-level fusion and the assembled hybrid result the RAG
route consumes in one call (hybrid-search.html).
"""
