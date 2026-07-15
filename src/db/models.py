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
    """Справочник нормализованных компетенций (заполняется на этапе кластеризации)."""

    __tablename__ = "skills_canonical"
    __table_args__ = (UniqueConstraint("name_norm", name="uq_skills_canonical_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(256), nullable=False)
    skill_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cluster_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
