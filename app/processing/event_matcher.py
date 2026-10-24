"""Conservative report linking. Similarity never establishes factual verification.

Linking is deliberately a strict title-equality check, not a fuzzy similarity score.
A false negative only misses a cross-source link; a false positive merges different
facts, hides follow-up alerts and silently corrupts analytics. Titles are therefore
compared after *typographic* normalization only, preserving token order, numbers,
signs, currencies, negation, question marks and product symbols.
"""

import unicodedata
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Event, EventArticle

WINDOW = timedelta(hours=72)
# Untrusted feed titles: bound comparison cost and refuse to link very long strings.
MAX_TITLE_CHARS = 600
# Short/general headlines are too weak to identify an event on their own.
MIN_TITLE_CHARS = 24
# Bound the candidate scan for one article.
CANDIDATE_LIMIT = 500


def normalized_title(title: str) -> str:
    """Return a typography-normalized title without erasing factual signals.

    NFC (not NFKC) is intentional. Compatibility normalization would fold distinct
    technical identifiers together (for example ``①``/``1``, ``²``/``2``, ``Ⅳ``/``IV``
    or fullwidth ``Ｃ``/``C``), so those characters stay distinct here. A false
    negative is preferable to treating two different identifiers as the same fact.

    Case is removed with ``str.lower()``, not ``str.casefold()``: casefold expands
    ``ß`` to ``ss`` and ``ﬀ`` to ``ff``, which would merge differently spelled
    entities or product identifiers. ``lower()`` preserves those characters.

    Whitespace is formatting. A sentence-final exclamation mark is also treated as
    decoration (``Title!`` == ``Title``), but a question mark is a factual signal
    and is never removed. All other punctuation and symbols, including ``+``,
    ``-``, ``−``, currencies and ``++``, are preserved exactly.
    """
    text = unicodedata.normalize("NFC", title).lower()
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split()).rstrip(" !")


def _comparable_title(title: str) -> str | None:
    """Return the normalized title only if it is eligible for strict comparison."""
    if len(title) > MAX_TITLE_CHARS:
        return None
    normalized = normalized_title(title)
    return normalized if len(normalized) >= MIN_TITLE_CHARS else None


def title_similarity(left: str, right: str) -> float:
    """Return 1.0 for equivalent titles and 0.0 otherwise; never a fuzzy score.

    Kept as a small compatibility shim for the existing matching contract. There is
    no partial score: titles either normalize to exactly the same string or they do
    not describe a confidently identical event title.
    """
    a, b = _comparable_title(left), _comparable_title(right)
    return 1.0 if a is not None and a == b else 0.0


def matching_event(session: Session, article: Article) -> Event | None:
    if article.publication_time_inferred:
        return None
    target = _comparable_title(article.title)
    if target is None:
        return None
    candidates = session.execute(
        select(Article, Event)
        .join(EventArticle, EventArticle.article_id == Article.id)
        .join(Event, Event.id == EventArticle.event_id)
        .where(
            Article.source_id != article.source_id,
            Article.publication_time_inferred.is_(False),
            Article.published_at >= article.published_at - WINDOW,
            Article.published_at <= article.published_at + WINDOW,
            Event.status == "active",
            EventArticle.relation_type == "initial",
        )
        .order_by(Article.published_at.desc(), Article.id)
        .limit(CANDIDATE_LIMIT)
    )
    matched: dict[str, Event] = {}
    for other, event in candidates:
        # Strict equality preserves order and every symbol; no token-set or numeric
        # comparison can mask swapped entities or changed signs.
        if _comparable_title(other.title) == target:
            matched[event.id] = event
    # Ambiguity between multiple existing events is left for review; do not pick arbitrarily.
    if len(matched) != 1:
        return None
    event = next(iter(matched.values()))
    # A publisher already represented in this event may be reporting a later development.
    # Compare against every linked report, not just the initial report's publisher.
    represented = session.scalar(
        select(Article.id)
        .join(EventArticle, EventArticle.article_id == Article.id)
        .where(EventArticle.event_id == event.id, Article.source_id == article.source_id)
        .limit(1)
    )
    return None if represented else event
