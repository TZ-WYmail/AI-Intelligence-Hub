from pathlib import Path

import yaml

from app.models import Article, Source, now
from app.processing.classifier import matches


def score(article: Article, source: Source, industry_ids: list[str]) -> tuple[int, dict]:
    rules = yaml.safe_load(Path("config/rules.yaml").read_text())
    weights = rules["weights"]
    if abs(sum(weights.values()) - 1) > 0.001:
        raise ValueError("Scoring weights must sum to 1")
    age = max(0, (now() - article.published_at).total_seconds() / 3600)
    dimensions = {
        "trust": source.trust_level * 100,
        "impact": 90 if any(matches(t, article.title) for t in rules["major_terms"]) else 35,
        "freshness": max(0, 100 - age * 4) if not article.publication_time_inferred else 30,
        "relevance": 100 if industry_ids else 10,
        "confirmation": 0,
    }
    return round(sum(dimensions[k] * weights[k] for k in dimensions)), dimensions
