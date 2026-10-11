import bank


def _balance(customer_id, account_id):
    return next(a["balance"] for a in bank.get_accounts(customer_id)["accounts"] if a["account_id"] == account_id)


def test_unknown_customer():
    assert bank.get_profile("C999") == {"error": "Unknown customer C999"}


def test_fraud_txn_high_and_normal_txn_low():
    assert bank.score_transaction("C002", "TXN2007")["level"] == "high"
    assert bank.score_transaction("C001", "TXN1007")["level"] == "low"


def test_c001_loan_approved_with_expected_emi():
    r = bank.check_loan_eligibility("C001", 500000, 36, "personal")
    assert r["decision"] == "approved", r
    assert abs(r["emi"] - 16251) <= 1, r  # formula gives 16251.22


def test_c003_loan_declined():
    assert bank.check_loan_eligibility("C003", 50000, 12)["decision"] == "declined"


def test_transfer_debits_exactly():
    before = _balance("C001", "ACC1001")
    r = bank.execute_transfer("C001", "ACC1001", "BENE-RENT-01", 1000.25)
    assert r["status"] == "completed", r
    assert round(before - _balance("C001", "ACC1001"), 2) == 1000.25


def test_insufficient_funds_leaves_balance():
    before = _balance("C003", "ACC3001")
    r = bank.execute_transfer("C003", "ACC3001", "BENE-RENT-01", 10000)
    assert "error" in r, r
    assert _balance("C003", "ACC3001") == before


def test_block_other_customers_card_errors():
    assert "error" in bank.block_card("C001", "CARD201")


def test_large_transfer_to_unknown_account_is_high():
    assert bank.screen_transfer("C001", 200000, "ACC9999")["level"] == "high"


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
