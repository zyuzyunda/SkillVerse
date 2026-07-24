#!/usr/bin/env python3
"""Обновить блок «Ориентиры по данным» в docs/report_technical_summary.md."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select, text

from src.db.models import GraphEdge, KgQuarantine, SkillCanonical, Vacancy
from src.db.session import SessionLocal

REPORT = Path(__file__).resolve().parents[1] / "docs" / "report_technical_summary.md"


def main() -> None:
    with SessionLocal() as s:
        hh = s.scalar(
            select(func.count()).select_from(Vacancy).where(Vacancy.data_source == "hh")
        ) or 0
        by_m = dict(
            s.execute(
                text(
                    """
                    select section_meta->>'method', count(*)
                    from vacancies
                    where data_source = 'hh' and section_meta ? 'method'
                    group by 1
                    """
                )
            ).all()
        )
        llm_done = (
            s.execute(
                text(
                    """
                    select count(*) from vacancy_skills
                    where skill_name = '__llm_done__' and source = 'llm_extract'
                    """
                )
            ).scalar()
            or 0
        )
        canon = s.scalar(select(func.count()).select_from(SkillCanonical)) or 0
        rs = (
            s.scalar(
                select(func.count())
                .select_from(GraphEdge)
                .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
            )
            or 0
        )
        co = (
            s.scalar(
                select(func.count())
                .select_from(GraphEdge)
                .where(GraphEdge.edge_type == "SKILL_CO_OCCURS")
            )
            or 0
        )
        q = (
            s.scalar(
                select(func.count())
                .select_from(KgQuarantine)
                .where(KgQuarantine.item_type == "summary")
            )
            or 0
        )
        roles = s.execute(
            select(Vacancy.role_group, func.count())
            .where(Vacancy.data_source == "hh")
            .group_by(Vacancy.role_group)
            .order_by(func.count().desc())
        ).all()
        role_s = ", ".join(f"{r}={int(n)}" for r, n in roles[:8])

    text_block = (
        "Корпус hh после полного пайплайна "
        "(Parser → Splitter → Extractor → Graph/Governance), "
        "окно date_from ≥ 2025-01-01:\n\n"
        f"- вакансий: **{hh}**; роли (топ): {role_s};\n"
        f"- секции: rules={by_m.get('rules', 0)}, llm={by_m.get('llm', 0)}, "
        f"fallback_full={by_m.get('fallback_full', 0)};\n"
        f"- LLM-extract завершён для **{llm_done}** вакансий;\n"
        f"- market-KG: **{canon}** canonical, **{rs}** ROLE_REQUIRES_SKILL, "
        f"**{co}** SKILL_CO_OCCURS; сводок карантина: {q}.\n"
    )

    body = REPORT.read_text(encoding="utf-8")
    body2, n = re.subn(
        r"## Ориентиры по данным \(hh\.ru\)\n\n.*?(?=\n## )",
        "## Ориентиры по данным (hh.ru)\n\n" + text_block + "\n",
        body,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit(f"section replace failed n={n}")
    REPORT.write_text(body2, encoding="utf-8")
    print(text_block)
    print("updated", REPORT)


if __name__ == "__main__":
    main()
