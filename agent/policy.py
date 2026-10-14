"""Company data and the egress gate.

Every request the browser sends is checked by `decide`. The rules live in
company/profile.yaml, so changing what needs approval is a data change, not a code change.
Approvals bind to a hash of the exact payload: if the payload changes, it needs a new approval.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

COMPANY_DIR = Path(__file__).resolve().parent.parent / "company"


@dataclass
class Company:
    profile: dict
    access: dict
    docs: dict[str, str]  # "policy:<file stem>" -> text; shown to the LLM and citable as a source


def load_company(path: Path = COMPANY_DIR) -> Company:
    docs = {f"policy:{f.stem}": f.read_text(encoding="utf-8")
            for f in sorted(path.rglob("*")) if f.suffix in (".md", ".yaml")}
    return Company(
        profile=yaml.safe_load((path / "profile.yaml").read_text(encoding="utf-8")),
        access=yaml.safe_load((path / "access_matrix.yaml").read_text(encoding="utf-8")),
        docs=docs,
    )


@dataclass
class Decision:
    action: str  # "allow" | "approve" | "block"
    rule: str = ""
    description: str = ""
    payload: dict = field(default_factory=dict)  # what the human is asked to approve
    key_hash: str = ""


def decide(method: str, url: str, post_data: str | None, company: Company, mode: str,
           approved: set[str]) -> Decision:
    path = urlparse(url).path
    if any(path == p or path.startswith(p + "/") for p in company.profile["blocked_paths"]):
        return Decision("block", "blocked_path", f"{path} is off-limits")
    if mode == "verify" and method != "GET":
        return Decision("block", "verify_read_only", "Verification is read-only; nothing can be changed")
    if method != "POST":
        return Decision("allow")

    form = parse_qs(post_data or "", keep_blank_values=True)
    pending = None
    for rule in company.profile["egress_rules"]:
        if not _applies(rule, method, path, form, company):
            continue
        if rule["action"] == "block":
            return Decision("block", rule["id"], rule["description"])
        payload = {f: path if f == "path" else ", ".join(form.get(f, [])) for f in rule["key_fields"]}
        key_hash = hashlib.sha256(json.dumps({"rule": rule["id"], **payload}, sort_keys=True).encode()).hexdigest()[:16]
        if key_hash not in approved and pending is None:
            pending = Decision("approve", rule["id"], rule["description"], payload, key_hash)
    return pending or Decision("allow")


def _applies(rule: dict, method: str, path: str, form: dict, company: Company) -> bool:
    match = rule["match"]
    if match.get("method", method) != method:
        return False
    if "path" in match and match["path"] != path:
        return False
    if "path_regex" in match and not re.match(match["path_regex"], path):
        return False
    when = rule.get("when")
    if not when:
        return True
    values = form.get(when["field"], [])
    if "not_domain" in when:
        emails = [e.strip().lower() for v in values for e in v.split(",") if e.strip()]
        return any(not e.endswith("@" + when["not_domain"]) for e in emails)
    if when.get("any_privileged"):
        return any(v in company.access["privileged"] for v in values)
    if "matches" in when:
        return any(re.search(company.profile["patterns"][when["matches"]], v) for v in values)
    return False
