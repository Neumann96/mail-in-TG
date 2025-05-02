import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
import aiohttp
import asyncio
from datetime import datetime
import sys
import imaplib
import email
from email.header import decode_header
import re
import locale
import dateparser

# Устанавливаем русскую локаль с правильной кодировкой
try:
    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
except:
    try:
        locale.setlocale(locale.LC_TIME, 'Russian_Russia.1251')
    except:
        logging.warning("Could not set Russian locale")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Get bot token and validate it
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN or BOT_TOKEN == 'your_telegram_bot_token':
    logging.error("❌ Ошибка: BOT_TOKEN не установлен в файле .env")
    logging.error("Пожалуйста, замените 'your_telegram_bot_token' на реальный токен от @BotFather")
    sys.exit(1)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# States
class AuthStates(StatesGroup):
    waiting_for_auth = State()


# Yandex OAuth configuration
YANDEX_AUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_SCOPES = "mail:imap_full"  # Полный доступ к почте через IMAP

# Gmail OAuth configuration
GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = "https://mail.google.com/"  # Полный доступ к Gmail

# Store user tokens and email credentials
user_tokens = {}
user_credentials = {}
last_email_ids = {}  # Хранение ID последних проверенных писем для каждого пользователя

# Словари для перевода дней недели и месяцев
DAYS = {
    'Mon': 'Понедельник',
    'Tue': 'Вторник',
    'Wed': 'Среда',
    'Thu': 'Четверг',
    'Fri': 'Пятница',
    'Sat': 'Суббота',
    'Sun': 'Воскресенье'
}

MONTHS = {
    'Jan': 'Января',
    'Feb': 'Февраля',
    'Mar': 'Марта',
    'Apr': 'Апреля',
    'May': 'Мая',
    'Jun': 'Июня',
    'Jul': 'Июля',
    'Aug': 'Августа',
    'Sep': 'Сентября',
    'Oct': 'Октября',
    'Nov': 'Ноября',
    'Dec': 'Декабря'
}


def decode_email_header(header):
    """Декодирует заголовок письма"""
    decoded = []
    for part, encoding in decode_header(header):
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or 'utf-8'))
        else:
            decoded.append(part)
    return ''.join(decoded)


def get_email_text(email_message):
    """Извлекает текст из письма, обрабатывая как plain text, так и HTML"""
    text = ""

    def extract_text_from_html(html_content):
        """Извлекает текст из HTML, тщательно очищая теги и стили"""
        # Удаляем CSS стили
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)

        # Удаляем скрипты
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)

        # Заменяем <br>, <p>, <div> на переносы строк
        html_content = re.sub(r'<br[^>]*>', '\n', html_content)
        html_content = re.sub(r'</p>\s*<p[^>]*>', '\n\n', html_content)
        html_content = re.sub(r'<div[^>]*>', '\n', html_content)
        html_content = re.sub(r'</div>', '\n', html_content)

        # Удаляем все оставшиеся HTML теги
        html_content = re.sub(r'<[^>]+>', '', html_content)

        # Заменяем множественные переносы строк на двойной перенос
        html_content = re.sub(r'\n\s*\n', '\n\n', html_content)

        # Заменяем множественные пробелы на один
        html_content = re.sub(r'\s+', ' ', html_content)

        # Декодируем HTML сущности
        html_entities = {
            '&nbsp;': ' ',
            '&amp;': '&',
            '&lt;': '<',
            '&gt;': '>',
            '&quot;': '"',
            '&apos;': "'",
            '&#x27;': "'",
            '&#x2F;': '/',
            '&mdash;': '—',
            '&ndash;': '–',
            '&laquo;': '«',
            '&raquo;': '»',
        }
        for entity, char in html_entities.items():
            html_content = html_content.replace(entity, char)

        # Удаляем пустые строки в начале и конце
        return html_content.strip()

    if email_message.is_multipart():
        for part in email_message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # Пропускаем вложения
            if "attachment" in content_disposition:
                continue

            if content_type == "text/plain":
                try:
                    text += part.get_payload(decode=True).decode()
                except:
                    text += part.get_payload()
            elif content_type == "text/html":
                try:
                    html_content = part.get_payload(decode=True).decode()
                    text += extract_text_from_html(html_content)
                except:
                    text += extract_text_from_html(part.get_payload())
    else:
        content_type = email_message.get_content_type()
        if content_type == "text/plain":
            try:
                text = email_message.get_payload(decode=True).decode()
            except:
                text = email_message.get_payload()
        elif content_type == "text/html":
            try:
                html_content = email_message.get_payload(decode=True).decode()
                text = extract_text_from_html(html_content)
            except:
                text = extract_text_from_html(email_message.get_payload())

    # Обрабатываем текст для сохранения структуры абзацев
    # Разбиваем на абзацы
    paragraphs = text.split('\n\n')
    # Удаляем пустые абзацы и лишние пробелы
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    # Собираем обратно с сохранением структуры
    return '\n\n'.join(paragraphs)


def format_email_date(date_str):
    """Форматирует дату письма в русский формат"""
    if not date_str:
        return 'Дата неизвестна'

    try:
        # Разбиваем строку даты на части
        parts = date_str.split()
        if len(parts) < 5:
            return date_str

        # Получаем компоненты даты
        day_of_week = DAYS.get(parts[0].rstrip(','), parts[0])  # Убираем запятую из дня недели
        day = parts[1]
        month = MONTHS.get(parts[2], parts[2])
        year = parts[3]
        time = parts[4].split(':')[0:2]  # Берем только часы и минуты
        time_str = ':'.join(time)

        # Формируем дату вручную
        formatted_date = f"{day_of_week}, {day} {month} {year}, {time_str}"
        return formatted_date
    except Exception as e:
        logging.error(f"Error formatting date {date_str}: {str(e)}")
        return date_str


async def check_emails(user_id: int):
    logging.info(f"Starting email check for user {user_id}")

    if user_id not in last_email_ids:
        last_email_ids[user_id] = set()
        try:
            credentials = user_credentials[user_id]
            if credentials['service'] == 'gmail':
                imap = imaplib.IMAP4_SSL('imap.gmail.com')
                imap.authenticate('XOAUTH2', lambda
                    x: f"user={credentials['email']}\1auth=Bearer {credentials['access_token']}\1\1")
            else:
                imap = imaplib.IMAP4_SSL('imap.yandex.ru')
                imap.authenticate('XOAUTH2', lambda
                    x: f"user={credentials['email']}\1auth=Bearer {credentials['access_token']}\1\1")

            imap.select('INBOX')
            status, messages = imap.search(None, 'ALL')
            if status == 'OK':
                last_email_ids[user_id] = set(messages[0].split())
            imap.close()
            imap.logout()
        except Exception as e:
            logging.error(f"Error during initial email check: {str(e)}")

    while user_id in user_credentials:
        try:
            credentials = user_credentials[user_id]
            if credentials['service'] == 'gmail':
                imap = imaplib.IMAP4_SSL('imap.gmail.com')
            else:
                imap = imaplib.IMAP4_SSL('imap.yandex.ru')

            imap.authenticate('XOAUTH2',
                              lambda x: f"user={credentials['email']}\1auth=Bearer {credentials['access_token']}\1\1")
            imap.select('INBOX')
            status, messages = imap.search(None, 'ALL')

            if status == 'OK':
                current_ids = set(messages[0].split())
                new_ids = current_ids - last_email_ids[user_id]

                if new_ids:
                    for msg_id in reversed(sorted(new_ids)):
                        try:
                            status, msg_data = imap.fetch(msg_id, '(RFC822)')
                            if status == 'OK':
                                email_body = msg_data[0][1]
                                email_message = email.message_from_bytes(email_body)

                                subject = decode_email_header(email_message['subject'] or 'Без темы')
                                from_addr = decode_email_header(email_message['from'] or 'Неизвестно')
                                date_str = email_message['date']
                                date = format_email_date(date_str) if date_str else 'Дата неизвестна'
                                full_text = get_email_text(email_message)

                                short_text = full_text[:200] + "..." if len(full_text) > 200 else full_text

                                email_id = f"{user_id}_{msg_id.decode()}"

                                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(
                                        text="📖 Показать полностью",
                                        callback_data=f"show_full_{email_id}"
                                    )]
                                ])

                                message_text = (
                                    f"📧 Новое письмо:\n"
                                    f"От: {from_addr}\n"
                                    f"Тема: {subject}\n"
                                    f"Дата: {date}\n\n"
                                    f"Текст письма:\n{short_text}"
                                )

                                if 'email_texts' not in user_credentials[user_id]:
                                    user_credentials[user_id]['email_texts'] = {}
                                user_credentials[user_id]['email_texts'][email_id] = {
                                    'full_text': full_text,
                                    'short_text': short_text,
                                    'from_addr': from_addr,
                                    'subject': subject,
                                    'date': date
                                }

                                await bot.send_message(user_id, message_text, reply_markup=keyboard)
                                logging.info(f"Sent notification about new email to user {user_id}")
                        except Exception as e:
                            logging.error(f"Error processing email {msg_id}: {str(e)}")
                            continue

                last_email_ids[user_id] = current_ids

            imap.close()
            imap.logout()

        except Exception as e:
            logging.error(f"Error checking emails for user {user_id}: {str(e)}")
            await bot.send_message(user_id, f"❌ Произошла ошибка при проверке почты: {str(e)}")

        await asyncio.sleep(30)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Яндекс Почта", callback_data="auth_yandex")]
    ])
    await message.answer(
        "Привет! Я бот для работы с почтой.\n"
        "Выберите почтовый сервис для авторизации:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "auth_yandex")
async def process_auth_button(callback: types.CallbackQuery, state: FSMContext):
    # Получаем redirect_uri из .env или используем стандартный
    redirect_uri = os.getenv('YANDEX_REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

    auth_url = f"{YANDEX_AUTH_URL}?response_type=code&client_id={os.getenv('YANDEX_CLIENT_ID')}&redirect_uri={redirect_uri}&scope={YANDEX_SCOPES}"

    # Логируем URL для отладки
    logging.info(f"Generated auth URL: {auth_url}")

    await callback.message.answer(
        f"Пожалуйста, перейдите по ссылке для авторизации:\n{auth_url}\n\n"
        "После авторизации, отправьте мне полученный код."
    )
    await state.set_state(AuthStates.waiting_for_auth)
    await callback.answer()


@dp.callback_query(F.data == "auth_gmail")
async def process_gmail_auth(callback: types.CallbackQuery, state: FSMContext):
    # Получаем redirect_uri из .env или используем стандартный
    redirect_uri = os.getenv('GMAIL_REDIRECT_URI', 'https://oauth2.googleapis.com/verification_code')

    auth_url = f"{GMAIL_AUTH_URL}?response_type=code&client_id={os.getenv('GMAIL_CLIENT_ID')}&redirect_uri={redirect_uri}&scope={GMAIL_SCOPES}&access_type=offline&prompt=consent"

    await callback.message.answer(
        f"Пожалуйста, перейдите по ссылке для авторизации в Gmail:\n{auth_url}\n\n"
        "После авторизации, отправьте мне полученный код."
    )
    await state.set_state(AuthStates.waiting_for_auth)
    await callback.answer()


@dp.message(AuthStates.waiting_for_auth)
async def process_auth_code(message: types.Message, state: FSMContext):
    auth_code = message.text.strip()

    if not auth_code or len(auth_code) < 4:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
        ])
        await message.answer(
            "❌ Это не похоже на код авторизации. Пожалуйста, отправьте код, который вы получили после авторизации.",
            reply_markup=keyboard
        )
        return

    try:
        async with aiohttp.ClientSession() as session:
            # Определяем, какой сервис используется
            if 'gmail' in message.text.lower():
                token_url = GMAIL_TOKEN_URL
                client_id = os.getenv('GMAIL_CLIENT_ID')
                client_secret = os.getenv('GMAIL_CLIENT_SECRET')
                user_info_url = 'https://www.googleapis.com/oauth2/v3/userinfo'
            else:
                token_url = YANDEX_TOKEN_URL
                client_id = os.getenv('YANDEX_CLIENT_ID')
                client_secret = os.getenv('YANDEX_CLIENT_SECRET')
                user_info_url = 'https://login.yandex.ru/info'

            # Exchange auth code for access token
            async with session.post(token_url, data={
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': os.getenv(
                    'GMAIL_REDIRECT_URI' if 'gmail' in message.text.lower() else 'YANDEX_REDIRECT_URI')
            }) as response:
                if response.status == 200:
                    token_data = await response.json()

                    # Получаем email пользователя
                    async with session.get(user_info_url, headers={
                        'Authorization': f'Bearer {token_data["access_token"]}'
                    }) as user_info_response:
                        if user_info_response.status == 200:
                            user_info = await user_info_response.json()

                            # Получаем email в зависимости от сервиса
                            if 'gmail' in message.text.lower():
                                email = user_info.get('email')
                            else:
                                email = user_info.get('default_email') or user_info.get('emails', [None])[
                                    0] or f"{user_info.get('login')}@yandex.ru"

                            if email:
                                user_credentials[message.from_user.id] = {
                                    'email': email,
                                    'access_token': token_data['access_token'],
                                    'refresh_token': token_data.get('refresh_token'),
                                    'service': 'gmail' if 'gmail' in message.text.lower() else 'yandex'
                                }

                                await message.answer(
                                    "✅ Авторизация успешна! Теперь я буду проверять вашу почту."
                                )
                                await state.clear()

                                # Start email checking loop
                                asyncio.create_task(check_emails(message.from_user.id))
                            else:
                                await message.answer(
                                    "❌ Не удалось определить email пользователя. "
                                    "Пожалуйста, убедитесь, что у вас есть доступ к почте.\n"
                                    "Попробуйте отправить код еще раз."
                                )
                        else:
                            await message.answer(
                                "❌ Ошибка при получении информации о пользователе. "
                                "Пожалуйста, попробуйте отправить код еще раз."
                            )
                else:
                    await message.answer(
                        "❌ Ошибка авторизации. Пожалуйста, попробуйте отправить код еще раз."
                    )
    except Exception as e:
        await message.answer(
            f"❌ Произошла ошибка: {str(e)}\n"
            "Пожалуйста, попробуйте отправить код еще раз."
        )


@dp.callback_query(F.data == "cancel_auth")
async def cancel_auth(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "❌ Авторизация отменена.\n"
        "Если вы хотите попробовать снова, нажмите кнопку 'Авторизоваться в Яндекс'."
    )
    await callback.answer()


async def send_long_message(chat_id: int, text: str, reply_markup=None):
    """Отправляет длинное сообщение, разбивая его на части"""
    MAX_LENGTH = 4000  # Оставляем запас для форматирования

    if len(text) <= MAX_LENGTH:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return

    # Разбиваем текст на части
    parts = []
    current_part = ""

    # Разбиваем по абзацам
    paragraphs = text.split('\n')
    for paragraph in paragraphs:
        if len(current_part) + len(paragraph) + 1 <= MAX_LENGTH:
            current_part += paragraph + '\n'
        else:
            if current_part:
                parts.append(current_part)
            current_part = paragraph + '\n'

    if current_part:
        parts.append(current_part)

    # Отправляем части
    for i, part in enumerate(parts):
        if i == 0:  # Первая часть с клавиатурой
            await bot.send_message(chat_id, part, reply_markup=reply_markup)
        else:  # Остальные части без клавиатуры
            await bot.send_message(chat_id, part)


@dp.callback_query(F.data.startswith("show_full_"))
async def show_full_email(callback: types.CallbackQuery):
    try:
        # Получаем ID письма из callback_data
        email_id = callback.data.replace("show_full_", "")
        user_id = callback.from_user.id

        # Проверяем, есть ли сохраненный текст для этого письма
        if user_id in user_credentials and 'email_texts' in user_credentials[user_id] and email_id in \
                user_credentials[user_id]['email_texts']:
            email_data = user_credentials[user_id]['email_texts'][email_id]

            # Формируем сообщение с полным текстом
            full_message = (
                f"📧 Письмо:\n"
                f"От: {email_data['from_addr']}\n"
                f"Тема: {email_data['subject']}\n"
                f"Дата: {email_data['date']}\n\n"
                f"Текст письма:\n{email_data['full_text']}"
            )

            # Создаем клавиатуру с кнопкой "Скрыть"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📖 Скрыть",
                    callback_data=f"hide_full_{email_id}"
                )]
            ])

            # Удаляем старое сообщение
            await callback.message.delete()
            # Отправляем новое сообщение с полным текстом
            await send_long_message(user_id, full_message, keyboard)
            await callback.answer()
        else:
            await callback.answer("❌ Текст письма не найден", show_alert=True)
    except Exception as e:
        logging.error(f"Error showing full email: {str(e)}")
        await callback.answer("❌ Произошла ошибка при отображении письма", show_alert=True)


@dp.callback_query(F.data.startswith("hide_full_"))
async def hide_full_email(callback: types.CallbackQuery):
    try:
        # Получаем ID письма из callback_data
        email_id = callback.data.replace("hide_full_", "")
        user_id = callback.from_user.id

        # Проверяем, есть ли сохраненный текст для этого письма
        if user_id in user_credentials and 'email_texts' in user_credentials[user_id] and email_id in \
                user_credentials[user_id]['email_texts']:
            email_data = user_credentials[user_id]['email_texts'][email_id]

            # Формируем сообщение с коротким текстом
            short_message = (
                f"📧 Письмо:\n"
                f"От: {email_data['from_addr']}\n"
                f"Тема: {email_data['subject']}\n"
                f"Дата: {email_data['date']}\n\n"
                f"Текст письма:\n{email_data['short_text']}"
            )

            # Создаем клавиатуру с кнопкой "Показать полностью"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📖 Показать полностью",
                    callback_data=f"show_full_{email_id}"
                )]
            ])

            # Удаляем старое сообщение
            await callback.message.delete()
            # Отправляем новое сообщение с коротким текстом
            await bot.send_message(user_id, short_message, reply_markup=keyboard)
            await callback.answer()
        else:
            await callback.answer("❌ Текст письма не найден", show_alert=True)
    except Exception as e:
        logging.error(f"Error hiding full email: {str(e)}")
        await callback.answer("❌ Произошла ошибка при скрытии письма", show_alert=True)


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main()) 