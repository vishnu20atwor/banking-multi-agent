# Simulated core-banking APIs. In-memory only; state resets on restart.
import statistics
from datetime import datetime
from functools import wraps
from itertools import count

CURRENCY = "INR"

CUSTOMERS = {
    "C001": {"name": "Priya Raman", "segment": "salaried", "city": "Chennai", "monthly_income": 120000,
             "kyc_status": "verified", "customer_since": "2019-04-12", "credit_score": 782,
             "utilization_pct": 22, "missed_payments_12m": 0, "accounts": ["ACC1001", "ACC1002"],
             "cards": ["CARD101"], "beneficiaries": ["ACC2001", "BENE-RENT-01"]},
    "C002": {"name": "Arjun Mehta", "segment": "self-employed", "city": "Mumbai", "monthly_income": 85000,
             "kyc_status": "verified", "customer_since": "2017-08-03", "credit_score": 655,
             "utilization_pct": 61, "missed_payments_12m": 1, "accounts": ["ACC2001"],
             "cards": ["CARD201", "CARD202"], "beneficiaries": ["BENE-SUPPLIER-01"]},
    "C003": {"name": "Kavya Nair", "segment": "student/part-time", "city": "Kochi", "monthly_income": 28000,
             "kyc_status": "pending", "customer_since": "2024-06-20", "credit_score": 580,
             "utilization_pct": 88, "missed_payments_12m": 3, "accounts": ["ACC3001"],
             "cards": ["CARD301"], "beneficiaries": []},
}

ACCOUNTS = {
    "ACC1001": {"type": "savings", "balance": 245000.50},
    "ACC1002": {"type": "current", "balance": 58000.00},
    "ACC2001": {"type": "savings", "balance": 91000.00},
    "ACC3001": {"type": "savings", "balance": 6200.00},
}

CARDS = {
    "CARD101": {"type": "credit", "last4": "4421", "status": "active", "limit": 200000},
    "CARD201": {"type": "credit", "last4": "9034", "status": "active", "limit": 150000},
    "CARD202": {"type": "debit", "last4": "1177", "status": "active", "limit": None},
    "CARD301": {"type": "debit", "last4": "5520", "status": "active", "limit": None},
}

LOANS = {
    "C001": [{"loan_id": "LN1001", "type": "car", "outstanding": 210000.0, "emi": 12000.0,
              "rate_pct": 9.5, "months_left": 20}],
    "C002": [{"loan_id": "LN2001", "type": "personal", "outstanding": 320000.0, "emi": 18000.0,
              "rate_pct": 16.0, "months_left": 26}],
    "C003": [{"loan_id": "LN3001", "type": "personal", "outstanding": 40000.0, "emi": 8000.0,
              "rate_pct": 17.5, "months_left": 5},
             {"loan_id": "LN3002", "type": "education", "outstanding": 30000.0, "emi": 6000.0,
              "rate_pct": 19.0, "months_left": 5}],
}


def _txn(txn_id, date, amount, kind, merchant, category, city, channel, account_id):
    return {"txn_id": txn_id, "date": date, "amount": float(amount), "type": kind, "merchant": merchant,
            "category": category, "city": city, "channel": channel, "account_id": account_id}


# Newest first; execute_transfer inserts at the front.
TRANSACTIONS = {
    "C001": [
        _txn("TXN1008", "2026-10-07T13:20:00", 1850, "debit", "Murugan Idli Shop", "restaurants", "Chennai", "card_pos", "ACC1002"),
        _txn("TXN1007", "2026-10-06T19:45:00", 3200, "debit", "Spencer's Retail", "groceries", "Chennai", "card_pos", "ACC1002"),
        _txn("TXN1006", "2026-10-05T11:10:00", 2100, "debit", "TANGEDCO", "utilities", "Chennai", "netbanking", "ACC1002"),
        _txn("TXN1005", "2026-10-04T08:30:00", 25000, "debit", "Sunrise Residency", "rent", "Chennai", "netbanking", "ACC1002"),
        _txn("TXN1004", "2026-10-03T20:05:00", 1450, "debit", "Cafe Coffee Day Anna Nagar", "restaurants", "Chennai", "card_pos", "ACC1002"),
        _txn("TXN1003", "2026-10-02T10:00:00", 120000, "credit", "Acme Technologies Payroll", "salary", "Chennai", "netbanking", "ACC1002"),
        _txn("TXN1002", "2026-10-01T18:30:00", 4100, "debit", "Big Bazaar Velachery", "groceries", "Chennai", "card_pos", "ACC1002"),
        _txn("TXN1001", "2026-10-01T09:15:00", 899, "debit", "Airtel Broadband", "utilities", "Chennai", "upi", "ACC1002"),
    ],
    "C002": [
        _txn("TXN2010", "2026-10-08T12:00:00", 45000, "credit", "Client Invoice Receipt", "income", "Mumbai", "netbanking", "ACC2001"),
        _txn("TXN2009", "2026-10-07T14:10:00", 2300, "debit", "Reliance Fresh Bandra", "groceries", "Mumbai", "card_pos", "ACC2001"),
        _txn("TXN2008", "2026-10-06T03:19:00", 49999, "debit", "GIFTCARD MART", "shopping", "Lagos", "card_online", "ACC2001"),
        _txn("TXN2007", "2026-10-06T03:12:00", 74999, "debit", "ELECTRO-HUB ONLINE", "electronics", "Lagos", "card_online", "ACC2001"),
        _txn("TXN2006", "2026-10-05T21:40:00", 1200, "debit", "Cafe Mondegar", "restaurants", "Mumbai", "card_pos", "ACC2001"),
        _txn("TXN2005", "2026-10-04T10:00:00", 18000, "debit", "Office Rent Lower Parel", "rent", "Mumbai", "netbanking", "ACC2001"),
        _txn("TXN2004", "2026-10-03T16:25:00", 3600, "debit", "BEST Electricity", "utilities", "Mumbai", "upi", "ACC2001"),
        _txn("TXN2003", "2026-10-02T13:05:00", 2750, "debit", "Zomato", "restaurants", "Mumbai", "upi", "ACC2001"),
        _txn("TXN2002", "2026-10-02T09:30:00", 6400, "debit", "D-Mart Thane", "groceries", "Mumbai", "card_pos", "ACC2001"),
        _txn("TXN2001", "2026-10-01T11:00:00", 85000, "credit", "Client Payment", "income", "Mumbai", "netbanking", "ACC2001"),
    ],
    "C003": [
        _txn("TXN3006", "2026-10-07T18:00:00", 450, "debit", "Chai Point Kochi", "restaurants", "Kochi", "upi", "ACC3001"),
        _txn("TXN3005", "2026-10-06T12:30:00", 800, "debit", "Kerala Books Store", "education", "Kochi", "card_pos", "ACC3001"),
        _txn("TXN3004", "2026-10-04T09:15:00", 1200, "debit", "Metro Mart", "groceries", "Kochi", "upi", "ACC3001"),
        _txn("TXN3003", "2026-10-03T17:45:00", 350, "debit", "Kochi Metro Card", "transport", "Kochi", "upi", "ACC3001"),
        _txn("TXN3002", "2026-10-02T10:00:00", 9000, "credit", "Part-time Payout", "income", "Kochi", "netbanking", "ACC3001"),
        _txn("TXN3001", "2026-10-01T20:10:00", 600, "debit", "Jio Recharge", "utilities", "Kochi", "upi", "ACC3001"),
    ],
}

_ref = count(10000001)


def _tool(fn):
    """Turn bad-input exceptions into {"error": ...} results."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}
    return wrapper


def _cust(cid):
    if not isinstance(cid, str) or cid not in CUSTOMERS:
        raise ValueError(f"Unknown customer {cid}")
    return CUSTOMERS[cid]


def _num(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")


def _int(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number")


def _level(score):
    return "high" if score >= 60 else "medium" if score >= 30 else "low"


def _emi(principal, rate_pct, n):
    r = rate_pct / 12 / 100
    f = (1 + r) ** n
    return principal * r * f / (f - 1)


@_tool
def list_customers():
    return {"customers": [{"id": cid, "name": c["name"], "segment": c["segment"]} for cid, c in CUSTOMERS.items()]}


@_tool
def get_profile(customer_id):
    c = _cust(customer_id)
    return {"id": customer_id, "name": c["name"], "segment": c["segment"], "city": c["city"],
            "monthly_income": c["monthly_income"], "kyc_status": c["kyc_status"],
            "customer_since": c["customer_since"]}


@_tool
def get_accounts(customer_id):
    c = _cust(customer_id)
    return {"accounts": [{"account_id": a, "type": ACCOUNTS[a]["type"], "balance": round(ACCOUNTS[a]["balance"], 2),
                          "currency": CURRENCY} for a in c["accounts"]]}


@_tool
def get_transactions(customer_id, limit=10):
    _cust(customer_id)
    n = max(1, min(50, _int(limit, "limit")))
    return {"transactions": TRANSACTIONS[customer_id][:n]}


@_tool
def get_cards(customer_id):
    c = _cust(customer_id)
    return {"cards": [{"card_id": k, **CARDS[k]} for k in c["cards"]]}


@_tool
def block_card(customer_id, card_id):
    c = _cust(customer_id)
    if card_id not in c["cards"]:
        raise ValueError(f"Card {card_id} does not belong to customer {customer_id}")
    CARDS[card_id]["status"] = "blocked"
    return {"card_id": card_id, "last4": CARDS[card_id]["last4"], "status": "blocked"}


@_tool
def score_transaction(customer_id, txn_id):
    c = _cust(customer_id)
    txns = TRANSACTIONS[customer_id]
    t = next((x for x in txns if x["txn_id"] == txn_id), None)
    if t is None:
        raise ValueError(f"Transaction {txn_id} not found for customer {customer_id}")
    debits = [x["amount"] for x in txns if x["type"] == "debit"]
    other_merchants = {x["merchant"] for x in txns if x["txn_id"] != txn_id}
    score, reasons = 0, []
    if t["city"] != c["city"]:
        score += 40
        reasons.append(f"Made in {t['city']}, not home city {c['city']}")
    if debits and t["amount"] > 3 * statistics.median(debits):
        score += 25
        reasons.append(f"Amount {t['amount']:,.2f} is over 3x the customer's median debit")
    hour = datetime.fromisoformat(t["date"]).hour
    if 0 <= hour <= 5:
        score += 20
        reasons.append(f"Transaction at {hour:02d}:00, a late-night hour")
    if t["channel"] == "card_online" and t["merchant"] not in other_merchants:
        score += 15
        reasons.append(f"First-time online merchant: {t['merchant']}")
    score = min(score, 100)
    return {"txn_id": txn_id, "amount": t["amount"], "merchant": t["merchant"], "risk_score": score,
            "level": _level(score), "reasons": reasons or ["Consistent with normal spending pattern"]}


@_tool
def screen_transfer(customer_id, amount, to_account):
    c = _cust(customer_id)
    amount = _num(amount, "amount")
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    total = sum(ACCOUNTS[a]["balance"] for a in c["accounts"])
    score, reasons = 0, []
    if amount > 0.5 * total:
        score += 45
        reasons.append("Amount is over half of total balance")
    if amount >= 100000:
        score += 30
        reasons.append("Large transfer of 1,00,000 or more")
    if to_account not in c["beneficiaries"]:
        score += 25
        reasons.append(f"{to_account} is not a known beneficiary")
    if c["kyc_status"] != "verified":
        score += 20
        reasons.append("KYC not verified")
    score = min(score, 100)
    return {"amount": amount, "to_account": to_account, "risk_score": score, "level": _level(score),
            "reasons": reasons or ["Consistent with normal transfer pattern"]}


@_tool
def execute_transfer(customer_id, from_account, to_account, amount):
    c = _cust(customer_id)
    amount = round(_num(amount, "amount"), 2)
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    if from_account not in c["accounts"]:
        raise ValueError(f"Account {from_account} does not belong to customer {customer_id}")
    if to_account == from_account:
        raise ValueError("Cannot transfer to the same account")
    src = ACCOUNTS[from_account]
    if src["balance"] < amount:
        raise ValueError(f"Insufficient funds: balance is {src['balance']:,.2f}")
    # ponytail: no locking, fine for a single-threaded sim; add a lock if called concurrently.
    src["balance"] = round(src["balance"] - amount, 2)
    if to_account in ACCOUNTS:
        ACCOUNTS[to_account]["balance"] = round(ACCOUNTS[to_account]["balance"] + amount, 2)
    ref = f"TRF{next(_ref):08d}"
    TRANSACTIONS[customer_id].insert(0, _txn(ref, datetime.now().replace(microsecond=0).isoformat(), amount,
                                             "debit", f"Transfer to {to_account}", "transfer", c["city"],
                                             "netbanking", from_account))
    return {"status": "completed", "reference": ref, "from_account": from_account, "to_account": to_account,
            "amount": amount, "new_balance": src["balance"]}


@_tool
def get_credit_report(customer_id):
    c = _cust(customer_id)
    loans = LOANS[customer_id]
    return {"score": c["credit_score"], "bureau": "CIBIL (simulated)", "active_loans": len(loans),
            "monthly_emi_total": round(sum(ln["emi"] for ln in loans), 2),
            "utilization_pct": c["utilization_pct"], "missed_payments_12m": c["missed_payments_12m"]}


@_tool
def get_loans(customer_id):
    _cust(customer_id)
    return {"loans": LOANS[customer_id]}


@_tool
def check_loan_eligibility(customer_id, amount, tenure_months, loan_type="personal"):
    # loan_type is accepted for the API shape; the spec defines no per-type rules, so it does not change the result.
    c = _cust(customer_id)
    principal = _num(amount, "amount")
    n = _int(tenure_months, "tenure_months")
    if principal <= 0:
        raise ValueError("amount must be greater than 0")
    if not 1 <= n <= 360:
        raise ValueError("tenure_months must be between 1 and 360")
    score = c["credit_score"]
    rate = 10.5 if score >= 750 else 12.0 if score >= 700 else 14.5 if score >= 650 else 18.0
    existing = sum(ln["emi"] for ln in LOANS[customer_id])
    emi = round(_emi(principal, rate, n), 2)
    dti_after = round((existing + emi) / c["monthly_income"] * 100, 1)
    kyc_ok = c["kyc_status"] == "verified"
    missed = c["missed_payments_12m"]
    if score < 600 or dti_after > 55 or not kyc_ok:
        decision = "declined"
    elif score < 700 or dti_after > 40 or missed > 0:
        decision = "refer"
    else:
        decision = "approved"
    reasons = []
    if score < 600:
        reasons.append(f"Credit score {score} is below the 600 minimum")
    elif score < 700:
        reasons.append(f"Credit score {score} is below 700")
    if dti_after > 55:
        reasons.append(f"DTI after loan is {dti_after}%, above 55%")
    elif dti_after > 40:
        reasons.append(f"DTI after loan is {dti_after}%, above 40%")
    if not kyc_ok:
        reasons.append("KYC not verified")
    if missed > 0:
        reasons.append(f"{missed} missed payment(s) in the last 12 months")
    if not reasons:
        reasons.append("Score, DTI and payment history within policy")
    # Largest principal (rounded down to 1000) that keeps DTI at or under 40%: invert the EMI formula.
    headroom = c["monthly_income"] * 0.40 - existing
    max_amt = 0
    if headroom > 0:
        r = rate / 12 / 100
        f = (1 + r) ** n
        max_amt = int(headroom * (f - 1) / (r * f) // 1000 * 1000)
    return {"eligible": decision != "declined", "decision": decision, "reasons": reasons,
            "interest_rate_pct": rate, "emi": emi, "dti_after_pct": dti_after,
            "max_eligible_amount": max_amt}
