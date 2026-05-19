# Autonomous Financial Risk & Compliance Auditor

## Project Summary

A stateful, on-premise AI compliance pipeline built with **LangGraph**, **FastAPI**, **Ollama**, and **Streamlit**. Designed for compliance reviewers who need plain-language decisions — not raw JSON.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Streamlit Dashboard  (port 8501)                        │
│  • Invoice ingestion  • Results display  • Override UI   │
└───────────────────┬─────────────────────────────────────┘
                    │ HTTP POST /api/v1/audit
┌───────────────────▼─────────────────────────────────────┐
│  FastAPI + Gunicorn  (port 8000)                         │
│  • Request validation (Pydantic)                         │
│  • MaxRequestSize middleware                             │
│  • CORS middleware                                       │
└───────────────────┬─────────────────────────────────────┘
                    │ LangGraph state machine
┌───────────────────▼─────────────────────────────────────┐
│  Compliance Graph (4 agents)                             │
│  1. Data Structuring Agent  — LLM + regex fallback       │
│  2. Policy Auditor          — deterministic rule engine  │
│  3. Risk Synthesizer        — LLM executive summary      │
│  4. Human Review Coord.     — override / escalate node   │
└───────────────────┬─────────────────────────────────────┘
                    │ ollama-python SDK
┌───────────────────▼─────────────────────────────────────┐
│  Ollama  (port 11434)  — local model inference           │
│  Default model: qwen2.5:7b  (~4 GB download on first run)│
└─────────────────────────────────────────────────────────┘
```

### Services (Docker Compose)

| Service | Container | Port | Role |
|---|---|---|---|
| `ollama` | `compliance_ollama` | 11434 | Local LLM inference engine |
| `ollama-init` | `compliance_ollama_init` | — | One-shot model downloader |
| `backend` | `compliance_backend` | 8000 | FastAPI audit API |
| `frontend` | `compliance_frontend` | 8501 | Streamlit reviewer dashboard |

---

## Quick Start

### Prerequisites
- Docker Desktop with WSL 2 backend enabled (Windows) or Docker Engine (Linux/macOS)
- ~6 GB free disk space (Ollama image + qwen2.5:7b model weights)
- Internet access on first run (model download)

### 1 — Build and start all services

```bash
cd compliance-auditor

# First run: builds images and pulls the qwen2.5:7b model automatically
docker compose up --build
```

> **Note:** The `ollama-init` service pulls `qwen2.5:7b` (~4 GB) automatically on first run.  
> The backend waits for this to complete before starting. Allow 5–15 minutes depending on your connection.

### 2 — Open the dashboard

Once all containers are healthy:

| Interface | URL |
|---|---|
| Streamlit dashboard | http://localhost:8501 |
| FastAPI docs | http://localhost:8000/docs |
| API root | http://localhost:8000 |

### 3 — Stop services

```bash
docker compose down
```

To also remove downloaded model weights (frees ~4 GB):

```bash
docker compose down -v
```

---

## Build issues on Windows / WSL 2

If `apt-get` fails during build (DNS / network errors inside containers), build the images using the host network:

```powershell
docker build -t compliance-auditor-backend --network host ./backend
docker build -t compliance-auditor-frontend --network host ./frontend
docker compose up
```

---

## Environment Variables

Copy `.env.example` to `.env` to customise settings:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://ollama:11434` | Ollama service URL |
| `OLLAMA_TIMEOUT` | `120` | LLM response timeout (seconds) |
| `BACKEND_HOST` | `http://backend:8000` | Backend URL (used by frontend) |
| `DEBUG` | `false` | Enable FastAPI debug mode |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## Human-in-the-loop Review

When a transaction is flagged as **HOLD / MANUAL REVIEW** or **ESCALATED**, the dashboard displays an override panel with three options:

- **Approve** — reviewer confirms the transaction is safe to process
- **Reject** — reviewer blocks the transaction
- **Escalate** — routes to senior compliance with mandatory notes

---

## Compliance Decision Logic

| Condition | Decision |
|---|---|
| No violations | `APPROVED` |
| Capital controls flag only | `HOLD / MANUAL REVIEW` |
| Sanction violation (blacklist match) | `REJECTED` |
| Reviewer override: approve | `APPROVED` |
| Reviewer override: reject | `REJECTED` |
| Reviewer override: escalate | `ESCALATED` |

---

## Production Notes

- Gunicorn runs with `--timeout 180` to accommodate LLM inference latency
- Model weights are persisted in the `ollama_models` Docker volume between restarts
- All containers have healthchecks; the frontend waits for the backend to be healthy before starting
- The backend enforces a 10 MB request size limit and a 50,000 character text limit
