"""Conservative report linking. Similarity never establishes factual verification."""

import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Event, EventArticle

WINDOW = timedelta(hours=72)
PROTECTED = re.compile(
    r"\d+(?:[.,]\d+)*|\b(?:no|not|denies|denied|approved|rejected)\b|否认|批准|拒绝|辟谣|撤回", re.I
)


def normalized_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(re.findall(r"\w+", text))


def title_similarity(left: str, right: str) -> float:
    # Bound pairwise comparison cost for untrusted feed titles.
    if max(len(left), len(right)) > 600:
        return 0.0
    a, b = normalized_title(left), normalized_title(right)
    # Short/general headlines, different numbers and changed factual polarity stay separate.
    if min(len(a), len(b)) < 24 or set(PROTECTED.findall(a)) != set(PROTECTED.findall(b)):
        return 0.0
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    words_a, words_b = set(a.split()), set(b.split())
    if len(words_a) >= 5 and len(words_b) >= 5:
        return ratio if words_a == words_b else 0.0
    # Chinese titles have no reliable whitespace segmentation: require nearly identical strings.
    return ratio if ratio >= 0.98 else 0.0


def matching_event(session: Session, article: Article) -> Event | None:
    if article.publication_time_inferred:
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
        .limit(500)
    )
    matches = [(title_similarity(article.title, other.title), event) for other, event in candidates]
    matches = [(similarity, event) for similarity, event in matches if similarity >= 0.94]
    # Ambiguity between multiple existing events is left for review; do not pick arbitrarily.
    if len({event.id for _, event in matches}) != 1:
        return None
    event = max(matches, key=lambda match: match[0])[1]
    # A publisher already represented in this event may be reporting a later development.
    # Compare against every linked report, not just the initial report's publisher.
    represented = session.scalar(
        select(Article.id)
        .join(EventArticle, EventArticle.article_id == Article.id)
        .where(EventArticle.event_id == event.id, Article.source_id == article.source_id)
        .limit(1)
    )
    return None if represented else event
