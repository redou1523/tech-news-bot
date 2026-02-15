import feedparser
import random
import time
import logging
import re
from datetime import datetime, time as dt_time
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError
import asyncio

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = "8246323888:AAEH8nmAUE2VxBH6bVsn5aRQTHwPJ38tzPg"
CHANNEL_ID = "@Tech_Time_IT"  # Например @tech_news_channel
ADMIN_ID = 8524385564  # Твой Telegram ID (узнать у @userinfobot)

# Время авто-постинга (с 00:00 до 10:00)
AUTO_POST_START = 0  # 0 часов (полночь)
AUTO_POST_END = 10  # 10 часов утра

# Источники контента
SOURCES = {
    "habr": "https://habr.com/ru/rss/all/",
    "techcrunch": "https://techcrunch.com/feed/",
    "ixbt_news": "https://www.ixbt.com/export/news.xml",
    "overclockers": "https://overclockers.ru/news/feed",
    "3dnews": "http://3dnews.ru/news/rss/",
}

# Эмодзи
EMOJI = {
    "новость": "📰",
    "обзор": "🔍",
}

# Интервал авто-постинга (в минутах) ночью
AUTO_POST_INTERVAL = 30  # каждые 30 минут ночью

# ================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class TechNewsBot:
    def __init__(self, token, channel_id, admin_id):
        self.bot = Bot(token=token)
        self.channel_id = channel_id
        self.admin_id = admin_id
        self.posted_links = set()
        self.pending_news = []  # Очередь новостей на рассмотрение
        self.stats = {
            'total_posts': 0,
            'posts_today': 0,
            'auto_posts': 0,
            'manual_posts': 0,
            'rejected': 0
        }

    def parse_rss(self, feed_url, source_name):
        """Парсит RSS-ленту"""
        try:
            feed = feedparser.parse(feed_url)
            entries = []
            for entry in feed.entries[:5]:  # Берём по 5 записей с источника
                if entry.link in self.posted_links:
                    continue

                entries.append({
                    'title': entry.title,
                    'link': entry.link,
                    'summary': entry.get('summary', ''),
                    'source': source_name,
                })
                self.posted_links.add(entry.link)
            return entries
        except Exception as e:
            logger.error(f"Ошибка парсинга {source_name}: {e}")
            return []

    def clean_html(self, html_text):
        """Удаляет HTML-теги из текста"""
        if not html_text:
            return ""
        clean = re.sub('<.*?>', '', html_text)
        clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&')
        clean = clean.replace('&quot;', '"').replace('&laquo;', '«').replace('&raquo;', '»')
        return clean[:200] + '...' if len(clean) > 200 else clean

    def collect_news(self):
        """Собирает новости со всех источников"""
        all_entries = []

        source_names = {
            "habr": "Хабр",
            "techcrunch": "TechCrunch",
            "ixbt_news": "iXBT",
            "overclockers": "Overclockers",
            "3dnews": "3DNews",
        }

        for key, url in SOURCES.items():
            source_name = source_names.get(key, key)
            entries = self.parse_rss(url, source_name)
            all_entries.extend(entries)

        return all_entries

    def format_post(self, entry, with_preview=False):
        """Форматирует запись для Telegram"""
        summary = self.clean_html(entry['summary'])

        # Без предпросмотра ссылки (добавляем пробел перед точкой)
        if not with_preview:
            link = entry['link'].replace('.', ' .')
        else:
            link = entry['link']

        post_text = (
            f"📰 **{entry['title']}**\n\n"
            f"{summary}\n\n"
            f"🔗 [Читать на {entry['source']}]({link})\n\n"
            f"#технологии #новости"
        )

        return post_text

    async def send_to_admin(self, entry):
        """Отправляет новость админу на модерацию"""
        text = self.format_post(entry, with_preview=False)

        keyboard = [
            [
                InlineKeyboardButton("✅ ПОСТИТЬ", callback_data=f"post_{len(self.pending_news)}"),
                InlineKeyboardButton("❌ УДАЛИТЬ", callback_data=f"delete_{len(self.pending_news)}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Сохраняем новость в очередь
        self.pending_news.append(entry)

        await self.bot.send_message(
            chat_id=self.admin_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup,
            disable_web_page_preview=True  # Отключаем предпросмотр
        )
        logger.info(f"📨 Отправлено на модерацию: {entry['title'][:30]}...")

    async def auto_post_to_channel(self):
        """Автоматический постинг в канал (ночью)"""
        entries = self.collect_news()
        if not entries:
            logger.warning("⚠️ Нет новых записей для авто-постинга")
            return

        entry = random.choice(entries)
        text = self.format_post(entry, with_preview=False)

        try:
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='Markdown',
                disable_web_page_preview=True  # Отключаем предпросмотр
            )

            self.stats['total_posts'] += 1
            self.stats['posts_today'] += 1
            self.stats['auto_posts'] += 1

            logger.info(f"✅ [AUTO] Пост в канал: {entry['title'][:30]}...")

            # Уведомляем админа
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=f"🌙 **Авто-пост ночью:**\n{entry['title']}",
                parse_mode='Markdown'
            )

        except TelegramError as e:
            logger.error(f"❌ Ошибка авто-постинга: {e}")

    async def post_to_channel(self, entry):
        """Ручной постинг в канал"""
        text = self.format_post(entry, with_preview=False)

        try:
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='Markdown',
                disable_web_page_preview=True  # Отключаем предпросмотр
            )

            self.stats['total_posts'] += 1
            self.stats['posts_today'] += 1
            self.stats['manual_posts'] += 1

            logger.info(f"✅ [MANUAL] Пост в канал: {entry['title'][:30]}...")
            return True

        except TelegramError as e:
            logger.error(f"❌ Ошибка постинга: {e}")
            return False

    async def collect_and_send_to_admin(self):
        """Собирает новости и отправляет админу"""
        logger.info("📡 Собираю новости для модерации...")

        entries = self.collect_news()

        if not entries:
            logger.warning("⚠️ Нет новых записей")
            await self.bot.send_message(
                chat_id=self.admin_id,
                text="⚠️ Новых новостей пока нет"
            )
            return

        # Перемешиваем и берём одну
        entry = random.choice(entries)
        await self.send_to_admin(entry)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает нажатия на кнопки модерации"""
        query = update.callback_query
        await query.answer()

        if update.effective_user.id != self.admin_id:
            await query.edit_message_text("⛔ Это не для тебя")
            return

        data = query.data
        parts = data.split('_')
        action = parts[0]
        index = int(parts[1]) if len(parts) > 1 else 0

        if index >= len(self.pending_news):
            await query.edit_message_text("❌ Новость уже обработана")
            return

        entry = self.pending_news[index]

        if action == "post":
            # Постим в канал
            success = await self.post_to_channel(entry)
            if success:
                await query.edit_message_text(
                    f"✅ **Новость опубликована!**\n\n{entry['title']}",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    f"❌ **Ошибка публикации**\n\n{entry['title']}",
                    parse_mode='Markdown'
                )

        elif action == "delete":
            # Просто удаляем из очереди
            self.stats['rejected'] += 1
            await query.edit_message_text(
                f"🗑️ **Новость удалена**\n\n{entry['title']}",
                parse_mode='Markdown'
            )

        # Удаляем из очереди
        self.pending_news.pop(index)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stats для админа"""
        if update.effective_user.id != self.admin_id:
            await update.message.reply_text("⛔ У тебя нет доступа")
            return

        now = datetime.now()
        is_auto = self.is_auto_post_time(now)

        stats_text = (
            f"📊 **СТАТИСТИКА**\n\n"
            f"📝 Всего постов: {self.stats['total_posts']}\n"
            f"📅 За сегодня: {self.stats['posts_today']}\n"
            f"🤖 Авто-постов: {self.stats['auto_posts']}\n"
            f"👆 Ручных: {self.stats['manual_posts']}\n"
            f"🗑️ Отклонено: {self.stats['rejected']}\n"
            f"⏰ В очереди: {len(self.pending_news)}\n\n"
            f"🌙 Режим: {'АВТО (ночь)' if is_auto else 'РУЧНОЙ'}"
        )

        await update.message.reply_text(stats_text, parse_mode='Markdown')

    def is_auto_post_time(self, current_time=None):
        """Проверяет, нужно ли сейчас автоматически постить"""
        if current_time is None:
            current_time = datetime.now()

        hour = current_time.hour

        if AUTO_POST_START <= AUTO_POST_END:
            return AUTO_POST_START <= hour < AUTO_POST_END
        else:
            return hour >= AUTO_POST_START or hour < AUTO_POST_END


# ========== ОСНОВНАЯ ФУНКЦИЯ ==========
async def main():
    if TELEGRAM_TOKEN == "ТВОЙ_ТОКЕН_СЮДА":
        print("❌ ОШИБКА: Вставь свой токен!")
        return

    if ADMIN_ID == 123456789:
        print("⚠️ ВНИМАНИЕ: Вставь свой Telegram ID в ADMIN_ID!")
        print("Узнать ID можно у @userinfobot")

    # Создаём бота
    bot = TechNewsBot(TELEGRAM_TOKEN, CHANNEL_ID, ADMIN_ID)

    # Создаём приложение для обработки кнопок
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("stats", bot.stats_command))
    app.add_handler(CommandHandler("news", lambda u, c: bot.collect_and_send_to_admin()))
    app.add_handler(CallbackQueryHandler(bot.button_handler))

    # Запускаем приложение в фоне
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("=" * 50)
    print("✅ БОТ-МОДЕРАТОР ЗАПУЩЕН!")
    print(f"📢 Канал: {CHANNEL_ID}")
    print(f"🌙 Авто-режим: {AUTO_POST_START}:00 - {AUTO_POST_END}:00")
    print("=" * 50)
    print("📨 Новости будут приходить сюда на модерацию")
    print("📊 /stats - статистика")
    print("📰 /news - получить новость сейчас")
    print("=" * 50)

    try:
        last_hour = -1

        while True:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute

            # Проверяем, нужно ли включать авто-режим
            if bot.is_auto_post_time(now):
                # Авто-режим (ночь)
                if current_minute % AUTO_POST_INTERVAL == 0 and current_hour != last_hour:
                    await bot.auto_post_to_channel()
                    last_hour = current_hour
                    await asyncio.sleep(60)  # Ждём минуту, чтобы не задвоить
            else:
                # Ручной режим (день) - ничего не делаем, ждём команд
                pass

            await asyncio.sleep(30)  # Проверяем каждые 30 секунд

    except KeyboardInterrupt:
        print("❌ Бот остановлен")
    finally:
        await app.stop()


# ========== ЗАПУСК ==========
if __name__ == "__main__":
    asyncio.run(main())
