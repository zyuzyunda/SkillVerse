from src.db.models import ParseRun, SkillCanonical, Vacancy, VacancySkill
from src.db.session import Base, engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("OK: tables created in", engine.url)


if __name__ == "__main__":
    init_db()
