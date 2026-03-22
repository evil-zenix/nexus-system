#!/usr/bin/env markdown
# 🚀 NEXUS SYSTEM — ПОЛНОЕ РУКОВОДСТВО

## 📑 Оглавление

1. [Структура Проекта](#структура-проекта)
2. [Установка](#установка)
3. [Конфигурация](#конфигурация)
4. [Запуск Системы](#запуск-системы)
5. [Database Слой](#database-слой)
6. [Core Router](#core-router)
7. [Workers](#workers)
8. [Storage Channel Паттерн](#storage-channel-паттерн)
9. [Мониторинг и Отладка](#мониторинг-и-отладка)
10. [FAQ](#faq)

---

## Структура Проекта

```
nexus/
├── config/
│   ├── __init__.py
│   ├── settings.py          # Pydantic конфигурация
│   └── logging.py           # Структурированное логирование
│
├── core/
│   ├── __init__.py
│   │
│   ├── database/            # ORM слой (SQLAlchemy)
│   │   ├── __init__.py
│   │   ├── models.py        # 4 асинхронные модели (SystemBot, Group, User, MessageLog)
│   │   ├── db.py            # Async connection pooling
│   │   └── queries.py       # CRUD операции
│   │
│   ├── queue/               # Redis брокер сообщений
│   │   ├── __init__.py
│   │   ├── redis_client.py  # Async Redis клиент
│   │   └── message_schema.py # Pydantic DTO схемы
│   │
│   ├── router/              # FastAPI точка входа
│   │   ├── __init__.py
│   │   ├── app.py           # FastAPI приложение + lifespan
│   │   ├── handlers.py      # Webhook обработчики
│   │   └── main.py          # Entry point (Uvicorn)
│   │
│   ├── workers/             # Асинхронные воркеры
│   │   ├── __init__.py
│   │   ├── base_worker.py   # Абстрактный базовый класс
│   │   ├── moderator.py     # Спам-проверка и модерация
│   │   ├── osint_scraper.py # Логирование и Storage Channel
│   │   └── main.py          # Entry point для воркеров
│   │
│   ├── telegram/            # Telegram API утилиты
│   │   ├── __init__.py
│   │   └── client.py        # TelegramClient обертка
│   │
│   └── storage/             # Storage Channel управление
│       ├── __init__.py
│       └── media_handler.py # Media хранение утилиты
│
├── docker-compose.yml       # Оркестрация контейнеров
├── Dockerfile              # Multi-stage сборка
├── requirements.txt        # Python зависимости
├── .env.example           # Пример конфигурации
└── README.md              # Документация
```

---

## Установка

### Шаг 1: Клонирование

```bash
git clone <repo-url>
cd nexus
```

### Шаг 2: Копирование конфигурации

```bash
cp .env.example .env
nano .env  # или vim .env
```

### Шаг 3: Заполнение конфигурации

```env
# Telegram бот токены (JSON)
TELEGRAM_TOKENS='{"moderator_bot": "YOUR_TOKEN_1", "osint_bot": "YOUR_TOKEN_2"}'

# ID приватного канала для хранения медиа
STORAGE_CHANNEL_ID=-1001234567890

# Database
DB_PASSWORD=super_secure_password

# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO
```

### Шаг 4: Запуск Docker Compose

```bash
# Запустить все контейнеры
docker-compose up -d

# Проверить статус
docker-compose ps

# Просмотр логов
docker-compose logs -f router
```

---

## Конфигурация

### Telegram Токены

Получить токены от @BotFather и добавить в .env:

```env
TELEGRAM_TOKENS='{"bot1": "1234567890:ABCdefGHIjklMNOpqrstUVwxyz", "bot2": "0987654321:XYZabcDEFghiJKLmnoPQRstUVwxyz"}'
```

### Storage Channel ID

Как получить ID приватного канала:

1. Создать приватный Telegram-канал
2. Добавить бота в админы канала
3. Запросить свой User ID: https://t.me/userinfobot
4. Скопировать ID из ответа и добавить минус спереди
5. Результат в .env: `STORAGE_CHANNEL_ID=-1001234567890`

### Webhook Secret (опционально)

Для дополнительной безопасности webhook'ов:

```env
WEBHOOK_SECRET=your_secret_token
```

Telegram отправит этот токен в header: `X-Telegram-Bot-Api-Secret-Token`

---

## Запуск Системы

### Быстрый старт (All-in-one)

```bash
docker-compose up -d
```

Это запустит:
- ✅ Router (FastAPI :8000)
- ✅ Moderator Worker
- ✅ OSINT Worker
- ✅ Redis (очередь)
- ✅ PostgreSQL (БД)

### Запуск отдельных компонентов

```bash
# Только Router
docker-compose up -d redis postgres router

# Router + один воркер
docker-compose up -d redis postgres router worker-moderator

# Локально (без Docker)
python -m core.router.main &
python -m core.workers.main --worker moderator &
python -m core.workers.main --worker osint &
```

### Проверка здоровья

```bash
# Health check Router
curl http://localhost:8000/health

# Info о системе
curl http://localhost:8000/info

# Метрики очередей
curl http://localhost:8000/metrics/queue

# PING
curl http://localhost:8000/ping
```

---

## Database Слой

### Модели

#### 1. SystemBot
```python
class SystemBot(Base):
    """Информация о боте в системе"""
    bot_id: int                 # Telegram Bot ID
    bot_name: str              # Имя в системе (moderator_bot)
    bot_token: str             # Telegram токен
    is_active: bool            # Активен ли
    last_seen: datetime        # Последний онлайн
```

#### 2. Group
```python
class Group(Base):
    """Целевой чат/группа"""
    telegram_chat_id: int      # Telegram Chat ID
    bot_id: int                # Foreign Key → SystemBot
    chat_type: str             # private/group/supergroup/channel
    title: str                 # Название
    members_count: int         # Количество членов
    is_subscribed: bool        # Активен ли мониторинг
```

#### 3. User
```python
class User(Base):
    """Пользователь в группе"""
    telegram_user_id: int      # Telegram User ID
    group_id: int              # Foreign Key → Group
    username: str              # @username
    first_name: str            # Имя
    message_count: int         # Количество сообщений
    is_banned: bool            # Забанен ли
    is_spam: bool              # Помечен ли как спамер
    warnings_count: int        # Количество предупреждений
```

#### 4. MessageLog ⭐ (КЛЮЧЕВАЯ!)
```python
class MessageLog(Base):
    """Логирование сообщений с Storage Channel поддержкой"""
    user_id: int               # Foreign Key → User
    group_id: int              # Foreign Key → Group
    telegram_message_id: int   # Telegram Message ID
    message_text: str          # Текст сообщения
    has_media: bool            # Есть ли медиа
    nexus_file_id: str         # ⭐ JSON с file_id'ами из Storage Channel
    moderation_status: str     # clean/spam/flagged
    is_deleted: bool           # Удалено ли
    is_edited: bool            # Отредактировано ли
```

### CRUD Операции

```python
# Создать/получить группу
group = await queries.get_or_create_group(
    session,
    telegram_chat_id=-1001234567890,
    bot_id=1,
    chat_type="supergroup",
    title="My Group"
)

# Создать/получить пользователя
user = await queries.get_or_create_user(
    session,
    telegram_user_id=123456789,
    group_id=group.id,
    username="john_doe"
)

# Залогировать сообщение
message_log = await queries.create_message_log(
    session,
    user_id=user.id,
    group_id=group.id,
    telegram_message_id=999,
    message_text="Hello world!",
    has_media=False,
    nexus_file_id="{}"
)

# Получить последние сообщения от пользователя
messages = await queries.get_recent_messages_by_user(
    session,
    user_id=user.id,
    limit=10
)
```

---

## Core Router

### Архитектура

```
Telegram Webhook
        ↓
/webhook/{bot_name}
        ↓
1. Валидировать бота
2. Валидировать secret (если нужно)
3. Парсить update
4. Синхронизировать БД
5. Построить NexusMessage
6. Кинуть в Redis очередь
        ↓
200 OK ← Telegram
        ↓
   Worker обработает
```

### Webhook API

```bash
# Обработка webhook от Telegram
POST /webhook/moderator_bot
Content-Type: application/json
X-Telegram-Bot-Api-Secret-Token: your_secret

{
  "update_id": 123456789,
  "message": {
    "message_id": 999,
    "date": 1234567890,
    "chat": {
      "id": -1001234567890,
      "type": "supergroup",
      "title": "My Group"
    },
    "from": {
      "id": 123456789,
      "is_bot": false,
      "first_name": "John"
    },
    "text": "Hello world!"
  }
}
```

### Health Check Endpoints

```bash
# Проверить здоровье всех сервисов
GET /health
→ {
    "status": "ok",
    "timestamp": "2024-01-01T12:00:00.000000",
    "services": {
      "redis": true,
      "database": true,
      "telegram": true
    },
    "version": "0.1.0"
  }

# Информация о системе
GET /info
→ {
    "ok": true,
    "system": "Nexus Core Router",
    "version": "0.1.0",
    "bots_configured": 2,
    "bot_names": ["moderator_bot", "osint_bot"],
    "storage_channel_id": -1001234567890
  }

# Метрики очередей
GET /metrics/queue
→ {
    "ok": true,
    "timestamp": "2024-01-01T12:00:00.000000",
    "queues": {
      "tasks:moderator": 5,
      "tasks:osint": 12
    },
    "total_tasks": 17
  }
```

### Конфигурация Webhook в Telegram

```bash
# Установить webhook
curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://yourdomain.com/webhook/moderator_bot",
    "secret_token": "your_secret_token"
  }'

# Проверить статус webhook
curl "https://api.telegram.org/bot{TOKEN}/getWebhookInfo"

# Удалить webhook
curl -X POST "https://api.telegram.org/bot{TOKEN}/deleteWebhook"
```

---

## Workers

### Moderator Worker

**Функциональность:**
- Проверка на спам-паттерны
- Проверка на запрещенные слова
- Flood detection (много капса, много символов)
- Удаление спам-сообщений
- Выдача предупреждений
- Блокировка пользователей при достижении порога

**Запуск:**
```bash
python -m core.workers.main --worker moderator --concurrency 5
```

**Результаты в БД:**
```sql
SELECT * FROM messages_log 
WHERE moderation_status = 'spam' 
ORDER BY created_at DESC;
```

### OSINT/Scraper Worker

**Функциональность:**
- Логирование ВСЕ сообщений
- Сохранение текста в БД
- **Копирование медиа в Storage Channel** (copyMessage API)
- Получение постоянного file_id
- Хранение file_id в БД (БЕЗ скачивания на диск!)

**Запуск:**
```bash
python -m core.workers.main --worker osint --concurrency 3
```

**Медиа обработка:**
```python
# OSINT воркер делает:
1. Получает сообщение с медиа из Redis
2. Вызывает bot.copy_message() → Storage Channel
3. Получает file_id из скопированного сообщения
4. Сохраняет JSON в nexus_file_id:
   {"photo": ["file_id_1"], "video": [], "document": []}
5. Сохраняет всё в message_log.nexus_file_id
```

**Результаты в БД:**
```sql
-- Все сообщения с медиа
SELECT message_id, user_id, has_media, nexus_file_id 
FROM messages_log 
WHERE has_media = true;

-- Извлечь file_id из JSON
SELECT message_id, 
       json_array_elements(
         json_extract_path(nexus_file_id::jsonb, 'photo')
       ) as photo_file_id
FROM messages_log 
WHERE nexus_file_id::jsonb -> 'photo' IS NOT NULL;
```

### Запуск нескольких воркеров параллельно

```bash
# Терминал 1: Moderator
python -m core.workers.main --worker moderator

# Терминал 2: OSINT
python -m core.workers.main --worker osint

# Или в Docker
docker-compose up -d worker-moderator worker-osint
```

---

## Storage Channel Паттерн

### Что это?

Вместо скачивания файлов на диск, используется Telegram как хранилище:

```
Сообщение в чате с фото
        ↓
OSINT Worker получает задачу
        ↓
bot.copy_message() → Privaсе Storage Channel
        ↓
Получить file_id из скопированного сообщения
        ↓
Сохранить file_id в БД (message_log.nexus_file_id)
        ↓
✓ Файл хранится на Telegram серверах
✓ Не занимает место на диске ПК
✓ file_id действует вечно
```

### Как настроить Storage Channel?

1. Создать приватный Telegram канал
2. Добавить бота в админы
3. Получить Chat ID: https://t.me/userinfobot → скопировать ID
4. В .env: `STORAGE_CHANNEL_ID=-1001234567890`

### Доступ к медиа

```python
# В любой момент можно получить файл
file_id_json = message_log.nexus_file_id
# {"photo": ["file_id_1", "file_id_2"], "video": [], ...}

photo_file_id = json.loads(file_id_json)["photo"][0]

# Отправить пользователю через бота
await bot.send_photo(
    chat_id=user_id,
    photo=photo_file_id,
    caption="Сохраненная фотография"
)
```

### Преимущества

✅ **Экономия места** — файлы на Telegram серверах  
✅ **Надежность** — Telegram резервные копии  
✅ **Скорость** — не нужно скачивать на диск  
✅ **Простота** — одна команда copyMessage  
✅ **Вечность** — file_id не истекает  

---

## Мониторинг и Отладка

### Логи

```bash
# Логи Router
docker-compose logs -f router --tail 100

# Логи воркера Moderator
docker-compose logs -f worker-moderator --tail 100

# Логи воркера OSINT
docker-compose logs -f worker-osint --tail 100

# Все логи
docker-compose logs -f
```

### Redis мониторинг

```bash
# Подключиться к Redis
docker-compose exec redis redis-cli

# Команды внутри Redis
KEYS tasks:*                    # Все очереди
LLEN tasks:moderator          # Длина очереди moderator
LLEN tasks:osint              # Длина очереди osint
LPOP tasks:moderator          # Получить первую задачу (удалит!)
LRANGE tasks:moderator 0 -1   # Все задачи в очереди
INFO memory                    # Потребление памяти
FLUSHDB                        # Очистить всю БД (ОСТОРОЖНО!)
```

### PostgreSQL мониторинг

```bash
# Подключиться к БД
docker-compose exec postgres psql -U nexus_user -d nexus_db

# Команды внутри psql
\dt                           # Все таблицы
SELECT COUNT(*) FROM users;   # Количество пользователей
SELECT COUNT(*) FROM messages_log; # Количество логов
SELECT COUNT(*) FROM groups;  # Количество групп

# Найти спам-сообщения
SELECT id, user_id, message_text, moderation_status 
FROM messages_log 
WHERE moderation_status = 'spam' 
LIMIT 10;

# Найти активных пользователей
SELECT u.username, COUNT(*) as msg_count 
FROM users u 
JOIN messages_log m ON u.id = m.user_id 
GROUP BY u.id 
ORDER BY msg_count DESC 
LIMIT 10;
```

### Проверка статуса сервисов

```bash
# Docker статус
docker-compose ps

# Проверить Redis
redis-cli -h 127.0.0.1 ping

# Проверить PostgreSQL
psql -h 127.0.0.1 -U nexus_user -d nexus_db -c "SELECT 1;"

# Проверить Router
curl http://localhost:8000/health
```

---

## FAQ

### Q: Как добавить нового Telegram бота?

A: 
1. Получить токен от @BotFather
2. Обновить .env:
   ```env
   TELEGRAM_TOKENS='{"bot1": "TOKEN1", "bot2": "TOKEN2", "bot3": "TOKEN3"}'
   ```
3. Перезагрузить Router:
   ```bash
   docker-compose restart router
   ```
4. Установить webhook на новом боте через Telegram API

### Q: Как добавить новый воркер?

A:
1. Создать файл `core/workers/my_worker.py`
2. Наследовать `BaseWorker` и реализовать метод `process()`
3. Добавить в `docker-compose.yml`:
   ```yaml
   worker-mybot:
     build: .
     command: python -m core.workers.main --worker mybot
     environment:
       - QUEUE_NAME=tasks:mybot
   ```
4. Запустить: `docker-compose up -d worker-mybot`

### Q: Какой максимум нагрузки на слабом ПК?

A: Зависит от конкретной машины, но примерно:
- ~5-10 ботов
- ~100 сообщений в секунду
- ~50 одновременных пользователей в группе

Для увеличения нагрузки:
- Уменьшить `WORKER_CONCURRENCY`
- Добавить больше памяти Redis
- Оптимизировать паттерны спама

### Q: Медиа занимает место на диске?

A: **НЕТ!** Storage Channel паттерн полностью избегает скачивания на диск.
Файлы хранятся на Telegram серверах, а мы только сохраняем file_id.

### Q: Как восстановить потерянные данные?

A:
1. Backup PostgreSQL:
   ```bash
   docker-compose exec postgres pg_dump -U nexus_user nexus_db > backup.sql
   ```
2. Restore:
   ```bash
   docker-compose exec postgres psql -U nexus_user nexus_db < backup.sql
   ```

### Q: Как очистить старые логи?

A:
```sql
-- Удалить логи старше 30 дней
DELETE FROM messages_log 
WHERE created_at < NOW() - INTERVAL '30 days';

-- Оптимизировать таблицу
VACUUM ANALYZE messages_log;
```

---

## 🎯 Следующие шаги

1. ✅ Установить и запустить систему
2. ✅ Проверить health endpoints
3. ✅ Залогировать несколько сообщений
4. ✅ Проверить БД и Redis
5. ✅ Настроить Storage Channel ID
6. ✅ Добавить свои паттерны спама
7. ✅ Расширить функционал воркеров

---

**Версия:** 0.1.0-alpha  
**Дата:** 2024-01-01  
**Автор:** DevOps + Python Squad
