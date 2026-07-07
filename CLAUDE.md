# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

RumBot — Telegram-бот «в одном файле» для команды поддержки ELD (Electronic Logging Device, «Safe Lane ELD»). Приветствует группы водителей, ведёт двуязычный (RU/EN) сценарий приветствия, рассылает объявления, проставляет дату DOT-проверки в название группы и пересылает сменные «refresh»-пинги в центральную диспетчерскую группу со сбором статистики. Вся логика — в [bot.py](bot.py).

## Команды

```bash
pip install -r requirements.txt          # зависимости (python-telegram-bot 21.6, supabase 2.9.1, python-dotenv)
py bot.py                                 # запуск (Windows, разработка); цикл long-poll, остановка Ctrl-C
python bot.py                             # запуск (Linux/прод)
python -c "import ast; ast.parse(open('bot.py', encoding='utf-8').read()); print('syntax OK')"   # проверка синтаксиса
```

Тестов, линтера и шага сборки в репозитории **нет**. Однострочник с `ast.parse` выше — единственная проверка перед запуском, которая здесь используется; не выдумывай команды pytest/flake8.

Схема БД — в [supabase_schema.sql](supabase_schema.sql); применяется вручную один раз через SQL-редактор в дашборде Supabase (или `mcp__supabase__apply_migration`), а не ботом во время работы.

## Конфигурация (.env)

Без этих переменных бот не стартует (`SystemExit`). Файл `.env` в gitignore.

- `BOT_TOKEN` — токен Telegram-бота (обязательно)
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — проект Supabase + ключ **service-role**, обязательно (service-ключ обходит RLS; на таблицах RLS включён без политик, поэтому anon-ключ не сможет читать/писать)
- `ADMIN_USERNAMES` — `@логины` через запятую; `ADMIN_IDS` — числовые user_id через запятую. Почти все команды доступны только этим пользователям. Задай хотя бы одну переменную.
- `REFRESH_GROUP_ID` — chat_id, куда уходят refresh-пинги (есть значение по умолчанию в коде)

## Архитектура

**Модель выполнения.** `python-telegram-bot` v21 (asyncio). `main()` собирает `Application`, регистрирует все хендлеры и вызывает `run_polling`. `post_init` выполняется один раз при старте: прогревает кэш чатов и проводит миграцию JSON→Supabase.

**Хранилище = Supabase + кэши в памяти.** Клиент `supabase-py` **синхронный**, поэтому каждый вызов БД обёрнут в локальную замыкающую функцию `_sync()` и выполняется через `asyncio.to_thread(...)`, чтобы не блокировать event loop, — придерживайся этого паттерна для любых новых запросов. Три таблицы (`chats`, `users`, `refresh_events`) плюс view `refresh_daily_stats`. Кэши уровня модуля зеркалят БД и являются источником истины во время работы:
- `_chats_cache: set[int]` / `_chat_langs: dict[int,str]` — загружаются в `_load_chats()` при старте; синхронизируются через `remember_chat`/`forget_chat`/`set_chat_lang`.
- `_users_cache: dict[int,str]` — язык по пользователю, подгружается лениво.
- `_pdf_file_ids: dict[str,str]` — кэширует `file_id` от Telegram после первой отправки приветственных PDF, чтобы дальше слать по ID, а не перезагружать файлы из [welcome_files/](welcome_files/).

Меняя состояние чата/пользователя, обновляй **и** Supabase, **и** соответствующий кэш — иначе чтения устареют.

**Контроль доступа.** Декоратор `@admin_only` (проверяет `is_admin` по `ADMIN_IDS`/`ADMIN_USERNAMES`) оборачивает каждую пользовательскую команду и колбэк выбора языка. Два хендлера намеренно **не** закрыты, потому что реагируют на события Telegram, а не на команды: `on_my_chat_member` (бота добавили/удалили/назначили админом) и `on_group_migrated` (смена ID при переходе group→supergroup). `on_refresh_trigger` делает собственную inline-проверку админа.

**Двуязычие.** Язык хранится **по чату для групп** и **по пользователю для личных сообщений** (`on_language_button` ветвится по типу чата). `DEFAULT_LANG = "ru"`. Тексты приветствия, подтверждения языка и т.п. — словари с ключами `"ru"`/`"en"`.

**Ключевые сценарии в `bot.py`:**
- *Приветствие* (`cmd_welcome` → `_send_welcome_pdfs`) — отправляет локализованный текст, затем ссылки на сторы, затем медиагруппу из 3 PDF, с паузами `WELCOME_INTER_MSG_DELAY` между сообщениями.
- *Рассылка* (`cmd_broadcast` → `_do_broadcast`) — запускается отдельной `asyncio.create_task` под защитой `_broadcast_lock` (одновременно только одна рассылка). Повтор по каждому чату с задержкой; чаты, вернувшие `Forbidden`/«chat not found»/«kicked»/«deactivated», автоматически удаляются через `forget_chat`. Ведёт живое статус-сообщение.
- *DOT-метка* (`cmd_dot`) — переписывает название группы в `🔴<день> <Mon>🔴 <базовое название>`, срезая прежний префикс через `DOT_PREFIX_RE`. Боту нужны права админа «Изменение информации о группе».
- *Refresh-пинг* (`on_refresh_trigger`) — когда админ пишет в группе (кроме самого `REFRESH_GROUP_ID`) одну из точных фраз из `REFRESH_TRIGGERS` **или** отправляет один из стикеров из `REFRESH_STICKER_IDS` (набор t.me/addstickers/eld24for7, сверка по `file_unique_id`), название группы пересылается в `REFRESH_GROUP_ID`, а в БД вставляется событие для сменного отчёта `/stats` (таймзона Asia/Bishkek, три 8-часовые смены).

**Устойчивость.** `_safe_send` и цикл рассылки централизуют обработку ретраев Telegram: `RetryAfter` → подождать и повторить; `Forbidden`/`BadRequest` → прекратить (часто с удалением чата). `error_handler` логирует любое необработанное исключение и шлёт трейс в личку всем `ADMIN_IDS`. Логи идут в stdout + ротируемый `bot.log` (в gitignore).

**Разовая миграция.** `migrate_json_to_supabase()` при первом старте импортирует устаревшие `chats.json`/`users.json`, затем переименовывает их в `*.migrated` (см. `chats.json.migrated`). Это история; новое состояние пишется сразу в Supabase.

## Деплой

Прод работает на Linux под systemd — см. [deploy/rumbot.service](deploy/rumbot.service) (пользователь `rumbot`, venv в `.venv/`, `Restart=always`). Логи через `journalctl -u rumbot`. Учти: `WorkingDirectory` в юните (`/home/rumbot/RUMELD_BOT`) — это прод-путь; поправь, если каталог деплоя другой.
