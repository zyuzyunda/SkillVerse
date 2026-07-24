# SkillVerse

AI-платформа управления компетенциями: граф знаний рынка DS/ML/AI + LLM-извлечение навыков + HR/career UI (Streamlit).

Репозиторий: [github.com/zyuzyunda/SkillVerse](https://github.com/zyuzyunda/SkillVerse).

**Архитектура (мультиагентная, гибрид LLM + правила):**  
[docs/architecture_multiagent.md](docs/architecture_multiagent.md)

Новый пайплайн (Блок 1 · секции) разрабатывается в ветке `feature/pipeline-block1`:
```bash
git checkout feature/pipeline-block1
PYTHONPATH=. python -m src.market.split_sections --source hh --resume
PYTHONPATH=. streamlit run app_streamlit.py
# раздел «Блок 1 · Секции»
```

## Запуск через Docker (рекомендуется)

Используется **Colima** + Docker CLI (без Docker Desktop).

```bash
# если Colima ещё не запущена
colima start

cd "AI платформа управления компетенциями предприятия"
# или клон: git clone https://github.com/zyuzyunda/SkillVerse.git


# поднять Postgres + инициализация схемы + seed вакансий
docker compose up --build

# в фоне:
# docker compose up --build -d
```

Postgres доступен на хосте: **localhost:5433**  
(порт 5433, чтобы не конфликтовать с Homebrew Postgres на 5432)

Подключение:
- user: `competence`
- password: `competence`
- db: `competence_platform`

Проверка:

```bash
docker compose exec db psql -U competence -d competence_platform \
  -c "SELECT role_group, count(*) FROM vacancies GROUP BY 1 ORDER BY 2 DESC;"
```

Повторный seed (Experiments, авг–сен 2025):

```bash
SEED_ON_START=1 docker compose run --rm app
```

Исторический срез из helper (мар–апр 2025):

```bash
# CSV уже в data/HHRU_united.csv
PYTHONPATH=. python -m src.market.seed_from_hh_united
```

Сборка market-графа (нормализация → canonical → рёбра с трендом):

```bash
# (рекомендуется) LLM-извлечение навыков из description_text hh-вакансий
# По умолчанию LLM_PROVIDER=auto: Groq → при 403 локальная Ollama (llama3.2:3b)
# ollama serve && ollama pull llama3.2:3b
PYTHONPATH=. python -m src.market.extract_skills_llm --source hh --resume
# только локальная Llama (без VPN); --resume продолжает после Ctrl+C
# PYTHONPATH=. python -m src.market.extract_skills_llm --provider ollama --resume
# тест / офлайн-smoke без API:
# PYTHONPATH=. python -m src.market.extract_skills_llm --limit 5 --provider ollama --no-resume
# PYTHONPATH=. python -m src.market.extract_skills_llm --limit 20 --mock --no-resume

# граф: key skills + llm_extract → ROLE_REQUIRES_SKILL + SKILL_CO_OCCURS (PMI)
PYTHONPATH=. python -m src.graph.build_market_graph
# только РФ:
# PYTHONPATH=. python -m src.graph.build_market_graph --source hh --source csv_seed --skip-org

docker compose exec db psql -U competence -d competence_platform \
  -c "SELECT edge_type, count(*) FROM graph_edges GROUP BY 1;"
```

Синтетика сотрудников + org-слой графа:

```bash
PYTHONPATH=. python -m src.org.generate_synthetic
PYTHONPATH=. python -m src.org.build_org_graph

# дефициты роль vs рынок
PYTHONPATH=. python -m src.org.skill_gaps --role data_science
PYTHONPATH=. python -m src.org.skill_gaps --role llm_agents

# рекомендации (обучение / курсы / мобильность)
PYTHONPATH=. python -m src.org.recommendations --role data_science

# HR Dashboard
PYTHONPATH=. streamlit run app_streamlit.py

# LLM-ассистент (8 сценариев; без GROQ_API_KEY — ответ по фактам)
PYTHONPATH=. python -m src.llm.ask --list
PYTHONPATH=. python -m src.llm.ask --scenario gaps_ds --no-llm
PYTHONPATH=. python -m src.llm.ask -q "Кого обучить по MLOps?"
```

Парсер hh.ru (официальный API через OAuth приложения + HTML fallback):

```bash
# в .env: HH_CLIENT_ID, HH_CLIENT_SECRET, HH_USER_AGENT=AppName/1.0 (email@…)
# тест
PYTHONPATH=. python -m src.market.parse_hh --limit-per-query 5 --max-pages 2

# полный прогон (Россия, DS/ML/AI) — при наличии credentials идёт через api.hh.ru
PYTHONPATH=. python -m src.market.parse_hh

# только роли
PYTHONPATH=. python -m src.market.parse_hh --role data_science --role mlops

# принудительно HTML (если API недоступен)
PYTHONPATH=. python -m src.market.parse_hh --html --limit-per-query 5

docker compose --profile parse run --rm parser --limit-per-query 5
```

Работа с Python на хосте против Docker-БД:

```bash
cp .env.example .env   # DATABASE_URL -> localhost:5433
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m src.market.parse_hh --limit-per-query 3
```

Остановка:

```bash
docker compose down
# с удалением данных БД:
# docker compose down -v
```

## Структура

- `docker-compose.yml` — Postgres + app (init/seed) + parser
- `Dockerfile` — образ приложения
- `data/` — seed рынка (DS/ML/AI)
- `docs/` — архитектура (мультиагентный пайплайн)
- `src/db` — модели Postgres
- `src/market` — парсер hh.ru, seed, LLM-извлечение навыков (`extract_skills_llm`)
- `src/graph` — нормализация навыков и market-граф
- `src/org` — синтетика сотрудников, org-граф, дефициты, рекомендации
- `src/llm` — сценарии и ассистент (Groq / fallback)
- `app_streamlit.py` — HR Dashboard / Мультивселенная / полный граф с фильтрами

Пайплайн навыков (РФ): description → LLM JSON (`llm_extract`, Ollama/Groq) ∪ `hh_key_skills` →
нормализация → canonical → рёбра `ROLE_REQUIRES_SKILL` и `SKILL_CO_OCCURS` (связки вроде Python–pandas–numpy).
Подробнее: [docs/architecture_multiagent.md](docs/architecture_multiagent.md).

## Примечание про hh.ru

С зарегистрированным приложением на [dev.hh.ru](https://dev.hh.ru) парсер
получает **токен приложения** (`client_credentials`) и ходит в `api.hh.ru`
с `Authorization: Bearer …` и корректным `HH_USER_AGENT`.

Без `HH_CLIENT_ID` / `HH_CLIENT_SECRET` остаётся HTML-fallback
(`hh.ru/search/vacancy` + карточка вакансии).

Seed CSV и Kaggle AI Jobs — дополнительные корпуса.
Тренды в графе считаются по годам `published_at`.
