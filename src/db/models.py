from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.session import Base


class ParseRun(Base):
    __tablename__ = "parse_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    queries: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    vacancies_fetched: Mapped[int] = mapped_column(Integer, default=0)
    vacancies_upserted: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    vacancies: Mapped[list[Vacancy]] = relationship(back_populates="parse_run")


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("hh_id", name="uq_vacancies_hh_id"),
        Index("ix_vacancies_published_at", "published_at"),
        Index("ix_vacancies_search_query", "search_query"),
        Index("ix_vacancies_role_group", "role_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hh_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    employer_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    area_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    experience_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    experience_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    employment_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    schedule_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    salary_from: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    salary_to: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    salary_gross: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_hh: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    alternate_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    search_query: Mapped[str] = mapped_column(String(256), nullable=False)
    role_group: Mapped[str] = mapped_column(String(64), nullable=False, default="ml_ai")

    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parse_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parse_runs.id"), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parse_run: Mapped[Optional[ParseRun]] = relationship(back_populates="vacancies")
    skills: Mapped[list[VacancySkill]] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan"
    )


class VacancySkill(Base):
    __tablename__ = "vacancy_skills"
    __table_args__ = (
        UniqueConstraint("vacancy_id", "skill_name", "source", name="uq_vacancy_skill_source"),
        Index("ix_vacancy_skills_name", "skill_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"))
    skill_name: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="hh_key_skills")
    skill_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    vacancy: Mapped[Vacancy] = relationship(back_populates="skills")


class SkillCanonical(Base):
    """Справочник нормализованных компетенций."""

    __tablename__ = "skills_canonical"
    __table_args__ = (UniqueConstraint("name_norm", name="uq_skills_canonical_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(256), nullable=False)
    skill_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cluster_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    vacancy_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    aliases: Mapped[list[SkillAlias]] = relationship(back_populates="canonical")


class SkillAlias(Base):
    """Сырые написания навыков → canonical."""

    __tablename__ = "skill_aliases"
    __table_args__ = (UniqueConstraint("alias_norm", name="uq_skill_aliases_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_id: Mapped[int] = mapped_column(ForeignKey("skills_canonical.id", ondelete="CASCADE"))
    match_method: Mapped[str] = mapped_column(String(32), default="exact")  # exact|alias|fuzzy

    canonical: Mapped[SkillCanonical] = relationship(back_populates="aliases")


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("node_key", name="uq_graph_nodes_key"),
        Index("ix_graph_nodes_type", "node_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)  # role|skill
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_key", "target_key", "edge_type", name="uq_graph_edges_src_tgt_type"
        ),
        Index("ix_graph_edges_type", "edge_type"),
        Index("ix_graph_edges_source", "source_key"),
        Index("ix_graph_edges_target", "target_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(256), nullable=False)
    target_key: Mapped[str] = mapped_column(String(256), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # ROLE_REQUIRES_SKILL | SKILL_CO_OCCURS | EMPLOYEE_HAS_SKILL | ...
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("code", name="uq_departments_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    parent_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    employees: Mapped[list[Employee]] = relationship(back_populates="department")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("code", name="uq_positions_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    role_group: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(32), default="middle")  # junior|middle|senior|lead

    employees: Mapped[list[Employee]] = relationship(back_populates="position")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("employee_code", name="uq_employees_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    experience_years: Mapped[float] = mapped_column(Float, default=2.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    department: Mapped[Department] = relationship(back_populates="employees")
    position: Mapped[Position] = relationship(back_populates="employees")
    skills: Mapped[list[EmployeeSkill]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )


class EmployeeSkill(Base):
    __tablename__ = "employee_skills"
    __table_args__ = (
        UniqueConstraint("employee_id", "canonical_id", name="uq_employee_skill"),
        Index("ix_employee_skills_canonical", "canonical_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    canonical_id: Mapped[int] = mapped_column(ForeignKey("skills_canonical.id"))
    proficiency: Mapped[float] = mapped_column(Float, default=0.7)  # 0..1
    source: Mapped[str] = mapped_column(String(32), default="synthetic")

    employee: Mapped[Employee] = relationship(back_populates="skills")
    canonical: Mapped[SkillCanonical] = relationship()


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("code", name="uq_courses_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, default=20)

    skills: Mapped[list[CourseSkill]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CourseSkill(Base):
    __tablename__ = "course_skills"
    __table_args__ = (
        UniqueConstraint("course_id", "canonical_id", name="uq_course_skill"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    canonical_id: Mapped[int] = mapped_column(ForeignKey("skills_canonical.id"))

    course: Mapped[Course] = relationship(back_populates="skills")
    canonical: Mapped[SkillCanonical] = relationship()

