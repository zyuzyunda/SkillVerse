"""Импорт исторического среза hh.ru (формат helper: HHRU_Объединённый_датасет.csv)."""

from __future__ import annotations

import argparse
import ast
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from src.db.models import ParseRun, Vacancy, VacancySkill
from src.db.session import Base, SessionLocal, engine
from src.market.seed_from_csv import (
    DS_ML_KEYWORDS,
    NON_RUSSIA_MARKERS,
    _is_russia,
    _role_group,
)

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "HHRU_united.csv"

ALLOWED_SEARCH_KEYWORDS = {
    "data scientist",
    "machine learning engineer",
    "data analyst, data scientist",
}


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # 2025-03-23T12:00:00+0300 already covered; try replacing colonless tz
    try:
        if re.search(r"[+-]\d{4}$", value):
            value2 = value[:-2] + ":" + value[-2:]
            return datetime.strptime(value2, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        pass
    return None


def _parse_skills(raw: str) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except (ValueError, SyntaxError):
        pass
    return [p.strip() for p in re.split(r"[,;|]", raw) if p.strip()]


def _is_ds_ml_row(name: str, search_keyword: str) -> bool:
    blob = f"{name} {search_keyword}".lower()
    kw = (search_keyword or "").strip().lower()
    if kw in ALLOWED_SEARCH_KEYWORDS:
        return True
    return any(k in blob for k in DS_ML_KEYWORDS)


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def import_hh_united(
    csv_path: Path,
    *,
    russia_only: bool = True,
    limit: Optional[int] = None,
) -> None:
    Base.metadata.create_all(bind=engine)

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    filtered = []
    for row in rows:
        name = row.get("name") or ""
        keyword = row.get("search_keyword") or ""
        city = row.get("city") or ""
        if not _is_ds_ml_row(name, keyword):
            continue
        if russia_only and not _is_russia(city):
            continue
        filtered.append(row)
        if limit is not None and len(filtered) >= limit:
            break

    with SessionLocal() as session:
        run = ParseRun(
            status="running",
            queries=[
                {
                    "source": str(csv_path),
                    "filter": "ds_ml_russia_helper_hh",
                    "period": "2025-03..2025-04",
                }
            ],
            notes="seed_from_hh_united",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        created = 0
        updated = 0
        skipped = 0

        for row in filtered:
            hh_id = str(row.get("id") or "").strip()
            if not hh_id:
                skipped += 1
                continue

            name = row.get("name") or "unknown"
            keyword = row.get("search_keyword") or "helper_hh"
            role_group = _role_group(name, keyword)
            description_html = row.get("description") or None
            skills = _parse_skills(row.get("key_skills") or "")

            salary_from = row.get("salary_from") or None
            salary_to = row.get("salary_to") or None
            try:
                salary_from_f = float(salary_from) if salary_from not in (None, "") else None
            except ValueError:
                salary_from_f = None
            try:
                salary_to_f = float(salary_to) if salary_to not in (None, "") else None
            except ValueError:
                salary_to_f = None

            payload = {
                "hh_id": hh_id,
                "name": name,
                "description_html": description_html,
                "description_text": _strip_html(description_html) if description_html else None,
                "employer_id": None,
                "employer_name": row.get("company"),
                "area_id": None,
                "area_name": row.get("city"),
                "experience_id": None,
                "experience_name": row.get("experience"),
                "employment_name": row.get("employment"),
                "schedule_name": None,
                "salary_from": salary_from_f,
                "salary_to": salary_to_f,
                "salary_currency": row.get("currency") or None,
                "salary_gross": None,
                "published_at": _parse_dt(row.get("published_at") or ""),
                "created_at_hh": None,
                "archived": False,
                "alternate_url": row.get("url"),
                "search_query": keyword,
                "role_group": role_group,
                "raw_json": dict(row),
                "parse_run_id": run.id,
            }

            existing = session.scalar(select(Vacancy).where(Vacancy.hh_id == hh_id))
            # также не дублировать, если раньше импортировали как seed_{id}
            seed_existing = session.scalar(
                select(Vacancy).where(Vacancy.hh_id == f"seed_{hh_id}")
            )

            if existing is None and seed_existing is not None:
                # апгрейд seed-записи до реального hh_id
                existing = seed_existing
                existing.hh_id = hh_id

            seen: set[str] = set()
            unique_skills: list[str] = []
            for skill in skills:
                key = skill.lower()
                if key not in seen:
                    seen.add(key)
                    unique_skills.append(skill)

            if existing is None:
                vacancy = Vacancy(**payload)
                session.add(vacancy)
                session.flush()
                created += 1
            else:
                vacancy = existing
                for k, v in payload.items():
                    setattr(vacancy, k, v)
                vacancy.skills = [s for s in vacancy.skills if s.source != "hh_key_skills"]
                updated += 1

            for skill_name in unique_skills:
                vacancy.skills.append(
                    VacancySkill(
                        skill_name=skill_name,
                        source="hh_key_skills",
                        skill_type="tools",
                    )
                )

        run.status = "completed"
        run.vacancies_fetched = len(filtered)
        run.vacancies_upserted = created
        run.finished_at = datetime.now(timezone.utc)
        run.notes = (
            f"seed_from_hh_united created={created} updated={updated} skipped={skipped}"
        )
        session.commit()
        print(
            f"Импорт helper HH: candidates={len(filtered)}, created={created}, "
            f"updated={updated}, skipped={skipped}, run_id={run.id}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import historical HH united dataset into Postgres"
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-non-russia", action="store_true")
    args = parser.parse_args()
    if not args.csv.exists():
        raise SystemExit(f"CSV не найден: {args.csv}")
    import_hh_united(
        args.csv,
        russia_only=not args.include_non_russia,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
