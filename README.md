# AI-платформа управления компетенциями предприятия

Интеллектуальная система анализа компетенций и поддержки кадровых решений
на основе графа знаний и LLM.

## Запуск через Docker (рекомендуется)

Используется **Colima** + Docker CLI (без Docker Desktop).

```bash
# если Colima ещё не запущена
colima start

cd "AI платформа управления компетенциями предприятия"

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
PYTHONPATH=. python -m src.graph.build_market_graph

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

Парсер hh.ru (HTML-поиск; `api.hh.ru/vacancies` сейчас отдаёт 403):

```bash
# тест
PYTHONPATH=. python -m src.market.parse_hh --limit-per-query 5 --max-pages 2

# полный прогон (Россия, DS/ML/AI)
PYTHONPATH=. python -m src.market.parse_hh

# только роли
PYTHONPATH=. python -m src.market.parse_hh --role data_science --role mlops

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
- `src/db` — модели Postgres
- `src/market` — парсер hh.ru и seed
- `src/graph` — нормализация навыков и market-граф
- `src/org` — синтетика сотрудников, org-граф, дефициты, рекомендации
- `src/llm` — сценарии и ассистент (Groq / fallback)
- `app_streamlit.py` — HR Dashboard

## Примечание про hh.ru

`api.hh.ru/vacancies` отвечает `403 forbidden` для программных клиентов
(антибот на edge, не лечится User-Agent/прокси).

Парсер ходит на публичный сайт `hh.ru/search/vacancy` + `hh.ru/vacancy/{id}`
(HTML + JSON-LD), собирает навыки и описания. Seed CSV остаётся запасным путём.
