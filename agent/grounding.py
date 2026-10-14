"""Grounding checks: the agent may only write or claim what it can trace to evidence.

Three invariants, all enforced in code, all fail closed:
1. Facts:  a fact's quote must appear in a known source, and its value must be readable from the quote.
2. Writes: every typed or selected value must cite a fact, the request, a policy doc or a human answer.
3. Claims: a "pass" verdict needs a quote that is on the page the verifier is looking at.
Every rejection message says how to fix the problem, so the agent recovers instead of looping.
"""
import re
import unicodedata
from datetime import date

from dateutil import parser as dateparser

FACT_TYPES = ("text", "name", "email", "date", "money", "quote")
WRITE_SOURCES = "fact:<key>, request, answer:<n>, policy:<doc> or composed"

EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)
LINE_PREFIX_RE = re.compile(r"(?m)^\s*L\d+:[ \t]?")
HONORIFIC_RE = re.compile(r"\b(mr|mrs|ms|dr|shri|smt)\.?\s+", re.I)
MONEY_RE = re.compile(r"(?:inr|rs\.?)\s*(\d[\d,]*(?:\.\d+)?)", re.I)
TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "₹": "INR ",
})
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DATE_RES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\.?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.I),
]


def normalize(text: str) -> str:
    """Compare text the way a careful human would: ignore case, spacing, ligatures, quote styles."""
    text = unicodedata.normalize("NFKC", text or "").translate(TRANSLATE)
    text = LINE_PREFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def to_iso_date(raw: str, day_first: bool = True) -> str | None:
    raw = raw.strip()
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw):
            y, mo, d = m.groups()
        elif m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", raw):
            a, b, y = m.groups()
            d, mo = (a, b) if day_first else (b, a)
        else:  # month written as a word, so there is no day/month ambiguity
            return dateparser.parse(raw).date().isoformat()
        return date(int(y), int(mo), int(d)).isoformat()
    except (ValueError, OverflowError):
        return None


def dates_in(text: str, day_first: bool = True) -> set[str]:
    return {iso for pat in DATE_RES for m in pat.finditer(text) if (iso := to_iso_date(m.group(0), day_first))}


def emails_in(text: str) -> set[str]:
    return {e.lower() for e in EMAIL_RE.findall(text)}


def money_in(text: str) -> set[str]:
    return {_digits(m) for m in MONEY_RE.findall(normalize(text))}


def numbers_in(text: str) -> set[str]:
    """Numbers that are not part of an email address or a date."""
    for pat in [EMAIL_RE, *DATE_RES]:
        text = pat.sub(" ", text)
    return set(re.findall(r"\d{2,}", text))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(HONORIFIC_RE.sub("", text or "")))


def _digits(value: str) -> str:
    whole = re.sub(r"[^\d.]", "", value).split(".")[0]
    return whole.lstrip("0") or "0"


def read_value(type_: str, value: str, quote: str, day_first: bool = True) -> str | None:
    """The normalized value if `value` can be read from `quote`, else None."""
    if type_ == "date":
        iso = to_iso_date(value, day_first)
        return iso if iso and iso in dates_in(quote, day_first) else None
    if type_ == "email":
        v = value.strip().lower()
        return v if v in emails_in(quote) else None
    if type_ == "money":
        v = _digits(value)
        return v if v in money_in(quote) else None
    if type_ == "name":
        v = " ".join(tokens(value))
        return v if v and v in " ".join(tokens(quote)) else None
    v = normalize(value)  # text, quote
    return v if v and v in normalize(quote) else None


def check_fact(value: str, type_: str, quote: str, source_id: str, sources: dict[str, str],
               day_first: bool = True) -> tuple[str | None, str]:
    """(normalized value, "") if grounded, else (None, how to fix it)."""
    if type_ not in FACT_TYPES:
        return None, f"Unknown fact type {type_!r}. Use one of: {', '.join(FACT_TYPES)}."
    if source_id not in sources:
        return None, f"Unknown source {source_id!r}. Available sources: {', '.join(sources)}."
    if len(normalize(quote)) < 3:
        return None, "The quote is empty or too short. Copy the exact text that contains the value."
    if normalize(quote) not in normalize(sources[source_id]):
        return None, (f"That quote is not in {source_id}. Copy the text exactly as it appears there "
                      "(you may shorten it, but do not paraphrase or combine separate lines).")
    normalized = read_value(type_, value, quote, day_first)
    if normalized is None:
        return None, (f"The value {value!r} cannot be read from the quote as a {type_}. "
                      "Use a quote that contains the value itself, or fix the value or type. "
                      "If this is new text you are writing (a subject line, a message), it is not a fact: "
                      "type it directly with fill(..., source='composed').")
    return normalized, ""


def check_write(value: str, source: str, facts: dict, sources: dict[str, str], day_first: bool = True) -> str:
    """"" if the value to type/select is grounded, else how to fix it."""
    if source.startswith("fact:"):
        key = source[5:]
        fact = facts.get(key)
        if not fact:
            return (f"There is no fact named {key!r}. Record it first with record_fact (with a quote), "
                    f"or cite another source ({WRITE_SOURCES}).")
        if fact["type"] in ("date", "email", "money"):
            matches = read_value(fact["type"], value, value, day_first) == fact["value"]
        elif fact["type"] == "name":
            matches = tokens(value) == fact["value"].split()
        else:
            matches = normalize(value) == fact["value"]
        return "" if matches else (f"{value!r} does not match fact {key} = {fact['raw']!r}. "
                                   "Type the fact's value (date format may differ).")
    if source == "composed":
        return check_composed(value, facts, sources, day_first)
    if source == "request" or source.startswith(("answer:", "policy:")):
        if source not in sources:
            return f"Unknown source {source!r}. Available: {', '.join(k for k in sources if k != 'page')}."
        if normalize(value) in normalize(sources[source]):
            return ""
        return f"{value!r} does not appear in {source}. Record what you need as a fact first, then cite fact:<key>."
    return f"Unknown source {source!r}. Cite one of: {WRITE_SOURCES}."


def check_composed(text: str, facts: dict, sources: dict[str, str], day_first: bool = True) -> str:
    """Free text is allowed only if every email, date and number in it is backed by evidence."""
    backing = " ".join(
        [sources[k] for k in sources if k == "request" or k.startswith(("answer:", "policy:"))]
        + [f"{f['raw']} | {f['value']}" for f in facts.values()]
    )
    allowed_dates = dates_in(backing, day_first) | {f["value"] for f in facts.values() if f["type"] == "date"}
    unsupported = sorted(
        (emails_in(text) - emails_in(backing))
        | {d for d in dates_in(text, day_first) if d not in allowed_dates}
        | (numbers_in(text) - numbers_in(backing))
    )
    if not unsupported:
        return ""
    return (f"These details in your text are not backed by any fact, the request, a human answer or a policy: "
            f"{unsupported}. Record them as facts first (with quotes), or remove them.")


def match_options(value: str, options: list[str]) -> list[str]:
    """Options whose words include every word of `value`."""
    want = set(tokens(value))
    return [o for o in options if want and want <= set(tokens(o))]


def check_claim(quote: str, page_text: str) -> str:
    """A "pass" verdict must quote the page the verifier is looking at."""
    if len(normalize(quote)) < 3:
        return "A pass needs a quote: copy the exact text from the current page that proves the criterion."
    if normalize(quote) not in normalize(page_text):
        return "That quote is not on the current page. Open the page that proves it and quote it exactly."
    return ""
