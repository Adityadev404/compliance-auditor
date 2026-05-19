"""
Compliance Auditor — MCP Server
================================
Exposes the compliance pipeline as Model Context Protocol tools.
Any MCP-compatible client (Claude Desktop, VS Code Copilot, etc.)
can connect to this server and call these tools directly.

Tools exposed:
  1. check_sanctions         — check a vendor/country against a blacklist
  2. check_capital_controls  — check a transaction amount against a threshold
  3. run_full_audit          — run the complete 4-agent LangGraph pipeline
  4. get_audit_health        — check if the backend API is reachable

Run locally (outside Docker):
  pip install -r requirements.txt
  python server.py

Run inside Docker:
  Automatically handled by docker-compose.yml (listens on port 5000).
"""

import os
import json
from typing import Any
import sys
import logging
import asyncio

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.models import InitializationOptions
from fastapi import FastAPI
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("BACKEND_HOST", "http://localhost:8000")
AUDIT_ENDPOINT = f"{BACKEND_URL}/api/v1/audit"
HEALTH_ENDPOINT = f"{BACKEND_URL}/health"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5:7b")
DEFAULT_THRESHOLD = float(os.getenv("DEFAULT_THRESHOLD", "15000.0"))
DEFAULT_BLACKLIST = os.getenv(
    "DEFAULT_BLACKLIST", "Iran,Russia,North Korea,Belarus,Syria,Cuba"
).split(",")
HTTP_TIMEOUT = 180.0  # seconds — must cover LLM inference time

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("compliance-auditor")


# ---------------------------------------------------------------------------
# Tool: list
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="check_sanctions",
            description=(
                "Check whether a vendor name or country appears on the restricted "
                "sanctions/blacklist. Returns a structured result with any violations found. "
                "Use this for quick entity screening without running the full audit pipeline."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vendor": {
                        "type": "string",
                        "description": "Vendor or supplier name to screen.",
                    },
                    "country": {
                        "type": "string",
                        "description": "Country or jurisdiction to screen.",
                    },
                    "blacklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional custom blacklist. Defaults to: "
                            + ", ".join(DEFAULT_BLACKLIST)
                        ),
                    },
                },
                "required": ["vendor", "country"],
            },
        ),
        types.Tool(
            name="check_capital_controls",
            description=(
                "Check whether a transaction amount triggers a capital controls flag "
                "by comparing it against a configured threshold. "
                "Returns whether the amount is within acceptable limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Transaction amount in USD.",
                    },
                    "threshold": {
                        "type": "number",
                        "description": (
                            f"Capital controls threshold in USD. "
                            f"Defaults to {DEFAULT_THRESHOLD:,.0f}."
                        ),
                    },
                },
                "required": ["amount"],
            },
        ),
        types.Tool(
            name="run_full_audit",
            description=(
                "Run the complete 4-agent stateful compliance audit pipeline against "
                "raw invoice or ledger text. This calls the backend LangGraph graph which: "
                "(1) extracts structured fields with an LLM, "
                "(2) runs deterministic policy checks, "
                "(3) generates an AI executive summary, "
                "(4) routes to human review if needed. "
                "Returns the full audit decision with violations, summary, and next steps."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "raw_text": {
                        "type": "string",
                        "description": "Raw invoice, ledger, or transaction text to audit.",
                    },
                    "model": {
                        "type": "string",
                        "description": f"Ollama model to use. Defaults to {DEFAULT_MODEL}.",
                    },
                    "threshold": {
                        "type": "number",
                        "description": f"Capital controls threshold in USD. Defaults to {DEFAULT_THRESHOLD:,.0f}.",
                    },
                    "blacklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Custom blacklist entries. Defaults to standard restricted list.",
                    },
                    "review_action": {
                        "type": "string",
                        "enum": ["approve", "reject", "escalate"],
                        "description": "Optional human reviewer override action.",
                    },
                    "review_notes": {
                        "type": "string",
                        "description": "Optional reviewer notes for override or escalation.",
                    },
                },
                "required": ["raw_text"],
            },
        ),
        types.Tool(
            name="get_audit_health",
            description=(
                "Check whether the compliance auditor backend API is online and healthy. "
                "Use this before running audits to confirm the service is reachable."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool: call
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:

    # -----------------------------------------------------------------------
    # check_sanctions
    # -----------------------------------------------------------------------
    if name == "check_sanctions":
        vendor = arguments.get("vendor", "").strip()
        country = arguments.get("country", "").strip()
        blacklist = arguments.get("blacklist", DEFAULT_BLACKLIST)

        if not vendor and not country:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "At least one of vendor or country must be provided."})
            )]

        target = f"{country} {vendor}".strip().upper()
        normalized = [e.strip().upper() for e in blacklist if e.strip()]
        violations = [
            f"SANCTION VIOLATION: '{entry}' matched in target '{target}'"
            for entry in normalized
            if entry in target
        ]

        result = {
            "tool": "check_sanctions",
            "vendor": vendor,
            "country": country,
            "blacklist_checked": normalized,
            "violations_found": len(violations),
            "violations": violations,
            "status": "VIOLATION DETECTED" if violations else "CLEAR",
            "summary": (
                f"Found {len(violations)} sanction violation(s) for {vendor or 'N/A'} / {country or 'N/A'}."
                if violations
                else f"No sanction violations found for {vendor or 'N/A'} / {country or 'N/A'}."
            ),
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # -----------------------------------------------------------------------
    # check_capital_controls
    # -----------------------------------------------------------------------
    if name == "check_capital_controls":
        try:
            amount = float(arguments["amount"])
        except (KeyError, ValueError, TypeError):
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "Invalid or missing 'amount'. Must be a number."})
            )]

        threshold = float(arguments.get("threshold", DEFAULT_THRESHOLD))
        flagged = amount >= threshold

        result = {
            "tool": "check_capital_controls",
            "amount_usd": amount,
            "threshold_usd": threshold,
            "flagged": flagged,
            "status": "CAPITAL CONTROLS FLAG" if flagged else "WITHIN LIMITS",
            "summary": (
                f"Transaction of ${amount:,.2f} EXCEEDS the threshold of ${threshold:,.2f} "
                f"by ${amount - threshold:,.2f}. Capital controls review required."
                if flagged
                else f"Transaction of ${amount:,.2f} is within the ${threshold:,.2f} threshold. No flag."
            ),
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # -----------------------------------------------------------------------
    # run_full_audit
    # -----------------------------------------------------------------------
    if name == "run_full_audit":
        raw_text = arguments.get("raw_text", "").strip()
        if not raw_text:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": "raw_text is required and cannot be empty."})
            )]

        payload = {
            "raw_text": raw_text,
            "selected_model": arguments.get("model", DEFAULT_MODEL),
            "value_threshold": float(arguments.get("threshold", DEFAULT_THRESHOLD)),
            "blacklist": arguments.get("blacklist", DEFAULT_BLACKLIST),
            "review_action": arguments.get("review_action"),
            "review_notes": arguments.get("review_notes"),
        }

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.post(AUDIT_ENDPOINT, json=payload)

            if response.status_code == 200:
                data = response.json()
                result = {
                    "tool": "run_full_audit",
                    "status": data.get("status"),
                    "extracted_vendor": data.get("extracted_vendor"),
                    "extracted_country": data.get("extracted_country"),
                    "extracted_amount_usd": data.get("extracted_amount"),
                    "violations": data.get("violations", []),
                    "executive_summary": data.get("justification"),
                    "next_steps": data.get("next_steps"),
                }
                return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": f"Backend returned HTTP {response.status_code}",
                        "detail": response.text,
                    })
                )]

        except httpx.ConnectError:
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "error": "Cannot connect to compliance backend.",
                    "hint": f"Ensure the backend is running at {BACKEND_URL}. Run: docker compose up",
                })
            )]
        except httpx.TimeoutException:
            return [types.TextContent(
                type="text",
                text=json.dumps({
                    "error": "Backend request timed out.",
                    "hint": "The LLM model may still be loading. Wait 30s and retry.",
                })
            )]
        except Exception as exc:
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": f"Unexpected error: {str(exc)}"})
            )]

    # -----------------------------------------------------------------------
    # get_audit_health
    # -----------------------------------------------------------------------
    if name == "get_audit_health":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(HEALTH_ENDPOINT)
            healthy = response.status_code == 200
            result = {
                "tool": "get_audit_health",
                "backend_url": BACKEND_URL,
                "healthy": healthy,
                "status": "ONLINE" if healthy else "DEGRADED",
                "http_status": response.status_code,
                "response": response.json() if healthy else response.text,
            }
        except Exception as exc:
            result = {
                "tool": "get_audit_health",
                "backend_url": BACKEND_URL,
                "healthy": False,
                "status": "OFFLINE",
                "error": str(exc),
                "hint": "Run: docker compose up",
            }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # -----------------------------------------------------------------------
    # Unknown tool
    # -----------------------------------------------------------------------
    return [types.TextContent(
        type="text",
        text=json.dumps({"error": f"Unknown tool: {name}"})
    )]


# ---------------------------------------------------------------------------
# FastAPI Setup - Keep container alive + provide health check
# ---------------------------------------------------------------------------

app = FastAPI(title="Compliance Auditor MCP Server")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "compliance-auditor-mcp",
        "version": "1.0.0",
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "compliance-auditor-mcp",
        "version": "1.0.0",
        "status": "running",
        "endpoints": ["/health", "/tools"],
    }


@app.get("/tools")
async def list_available_tools():
    """List all available MCP tools."""
    tools = await list_tools()
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
            }
            for tool in tools
        ]
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """Main entry point using stdio transport for MCP."""
    import mcp.server.stdio
    
    logger.info("Starting Compliance Auditor MCP Server...")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("MCP Server initialized, running...")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="compliance-auditor",
                server_version="1.0.0",
                capabilities=types.ServerCapabilities(
                    tools=types.ToolsCapability(listChanged=True)
                ),
            ),
        )


if __name__ == "__main__":
    # Check if running with stdio (no TTY) or with HTTP server
    # If there's no TTY, fall back to HTTP mode
    if not sys.stdin.isatty():
        logger.info("No TTY detected, running HTTP server mode on port 5000")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=5000,
            log_level="info",
        )
    else:
        logger.info("TTY detected, running MCP stdio mode")
        asyncio.run(main())