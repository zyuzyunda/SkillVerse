"""Сбор фактического контекста из БД для ответов ассистента."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select

from src.db.models import Department, Employee, GraphEdge
from src.db.session import SessionLocal
from src.org.recommendations import (
    recommend_courses,
    recommend_mobility,
    recommend_training,
)
from src.org.risk_metrics import compute_skill_risks
from src.org.skill_gaps import compute_skill_gaps, list_role_groups


def _detect_role(question: str, available: list[str]) -> str:
    q = question.lower()
    mapping = [
        ("mlops", "mlops"),
        ("llm", "llm_agents"),
        ("agent", "llm_agents"),
        ("nlp", "nlp"),
        ("computer vision", "computer_vision"),
        ("cv ", "computer_vision"),
        ("research", "rnd"),
        ("rnd", "rnd"),
        ("ml engineer", "ml_engineering"),
        ("machine learning", "ml_engineering"),
        ("data science", "data_science"),
        ("дата-саент", "data_science"),
        ("дата саент", "data_science"),
    ]
    for needle, role in mapping:
        if needle in q and role in available:
            return role
    return "data_science" if "data_science" in available else (available[0] if available else "data_science")


def build_context(
    question: str,
    *,
    role_group: Optional[str] = None,
    department_code: Optional[str] = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        roles = list_role_groups(session)
        role = role_group or _detect_role(question, roles)

        gaps = compute_skill_gaps(
            session, role_group=role, department_code=department_code, min_market_support=0.2
        )[:12]
        risks = compute_skill_risks(
            session, role_group=role, department_code=department_code, min_market_support=0.15
        )
        spof = [r for r in risks if r.is_spof][:8]
        critical = [r for r in risks if r.is_critical][:8]
        gap_objs, trainees = recommend_training(
            session, role_group=role, department_code=department_code, top_skills=5, top_people=8
        )
        courses = recommend_courses(
            session, role_group=role, department_code=department_code, top=5
        )
        mobility = recommend_mobility(session, target_role=role, top=6)

        growing = [
            {
                "skill": g.skill_name,
                "support": g.market_support,
                "trend": g.trend,
                "gap": g.gap,
            }
            for g in gaps
            if g.trend > 0.2
        ][:8]

        n_emp = session.scalar(
            select(func.count()).select_from(Employee).where(Employee.is_active.is_(True))
        )
        n_dept = session.scalar(select(func.count()).select_from(Department))
        n_market = session.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
        )

        return {
            "role_group": role,
            "department_code": department_code,
            "stats": {
                "employees": n_emp,
                "departments": n_dept,
                "market_edges": n_market,
                "available_roles": roles,
            },
            "gaps": [
                {
                    "skill": g.skill_name,
                    "market": g.market_support,
                    "org": g.org_coverage,
                    "gap": g.gap,
                    "trend": g.trend,
                }
                for g in (gap_objs or gaps[:5])
            ],
            "trainees": [
                {
                    "name": t.full_name,
                    "position": t.position,
                    "missing": t.missing_skills,
                    "score": t.score,
                    "reason": t.reason,
                }
                for t in trainees
            ],
            "courses": [
                {
                    "title": c.title,
                    "code": c.course_code,
                    "hours": c.duration_hours,
                    "skills": c.skills_covered,
                    "candidates": c.candidates_count,
                    "reason": c.reason,
                }
                for c in courses
            ],
            "mobility": [
                {
                    "name": m.full_name,
                    "from": m.current_position,
                    "from_role": m.current_role,
                    "overlap": m.overlap,
                    "missing": m.missing_skills,
                    "reason": m.reason,
                }
                for m in mobility
            ],
            "growing_skills": growing,
            "spof": [
                {
                    "skill": r.skill_name,
                    "market": r.market_support,
                    "holders": r.holders,
                    "who": r.holder_names,
                    "risk": r.risk_score,
                }
                for r in spof
            ],
            "critical": [
                {
                    "skill": r.skill_name,
                    "market": r.market_support,
                    "org": r.coverage,
                    "holders": r.holders,
                    "risk": r.risk_score,
                }
                for r in critical
            ],
        }


def context_to_prompt_block(ctx: dict[str, Any]) -> str:
    lines = [
        f"Роль анализа: {ctx['role_group']}",
        f"Подразделение-фильтр: {ctx.get('department_code') or 'все'}",
        f"Сотрудников в компании: {ctx['stats']['employees']}",
        "",
        "Дефициты (skill | market | org | gap | trend):",
    ]
    for g in ctx["gaps"]:
        lines.append(
            f"- {g['skill']}: market={g['market']:.2f}, org={g['org']:.2f}, "
            f"gap={g['gap']:.2f}, trend={g['trend']:.2f}"
        )
    lines.append("")
    lines.append("Кандидаты на обучение:")
    for t in ctx["trainees"][:6]:
        lines.append(f"- {t['name']} ({t['position']}): {', '.join(t['missing'][:4])}")
    lines.append("")
    lines.append("Курсы:")
    for c in ctx["courses"][:5]:
        lines.append(
            f"- {c['title']} [{c['code']}], {c['hours']}ч, skills={', '.join(c['skills'])}, "
            f"candidates={c['candidates']}"
        )
    lines.append("")
    lines.append("Мобильность:")
    for m in ctx["mobility"][:5]:
        lines.append(
            f"- {m['name']} ({m['from']} / {m['from_role']}): overlap={m['overlap']:.0%}"
        )
    lines.append("")
    lines.append("SPOF (1–2 носителя):")
    for r in ctx.get("spof", [])[:6]:
        who = ", ".join(r["who"]) if r.get("who") else "—"
        lines.append(
            f"- {r['skill']}: market={r['market']:.2f}, holders={r['holders']}, who={who}"
        )
    lines.append("")
    lines.append("Critical skills:")
    for r in ctx.get("critical", [])[:6]:
        lines.append(
            f"- {r['skill']}: market={r['market']:.2f}, org={r['org']:.2f}, "
            f"holders={r['holders']}, risk={r['risk']:.2f}"
        )
    lines.append("")
    lines.append("Растущие навыки:")
    for g in ctx["growing_skills"][:6]:
        lines.append(f"- {g['skill']}: support={g['support']:.2f}, trend={g['trend']:.2f}")
    return "\n".join(lines)
