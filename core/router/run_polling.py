import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, types
from config.settings import settings
from core.queue.redis_client import init_redis
from core.database.db import init_db

# Настройка простого логирования в консоль
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("🚀 Запуск Nexus System в режиме Long Polling...")
    
    # 1. Инициализируем БД и Редис
    await init_db()
    await init_redis()
    
    # 2. Парсим токены из твоего .env
    try:
        tokens_dict = json.loads(settings.telegram_tokens)
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга TELEGRAM_TOKENS: {e}")
        return

    # 3. Создаем диспетчер (обработчик сообщений)
    dp = Dispatcher()

    # Тестовый ответ, чтобы понять, что бот живой
    @dp.message()
    async def handle_any_message(message: types.Message):
        logger.info(f"📩 Сообщение от {message.from_user.full_name}: {message.text}")
        await message.reply("🛰 Nexus Online. Ваше сообщение получено и обрабатывается воркерами.")

    # 4. Запускаем всех ботов параллельно
    bots = [Bot(token=t) for t in tokens_dict.values()]
    
    logger.info(f"✅ Подключено ботов: {len(bots)}. Начинаю опрос Telegram...")
    
    # Это "магия" Polling — боты сами забирают сообщения
    await dp.start_polling(*bots)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Система остановлена")