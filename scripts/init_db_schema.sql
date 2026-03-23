-- ============================================================================
-- Глобальный профиль пользователя (общая экономика для всей сети ботов)
-- Один профиль = один telegram_user_id = один кошелёк во всех botах
-- ============================================================================
CREATE TABLE IF NOT EXISTS global_users (
    id              SERIAL PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE NOT NULL,
    username        VARCHAR(255),
    full_name       VARCHAR(511),
    
    -- Экономика
    diamonds        INTEGER     NOT NULL DEFAULT 0,
    xp              INTEGER     NOT NULL DEFAULT 0,
    balance         FLOAT       NOT NULL DEFAULT 0.0,
    
    -- Скрытие из OSINT-выдачи
    is_hidden       BOOLEAN     NOT NULL DEFAULT FALSE,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_global_users_telegram_user_id ON global_users (telegram_user_id);
CREATE INDEX IF NOT EXISTS ix_global_users_username         ON global_users (username);

-- ============================================================================
-- Логи поиска паролей
-- ============================================================================
CREATE TABLE IF NOT EXISTS password_searches (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL REFERENCES global_users(telegram_user_id) ON DELETE CASCADE,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_password_searches_user_id ON password_searches (telegram_user_id);

-- ============================================================================
-- Таблица ботов

CREATE TABLE IF NOT EXISTS system_bots (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT UNIQUE NOT NULL,
    bot_name VARCHAR(255),
    bot_username VARCHAR(255),
    bot_token VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    last_seen TIMESTAMP WITH TIME ZONE,
    metadata_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица групп/чатов
CREATE TABLE IF NOT EXISTS groups (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    bot_id INTEGER REFERENCES system_bots(id),
    chat_type VARCHAR(50),
    title VARCHAR(255),
    username VARCHAR(255),
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    members_count INTEGER DEFAULT 0,
    is_subscribed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE NOT NULL,
    group_id INTEGER REFERENCES groups(id),
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    full_name VARCHAR(255),
    status VARCHAR(50) DEFAULT 'user',
    message_count INTEGER DEFAULT 0,
    is_banned BOOLEAN DEFAULT FALSE,
    is_spam BOOLEAN DEFAULT FALSE,
    warnings_count INTEGER DEFAULT 0,
    first_message_date TIMESTAMP WITH TIME ZONE,
    last_message_date TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Лог сообщений
CREATE TABLE IF NOT EXISTS messages_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    group_id INTEGER REFERENCES groups(id),
    telegram_message_id BIGINT,
    message_text TEXT,
    has_media BOOLEAN DEFAULT FALSE,
    nexus_file_id VARCHAR(255),
    media_type VARCHAR(50),
    moderation_status VARCHAR(50) DEFAULT 'pending',
    moderation_reason TEXT,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP WITH TIME ZONE,
    is_edited BOOLEAN DEFAULT FALSE,
    edited_at TIMESTAMP WITH TIME ZONE,
    reaction_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
