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
    waiting_for_password = State()


# Yandex OAuth configuration
YANDEX_AUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_SCOPES = "mail:imap_full"  # Полный доступ к почте через IMAP

# Store user tokens and email credentials
user_tokens = {}
user_credentials = {}
last_email_ids = {}  # Хранение ID последних проверенных писем для каждого пользователя


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


async def check_emails(user_id: int):
    logging.info(f"Starting email check for user {user_id}")

    # Инициализируем список последних ID для пользователя
    if user_id not in last_email_ids:
        last_email_ids[user_id] = set()
        # При первом запуске получаем текущие ID писем, но не отправляем их
        try:
            credentials = user_credentials[user_id]
            imap = imaplib.IMAP4_SSL('imap.yandex.ru')
            imap.login(credentials['email'], credentials['password'])
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
            imap.login(credentials['email'], credentials['password'])

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
                                date = email_message['date'] or 'Дата неизвестна'
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
                    # Получаем email пользователя
                    async with session.get('https://login.yandex.ru/info', headers={
                        'Authorization': f'OAuth {token_data["access_token"]}'
                    }) as user_info_response:
                        if user_info_response.status == 200:
                            user_info = await user_info_response.json()
                            email = user_info.get('default_email')

                            if email:
                                await message.answer(
                                    "✅ Авторизация успешна!\n\n"
                                    "Для доступа к почте через IMAP вам нужно:\n"
                                    "1. Перейдите в настройки безопасности Яндекс: https://id.yandex.ru/security\n"
                                    "2. В разделе 'Пароли приложений' создайте новый пароль\n"
                                    "3. Выберите 'Почта' в качестве приложения\n"
                                    "4. Скопируйте сгенерированный пароль и отправьте его мне"
                                )
                                # Сохраняем email для следующего шага
                                user_credentials[message.from_user.id] = {
                                    'email': email,
                                    'waiting_for_password': True
                                }
                                await state.set_state(AuthStates.waiting_for_password)
                            else:
                                await message.answer("❌ Не удалось получить email пользователя.")
                        else:
                            await message.answer("❌ Ошибка при получении информации о пользователе.")
                else:
                    response_text = await response.text()
                    logging.error(f"Token request failed: {response.status} - {response_text}")
                    await message.answer("❌ Ошибка авторизации. Пожалуйста, попробуйте снова.")
    except Exception as e:
        logging.error(f"Error during token exchange: {str(e)}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")
        await state.clear()


@dp.message(AuthStates.waiting_for_password)
async def process_app_password(message: types.Message, state: FSMContext):
    if message.from_user.id not in user_credentials:
        await message.answer("❌ Ошибка: сначала выполните авторизацию через Яндекс.")
        await state.clear()
        return

    app_password = message.text.strip()
    user_credentials[message.from_user.id]['password'] = app_password
    user_credentials[message.from_user.id]['waiting_for_password'] = False

    await message.answer("✅ Пароль приложения сохранен! Теперь я буду проверять вашу почту.")
    await state.clear()

    # Start email checking loop
    asyncio.create_task(check_emails(message.from_user.id))


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

            # Отправляем полный текст
            await callback.message.edit_text(full_message, reply_markup=keyboard)
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

            # Редактируем сообщение, показывая короткий текст
            await callback.message.edit_text(short_message, reply_markup=keyboard)
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