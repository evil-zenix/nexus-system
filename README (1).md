# Nexus — Распределенная система Telegram-ботов

Легковесная, асинхронная, отказоустойчивая система для управления несколькими Telegram-ботами на слабом железе.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Telegram Webhooks                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
                    ┌────▼─────┐
                    │  Router   │ (FastAPI)
                    │ :8000     │
                    └────┬─────┘
                         │
                    ┌────▼─────────────┐
                    │   Redis Queue    │
                    │ (Message Broker) │
                    └────┬─────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼──────┐  ┌─────▼─────┐  ┌──────▼──────┐
   │ Moderator │  │   OSINT    │  │ More...     │
   │  Worker   │  │   Worker   │  │ (Optional)  │
   └────┬──────┘  └─────┬─────┘  └──────┬──────┘
        │                │               │
        └────────────────┼───────────────┘
                         │
                    ┌────▼─────────┐
                    │  PostgreSQL   │
                    │  (Logs, Data) │
                    └───────────────┘
```

---

## 📋 Требования

- **Docker & Docker Compose** 20.10+
- **Linux** (оптимизировано)
- **Telegram Bot Token(s)** (получить от @BotFather)

---

## 🚀 Быстрый старт

### 1. Клонирование и подготовка

```bash
git clone <repo-url>
cd nexus
```

### 2. Конфигурация окружения

```bash
cp .env.example .env
nano .env  # или vim .env
```

**Обязательные переменные:**
- `TELEGRAM_TOKENS` — JSON с токенами ботов
- `STORAGE_CHANNEL_ID` — ID приватного канала для файлов
- `DB_PASSWORD` — пароль PostgreSQL

### 3. Запуск системы

```bash
# Запуск всех сервисов
docker-compose up -d

# Проверка статуса
docker-compose ps

# Логи
docker-compose logs -f router
docker-compose logs -f worker-moderator
docker-compose logs -f worker-osint
```

### 4. Проверка здоровья

```bash
# Router health check
curl http://localhost:8000/health

# Redis
redis-cli -h 127.0.0.1 ping

# PostgreSQL
psql -h 127.0.0.1 -U nexus_user -d nexus_db -c "SELECT 1;"
```

---

## 📁 Структура проекта

```
nexus/
├── config/              # Конфигурация приложения
├── core/
│   ├── router/         # FastAPI Router (Webhook точка входа)
│   ├── workers/        # Асинхронные воркеры (Moderator, OSINT)
│   ├── database/       # ORM + запросы
│   ├── queue/          # Redis интеграция
│   ├── storage/        # Работа с Storage Channel
│   └── telegram/       # Telegram API утилиты
├── migrations/         # Alembic миграции БД
├── scripts/            # Вспомогательные скрипты
├── tests/              # Unit & integration тесты
├── docker-compose.yml  # Оркестрация контейнеров
├── Dockerfile          # Образ для Python сервисов
└── requirements.txt    # Python зависимости
```

---

## 🔧 Конфигурация

### Telegram токены (JSON)

```json
{
  "moderator_bot": "1234567890:ABCdefGHIjklMNOpqrstUVwxyz",
  "osint_bot": "0987654321:XYZabcDEFghiJKLmnoPQRstUVwxyz"
}
```

### Storage Channel для медиа

Чтобы получить `STORAGE_CHANNEL_ID`:

1. Создай приватный Telegram-канал
2. Запроси инфо о себе: https://t.me/userinfobot
3. Используй `chat_id` со знаком минус: `-1001234567890`
4. Сделай бота админом канала

---

## 📊 Database

### Инициализация

```bash
# Внутри контейнера или локально
alembic upgrade head
```

### Основные таблицы

- `users` — данные о пользователях (user_id, username, first_seen)
- `messages` — логирование сообщений (text, chat_id, timestamp)
- `media_files` — ссылки на медиа (file_id, media_type, storage_url)
- `moderation_logs` — результаты модерации (is_spam, reason, timestamp)

---

## 🔄 Рабочий процесс

### 1️⃣ Router получает Webhook от Telegram

```
POST /webhook/bot1 → JSON сообщение
```

### 2️⃣ Router кидает в Redis очередь

```
RPUSH tasks:moderator '{"message": {...}}'
RPUSH tasks:osint '{"message": {...}}'
```

### 3️⃣ Workers тянут из Redis и обрабатывают

**Moderator Worker:**
- Проверяет спам-паттерны
- Логирует результат в `moderation_logs`

**OSINT Worker:**
- Сохраняет текст сообщения в `messages`
- Медиа отправляет в Storage Channel через `copyMessage`
- Записывает `file_id` в `media_files`

### 4️⃣ Данные в PostgreSQL

```sql
SELECT * FROM messages 
WHERE chat_id = -1001234567890 
ORDER BY timestamp DESC 
LIMIT 10;
```

---

## 📈 Масштабирование

### Добавить новый воркер

1. Создать класс в `core/workers/`
2. Добавить сервис в `docker-compose.yml`
3. Настроить Redis очередь в конфиге
4. Перезагрузить: `docker-compose up -d`

### Увеличить concurrency воркеров

```yaml
environment:
  - WORKER_CONCURRENCY=10  # по умолчанию 5
```

---

## 🐛 Отладка

### Смотреть логи Router

```bash
docker-compose logs -f router --tail 100
```

### Смотреть логи Worker

```bash
docker-compose logs -f worker-moderator
docker-compose logs -f worker-osint
```

### Залезть в Redis

```bash
docker-compose exec redis redis-cli
> KEYS tasks:*
> LLEN tasks:moderator
> LPOP tasks:moderator
```

### Залезть в PostgreSQL

```bash
docker-compose exec postgres psql -U nexus_user -d nexus_db
> \dt  # список таблиц
> SELECT COUNT(*) FROM messages;
```

---

## ⚡ Оптимизация для слабого железа

✅ **Уже встроено:**
- Alpine Linux образы (Redis, PostgreSQL)
- Асинхронная обработка (asyncio, aiogram, asyncpg)
- Пулинг соединений к БД
- Redis память ограничена (256MB LRU)
- PostgreSQL `max_connections=50`
- Нет локального хранилища файлов (только Storage Channel)

💡 **Если нужно еще уменьшить:**

```bash
# Уменьшить реплики воркеров
docker-compose up -d --scale worker-moderator=1 --scale worker-osint=1

# Уменьшить лимиты контейнеров
# Добавить в docker-compose.yml:
# deploy:
#   resources:
#     limits:
#       memory: 256M
#       cpus: '0.5'
```

---

## 🧪 Тестирование

```bash
# Unit тесты
docker-compose exec router pytest tests/

# С coverage
docker-compose exec router pytest --cov=core tests/
```

---

## 📝 Лицензия

MIT

---

## 🤝 Contributing

Требования к коду:
- Black форматирование
- Type hints
- Async-first подход
- Логирование через structlog

---

## ❓ FAQ

**Q: Почему медиа не скачиваются на диск?**  
A: Storage Channel approach экономит место на слабом железе и избегает проблем с правами доступа.

**Q: Какой максимум бот-воркеров?**  
A: ~5-10 на слабом ПК, в зависимости от нагрузки. Мониторь логи.

**Q: Как добавить новый Telegram-бот?**  
A: Добавить токен в `TELEGRAM_TOKENS` JSON и перезагрузить Router.

**Q: Падает на слабом железе?**  
A: Снизить `WORKER_CONCURRENCY` и `DB max_connections`.

---

**Автор:** DevOps + Python Squad  
**Версия:** 0.1.0-alpha
