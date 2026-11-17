"""Enterprise knowledge assistant: intent routing, multi-turn memory, grounded answers.

This package is a service layer that sits *beside* the frozen knowledge base (ekb/):
it never imports or modifies ekb internals, it calls the KB's public HTTP API. That is
how an assistant is layered on a retrieval service in practice — the KB stays the
system of record for documents, permissions and citations, and the assistant owns
conversation state, intent routing, query rewriting, handoff and the unanswered-question
backlog that operations use to close knowledge gaps.
"""

__all__ = ["service", "intent", "store", "kb", "llm"]
