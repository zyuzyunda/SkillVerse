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
from src.graph.viz import PLOTLY_DIVERGING, PLOTLY_HEAT, PLOTLY_SEQUENTIAL, render_subgraph_html, role_color
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
    page_title="HR Competence Platform",
    page_icon="📊",
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
                "early": r.support_early,
                "late": r.support_late,
                "role": r.role_group,
            }
            for r in sorted(rows, key=lambda x: (x.support, x.trend), reverse=True)[:25]
        ]
        # heatmap уже фильтруется по selected_roles ниже
        role_filter = None

    ov = pulse["overview"]
    labels = ov["period_labels"]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Вакансии", ov["vacancies"])
    m2.metric(labels["early"], ov["period_early"])
    m3.metric(labels["late"], ov["period_late"])
    m4.metric("Навыки в графе", ov["canonical_skills"])
    m5.metric("Кластеры", ov["clusters"])

    role_df = pd.DataFrame(
        [{"role": k, "vacancies": v} for k, v in ov["by_role"].items()]
    ).sort_values("vacancies", ascending=False)
    # подсветка выбранных ролей
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
            f"Тренд = support({labels['late']}) − support({labels['early']}). "
            "Положительный — навык чаще встречается в позднем срезе."
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


def main() -> None:
    st.title("AI-платформа управления компетенциями")
    st.caption("Рынок труда × внутренние компетенции × рекомендации")

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

    if page == "Обзор":
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
