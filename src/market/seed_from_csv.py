"""Импорт рыночных вакансий из CSV (Experiments) в PostgreSQL.

Используется как стартовый датасет, пока живой API hh.ru недоступен с текущего IP.
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select

from src.db.models import ParseRun, Vacancy, VacancySkill
from src.db.session import Base, SessionLocal, engine

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "final_vacancies.csv"

DS_ML_KEYWORDS = (
    "data scientist",
    "data science",
    "machine learning",
    "ml engineer",
    "ml-инженер",
    "mlops",
    "ai engineer",
    "deep learning",
    "nlp",
    "computer vision",
    "llm",
    "research scientist",
    "ai researcher",
    "research engineer",
    "r&d",
    "дата-сайентист",
    "машинного обучения",
    "искусственного интеллекта",
    "нейросет",
    "agent",
    "агент",
)

NON_RUSSIA_MARKERS = (
    "алматы",
    "астана",
    "шымкент",
    "казахстан",
    "минск",
    "беларусь",
    "ташкент",
    "баку",
    "ереван",
    "киев",
    "ukraine",
    "қазақстан",
    "тбилиси",
    "грузия",
    "сербия",
    "белград",
    "ереван",
    "бишкек",
    "кишин",
    "рим",
    "берлин",
    "амстердам",
    "dubai",
    "дубай",
    "cyprus",
    "кипр",
    "yerevan",
    "армения",
    "черногория",
    "литва",
    "латвия",
    "эстония",
    "польша",
    "турция",
    "израиль",
)


def _split_skills(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,;|/]", raw)
    return [p.strip() for p in parts if p.strip()]


def _is_ds_ml(title: str, role_name: str = "") -> bool:
    blob = f"{title} {role_name}".lower()
    return any(k in blob for k in DS_ML_KEYWORDS)


def _is_russia(region: str) -> bool:
    region_l = (region or "").lower().strip()
    if not region_l:
        return True
    if any(m in region_l for m in NON_RUSSIA_MARKERS):
        return False
    return True


def _parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _role_group(title: str, role_name: str) -> str:
    blob = f"{title} {role_name}".lower()
    if "mlops" in blob or "dataops" in blob:
        return "mlops"
    if any(x in blob for x in ("llm", "agent", "агент", "prompt")):
        return "llm_agents"
    if any(x in blob for x in ("nlp", "language")):
        return "nlp"
    if any(x in blob for x in ("computer vision", "cv engineer", "видео", "vision")):
        return "computer_vision"
    if any(x in blob for x in ("research", "исследоват", "r&d", "rnd")):
        return "rnd"
    if any(x in blob for x in ("ai engineer", "deep learning", "нейросет")):
        return "ai_engineering"
    if any(x in blob for x in ("ml engineer", "ml-инженер", "machine learning")):
        return "ml_engineering"
    return "data_science"


def import_csv(
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
        title = row.get("job_title") or ""
        role = row.get("role_name") or ""
        region = row.get("region") or ""
        if not _is_ds_ml(title, role):
            continue
        if russia_only and not _is_russia(region):
            continue
        filtered.append(row)
        if limit is not None and len(filtered) >= limit:
            break

    with SessionLocal() as session:
        run = ParseRun(
            status="running",
            queries=[{"source": str(csv_path), "filter": "ds_ml_russia" if russia_only else "ds_ml"}],
            notes="seed_from_csv",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        created = 0
        updated = 0
        for row in filtered:
            hh_id = str(row.get("vacancy_id") or "").strip()
            if not hh_id:
                continue
            title = row.get("job_title") or "unknown"
            role_group = _role_group(title, row.get("role_name") or "")
            payload = {
                "hh_id": f"seed_{hh_id}",
                "name": title,
                "description_html": None,
                "description_text": row.get("text") or None,
                "employer_id": None,
                "employer_name": row.get("company_name"),
                "area_id": None,
                "area_name": row.get("region"),
                "experience_id": None,
                "experience_name": row.get("experience_required"),
                "employment_name": None,
                "schedule_name": "Удалёнка" if str(row.get("is_remote")).lower() == "true" else None,
                "salary_from": None,
                "salary_to": None,
                "salary_currency": None,
                "salary_gross": None,
                "published_at": _parse_date(row.get("date") or ""),
                "created_at_hh": None,
                "archived": False,
                "alternate_url": None,
                "search_query": "seed:final_vacancies",
                "role_group": role_group,
                "data_source": "csv_seed",
                "raw_json": dict(row),
                "parse_run_id": run.id,
            }

            existing = session.scalar(select(Vacancy).where(Vacancy.hh_id == payload["hh_id"]))
            skills: list[tuple[str, str, str]] = []
            seen_skills: set[tuple[str, str]] = set()
            for skill in _split_skills(row.get("hard_skills")):
                key = (skill.lower(), "seed_hard")
                if key not in seen_skills:
                    seen_skills.add(key)
                    skills.append((skill, "seed_hard", "competencies"))
            for skill in _split_skills(row.get("soft_skills")):
                key = (skill.lower(), "seed_soft")
                if key not in seen_skills:
                    seen_skills.add(key)
                    skills.append((skill, "seed_soft", "soft_skills"))
            for skill in _split_skills(row.get("tool_name")):
                key = (skill.lower(), "seed_tools")
                if key not in seen_skills:
                    seen_skills.add(key)
                    skills.append((skill, "seed_tools", "tools"))

            if existing is None:
                vacancy = Vacancy(**payload)
                session.add(vacancy)
                session.flush()
                created += 1
            else:
                vacancy = existing
                for k, v in payload.items():
                    setattr(vacancy, k, v)
                vacancy.skills.clear()
                updated += 1

            for name, source, skill_type in skills:
                vacancy.skills.append(
                    VacancySkill(skill_name=name, source=source, skill_type=skill_type)
                )

        run.status = "completed"
        run.vacancies_fetched = len(filtered)
        run.vacancies_upserted = created
        run.finished_at = datetime.now(timezone.utc)
        run.notes = f"seed_from_csv created={created} updated={updated}"
        session.commit()
        print(
            f"Импорт завершён: candidates={len(filtered)}, created={created}, "
            f"updated={updated}, run_id={run.id}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed vacancies from Experiments CSV into Postgres")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-non-russia", action="store_true")
    args = parser.parse_args()
    if not args.csv.exists():
        raise SystemExit(f"CSV не найден: {args.csv}")
    import_csv(args.csv, russia_only=not args.include_non_russia, limit=args.limit)


if __name__ == "__main__":
    main()
