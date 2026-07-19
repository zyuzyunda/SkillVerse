"""Мультивселенная карьерных возможностей: корпус, временной анализ, мосты между ролями."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import GraphEdge, SkillCanonical, Vacancy, VacancySkill
from src.db.session import SessionLocal
from src.market.pulse import MarketSkillRow, load_market_skills, rising_falling


SOURCE_LABELS = {
    "hh": "hh.ru",
    "csv_seed": "CSV seed",
    "kaggle_ai": "Kaggle AI Jobs",
}


def corpus_stats(
    session: Session,
    *,
    sources: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Статистика по входным вакансиям и сырым навыкам."""
    base = select(Vacancy)
    if sources:
        base = base.where(Vacancy.data_source.in_(sources))

    def _count(extra=None):
        q = select(func.count()).select_from(Vacancy)
        if sources:
            q = q.where(Vacancy.data_source.in_(sources))
        if extra is not None:
            q = q.where(extra)
        return int(session.scalar(q) or 0)

    n_vac = _count()
    n_dated = _count(Vacancy.published_at.is_not(None))
    n_with_salary = _count(
        (Vacancy.salary_from.is_not(None)) | (Vacancy.salary_to.is_not(None))
    )

    skill_join = (
        select(func.count(func.distinct(Vacancy.id)))
        .select_from(Vacancy)
        .join(VacancySkill, VacancySkill.vacancy_id == Vacancy.id)
    )
    if sources:
        skill_join = skill_join.where(Vacancy.data_source.in_(sources))
    n_with_skills = int(session.scalar(skill_join) or 0)

    by_source_q = (
        select(Vacancy.data_source, func.count())
        .group_by(Vacancy.data_source)
        .order_by(func.count().desc())
    )
    by_role_q = (
        select(Vacancy.role_group, func.count())
        .group_by(Vacancy.role_group)
        .order_by(func.count().desc())
    )
    by_year_q = (
        select(func.extract("year", Vacancy.published_at), func.count())
        .where(Vacancy.published_at.is_not(None))
        .group_by(func.extract("year", Vacancy.published_at))
        .order_by(func.extract("year", Vacancy.published_at))
    )
    by_area_q = (
        select(Vacancy.area_name, func.count())
        .where(Vacancy.area_name.is_not(None))
        .group_by(Vacancy.area_name)
        .order_by(func.count().desc())
        .limit(12)
    )
    by_exp_q = (
        select(Vacancy.experience_name, func.count())
        .where(Vacancy.experience_name.is_not(None))
        .group_by(Vacancy.experience_name)
        .order_by(func.count().desc())
    )
    if sources:
        by_source_q = by_source_q.where(Vacancy.data_source.in_(sources))
        by_role_q = by_role_q.where(Vacancy.data_source.in_(sources))
        by_year_q = by_year_q.where(Vacancy.data_source.in_(sources))
        by_area_q = by_area_q.where(Vacancy.data_source.in_(sources))
        by_exp_q = by_exp_q.where(Vacancy.data_source.in_(sources))

    by_source = {str(k): int(v) for k, v in session.execute(by_source_q).all()}
    by_role = {str(k): int(v) for k, v in session.execute(by_role_q).all()}
    by_year = {int(y): int(c) for y, c in session.execute(by_year_q).all() if y is not None}
    by_area = {str(k): int(v) for k, v in session.execute(by_area_q).all()}
    by_experience = {str(k): int(v) for k, v in session.execute(by_exp_q).all()}

    # skills coverage
    skill_q = (
        select(
            func.count(VacancySkill.id),
            func.count(func.distinct(VacancySkill.skill_name)),
        )
        .select_from(VacancySkill)
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
    )
    if sources:
        skill_q = skill_q.where(Vacancy.data_source.in_(sources))
    skill_tags, unique_raw = session.execute(skill_q).one()
    skill_tags = int(skill_tags or 0)
    unique_raw = int(unique_raw or 0)

    avg_skills = round(skill_tags / n_with_skills, 2) if n_with_skills else 0.0

    by_skill_source_q = (
        select(VacancySkill.source, func.count())
        .select_from(VacancySkill)
        .join(Vacancy, Vacancy.id == VacancySkill.vacancy_id)
        .group_by(VacancySkill.source)
        .order_by(func.count().desc())
    )
    if sources:
        by_skill_source_q = by_skill_source_q.where(Vacancy.data_source.in_(sources))
    by_skill_source = {str(k): int(v) for k, v in session.execute(by_skill_source_q).all()}

    date_min_q = select(func.min(Vacancy.published_at)).where(Vacancy.published_at.is_not(None))
    date_max_q = select(func.max(Vacancy.published_at)).where(Vacancy.published_at.is_not(None))
    if sources:
        date_min_q = date_min_q.where(Vacancy.data_source.in_(sources))
        date_max_q = date_max_q.where(Vacancy.data_source.in_(sources))
    date_min = session.scalar(date_min_q)
    date_max = session.scalar(date_max_q)

    n_canonical = session.scalar(select(func.count()).select_from(SkillCanonical)) or 0
    n_edges = (
        session.scalar(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
        )
        or 0
    )

    # year × source matrix
    ys_q = (
        select(
            func.extract("year", Vacancy.published_at),
            Vacancy.data_source,
            func.count(),
        )
        .where(Vacancy.published_at.is_not(None))
        .group_by(func.extract("year", Vacancy.published_at), Vacancy.data_source)
        .order_by(func.extract("year", Vacancy.published_at))
    )
    if sources:
        ys_q = ys_q.where(Vacancy.data_source.in_(sources))
    year_source = [
        {"year": int(y), "source": str(s), "vacancies": int(c)}
        for y, s, c in session.execute(ys_q).all()
        if y is not None
    ]

    return {
        "vacancies": n_vac,
        "with_date": n_dated,
        "without_date": n_vac - n_dated,
        "with_skills": n_with_skills,
        "without_skills": n_vac - n_with_skills,
        "with_salary": n_with_salary,
        "date_coverage": round(n_dated / n_vac, 4) if n_vac else 0.0,
        "skill_coverage": round(n_with_skills / n_vac, 4) if n_vac else 0.0,
        "skill_tags": skill_tags,
        "unique_raw_skills": unique_raw,
        "avg_skills_per_vacancy": avg_skills,
        "by_source": by_source,
        "by_role": by_role,
        "by_year": by_year,
        "by_area": by_area,
        "by_experience": by_experience,
        "by_skill_source": by_skill_source,
        "year_source": year_source,
        "date_min": date_min.isoformat() if date_min else None,
        "date_max": date_max.isoformat() if date_max else None,
        "year_min": min(by_year) if by_year else None,
        "year_max": max(by_year) if by_year else None,
        "canonical_skills": int(n_canonical),
        "role_skill_edges": int(n_edges),
        "roles_count": len(by_role),
        "sources_count": len(by_source),
    }


def skill_timelines(
    rows: list[MarketSkillRow],
    *,
    top: int = 12,
    sort_by: str = "support",
    temporal_source: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Длинный формат: skill × year → support для line chart (один источник, без смешения)."""

    def series_for(r: MarketSkillRow) -> tuple[str, dict[str, float]]:
        by_src = r.support_by_year_by_source or {}
        if temporal_source and temporal_source in by_src:
            return temporal_source, by_src[temporal_source]
        if r.support_by_year:
            return r.temporal_source or "mixed", r.support_by_year
        if by_src:
            # longest
            src, series = max(by_src.items(), key=lambda x: len(x[1]))
            return src, series
        return "none", {}

    enriched: list[tuple[MarketSkillRow, str, dict[str, float]]] = []
    for r in rows:
        src, series = series_for(r)
        if len(series) < 2 and sort_by != "support":
            continue
        if not series:
            continue
        enriched.append((r, src, series))

    if sort_by == "trend":
        ranked = sorted(enriched, key=lambda x: (abs(x[0].trend), x[0].support), reverse=True)
    elif sort_by == "volatility":
        ranked = sorted(
            enriched,
            key=lambda x: (_volatility(x[2]), x[0].support),
            reverse=True,
        )
    else:
        ranked = sorted(enriched, key=lambda x: (x[0].support, abs(x[0].trend)), reverse=True)

    seen: set[str] = set()
    picked: list[tuple[MarketSkillRow, str, dict[str, float]]] = []
    for item in ranked:
        r = item[0]
        if r.skill_norm in seen:
            continue
        seen.add(r.skill_norm)
        picked.append(item)
        if len(picked) >= top:
            break

    out: list[dict[str, Any]] = []
    for r, src, series in picked:
        for y, v in sorted(series.items(), key=lambda x: int(x[0])):
            out.append(
                {
                    "skill": r.skill_name,
                    "skill_norm": r.skill_norm,
                    "year": int(y),
                    "support": round(float(v), 4),
                    "cluster": r.cluster,
                    "role": r.role_group,
                    "trend": r.trend,
                    "source": src,
                }
            )
    return out

def skill_year_heatmap(
    rows: list[MarketSkillRow],
    *,
    top: int = 18,
    temporal_source: Optional[str] = None,
) -> dict[str, Any]:
    """Матрица skill × year (max support across roles), один временной источник."""
    by_skill: dict[str, dict[str, Any]] = {}
    for r in rows:
        series = r.support_by_year
        src = r.temporal_source
        if temporal_source and r.support_by_year_by_source:
            series = r.support_by_year_by_source.get(temporal_source) or {}
            src = temporal_source
        if not series:
            continue
        cur = by_skill.get(r.skill_norm)
        if cur is None:
            by_skill[r.skill_norm] = {
                "skill": r.skill_name,
                "support": r.support,
                "trend": r.trend,
                "by_year": dict(series),
                "source": src,
            }
        else:
            if r.support > cur["support"]:
                cur["skill"] = r.skill_name
                cur["support"] = r.support
            for y, v in series.items():
                cur["by_year"][y] = max(float(cur["by_year"].get(y, 0)), float(v))
            cur["trend"] = (cur["trend"] + r.trend) / 2

    top_skills = sorted(
        by_skill.values(),
        key=lambda x: (len(x["by_year"]), x["support"], abs(x["trend"])),
        reverse=True,
    )[:top]
    years = sorted({int(y) for s in top_skills for y in s["by_year"].keys()})
    matrix = []
    labels = []
    for s in top_skills:
        labels.append(s["skill"])
        matrix.append(
            [round(float(s["by_year"].get(str(y), s["by_year"].get(y, 0))), 4) for y in years]
        )
    return {"skills": labels, "years": years, "matrix": matrix}


def career_bridges(
    rows: list[MarketSkillRow],
    *,
    min_roles: int = 2,
    min_support: float = 0.15,
    top: int = 25,
) -> list[dict[str, Any]]:
    """Навыки-мосты между карьерными вселенными (ролями)."""
    by_skill: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.support < min_support:
            continue
        cur = by_skill.get(r.skill_norm)
        if cur is None:
            by_skill[r.skill_norm] = {
                "skill": r.skill_name,
                "skill_norm": r.skill_norm,
                "cluster": r.cluster,
                "roles": {r.role_group},
                "max_support": r.support,
                "avg_trend": r.trend,
                "support_by_year": dict(r.support_by_year),
                "by_role": {r.role_group: r.support},
            }
        else:
            cur["roles"].add(r.role_group)
            cur["by_role"][r.role_group] = max(cur["by_role"].get(r.role_group, 0), r.support)
            cur["max_support"] = max(cur["max_support"], r.support)
            n = len(cur["roles"])
            cur["avg_trend"] = (cur["avg_trend"] * (n - 1) + r.trend) / n
            for y, v in r.support_by_year.items():
                cur["support_by_year"][y] = max(float(cur["support_by_year"].get(y, 0)), float(v))

    out = []
    for item in by_skill.values():
        roles = sorted(item["roles"])
        if len(roles) < min_roles:
            continue
        out.append(
            {
                "skill": item["skill"],
                "cluster": item["cluster"],
                "roles_count": len(roles),
                "roles": roles,
                "max_support": round(item["max_support"], 4),
                "avg_trend": round(item["avg_trend"], 4),
                "support_by_year": item["support_by_year"],
                "by_role": {k: round(v, 4) for k, v in item["by_role"].items()},
            }
        )
    out.sort(key=lambda x: (x["roles_count"], x["max_support"], abs(x["avg_trend"])), reverse=True)
    return out[:top]


def year_over_year_moves(
    rows: list[MarketSkillRow],
    *,
    top: int = 15,
    temporal_source: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Крупнейшие годовые сдвиги support внутри одного источника."""
    moves: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for r in rows:
        series = r.support_by_year
        src = r.temporal_source or "unknown"
        if temporal_source and r.support_by_year_by_source:
            series = r.support_by_year_by_source.get(temporal_source) or {}
            src = temporal_source
        years = sorted((int(y), float(v)) for y, v in (series or {}).items())
        for i in range(1, len(years)):
            y0, v0 = years[i - 1]
            y1, v1 = years[i]
            key = (r.skill_norm, src, y1)
            if key in seen:
                continue
            seen.add(key)
            delta = v1 - v0
            moves.append(
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "source": src,
                    "from_year": y0,
                    "to_year": y1,
                    "from_support": round(v0, 4),
                    "to_support": round(v1, 4),
                    "delta": round(delta, 4),
                    "support": r.support,
                }
            )
    moves.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return moves[:top]


def build_market_signals(
    rows: list[MarketSkillRow],
    bridges: list[dict[str, Any]],
    *,
    mode: str = "personal",
    top: int = 5,
) -> dict[str, Any]:
    """
    Рыночные сигналы с бизнес-смыслом (не «ещё один график»).
    mode: personal | consultant — разный язык действий.
    """
    # лучшая строка на навык (max support, затем |trend|)
    by_norm: dict[str, MarketSkillRow] = {}
    roles_of: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        roles_of[r.skill_norm].add(r.role_group)
        prev = by_norm.get(r.skill_norm)
        if prev is None or (r.support, abs(r.trend)) > (prev.support, abs(prev.trend)):
            by_norm[r.skill_norm] = r

    skills = list(by_norm.values())
    bridge_map = {b["skill"]: b for b in bridges}
    for b in bridges:
        if b.get("skill_norm"):
            bridge_map[str(b["skill_norm"])] = b

    def _pack(
        r: MarketSkillRow,
        *,
        kind: str,
        headline: str,
        action: str,
        why: str,
        score: float,
    ) -> dict[str, Any]:
        b = bridge_map.get(r.skill_name) or bridge_map.get(r.skill_norm) or {}
        n_roles = int(b.get("roles_count") or len(roles_of.get(r.skill_norm, {r.role_group})))
        role_list = sorted(b.get("roles") or roles_of.get(r.skill_norm) or [r.role_group])
        return {
            "kind": kind,
            "skill": r.skill_name,
            "skill_norm": r.skill_norm,
            "cluster": r.cluster,
            "role": r.role_group,
            "roles": role_list,
            "roles_count": n_roles,
            "support": round(r.support, 4),
            "trend": round(r.trend, 4),
            "trend_label": f"{r.trend:+.1%}",
            "support_label": f"{r.support:.0%}",
            "headline": headline,
            "action": action,
            "why": why,
            "score": round(score, 4),
            "cta": "Открыть в «Моя вселенная»",
        }

    is_personal = mode != "consultant"

    # 1) Учить / рекомендовать сейчас: спрос + рост
    learn_now = []
    for r in sorted(skills, key=lambda x: (x.trend, x.support), reverse=True):
        if r.trend < 0.03 or r.support < 0.12:
            continue
        if is_personal:
            h = f"Учить сейчас: {r.skill_name}"
            a = "Добавьте в маршрут обучения — спрос растёт и уже заметный на рынке."
            w = f"Спрос {r.support:.0%} · тренд {r.trend:+.1%} · роль {r.role_group}"
        else:
            h = f"Рекомендовать клиенту: {r.skill_name}"
            a = "Приоритет в ИПР / менторинге — растущий спрос при уже заметной базе вакансий."
            w = f"Спрос {r.support:.0%} · тренд {r.trend:+.1%} · якорь-роль {r.role_group}"
        learn_now.append(
            _pack(r, kind="learn_now", headline=h, action=a, why=w, score=r.trend * 0.6 + r.support * 0.4)
        )
    learn_now = learn_now[:top]

    # 2) Не раздувать / риск устаревания: высокий спрос, но падает
    fading = []
    for r in sorted(skills, key=lambda x: (x.trend, -x.support)):
        if r.trend > -0.03 or r.support < 0.2:
            continue
        if is_personal:
            h = f"Не раздувать: {r.skill_name}"
            a = "Оставьте как базу, если уже есть; не делайте главным фокусом обучения."
            w = f"Всё ещё в {r.support:.0%} вакансий, но тренд {r.trend:+.1%}"
        else:
            h = f"Риск устаревания: {r.skill_name}"
            a = "В команде навык ещё нужен, но рынок сжимается — планируйте замену / апскилл."
            w = f"Спрос {r.support:.0%} · тренд {r.trend:+.1%} · роль {r.role_group}"
        fading.append(
            _pack(r, kind="fading", headline=h, action=a, why=w, score=abs(r.trend) * 0.5 + r.support * 0.5)
        )
    fading = fading[:top]

    # 3) Универсальные мосты
    bridge_signals = []
    for b in bridges[: top * 2]:
        skill_name = b["skill"]
        r = by_norm.get(b.get("skill_norm") or "")
        if r is None:
            # match by name
            r = next((x for x in skills if x.skill_name == skill_name), None)
        if r is None:
            r = MarketSkillRow(
                skill_name=skill_name,
                skill_norm=str(b.get("skill_norm") or skill_name.lower()),
                cluster=str(b.get("cluster") or "Other"),
                role_group=(b.get("roles") or ["—"])[0],
                support=float(b.get("max_support") or 0),
                trend=float(b.get("avg_trend") or 0),
                support_by_year=b.get("support_by_year") or {},
                trend_from_year=None,
                trend_to_year=None,
                count=0,
                role_vacancies=0,
            )
        n_roles = int(b.get("roles_count") or 0)
        if n_roles < 3:
            continue
        if is_personal:
            h = f"Универсальный мост: {r.skill_name}"
            a = f"Даёт вход в {n_roles} вселенных — выгодная инвестиция, если целитесь шире одной роли."
        else:
            h = f"Трансферный навык: {r.skill_name}"
            a = f"Сильный кандидат на кросс-ролевое развитие / внутреннюю мобильность ({n_roles} ролей)."
        w = (
            f"Роли: {', '.join((b.get('roles') or [])[:5])}"
            + ("…" if n_roles > 5 else "")
            + f" · спрос до {float(b.get('max_support') or 0):.0%}"
        )
        bridge_signals.append(
            _pack(
                r,
                kind="bridge",
                headline=h,
                action=a,
                why=w,
                score=n_roles * 0.15 + float(b.get("max_support") or 0),
            )
        )
    bridge_signals = sorted(bridge_signals, key=lambda x: x["score"], reverse=True)[:top]

    # 4) Нишевый рост: пока редкий, но резко растёт
    niche = []
    for r in sorted(skills, key=lambda x: x.trend, reverse=True):
        if r.trend < 0.05 or r.support < 0.05 or r.support >= 0.25:
            continue
        if is_personal:
            h = f"Ранний сигнал: {r.skill_name}"
            a = "Ещё не везде, но растёт быстро — имеет смысл прицельно, если цель — дифференциация."
        else:
            h = f"Emerging: {r.skill_name}"
            a = "Малый base rate, сильный рост — тема для пилота / спецтрека, не для массового обучения."
        w = f"Спрос {r.support:.0%} · тренд {r.trend:+.1%} · {r.role_group}"
        niche.append(
            _pack(r, kind="niche_rising", headline=h, action=a, why=w, score=r.trend)
        )
    niche = niche[:top]

    # 5) Ядро рынка: высокий спрос, стабильный тренд
    core = []
    for r in sorted(skills, key=lambda x: x.support, reverse=True):
        if r.support < 0.35 or abs(r.trend) > 0.05:
            continue
        if is_personal:
            h = f"Ядро рынка: {r.skill_name}"
            a = "Must-have для входа в роль — без этого сложнее конкурировать."
        else:
            h = f"Базовый стандарт: {r.skill_name}"
            a = "Ожидаемый минимум в найме / оценке готовности кандидата к роли."
        w = f"Спрос {r.support:.0%} · тренд {r.trend:+.1%} (стабильно) · {r.role_group}"
        core.append(
            _pack(r, kind="core", headline=h, action=a, why=w, score=r.support)
        )
    core = core[:top]

    # Импульс ролей: средний тренд топ-навыков
    by_role: dict[str, list[MarketSkillRow]] = defaultdict(list)
    for r in rows:
        by_role[r.role_group].append(r)
    role_momentum = []
    for role, items in by_role.items():
        top_items = sorted(items, key=lambda x: x.support, reverse=True)[:12]
        if len(top_items) < 5:
            continue
        avg_t = sum(x.trend for x in top_items) / len(top_items)
        avg_s = sum(x.support for x in top_items) / len(top_items)
        rising_n = sum(1 for x in top_items if x.trend >= 0.03)
        falling_n = sum(1 for x in top_items if x.trend <= -0.03)
        if is_personal:
            if avg_t >= 0.02:
                verdict = "Вселенная на подъёме — хороший момент входить/углубляться."
            elif avg_t <= -0.02:
                verdict = "Спрос в ядре остывает — смотрите соседние миры и мосты."
            else:
                verdict = "Стабильная вселенная — упор на ядро + точечные растущие навыки."
        else:
            if avg_t >= 0.02:
                verdict = "Роль в фазе роста — усиливать hiring/обучение под rising skills."
            elif avg_t <= -0.02:
                verdict = "Роль в сжатии ядра — пересмотр профиля и reskilling-планов."
            else:
                verdict = "Стабильный спрос — держать стандарты ядра, точечно обновлять стек."
        role_momentum.append(
            {
                "role": role,
                "avg_trend": round(avg_t, 4),
                "avg_trend_label": f"{avg_t:+.1%}",
                "avg_support": round(avg_s, 4),
                "rising_skills": rising_n,
                "falling_skills": falling_n,
                "verdict": verdict,
                "score": round(avg_t, 4),
            }
        )
    role_momentum.sort(key=lambda x: x["avg_trend"], reverse=True)

    # краткий executive summary
    summary_bits = []
    if learn_now:
        summary_bits.append(f"рост: {', '.join(x['skill'] for x in learn_now[:3])}")
    if fading:
        summary_bits.append(f"сжатие: {', '.join(x['skill'] for x in fading[:3])}")
    if bridge_signals:
        summary_bits.append(f"мосты: {', '.join(x['skill'] for x in bridge_signals[:3])}")
    if is_personal:
        summary = (
            "Сигналы рынка для вашего развития. "
            + (" · ".join(summary_bits) if summary_bits else "Недостаточно динамики в выбранном срезе.")
        )
    else:
        summary = (
            "Сигналы для консультации / HR. "
            + (" · ".join(summary_bits) if summary_bits else "Недостаточно динамики в выбранном срезе.")
        )

    return {
        "mode": mode,
        "summary": summary,
        "learn_now": learn_now,
        "fading": fading,
        "bridges": bridge_signals,
        "niche_rising": niche,
        "core": core,
        "role_momentum": role_momentum,
    }


def annotate_graph_trends(
    graph_data: dict[str, Any],
    rows: list[MarketSkillRow],
) -> dict[str, Any]:
    """Добавляет trend / bridge_roles к skill-узлам подграфа."""
    by_norm: dict[str, MarketSkillRow] = {}
    role_sets: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        role_sets[r.skill_norm].add(r.role_group)
        prev = by_norm.get(r.skill_norm)
        if prev is None or r.support > prev.support:
            by_norm[r.skill_norm] = r

    nodes = []
    for n in graph_data.get("nodes", []):
        nn = dict(n)
        if nn.get("node_type") == "skill":
            sid = nn["id"].replace("skill:", "", 1) if nn["id"].startswith("skill:") else nn["id"]
            match = by_norm.get(sid)
            if match is None:
                for r in rows:
                    if r.skill_name == nn.get("label") or r.skill_norm == sid:
                        match = r
                        break
            if match:
                nn["trend"] = match.trend
                nn["support_by_year"] = match.support_by_year
                nn["bridge_roles"] = len(role_sets.get(match.skill_norm, set()))
            else:
                nn["trend"] = 0.0
                nn["bridge_roles"] = 1
        nodes.append(nn)
    out = dict(graph_data)
    out["nodes"] = nodes
    return out


def get_multiverse(
    *,
    role_group: Optional[str] = None,
    role_groups: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    min_support: float = 0.12,
    temporal_source: Optional[str] = None,
    audience: str = "personal",
) -> dict[str, Any]:
    with SessionLocal() as session:
        corpus = corpus_stats(session, sources=sources)
        all_rows = load_market_skills(session, role_group=None, min_support=min_support)
        if role_groups:
            rows = [r for r in all_rows if r.role_group in role_groups]
        elif role_group:
            rows = [r for r in all_rows if r.role_group == role_group]
        else:
            rows = all_rows

        # доступные источники для временных рядов
        temporal_options: set[str] = set()
        for r in rows:
            if r.support_by_year_by_source:
                for src, series in r.support_by_year_by_source.items():
                    if len(series) >= 2 or src != "csv_seed":
                        temporal_options.add(src)
            elif r.temporal_source and r.temporal_source != "none":
                temporal_options.add(r.temporal_source)

        # для rising/falling при выборе источника — тренд именно этого источника
        rank_rows = rows
        if temporal_source:
            adjusted: list[MarketSkillRow] = []
            for r in rows:
                by_src = r.support_by_year_by_source or {}
                series = by_src.get(temporal_source)
                if not series or not any(float(v) > 0 for v in series.values()):
                    continue
                years = sorted(int(y) for y in series)
                local_trend = 0.0
                if len(years) >= 4:
                    # длинный ряд: изменение за весь горизонт (не только последний YoY ≈ 0)
                    local_trend = float(series[str(years[-1])]) - float(series[str(years[0])])
                elif len(years) >= 2:
                    local_trend = float(series[str(years[-1])]) - float(series[str(years[-2])])
                adjusted.append(
                    MarketSkillRow(
                        skill_name=r.skill_name,
                        skill_norm=r.skill_norm,
                        cluster=r.cluster,
                        role_group=r.role_group,
                        support=r.support,
                        trend=round(local_trend, 4),
                        support_by_year={str(k): float(v) for k, v in series.items()},
                        trend_from_year=years[0] if len(years) >= 4 else (years[-2] if len(years) >= 2 else None),
                        trend_to_year=years[-1] if years else None,
                        count=r.count,
                        role_vacancies=r.role_vacancies,
                        temporal_source=temporal_source,
                        trend_slope=r.trend_slope,
                        support_by_year_by_source=r.support_by_year_by_source,
                        trend_by_source=r.trend_by_source,
                        trend_method="horizon" if len(years) >= 4 else "within_source_yoy",
                    )
                )
            rank_rows = adjusted

        min_abs = 0.02 if temporal_source == "kaggle_ai" else 0.03
        rising, falling = rising_falling(rank_rows, top=12, min_abs_trend=min_abs)
        bridges_src = all_rows
        bridges = career_bridges(bridges_src, top=25)
        if role_groups:
            rg = set(role_groups)
            bridges = [b for b in bridges if len(rg.intersection(b["roles"])) >= 1]
            bridges.sort(
                key=lambda b: (
                    len(rg.intersection(b["roles"])),
                    b["roles_count"],
                    b["max_support"],
                ),
                reverse=True,
            )
        signals = build_market_signals(
            rank_rows if rank_rows else rows,
            bridges[:20],
            mode="consultant" if audience == "consultant" else "personal",
            top=5,
        )
        return {
            "corpus": corpus,
            "role_group": role_group,
            "role_groups": role_groups,
            "rows_count": len(rows),
            "temporal_options": sorted(temporal_options),
            "temporal_source": temporal_source,
            "audience": audience,
            "signals": signals,
            "timelines_support": skill_timelines(
                rows, top=10, sort_by="support", temporal_source=temporal_source
            ),
            "timelines_trend": skill_timelines(
                rows, top=10, sort_by="trend", temporal_source=temporal_source
            ),
            "timelines_volatile": skill_timelines(
                rows, top=10, sort_by="volatility", temporal_source=temporal_source
            ),
            "heatmap": skill_year_heatmap(rows, top=16, temporal_source=temporal_source),
            "bridges": bridges[:20],
            "yoy_moves": year_over_year_moves(rows, top=20, temporal_source=temporal_source),
            "rising": [
                {
                    "skill": r.skill_name,
                    "role": r.role_group,
                    "cluster": r.cluster,
                    "support": r.support,
                    "trend": r.trend,
                    "temporal_source": r.temporal_source,
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
                    "temporal_source": r.temporal_source,
                    "support_by_year": r.support_by_year,
                }
                for r in falling
            ],
        }


def _volatility(by_year: dict[str, float]) -> float:
    vals = [float(v) for v in by_year.values()]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)
