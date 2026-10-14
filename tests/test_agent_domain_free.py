"""Generalization as a tested property: nothing under agent/ may know this company's domain.

Everything specific to Acme or to HR lives in company/ (data) and sandbox/ (the environment).
"""
import re
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
FORBIDDEN = re.compile(r"priya|onboard|offboard|hris|salary|laptop|acme|employee|/hr\b|/it\b|/mail\b", re.I)


def test_agent_code_has_no_domain_words():
    hits = [f"{path.name}:{n}: {line.strip()}"
            for path in AGENT_DIR.glob("*.py")
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if FORBIDDEN.search(line)]
    assert not hits, "Domain knowledge leaked into agent/:\n" + "\n".join(hits)
