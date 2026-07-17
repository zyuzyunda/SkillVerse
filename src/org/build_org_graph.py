"""Добавление org-узлов и рёбер в graph_nodes / graph_edges (без удаления market)."""

from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from src.db.models import (
    Course,
    CourseSkill,
    Department,
    Employee,
    EmployeeSkill,
    GraphEdge,
    GraphNode,
    Position,
    SkillCanonical,
)
from src.db.session import Base, SessionLocal, engine

ORG_NODE_TYPES = {"employee", "department", "position", "course"}
ORG_EDGE_TYPES = {
    "EMPLOYEE_HAS_SKILL",
    "EMPLOYEE_IN_DEPT",
    "EMPLOYEE_HAS_POSITION",
    "POSITION_MAPS_TO_ROLE",
    "COURSE_TEACHES_SKILL",
    "DEPT_CONTAINS_POSITION",
}


def _upsert_node(
    session: Session,
    *,
    node_key: str,
    node_type: str,
    label: str,
    properties: dict[str, Any],
) -> None:
    existing = session.scalar(select(GraphNode).where(GraphNode.node_key == node_key))
    if existing:
        existing.node_type = node_type
        existing.label = label
        existing.properties = properties
    else:
        session.add(
            GraphNode(
                node_key=node_key,
                node_type=node_type,
                label=label,
                properties=properties,
            )
        )


def _upsert_edge(
    session: Session,
    *,
    source_key: str,
    target_key: str,
    edge_type: str,
    weight: float,
    properties: dict[str, Any],
) -> None:
    existing = session.scalar(
        select(GraphEdge).where(
            GraphEdge.source_key == source_key,
            GraphEdge.target_key == target_key,
            GraphEdge.edge_type == edge_type,
        )
    )
    if existing:
        existing.weight = weight
        existing.properties = properties
    else:
        session.add(
            GraphEdge(
                source_key=source_key,
                target_key=target_key,
                edge_type=edge_type,
                weight=weight,
                properties=properties,
            )
        )


def clear_org_graph(session: Session) -> None:
    session.execute(delete(GraphEdge).where(GraphEdge.edge_type.in_(ORG_EDGE_TYPES)))
    session.execute(delete(GraphNode).where(GraphNode.node_type.in_(ORG_NODE_TYPES)))
    session.flush()


def build_org_graph() -> dict[str, Any]:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        employees = session.scalars(
            select(Employee)
            .options(
                selectinload(Employee.skills),
                selectinload(Employee.department),
                selectinload(Employee.position),
            )
            .where(Employee.is_active.is_(True))
        ).all()
        if not employees:
            raise SystemExit("Нет сотрудников — сначала PYTHONPATH=. python -m src.org.generate_synthetic")

        depts = session.scalars(select(Department)).all()
        positions = session.scalars(select(Position)).all()
        courses = session.scalars(
            select(Course).options(selectinload(Course.skills))
        ).all()
        canon_by_id = {
            c.id: c for c in session.scalars(select(SkillCanonical)).all()
        }

        clear_org_graph(session)

        # nodes
        for d in depts:
            _upsert_node(
                session,
                node_key=f"dept:{d.code}",
                node_type="department",
                label=d.name,
                properties={"code": d.code, "department_id": d.id},
            )
        for p in positions:
            _upsert_node(
                session,
                node_key=f"position:{p.code}",
                node_type="position",
                label=p.title,
                properties={
                    "code": p.code,
                    "role_group": p.role_group,
                    "level": p.level,
                    "position_id": p.id,
                },
            )
            # position → market role
            _upsert_edge(
                session,
                source_key=f"position:{p.code}",
                target_key=f"role:{p.role_group}",
                edge_type="POSITION_MAPS_TO_ROLE",
                weight=1.0,
                properties={"role_group": p.role_group},
            )

        for c in courses:
            _upsert_node(
                session,
                node_key=f"course:{c.code}",
                node_type="course",
                label=c.title,
                properties={
                    "code": c.code,
                    "duration_hours": c.duration_hours,
                    "course_id": c.id,
                },
            )
            for cs in c.skills:
                skill = canon_by_id.get(cs.canonical_id)
                if not skill:
                    continue
                _upsert_edge(
                    session,
                    source_key=f"course:{c.code}",
                    target_key=f"skill:{skill.name_norm}",
                    edge_type="COURSE_TEACHES_SKILL",
                    weight=1.0,
                    properties={
                        "skill_name": skill.name,
                        "canonical_id": skill.id,
                    },
                )

        # dept ↔ position via employees' positions (unique pairs)
        dept_pos: set[tuple[str, str]] = set()
        for emp in employees:
            dept_code = emp.department.code
            pos_code = emp.position.code
            dept_pos.add((dept_code, pos_code))

            _upsert_node(
                session,
                node_key=f"employee:{emp.employee_code}",
                node_type="employee",
                label=emp.full_name,
                properties={
                    "employee_code": emp.employee_code,
                    "employee_id": emp.id,
                    "experience_years": emp.experience_years,
                    "department": emp.department.name,
                    "position": emp.position.title,
                    "role_group": emp.position.role_group,
                },
            )
            _upsert_edge(
                session,
                source_key=f"employee:{emp.employee_code}",
                target_key=f"dept:{dept_code}",
                edge_type="EMPLOYEE_IN_DEPT",
                weight=1.0,
                properties={"department": emp.department.name},
            )
            _upsert_edge(
                session,
                source_key=f"employee:{emp.employee_code}",
                target_key=f"position:{pos_code}",
                edge_type="EMPLOYEE_HAS_POSITION",
                weight=1.0,
                properties={"position": emp.position.title},
            )
            for es in emp.skills:
                skill = canon_by_id.get(es.canonical_id)
                if not skill:
                    continue
                _upsert_edge(
                    session,
                    source_key=f"employee:{emp.employee_code}",
                    target_key=f"skill:{skill.name_norm}",
                    edge_type="EMPLOYEE_HAS_SKILL",
                    weight=float(es.proficiency),
                    properties={
                        "skill_name": skill.name,
                        "proficiency": es.proficiency,
                        "canonical_id": skill.id,
                    },
                )

        for dept_code, pos_code in dept_pos:
            _upsert_edge(
                session,
                source_key=f"dept:{dept_code}",
                target_key=f"position:{pos_code}",
                edge_type="DEPT_CONTAINS_POSITION",
                weight=1.0,
                properties={},
            )

        session.commit()

        from sqlalchemy import func

        n_org_nodes = session.scalar(
            select(func.count()).select_from(GraphNode).where(
                GraphNode.node_type.in_(ORG_NODE_TYPES)
            )
        )
        n_org_edges = session.scalar(
            select(func.count()).select_from(GraphEdge).where(
                GraphEdge.edge_type.in_(ORG_EDGE_TYPES)
            )
        )
        stats = {
            "org_nodes": n_org_nodes,
            "org_edges": n_org_edges,
            "employees": len(employees),
            "courses": len(courses),
        }
        print("Org-граф готов:", stats)
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build org layer of knowledge graph")
    parser.parse_args()
    build_org_graph()


if __name__ == "__main__":
    main()
