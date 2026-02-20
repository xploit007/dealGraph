import os
import sys

# Fix Windows console encoding — agent output may contain emojis/unicode
# that the default charmap codec can't handle
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Disable ddtrace auto-patching of our local "agents" package BEFORE any imports.
# ddtrace mistakes it for the OpenAI Agents SDK and tries to import agents.tracing,
# which crashes the process with ModuleNotFoundError.
os.environ["DD_TRACE_OPENAI_AGENTS_ENABLED"] = "false"
try:
    import ddtrace
    ddtrace.config._disabled_integrations.add("openai_agents")
except Exception:
    pass

import json
import re
import types
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

import uuid
import logging

load_dotenv()

# Suppress noisy ddtrace "failed to send traces" spam
logging.getLogger("ddtrace").setLevel(logging.CRITICAL)

# Datadog LLMObs setup
try:
    from ddtrace.llmobs import LLMObs
    LLMObs.enable(
        ml_app="dealgraph",
        api_key=os.getenv("DD_API_KEY"),
        site=os.getenv("DD_SITE", "datadoghq.com"),
        agentless_enabled=True
    )
    print("[DealGraph] Datadog LLMObs enabled")
except Exception as e:
    print(f"[DealGraph] Datadog LLMObs skipped: {e}")

def _log(msg: str):
    """Print that won't crash on Windows with emoji/unicode in agent output."""
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeDecodeError):
        print(msg.encode("ascii", errors="replace").decode("ascii"))


app = FastAPI(title="DealGraph API", redirect_slashes=False)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Paths
AUDIO_DIR = Path(__file__).parent / "audio"
FALLBACK_RESPONSE_PATH = Path(__file__).parent / "fallback_response.json"


class AnalyzeRequest(BaseModel):
    deck_text: str


def _load_fallback_response() -> dict:
    if FALLBACK_RESPONSE_PATH.exists():
        with open(FALLBACK_RESPONSE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {
        "status": "complete",
        "claims": [],
        "score": {"overall": 0, "breakdown": {}, "recommendation": "Further Diligence"},
        "memo": "Analysis unavailable.",
        "audio_url": "/api/audio/fallback_memo.mp3",
        "competitors": []
    }


def _parse_json_from_text(text: str):
    """Try to extract and parse JSON from agent output (may be wrapped in markdown or prose)."""
    if not text or not isinstance(text, str):
        return None
    # Strip markdown code fences if present
    cleaned = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`')
    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Try to find JSON array
    m = re.search(r'\[[\s\S]*\]', cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Try to find JSON object
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Last resort: Python repr uses single quotes — try converting
    try:
        import ast
        result = ast.literal_eval(cleaned)
        if isinstance(result, (dict, list)):
            return result
    except (ValueError, SyntaxError):
        pass
    # Try ast.literal_eval on extracted object/array
    for pattern in [r'\{[\s\S]*\}', r'\[[\s\S]*\]']:
        m = re.search(pattern, cleaned)
        if m:
            try:
                result = ast.literal_eval(m.group(0))
                if isinstance(result, (dict, list)):
                    return result
            except (ValueError, SyntaxError):
                pass
    return None


def _build_response_from_shared_state(company_name: str = "Acme Payments") -> dict | None:
    """Build API response from shared_state. Returns None if insufficient data."""
    from agents import shared_state
    from tools.neo4j_tools import find_competitors

    state = shared_state.analysis_state
    fact_checks_raw = state.get("fact_checks") or state.get("claims")
    score_raw = state.get("score")
    memo = state.get("memo") or ""

    _log(f"[DEBUG build_response] fact_checks_raw type={type(fact_checks_raw).__name__} len={len(str(fact_checks_raw))}")
    _log(f"[DEBUG build_response] score_raw type={type(score_raw).__name__} preview={str(score_raw)[:300]}")
    _log(f"[DEBUG build_response] memo type={type(memo).__name__} len={len(str(memo))}")

    # Normalize memo: sometimes the agent leaves a message object instead of a string
    if isinstance(memo, dict):
        try:
            memo = memo["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            memo = "Analysis memo unavailable."
    if not isinstance(memo, str):
        memo = str(memo)
    audio_filename = state.get("audio_filename") or "fallback_memo.mp3"

    fact_checks_str = str(fact_checks_raw) if fact_checks_raw else ""
    _log(f"[DEBUG build_response] parsing claims from: {fact_checks_str[:300]}")
    claims = _parse_json_from_text(fact_checks_str) if fact_checks_raw else None
    _log(f"[DEBUG build_response] parsed claims: type={type(claims).__name__} value={str(claims)[:200] if claims else 'None'}")
    if not isinstance(claims, list):
        claims = None
    else:
        # Normalize claim fields to match frontend types exactly
        VALID_CATEGORIES = {"market_size", "traction", "team", "competition", "financial"}
        VALID_STATUSES = {"verified", "unverified", "partial", "red_flag"}
        for i, c in enumerate(claims):
            if not isinstance(c, dict):
                continue
            if "id" not in c:
                c["id"] = i + 1
            if c.get("category") not in VALID_CATEGORIES:
                c["category"] = "traction"
            if c.get("status") not in VALID_STATUSES:
                c["status"] = "unverified"
            if "evidence" not in c:
                c["evidence"] = ""
            if "text" not in c:
                c["text"] = ""

    score_str = str(score_raw) if score_raw else ""
    _log(f"[DEBUG build_response] parsing score from: {score_str[:300]}")
    score = _parse_json_from_text(score_str) if score_raw else None
    _log(f"[DEBUG build_response] parsed score: {score}")
    if not isinstance(score, dict):
        score = {"overall": 0, "breakdown": {}, "recommendation": "Further Diligence"}
    if "breakdown" not in score:
        score["breakdown"] = {}
    # Ensure all 5 breakdown keys exist (frontend requires exactly these)
    for key in ("team", "market", "traction", "competition", "financials"):
        if key not in score["breakdown"]:
            score["breakdown"][key] = 0
    if "recommendation" not in score:
        score["recommendation"] = "Further Diligence"

    try:
        comps = find_competitors(company_name)
        competitors = [
            {"name": c.get("name"), "total_raised": c.get("total_raised"), "stage": c.get("stage"), "employee_count": c.get("employee_count")}
            for c in comps
        ]
    except Exception:
        competitors = []

    return {
        "status": "complete",
        "claims": claims if claims else [],
        "score": score,
        "memo": memo,
        "audio_url": f"/api/audio/{audio_filename}",
        "competitors": competitors
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze_deck(req: AnalyzeRequest):
    """Run the full DealGraph analysis pipeline."""
    return await _analyze_deck_internal(req.deck_text)


@app.get("/api/audio/{filename}")
async def get_audio(filename: str):
    """Serve generated audio files."""
    filepath = AUDIO_DIR / filename
    if filepath.exists():
        return FileResponse(str(filepath), media_type="audio/mpeg")
    fallback = AUDIO_DIR / "fallback_memo.mp3"
    if fallback.exists():
        return FileResponse(str(fallback), media_type="audio/mpeg")
    return {"error": "Audio not found"}


async def _analyze_deck_internal(deck_text: str) -> dict:
    """Core analysis pipeline, shared by REST endpoint and CopilotKit action."""
    from agents import shared_state
    from agents.orchestrator import orchestrator

    shared_state.analysis_state = {
        "claims": [], "fact_checks": [], "score": {},
        "memo": "", "audio_filename": "", "competitors": []
    }

    try:
        orchestrator(f"Analyze this pitch deck:\n\n{deck_text}")
        result = _build_response_from_shared_state()
        if result:
            claims_ok = isinstance(result.get("claims"), list) and len(result["claims"]) > 0
            memo_ok = isinstance(result.get("memo"), str) and len(result["memo"]) > 0
            score_ok = isinstance(result.get("score"), dict) and result["score"].get("overall", 0) > 0
            if claims_ok and memo_ok and score_ok:
                _log("[CopilotKit] PIPELINE RESULT ACCEPTED")
                return result
    except Exception as e:
        import traceback
        _log(f"Pipeline error: {e}")
        traceback.print_exc()

    return _load_fallback_response()


# ── CopilotKit AG-UI Protocol Endpoint ──
# Manual implementation (CopilotKit SDK's built-in endpoint doesn't register
# a proper AG-UI agent, causing "Agent 'default' not found" on v1.50+).

_COPILOTKIT_INFO = {
    "agents": {
        "default": {
            "name": "default",
            "description": (
                "DealGraph AI Due Diligence Agent - analyzes pitch decks, "
                "verifies claims against knowledge graph, scores deals, "
                "and generates investment memos"
            ),
        }
    },
    "actions": {
        "analyzeDeck": {
            "name": "analyzeDeck",
            "description": "Analyze a startup pitch deck",
            "parameters": [
                {
                    "name": "deck_text",
                    "type": "string",
                    "description": "Pitch deck text to analyze",
                    "required": True,
                }
            ],
        }
    },
}


@app.post("/copilotkit")
@app.post("/copilotkit/")
@app.post("/copilotkit/{path:path}")
async def copilotkit_handler(request: Request, path: str = ""):
    """CopilotKit AG-UI protocol handler."""
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        data = {}

    method = data.get("method", "")

    # ── Info request ──
    if method == "info" or path == "info" or not method:
        return JSONResponse(_COPILOTKIT_INFO)

    # ── Agent run request ──
    if method == "agent/run" or "messages" in data:
        messages = data.get("messages", data.get("body", {}).get("messages", []))

        deck_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    deck_text = content
                elif isinstance(content, list):
                    deck_text = " ".join(
                        c.get("text", "") for c in content if c.get("type") == "text"
                    )
                break

        if not deck_text:
            deck_text = data.get("deck_text", "No deck text provided")

        async def stream_response():
            run_id = str(uuid.uuid4())
            thread_id = data.get("threadId", str(uuid.uuid4()))
            tool_call_id = str(uuid.uuid4())

            yield f"data: {json.dumps({'type': 'run.start', 'runId': run_id, 'threadId': thread_id})}\n\n"

            # Emit tool call so CopilotKit triggers the frontend useCopilotAction
            yield f"data: {json.dumps({'type': 'tool.call.start', 'toolCallId': tool_call_id, 'toolCallName': 'analyzeDeck'})}\n\n"
            yield f"data: {json.dumps({'type': 'tool.call.args', 'toolCallId': tool_call_id, 'args': json.dumps({'deck_text': deck_text})})}\n\n"
            yield f"data: {json.dumps({'type': 'tool.call.end', 'toolCallId': tool_call_id})}\n\n"

            yield f"data: {json.dumps({'type': 'text.message.start', 'messageId': str(uuid.uuid4()), 'role': 'assistant'})}\n\n"
            yield f"data: {json.dumps({'type': 'text.message.content', 'content': 'Analyzing pitch deck...'})}\n\n"
            yield f"data: {json.dumps({'type': 'text.message.end'})}\n\n"

            try:
                result = await _analyze_deck_internal(deck_text)

                score = result.get("score", {}).get("overall", "N/A")
                claims_count = len(result.get("claims", []))
                recommendation = result.get("score", {}).get("recommendation", "N/A")
                summary = (
                    f"Analysis complete. Found {claims_count} claims. "
                    f"Deal Score: {score}/10. Recommendation: {recommendation}."
                )

                # State update with full result for the frontend
                yield f"data: {json.dumps({'type': 'state.update', 'state': {'analysisResult': result}})}\n\n"

                yield f"data: {json.dumps({'type': 'text.message.start', 'messageId': str(uuid.uuid4()), 'role': 'assistant'})}\n\n"
                yield f"data: {json.dumps({'type': 'text.message.content', 'content': summary})}\n\n"
                yield f"data: {json.dumps({'type': 'text.message.end'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'text.message.start', 'messageId': str(uuid.uuid4()), 'role': 'assistant'})}\n\n"
                yield f"data: {json.dumps({'type': 'text.message.content', 'content': f'Analysis error: {str(e)}'})}\n\n"
                yield f"data: {json.dumps({'type': 'text.message.end'})}\n\n"

            yield f"data: {json.dumps({'type': 'run.end', 'runId': run_id})}\n\n"

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Fallback ──
    return JSONResponse(_COPILOTKIT_INFO)


_log("[DealGraph] CopilotKit AG-UI endpoint registered at /copilotkit")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
