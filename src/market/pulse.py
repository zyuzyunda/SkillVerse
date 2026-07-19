"""Пульс рынка труда: обзор вакансий и трендов по годам (без early/late)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import GraphEdge, GraphNode, SkillCanonical, Vacancy
from src.db.session import SessionLocal


@dataclass
class MarketSkillRow:
    skill_name: str
    skill_norm: str
    cluster: str
    role_group: str
    support: float
    trend: float
    support_by_year: dict[str, float]
    trend_from_year: Optional[int]
    trend_to_year: Optional[int]
    count: int
    role_vacancies: int
    temporal_source: str = "none"
    trend_slope: float = 0.0
    support_by_year_by_source: Optional[dict[str, dict[str, float]]] = None
    trend_by_source: Optional[dict[str, float]] = None
    trend_method: str = "none"


def market_overview(session: Session, *, sources: Optional[list[str]] = None) -> dict[str, Any]:
    q = select(Vacancy)
    if sources:
        q = q.where(Vacancy.data_source.in_(sources))
    # counts
    count_q = select(func.count()).select_from(Vacancy)
    if sources:
        count_q = count_q.where(Vacancy.data_source.in_(sources))
    n_vac = session.scalar(count_q) or 0

    by_role_q = (
        select(Vacancy.role_group, func.count())
        .group_by(Vacancy.role_group)
        .order_by(func.count().desc())
    )
    by_source_q = (
        select(Vacancy.data_source, func.count())
        .group_by(Vacancy.data_source)
        .order_by(func.count().desc())
    )
    by_year_q = (
        select(func.extract("year", Vacancy.published_at), func.count())
        .where(Vacancy.published_at.is_not(None))
        .group_by(func.extract("year", Vacancy.published_at))
        .order_by(func.extract("year", Vacancy.published_at))
    )
    if sources:
        by_role_q = by_role_q.where(Vacancy.data_source.in_(sources))
        by_source_q = by_source_q.where(Vacancy.data_source.in_(sources))
        by_year_q = by_year_q.where(Vacancy.data_source.in_(sources))

    by_role = dict(session.execute(by_role_q).all())
    by_source = dict(session.execute(by_source_q).all())
    by_year = {
        int(y): int(c) for y, c in session.execute(by_year_q).all() if y is not None
    }

    n_skills = (
        session.scalar(select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "skill"))
        or 0
    )
    n_clusters = (
        session.scalar(
            select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "cluster")
        )
        or 0
    )
    n_edges = (
        session.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
        )
        or 0
    )
    return {
        "vacancies": n_vac,
        "by_role": by_role,
        "by_source": by_source,
        "by_year": by_year,
        "canonical_skills": n_skills,
        "clusters": n_clusters,
        "role_skill_edges": n_edges,
        "year_min": min(by_year) if by_year else None,
        "year_max": max(by_year) if by_year else None,
    }


def load_market_skills(
    session: Session,
    *,
    role_group: Optional[str] = None,
    min_support: float = 0.1,
) -> list[MarketSkillRow]:
    q = select(GraphEdge).where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
    if role_group:
        q = q.where(GraphEdge.source_key == f"role:{role_group}")
    edges = session.scalars(q).all()
    canon = {c.name_norm: c for c in session.scalars(select(SkillCanonical)).all()}

    rows: list[MarketSkillRow] = []
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        skill_norm = props.get("skill_norm") or e.target_key.replace("skill:", "", 1)
        skill = canon.get(skill_norm)
        by_year = props.get("support_by_year") or {}
        if isinstance(by_year, dict):
            by_year = {str(k): float(v) for k, v in by_year.items()}
        else:
            by_year = {}
        by_year_src = props.get("support_by_year_by_source") or {}
        if isinstance(by_year_src, dict):
            by_year_src = {
                str(src): {str(y): float(v) for y, v in (series or {}).items()}
                for src, series in by_year_src.items()
                if isinstance(series, dict)
            }
        else:
            by_year_src = {}
        trend_by_src = props.get("trend_by_source") or {}
        if isinstance(trend_by_src, dict):
            trend_by_src = {str(k): float(v) for k, v in trend_by_src.items()}
        else:
            trend_by_src = {}
        rows.append(
            MarketSkillRow(
                skill_name=str(props.get("skill_name") or (skill.name if skill else skill_norm)),
                skill_norm=skill_norm,
                cluster=(skill.cluster_name if skill else None) or "Other",
                role_group=str(props.get("role_group") or e.source_key.replace("role:", "", 1)),
                support=round(support, 4),
                trend=round(float(props.get("trend") or 0), 4),
                support_by_year=by_year,
                trend_from_year=props.get("trend_from_year"),
                trend_to_year=props.get("trend_to_year"),
                count=int(props.get("count") or 0),
                role_vacancies=int(props.get("role_vacancies") or 0),
                temporal_source=str(props.get("temporal_source") or "none"),
                trend_slope=round(float(props.get("trend_slope") or props.get("trend") or 0), 4),
                support_by_year_by_source=by_year_src or None,
                trend_by_source=trend_by_src or None,
                trend_method=str(props.get("trend_method") or "none"),
            )
        )
    rows.sort(key=lambda r: (r.support, r.trend), reverse=True)
    return rows


def top_skills_across_roles(
    rows: list[MarketSkillRow],
    *,
    top: int = 20,
) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        cur = agg.get(r.skill_norm)
        if cur is None:
            agg[r.skill_norm] = {
                "skill": r.skill_name,
                "cluster": r.cluster,
                "max_support": r.support,
                "avg_trend": r.trend,
                "roles": 1,
                "best_role": r.role_group,
                "support_by_year": dict(r.support_by_year),
            }
        else:
            cur["roles"] += 1
            cur["avg_trend"] = (cur["avg_trend"] * (cur["roles"] - 1) + r.trend) / cur["roles"]
            if r.support > cur["max_support"]:
                cur["max_support"] = r.support
                cur["best_role"] = r.role_group
                cur["skill"] = r.skill_name
                cur["cluster"] = r.cluster
            # merge years (max)
            for y, v in r.support_by_year.items():
                cur["support_by_year"][y] = max(float(cur["support_by_year"].get(y, 0)), float(v))
    out = sorted(agg.values(), key=lambda x: (x["max_support"], x["avg_trend"]), reverse=True)
    return out[:top]


def rising_falling(
    rows: list[MarketSkillRow],
    *,
    top: int = 15,
    min_abs_trend: float = 0.05,
) -> tuple[list[MarketSkillRow], list[MarketSkillRow]]:
    rising = [r for r in rows if r.trend >= min_abs_trend]
    falling = [r for r in rows if r.trend <= -min_abs_trend]
    rising.sort(key=lambda r: (r.trend, r.support), reverse=True)
    falling.sort(key=lambda r: (r.trend, -r.support))
    return rising[:top], falling[:top]


def cluster_demand(rows: list[MarketSkillRow]) -> list[dict[str, Any]]:
    by_cluster: dict[str, list[MarketSkillRow]] = defaultdict(list)
    for r in rows:
        if r.cluster == "Other":
            continue
        by_cluster[r.cluster].append(r)
    out = []
    for cluster, items in by_cluster.items():
        supports = [i.support for i in items]
        trends = [i.trend for i in items]
        out.append(
            {
                "cluster": cluster,
                "skills": len({i.skill_norm for i in items}),
                "max_support": round(max(supports), 4),
                "avg_support": round(sum(supports) / len(supports), 4),
                "avg_trend": round(sum(trends) / len(trends), 4),
                "top_skill": max(items, key=lambda x: x.support).skill_name,
            }
        )
    out.sort(key=lambda x: (x["max_support"], x["avg_trend"]), reverse=True)
    return out


def role_cluster_matrix(
    session: Session,
    *,
    min_support: float = 0.15,
) -> list[dict[str, Any]]:
    edges = session.scalars(
        select(GraphEdge).where(GraphEdge.edge_type == "ROLE_REQUIRES_CLUSTER")
    ).all()
    rows = []
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        rows.append(
            {
                "role": props.get("role_group") or e.source_key.replace("role:", "", 1),
                "cluster": props.get("cluster") or e.target_key,
                "support": round(support, 4),
                "trend": round(float(props.get("trend") or 0), 4),
                "top_skill": props.get("top_skill") or "",
            }
        )
    return rows


def get_pulse(
    *,
    role_group: Optional[str] = None,
    sources: Optional[list[str]] = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        overview = market_overview(session, sources=sources)
        all_rows = load_market_skills(session, role_group=None, min_support=0.1)
        role_rows = (
            load_market_skills(session, role_group=role_group, min_support=0.1)
            if role_group
            else all_rows
        )
        rising, falling = rising_falling(role_rows)
        return {
            "overview": overview,
            "role_group": role_group,
            "top_skills": top_skills_across_roles(all_rows if role_group is None else role_rows),
            "role_top": [
                {
                    "skill": r.skill_name,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "from_year": r.trend_from_year,
                    "to_year": r.trend_to_year,
                    "support_by_year": r.support_by_year,
                }
                for r in role_rows[:25]
            ],
            "rising": [
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "from_year": r.trend_from_year,
                    "to_year": r.trend_to_year,
                    "support_by_year": r.support_by_year,
                }
                for r in rising
            ],
            "falling": [
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "from_year": r.trend_from_year,
                    "to_year": r.trend_to_year,
                    "support_by_year": r.support_by_year,
                }
                for r in falling
            ],
            "clusters": cluster_demand(role_rows),
            "role_cluster": role_cluster_matrix(session),
        }
