from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from agents.claim_extractor import claim_extractor
from agents.fact_checker import fact_checker
from agents.deal_scorer import deal_scorer
from agents.memo_writer import memo_writer
from agents import shared_state


@tool
def extract_claims(deck_text: str) -> str:
    """Extract all verifiable claims from pitch deck text."""
    response = claim_extractor(f"Extract all verifiable claims from this pitch deck:\n\n{deck_text}")
    out = getattr(response, "content", None) or getattr(response, "message", None) or str(response)
    shared_state.analysis_state["claims"] = out
    return str(out)


@tool
def fact_check_claims(claims_json: str) -> str:
    """Verify each claim against the knowledge graph."""
    response = fact_checker(f"Verify these claims against the knowledge graph:\n\n{claims_json}")
    out = getattr(response, "content", None) or getattr(response, "message", None) or str(response)
    shared_state.analysis_state["fact_checks"] = out
    return str(out)


@tool
def score_deal(fact_check_results: str) -> str:
    """Score the deal based on fact-check results."""
    response = deal_scorer(f"Score this deal based on these fact-check results:\n\n{fact_check_results}")
    out = getattr(response, "content", None) or getattr(response, "message", None) or str(response)
    shared_state.analysis_state["score"] = out
    return str(out)


@tool
def write_memo(score_and_facts: str) -> str:
    """Write the investment memo and generate voice briefing."""
    response = memo_writer(f"Write the deal memo and voice briefing:\n\n{score_and_facts}")
    out = getattr(response, "content", None) or getattr(response, "message", None) or str(response)
    shared_state.analysis_state["memo"] = out
    return str(out)


ORCHESTRATOR_PROMPT = """You are DealGraph, an AI due diligence copilot for investors.

When given pitch deck text, execute these steps IN ORDER:
1. Use extract_claims to pull out every verifiable claim
2. Use fact_check_claims to verify each claim against the knowledge graph
3. Use score_deal to compute the investment score
4. Use write_memo to generate the deal memo and voice briefing

After each step, briefly note what was found before moving to the next step.
Be thorough but concise. Investors value precision over verbosity."""

orchestrator = Agent(
    model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0"),
    system_prompt=ORCHESTRATOR_PROMPT,
    tools=[extract_claims, fact_check_claims, score_deal, write_memo]
)
