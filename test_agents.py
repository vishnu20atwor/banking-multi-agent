# Checks the demo use cases end to end on the rule-based brain (no API key needed).
# Run: python test_agents.py
import agents

agents.client = None


def ask(cid, message):
    events = list(agents.run(cid, message))
    route = next(e for e in events if e["type"] == "route")
    answer = next(e for e in events if e["type"] == "answer")
    tools = [e["tool"] for e in events if e["type"] == "tool_call"]
    return route["agents"], tools, answer["text"], events


def balance(cid):
    return agents.bank.get_accounts(cid)["accounts"][0]["balance"]


def test_balance_and_transactions():
    route, tools, text, _ = ask("C001", "What's my balance and my last 5 transactions?")
    assert route == ["banking"] and tools == ["get_accounts", "get_transactions"]
    assert "ACC1001" in text and text.count("\n- ") >= 6


def test_suspicious_transactions():
    route, tools, text, _ = ask("C002", "Do any of my recent transactions look suspicious?")
    assert route == ["fraud"] and "score_transaction" in tools
    assert "TXN2007" in text and "high" in text
    assert "none look suspicious" in ask("C001", "Do any of my recent transactions look suspicious?")[2]


def test_loan_eligibility():
    route, tools, text, _ = ask("C001", "Am I eligible for a 5 lakh personal loan over 3 years?")
    assert route == ["credit"] and tools == ["get_credit_report", "check_loan_eligibility"]
    assert "approved" in text and "500,000.00" in text and "36 months" in text
    assert "declined" in ask("C003", "Can I borrow 50000 for 12 months?")[2]


def test_transfer_needs_approval():
    before = balance("C001")
    route, tools, _, events = ask("C001", "Transfer 5000 from ACC1001 to ACC2001")
    assert route == ["fraud", "banking"] and tools == ["screen_transfer", "request_transfer"]
    approval = next(e for e in events if e["type"] == "approval")
    assert balance("C001") == before, "moved money before approval"
    assert agents.approve(approval["id"], True)["ok"]
    assert balance("C001") == before - 5000
    assert not agents.approve(approval["id"], True)["ok"], "approval must be single-use"


def test_rejected_transfer_moves_nothing():
    before = balance("C001")
    events = ask("C001", "Transfer 1000 from ACC1001 to ACC2001")[3]
    assert agents.approve(next(e for e in events if e["type"] == "approval")["id"], False)["ok"]
    assert balance("C001") == before


def test_risky_transfer_is_blocked():
    _, _, text, events = ask("C001", "Send 2 lakh from ACC1001 to ACC9999")
    assert "blocked" in text and not any(e["type"] == "approval" for e in events)


def test_block_card_after_fraud():
    route, tools, text, _ = ask("C001", "Block my credit card, I think it was used fraudulently")
    assert route == ["fraud", "banking"] and tools[-2:] == ["get_cards", "block_card"]
    assert "blocked" in text
    assert agents.bank.get_cards("C001")["cards"][0]["status"] == "blocked"


def test_tools_are_scoped_per_agent():
    events = list(agents._run_tool("credit", "block_card", {"card_id": "CARD201"}, "C002"))
    assert "error" in events[1]["result"]
    assert agents.bank.get_cards("C002")["cards"][0]["status"] == "active"


def test_out_of_scope():
    route, _, text, _ = ask("C001", "tell me a joke")
    assert route == [] and text == agents.HELP


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
