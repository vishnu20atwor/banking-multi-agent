# Supervisor + specialist agents. Two interchangeable brains share one pipeline:
#   llm   - an OpenAI-compatible chat model (Groq by default) routes and calls tools
#   rules - keyword routing + fixed tool sequences, used when no API key is set
import json
import os
import re
import uuid
from pathlib import Path

import bank

_env = Path(__file__).with_name(".env")
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _k, _sep, _v = _line.partition("=")
        if _sep and not _k.strip().startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip())

# `or` rather than a getenv default: CI and .env can supply these as empty strings
MODEL = os.getenv("LLM_MODEL") or "llama-3.3-70b-versatile"
BASE_URL = os.getenv("LLM_BASE_URL") or "https://api.groq.com/openai/v1"
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")

client = None
if API_KEY:
    from openai import OpenAI

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)

# ponytail: in-memory and single-process; move to a store with TTL if this ever runs multi-worker
PENDING = {}


def inr(x):
    return f"₹{x:,.2f}"


# --- tools -----------------------------------------------------------------

def request_transfer(customer_id, from_account, to_account, amount):
    """Never moves money: screens the transfer, then parks it for human approval."""
    accounts = bank.get_accounts(customer_id)
    if "error" in accounts:
        return accounts
    risk = bank.screen_transfer(customer_id, amount, to_account)
    if "error" in risk:
        return risk
    amount = float(amount)
    source = next((a for a in accounts["accounts"] if a["account_id"] == from_account), None)
    if source is None:
        return {"error": f"{from_account} is not one of this customer's accounts"}
    if from_account == to_account:
        return {"error": "Source and destination accounts are the same"}
    if source["balance"] < amount:
        return {"error": f"Insufficient funds: {from_account} holds {inr(source['balance'])}"}
    if risk["level"] == "high":
        return {"status": "blocked", "reason": "High fraud risk - held for manual review", "risk": risk}
    approval_id = uuid.uuid4().hex[:12]
    args = {"from_account": from_account, "to_account": to_account, "amount": amount}
    PENDING[approval_id] = {"customer_id": customer_id, "args": args, "risk": risk}
    return {"status": "pending_approval", "approval_id": approval_id, "risk": risk}


def approve(approval_id, approved):
    pending = PENDING.pop(approval_id, None)
    if pending is None:
        return {"ok": False, "message": "This approval request has expired or was already handled.", "result": None}
    if not approved:
        return {"ok": True, "message": "Transfer rejected. No money was moved.", "result": None}
    result = bank.execute_transfer(pending["customer_id"], **pending["args"])
    if "error" in result:
        return {"ok": False, "message": result["error"], "result": result}
    message = f"Transfer completed. Reference {result['reference']}. New balance {inr(result['new_balance'])}."
    return {"ok": True, "message": message, "result": result}


_S, _N, _I = {"type": "string"}, {"type": "number"}, {"type": "integer"}

# customer_id is never a model-visible parameter: the server injects the
# authenticated customer, so a prompt cannot reach another customer's data.
# name: (function, description, parameters, required)
TOOLS = {
    "get_profile": (bank.get_profile, "Customer profile, income and KYC status.", {}, []),
    "get_accounts": (bank.get_accounts, "The customer's accounts with current balances.", {}, []),
    "get_transactions": (bank.get_transactions, "Recent transactions, newest first.", {"limit": _I}, []),
    "get_cards": (bank.get_cards, "The customer's cards and their status.", {}, []),
    "block_card": (bank.block_card, "Block one of the customer's cards.", {"card_id": _S}, ["card_id"]),
    "score_transaction": (bank.score_transaction, "Fraud risk score for one past transaction.",
                          {"txn_id": _S}, ["txn_id"]),
    "screen_transfer": (bank.screen_transfer, "Fraud risk score for a proposed outgoing transfer.",
                        {"amount": _N, "to_account": _S}, ["amount", "to_account"]),
    "request_transfer": (request_transfer,
                         "Request a transfer. It is screened and then waits for the customer's approval; "
                         "it does not move money.",
                         {"from_account": _S, "to_account": _S, "amount": _N},
                         ["from_account", "to_account", "amount"]),
    "get_credit_report": (bank.get_credit_report, "Bureau score and repayment history.", {}, []),
    "get_loans": (bank.get_loans, "The customer's existing loans.", {}, []),
    "check_loan_eligibility": (bank.check_loan_eligibility,
                               "Eligibility decision, interest rate and EMI for a new loan.",
                               {"amount": _N, "tenure_months": _I, "loan_type": _S},
                               ["amount", "tenure_months"]),
}

AGENTS = {
    "banking": {
        "label": "Banking",
        "role": "You are the Banking agent. You handle profile, balances, transactions, cards and transfers.",
        "tools": ["get_profile", "get_accounts", "get_transactions", "get_cards", "block_card", "request_transfer"],
    },
    "fraud": {
        "label": "Fraud",
        "role": "You are the Risk and Fraud agent. You score transactions and screen transfers for fraud risk, "
                "and explain which signals fired.",
        "tools": ["get_transactions", "score_transaction", "screen_transfer"],
    },
    "credit": {
        "label": "Credit",
        "role": "You are the Credit agent. You assess creditworthiness and loan eligibility, quoting the rate, "
                "EMI and the reasons behind the decision.",
        "tools": ["get_profile", "get_credit_report", "get_loans", "check_loan_eligibility"],
    },
}

AGENT_RULES = (
    " You act for one already-authenticated customer; tools are scoped to them, so never ask for a customer id."
    " Use only facts returned by tools and never invent balances, ids or scores. Amounts are in INR."
    " request_transfer never moves money: it returns pending_approval (tell the customer to approve or reject"
    " it on screen) or blocked (explain why). Do only your task and reply with a short factual summary."
)

SUPERVISOR = """You are the Supervisor of a retail bank's multi-agent assistant. Decide which specialist
agents handle the customer's latest message.

Agents:
- banking: profile, account balances, transaction history, cards (list or block), money transfers.
- fraud: scores past transactions and screens proposed transfers for fraud risk.
- credit: credit report, existing loans, loan eligibility and EMI.

Rules:
- Use the fewest agents that fully answer the request. Agents run in the order you list them and each one
  sees the findings of the agents before it.
- A money transfer always goes to fraud first (screen it), then banking (request it).
- A report of card misuse goes to fraud (review recent transactions), then banking (block the card) if the
  customer asked for a block.
- For a greeting or anything outside banking, list no agents and write a short reply yourself.

Answer with JSON only:
{"reason": "one sentence", "agents": [{"name": "banking", "task": "what this agent must do"}], "reply": "only when agents is empty"}"""


def _run_tool(agent, name, args, cid):
    yield {"type": "tool_call", "agent": agent, "tool": name, "args": args}
    if name not in AGENTS[agent]["tools"]:
        result = {"error": f"{name} is not a tool available to the {agent} agent"}
    else:
        try:
            result = TOOLS[name][0](cid, **args)
        except (TypeError, ValueError) as e:
            result = {"error": f"Bad arguments for {name}: {e}"}
    yield {"type": "tool_result", "agent": agent, "tool": name, "result": result}
    if result.get("approval_id"):
        pending = PENDING[result["approval_id"]]
        yield {"type": "approval", "id": result["approval_id"],
               "action": {"tool": "execute_transfer", "args": pending["args"]}, "risk": pending["risk"]}
    return result


# --- llm brain -------------------------------------------------------------

def _chat(messages, **kw):
    return client.chat.completions.create(model=MODEL, messages=messages, temperature=0.2, **kw).choices[0].message


def _route_llm(message, history):
    messages = [{"role": "system", "content": SUPERVISOR}, *history, {"role": "user", "content": message}]
    data = json.loads(_chat(messages, response_format={"type": "json_object"}).content)
    plan = {}
    for a in data.get("agents") or []:
        if isinstance(a, dict) and a.get("name") in AGENTS:
            plan.setdefault(a["name"], str(a.get("task") or message))
    return list(plan.items()), str(data.get("reason") or ""), str(data.get("reply") or HELP)


def _schema(name):
    _, desc, props, required = TOOLS[name]
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def _agent_llm(name, task, cid, message, findings):
    context = "\n".join(f"{AGENTS[a]['label']} agent found: {s}" for a, s in findings.items()) or "none"
    messages = [
        {"role": "system", "content": AGENTS[name]["role"] + AGENT_RULES},
        {"role": "user", "content": f"Customer message: {message}\nYour task: {task}\nEarlier findings: {context}"},
    ]
    tools = [_schema(t) for t in AGENTS[name]["tools"]]
    for _ in range(5):
        reply = _chat(messages, tools=tools)
        if not reply.tool_calls:
            return reply.content or ""
        messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in reply.tool_calls]})
        for c in reply.tool_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if c.function.name in TOOLS:
                args = {k: v for k, v in args.items() if k in TOOLS[c.function.name][2]}
            result = yield from _run_tool(name, c.function.name, args, cid)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
    return "I could not finish this task within the step limit."


def _synthesise(message, findings):
    notes = "\n".join(f"{AGENTS[a]['label']} agent: {s}" for a, s in findings.items())
    return _chat([
        {"role": "system", "content": "You are the Supervisor. Merge the specialist findings into one clear reply "
                                      "to the customer. Add no facts of your own. If a transfer is awaiting "
                                      "approval, say so. Be concise."},
        {"role": "user", "content": f"Customer message: {message}\n\n{notes}"},
    ]).content or notes


# --- rules brain -----------------------------------------------------------

HELP = ("I can help with balances and transactions, checking transactions for fraud, loan eligibility, "
        "transfers and blocking a card. What would you like to do?")

ACC_RE = re.compile(r"\b(?:ACC\d+|BENE-[\w-]+)", re.I)
TRANSFER_RE = re.compile(r"\b(transfer|send|pay|remit)\b", re.I)
BLOCK_RE = re.compile(r"\b(block|freeze)\b", re.I)


def _amount(text):
    t = re.sub(r"\b(?:TXN|CARD)\d+\b", " ", ACC_RE.sub(" ", text), flags=re.I)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|k)\b", t, re.I)
    if m:
        return float(m.group(1).replace(",", "")) * {"l": 1e5, "c": 1e7, "k": 1e3}[m.group(2)[0].lower()]
    t = re.sub(r"\d+\s*(?:years?|yrs?|months?|transactions?|txns?)\b|\b(?:last|recent|past)\s+\d+", " ", t, flags=re.I)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", t)
    return float(m.group().replace(",", "")) if m else None


def _tenure_months(text):
    m = re.search(r"(\d+)\s*(years?|yrs?|months?)", text, re.I)
    if not m:
        return 36
    return int(m.group(1)) * (1 if m.group(2).lower().startswith("m") else 12)


def _transfer_parties(text):
    def after(word):
        m = re.search(rf"\b{word}\s+({ACC_RE.pattern})", text, re.I)
        return m.group(1).upper() if m else None

    found = [a.upper() for a in ACC_RE.findall(text)]
    src, dst = after("from"), after("to")
    if dst is None:
        dst = next((a for a in reversed(found) if a != src), None)
    return src, dst


def _route_rules(message):
    m = message.lower()
    fraud = re.search(r"suspicious|fraud|unauthori[sz]ed|scam|stolen|hack|didn'?t make|not me|\brisk", m)
    credit = re.search(r"\bloans?\b|\bemi\b|credit (score|report)|eligib|borrow|cibil", m)
    banking = re.search(r"balance|transaction|statement|account|\bcards?\b|profile|kyc|spent|spend", m)
    if TRANSFER_RE.search(m):
        return ([("fraud", "Screen the proposed transfer for fraud risk"),
                 ("banking", "Request the transfer for customer approval")],
                "A money transfer is screened by Fraud before Banking requests it.", "")
    plan = []
    if fraud:
        plan.append(("fraud", "Review recent transactions for fraud signals"))
    if BLOCK_RE.search(m) or (banking and not fraud):
        plan.append(("banking", "Block the card" if BLOCK_RE.search(m) else "Fetch the requested account information"))
    if credit:
        plan.append(("credit", "Assess credit standing and loan eligibility"))
    if not plan:
        return [], "No banking intent recognised.", HELP
    return plan, "Matched keywords for: " + ", ".join(AGENTS[a]["label"] for a, _ in plan) + ".", ""


def _risk_line(r):
    return f"**{r['level']}** risk (score {r['risk_score']}): " + "; ".join(r["reasons"])


def _rules_banking(cid, msg):
    m = msg.lower()
    if TRANSFER_RE.search(m):
        amount, (src, dst) = _amount(msg), _transfer_parties(msg)
        if amount is None or dst is None:
            return 'To set up a transfer I need an amount and a destination, e.g. "Transfer 5000 from ACC1001 to ACC2001".'
        if src is None:
            accounts = yield from _run_tool("banking", "get_accounts", {}, cid)
            if "error" in accounts:
                return accounts["error"]
            src = accounts["accounts"][0]["account_id"]
        r = yield from _run_tool("banking", "request_transfer",
                                 {"from_account": src, "to_account": dst, "amount": amount}, cid)
        if "error" in r:
            return f"I couldn't set up the transfer: {r['error']}."
        if r["status"] == "blocked":
            return f"The transfer of {inr(amount)} to {dst} was **blocked** and held for manual review. " \
                   + _risk_line(r["risk"])
        return f"A transfer of {inr(amount)} from {src} to {dst} is ready. **Please approve or reject it on the card above.**"
    if BLOCK_RE.search(m):
        cards = yield from _run_tool("banking", "get_cards", {}, cid)
        if "error" in cards:
            return cards["error"]
        active = [c for c in cards["cards"] if c["status"] == "active"]
        named = re.search(r"\bCARD\d+\b", msg, re.I)
        if named:
            active = [c for c in cards["cards"] if c["card_id"] == named.group().upper()]
        elif len(active) > 1:
            active = [c for c in active if c["type"] in m] or active
        if len(active) != 1:
            if not active:
                return "There is no matching active card to block."
            return "Which card should I block? " + ", ".join(
                f"{c['card_id']} ({c['type']} ending {c['last4']})" for c in active)
        r = yield from _run_tool("banking", "block_card", {"card_id": active[0]["card_id"]}, cid)
        if "error" in r:
            return r["error"]
        return f"Your {active[0]['type']} card ending {r['last4']} ({r['card_id']}) is now **blocked**."
    out = []
    if re.search(r"profile|kyc", m):
        p = yield from _run_tool("banking", "get_profile", {}, cid)
        if "error" in p:
            return p["error"]
        out.append(f"{p['name']} · {p['segment']} · {p['city']} · KYC {p['kyc_status']}")
    wants_txns = re.search(r"transaction|statement|spent|spend", m)
    if re.search(r"\bcards?\b", m):
        cards = yield from _run_tool("banking", "get_cards", {}, cid)
        if "error" in cards:
            return cards["error"]
        out += [f"- {c['card_id']} · {c['type']} card ending {c['last4']} · {c['status']}" for c in cards["cards"]]
    if re.search(r"balance|account", m) or not (out or wants_txns):
        accounts = yield from _run_tool("banking", "get_accounts", {}, cid)
        if "error" in accounts:
            return accounts["error"]
        out += [f"- {a['account_id']} ({a['type']}): **{inr(a['balance'])}**" for a in accounts["accounts"]]
    if wants_txns:
        n = re.search(r"(?:last|recent|past)\s+(\d+)|(\d+)\s*(?:transactions?|txns?)", m)
        limit = int(n.group(1) or n.group(2)) if n else 5
        txns = yield from _run_tool("banking", "get_transactions", {"limit": limit}, cid)
        if "error" in txns:
            return txns["error"]
        out.append(f"Last {len(txns['transactions'])} transactions:")
        out += [f"- {t['date'][:10]} · {t['merchant']} · {'-' if t['type'] == 'debit' else '+'}{inr(t['amount'])}"
                for t in txns["transactions"]]
    return "\n".join(out)


def _rules_fraud(cid, msg):
    if TRANSFER_RE.search(msg):
        amount, (_, dst) = _amount(msg), _transfer_parties(msg)
        if amount is None or dst is None:
            return "No transfer details to screen yet."
        r = yield from _run_tool("fraud", "screen_transfer", {"amount": amount, "to_account": dst}, cid)
        return r["error"] if "error" in r else f"Transfer screening: {_risk_line(r)}"
    named = re.search(r"\bTXN\d+\b", msg, re.I)
    if named:
        ids = [named.group().upper()]
    else:
        txns = yield from _run_tool("fraud", "get_transactions", {"limit": 10}, cid)
        if "error" in txns:
            return txns["error"]
        ids = [t["txn_id"] for t in txns["transactions"] if t["type"] == "debit"]
    flagged, checked = [], 0
    for txn_id in ids:
        r = yield from _run_tool("fraud", "score_transaction", {"txn_id": txn_id}, cid)
        if "error" in r:
            return r["error"]
        checked += 1
        if r["level"] != "low" or named:
            flagged.append(f"- {r['txn_id']} · {inr(r['amount'])} at {r['merchant']}: {_risk_line(r)}")
    if named:
        return flagged[0][2:]
    if not flagged:
        return f"I checked {checked} recent debit transactions and none look suspicious."
    return f"Checked {checked} recent debit transactions; {len(flagged)} need attention:\n" + "\n".join(flagged)


def _rules_credit(cid, msg):
    report = yield from _run_tool("credit", "get_credit_report", {}, cid)
    if "error" in report:
        return report["error"]
    head = (f"Credit score **{report['score']}** ({report['bureau']}), {report['utilization_pct']}% utilisation, "
            f"{report['missed_payments_12m']} missed payment(s) in 12 months.")
    amount = _amount(msg)
    if amount is None or not re.search(r"\bloans?\b|eligib|borrow", msg, re.I):
        loans = yield from _run_tool("credit", "get_loans", {}, cid)
        if "error" in loans:
            return loans["error"]
        rows = [f"- {l['loan_id']} · {l['type']} · outstanding {inr(l['outstanding'])} · EMI {inr(l['emi'])}"
                for l in loans["loans"]]
        return "\n".join([head, *rows])
    tenure = _tenure_months(msg)
    e = yield from _run_tool("credit", "check_loan_eligibility", {"amount": amount, "tenure_months": tenure}, cid)
    if "error" in e:
        return e["error"]
    return "\n".join([
        head,
        f"Loan of {inr(amount)} over {tenure} months: **{e['decision']}**.",
        f"- Rate {e['interest_rate_pct']}% · EMI {inr(e['emi'])} · debt-to-income after loan {e['dti_after_pct']}%",
        f"- Maximum eligible amount: {inr(e['max_eligible_amount'])}",
        *[f"- {reason}" for reason in e["reasons"]],
    ])


RULE_AGENTS = {"banking": _rules_banking, "fraud": _rules_fraud, "credit": _rules_credit}


# --- pipeline --------------------------------------------------------------

def _pipeline(cid, message, history, llm):
    plan, reason, reply = _route_llm(message, history) if llm else _route_rules(message)
    yield {"type": "route", "agents": [name for name, _ in plan], "reason": reason, "mode": "llm" if llm else "rules"}
    if not plan:
        yield {"type": "answer", "text": reply}
        return
    findings = {}
    for name, task in plan:
        yield {"type": "agent_start", "agent": name, "task": task}
        if llm:
            findings[name] = yield from _agent_llm(name, task, cid, message, findings)
        else:
            findings[name] = yield from RULE_AGENTS[name](cid, message)
        yield {"type": "agent_done", "agent": name, "summary": findings[name]}
    if len(findings) == 1:
        text = findings[plan[0][0]]
    elif llm:
        text = _synthesise(message, findings)
    else:
        text = "\n\n".join(f"**{AGENTS[a]['label']}**\n{s}" for a, s in findings.items())
    yield {"type": "answer", "text": text}


def run(cid, message, history=()):
    """Yield trace events for one customer message."""
    if client is None:
        yield from _pipeline(cid, message, [], llm=False)
        return
    acted = False
    try:
        for event in _pipeline(cid, message, list(history)[-8:], llm=True):
            acted = acted or event["type"] == "tool_call"
            yield event
    except Exception as e:  # provider outage, rate limit, malformed model output
        if acted:
            # tools already ran (maybe a transfer request): replaying could repeat side effects
            yield {"type": "error", "message": f"The language model failed mid-request: {e}"}
            return
        yield {"type": "error",
               "message": f"Language model unavailable ({type(e).__name__}); using the rule-based fallback."}
        yield from _pipeline(cid, message, [], llm=False)
