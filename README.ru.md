# Job Searching Assistant

**Русский** | [English](README.md)

Job Searching Assistant — локальная платформа для поиска вакансий и подготовки откликов с
обязательной проверкой пользователем. Она импортирует и находит вакансии, анализирует резюме,
рассчитывает объяснимое соответствие, готовит материалы и заполняет поддерживаемые браузерные
формы до контрольной точки. Реальная отправка в HeadHunter и LinkedIn по умолчанию отключена.

Текущая версия: **1.1.300**.

## Возможности

- локальный кабинет FastAPI и очередь проверки;
- PostgreSQL как основной источник бизнес-данных;
- Redis для координации задач и lease-механизма;
- перестраиваемый индекс сопоставления OpenSearch;
- изолированные Playwright-сессии для HeadHunter, LinkedIn, Greenhouse и настроенных сайтов;
- выбранные пользователем Anthropic, Gemini или OpenAI-совместимые модели;
- шифрование browser state, LLM-ключей и чувствительных autofill-значений;
- контролируемые fixtures для browser и end-to-end проверок.

Кабинет доступен по адресу `http://127.0.0.1:8000/dashboard`, подробная очередь проверки —
`http://127.0.0.1:8000/review`.

## Быстрый запуск в Windows

Нужны Windows 10/11, Docker Desktop с WSL 2, Git for Windows, не менее 8 ГБ RAM и 10 ГБ свободного
места.

```powershell
git clone https://github.com/Sa1avatus/job-searching-assistant.git
Set-Location job-searching-assistant
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Скрипт создаёт игнорируемую локальную конфигурацию, при необходимости генерирует ключи шифрования,
создаёт общую локальную Docker-сеть Worker, собирает контейнеры, применяет миграции и ждёт health
checks. Существующие Docker volumes он не удаляет.

Безопасная остановка и повторный запуск без удаления данных:

```powershell
docker compose down
docker compose --profile browser up -d --wait
```

Не запускайте `docker compose down -v`, если не хотите удалить локальные данные PostgreSQL, Redis,
OpenSearch и моделей.

## Первый запуск

1. Создайте локального пользователя в разделе **Access**.
2. При необходимости выберите LLM-провайдера и модель в **Model**.
3. Загрузите и проверьте одно или несколько резюме.
4. Сохраните пользовательские сессии сайтов в **Site sessions**, где нужна авторизация.
5. Выберите резюме и запустите поиск вакансий.
6. Проверяйте подготовленные данные и каждое внешнее действие перед подтверждением.

Поиск HeadHunter из кабинета использует сохранённую сессию. Низкоуровневый read-only adapter
может читать публичные страницы вакансий без неё, но кабинет работает иначе. Поиск LinkedIn требует
сохранённой сессии и `APP_ENABLE_LINKEDIN_APPLY=true`. Greenhouse читает публичные доски, а
браузерная подготовка всегда останавливается до отправки.

## Детальное сопоставление

Встроенный BGE-сервис требует NVIDIA-совместимого Docker, не менее 16 ГБ RAM и около 12 ГБ
дополнительного места. При первом запуске загружается несколько гигабайт:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -EnableDetailedMatching
```

Matching v2 по умолчанию работает в shadow mode. Основные данные остаются в PostgreSQL, индекс
OpenSearch можно перестроить.

## Локальная разработка

Требуется Python 3.12 или новее:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

Команды запуска и диагностики находятся в [`docs/commands.md`](docs/commands.md), выбор безопасных
проверок — в [`docs/testing.md`](docs/testing.md).

## Документация

- [`docs/product-scope.md`](docs/product-scope.md) — возможности и продуктовая политика;
- [`docs/architecture.md`](docs/architecture.md) — компоненты и поток данных;
- [`docs/database.md`](docs/database.md) — хранение данных и миграции;
- [`docs/browser-automation.md`](docs/browser-automation.md) — сессии и внешние действия;
- [`docs/security.md`](docs/security.md) — секреты, доступ и персональные данные;
- [`docs/known-limitations.md`](docs/known-limitations.md) — проверенные ограничения и риски;
- [`docs/adapter-guide.md`](docs/adapter-guide.md) — особенности интеграций;
- [`docs/matching-architecture.md`](docs/matching-architecture.md) — входная точка Matching v2.

## Безопасность

- `.env`, browser state, резюме, screenshots и реальные evidence остаются локальными и игнорируются.
- Текст от модели не управляет Playwright или хранилищем напрямую.
- CAPTCHA, 2FA, чувствительные декларации, неизвестные обязательные ответы и переход на неразрешённый
  host всегда требуют участия пользователя.
- Автоматические тесты используют контролируемые fixtures и не отправляют реальные отклики.
