"""Обзорный market-дашборд: Россия (hh) / Мир (Kaggle)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import SkillAlias, SkillCanonical, Vacancy, VacancySkill
from src.db.session import SessionLocal
from src.graph.normalize import normalize_text
from src.market.multiverse import career_bridges
from src.market.pulse import MarketSkillRow, load_market_skills, rising_falling


MARKET_MODES = {
    "russia": {
        "label": "Россия",
        "subtitle": "hh.ru · города и компании",
        "sources": ["hh"],
        "geo_kind": "city",
        "geo_title": "Города",
        "show_companies": True,
        "temporal_source": "hh",
    },
    "world": {
        "label": "Мир",
        "subtitle": "Kaggle AI Jobs · страны и длинные тренды",
        "sources": ["kaggle_ai"],
        "geo_kind": "country",
        "geo_title": "Страны",
        "show_companies": False,
        "temporal_source": "kaggle_ai",
    },
}


def _vac_filter(sources: list[str]):
    return Vacancy.data_source.in_(sources)


def pulse_kpis(session: Session, sources: list[str]) -> dict[str, Any]:
    n = int(session.scalar(select(func.count()).select_from(Vacancy).where(_vac_filter(sources))) or 0)
    n_dated = int(
        session.scalar(
            select(func.count())
            .select_from(Vacancy)
            .where(_vac_filter(sources), Vacancy.published_at.is_not(None))
        )
        or 0
    )
    n_skills_vac = int(
        session.scalar(
            select(func.count(func.distinct(Vacancy.id)))
            .select_from(Vacancy)
            .join(VacancySkill, VacancySkill.vacancy_id == Vacancy.id)
            .where(_vac_filter(sources))
        )
        or 0
    )
    n_emp = int(
        session.scalar(
            select(func.count(func.distinct(Vacancy.employer_name)))
            .where(_vac_filter(sources), Vacancy.employer_name.is_not(None))
        )
        or 0
    )
    n_geo = int(
        session.scalar(
            select(func.count(func.distinct(Vacancy.area_name)))
            .where(_vac_filter(sources), Vacancy.area_name.is_not(None))
        )
        or 0
    )
    n_roles = int(
        session.scalar(
            select(func.count(func.distinct(Vacancy.role_group))).where(_vac_filter(sources))
        )
        or 0
    )
    years = {
        int(y): int(c)
        for y, c in session.execute(
            select(func.extract("year", Vacancy.published_at), func.count())
            .where(_vac_filter(sources), Vacancy.published_at.is_not(None))
            .group_by(func.extract("year", Vacancy.published_at))
            .order_by(func.extract("year", Vacancy.published_at))
        ).all()
        if y is not None
    }
    by_role = {
        str(k): int(v)
        for k, v in session.execute(
            select(Vacancy.role_group, func.count())
            .where(_vac_filter(sources))
            .group_by(Vacancy.role_group)
            .order_by(func.count().desc())
        ).all()
    }
    # unique raw skills
    n_unique_skills = int(
        session.scalar(
            select(func.count(func.distinct(VacancySkill.skill_name)))
            .select_from(VacancySkill)
            .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
            .where(_vac_filter(sources))
        )
        or 0
    )
    return {
        "vacancies": n,
        "with_date": n_dated,
        "date_coverage": round(n_dated / n, 4) if n else 0.0,
        "with_skills": n_skills_vac,
        "skill_coverage": round(n_skills_vac / n, 4) if n else 0.0,
        "employers": n_emp,
        "geo_places": n_geo,
        "roles": n_roles,
        "unique_skills": n_unique_skills,
        "by_year": years,
        "by_role": by_role,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
    }


def geo_breakdown(session: Session, sources: list[str], *, top: int = 15) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Vacancy.area_name, func.count())
        .where(_vac_filter(sources), Vacancy.area_name.is_not(None))
        .group_by(Vacancy.area_name)
        .order_by(func.count().desc())
        .limit(top)
    ).all()
    total = sum(int(c) for _, c in rows) or 1
    return [
        {"place": str(name), "vacancies": int(c), "share": round(int(c) / total, 4)}
        for name, c in rows
    ]


def employer_breakdown(
    session: Session, sources: list[str], *, top: int = 15
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Vacancy.employer_name, func.count())
        .where(_vac_filter(sources), Vacancy.employer_name.is_not(None))
        .group_by(Vacancy.employer_name)
        .order_by(func.count().desc())
        .limit(top)
    ).all()
    total = sum(int(c) for _, c in rows) or 1
    return [
        {"employer": str(name), "vacancies": int(c), "share": round(int(c) / total, 4)}
        for name, c in rows
    ]


def top_skills_by_geo(
    session: Session,
    sources: list[str],
    place: str,
    *,
    top: int = 10,
) -> list[dict[str, Any]]:
    q = (
        select(VacancySkill.skill_name, func.count(func.distinct(Vacancy.id)))
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
        .where(_vac_filter(sources), Vacancy.area_name == place)
        .group_by(VacancySkill.skill_name)
        .order_by(func.count(func.distinct(Vacancy.id)).desc())
        .limit(top)
    )
    return [{"skill": str(name), "count": int(c)} for name, c in session.execute(q).all()]


def top_skills_by_employer(
    session: Session,
    sources: list[str],
    employer: str,
    *,
    top: int = 10,
) -> list[dict[str, Any]]:
    q = (
        select(VacancySkill.skill_name, func.count(func.distinct(Vacancy.id)))
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
        .where(_vac_filter(sources), Vacancy.employer_name == employer)
        .group_by(VacancySkill.skill_name)
        .order_by(func.count(func.distinct(Vacancy.id)).desc())
        .limit(top)
    )
    return [{"skill": str(name), "count": int(c)} for name, c in session.execute(q).all()]


def _local_trend(r: MarketSkillRow, temporal_source: str) -> float:
    by_src = r.support_by_year_by_source or {}
    series = by_src.get(temporal_source) or r.support_by_year or {}
    if not series:
        return float(r.trend or 0)
    years = sorted((int(y), float(v)) for y, v in series.items())
    if len(years) < 2:
        return 0.0
    if len(years) >= 4:
        return years[-1][1] - years[0][1]
    return years[-1][1] - years[-2][1]


def _with_source_trend(
    rows: list[MarketSkillRow], temporal_source: str
) -> list[MarketSkillRow]:
    out: list[MarketSkillRow] = []
    for r in rows:
        by_src = r.support_by_year_by_source or {}
        series = by_src.get(temporal_source)
        if not series or not any(float(v) > 0 for v in series.values()):
            continue
        support = max(float(v) for v in series.values())
        trend = _local_trend(r, temporal_source)
        years = sorted(int(y) for y in series)
        out.append(
            MarketSkillRow(
                skill_name=r.skill_name,
                skill_norm=r.skill_norm,
                cluster=r.cluster,
                role_group=r.role_group,
                support=round(support, 4),
                trend=round(trend, 4),
                support_by_year={str(k): float(v) for k, v in series.items()},
                trend_from_year=years[0] if len(years) >= 4 else (years[-2] if len(years) >= 2 else None),
                trend_to_year=years[-1] if years else None,
                count=r.count,
                role_vacancies=r.role_vacancies,
                temporal_source=temporal_source,
                support_by_year_by_source=r.support_by_year_by_source,
                trend_by_source=r.trend_by_source,
            )
        )
    return out


def skill_rarity(
    session: Session,
    sources: list[str],
    *,
    top: int = 15,
    max_share: float = 0.05,
    min_count: int = 3,
) -> list[dict[str, Any]]:
    """Редкие навыки: редко встречаются, но не единичные."""
    n_vac = int(
        session.scalar(
            select(func.count(func.distinct(Vacancy.id)))
            .select_from(Vacancy)
            .join(VacancySkill, VacancySkill.vacancy_id == Vacancy.id)
            .where(_vac_filter(sources))
        )
        or 0
    )
    if n_vac <= 0:
        return []
    rare_q = (
        select(VacancySkill.skill_name, func.count(func.distinct(Vacancy.id)))
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
        .where(_vac_filter(sources))
        .group_by(VacancySkill.skill_name)
        .having(func.count(func.distinct(Vacancy.id)) >= min_count)
        .order_by(func.count(func.distinct(Vacancy.id)).asc())
        .limit(200)
    )
    rows = session.execute(rare_q).all()
    out = []
    for name, c in rows:
        share = int(c) / n_vac
        if share <= max_share:
            out.append({"skill": str(name), "count": int(c), "share": round(share, 4)})
        if len(out) >= top:
            break
    return out


def unique_role_skills(
    rows: list[MarketSkillRow],
    *,
    top: int = 15,
    min_support: float = 0.15,
    max_other: float = 0.08,
) -> list[dict[str, Any]]:
    """Навык силён в одной роли и слаб в остальных."""
    by_skill: dict[str, list[MarketSkillRow]] = defaultdict(list)
    for r in rows:
        by_skill[r.skill_norm].append(r)
    out = []
    for norm, items in by_skill.items():
        best = max(items, key=lambda x: x.support)
        others = [x for x in items if x.role_group != best.role_group]
        other_max = max((x.support for x in others), default=0.0)
        if best.support >= min_support and other_max <= max_other:
            out.append(
                {
                    "skill": best.skill_name,
                    "role": best.role_group,
                    "support": best.support,
                    "trend": best.trend,
                    "other_max": round(other_max, 4),
                    "uniqueness": round(best.support - other_max, 4),
                    "cluster": best.cluster,
                }
            )
    out.sort(key=lambda x: x["uniqueness"], reverse=True)
    return out[:top]


def _skill_match_map(session: Session) -> dict[str, set[str]]:
    """canonical name_norm → set of alias_norm / name_norm для матчинга сырых тегов."""
    canons = {c.id: c for c in session.scalars(select(SkillCanonical)).all()}
    out: dict[str, set[str]] = defaultdict(set)
    for c in canons.values():
        out[c.name_norm].add(c.name_norm)
        out[c.name_norm].add(normalize_text(c.name))
    for a in session.scalars(select(SkillAlias)).all():
        c = canons.get(a.canonical_id)
        if not c:
            continue
        out[c.name_norm].add(a.alias_norm)
        out[c.name_norm].add(normalize_text(a.alias))
    return out


def skill_timeseries_by_day(
    session: Session,
    sources: list[str],
    skill_norms: list[str],
    skill_labels: dict[str, str],
    *,
    role_groups: Optional[list[str]] = None,
    rolling_days: int = 7,
    date_from: Optional[Any] = None,
    date_to: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """
    Дневная доля вакансий с навыком по published_at.
    Возвращает date, skill, support, vacancies, support_roll.
    """
    if not skill_norms:
        return []
    match_map = _skill_match_map(session)

    q = (
        select(
            Vacancy.id,
            Vacancy.published_at,
            VacancySkill.skill_name,
        )
        .join(VacancySkill, VacancySkill.vacancy_id == Vacancy.id, isouter=True)
        .where(
            _vac_filter(sources),
            Vacancy.published_at.is_not(None),
        )
    )
    if role_groups:
        q = q.where(Vacancy.role_group.in_(role_groups))
    if date_from is not None:
        q = q.where(Vacancy.published_at >= date_from)
    if date_to is not None:
        # включить весь день date_to
        from datetime import datetime, time, timedelta

        if hasattr(date_to, "year") and not hasattr(date_to, "hour"):
            end = datetime.combine(date_to, time.max)
        else:
            end = date_to
        q = q.where(Vacancy.published_at <= end)

    vac_day: dict[int, Any] = {}
    vac_skills: dict[int, set[str]] = defaultdict(set)
    for vid, published_at, skill_name in session.execute(q).all():
        if published_at is None:
            continue
        day = published_at.date() if hasattr(published_at, "date") else published_at
        vac_day[vid] = day
        if skill_name:
            vac_skills[vid].add(normalize_text(skill_name))

    day_vacs: dict[Any, set[int]] = defaultdict(set)
    for vid, day in vac_day.items():
        day_vacs[day].add(vid)

    if not day_vacs:
        return []

    days_sorted = sorted(day_vacs.keys())
    daily: dict[str, dict[Any, tuple[int, int]]] = {n: {} for n in skill_norms}
    for day in days_sorted:
        vids = day_vacs[day]
        n_day = len(vids)
        for norm in skill_norms:
            aliases = match_map.get(norm, {norm})
            hit = sum(1 for vid in vids if vac_skills.get(vid, set()) & aliases)
            daily[norm][day] = (hit, n_day)

    out: list[dict[str, Any]] = []
    for norm in skill_norms:
        label = skill_labels.get(norm, norm)
        series = []
        for day in days_sorted:
            hit, n_day = daily[norm].get(day, (0, len(day_vacs[day])))
            support = hit / n_day if n_day else 0.0
            series.append((day, support, n_day, hit))
        for i, (day, support, n_day, hit) in enumerate(series):
            window = [series[j][1] for j in range(max(0, i - rolling_days + 1), i + 1)]
            roll = sum(window) / len(window) if window else support
            out.append(
                {
                    "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                    "skill": label,
                    "skill_norm": norm,
                    "support": round(support, 4),
                    "support_roll": round(roll, 4),
                    "vacancies": n_day,
                    "hits": hit,
                }
            )
    return out


def list_daily_skill_options(
    *,
    market_mode: str = "russia",
    role_groups: Optional[list[str]] = None,
    min_support: float = 0.05,
    limit: int = 250,
) -> list[dict[str, Any]]:
    """Каталог навыков для фильтра дневного графика."""
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    temporal = str(cfg["temporal_source"])
    with SessionLocal() as session:
        rows = load_market_skills(session, role_group=None, min_support=min_support)
        rows = _with_source_trend(rows, temporal)
        if role_groups:
            rows = [r for r in rows if r.role_group in role_groups]
        best: dict[str, MarketSkillRow] = {}
        for r in rows:
            prev = best.get(r.skill_norm)
            if prev is None or r.support > prev.support:
                best[r.skill_norm] = r
        opts = sorted(best.values(), key=lambda x: (x.support, abs(x.trend)), reverse=True)
        return [
            {
                "skill": r.skill_name,
                "skill_norm": r.skill_norm,
                "support": r.support,
                "trend": r.trend,
            }
            for r in opts[:limit]
        ]


def fetch_skill_daily(
    *,
    market_mode: str,
    skills: list[dict[str, str]],
    role_groups: Optional[list[str]] = None,
    date_from: Optional[Any] = None,
    date_to: Optional[Any] = None,
    rolling_days: int = 7,
) -> list[dict[str, Any]]:
    """skills: [{skill, skill_norm}, ...]."""
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    norms = []
    labels: dict[str, str] = {}
    for s in skills:
        norm = s.get("skill_norm") or ""
        if not norm or norm in labels:
            continue
        norms.append(norm)
        labels[norm] = s.get("skill") or norm
    if not norms:
        return []
    with SessionLocal() as session:
        return skill_timeseries_by_day(
            session,
            list(cfg["sources"]),
            norms,
            labels,
            role_groups=role_groups,
            rolling_days=rolling_days,
            date_from=date_from,
            date_to=date_to,
        )


def published_date_bounds(
    *,
    market_mode: str,
    role_groups: Optional[list[str]] = None,
) -> tuple[Optional[Any], Optional[Any]]:
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    with SessionLocal() as session:
        q = select(func.min(Vacancy.published_at), func.max(Vacancy.published_at)).where(
            _vac_filter(list(cfg["sources"])),
            Vacancy.published_at.is_not(None),
        )
        if role_groups:
            q = q.where(Vacancy.role_group.in_(role_groups))
        mn, mx = session.execute(q).one()
        if mn is None:
            return None, None
        d0 = mn.date() if hasattr(mn, "date") else mn
        d1 = mx.date() if hasattr(mx, "date") else mx
        return d0, d1


def role_skill_heatmap(rows: list[MarketSkillRow], *, top_skills: int = 12) -> dict[str, Any]:
    # top skills by max support
    best: dict[str, float] = {}
    names: dict[str, str] = {}
    for r in rows:
        if r.support > best.get(r.skill_norm, 0):
            best[r.skill_norm] = r.support
            names[r.skill_norm] = r.skill_name
    top_norms = [n for n, _ in sorted(best.items(), key=lambda x: -x[1])[:top_skills]]
    roles = sorted({r.role_group for r in rows})
    grid = {role: {n: 0.0 for n in top_norms} for role in roles}
    for r in rows:
        if r.skill_norm in grid.get(r.role_group, {}):
            grid[r.role_group][r.skill_norm] = max(grid[r.role_group][r.skill_norm], r.support)
    matrix = [[grid[role][n] for n in top_norms] for role in roles]
    return {
        "roles": roles,
        "skills": [names[n] for n in top_norms],
        "matrix": matrix,
    }


def get_market_dashboard(
    *,
    market_mode: str = "russia",
    role_groups: Optional[list[str]] = None,
    min_support: float = 0.1,
) -> dict[str, Any]:
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    sources = list(cfg["sources"])
    temporal = str(cfg["temporal_source"])

    with SessionLocal() as session:
        pulse = pulse_kpis(session, sources)
        geo = geo_breakdown(session, sources, top=12)
        employers = (
            employer_breakdown(session, sources, top=12) if cfg["show_companies"] else []
        )

        all_rows = load_market_skills(session, role_group=None, min_support=min_support)
        # фильтр по наличию source-ряда
        rows = _with_source_trend(all_rows, temporal)
        if role_groups:
            rows = [r for r in rows if r.role_group in role_groups]

        # если после фильтра пусто — fallback на raw support из графа с role filter
        if not rows:
            rows = [
                r
                for r in all_rows
                if (not role_groups or r.role_group in role_groups)
            ]

        min_abs = 0.02 if temporal == "kaggle_ai" else 0.03
        rising, falling = rising_falling(rows, top=20, min_abs_trend=min_abs)

        def _dedupe(items: list[MarketSkillRow], n: int = 12) -> list[MarketSkillRow]:
            seen: set[str] = set()
            out: list[MarketSkillRow] = []
            for r in items:
                if r.skill_norm in seen:
                    continue
                seen.add(r.skill_norm)
                out.append(r)
                if len(out) >= n:
                    break
            return out

        rising = _dedupe(rising)
        falling = _dedupe(falling)
        bridges = career_bridges(rows if rows else all_rows, top=15, min_support=0.12)
        rare = skill_rarity(
            session,
            sources,
            top=12,
            max_share=0.08 if temporal == "hh" else 0.45,
            min_count=3 if temporal == "hh" else 100,
        )
        unique = unique_role_skills(
            rows,
            top=12,
            min_support=0.15 if temporal == "hh" else 0.35,
            max_other=0.08 if temporal == "hh" else 0.25,
        )
        heatmap = role_skill_heatmap(rows, top_skills=12)

        # топ навыки по объёму в корпусе (сырые)
        top_raw = session.execute(
            select(VacancySkill.skill_name, func.count(func.distinct(Vacancy.id)))
            .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
            .where(_vac_filter(sources))
            .group_by(VacancySkill.skill_name)
            .order_by(func.count(func.distinct(Vacancy.id)).desc())
            .limit(15)
        ).all()
        n_tagged = pulse["with_skills"] or 1
        top_demand = [
            {"skill": str(name), "count": int(c), "share": round(int(c) / n_tagged, 4)}
            for name, c in top_raw
        ]

        # drill defaults
        geo_skills = {}
        if geo:
            geo_skills[geo[0]["place"]] = top_skills_by_geo(
                session, sources, geo[0]["place"], top=8
            )
        emp_skills = {}
        if employers:
            emp_skills[employers[0]["employer"]] = top_skills_by_employer(
                session, sources, employers[0]["employer"], top=8
            )

        def _sk(r: MarketSkillRow) -> dict[str, Any]:
            return {
                "skill": r.skill_name,
                "skill_norm": r.skill_norm,
                "role": r.role_group,
                "cluster": r.cluster,
                "support": r.support,
                "trend": r.trend,
                "trend_label": f"{r.trend:+.1%}",
                "support_label": f"{r.support:.0%}",
                "support_by_year": r.support_by_year,
            }

        rising_out = [_sk(r) for r in rising]
        falling_out = [_sk(r) for r in falling]

        return {
            "mode": market_mode,
            "config": cfg,
            "pulse": pulse,
            "geo": geo,
            "employers": employers,
            "top_demand": top_demand,
            "rising": rising_out,
            "falling": falling_out,
            "rare": rare,
            "unique": unique,
            "bridges": bridges[:12],
            "heatmap": heatmap,
            "geo_skills": geo_skills,
            "employer_skills": emp_skills,
            "roles_available": sorted({r.role_group for r in all_rows}),
        }


def drill_geo_skills(market_mode: str, place: str, top: int = 10) -> list[dict[str, Any]]:
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    with SessionLocal() as session:
        return top_skills_by_geo(session, list(cfg["sources"]), place, top=top)


def drill_employer_skills(
    market_mode: str, employer: str, top: int = 10
) -> list[dict[str, Any]]:
    cfg = MARKET_MODES.get(market_mode) or MARKET_MODES["russia"]
    with SessionLocal() as session:
        return top_skills_by_employer(session, list(cfg["sources"]), employer, top=top)
