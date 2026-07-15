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

Парсер hh.ru (когда API доступен):

```bash
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
- `data/final_vacancies.csv` — seed рынка (DS/ML/AI)
- `src/db` — модели Postgres
- `src/market` — парсер hh.ru и seed

## Примечание про hh.ru

С некоторых IP `api.hh.ru` отвечает `403` (DDoS-Guard).
Seed в Postgres закрывает этот этап; живой парсер готов к повторному запуску.
