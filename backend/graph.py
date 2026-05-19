import json
import re
from typing import TypedDict, List, Dict, Any, Optional

from config import settings
from logger import get_logger
from ollama import Client

from langgraph.graph import StateGraph, START, END

logger = get_logger(__name__)
client = Client(host=settings.OLLAMA_HOST)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class AuditState(TypedDict):
    raw_text: str
    selected_model: str
    value_threshold: float
    blacklist: List[str]
    extracted_vendor: str
    extracted_country: str
    extracted_amount: float
    violations: List[str]
    status: str
    justification: str
    review_action: Optional[str]
    review_notes: Optional[str]
    next_steps: str


FALLBACK_VENDOR = "Unknown Entity"
FALLBACK_COUNTRY = "Unknown Destination"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def clean_json_string(raw_string: str) -> str:
    """Extract the first JSON object from an LLM response."""
    match = re.search(r"\{[\s\S]*\}", raw_string)
    return match.group(0).strip() if match else raw_string.strip()


def extract_amount_from_text(raw_text: str) -> float:
    """Regex fallback: pull the first dollar-style number from free text."""
    cleaned = raw_text.replace(",", "")
    match = re.search(r"\$?([0-9]+(?:\.[0-9]{1,2})?)", cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            logger.warning("Amount extraction failed; returning 0.0.")
    return 0.0


def extract_field(raw_text: str, field_names: List[str]) -> str:
    """Regex fallback: pull a named field value from free text."""
    sanitized = raw_text.replace("\r", " ").replace("\n", " ")
    for token in field_names:
        pattern = re.compile(
            rf"{re.escape(token)}[:\-\s]+([A-Za-z0-9 .,'&-]+)",
            re.IGNORECASE,
        )
        match = pattern.search(sanitized)
        if match:
            return match.group(1).strip()
    return ""


def _ollama_generate(model: str, prompt: str) -> str:
    """
    Call Ollama and reliably return the response text regardless of
    whether the SDK returns a dict or a GenerateResponse object.
    """
    response = client.generate(
        model=model,
        prompt=prompt,
        options={"temperature": 0.0},
    )
    # ollama-python >= 0.3 returns a GenerateResponse dataclass
    if hasattr(response, "response"):
        return response.response or ""
    # Older versions returned a plain dict
    if isinstance(response, dict):
        return response.get("response", "") or ""
    return str(response)


# ---------------------------------------------------------------------------
# Node 1 — Data Structuring Agent
# ---------------------------------------------------------------------------

def parser_node(state: AuditState) -> Dict[str, Any]:
    logger.info("Agent 1 – Data Structuring Agent: extracting structured fields.")
    prompt = (
        "You are Agent 1: Data Structuring Agent. "
        "Extract exactly three fields from the invoice text below.\n"
        'Return ONLY a valid JSON object: {"vendor": "string", "country": "string", "amount": number}\n'
        "Do not include markdown fences, explanation, or extra keys.\n\n"
        f"Invoice Text:\n{state['raw_text']}"
    )

    try:
        raw_output = _ollama_generate(state["selected_model"], prompt)
        data = json.loads(clean_json_string(raw_output))
        result = {
            "extracted_vendor": str(data.get("vendor") or FALLBACK_VENDOR),
            "extracted_country": str(data.get("country") or FALLBACK_COUNTRY),
            "extracted_amount": float(data.get("amount") or 0.0),
        }
        logger.info("Parser node succeeded via LLM extraction.")
        return result
    except Exception:
        logger.warning("Parser node LLM call failed; using regex fallback.", exc_info=True)
        return {
            "extracted_vendor": (
                extract_field(state["raw_text"], ["vendor", "vendor declared", "supplier"])
                or FALLBACK_VENDOR
            ),
            "extracted_country": (
                extract_field(
                    state["raw_text"],
                    ["country", "destination", "location", "dispatch location"],
                )
                or FALLBACK_COUNTRY
            ),
            "extracted_amount": extract_amount_from_text(state["raw_text"]),
        }


# ---------------------------------------------------------------------------
# Node 2 — Deterministic Policy Auditor
# ---------------------------------------------------------------------------

def normalize_blacklist(blacklist: List[str]) -> List[str]:
    return [entry.strip().upper() for entry in blacklist if entry and entry.strip()]


def policy_auditor_node(state: AuditState) -> Dict[str, Any]:
    logger.info("Agent 2 – Policy Engine: evaluating compliance rules.")
    violations: List[str] = []
    target = f"{state['extracted_country']} {state['extracted_vendor']}".strip().upper()

    for blocked in normalize_blacklist(state["blacklist"]):
        if blocked in target:
            msg = f"SANCTION VIOLATION: Transaction target matches blacklist entry: [{blocked}]."
            violations.append(msg)
            logger.warning(msg)

    if state["extracted_amount"] >= state["value_threshold"]:
        msg = (
            f"CAPITAL CONTROLS FLAG: Transaction value "
            f"(${state['extracted_amount']:,.2f}) exceeds the "
            f"configured threshold of ${state['value_threshold']:,.2f}."
        )
        violations.append(msg)
        logger.warning(msg)

    logger.info("Policy auditor completed with %d violation(s).", len(violations))
    return {"violations": violations}


# ---------------------------------------------------------------------------
# Node 3 — Executive Risk Synthesizer
# ---------------------------------------------------------------------------

def risk_synthesizer_node(state: AuditState) -> Dict[str, Any]:
    logger.info("Agent 3 – Risk Synthesizer: generating executive summary.")

    has_sanction = any("SANCTION" in v for v in state["violations"])
    if not state["violations"]:
        status = "APPROVED"
    elif has_sanction:
        status = "REJECTED"
    else:
        status = "HOLD / MANUAL REVIEW"

    if state["violations"]:
        prompt = (
            f"You are Agent 3: Risk Synthesizer acting as Chief Compliance Officer. "
            f"Write a single concise professional audit justification paragraph "
            f"for a transaction flagged as '{status}'. "
            f"Violations detected: {'; '.join(state['violations'])}. "
            "Use formal corporate language. Do not use bullet points or headers."
        )
    else:
        prompt = (
            "You are Agent 3: Risk Synthesizer acting as Chief Compliance Officer. "
            "Write a single concise professional audit justification paragraph "
            "confirming that this transaction passed all compliance checks and is approved. "
            "Use formal corporate language. Do not use bullet points or headers."
        )

    try:
        justification = _ollama_generate(state["selected_model"], prompt).strip()
        if not justification:
            raise ValueError("Empty response from model.")
        logger.info("Risk synthesizer completed successfully.")
        return {"status": status, "justification": justification}
    except Exception:
        logger.warning("Risk synthesizer LLM call failed; using fallback.", exc_info=True)
        fallback = (
            f"This transaction has been flagged as {status}. "
            "The compliance rules were evaluated deterministically and the outcome "
            "is based on the violations listed above. Manual review is recommended."
            if state["violations"]
            else "This transaction has passed all automated compliance checks and is approved for processing."
        )
        return {"status": status, "justification": fallback}


# ---------------------------------------------------------------------------
# Node 4 — Human Review Coordinator
# ---------------------------------------------------------------------------

def human_review_node(state: AuditState) -> Dict[str, Any]:
    logger.info("Agent 4 – Human Review Coordinator: flagging for manual review.")
    return {
        "status": "HOLD / MANUAL REVIEW",
        "next_steps": (
            "Manual review required. Verify supporting documents, supplier credentials, "
            "and jurisdictional controls before proceeding. "
            "If there is any concern about sanctions or dual-use procurement, "
            "escalate to senior compliance immediately."
        ),
        "justification": state["justification"],
    }


def override_review_node(state: AuditState) -> Dict[str, Any]:
    action = state["review_action"]
    notes = state.get("review_notes") or ""

    if action == "approve":
        logger.info("Human reviewer approved the transaction.")
        return {
            "status": "APPROVED",
            "justification": f"{state['justification']} Human reviewer confirmed approval. {notes}".strip(),
            "next_steps": "Compliance reviewer approved the transaction; proceed with processing.",
        }
    if action == "reject":
        logger.info("Human reviewer rejected the transaction.")
        return {
            "status": "REJECTED",
            "justification": f"{state['justification']} Human reviewer rejected the transaction. {notes}".strip(),
            "next_steps": "Compliance reviewer rejected this transaction; block execution and notify the originating team.",
        }
    if action == "escalate":
        logger.info("Human reviewer escalated the transaction.")
        return {
            "status": "ESCALATED",
            "justification": f"{state['justification']} Escalated by reviewer: {notes or 'No additional notes.'}",
            "next_steps": "Escalated for senior compliance investigation. Capture all evidence and prepare escalation notes.",
        }
    logger.warning("override_review_node called with unrecognised action: %s", action)
    return {}


# ---------------------------------------------------------------------------
# Node 5 — Finalizer
# ---------------------------------------------------------------------------

def finalize_node(state: AuditState) -> Dict[str, Any]:
    if state.get("next_steps"):
        return {}
    status = state.get("status", "")
    step_map = {
        "APPROVED": "Approved. Transaction may move forward under existing compliance controls.",
        "REJECTED": "Rejected. Do not process this transaction and notify the compliance team.",
        "ESCALATED": "Escalated. Senior compliance review is required before any action is taken.",
        "HOLD / MANUAL REVIEW": "On hold pending manual review. Do not process until a reviewer decision is recorded.",
    }
    return {"next_steps": step_map.get(status, "Review completed. No additional action required.")}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_after_risk(state: AuditState) -> str:
    if state.get("review_action") in {"approve", "reject", "escalate"}:
        return "override_review"
    if state.get("status") == "HOLD / MANUAL REVIEW":
        return "human_review"
    return "finalize"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_compliance_graph() -> object:
    graph = StateGraph(AuditState)

    graph.add_node("parser", parser_node)
    graph.add_node("policy", policy_auditor_node)
    graph.add_node("risk", risk_synthesizer_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("override_review", override_review_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "parser")
    graph.add_edge("parser", "policy")
    graph.add_edge("policy", "risk")
    # NOTE: only ONE edge source from "risk" — via conditional routing
    graph.add_conditional_edges(
        "risk",
        route_after_risk,
        {
            "human_review": "human_review",
            "override_review": "override_review",
            "finalize": "finalize",
        },
    )
    graph.add_edge("human_review", "finalize")
    graph.add_edge("override_review", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


COMPILED_COMPLIANCE_GRAPH = build_compliance_graph()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_compliance_graph(input_data: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("Starting compliance graph orchestration.")

    state: AuditState = {
        "raw_text": input_data["raw_text"],
        "selected_model": input_data.get("selected_model", "qwen2.5:7b"),
        "value_threshold": float(input_data.get("value_threshold", 15000.0)),
        "blacklist": input_data.get("blacklist", []),
        "extracted_vendor": "",
        "extracted_country": "",
        "extracted_amount": 0.0,
        "violations": [],
        "status": "",
        "justification": "",
        "review_action": input_data.get("review_action"),
        "review_notes": input_data.get("review_notes"),
        "next_steps": "",
    }

    output = COMPILED_COMPLIANCE_GRAPH.invoke(state)

    if not output.get("next_steps"):
        output["next_steps"] = "Audit completed. No next steps were generated."

    logger.info(
        "Compliance graph completed — status=%s vendor=%s country=%s",
        output.get("status"),
        output.get("extracted_vendor"),
        output.get("extracted_country"),
    )
    return output
