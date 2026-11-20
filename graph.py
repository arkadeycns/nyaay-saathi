"""
LangGraph wiring for the three flows. Nodes live in nodes_*.py; this file only
declares state and edges, so the AI-vs-code split is visible at a glance:

advisor:   screen(code) → intake(LLM) → triage(code) → retrieve(LLM rewrite + search + gate)
           → advise(LLM) → verify(code A + LLM B, 1 regeneration) → assemble(code)
decoder:   extract(LLM) → span_check(code) → triage(code) → provisions(code + search)
           → explain(LLM) → verify(code A + LLM B) → assemble(code)
simulator: is a turn loop in nodes_simulator.py (no branching needed).
"""
from __future__ import annotations

import functools
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

import nodes_advisor as A
import nodes_decoder as D


class AdvisorState(TypedDict, total=False):
    messages: list[dict]            # chat history: {"role": "user"|"assistant", "content": str}
    clarify_rounds: int
    outcome: Optional[str]          # None = continue | crisis | escalate | clarify | ungrounded | use_decoder
    blocked: Optional[str]
    crisis_message: str
    facts: dict
    context: Optional[dict]
    distress: bool
    lang: str
    questions: list[str]
    escalation: dict
    queries: list[str]
    sources: dict
    gate: dict
    advice: dict
    verifier_log: list[dict]
    dropped_claims: int
    partial_claims: list[int]
    result: dict
    audit: Annotated[list[dict], operator.add]


def _stop_or(next_node: str):
    return lambda s: END if s.get("outcome") else next_node


@functools.lru_cache(maxsize=1)
def advisor_graph():
    g = StateGraph(AdvisorState)
    g.add_node("screen", A.screen)
    g.add_node("intake", A.intake)
    g.add_node("triage", A.triage)
    g.add_node("retrieve", A.retrieve)
    g.add_node("advise", A.advise)
    g.add_node("verify", A.verify_node)
    g.add_node("assemble", A.assemble)
    g.set_entry_point("screen")
    g.add_conditional_edges("screen", _stop_or("intake"))
    g.add_edge("intake", "triage")
    g.add_conditional_edges("triage", _stop_or("retrieve"))
    g.add_conditional_edges("retrieve", _stop_or("advise"))
    g.add_edge("advise", "verify")
    g.add_edge("verify", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


class DecoderState(TypedDict, total=False):
    notice_text: str
    received_date: Optional[str]
    outcome: Optional[str]          # None | escalate | crisis
    notice: dict
    span_issues: list[str]
    triage: dict
    provisions: list[dict]
    sources: dict
    explanation: dict
    verifier_log: list[dict]
    dropped_claims: int
    result: dict
    audit: Annotated[list[dict], operator.add]


@functools.lru_cache(maxsize=1)
def decoder_graph():
    g = StateGraph(DecoderState)
    g.add_node("extract", D.extract)
    g.add_node("span_check", D.span_check)
    g.add_node("triage_notice", D.triage)
    g.add_node("lookup_provisions", D.provisions)
    g.add_node("explain", D.explain)
    g.add_node("verify", D.verify_node)
    g.add_node("assemble", D.assemble)
    g.set_entry_point("extract")
    g.add_edge("extract", "span_check")
    g.add_edge("span_check", "triage_notice")
    g.add_conditional_edges("triage_notice", _stop_or("lookup_provisions"))
    g.add_edge("lookup_provisions", "explain")
    g.add_edge("explain", "verify")
    g.add_edge("verify", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


NODE_LABELS = {
    "screen": "Checking for urgent safety issues",
    "intake": "Understanding your situation",
    "triage": "Checking scope and urgency (rules)",
    "retrieve": "Searching the law text",
    "advise": "Drafting an explanation",
    "verify": "Checking every legal statement against the source",
    "assemble": "Preparing your options, deadlines and checklist",
    "extract": "Reading the notice",
    "span_check": "Checking every extracted detail against the notice text",
    "triage_notice": "Checking what kind of notice this is (rules)",
    "lookup_provisions": "Looking up the laws the notice cites",
    "explain": "Explaining it in plain words",
}


def run(graph, state: dict[str, Any], on_step=None) -> dict:
    """Stream node updates (for UI status lines) and return the final merged state."""
    final = dict(state)
    for update in graph.stream(state, stream_mode="updates"):
        for node, delta in update.items():
            if on_step:
                on_step(node, NODE_LABELS.get(node, node))
            if delta:
                for k, v in delta.items():
                    final[k] = final.get(k, []) + v if k == "audit" else v
    return final
