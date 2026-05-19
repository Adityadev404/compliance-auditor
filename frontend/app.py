import os
import re
import time

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Enterprise AI Risk Auditor",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BACKEND_HOST = os.getenv("BACKEND_HOST", "http://127.0.0.1:8000")
API_ENDPOINT = f"{BACKEND_HOST}/api/v1/audit"
HEALTH_ENDPOINT = f"{BACKEND_HOST}/health"
REQUEST_TIMEOUT = 180  # seconds — must accommodate LLM inference time

MOCK_INVOICE = """\
INVOICE TRANSACTION LOGS: #INV-2026-904
VENDOR DECLARED: Vostok Industrial Components Group.
TARGET DISPATCH LOCATION: Freight Terminal Alpha, Saint Petersburg, Russia
NET VALUATION: $34,800.00 USD
MANIFEST INVENTORY: Telemetry tracking hardware units, dual-use high-frequency \
semiconductors, and communication arrays.\
"""

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "audit_response" not in st.session_state:
    st.session_state.audit_response = None
if "last_payload" not in st.session_state:
    st.session_state.last_payload = None
if "backend_ok" not in st.session_state:
    st.session_state.backend_ok = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_backend_health() -> bool:
    try:
        r = requests.get(HEALTH_ENDPOINT, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def parse_blacklist(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n,;]+", raw) if item.strip()]


def status_color(status: str) -> str:
    mapping = {
        "APPROVED": "green",
        "REJECTED": "red",
        "HOLD / MANUAL REVIEW": "orange",
        "ESCALATED": "red",
    }
    return mapping.get(status, "gray")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🛡️ Audit Control Panel")

    # Backend health indicator
    if st.button("🔄 Check backend status"):
        st.session_state.backend_ok = check_backend_health()

    if st.session_state.backend_ok is True:
        st.success("Backend: Online ✓")
    elif st.session_state.backend_ok is False:
        st.error("Backend: Offline ✗")
    else:
        st.info("Click above to check backend status.")

    st.divider()

    active_model = st.selectbox(
        "Inference model",
        ["qwen2.5:7b", "llama3:8b", "mistral:latest"],
        help="Select the local Ollama model to use for AI summarisation.",
    )
    cap_limit = st.slider(
        "Audit threshold (USD)",
        min_value=1_000,
        max_value=500_000,
        value=15_000,
        step=1_000,
        help="Transactions at or above this value trigger a capital-controls flag.",
    )
    blacklist_input = st.text_area(
        "Restricted jurisdictions / entities",
        value="Iran, Russia, North Korea",
        height=120,
        help="Comma- or newline-separated list. Case-insensitive.",
    )

    st.divider()
    st.markdown(
        "**How to use**\n"
        "1. Paste or upload invoice / ledger text.\n"
        "2. Adjust the threshold and blacklist as needed.\n"
        "3. Click **Run compliance workflow**.\n"
        "4. Review the decision, summary, and next steps.\n"
        "5. If flagged for manual review, use the override panel."
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🛡️ On-Premise Financial Risk & Compliance Auditor")
st.markdown(
    "AI-powered, stateful compliance pipeline — no raw JSON required. "
    "Review the plain-language decision, executive summary, and recommended next steps."
)

# ---------------------------------------------------------------------------
# Document ingestion
# ---------------------------------------------------------------------------
st.subheader("Document ingestion")
uploaded_file = st.file_uploader(
    "Upload an invoice or transaction text file",
    type=["txt", "json"],
    help="UTF-8 encoded .txt or .json files only.",
)

if uploaded_file is not None:
    try:
        document_payload = uploaded_file.read().decode("utf-8", errors="ignore")
        st.success(f"File loaded: **{uploaded_file.name}** ({len(document_payload):,} characters)")
    except Exception as exc:
        document_payload = MOCK_INVOICE
        st.error(f"Could not decode file: {exc}. Using sample invoice instead.")
else:
    document_payload = MOCK_INVOICE

document_payload = st.text_area(
    "Raw invoice / ledger / transaction text",
    value=document_payload,
    height=220,
    help="Paste free-form text. The AI agent will extract vendor, country, and amount automatically.",
)

# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------
run_clicked = st.button("▶ Run compliance workflow", type="primary", use_container_width=True)

if run_clicked:
    if not document_payload.strip():
        st.warning("Please enter or upload invoice text before running the workflow.")
    else:
        payload = {
            "raw_text": document_payload.strip(),
            "selected_model": active_model,
            "value_threshold": float(cap_limit),
            "blacklist": parse_blacklist(blacklist_input),
            "review_action": None,
            "review_notes": None,
        }
        st.session_state.last_payload = payload
        st.session_state.audit_response = None  # clear previous result

        with st.spinner("Orchestrating the compliance pipeline — this may take 30–90 seconds on first run…"):
            try:
                response = requests.post(API_ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    st.session_state.audit_response = response.json()
                elif response.status_code == 422:
                    st.error(f"Validation error: {response.json().get('detail', response.text)}")
                else:
                    st.error(f"API error {response.status_code}: {response.text}")
            except requests.exceptions.Timeout:
                st.error(
                    "The request timed out. The model may still be loading on first run. "
                    "Wait 30 seconds and try again, or pull the model manually: "
                    "`docker exec -it compliance_ollama ollama pull qwen2.5:7b`"
                )
            except requests.exceptions.ConnectionError:
                st.error(
                    "Cannot reach the backend service. "
                    "Ensure Docker Compose is running and the backend container is healthy."
                )
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
if st.session_state.audit_response:
    data = st.session_state.audit_response
    status = data.get("status", "UNKNOWN")
    vendor = data.get("extracted_vendor", "Unknown")
    country = data.get("extracted_country", "Unknown")
    amount = data.get("extracted_amount", 0.0)
    justification = data.get("justification", "No summary returned.")
    violations = data.get("violations", [])
    next_steps = data.get("next_steps", "No next steps provided.")

    st.divider()
    st.subheader("Audit outcome")

    # Decision banner
    col_decision, col_vendor, col_country, col_amount = st.columns([1.5, 1, 1, 1])
    with col_decision:
        if status == "APPROVED":
            st.success(f"**DECISION: {status}**")
        elif status == "HOLD / MANUAL REVIEW":
            st.warning(f"**DECISION: {status}**")
        else:
            st.error(f"**DECISION: {status}**")
    with col_vendor:
        st.metric("Vendor / Supplier", vendor)
    with col_country:
        st.metric("Jurisdiction", country)
    with col_amount:
        st.metric("Transaction Amount", f"${amount:,.2f}")

    # Compliance findings
    st.subheader("Compliance findings")
    if violations:
        for v in violations:
            st.error(f"⚠️ {v}")
    else:
        st.success("✅ No violations detected. Transaction passed all policy checks.")

    # Executive summary
    st.subheader("Executive summary")
    st.info(justification)

    # Recommended next steps
    st.subheader("Recommended next steps")
    if status == "APPROVED":
        st.success(next_steps)
    elif status == "HOLD / MANUAL REVIEW":
        st.warning(next_steps)
    else:
        st.error(next_steps)

    # Human reviewer override (only for flagged statuses)
    if status in {"HOLD / MANUAL REVIEW", "ESCALATED"}:
        st.divider()
        st.subheader("Human reviewer override")
        with st.form("override_form"):
            override_option = st.selectbox(
                "Reviewer decision",
                ["— select an action —", "Approve transaction", "Reject transaction", "Escalate for deeper review"],
            )
            review_notes = st.text_area("Reviewer notes (optional)", height=100)
            submitted = st.form_submit_button("Submit reviewer decision", type="primary")

        if submitted:
            action_map = {
                "Approve transaction": "approve",
                "Reject transaction": "reject",
                "Escalate for deeper review": "escalate",
            }
            review_action = action_map.get(override_option)
            if not review_action:
                st.warning("Please select a valid action before submitting.")
            elif st.session_state.last_payload is None:
                st.error("Original payload missing. Re-run the audit first.")
            else:
                override_payload = {**st.session_state.last_payload, "review_action": review_action, "review_notes": review_notes}
                with st.spinner("Applying reviewer decision…"):
                    try:
                        r = requests.post(API_ENDPOINT, json=override_payload, timeout=REQUEST_TIMEOUT)
                        if r.status_code == 200:
                            st.session_state.audit_response = r.json()
                            st.success("Reviewer decision applied.")
                            st.rerun()
                        else:
                            st.error(f"Override error {r.status_code}: {r.text}")
                    except requests.exceptions.Timeout:
                        st.error("Override request timed out. Try again.")
                    except Exception as exc:
                        st.error(f"Override failed: {exc}")

    # Raw payload expander (auditor view)
    with st.expander("Raw audit payload (auditor view)"):
        st.json(data)
