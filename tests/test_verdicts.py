"""Verifier evidence rules: a pass must quote pages actually viewed during verification (any of them)."""
from agent.policy import load_company
from agent.tools import _verdict

COMPANY = load_company()
STATE = {
    "spec": {"criteria": [{"id": "C1", "text": "Exactly one record"}, {"id": "C2", "text": "Ticket exists"}]},
    "counters": {"work": 10, "verify": 3, "repair": 0, "consec_fail": 0},
    "url": "/hr/employees/E1062",
    "obs": "| Employee ID | E1062 |\n| Status | Active |",
    "page_text": "Employee ID E1062 Status Active",
    "seen": {"/hr/employees?q=Priya": {"snapshot": "| E1062 | Priya Sharma | Active |", "text": "E1062 Priya Sharma"}},
}


async def test_pass_can_combine_quotes_from_several_viewed_pages():
    outcome, msg, _, updates = await _verdict(
        {"criterion_id": "C1", "status": "pass", "quotes": ["| E1062 | Priya Sharma | Active |", "| Status | Active |"]},
        STATE, None, COMPANY)
    assert outcome == "ok", msg
    assert updates["verdicts"]["C1"]["url"] == "/hr/employees?q=Priya, /hr/employees/E1062"


async def test_pass_with_a_quote_from_an_unviewed_page_is_rejected():
    outcome, msg, _, _ = await _verdict(
        {"criterion_id": "C1", "status": "pass", "quotes": ["| Type | Onboarding |"]}, STATE, None, COMPANY)
    assert outcome == "rejected" and "pages you viewed" in msg
