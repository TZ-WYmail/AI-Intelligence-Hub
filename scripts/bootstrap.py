"""Idempotent initial data import. Use API/dashboard for later edits."""

from pathlib import Path

import yaml
from sqlalchemy import select

from app.db import Session
from app.models import Industry, Source


def main():
    with Session() as session:
        for values in yaml.safe_load(Path("config/industries.yaml").read_text()):
            if not session.get(Industry, values["id"]):
                session.add(Industry(**values))
        session.flush()
        path = Path("config/sources.yaml")
        for values in yaml.safe_load(path.read_text()) if path.exists() else []:
            fields = {k: v for k, v in values.items() if k in Source.__table__.columns.keys()}
            if not session.scalar(select(Source).where(Source.name == fields["name"])):
                session.add(Source(**fields))
        session.commit()


if __name__ == "__main__":
    main()
