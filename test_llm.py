# Live smoke test of the LLM brain against the real provider.
# Needs GROQ_API_KEY (or LLM_API_KEY) in the environment or .env; skips without one.
# Run: python test_llm.py
import agents

CASES = [
    ("C001", "What's my balance?", "banking"),
    ("C002", "Do any of my recent transactions look suspicious?", "fraud"),
    ("C001", "Am I eligible for a 5 lakh personal loan over 3 years?", "credit"),
]

if __name__ == "__main__":
    if agents.client is None:
        print("skipped: no API key set")
        raise SystemExit(0)
    for cid, message, expected in CASES:
        events = list(agents.run(cid, message))
        errors = [e["message"] for e in events if e["type"] == "error"]
        assert not errors, errors
        route = events[0]
        assert route["mode"] == "llm" and expected in route["agents"], route
        assert any(e["type"] == "tool_call" for e in events), "the model called no tool"
        assert events[-1]["type"] == "answer" and events[-1]["text"].strip(), events[-1]
        print(f"ok: {message} -> {route['agents']}")
