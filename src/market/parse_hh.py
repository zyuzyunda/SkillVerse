from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

from src.db.models import ParseRun, Vacancy, VacancySkill
from src.db.session import Base, SessionLocal, engine
from src.market.hh_client import HHClient, HHForbiddenError, normalize_vacancy
from src.market.queries import SEARCH_QUERIES, SearchQuery


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def upsert_vacancy(session: Session, payload: dict, parse_run_id: int) -> tuple[Vacancy, bool]:
    """Возвращает (vacancy, created)."""
    existing = session.scalar(select(Vacancy).where(Vacancy.hh_id == payload["hh_id"]))
    key_skills = payload.pop("key_skills", []) or []

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

    # явное удаление, иначе UniqueViolation при «замене» через relationship
    session.query(VacancySkill).filter(
        VacancySkill.vacancy_id == vacancy.id,
        VacancySkill.source == "hh_key_skills",
    ).delete(synchronize_session=False)
    session.flush()

    seen: set[str] = set()
    for skill_name in key_skills:
        name = (skill_name or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        session.add(
            VacancySkill(
                vacancy_id=vacancy.id,
                skill_name=name,
                source="hh_key_skills",
                skill_type="tools",
            )
        )
    return vacancy, created


def run_parser(
    *,
    queries: Optional[Iterable[SearchQuery]] = None,
    limit_per_query: Optional[int] = None,
    max_pages: Optional[int] = None,
    dry_run: bool = False,
    prefer_html: Optional[bool] = None,
    date_from: Optional[str] = None,
) -> None:
    ensure_schema()
    queries = list(queries or SEARCH_QUERIES)
    client = HHClient(prefer_html=prefer_html)
    source_note = "html" if client.prefer_html else "api"
    from src.config import settings as _settings

    effective_date = date_from if date_from is not None else (_settings.hh_date_from or None)
    if effective_date:
        effective_date = str(effective_date).strip() or None

    with SessionLocal() as session:
        run = ParseRun(
            status="running",
            queries=[{"text": q.text, "role_group": q.role_group} for q in queries],
            notes=f"source={source_note}; date_from={effective_date or '-'}",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        fetched = 0
        upserted = 0
        errors = 0
        seen_ids: set[str] = set()

        try:
            for query in queries:
                print(f"\n=== {query.text} [{query.role_group}] ===")
                try:
                    ids = client.search_vacancy_ids(
                        query.text,
                        search_field=query.search_field,
                        max_pages=max_pages,
                        date_from=effective_date,
                    )
                except HHForbiddenError as exc:
                    print(f"поиск недоступен: {exc}")
                    raise

                if limit_per_query is not None:
                    ids = ids[:limit_per_query]
                print(f"найдено id: {len(ids)}")

                for vid in tqdm(ids, desc=query.text[:32]):
                    if vid in seen_ids:
                        continue
                    seen_ids.add(vid)
                    try:
                        raw = client.get_vacancy(vid)
                    except HHForbiddenError:
                        raise
                    except Exception as exc:
                        errors += 1
                        tqdm.write(f"skip {vid}: {exc}")
                        continue

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
            run.finished_at = datetime.now(timezone.utc)
            note = f"errors={errors}; source={source_note}"
            run.notes = ((run.notes or "") + "; " + note).strip("; ")
            session.commit()
            print(
                f"\nГотово. fetched={fetched}, created={upserted}, "
                f"errors={errors}, run_id={run.id}, source={source_note}"
            )
        except Exception as exc:
            run.status = "failed"
            run.notes = str(exc)[:2000]
            run.vacancies_fetched = fetched
            run.vacancies_upserted = upserted
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
        "--max-pages",
        type=int,
        default=None,
        help="Макс. страниц поиска на запрос (по умолчанию из HH_MAX_PAGES_PER_QUERY)",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help="Только указанные тексты запросов (можно несколько раз)",
    )
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        help="Только role_group (можно несколько раз), напр. data_science",
    )
    parser.add_argument("--dry-run", action="store_true", help="Не писать в БД")
    parser.add_argument(
        "--try-api",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="API hh.ru с OAuth приложения (default: auto — API если есть HH_CLIENT_*)",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Принудительно HTML-скрапинг (без API)",
    )
    parser.add_argument(
        "--date-from",
        default=None,
        help="ISO дата нижней границы публикации, напр. 2025-01-01 (только API)",
    )
    args = parser.parse_args()

    queries = SEARCH_QUERIES
    if args.role:
        wanted_roles = set(args.role)
        queries = [q for q in queries if q.role_group in wanted_roles]
    if args.query:
        wanted = set(args.query)
        queries = [q for q in queries if q.text in wanted]
    if not queries:
        raise SystemExit("Нет запросов после фильтров --query/--role")

    prefer_html: Optional[bool]
    if args.html:
        prefer_html = True
    elif args.try_api is True:
        prefer_html = False
    elif args.try_api is False:
        prefer_html = True
    else:
        prefer_html = None  # auto

    run_parser(
        queries=queries,
        limit_per_query=args.limit_per_query,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
        prefer_html=prefer_html,
        date_from=args.date_from,
    )


if __name__ == "__main__":
    main()
