"""
Конфигурация Nexus системы через Pydantic V2 + python-dotenv
"""
import json
from typing import Dict, Optional

from pydantic import Field, validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Основная конфигурация приложения.
    Читает из .env файла или переменных окружения.
    """

    # ========================================================================
    # TELEGRAM CONFIGURATION
    # ========================================================================
    
    # Токены ботов в формате JSON: {"bot_name": "TOKEN"}
    telegram_tokens: Dict[str, str] = Field(default_factory=dict)
    
    # ID приватного канала для хранения медиа
    storage_channel_id: int = Field(default=0)
    
    # Webhook конфигурация
    webhook_host: str = Field(default="http://localhost:8000")
    webhook_secret: str = Field(default="change_me_in_production")
    
    # ========================================================================
    # DATABASE CONFIGURATION
    # ========================================================================
    
    database_url: str = Field(
        default="postgresql+asyncpg://nexus_user:password@localhost/nexus_db"
    )
    
    # Размер пула соединений БД
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=10)
    
    # ========================================================================
    # REDIS CONFIGURATION
    # ========================================================================
    
    redis_url: str = Field(default="redis://localhost:6379/0")
    
    # TTL для задач в очереди (сек)
    task_ttl: int = Field(default=3600)
    
    # ========================================================================
    # WORKER CONFIGURATION
    # ========================================================================
    
    # Тип воркера (moderator, osint и т.д.)
    worker_type: Optional[str] = Field(default=None)
    
    # Количество одновременных задач
    worker_concurrency: int = Field(default=5)
    
    # Имя очереди для воркера
    queue_name: str = Field(default="tasks:default")
    
    # Таймаут обработки одной задачи (сек)
    task_timeout: int = Field(default=30)
    
    # ========================================================================
    # LOGGING & ENVIRONMENT
    # ========================================================================
    
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    
    # Логировать ли API запросы
    log_api_requests: bool = Field(default=False)
    
    # ========================================================================
    # FEATURE FLAGS
    # ========================================================================
    
    # Максимальный размер сообщения для логирования (байты)
    max_message_size: int = Field(default=4096)
    
    # Включить ли асинхронное сохранение в БД
    async_db_writes: bool = Field(default=True)
    
    # ========================================================================
    # CLASS CONFIGURATION
    # ========================================================================
    
    class Config:
        """Pydantic V2 конфиг"""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"  # Разрешить дополнительные переменные
    
    # ========================================================================
    # VALIDATORS
    # ========================================================================
    
    @validator("telegram_tokens", pre=True)
    def parse_telegram_tokens(cls, v):
        """Парсить JSON строку в словарь токенов"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                raise ValueError(
                    "TELEGRAM_TOKENS должен быть валидным JSON, "
                    'например: \'{"bot1": "TOKEN_1", "bot2": "TOKEN_2"}\''
                )
        elif isinstance(v, dict):
            return v
        return {}
    
    @validator("log_level")
    def validate_log_level(cls, v):
        """Проверить что log_level валидный"""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(
                f"log_level должен быть одним из {valid_levels}"
            )
        return v.upper()
    
    @validator("environment")
    def validate_environment(cls, v):
        """Проверить что environment валидный"""
        valid_envs = {"development", "staging", "production"}
        if v.lower() not in valid_envs:
            raise ValueError(
                f"environment должен быть одним из {valid_envs}"
            )
        return v.lower()
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def get_bot_token(self, bot_name: str) -> Optional[str]:
        """Получить токен бота по имени"""
        return self.telegram_tokens.get(bot_name)
    
    def get_all_bot_tokens(self) -> Dict[str, str]:
        """Получить все токены ботов"""
        return self.telegram_tokens.copy()
    
    def get_all_bot_names(self) -> list[str]:
        """Получить список имен всех ботов"""
        return list(self.telegram_tokens.keys())
    
    @property
    def is_development(self) -> bool:
        """Проверить что мы в режиме разработки"""
        return self.environment == "development"
    
    @property
    def is_production(self) -> bool:
        """Проверить что мы в production"""
        return self.environment == "production"


# Глобальная переменная конфигурации (синглтон)
settings = Settings()

if __name__ == "__main__":
    # Вывести текущую конфигурацию при запуске
    print("📋 Текущая конфигурация Nexus:")
    print(f"  Environment: {settings.environment}")
    print(f"  Log Level: {settings.log_level}")
    print(f"  Redis: {settings.redis_url}")
    print(f"  Database: {settings.database_url}")
    print(f"  Telegram Bots: {list(settings.telegram_tokens.keys())}")
    print(f"  Storage Channel: {settings.storage_channel_id}")
    print(f"  Worker Type: {settings.worker_type}")