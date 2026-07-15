from __future__ import annotations

import argparse
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

from src.db.models import ParseRun, Vacancy, VacancySkill
from src.db.session import SessionLocal, engine
from src.db.session import Base
from src.market.hh_client import HHClient, normalize_vacancy
from src.market.queries import SEARCH_QUERIES, SearchQuery


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def upsert_vacancy(session: Session, payload: dict, parse_run_id: int) -> tuple[Vacancy, bool]:
    """Возвращает (vacancy, created)."""
    existing = session.scalar(select(Vacancy).where(Vacancy.hh_id == payload["hh_id"]))
    key_skills = payload.pop("key_skills", [])

    if existing is None:
        vacancy = Vacancy(**payload, parse_run_id=parse_run_id)
        session.add(vacancy)
        session.flush()
        created = True
    else:
        vacancy = existing
        for key, value in payload.items():
            setattr(vacancy, key, value)
        vacancy.parse_run_id = parse_run_id
        created = False
        # обновляем hh_key_skills
        vacancy.skills = [s for s in vacancy.skills if s.source != "hh_key_skills"]

    for skill_name in key_skills:
        vacancy.skills.append(
            VacancySkill(skill_name=skill_name, source="hh_key_skills", skill_type="tools")
        )
    return vacancy, created


def run_parser(
    *,
    queries: Optional[Iterable[SearchQuery]] = None,
    limit_per_query: Optional[int] = None,
    dry_run: bool = False,
) -> None:
    ensure_schema()
    queries = list(queries or SEARCH_QUERIES)
    client = HHClient()

    with SessionLocal() as session:
        run = ParseRun(
            status="running",
            queries=[{"text": q.text, "role_group": q.role_group} for q in queries],
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        fetched = 0
        upserted = 0
        seen_ids: set[str] = set()

        try:
            for query in queries:
                print(f"\n=== {query.text} [{query.role_group}] ===")
                ids = client.search_vacancy_ids(query.text, search_field=query.search_field)
                if limit_per_query is not None:
                    ids = ids[:limit_per_query]
                print(f"найдено id: {len(ids)}")

                for vid in tqdm(ids, desc=query.text[:32]):
                    if vid in seen_ids:
                        continue
                    seen_ids.add(vid)
                    raw = client.get_vacancy(vid)
                    fetched += 1
                    if dry_run:
                        continue
                    payload = normalize_vacancy(
                        raw, search_query=query.text, role_group=query.role_group
                    )
                    _, created = upsert_vacancy(session, payload, run.id)
                    if created:
                        upserted += 1
                    if fetched % 20 == 0:
                        session.commit()

                session.commit()

            run.status = "completed"
            run.vacancies_fetched = fetched
            run.vacancies_upserted = upserted
            from datetime import datetime, timezone

            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            print(f"\nГотово. fetched={fetched}, created={upserted}, run_id={run.id}")
        except Exception as exc:
            run.status = "failed"
            run.notes = str(exc)[:2000]
            run.vacancies_fetched = fetched
            run.vacancies_upserted = upserted
            from datetime import datetime, timezone

            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка вакансий hh.ru в PostgreSQL")
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Ограничить число вакансий на запрос (для теста)",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help="Только указанные тексты запросов (можно несколько раз)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Не писать в БД")
    args = parser.parse_args()

    queries = SEARCH_QUERIES
    if args.query:
        wanted = set(args.query)
        queries = [q for q in SEARCH_QUERIES if q.text in wanted]
        if not queries:
            raise SystemExit(f"Не найдено запросов: {args.query}")

    run_parser(
        queries=queries,
        limit_per_query=args.limit_per_query,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
