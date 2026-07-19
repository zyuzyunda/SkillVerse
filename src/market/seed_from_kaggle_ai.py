"""Импорт Kaggle AI Jobs (2020–2026) в PostgreSQL.

Отдельный data_source=kaggle_ai, published_at = 1 января posted_year (UTC).
Навыки стыкуются по порядку строк (в датасете job_id частично расходится).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import delete, select, text
from tqdm import tqdm

from src.db.models import ParseRun, Vacancy, VacancySkill
from src.db.session import Base, SessionLocal, engine

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "archive (18)"

TITLE_TO_ROLE: dict[str, str] = {
    "Data Scientist": "data_science",
    "Data Analyst": "data_science",
    "Machine Learning Engineer": "ml_engineering",
    "MLOps Engineer": "mlops",
    "AI Researcher": "rnd",
    "Applied Scientist": "rnd",
}


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE vacancies
                ADD COLUMN IF NOT EXISTS data_source VARCHAR(32) DEFAULT 'hh'
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_vacancies_data_source
                ON vacancies (data_source)
                """
            )
        )


def _role_group(title: str) -> str:
    return TITLE_TO_ROLE.get(title, "ml_ai")


def _load_frames(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    jobs = pd.read_csv(data_dir / "ai_jobs.csv")
    skills = pd.read_csv(data_dir / "skills_demand.csv")
    if len(jobs) == 0:
        raise SystemExit(f"Пустой ai_jobs.csv в {data_dir}")

    # skill job_id часто ≠ jobs.job_id, но порядок уникальных id совпадает
    skill_order = list(dict.fromkeys(skills["job_id"].tolist()))
    if len(skill_order) != len(jobs):
        raise SystemExit(
            f"Несовпадение числа id: jobs={len(jobs)}, skill_ids={len(skill_order)}"
        )
    id_map = dict(zip(skill_order, jobs["job_id"].tolist()))
    skills = skills.copy()
    skills["job_id_resolved"] = skills["job_id"].map(id_map)
    if skills["job_id_resolved"].isna().any():
        raise SystemExit("Не удалось сопоставить skills.job_id → ai_jobs.job_id")

    skills_by_job: dict[str, list[str]] = (
        skills.groupby("job_id_resolved")["skill"]
        .apply(lambda s: sorted({str(x).strip() for x in s if str(x).strip()}))
        .to_dict()
    )
    jobs = jobs.copy()
    jobs["skills"] = jobs["job_id"].map(lambda j: skills_by_job.get(j, []))
    return jobs, skills


def seed_kaggle(
    *,
    data_dir: Path = DEFAULT_DIR,
    limit: Optional[int] = None,
    replace: bool = True,
) -> dict:
    ensure_schema()
    jobs, _ = _load_frames(data_dir)
    if limit is not None:
        jobs = jobs.head(limit)

    with SessionLocal() as session:
        if replace:
            old_ids = list(
                session.scalars(
                    select(Vacancy.id).where(Vacancy.data_source == "kaggle_ai")
                ).all()
            )
            if old_ids:
                session.execute(delete(VacancySkill).where(VacancySkill.vacancy_id.in_(old_ids)))
                session.execute(delete(Vacancy).where(Vacancy.id.in_(old_ids)))
                session.flush()

        run = ParseRun(
            status="running",
            queries=[{"source": "kaggle_ai", "dir": str(data_dir), "rows": len(jobs)}],
            notes="kaggle_ai seed",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        created = 0
        updated = 0
        skill_links = 0

        for _, row in tqdm(jobs.iterrows(), total=len(jobs), desc="kaggle_ai"):
            external_id = f"kaggle:{row['job_id']}"
            year = int(row["posted_year"])
            published = datetime(year, 1, 1, tzinfo=timezone.utc)
            role = _role_group(str(row["job_title"]))
            skills = list(row["skills"] or [])

            payload = {
                "hh_id": external_id,
                "name": str(row["job_title"]),
                "description_html": None,
                "description_text": (
                    f"{row['job_title']} | {row.get('industry')} | "
                    f"{row.get('country')} | {row.get('experience_level')}"
                ),
                "employer_id": None,
                "employer_name": str(row.get("company_type") or "") or None,
                "area_id": None,
                "area_name": str(row.get("country") or "") or None,
                "experience_id": str(row.get("experience_level") or "") or None,
                "experience_name": str(row.get("experience_level") or "") or None,
                "employment_name": str(row.get("employment_type") or "") or None,
                "schedule_name": str(row.get("remote_type") or "") or None,
                "salary_from": float(row["salary_min_usd"]) if pd.notna(row["salary_min_usd"]) else None,
                "salary_to": float(row["salary_max_usd"]) if pd.notna(row["salary_max_usd"]) else None,
                "salary_currency": "USD",
                "salary_gross": None,
                "published_at": published,
                "created_at_hh": published,
                "archived": False,
                "alternate_url": None,
                "search_query": f"kaggle:{row['job_title']}",
                "role_group": role,
                "data_source": "kaggle_ai",
                "raw_json": {
                    "source": "kaggle_ai",
                    "job_id": row["job_id"],
                    "posted_year": year,
                    "country": row.get("country"),
                    "city": row.get("city"),
                    "company_size": row.get("company_size"),
                    "industry": row.get("industry"),
                    "min_experience_years": int(row["min_experience_years"])
                    if pd.notna(row.get("min_experience_years"))
                    else None,
                },
                "parse_run_id": run.id,
            }

            existing = session.scalar(select(Vacancy).where(Vacancy.hh_id == external_id))
            if existing is None:
                vac = Vacancy(**payload)
                session.add(vac)
                session.flush()
                created += 1
            else:
                vac = existing
                for k, v in payload.items():
                    setattr(vac, k, v)
                vac.skills = [s for s in vac.skills if s.source != "kaggle_ai"]
                updated += 1

            for skill_name in skills:
                vac.skills.append(
                    VacancySkill(
                        skill_name=skill_name,
                        source="kaggle_ai",
                        skill_type="tools",
                    )
                )
                skill_links += 1

            if (created + updated) % 500 == 0:
                session.commit()

        run.status = "completed"
        run.vacancies_fetched = created + updated
        run.vacancies_upserted = created
        run.finished_at = datetime.now(timezone.utc)
        run.notes = f"kaggle_ai created={created} updated={updated} skills={skill_links}"
        session.commit()

        stats = {
            "created": created,
            "updated": updated,
            "skill_links": skill_links,
            "run_id": run.id,
            "years": sorted({int(y) for y in jobs["posted_year"].unique()}),
        }
        print("Kaggle seed готов:", stats)
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Kaggle AI jobs into Postgres")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Не удалять предыдущие kaggle_ai записи перед импортом",
    )
    args = parser.parse_args()
    seed_kaggle(data_dir=args.dir, limit=args.limit, replace=not args.keep_existing)


if __name__ == "__main__":
    main()
