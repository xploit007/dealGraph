import os
import json
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Datadog setup (must be before importing agents that use LLM)
try:
    from ddtrace.llmobs import LLMObs
    LLMObs.enable(
        ml_app="dealgraph",
        api_key=os.getenv("DD_API_KEY"),
        site=os.getenv("DD_SITE", "datadoghq.com"),
        agentless_enabled=True
    )
except Exception:
    pass

app = FastAPI(title="DealGraph API")
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
    # Try direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Try to find JSON array
    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Try to find JSON object
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
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
    # Normalize memo: sometimes the agent leaves a message object instead of a string
    if isinstance(memo, dict):
        try:
            memo = memo["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            memo = "Analysis memo unavailable."
    if not isinstance(memo, str):
        memo = str(memo)
    audio_filename = state.get("audio_filename") or "fallback_memo.mp3"

    claims = _parse_json_from_text(str(fact_checks_raw)) if fact_checks_raw else None
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

    score = _parse_json_from_text(str(score_raw)) if score_raw else None
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
    from agents import shared_state
    from agents.orchestrator import orchestrator

    # Reset shared state
    shared_state.analysis_state = {
        "claims": [],
        "fact_checks": [],
        "score": {},
        "memo": "",
        "audio_filename": "",
        "competitors": []
    }

    try:
        raw_result = orchestrator(f"Analyze this pitch deck:\n\n{req.deck_text}")
        # Build response from shared state (agents write into it)
        result = _build_response_from_shared_state()
        if result:
            claims_ok = isinstance(result.get("claims"), list) and len(result["claims"]) > 0
            memo_ok = isinstance(result.get("memo"), str) and len(result["memo"]) > 0
            score_ok = isinstance(result.get("score"), dict) and result["score"].get("overall", 0) > 0
            if claims_ok and memo_ok and score_ok:
                return result
    except Exception as e:
        import traceback
        print(f"Pipeline error: {e}")
        traceback.print_exc()

    # Fallback response
    fallback = _load_fallback_response()
    try:
        from tools.neo4j_tools import find_competitors
        comps = find_competitors("Acme Payments")
        fallback["competitors"] = [
            {"name": c.get("name"), "total_raised": c.get("total_raised"), "stage": c.get("stage"), "employee_count": c.get("employee_count")}
            for c in comps
        ]
    except Exception:
        pass
    return fallback


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


@app.post("/copilotkit")
async def copilotkit_endpoint(request: Request):
    body = await request.json()
    # Return a minimal valid response so CopilotKit frontend doesn't crash
    # We will implement full AG-UI protocol during the hackathon
    return {"status": "ok", "message": "CopilotKit endpoint ready"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
