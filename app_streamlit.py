"""HR Dashboard: компетенции, дефициты, рекомендации."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.db.models import Department, Employee, EmployeeSkill, GraphEdge, GraphNode
from src.db.session import SessionLocal
from src.graph.graph_service import market_subgraph, role_subgraph
from src.graph.viz import (
    PLOTLY_DIVERGING,
    PLOTLY_HEAT,
    PLOTLY_SEQUENTIAL,
    render_multiverse_html,
    render_subgraph_html,
    render_universes_map_html,
    role_color,
)
from src.market.dashboard import (
    MARKET_MODES,
    drill_employer_skills,
    drill_geo_skills,
    fetch_skill_daily,
    get_market_dashboard,
    list_daily_skill_options,
    published_date_bounds,
)
from src.market.multiverse import annotate_graph_trends
from src.market.personal_path import get_personal_path, skill_catalog
from src.market.pulse import get_pulse, load_market_skills, top_skills_across_roles
from src.org.recommendations import (
    recommend_courses,
    recommend_mobility,
    recommend_training,
)
from src.org.risk_metrics import compute_cluster_coverage, compute_skill_risks
from src.org.skill_gaps import (
    compute_cluster_gaps,
    compute_skill_gaps,
    list_departments,
    list_role_groups,
)

st.set_page_config(
    page_title="Карьерная мультивселенная · Competence Platform",
    page_icon="◈",
    layout="wide",
)


@st.cache_data(ttl=60)
def _roles() -> list[str]:
    with SessionLocal() as session:
        return list_role_groups(session)


@st.cache_data(ttl=60)
def _departments() -> list[tuple[str, str]]:
    with SessionLocal() as session:
        return list_departments(session)


@st.cache_data(ttl=60)
def _overview_stats() -> dict:
    with SessionLocal() as session:
        return {
            "employees": session.scalar(select(func.count()).select_from(Employee)) or 0,
            "departments": session.scalar(select(func.count()).select_from(Department)) or 0,
            "skills": session.scalar(
                select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "skill")
            )
            or 0,
            "clusters": session.scalar(
                select(func.count()).select_from(GraphNode).where(GraphNode.node_type == "cluster")
            )
            or 0,
            "market_edges": session.scalar(
                select(func.count())
                .select_from(GraphEdge)
                .where(GraphEdge.edge_type == "ROLE_REQUIRES_SKILL")
            )
            or 0,
        }


def page_overview() -> None:
    st.header("Обзор")
    stats = _overview_stats()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Сотрудники", stats["employees"])
    c2.metric("Подразделения", stats["departments"])
    c3.metric("Навыки в графе", stats["skills"])
    c4.metric("Кластеры", stats["clusters"])
    c5.metric("Рыночные связи роль↔навык", stats["market_edges"])

    with SessionLocal() as session:
        rows = session.execute(
            select(Department.name, func.count(Employee.id))
            .join(Employee, Employee.department_id == Department.id)
            .where(Employee.is_active.is_(True))
            .group_by(Department.name)
            .order_by(func.count(Employee.id).desc())
        ).all()
    if rows:
        df = pd.DataFrame(rows, columns=["department", "employees"])
        fig = px.bar(df, x="department", y="employees", title="Численность по подразделениям")
        st.plotly_chart(fig, use_container_width=True)


def page_gaps(role: str, dept_code: str | None) -> None:
    st.header("Дефицит компетенций")
    with SessionLocal() as session:
        gaps = compute_skill_gaps(
            session,
            role_group=role,
            department_code=dept_code,
            min_market_support=0.2,
        )
        cluster_gaps = compute_cluster_gaps(
            session,
            role_group=role,
            department_code=dept_code,
            min_market_support=0.2,
        )
    if not gaps:
        st.warning("Нет данных по дефицитам для выбранных фильтров.")
        return

    tab_skills, tab_clusters = st.tabs(["По навыкам", "По кластерам"])

    with tab_skills:
        df = pd.DataFrame(
            [
                {
                    "skill": g.skill_name,
                    "cluster": g.cluster,
                    "market": g.market_support,
                    "org": g.org_coverage,
                    "gap": g.gap,
                    "trend": g.trend,
                }
                for g in gaps[:30]
            ]
        )
        st.caption(
            f"Сотрудников в выборке: {gaps[0].employees_total}. "
            "Gap = доля вакансий рынка − доля сотрудников с навыком."
        )
        fig = px.bar(
            df.head(15),
            x="skill",
            y=["market", "org"],
            barmode="group",
            title=f"Рынок vs организация — {role}",
            labels={"value": "доля", "skill": "навык", "variable": ""},
        )
        st.plotly_chart(fig, use_container_width=True)

        fig2 = px.scatter(
            df,
            x="market",
            y="gap",
            color="cluster",
            size=(df["trend"].clip(lower=0) + 0.05) * 20,
            hover_name="skill",
            title="Карта дефицитов (цвет = кластер, размер ~ рост тренда)",
            labels={"market": "востребованность на рынке", "gap": "дефицит в компании"},
        )
        st.plotly_chart(fig2, use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_clusters:
        if not cluster_gaps:
            st.info("Кластеры ещё не собраны. Пересоберите market-граф.")
            return
        cdf = pd.DataFrame(
            [
                {
                    "cluster": g.cluster,
                    "market": g.market_support,
                    "org": g.org_coverage,
                    "gap": g.gap,
                    "trend": g.trend,
                    "skills": g.skills_in_signal,
                    "top_skill": g.top_skill,
                    "holders": g.holders,
                }
                for g in cluster_gaps
            ]
        )
        st.caption(
            "Market = max support навыка в кластере; org = доля сотрудников "
            "с хотя бы одним навыком кластера."
        )
        fig = px.bar(
            cdf,
            x="cluster",
            y=["market", "org"],
            barmode="group",
            title=f"Дефицит по кластерам — {role}",
            labels={"value": "доля", "cluster": "кластер", "variable": ""},
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(cdf, use_container_width=True, hide_index=True)


def page_skills_distribution(role: str, dept_code: str | None) -> None:
    st.header("Распределение навыков")
    with SessionLocal() as session:
        employees = session.scalars(
            select(Employee)
            .options(
                selectinload(Employee.skills).selectinload(EmployeeSkill.canonical),
                selectinload(Employee.position),
                selectinload(Employee.department),
            )
            .where(Employee.is_active.is_(True))
        ).all()
        employees = [e for e in employees if e.position.role_group == role]
        if dept_code:
            employees = [e for e in employees if e.department.code == dept_code]

        counts: dict[str, int] = {}
        cluster_counts: dict[str, int] = {}
        for e in employees:
            for s in e.skills:
                name = s.canonical.name if s.canonical else str(s.canonical_id)
                counts[name] = counts.get(name, 0) + 1
                cluster = (s.canonical.cluster_name if s.canonical else None) or "Other"
                cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1

    if not counts:
        st.info("Нет навыков в выборке.")
        return

    tab1, tab2 = st.tabs(["По навыкам", "По кластерам"])
    with tab1:
        df = (
            pd.DataFrame([{"skill": k, "holders": v} for k, v in counts.items()])
            .sort_values("holders", ascending=False)
            .head(25)
        )
        fig = px.bar(df, x="skill", y="holders", title=f"Топ навыков сотрудников — {role}")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)

    with tab2:
        cdf = (
            pd.DataFrame([{"cluster": k, "mentions": v} for k, v in cluster_counts.items()])
            .sort_values("mentions", ascending=False)
        )
        fig = px.bar(cdf, x="cluster", y="mentions", title=f"Навыки по кластерам — {role}")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(cdf, use_container_width=True, hide_index=True)


def page_risks(role: str, dept_code: str | None) -> None:
    st.header("Риски компетенций")
    with SessionLocal() as session:
        risks = compute_skill_risks(
            session, role_group=role, department_code=dept_code, min_market_support=0.15
        )
        coverage = compute_cluster_coverage(
            session, role_group=role, department_code=dept_code
        )

    if not risks:
        st.warning("Нет рыночных навыков для выбранных фильтров.")
        return

    spof = [r for r in risks if r.is_spof]
    critical = [r for r in risks if r.is_critical]
    absent = [r for r in risks if r.holders == 0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SPOF (1–2 носителя)", len(spof))
    c2.metric("Нет носителей", len(absent))
    c3.metric("Critical skills", len(critical))
    c4.metric("Сотрудников", risks[0].employees_total)

    tab1, tab2, tab3, tab4 = st.tabs(
        ["SPOF", "Нет носителей", "Critical", "Coverage по кластерам"]
    )

    with tab1:
        st.caption(
            "Single Point of Failure — рыночный навык только у 1–2 человек (bus factor)."
        )
        if not spof:
            st.success("SPOF не найдены при текущих порогах.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "skill": r.skill_name,
                        "cluster": r.cluster,
                        "market": r.market_support,
                        "holders": r.holders,
                        "who": ", ".join(r.holder_names) or "—",
                        "risk": r.risk_score,
                    }
                    for r in spof[:40]
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab2:
        st.caption("Рыночные навыки, которых в выборке нет ни у кого.")
        if not absent:
            st.success("Все рыночные навыки покрыты хотя бы одним человеком.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "skill": r.skill_name,
                        "cluster": r.cluster,
                        "market": r.market_support,
                        "trend": r.trend,
                        "risk": r.risk_score,
                    }
                    for r in absent[:40]
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab3:
        st.caption(
            "Critical: высокий спрос на рынке, низкое покрытие в компании, мало носителей."
        )
        if not critical:
            st.info("Нет critical skills.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "skill": r.skill_name,
                        "cluster": r.cluster,
                        "market": r.market_support,
                        "org": r.coverage,
                        "gap": r.gap,
                        "holders": r.holders,
                        "risk": r.risk_score,
                    }
                    for r in critical[:40]
                ]
            )
            fig = px.scatter(
                df,
                x="market",
                y="org",
                size="risk",
                color="cluster",
                hover_name="skill",
                title="Critical skills: рынок vs покрытие",
                labels={"market": "рынок", "org": "покрытие в компании"},
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab4:
        if not coverage:
            st.info("Нет данных coverage.")
        else:
            cdf = pd.DataFrame(
                [
                    {
                        "department": c.department,
                        "cluster": c.cluster,
                        "coverage": c.coverage,
                        "holders": c.holders,
                        "employees": c.employees_total,
                    }
                    for c in coverage
                    if c.cluster != "Other"
                ]
            )
            if cdf.empty:
                st.info("Нет кластеров для heatmap.")
            else:
                pivot = cdf.pivot_table(
                    index="cluster", columns="department", values="coverage", aggfunc="mean"
                ).fillna(0)
                fig = px.imshow(
                    pivot,
                    aspect="auto",
                    color_continuous_scale="YlOrRd_r",
                    title="Coverage кластеров по подразделениям",
                    labels={"color": "coverage"},
                )
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    cdf.sort_values(["coverage", "cluster"]),
                    use_container_width=True,
                    hide_index=True,
                )


def page_graph(role: str) -> None:
    st.header("Подграф роли")
    st.caption(
        "Перетаскивайте узлы мышью · двигайте холст зажатой ЛКМ по фону · "
        "зуум колёсиком · кнопки навигации справа внизу."
    )
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.slider("Топ навыков", min_value=10, max_value=40, value=20, step=5)
        min_support = st.slider("Min market support", 0.10, 0.40, 0.15, 0.05)
    with c2:
        include_clusters = st.checkbox("Показать кластеры", value=True)
        include_cooc = st.checkbox("Показать co-occurrence между навыками", value=True)

    data = role_subgraph(
        role,
        top_skills=top_n,
        min_support=min_support,
        include_clusters=include_clusters,
        include_cooc=include_cooc,
    )
    if not data["nodes"]:
        st.warning("Подграф пуст — проверьте роль и пороги.")
        return

    st.caption(f"Узлов: {data['n_nodes']} · рёбер: {data['n_edges']} · роль: `{role}`")

    legend = st.columns(3)
    legend[0].markdown("🟦 **role** — роль (свой цвет)")
    legend[1].markdown("🟧 **cluster** — кластер")
    legend[2].markdown("🟩 **skill** — навык (размер ~ support)")

    html = render_subgraph_html(data, height="680px", role_order=[role])
    components.html(html, height=700, scrolling=False)

    skills = [n for n in data["nodes"] if n["node_type"] == "skill"]
    if skills:
        with st.expander("Таблица навыков на графе"):
            sdf = (
                pd.DataFrame(skills)[["label", "cluster", "support"]]
                .rename(columns={"label": "skill"})
                .sort_values("support", ascending=False)
            )
            st.dataframe(sdf, use_container_width=True, hide_index=True)


def page_market_pulse(role: str) -> None:
    st.header("Пульс рынка")
    st.caption(
        "Обзор рынка труда DS/ML/AI по вакансиям hh.ru (два среза: весна и лето–осень 2025). "
        "Без внутренних сотрудников — только рынок."
    )

    all_roles = _roles()
    default_roles = [role] if role in all_roles else (all_roles[:3] if all_roles else [])

    st.subheader("Граф рынка")
    st.caption(
        "Выберите роли — на графе останутся их топ-навыки. "
        "Общие навыки связывают роли. Перетаскивайте узлы, двигайте холст, зуум колёсиком."
    )
    selected_roles = st.multiselect(
        "Роли на графе",
        options=all_roles,
        default=default_roles,
        help="Можно выбрать несколько ролей для сравнения",
    )
    g1, g2, g3 = st.columns(3)
    with g1:
        top_n = st.slider("Топ навыков на роль", 8, 30, 12, 2, key="pulse_top")
    with g2:
        min_sup = st.slider("Min support", 0.10, 0.40, 0.18, 0.02, key="pulse_sup")
    with g3:
        show_clusters = st.checkbox("Кластеры", value=True, key="pulse_cl")
        show_cooc = st.checkbox("Co-occurrence", value=False, key="pulse_cooc")

    if selected_roles:
        # легенда ролей
        legend_cols = st.columns(min(len(selected_roles), 4))
        for i, r in enumerate(selected_roles):
            color = role_color(r, selected_roles)
            legend_cols[i % len(legend_cols)].markdown(
                f"<span style='display:inline-block;width:10px;height:10px;"
                f"border-radius:3px;background:{color};margin-right:6px'></span>"
                f"**{r}**",
                unsafe_allow_html=True,
            )

        data = market_subgraph(
            selected_roles,
            top_skills=top_n,
            min_support=min_sup,
            include_clusters=show_clusters,
            include_cooc=show_cooc,
        )
        st.caption(
            f"Узлов: {data['n_nodes']} · рёбер: {data['n_edges']} · "
            f"роли: {', '.join(selected_roles)}"
        )
        if data["nodes"]:
            html = render_subgraph_html(data, height="720px", role_order=selected_roles)
            components.html(html, height=740, scrolling=False)
        else:
            st.warning("Подграф пуст — снизьте порог support или выберите другие роли.")
    else:
        st.info("Выберите хотя бы одну роль, чтобы построить граф.")

    st.divider()
    st.subheader("Обзор рынка")

    scope = st.radio(
        "Срез аналитики",
        ["Весь рынок", f"Роль из сайдбара: {role}", "Выбранные на графе"],
        horizontal=True,
        key="pulse_scope",
    )
    if scope.startswith("Весь"):
        role_filter = None
    elif scope.startswith("Выбранные"):
        role_filter = selected_roles[0] if len(selected_roles) == 1 else None
        # для нескольких ролей покажем весь рынок в таблицах, но rising по первой —
        # лучше: если несколько — весь рынок; если одна из выбранных — она
        if len(selected_roles) == 1:
            role_filter = selected_roles[0]
        else:
            role_filter = None
    else:
        role_filter = role

    # если выбрано несколько ролей на графе и срез "Выбранные" — агрегируем по ним в caption
    if scope.startswith("Выбранные") and len(selected_roles) > 1:
        st.caption(
            f"В таблицах ниже — весь рынок; на графе сравниваются: {', '.join(selected_roles)}. "
            "Для узкой аналитики оставьте одну роль."
        )

    pulse = get_pulse(role_group=role_filter)
    multi_mode = bool(scope.startswith("Выбранные") and len(selected_roles) > 1)
    if multi_mode:
        full = get_pulse(role_group=None)
        with SessionLocal() as session:
            rows = []
            for r in selected_roles:
                rows.extend(load_market_skills(session, role_group=r, min_support=0.1))
        pulse["rising"] = [r for r in full["rising"] if r.get("role") in selected_roles][:15]
        pulse["falling"] = [r for r in full["falling"] if r.get("role") in selected_roles][:15]
        pulse["top_skills"] = top_skills_across_roles(rows, top=25)
        pulse["role_top"] = [
            {
                "skill": r.skill_name,
                "cluster": r.cluster,
                "support": r.support,
                "trend": r.trend,
                "from_year": r.trend_from_year,
                "to_year": r.trend_to_year,
                "support_by_year": r.support_by_year,
                "role": r.role_group,
            }
            for r in sorted(rows, key=lambda x: (x.support, x.trend), reverse=True)[:25]
        ]
        role_filter = None

    ov = pulse["overview"]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Вакансии", ov["vacancies"])
    y_min, y_max = ov.get("year_min"), ov.get("year_max")
    m2.metric("Годы данных", f"{y_min or '—'}–{y_max or '—'}")
    m3.metric("Источников", len(ov.get("by_source") or {}))
    m4.metric("Навыки в графе", ov["canonical_skills"])
    m5.metric("Кластеры", ov["clusters"])

    c_year, c_src = st.columns(2)
    with c_year:
        year_df = pd.DataFrame(
            [{"year": y, "vacancies": n} for y, n in sorted((ov.get("by_year") or {}).items())]
        )
        if not year_df.empty:
            fig_y = px.bar(
                year_df,
                x="year",
                y="vacancies",
                title="Вакансии по годам (published_at)",
                labels={"vacancies": "вакансий", "year": "год"},
            )
            fig_y.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF")
            st.plotly_chart(fig_y, use_container_width=True)
    with c_src:
        src_df = pd.DataFrame(
            [{"source": k, "vacancies": v} for k, v in (ov.get("by_source") or {}).items()]
        )
        if not src_df.empty:
            fig_s = px.bar(
                src_df,
                x="source",
                y="vacancies",
                title="Источники данных",
                color="source",
                color_discrete_sequence=["#0D9488", "#2563EB", "#F97316", "#94A3B8"],
            )
            fig_s.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", showlegend=False)
            st.plotly_chart(fig_s, use_container_width=True)

    role_df = pd.DataFrame(
        [{"role": k, "vacancies": v} for k, v in ov["by_role"].items()]
    ).sort_values("vacancies", ascending=False)
    role_df["selected"] = role_df["role"].isin(selected_roles)
    fig_roles = px.bar(
        role_df,
        x="role",
        y="vacancies",
        color="selected",
        color_discrete_map={True: "#0D9488", False: "#CBD5E1"},
        title="Вакансии по role_group",
        labels={"vacancies": "вакансий", "role": "роль", "selected": "на графе"},
    )
    fig_roles.update_layout(
        plot_bgcolor="#F8FAFC",
        paper_bgcolor="#FFFFFF",
        font_color="#0F172A",
        showlegend=True,
    )
    st.plotly_chart(fig_roles, use_container_width=True)

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Топ навыков", "Растёт / падает", "Кластеры", "Роль × кластер"]
    )

    with tab1:
        if multi_mode:
            df = pd.DataFrame(pulse["role_top"])
            if df.empty:
                st.info("Нет данных.")
            else:
                fig = px.bar(
                    df.head(18),
                    x="skill",
                    y="support",
                    color="role",
                    color_discrete_sequence=[role_color(r, selected_roles) for r in selected_roles],
                    title="Топ навыков по выбранным ролям",
                    labels={"support": "доля вакансий", "skill": "навык"},
                )
                fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", font_color="#0F172A")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            top = pulse["role_top"] if role_filter else pulse["top_skills"]
            if not top:
                st.info("Нет данных.")
            else:
                if role_filter:
                    df = pd.DataFrame(top)
                    fig = px.bar(
                        df.head(15),
                        x="skill",
                        y="support",
                        color="trend",
                        color_continuous_scale=PLOTLY_DIVERGING,
                        title=f"Топ навыков на рынке — {role_filter}",
                        labels={"support": "доля вакансий", "skill": "навык"},
                    )
                else:
                    df = pd.DataFrame(top)
                    fig = px.bar(
                        df.head(15),
                        x="skill",
                        y="max_support",
                        color="avg_trend",
                        color_continuous_scale=PLOTLY_DIVERGING,
                        hover_data=["cluster", "best_role", "roles"],
                        title="Топ навыков по рынку (max support по ролям)",
                        labels={"max_support": "max support", "skill": "навык"},
                    )
                fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", font_color="#0F172A")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df, use_container_width=True, hide_index=True)

    with tab2:
        st.caption(
            "Тренд считается **внутри одного источника** (hh отдельно, Kaggle отдельно), "
            "без смешения корпусов. Иначе csv_seed (много тегов) vs hh даёт ложный обвал. "
            "Δ = support(последний год) − support(предыдущий) по `published_at`."
        )
        c_up, c_down = st.columns(2)
        with c_up:
            st.subheader("Растёт")
            rising = pulse["rising"]
            if not rising:
                st.info("Нет сильных ростов.")
            else:
                rdf = pd.DataFrame(rising)
                fig = px.bar(
                    rdf,
                    x="skill",
                    y="trend",
                    color="support",
                    color_continuous_scale=PLOTLY_SEQUENTIAL,
                    title="Растущие навыки",
                    labels={"trend": "Δ support", "skill": "навык"},
                )
                fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(rdf, use_container_width=True, hide_index=True)
        with c_down:
            st.subheader("Падает")
            falling = pulse["falling"]
            if not falling:
                st.info("Нет сильных падений.")
            else:
                fdf = pd.DataFrame(falling)
                fig = px.bar(
                    fdf,
                    x="skill",
                    y="trend",
                    color="support",
                    color_continuous_scale=PLOTLY_SEQUENTIAL,
                    title="Снижающиеся навыки",
                    labels={"trend": "Δ support", "skill": "навык"},
                )
                fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(fdf, use_container_width=True, hide_index=True)

    with tab3:
        clusters = pulse["clusters"]
        if not clusters:
            st.info("Нет кластеров.")
        else:
            cdf = pd.DataFrame(clusters)
            fig = px.scatter(
                cdf,
                x="avg_support",
                y="avg_trend",
                size="skills",
                color="max_support",
                color_continuous_scale=PLOTLY_SEQUENTIAL,
                hover_name="cluster",
                hover_data=["top_skill"],
                title="Карта кластеров: спрос × тренд",
                labels={
                    "avg_support": "средний support",
                    "avg_trend": "средний тренд",
                },
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(cdf, use_container_width=True, hide_index=True)

    with tab4:
        rc = pulse["role_cluster"]
        if not rc:
            st.info("Нет рёбер ROLE_REQUIRES_CLUSTER — пересоберите market-граф.")
        else:
            rdf = pd.DataFrame(rc)
            if selected_roles:
                rdf_view = rdf[rdf["role"].isin(selected_roles)]
                if rdf_view.empty:
                    rdf_view = rdf
            else:
                rdf_view = rdf
            pivot = rdf_view.pivot_table(
                index="cluster", columns="role", values="support", aggfunc="max"
            ).fillna(0)
            fig = px.imshow(
                pivot,
                aspect="auto",
                color_continuous_scale=PLOTLY_HEAT,
                title="Спрос кластеров по ролям (max support)",
                labels={"color": "support"},
            )
            fig.update_layout(paper_bgcolor="#FFFFFF", font_color="#0F172A")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                rdf_view.sort_values(["role", "support"], ascending=[True, False]),
                use_container_width=True,
                hide_index=True,
            )


def page_recommendations(role: str, dept_code: str | None) -> None:
    st.header("Рекомендации")
    with SessionLocal() as session:
        gaps, people = recommend_training(
            session, role_group=role, department_code=dept_code
        )
        courses = recommend_courses(session, role_group=role, department_code=dept_code)
        mobility = recommend_mobility(session, target_role=role)

    tab1, tab2, tab3 = st.tabs(["Кого обучить", "Курсы", "Внутренняя мобильность"])

    with tab1:
        if not people:
            st.info("Нет кандидатов на обучение.")
        else:
            st.write("Приоритет: закрыть ключевые рыночные дефициты.")
            df = pd.DataFrame(
                [
                    {
                        "code": p.employee_code,
                        "name": p.full_name,
                        "position": p.position,
                        "level": p.level,
                        "missing": ", ".join(p.missing_skills),
                        "score": p.score,
                    }
                    for p in people
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            for p in people[:5]:
                st.markdown(f"**{p.full_name}** — {p.reason}")

    with tab2:
        if not courses:
            st.info("Нет подходящих курсов под текущие дефициты.")
        else:
            for c in courses:
                st.subheader(c.title)
                st.caption(f"{c.course_code} · {c.duration_hours} ч · кандидатов: {c.candidates_count}")
                st.write(c.reason)
                st.progress(min(1.0, c.gap_score / max(g.gap for g in gaps)) if gaps else 0.0)

    with tab3:
        if not mobility:
            st.info("Нет кандидатов на внутренний переход с достаточным overlap.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "name": m.full_name,
                        "from": m.current_position,
                        "from_role": m.current_role,
                        "overlap": m.overlap,
                        "has": ", ".join(m.matching_skills[:5]),
                        "need": ", ".join(m.missing_skills[:5]),
                    }
                    for m in mobility
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)


def page_employees(role: str, dept_code: str | None) -> None:
    st.header("Сотрудники")
    with SessionLocal() as session:
        employees = session.scalars(
            select(Employee)
            .options(
                selectinload(Employee.skills).selectinload(EmployeeSkill.canonical),
                selectinload(Employee.position),
                selectinload(Employee.department),
            )
            .where(Employee.is_active.is_(True))
        ).all()
        if role:
            employees = [e for e in employees if e.position.role_group == role]
        if dept_code:
            employees = [e for e in employees if e.department.code == dept_code]

        rows = []
        for e in employees:
            skills = ", ".join(
                sorted(s.canonical.name for s in e.skills if s.canonical)[:12]
            )
            rows.append(
                {
                    "code": e.employee_code,
                    "name": e.full_name,
                    "department": e.department.name,
                    "position": e.position.title,
                    "level": e.position.level,
                    "exp_years": e.experience_years,
                    "skills_n": len(e.skills),
                    "skills": skills,
                }
            )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def page_assistant(role: str, dept_code: str | None) -> None:
    from src.config import settings
    from src.llm.assistant import ask
    from src.llm.scenarios import SCENARIOS

    st.header("LLM-ассистент")
    st.caption(
        "Ответы опираются на граф компетенций и модуль рекомендаций. "
        + (
            "Режим: Groq LLM."
            if settings.groq_api_key
            else "Ключ GROQ_API_KEY не задан — работает фактический fallback."
        )
    )

    labels = {f"{s.title}": s for s in SCENARIOS}
    choice = st.selectbox("Готовый сценарий", ["— свободный вопрос —"] + list(labels.keys()))
    default_q = labels[choice].question if choice in labels else ""
    question = st.text_area("Вопрос", value=default_q, height=100)

    col_a, col_b = st.columns([1, 3])
    with col_a:
        use_llm = st.checkbox("Использовать LLM (Groq)", value=bool(settings.groq_api_key))
    with col_b:
        run = st.button("Спросить", type="primary")

    if run and question.strip():
        with st.spinner("Готовлю ответ..."):
            intent = labels[choice].intent if choice in labels else None
            result = ask(
                question.strip(),
                role_group=role,
                department_code=dept_code,
                use_llm=use_llm,
                intent=intent,
            )
        st.markdown(result["answer"])
        st.caption(
            f"mode={result['mode']} · intent={result['intent']} · role={result['role_group']}"
        )
        with st.expander("Контекст (факты из БД)"):
            st.json(
                {
                    "gaps": result["context"]["gaps"],
                    "trainees": result["context"]["trainees"][:5],
                    "courses": result["context"]["courses"][:5],
                    "mobility": result["context"]["mobility"][:5],
                }
            )
    elif run:
        st.warning("Введите вопрос.")


def page_career_multiverse(role: str) -> None:
    """Обзорный дашборд рынка: Россия (hh) / Мир (Kaggle)."""
    st.markdown(
        """
        <style>
        .mv-dash-hero {
            background: linear-gradient(125deg, #F8FAFC 0%, #ECFEFF 40%, #FEF3C7 100%);
            border: 1px solid #CBD5E1;
            border-radius: 18px;
            padding: 1.35rem 1.6rem 1.15rem;
            margin-bottom: 0.85rem;
        }
        .mv-dash-hero h1 {
            font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
            font-size: 1.7rem; font-weight: 700; letter-spacing: -0.02em;
            color: #0B1220; margin: 0 0 0.3rem 0;
        }
        .mv-dash-hero p { color: #334155; margin: 0; max-width: 48rem; line-height: 1.4; }
        .mv-kicker {
            font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em;
            color: #0F766E; font-weight: 600; margin-bottom: 0.3rem;
        }
        </style>
        <div class="mv-dash-hero">
          <div class="mv-kicker">Market intelligence</div>
          <h1>Мультивселенная · обзор рынка</h1>
          <p>
            Спады и рост навыков, редкие и уникальные компетенции,
            география и компании — в одном интерактивном срезе.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    mode_label = st.radio(
        "Рынок",
        ["Россия · hh.ru", "Мир · Kaggle"],
        horizontal=True,
        key="mv_market_mode",
        help="Россия = только hh.ru. Мир = только Kaggle AI Jobs.",
    )
    market_mode = "world" if mode_label.startswith("Мир") else "russia"
    cfg = MARKET_MODES[market_mode]

    all_roles = _roles()
    role_opts = st.multiselect(
        "Фильтр ролей (опционально)",
        options=all_roles,
        default=[],
        key="mv_dash_roles",
        help="Пусто = весь рынок в выбранном режиме.",
    )

    dash = get_market_dashboard(
        market_mode=market_mode,
        role_groups=role_opts or None,
        min_support=0.08,
    )
    pulse = dash["pulse"]
    y_span = (
        f"{pulse['year_min']}–{pulse['year_max']}" if pulse.get("year_min") else "—"
    )

    st.caption(
        f"**{cfg['label']}** · {cfg['subtitle']} · горизонт {y_span}"
    )

    # —— PULSE ——
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Вакансии", f"{pulse['vacancies']:,}".replace(",", " "))
    k2.metric("Уник. навыки", f"{pulse['unique_skills']:,}".replace(",", " "))
    k3.metric(cfg["geo_title"], pulse["geo_places"])
    k4.metric(
        "Компании" if cfg["show_companies"] else "Роли",
        pulse["employers"] if cfg["show_companies"] else pulse["roles"],
    )
    k5.metric("Покрытие датами", f"{pulse['date_coverage']:.0%}")
    k6.metric("С навыками", f"{pulse['skill_coverage']:.0%}")

    c_year, c_role = st.columns([1.2, 1])
    with c_year:
        ydf = pd.DataFrame(
            [{"year": y, "vacancies": n} for y, n in sorted(pulse["by_year"].items())]
        )
        if not ydf.empty:
            fig = px.area(
                ydf,
                x="year",
                y="vacancies",
                title="Вакансии по годам",
                color_discrete_sequence=["#0D9488"],
            )
            fig.update_layout(
                plot_bgcolor="#F8FAFC",
                paper_bgcolor="#FFFFFF",
                margin=dict(t=48, b=20, l=20, r=20),
                height=280,
            )
            st.plotly_chart(fig, use_container_width=True)
    with c_role:
        rdf = pd.DataFrame(
            [{"role": k, "vacancies": v} for k, v in pulse["by_role"].items()]
        ).sort_values("vacancies", ascending=True)
        if not rdf.empty:
            fig = px.bar(
                rdf,
                x="vacancies",
                y="role",
                orientation="h",
                title="Вакансии по ролям",
                color_discrete_sequence=["#2563EB"],
            )
            fig.update_layout(
                plot_bgcolor="#F8FAFC",
                paper_bgcolor="#FFFFFF",
                margin=dict(t=48, b=20, l=20, r=20),
                height=280,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # —— SKILLS ——
    st.subheader("Навыки рынка")
    t_rise, t_fall, t_rare, t_uniq, t_bridge, t_demand = st.tabs(
        ["Растёт", "Падает", "Редкие", "Уникальные для роли", "Мосты", "Топ спроса"]
    )

    with t_rise:
        rising = dash["rising"]
        if not rising:
            st.info("Нет сильных ростов в этом срезе.")
        else:
            df = pd.DataFrame(rising)
            fig = px.bar(
                df,
                x="skill",
                y="trend",
                color="support",
                color_continuous_scale=PLOTLY_SEQUENTIAL,
                hover_data=["role", "cluster", "support_label"],
                title="Растущий спрос",
                labels={"trend": "тренд", "skill": "навык"},
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=360)
            st.plotly_chart(fig, use_container_width=True)

            if market_mode == "russia":
                st.markdown("##### Динамика по дням")
                st.caption(
                    "Ось — `published_at`. hh.ru собран волнами (весна 2025 / лето 2026). "
                    "По умолчанию — топ растущих; можно добавить любые навыки из каталога."
                )
                d0, d1 = published_date_bounds(
                    market_mode=market_mode, role_groups=role_opts or None
                )
                skill_opts = list_daily_skill_options(
                    market_mode=market_mode,
                    role_groups=role_opts or None,
                    min_support=0.05,
                )
                label_to_meta = {o["skill"]: o for o in skill_opts}
                all_skill_labels = [o["skill"] for o in skill_opts]
                default_skills = [
                    x["skill"] for x in rising[:6] if x["skill"] in label_to_meta
                ]
                if not default_skills and all_skill_labels:
                    default_skills = all_skill_labels[:6]

                f1, f2, f3 = st.columns([1.2, 1.2, 0.8])
                with f1:
                    selected_skill_labels = st.multiselect(
                        "Навыки на графике",
                        options=all_skill_labels,
                        default=default_skills,
                        key="mv_daily_skills",
                        help="Стартовый набор — топ растущих. Добавляйте любые из списка.",
                    )
                with f2:
                    if d0 and d1:
                        date_range = st.date_input(
                            "Период",
                            value=(d0, d1),
                            min_value=d0,
                            max_value=d1,
                            key="mv_daily_dates",
                        )
                    else:
                        date_range = None
                        st.caption("Нет дат published_at")
                with f3:
                    rolling = st.slider("Окно сглаживания, дней", 1, 21, 7, key="mv_daily_roll")
                    show_raw = st.checkbox("Показать сырые дни", value=False, key="mv_daily_raw")

                date_from = date_to = None
                if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
                    date_from, date_to = date_range[0], date_range[1]
                elif date_range is not None and not isinstance(date_range, (list, tuple)):
                    date_from = date_to = date_range

                if selected_skill_labels:
                    selected_meta = [label_to_meta[s] for s in selected_skill_labels if s in label_to_meta]
                    # если навык из rising нет в catalog (редко) — добавим вручную
                    for x in rising:
                        if x["skill"] in selected_skill_labels and x["skill"] not in label_to_meta:
                            selected_meta.append(
                                {"skill": x["skill"], "skill_norm": x.get("skill_norm") or x["skill"]}
                            )
                    daily = fetch_skill_daily(
                        market_mode=market_mode,
                        skills=selected_meta,
                        role_groups=role_opts or None,
                        date_from=date_from,
                        date_to=date_to,
                        rolling_days=rolling,
                    )
                    if not daily:
                        st.info("Нет точек за выбранный период / навыки.")
                    else:
                        ddf = pd.DataFrame(daily)
                        ddf["date"] = pd.to_datetime(ddf["date"])
                        y_col = "support" if (show_raw or rolling <= 1) else "support_roll"
                        fig2 = px.line(
                            ddf,
                            x="date",
                            y=y_col,
                            color="skill",
                            markers=True,
                            hover_data=["support", "support_roll", "vacancies", "hits"],
                            title="Динамика навыков по дням публикации",
                            labels={
                                y_col: "доля вакансий" + ("" if rolling <= 1 or show_raw else f" ({rolling}д)"),
                                "date": "дата публикации",
                                "skill": "навык",
                            },
                        )
                        fig2.update_traces(mode="lines+markers")
                        fig2.update_layout(
                            plot_bgcolor="#F8FAFC",
                            paper_bgcolor="#FFFFFF",
                            height=400,
                            hovermode="x unified",
                        )
                        st.plotly_chart(fig2, use_container_width=True)
                        if show_raw and rolling > 1:
                            fig3 = px.scatter(
                                ddf,
                                x="date",
                                y="support",
                                color="skill",
                                size="vacancies",
                                hover_data=["hits", "vacancies"],
                                title="Сырые дневные доли",
                                labels={"support": "доля за день", "date": "дата"},
                            )
                            fig3.update_layout(
                                plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=340
                            )
                            st.plotly_chart(fig3, use_container_width=True)
                else:
                    st.info("Выберите хотя бы один навык.")
            elif market_mode == "world":
                rows_tl = []
                for item in rising[:6]:
                    for y, v in sorted(
                        (item.get("support_by_year") or {}).items(), key=lambda x: int(x[0])
                    ):
                        rows_tl.append(
                            {"skill": item["skill"], "year": int(y), "support": float(v)}
                        )
                if rows_tl:
                    st.caption("Kaggle: в датасете только год размещения — дневной оси нет.")
                    fig2 = px.line(
                        pd.DataFrame(rows_tl),
                        x="year",
                        y="support",
                        color="skill",
                        markers=True,
                        title="Динамика топ растущих по годам",
                    )
                    fig2.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=320)
                    st.plotly_chart(fig2, use_container_width=True)

    with t_fall:
        falling = dash["falling"]
        if not falling:
            st.info("Нет сильных падений в этом срезе.")
        else:
            df = pd.DataFrame(falling)
            fig = px.bar(
                df,
                x="skill",
                y="trend",
                color="support",
                color_continuous_scale=PLOTLY_SEQUENTIAL,
                hover_data=["role", "cluster", "support_label"],
                title="Сжимающийся спрос",
                labels={"trend": "тренд", "skill": "навык"},
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=360)
            st.plotly_chart(fig, use_container_width=True)
            if market_mode == "russia" and falling:
                st.caption(
                    "Дневную динамику падающих смотрите на вкладке «Растёт»: "
                    "добавьте нужные навыки в фильтр графика."
                )

    with t_rare:
        rare = dash["rare"]
        if market_mode == "world":
            st.caption(
                "В Kaggle узкий словарь навыков (~11 типов) — «редкость» относительная внутри корпуса."
            )
        if not rare:
            st.info("Мало редких навыков (или слишком мелкий корпус).")
        else:
            df = pd.DataFrame(rare)
            fig = px.bar(
                df,
                x="skill",
                y="share",
                title="Редкие навыки (доля вакансий с навыком)",
                color_discrete_sequence=["#D97706"],
                labels={"share": "доля", "skill": "навык"},
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=360)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df, use_container_width=True, hide_index=True)

    with t_uniq:
        unique = dash["unique"]
        if market_mode == "world":
            st.caption(
                "На Kaggle навыки сильно пересекаются между ролями — уникальных мало."
            )
        if not unique:
            st.info("Нет ярко «роль-специфичных» навыков в срезе.")
        else:
            df = pd.DataFrame(unique)
            fig = px.scatter(
                df,
                x="support",
                y="uniqueness",
                size="support",
                color="role",
                hover_name="skill",
                hover_data=["other_max", "cluster"],
                title="Уникальные для роли (высокий спрос здесь, низкий в других)",
                labels={"support": "спрос в роли", "uniqueness": "отрыв от других ролей"},
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=400)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                df.rename(
                    columns={
                        "skill": "навык",
                        "role": "роль",
                        "support": "спрос",
                        "other_max": "макс. в других",
                        "uniqueness": "уникальность",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    with t_bridge:
        bridges = dash["bridges"]
        if not bridges:
            st.info("Мостов между ролями мало в этом срезе.")
        else:
            bdf = pd.DataFrame(
                [
                    {
                        "skill": b["skill"],
                        "roles_count": b["roles_count"],
                        "max_support": b["max_support"],
                        "avg_trend": b["avg_trend"],
                        "roles": ", ".join(b["roles"][:6]),
                        "cluster": b["cluster"],
                    }
                    for b in bridges
                ]
            )
            fig = px.scatter(
                bdf,
                x="max_support",
                y="avg_trend",
                size="roles_count",
                color="roles_count",
                hover_name="skill",
                hover_data=["roles", "cluster"],
                color_continuous_scale=PLOTLY_SEQUENTIAL,
                title="Мосты между вселенными",
                labels={
                    "max_support": "макс. спрос",
                    "avg_trend": "ср. тренд",
                    "roles_count": "ролей",
                },
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=400)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(bdf, use_container_width=True, hide_index=True)

    with t_demand:
        top_d = dash["top_demand"]
        if not top_d:
            st.info("Нет skill-тегов в корпусе.")
        else:
            df = pd.DataFrame(top_d)
            fig = px.bar(
                df,
                x="skill",
                y="share",
                title="Топ навыков по частоте в вакансиях",
                color_discrete_sequence=["#0D9488"],
                labels={"share": "доля вакансий", "skill": "навык"},
            )
            fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF", height=360)
            st.plotly_chart(fig, use_container_width=True)

    hm = dash["heatmap"]
    if hm.get("skills") and hm.get("roles"):
        st.markdown("##### Роль × навык")
        fig = px.imshow(
            hm["matrix"],
            x=hm["skills"],
            y=hm["roles"],
            aspect="auto",
            color_continuous_scale=PLOTLY_HEAT,
            title="Спрос навыков по ролям",
            labels={"color": "support"},
        )
        fig.update_layout(paper_bgcolor="#FFFFFF", height=420)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # —— GEO ——
    st.subheader(cfg["geo_title"])
    geo = dash["geo"]
    if not geo:
        st.info("Нет геоданных в этом режиме.")
    else:
        g1, g2 = st.columns([1.2, 1])
        with g1:
            gdf = pd.DataFrame(geo)
            fig = px.treemap(
                gdf,
                path=["place"],
                values="vacancies",
                color="share",
                color_continuous_scale=PLOTLY_SEQUENTIAL,
                title=f"Распределение вакансий · {cfg['geo_title'].lower()}",
            )
            fig.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=420)
            st.plotly_chart(fig, use_container_width=True)
        with g2:
            places = [g["place"] for g in geo]
            place = st.selectbox(
                f"Drill-down: навыки в {cfg['geo_title'][:-1].lower()}е",
                options=places,
                key="mv_geo_place",
            )
            skills = drill_geo_skills(market_mode, place, top=10)
            if skills:
                sdf = pd.DataFrame(skills)
                fig = px.bar(
                    sdf,
                    x="count",
                    y="skill",
                    orientation="h",
                    title=f"Топ навыков · {place}",
                    color_discrete_sequence=["#2563EB"],
                )
                fig.update_layout(
                    plot_bgcolor="#F8FAFC",
                    paper_bgcolor="#FFFFFF",
                    height=420,
                    yaxis={"categoryorder": "total ascending"},
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("Нет навыков для выбранного места.")

    # —— COMPANIES (RU) ——
    if cfg["show_companies"]:
        st.divider()
        st.subheader("Компании")
        employers = dash["employers"]
        if not employers:
            st.info("Нет работодателей в срезе.")
        else:
            e1, e2 = st.columns([1.2, 1])
            with e1:
                edf = pd.DataFrame(employers)
                fig = px.bar(
                    edf.sort_values("vacancies"),
                    x="vacancies",
                    y="employer",
                    orientation="h",
                    title="Топ работодателей по числу вакансий",
                    color="share",
                    color_continuous_scale=PLOTLY_SEQUENTIAL,
                )
                fig.update_layout(
                    plot_bgcolor="#F8FAFC",
                    paper_bgcolor="#FFFFFF",
                    height=480,
                    yaxis={"categoryorder": "total ascending"},
                )
                st.plotly_chart(fig, use_container_width=True)
            with e2:
                emps = [e["employer"] for e in employers]
                emp = st.selectbox("Drill-down: стек компании", options=emps, key="mv_emp")
                skills = drill_employer_skills(market_mode, emp, top=10)
                if skills:
                    sdf = pd.DataFrame(skills)
                    fig = px.bar(
                        sdf,
                        x="count",
                        y="skill",
                        orientation="h",
                        title=f"Навыки в вакансиях · {emp}",
                        color_discrete_sequence=["#D97706"],
                    )
                    fig.update_layout(
                        plot_bgcolor="#F8FAFC",
                        paper_bgcolor="#FFFFFF",
                        height=480,
                        yaxis={"categoryorder": "total ascending"},
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.caption("Нет skill-тегов у выбранной компании.")

    st.divider()

    # —— GRAPH ——
    with st.expander("Карта вселенных (граф ролей и навыков)", expanded=False):
        st.caption(
            "Исследовательский слой: роли и топ-навыки. "
            "Бирюзовый ≈ растёт, розовый ≈ падает, синий ≈ мост."
        )
        graph_roles = role_opts or list(pulse["by_role"].keys())[:4]
        if not graph_roles:
            st.info("Нет ролей для графа.")
        else:
            gcol1, gcol2, gcol3 = st.columns(3)
            with gcol1:
                top_n = st.slider("Топ навыков / роль", 8, 24, 12, 2, key="mv_g_top")
            with gcol2:
                min_sup = st.slider("Min support", 0.08, 0.35, 0.15, 0.01, key="mv_g_sup")
            with gcol3:
                show_cooc = st.checkbox("Co-occurrence", value=False, key="mv_g_cooc")
            data = market_subgraph(
                graph_roles,
                top_skills=top_n,
                min_support=min_sup,
                include_clusters=True,
                include_cooc=show_cooc,
            )
            with SessionLocal() as session:
                rows = load_market_skills(session, role_group=None, min_support=0.08)
            data = annotate_graph_trends(data, rows)
            if data["nodes"]:
                html = render_multiverse_html(data, height="640px", role_order=graph_roles)
                components.html(html, height=660, scrolling=False)
            else:
                st.warning("Подграф пуст — снизьте порог support.")


def page_my_universe(default_role: str) -> None:
    """Личная вселенная: навыки на входе → готовность → путь развития."""

    def _fmt_trend(v: float) -> str:
        try:
            return f"{float(v):+.1%}"
        except (TypeError, ValueError):
            return "0%"

    st.markdown(
        """
        <style>
        .mu-hero {
            background: linear-gradient(120deg, #ECFDF5 0%, #EFF6FF 55%, #FFF7ED 100%);
            border: 1px solid #CBD5E1;
            border-radius: 18px;
            padding: 1.5rem 1.75rem 1.25rem;
            margin-bottom: 1rem;
        }
        .mu-hero h1 {
            font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: #0B1220;
            margin: 0 0 0.35rem 0;
        }
        .mu-hero p { color: #334155; margin: 0; max-width: 46rem; line-height: 1.45; }
        .mu-kicker {
            font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.12em;
            color: #0F766E; font-weight: 600; margin-bottom: 0.35rem;
        }
        </style>
        <div class="mu-hero">
          <div class="mu-kicker">Персональный маршрут</div>
          <h1>Моя профессиональная вселенная</h1>
          <p>
            Выберите роль-вселенную, укажите навыки, которые уже есть —
            получите готовность к рынку, пробелы и путь развития.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    roles = _roles()
    role_idx = roles.index(default_role) if default_role in roles else 0
    target = st.selectbox(
        "Ваша целевая вселенная (роль)",
        options=roles,
        index=role_idx,
        key="mu_role",
        help="Роль, под которую считаем рыночные требования и пробелы.",
    )

    with SessionLocal() as session:
        role_cat = skill_catalog(session, role_group=target, min_support=0.08, limit=200)
        all_cat = skill_catalog(session, role_group=None, min_support=0.1, limit=300)
    role_names = [r["skill"] for r in role_cat]
    all_names = sorted({r["skill"] for r in all_cat} | set(role_names))

    # пресет применяется до создания multiselect (иначе Streamlit запрещает писать в key виджета)
    if "mu_skills_preset" in st.session_state:
        preset = st.session_state.pop("mu_skills_preset")
        st.session_state["mu_skills"] = [s for s in preset if s in all_names]

    presets = st.expander("Быстрый старт — примеры профилей", expanded=False)
    with presets:
        p1, p2, p3 = st.columns(3)
        if p1.button("Junior DS", key="mu_p1"):
            st.session_state["mu_skills_preset"] = ["Python", "SQL", "Pandas", "NumPy"]
            st.rerun()
        if p2.button("ML Engineer", key="mu_p2"):
            st.session_state["mu_skills_preset"] = ["Python", "PyTorch", "Docker", "SQL", "Git"]
            st.rerun()
        if p3.button("LLM / Agents", key="mu_p3"):
            st.session_state["mu_skills_preset"] = ["Python", "LLM", "RAG", "LangChain", "Docker"]
            st.rerun()

    c_in1, c_in2 = st.columns([2, 1])
    with c_in1:
        picked = st.multiselect(
            "Навыки, которые у вас уже есть",
            options=all_names,
            default=[],
            key="mu_skills",
            help="Можно выбрать из каталога рынка. Дополнительно — свободный ввод ниже.",
        )
    with c_in2:
        min_sup = st.slider(
            "Порог спроса навыка",
            0.08,
            0.35,
            0.12,
            0.01,
            key="mu_sup",
            help="Минимальная доля вакансий роли с этим навыком. "
            "Ниже порога навык не входит в ядро требований. 0.12 ≈ 12% вакансий.",
        )
        st.caption(f"Сейчас: навык нужен минимум в **{min_sup:.0%}** вакансий роли")


    free = st.text_area(
        "Ещё навыки (через запятую или с новой строки)",
        value="",
        height=80,
        placeholder="Python, SQL, Docker, LLM, …",
        key="mu_free",
    )
    free_parts = []
    for chunk in free.replace("\n", ",").split(","):
        t = chunk.strip()
        if t:
            free_parts.append(t)

    raw_skills = list(dict.fromkeys([*picked, *free_parts]))

    if not raw_skills:
        st.info("Добавьте хотя бы один навык — тогда появятся готовность и рекомендации.")
        if role_names:
            st.caption("Чаще всего рынок просит в этой вселенной:")
            st.write(", ".join(role_names[:12]))
        return

    path = get_personal_path(
        target_role=target,
        raw_skills=raw_skills,
        min_support=min_sup,
    )

    st.success(path["summary"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Готовность к ядру роли", f"{path['readiness']:.0%}")
    m2.metric("Закрыто в ядре", len(path["have"]))
    m3.metric("Пробелы в ядре", len(path["gaps"]))
    m4.metric("В пути обучения", len(path["learning_path"]))
    st.caption("Готовность = доля рыночного веса топ‑навыков роли, которые у вас уже есть.")

    if path["unmatched"]:
        st.warning(
            "Не сопоставлены со справочником: "
            + ", ".join(path["unmatched"])
            + ". Попробуйте другое написание из каталога."
        )

    # интерактивная карта вселенных
    adj = path["adjacent"]
    st.subheader("Карта ваших вселенных")
    st.caption(
        "Центр — вы. ★ бирюзовая вселенная — основной путь. "
        "Вокруг — соседние миры: чем ближе и крупнее узел, тем выше готовность. "
        "Наведите на мир, перетаскивайте, зумите."
    )
    if adj:
        legend = st.columns(4)
        legend[0].markdown("⬛ **Вы** — текущий профиль")
        legend[1].markdown("🟩 **★ основной путь** — целевая роль")
        legend[2].markdown("🟦 **близкий сосед** — готовность ≥45%")
        legend[3].markdown("🟧 / ⬜ **дальше** — нужна доработка")
        html_map = render_universes_map_html(adj, target_role=target, height="560px")
        components.html(html_map, height=580, scrolling=False)
    else:
        st.info("Недостаточно данных по ролям для карты.")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Маршрут обучения", "Что уже есть", "Соседние вселенные", "Курсы"]
    )

    with tab1:
        steps = path.get("learning_steps") or []
        if not steps:
            st.info("Критичных пробелов нет — усиливайте соседние вселенные или трендовые навыки.")
        else:
            st.markdown("##### Пошаговый путь")
            st.caption(
                "Идите по шагам сверху вниз. Отметьте навыки как изученные — они добавятся в профиль, "
                "и маршрут пересчитается. Тренд: изменение спроса (hh YoY или горизонт Kaggle)."
            )

            # прогресс по шагам (сколько skills из шагов уже в профиле)
            have_names = {h["skill"] for h in path["have"]}
            total_step_skills = sum(len(s["skills"]) for s in steps)
            done_step_skills = sum(
                1
                for s in steps
                for sk in s["skills"]
                if sk["skill"] in have_names or sk["skill"] in set(picked)
            )
            st.progress(
                min(1.0, done_step_skills / max(total_step_skills, 1)),
                text=f"Прогресс маршрута: {done_step_skills}/{total_step_skills} навыков",
            )

            # какой шаг сейчас активен (первый, где есть неизученные)
            active_step = None
            for s in steps:
                pending = [sk for sk in s["skills"] if sk["skill"] not in have_names]
                if pending:
                    active_step = s["step"]
                    break
            if active_step is None and steps:
                active_step = steps[-1]["step"]

            for s in steps:
                pending = [sk for sk in s["skills"] if sk["skill"] not in have_names]
                done = [sk for sk in s["skills"] if sk["skill"] in have_names]
                is_active = s["step"] == active_step
                is_done = len(pending) == 0

                if is_done:
                    badge = "✅ пройден"
                elif is_active:
                    badge = "▶ сейчас"
                else:
                    badge = "○ дальше"

                with st.container(border=True):
                    h_l, h_r = st.columns([4, 1])
                    with h_l:
                        st.markdown(
                            f"**Шаг {s['step']}. {s['title']}** · {badge}  \n"
                            f"{s['subtitle']}"
                        )
                    with h_r:
                        st.metric("+готовность", f"+{s['readiness_gain']:.0%}")

                    if is_done:
                        st.caption("Все навыки шага уже в профиле: " + ", ".join(sk["skill"] for sk in done))
                    elif not is_active:
                        st.caption(
                            "Сначала закройте предыдущий шаг. Навыки: "
                            + ", ".join(sk["skill"] for sk in s["skills"])
                        )
                    else:
                        st.markdown("**Изучите сейчас:**")
                        checked: list[str] = []
                        for sk in s["skills"]:
                            already = sk["skill"] in have_names
                            label = (
                                f"{sk['skill']} — спрос {sk['support']:.0%}, "
                                f"тренд {sk.get('trend_label') or _fmt_trend(sk['trend'])}"
                            )
                            if already:
                                st.markdown(f"- ~~{label}~~ ✓")
                            else:
                                if st.checkbox(label, key=f"mu_learn_{s['step']}_{sk['skill_norm']}"):
                                    checked.append(sk["skill"])
                                st.caption(sk.get("reason") or "")

                        if s.get("course_titles"):
                            st.markdown(
                                "Курсы к шагу: " + " · ".join(f"*{t}*" for t in s["course_titles"])
                            )

                        if st.button(
                            "Отметить выбранные как изученные",
                            key=f"mu_complete_{s['step']}",
                            type="primary",
                            disabled=not checked,
                        ):
                            current = list(st.session_state.get("mu_skills") or picked)
                            merged = list(dict.fromkeys([*current, *checked]))
                            st.session_state["mu_skills_preset"] = merged
                            st.rerun()

                        if st.button(
                            "Закрыть весь шаг",
                            key=f"mu_complete_all_{s['step']}",
                        ):
                            names = [sk["skill"] for sk in pending]
                            current = list(st.session_state.get("mu_skills") or picked)
                            merged = list(dict.fromkeys([*current, *names]))
                            st.session_state["mu_skills_preset"] = merged
                            st.rerun()

            with st.expander("Все пробелы списком"):
                lp = path["learning_path"]
                if lp:
                    ldf = pd.DataFrame(lp)
                    ldf["тренд"] = ldf.apply(
                        lambda r: r.get("trend_label") or _fmt_trend(r["trend"]), axis=1
                    )
                    ldf["спрос"] = ldf["support"].map(lambda x: f"{x:.0%}")
                    fig = px.bar(
                        ldf,
                        x="skill",
                        y="priority",
                        color="trend",
                        color_continuous_scale=PLOTLY_DIVERGING,
                        hover_data=["спрос", "тренд", "cluster", "reason"],
                        title="Приоритет = спрос × (1 + тренд)",
                        labels={"priority": "приоритет", "skill": "навык", "trend": "тренд"},
                    )
                    fig.update_layout(plot_bgcolor="#F8FAFC", paper_bgcolor="#FFFFFF")
                    # фиксируем шкалу цвета, чтобы малый тренд не «обнулялся» визуально
                    fig.update_coloraxes(cmin=-0.2, cmax=0.2)
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(
                        ldf[["skill", "cluster", "спрос", "тренд", "priority", "reason"]],
                        use_container_width=True,
                        hide_index=True,
                    )

    with tab2:
        c_have, c_bonus = st.columns(2)
        with c_have:
            st.markdown("**Покрывают целевую вселенную**")
            if path["have"]:
                hdf = pd.DataFrame(path["have"])
                hdf["тренд"] = hdf.apply(
                    lambda r: r.get("trend_label") or _fmt_trend(r["trend"]), axis=1
                )
                hdf["спрос"] = hdf["support"].map(lambda x: f"{x:.0%}")
                st.dataframe(
                    hdf[["skill", "спрос", "тренд", "cluster"]],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("Пока нет пересечений с ядром роли.")
        with c_bonus:
            st.markdown("**Есть у вас, но вне ядра роли**")
            if path["bonus"]:
                st.dataframe(
                    pd.DataFrame(path["bonus"])[["skill", "reason"]],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("Все ваши навыки внутри выбранной вселенной — отлично.")

    with tab3:
        if not adj:
            st.info("Нет данных по ролям.")
        else:
            rows = []
            for a in adj:
                rows.append(
                    {
                        "роль": a["role"],
                        "готовность": a["readiness"],
                        "закрыто": f"{a['covered']}/{a['required']}",
                        "есть": ", ".join(a["matching_top"][:4]),
                        "не хватает": ", ".join(a["missing_top"][:4]),
                        "целевая": "✓" if a.get("is_target") else "",
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "Соседняя вселенная с высокой готовностью — реалистичный карьерный переход "
                "без полного переобучения."
            )

    with tab4:
        courses = path["courses"]
        if not courses:
            st.info("Нет курсов, закрывающих ваши пробелы. Смотрите путь обучения вручную.")
        else:
            cdf = pd.DataFrame(courses)
            st.dataframe(
                cdf.rename(
                    columns={
                        "title": "курс",
                        "hours": "часы",
                        "skills": "закрывает",
                        "score": "score",
                        "reason": "почему",
                    }
                )[["курс", "часы", "закрывает", "score", "почему"]],
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader("Карта вашего пути")
    st.caption(
        "Целевая роль + ваши навыки + топ-пробелы. "
        "Зелёные акценты на графиках выше — то, что уже есть; путь обучения — что добрать."
    )

    # мини-граф: роль + have + learning gaps
    focus_skills = [h["skill"] for h in path["have"][:10]] + [
        g["skill"] for g in path["learning_path"][:10]
    ]
    data = role_subgraph(
        target,
        top_skills=22,
        min_support=min_sup,
        include_clusters=True,
        include_cooc=False,
    )
    # подсветим статусы в title через annotate + properties
    have_set = {h["skill"].lower() for h in path["have"]}
    gap_set = {g["skill"].lower() for g in path["learning_path"]}
    nodes = []
    for n in data.get("nodes", []):
        nn = dict(n)
        if nn.get("node_type") == "skill":
            label = (nn.get("label") or "").lower()
            if label in have_set:
                nn["trend"] = 0.2
                nn["bridge_roles"] = 1
            elif label in gap_set:
                nn["trend"] = -0.2
                nn["bridge_roles"] = 1
            else:
                nn["trend"] = 0.0
        nodes.append(nn)
    data = dict(data)
    data["nodes"] = nodes
    # фильтр: оставим роль, кластеры и навыки из focus / top
    if focus_skills:
        keep_labels = {s.lower() for s in focus_skills}
        keep_ids = {"role:" + target}
        for n in data["nodes"]:
            if n.get("node_type") == "skill" and (n.get("label") or "").lower() in keep_labels:
                keep_ids.add(n["id"])
            if n.get("node_type") == "cluster":
                keep_ids.add(n["id"])
            if n.get("node_type") == "role":
                keep_ids.add(n["id"])
        data["nodes"] = [n for n in data["nodes"] if n["id"] in keep_ids]
        data["edges"] = [
            e
            for e in data["edges"]
            if e["source"] in keep_ids and e["target"] in keep_ids
        ]
        data["n_nodes"] = len(data["nodes"])
        data["n_edges"] = len(data["edges"])

    if data["nodes"]:
        html = render_multiverse_html(data, height="620px", role_order=[target])
        components.html(html, height=640, scrolling=False)
        st.caption(
            "На карте: бирюзовый оттенок ≈ уже есть, розовый ≈ в пути обучения, серый ≈ прочий рынок."
        )
    else:
        st.warning("Не удалось построить карту для выбранных навыков.")


def main() -> None:
    st.title("Карьерная аналитика и компетенции")
    st.caption("Рынок труда во времени · личные пути развития · орг-компетенции")

    roles = _roles()
    depts = _departments()
    if not roles:
        st.error("Нет данных графа. Сначала загрузите вакансии и соберите граф.")
        st.code(
            "\n".join(
                [
                    "PYTHONPATH=. python -m src.graph.build_market_graph",
                    "PYTHONPATH=. python -m src.org.generate_synthetic",
                    "PYTHONPATH=. python -m src.org.build_org_graph",
                ]
            )
        )
        return

    with st.sidebar:
        st.subheader("Фильтры")
        role = st.selectbox("Роль (role_group)", roles, index=roles.index("data_science") if "data_science" in roles else 0)
        dept_options = [("ALL", "Все подразделения")] + depts
        dept_label = st.selectbox(
            "Подразделение",
            options=dept_options,
            format_func=lambda x: x[1],
        )
        dept_code = None if dept_label[0] == "ALL" else dept_label[0]
        page = st.radio(
            "Раздел",
            [
                "Мультивселенная",
                "Моя вселенная",
                "Обзор",
                "Пульс рынка",
                "Дефициты",
                "Риски",
                "Граф",
                "Навыки",
                "Рекомендации",
                "Сотрудники",
                "Ассистент",
            ],
        )

    if page == "Мультивселенная":
        page_career_multiverse(role)
    elif page == "Моя вселенная":
        page_my_universe(role)
    elif page == "Обзор":
        page_overview()
    elif page == "Пульс рынка":
        page_market_pulse(role)
    elif page == "Дефициты":
        page_gaps(role, dept_code)
    elif page == "Риски":
        page_risks(role, dept_code)
    elif page == "Граф":
        page_graph(role)
    elif page == "Навыки":
        page_skills_distribution(role, dept_code)
    elif page == "Рекомендации":
        page_recommendations(role, dept_code)
    elif page == "Ассистент":
        page_assistant(role, dept_code)
    else:
        page_employees(role, dept_code)


if __name__ == "__main__":
    main()
