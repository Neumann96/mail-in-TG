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
    """Извлекает текст из письма"""
    text = ""
    if email_message.is_multipart():
        for part in email_message.walk():
            if part.get_content_type() == "text/plain":
                try:
                    text += part.get_payload(decode=True).decode()
                except:
                    text += part.get_payload()
    else:
        try:
            text = email_message.get_payload(decode=True).decode()
        except:
            text = email_message.get_payload()
    return text.strip()


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

    # Инициализируем список последних ID для пользователя
    if user_id not in last_email_ids:
        last_email_ids[user_id] = set()
        # При первом запуске получаем текущие ID писем, но не отправляем их
        try:
            credentials = user_credentials[user_id]
            imap = imaplib.IMAP4_SSL('imap.yandex.ru')
            # Используем OAuth токен для авторизации
            imap.authenticate('XOAUTH2',
                              lambda x: f"user={credentials['email']}\1auth=Bearer {credentials['access_token']}\1\1")
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
            # Подключаемся к IMAP серверу
            imap = imaplib.IMAP4_SSL('imap.yandex.ru')
            # Используем OAuth токен для авторизации
            imap.authenticate('XOAUTH2',
                              lambda x: f"user={credentials['email']}\1auth=Bearer {credentials['access_token']}\1\1")

            # Выбираем папку входящих
            imap.select('INBOX')

            # Получаем все письма
            status, messages = imap.search(None, 'ALL')

            if status == 'OK':
                # Получаем ID всех писем
                current_ids = set(messages[0].split())

                # Проверяем только новые письма (те, которых нет в last_email_ids)
                new_ids = current_ids - last_email_ids[user_id]

                if new_ids:
                    # Обрабатываем только новые письма
                    for msg_id in reversed(
                            sorted(new_ids)):  # Сортируем в обратном порядке, чтобы получить сначала новые
                        try:
                            # Получаем письмо
                            status, msg_data = imap.fetch(msg_id, '(RFC822)')
                            if status == 'OK':
                                email_body = msg_data[0][1]
                                email_message = email.message_from_bytes(email_body)

                                # Получаем информацию о письме
                                subject = decode_email_header(email_message['subject'] or 'Без темы')
                                from_addr = decode_email_header(email_message['from'] or 'Неизвестно')
                                date_str = email_message['date']
                                date = format_email_date(date_str) if date_str else 'Дата неизвестна'
                                full_text = get_email_text(email_message)

                                # Формируем короткое сообщение
                                short_text = full_text[:200] + "..." if len(full_text) > 200 else full_text

                                # Создаем уникальный идентификатор для письма
                                email_id = f"{user_id}_{msg_id.decode()}"

                                # Создаем клавиатуру с кнопкой "Показать полностью"
                                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(
                                        text="📖 Показать полностью",
                                        callback_data=f"show_full_{email_id}"
                                    )]
                                ])

                                # Формируем сообщение
                                message_text = (
                                    f"📧 Новое письмо:\n"
                                    f"От: {from_addr}\n"
                                    f"Тема: {subject}\n"
                                    f"Дата: {date}\n\n"
                                    f"Текст письма:\n{short_text}"
                                )

                                # Сохраняем полный текст для этого сообщения
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

                # Обновляем список последних ID
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
        [InlineKeyboardButton(text="🔑 Авторизоваться в Яндекс", callback_data="auth_yandex")]
    ])
    await message.answer(
        "Привет! Я бот для работы с Яндекс Почтой.\n"
        "Нажмите кнопку ниже, чтобы авторизоваться:",
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


@dp.message(AuthStates.waiting_for_auth)
async def process_auth_code(message: types.Message, state: FSMContext):
    auth_code = message.text.strip()

    # Проверяем, является ли сообщение кодом (код может содержать буквы и цифры)
    if not auth_code or len(auth_code) < 4:  # Минимальная длина кода обычно 4 символа
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
            # Exchange auth code for access token
            async with session.post(YANDEX_TOKEN_URL, data={
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_id': os.getenv('YANDEX_CLIENT_ID'),
                'client_secret': os.getenv('YANDEX_CLIENT_SECRET')
            }) as response:
                if response.status == 200:
                    token_data = await response.json()
                    logging.info(f"Successfully received token data: {token_data}")

                    # Получаем email пользователя
                    async with session.get('https://login.yandex.ru/info', headers={
                        'Authorization': f'OAuth {token_data["access_token"]}'
                    }) as user_info_response:
                        if user_info_response.status == 200:
                            user_info = await user_info_response.json()
                            logging.info(f"Received user info: {user_info}")

                            # Пробуем получить email разными способами
                            email = None
                            if 'default_email' in user_info:
                                email = user_info['default_email']
                            elif 'emails' in user_info and user_info['emails']:
                                email = user_info['emails'][0]
                            elif 'login' in user_info:
                                email = f"{user_info['login']}@yandex.ru"

                            if email:
                                # Сохраняем данные пользователя
                                user_credentials[message.from_user.id] = {
                                    'email': email,
                                    'access_token': token_data['access_token'],
                                    'refresh_token': token_data.get('refresh_token')
                                }

                                await message.answer(
                                    "✅ Авторизация успешна! Теперь я буду проверять вашу почту."
                                )
                                await state.clear()

                                # Start email checking loop
                                asyncio.create_task(check_emails(message.from_user.id))
                            else:
                                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
                                ])
                                logging.error(f"Could not determine email from user info: {user_info}")
                                await message.answer(
                                    "❌ Не удалось определить email пользователя. "
                                    "Пожалуйста, убедитесь, что у вас есть доступ к Яндекс.Почте.\n"
                                    "Попробуйте отправить код еще раз.",
                                    reply_markup=keyboard
                                )
                        else:
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
                            ])
                            error_text = await user_info_response.text()
                            logging.error(f"Error getting user info: {user_info_response.status} - {error_text}")
                            await message.answer(
                                "❌ Ошибка при получении информации о пользователе. "
                                "Пожалуйста, попробуйте отправить код еще раз.",
                                reply_markup=keyboard
                            )
                else:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
                    ])
                    response_text = await response.text()
                    logging.error(f"Token request failed: {response.status} - {response_text}")
                    error_data = await response.json()

                    if error_data.get('error') == 'invalid_grant':
                        # Код истек, предлагаем повторить авторизацию
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Повторить авторизацию", callback_data="auth_yandex")],
                            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
                        ])
                        await message.answer(
                            "❌ Код авторизации истек. Пожалуйста, повторите процесс авторизации.",
                            reply_markup=keyboard
                        )
                    else:
                        await message.answer(
                            "❌ Ошибка авторизации. Пожалуйста, попробуйте отправить код еще раз.",
                            reply_markup=keyboard
                        )
    except Exception as e:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")]
        ])
        logging.error(f"Error during token exchange: {str(e)}")
        await message.answer(
            f"❌ Произошла ошибка: {str(e)}\n"
            "Пожалуйста, попробуйте отправить код еще раз.",
            reply_markup=keyboard
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