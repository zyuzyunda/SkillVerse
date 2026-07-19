"""Личная карьерная вселенная: навыки пользователя → gaps → рекомендации."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.db.models import Course, GraphEdge, SkillAlias, SkillCanonical
from src.db.session import SessionLocal
from src.graph.normalize import normalize_text


@dataclass
class ResolvedSkill:
    raw: str
    skill_name: str
    skill_norm: str
    canonical_id: Optional[int]
    match_method: str  # exact|alias|fuzzy|unmatched


@dataclass
class SkillAdvice:
    skill_name: str
    skill_norm: str
    cluster: str
    support: float
    trend: float
    priority: float
    reason: str
    status: str  # have|gap|bonus
    trend_source: str = "edge"
    trend_label: str = "0%"


@dataclass
class LearningStep:
    step: int
    title: str
    subtitle: str
    skills: list[SkillAdvice]
    focus: str  # foundation|core|depth|trend
    readiness_gain: float
    course_titles: list[str] = field(default_factory=list)


@dataclass
class UniverseFit:
    role_group: str
    readiness: float
    covered: int
    required: int
    missing_top: list[str]
    matching_top: list[str]
    market_weight: float


@dataclass
class CourseFit:
    course_code: str
    title: str
    duration_hours: int
    skills_covered: list[str]
    gap_score: float
    reason: str


@dataclass
class PersonalPathResult:
    target_role: str
    readiness: float
    resolved: list[ResolvedSkill]
    unmatched: list[str]
    have: list[SkillAdvice]
    gaps: list[SkillAdvice]
    bonus: list[SkillAdvice]
    learning_path: list[SkillAdvice]
    learning_steps: list[LearningStep]
    adjacent: list[UniverseFit]
    courses: list[CourseFit]
    summary: str
    catalog_size: int = 0


def _load_skill_maps(session: Session) -> dict[str, SkillCanonical]:
    canons = list(session.scalars(select(SkillCanonical)).all())
    by_id = {c.id: c for c in canons}
    by_alias: dict[str, SkillCanonical] = {}
    for a in session.scalars(select(SkillAlias)).all():
        c = by_id.get(a.canonical_id)
        if c:
            by_alias[a.alias_norm] = c
            by_alias[normalize_text(a.alias)] = c
    for c in canons:
        by_alias[c.name_norm] = c
        by_alias[normalize_text(c.name)] = c
    return by_alias


def resolve_skills(
    session: Session,
    raw_skills: list[str],
    *,
    fuzzy: bool = True,
) -> list[ResolvedSkill]:
    by_alias = _load_skill_maps(session)
    canon_by_norm = {c.name_norm: c for c in by_alias.values()}
    canon_norms = list(canon_by_norm.keys())

    out: list[ResolvedSkill] = []
    seen: set[str] = set()
    for raw in raw_skills:
        text = (raw or "").strip()
        if not text:
            continue
        norm = normalize_text(text)
        if not norm:
            continue
        hit = by_alias.get(norm)
        method = "alias"
        if hit is None and fuzzy and canon_norms:
            try:
                from rapidfuzz import fuzz, process

                match = process.extractOne(
                    norm, canon_norms, scorer=fuzz.token_sort_ratio
                )
                if match and match[1] >= 88:
                    hit = canon_by_norm.get(match[0])
                    method = "fuzzy"
            except Exception:
                hit = None
        if hit is None:
            out.append(
                ResolvedSkill(
                    raw=text,
                    skill_name=text,
                    skill_norm=norm,
                    canonical_id=None,
                    match_method="unmatched",
                )
            )
            continue
        if hit.name_norm in seen:
            continue
        seen.add(hit.name_norm)
        out.append(
            ResolvedSkill(
                raw=text,
                skill_name=hit.name,
                skill_norm=hit.name_norm,
                canonical_id=hit.id,
                match_method="exact" if hit.name_norm == norm else method,
            )
        )
    return out


def skill_catalog(
    session: Session,
    *,
    role_group: Optional[str] = None,
    min_support: float = 0.08,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Список навыков для UI (multiselect)."""
    q = select(GraphEdge).where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
    if role_group:
        q = q.where(GraphEdge.source_key == f"role:{role_group}")
    edges = session.scalars(q).all()
    best: dict[str, dict[str, Any]] = {}
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        norm = str(props.get("skill_norm") or e.target_key.replace("skill:", "", 1))
        name = str(props.get("skill_name") or norm)
        prev = best.get(norm)
        if prev is None or support > prev["support"]:
            best[norm] = {
                "skill": name,
                "skill_norm": norm,
                "support": round(support, 4),
                "cluster": props.get("cluster") or "Other",
                "role": props.get("role_group") or e.source_key.replace("role:", "", 1),
            }
    rows = sorted(best.values(), key=lambda x: x["support"], reverse=True)
    return rows[:limit]


def _extract_trend(props: dict[str, Any]) -> tuple[float, str]:
    """
    Читаемый тренд для UI.
    Приоритет: hh YoY → горизонт kaggle → агрегированный trend с ребра.
    """
    by_src = props.get("trend_by_source") or {}
    if isinstance(by_src, dict) and "hh" in by_src:
        try:
            t = float(by_src["hh"])
            return round(t, 4), "hh"
        except (TypeError, ValueError):
            pass

    by_year_src = props.get("support_by_year_by_source") or {}
    if isinstance(by_year_src, dict):
        kaggle = by_year_src.get("kaggle_ai") or {}
        if isinstance(kaggle, dict) and len(kaggle) >= 4:
            years = sorted((int(y), float(v)) for y, v in kaggle.items())
            if any(v > 0 for _, v in years):
                t = years[-1][1] - years[0][1]
                return round(t, 4), "kaggle_horizon"

    try:
        t = float(props.get("trend") or 0)
    except (TypeError, ValueError):
        t = 0.0
    return round(t, 4), str(props.get("trend_method") or "edge")


def _role_market(
    session: Session,
    role_group: str,
    *,
    min_support: float = 0.12,
) -> list[dict[str, Any]]:
    edges = session.scalars(
        select(GraphEdge).where(
            GraphEdge.edge_type == "ROLE_REQUIRES_SKILL",
            GraphEdge.source_key == f"role:{role_group}",
        )
    ).all()
    canon = {c.name_norm: c for c in session.scalars(select(SkillCanonical)).all()}
    rows = []
    for e in edges:
        props = e.properties or {}
        support = float(props.get("support") or 0)
        if support < min_support:
            continue
        norm = str(props.get("skill_norm") or e.target_key.replace("skill:", "", 1))
        c = canon.get(norm)
        trend, trend_src = _extract_trend(props)
        rows.append(
            {
                "skill_norm": norm,
                "skill_name": str(props.get("skill_name") or (c.name if c else norm)),
                "support": support,
                "trend": trend,
                "trend_source": trend_src,
                "cluster": (c.cluster_name if c else None)
                or props.get("cluster")
                or "Other",
                "weight": float(e.weight or support),
            }
        )
    rows.sort(key=lambda x: (x["support"], x["trend"]), reverse=True)
    return rows


def _trend_label(trend: float) -> str:
    return f"{trend:+.1%}"


def _priority(support: float, trend: float) -> float:
    # растущие gaps чуть важнее
    return round(support * (1.0 + max(-0.3, min(0.5, trend))), 4)


def _readiness(have_norms: set[str], market: list[dict[str, Any]]) -> tuple[float, float]:
    if not market:
        return 0.0, 0.0
    total_w = sum(float(m["support"]) for m in market)
    got_w = sum(float(m["support"]) for m in market if m["skill_norm"] in have_norms)
    if total_w <= 0:
        return 0.0, 0.0
    return round(got_w / total_w, 4), total_w


def build_learning_steps(
    gaps: list[SkillAdvice],
    *,
    market_core: list[dict[str, Any]],
    courses: list[CourseFit],
    have_norms: set[str],
    per_step: int = 3,
) -> list[LearningStep]:
    """Делит пробелы на последовательные шаги обучения."""
    if not gaps:
        return []

    remaining = list(gaps)
    steps: list[LearningStep] = []

    # Шаг 1 — фундамент: максимальный support
    foundation = sorted(remaining, key=lambda g: g.support, reverse=True)[:per_step]
    found_norms = {g.skill_norm for g in foundation}
    remaining = [g for g in remaining if g.skill_norm not in found_norms]

    # Шаг 4 заранее: растущие
    rising = [g for g in remaining if g.trend >= 0.03]
    rising = sorted(rising, key=lambda g: (g.trend, g.support), reverse=True)[:per_step]
    rise_norms = {g.skill_norm for g in rising}
    mid_pool = [g for g in remaining if g.skill_norm not in rise_norms]

    # Шаг 2–3 из середины по priority
    core = mid_pool[:per_step]
    core_norms = {g.skill_norm for g in core}
    depth = [g for g in mid_pool if g.skill_norm not in core_norms][:per_step]

    # если rising пуст — добираем из хвоста
    if not rising:
        rising = [
            g
            for g in gaps
            if g.skill_norm not in found_norms | core_norms | {x.skill_norm for x in depth}
        ][:per_step]

    blueprints = [
        (1, "Фундамент", "База, без которой сложно закрыть ядро роли", "foundation", foundation),
        (2, "Ядро вселенной", "Самое частое в вакансиях вашей роли", "core", core),
        (3, "Глубина профиля", "Усиливаем конкурентоспособность", "depth", depth),
        (4, "На волне тренда", "Растущие навыки — задел на будущее", "trend", rising),
    ]

    total_w = sum(float(m["support"]) for m in market_core) or 1.0
    for num, title, subtitle, focus, skills in blueprints:
        if not skills:
            continue
        gain = sum(g.support for g in skills) / total_w
        skill_names = {g.skill_name for g in skills}
        step_courses = []
        for c in courses:
            if skill_names.intersection(c.skills_covered):
                step_courses.append(c.title)
        steps.append(
            LearningStep(
                step=num,
                title=title,
                subtitle=subtitle,
                skills=skills,
                focus=focus,
                readiness_gain=round(gain, 4),
                course_titles=step_courses[:3],
            )
        )
    # перенумеровать подряд
    for i, s in enumerate(steps, start=1):
        s.step = i
    return steps


def recommend_personal_path(
    session: Session,
    *,
    target_role: str,
    raw_skills: list[str],
    min_support: float = 0.12,
    top_gaps: int = 12,
    top_adjacent: int = 6,
    core_skills: int = 25,
) -> PersonalPathResult:
    resolved = resolve_skills(session, raw_skills)
    have_norms = {r.skill_norm for r in resolved if r.match_method != "unmatched"}
    unmatched = [r.raw for r in resolved if r.match_method == "unmatched"]

    market_full = _role_market(session, target_role, min_support=min_support)
    # ядро роли — топ по support; readiness считаем по ядру, иначе размывается хвостом
    market = market_full[:core_skills]
    market_by_norm = {m["skill_norm"]: m for m in market_full}
    readiness, _ = _readiness(have_norms, market)

    have: list[SkillAdvice] = []
    gaps: list[SkillAdvice] = []
    for m in market:
        trend = float(m["trend"])
        advice = SkillAdvice(
            skill_name=m["skill_name"],
            skill_norm=m["skill_norm"],
            cluster=m["cluster"],
            support=round(m["support"], 4),
            trend=round(trend, 4),
            priority=_priority(m["support"], trend),
            reason="",
            status="have" if m["skill_norm"] in have_norms else "gap",
            trend_source=str(m.get("trend_source") or "edge"),
            trend_label=_trend_label(trend),
        )
        if advice.status == "have":
            advice.reason = (
                f"Уже есть · demand {advice.support:.0%} · тренд {advice.trend_label}"
            )
            have.append(advice)
        else:
            trend_note = (
                "растёт"
                if advice.trend >= 0.03
                else ("падает" if advice.trend <= -0.03 else "стабильно")
            )
            advice.reason = (
                f"В ядре роли ({advice.support:.0%} вакансий) · "
                f"тренд {advice.trend_label} ({trend_note})"
            )
            gaps.append(advice)

    gaps.sort(key=lambda g: g.priority, reverse=True)
    have.sort(key=lambda g: g.support, reverse=True)

    # бонус: ваши навыки вне ядра целевой роли
    bonus: list[SkillAdvice] = []
    core_norms = {m["skill_norm"] for m in market}
    for r in resolved:
        if r.match_method == "unmatched" or r.skill_norm in core_norms:
            continue
        extra = market_by_norm.get(r.skill_norm)
        tr = float((extra or {}).get("trend") or 0)
        bonus.append(
            SkillAdvice(
                skill_name=r.skill_name,
                skill_norm=r.skill_norm,
                cluster=(extra or {}).get("cluster") or "—",
                support=float((extra or {}).get("support") or 0),
                trend=tr,
                priority=0.0,
                reason="Есть у вас, но не в топ-ядре выбранной вселенной — полезно для соседних ролей",
                status="bonus",
                trend_source=str((extra or {}).get("trend_source") or "—"),
                trend_label=_trend_label(tr),
            )
        )

    learning_path = gaps[:top_gaps]

    # соседние вселенные — readiness тоже по ядру
    roles = session.scalars(
        select(GraphEdge.source_key).where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL").distinct()
    ).all()
    role_names = sorted({rk.replace("role:", "", 1) for rk in roles if rk.startswith("role:")})
    adjacent: list[UniverseFit] = []
    for role in role_names:
        mkt_full = _role_market(session, role, min_support=min_support)
        mkt = mkt_full[:core_skills]
        if len(mkt) < 5:
            continue
        ready, total_w = _readiness(have_norms, mkt)
        missing = [x["skill_name"] for x in mkt if x["skill_norm"] not in have_norms][:5]
        matching = [x["skill_name"] for x in mkt if x["skill_norm"] in have_norms][:5]
        adjacent.append(
            UniverseFit(
                role_group=role,
                readiness=ready,
                covered=sum(1 for x in mkt if x["skill_norm"] in have_norms),
                required=len(mkt),
                missing_top=missing,
                matching_top=matching,
                market_weight=round(total_w, 3),
            )
        )
    adjacent.sort(
        key=lambda u: (u.role_group == target_role, u.readiness, u.covered / max(u.required, 1)),
        reverse=True,
    )
    target_fit = next((u for u in adjacent if u.role_group == target_role), None)
    others = [u for u in adjacent if u.role_group != target_role][:top_adjacent]
    adjacent = ([target_fit] if target_fit else []) + others

    # курсы под gaps
    gap_norms = {g.skill_norm for g in learning_path}
    gap_priority = {g.skill_norm: g.priority for g in learning_path}
    courses_db = session.scalars(select(Course).options(selectinload(Course.skills))).all()
    canon = {c.id: c for c in session.scalars(select(SkillCanonical)).all()}
    course_fits: list[CourseFit] = []
    for course in courses_db:
        covered = []
        score = 0.0
        for cs in course.skills:
            c = canon.get(cs.canonical_id)
            if not c or c.name_norm not in gap_norms:
                continue
            covered.append(c.name)
            score += gap_priority.get(c.name_norm, 0.1)
        if not covered:
            continue
        course_fits.append(
            CourseFit(
                course_code=course.code,
                title=course.title,
                duration_hours=int(course.duration_hours or 0),
                skills_covered=covered,
                gap_score=round(score, 3),
                reason=f"Закрывает: {', '.join(covered)}",
            )
        )
    course_fits.sort(key=lambda c: c.gap_score, reverse=True)
    course_fits = course_fits[:8]

    learning_steps = build_learning_steps(
        learning_path,
        market_core=market,
        courses=course_fits,
        have_norms=have_norms,
        per_step=3,
    )

    if readiness >= 0.7:
        summary = (
            f"Вы уже близки к вселенной «{target_role}» (готовность {readiness:.0%}). "
            "Доберите 2–4 ключевых навыка из пути обучения — и профиль станет конкурентным."
        )
    elif readiness >= 0.4:
        summary = (
            f"База для «{target_role}» есть (готовность {readiness:.0%}). "
            "Сфокусируйтесь на навыках с высоким support и положительным трендом."
        )
    elif have_norms:
        summary = (
            f"Стартовая точка для «{target_role}» (готовность {readiness:.0%}). "
            "Пройдите шаги обучения по порядку — каждый шаг поднимает готовность к рынку."
        )
    else:
        summary = (
            "Добавьте навыки, которые у вас уже есть — тогда появятся персональный путь "
            "и соседние вселенные."
        )

    return PersonalPathResult(
        target_role=target_role,
        readiness=readiness,
        resolved=resolved,
        unmatched=unmatched,
        have=have,
        gaps=gaps,
        bonus=bonus,
        learning_path=learning_path,
        learning_steps=learning_steps,
        adjacent=adjacent,
        courses=course_fits,
        summary=summary,
        catalog_size=len(market),
    )


def get_personal_path(
    *,
    target_role: str,
    raw_skills: list[str],
    min_support: float = 0.12,
) -> dict[str, Any]:
    with SessionLocal() as session:
        result = recommend_personal_path(
            session,
            target_role=target_role,
            raw_skills=raw_skills,
            min_support=min_support,
        )
        catalog = skill_catalog(session, role_group=target_role, min_support=0.08)
        all_catalog = skill_catalog(session, role_group=None, min_support=0.1, limit=250)

        def _adv(a: SkillAdvice) -> dict[str, Any]:
            return {
                "skill": a.skill_name,
                "skill_norm": a.skill_norm,
                "cluster": a.cluster,
                "support": a.support,
                "trend": a.trend,
                "trend_label": a.trend_label,
                "trend_source": a.trend_source,
                "priority": a.priority,
                "reason": a.reason,
                "status": a.status,
            }

        return {
            "target_role": result.target_role,
            "readiness": result.readiness,
            "summary": result.summary,
            "unmatched": result.unmatched,
            "resolved": [
                {
                    "raw": r.raw,
                    "skill": r.skill_name,
                    "skill_norm": r.skill_norm,
                    "method": r.match_method,
                }
                for r in result.resolved
            ],
            "have": [_adv(a) for a in result.have],
            "gaps": [_adv(a) for a in result.gaps],
            "bonus": [_adv(a) for a in result.bonus],
            "learning_path": [_adv(a) for a in result.learning_path],
            "learning_steps": [
                {
                    "step": s.step,
                    "title": s.title,
                    "subtitle": s.subtitle,
                    "focus": s.focus,
                    "readiness_gain": s.readiness_gain,
                    "course_titles": s.course_titles,
                    "skills": [_adv(a) for a in s.skills],
                }
                for s in result.learning_steps
            ],
            "adjacent": [
                {
                    "role": u.role_group,
                    "readiness": u.readiness,
                    "covered": u.covered,
                    "required": u.required,
                    "missing_top": u.missing_top,
                    "matching_top": u.matching_top,
                    "is_target": u.role_group == result.target_role,
                }
                for u in result.adjacent
            ],
            "courses": [
                {
                    "code": c.course_code,
                    "title": c.title,
                    "hours": c.duration_hours,
                    "skills": c.skills_covered,
                    "score": c.gap_score,
                    "reason": c.reason,
                }
                for c in result.courses
            ],
            "catalog": catalog,
            "all_catalog": all_catalog,
        }
