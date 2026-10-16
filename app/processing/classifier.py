import re

from app.models import Industry


def matches(term: str, text: str) -> bool:
    if re.fullmatch(r"[a-zA-Z0-9 ]+", term):
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE))
    return term.casefold() in text.casefold()


def classify(title: str, summary: str, industries: list[Industry], source_ids: list[str]) -> list[str]:
    text = title + " " + summary
    result = []
    for industry in industries:
        if not industry.enabled or any(matches(k, text) for k in industry.exclusions):
            continue
        if industry.id in source_ids or any(matches(k, text) for k in industry.keywords + industry.companies):
            result.append(industry.id)
    return result
