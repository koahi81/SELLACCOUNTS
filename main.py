from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
import logging
import sqlite3
import random
import os

# ================== CONFIG ==================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '7636170713'))
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '@koshiyyi')
API_ID = int(os.getenv('API_ID', '26449109'))
API_HASH = os.getenv('API_HASH', 'aaeee2d2d8859857517ab9b0f7ccea19')
PRICE = 130  # Цена в рублях

# ================== DATABASE ==================
class Database:
    def __init__(self, db_path='/tmp/accounts.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT UNIQUE,
                    password TEXT,
                    session_file TEXT,
                    status TEXT DEFAULT 'ready',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    user_id INTEGER,
                    amount INTEGER,
                    sold_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
    
    def add_account(self, phone_number, password, session_file):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO accounts (phone_number, password, session_file, status)
                VALUES (?, ?, ?, 'ready')
            ''', (phone_number, password, session_file))
            conn.commit()
    
    def get_ready_account(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM accounts WHERE status = 'ready' LIMIT 1
            ''')
            return cursor.fetchone()
    
    def mark_account_sold(self, account_id, user_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE accounts SET status = 'sold' WHERE id = ?
            ''', (account_id,))
            cursor.execute('''
                INSERT INTO sales (account_id, user_id, amount) VALUES (?, ?, ?)
            ''', (account_id, user_id, amount))
            conn.commit()
    
    def get_balance(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT balance FROM balance WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
    
    def update_balance(self, user_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO balance (user_id, balance) 
                VALUES (?, COALESCE((SELECT balance FROM balance WHERE user_id = ?), 0) + ?)
            ''', (user_id, user_id, amount))
            conn.commit()
    
    def get_stats(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM accounts WHERE status = "ready"')
            ready_accounts = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM sales WHERE DATE(sold_at) = DATE("now")')
            sold_today = cursor.fetchone()[0]
            
            cursor.execute('SELECT SUM(amount) FROM sales WHERE DATE(sold_at) = DATE("now")')
            revenue_today = cursor.fetchone()[0] or 0
            
            return ready_accounts, sold_today, revenue_today

# ================== TELETHON CLIENT ==================
class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.active_clients = {}
    
    async def authorize_account(self, phone_number, code, password):
        try:
            session = StringSession()
            client = TelegramClient(session, self.api_id, self.api_hash)
            await client.connect()
            
            # Входим в аккаунт
            await client.sign_in(phone_number, code)
            
            # Устанавливаем пароль если требуется
            if not await client.is_user_authorized():
                await client.sign_in(password=password)
            
            if await client.is_user_authorized():
                session_string = session.save()
                self.active_clients[phone_number] = {
                    'client': client,
                    'session_string': session_string
                }
                return {'success': True, 'session_string': session_string}
            else:
                return {'success': False, 'error': 'Authorization failed'}
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    async def get_code_from_session(self, phone_number):
        try:
            client_data = self.active_clients.get(phone_number)
            if client_data:
                # Генерируем случайный код (в реальности получаем из сессии)
                return str(random.randint(10000, 99999))
            return None
        except Exception as e:
            logging.error(f"Error getting code: {e}")
            return None

# ================== BOT INIT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = Database()
account_manager = AccountManager(API_ID, API_HASH)

# States для добавления аккаунтов
class AddAccount(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

# States для пополнения баланса
class TopUpBalance(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

# Глобальный словарь для временных данных
user_data = {}

# Функция проверки админа
def is_admin(user_id, username):
    username = username.lower() if username else ""
    return user_id == ADMIN_ID or username == ADMIN_USERNAME.lower()

# ================== ADMIN COMMANDS ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if is_admin(message.from_user.id, message.from_user.username):
        await message.answer(
            "👨‍💻 Панель администратора\n\n"
            "Доступные команды:\n"
            "/add_accounts - Добавить аккаунты\n"
            "/topup_balance - Пополнить баланс\n"
            "/stats - Статистика\n"
            "/my_balance - Мой баланс"
        )
    else:
        balance = db.get_balance(message.from_user.id)
        await message.answer(
            f"🛒 Добро пожаловать!\n\n"
            f"Купить Telegram аккаунт - {PRICE}₽\n"
            f"💰 Ваш баланс: {balance}₽\n\n"
            f"Выберите действие:",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="🛒 Купить аккаунт",
                            callback_data="buy_account"
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="💰 Мой баланс",
                            callback_data="my_balance"
                        )
                    ]
                ]
            )
        )

@dp.message(Command("add_accounts"))
async def cmd_add_accounts(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        return
    await message.answer("Введите номер телефона аккаунта:")
    await state.set_state(AddAccount.waiting_for_phone)

@dp.message(AddAccount.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone=phone)
    await message.answer(f"📱 Номер: {phone}\n⏳ Ожидаю код из SMS...")
    await message.answer("Введите полученный код:")
    await state.set_state(AddAccount.waiting_for_code)

@dp.message(AddAccount.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    await state.update_data(code=code)
    await message.answer("Введите пароль для этого аккаунта:")
    await state.set_state(AddAccount.waiting_for_password)

@dp.message(AddAccount.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    phone = data['phone']
    code = data['code']
    
    result = await account_manager.authorize_account(phone, code, password)
    
    if result['success']:
        db.add_account(phone, password, result['session_string'])
        await message.answer(
            f"✅ Аккаунт добавлен!\n"
            f"📱 {phone}\n"
            f"🔐 Пароль: {password}"
        )
    else:
        await message.answer(f"❌ Ошибка: {result['error']}")
    await state.clear()

@dp.message(Command("topup_balance"))
async def cmd_topup_balance(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        return
    await message.answer("Введите ID пользователя для пополнения:")
    await state.set_state(TopUpBalance.waiting_for_user_id)

@dp.message(TopUpBalance.waiting_for_user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        await message.answer("Введите сумму для пополнения:")
        await state.set_state(TopUpBalance.waiting_for_amount)
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")

@dp.message(TopUpBalance.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        user_id = data['user_id']
        
        db.update_balance(user_id, amount)
        new_balance = db.get_balance(user_id)
        
        await message.answer(
            f"✅ Баланс пополнен!\n"
            f"👤 Пользователь: {user_id}\n"
            f"💳 Сумма: +{amount}₽\n"
            f"💰 Новый баланс: {new_balance}₽"
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"💰 Ваш баланс пополнен на {amount}₽\n"
                f"💳 Новый баланс: {new_balance}₽"
            )
        except:
            await message.answer("⚠️ Не удалось уведомить пользователя")
        
        await state.clear()
    except ValueError:
        await message.answer("❌ Неверная сумма")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        return
    
    ready_accounts, sold_today, revenue_today = db.get_stats()
    
    await message.answer(
        f"📊 Статистика магазина:\n\n"
        f"📱 Аккаунтов готово: {ready_accounts}\n"
        f"🛒 Продано сегодня: {sold_today}\n"
        f"💰 Выручка сегодня: {revenue_today}₽"
    )

@dp.message(Command("my_balance"))
async def cmd_my_balance(message: types.Message):
    balance = db.get_balance(message.from_user.id)
    await message.answer(f"💰 Ваш баланс: {balance}₽")

# ================== BUYING FLOW ==================
@dp.callback_query(F.data == "buy_account")
async def process_buy(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance = db.get_balance(user_id)
    
    if balance >= PRICE:
        # Списание средств
        db.update_balance(user_id, -PRICE)
        new_balance = db.get_balance(user_id)
        
        account = db.get_ready_account()
        
        if account:
            account_id, phone, password, session_file, status, created_at = account
            
            user_data[user_id] = {
                'account_id': account_id,
                'phone': phone,
                'password': password
            }
            
            await callback.message.answer(
                f"✅ Покупка успешна! Списано {PRICE}₽\n"
                f"💰 Новый баланс: {new_balance}₽\n\n"
                f"📱 Ваш номер для входа:\n"
                f"`{phone}`\n\n"
                f"Нажмите кнопку ниже чтобы получить код:",
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[
                        types.InlineKeyboardButton(
                            text="🔑 Получить код",
                            callback_data="get_code"
                        )
                    ]]
                )
            )
        else:
            # Возвращаем деньги если нет аккаунтов
            db.update_balance(user_id, PRICE)
            await callback.message.answer("❌ Нет доступных аккаунтов. Деньги возвращены на баланс.")
    else:
        await callback.message.answer(
            f"❌ Недостаточно средств\n"
            f"💳 Нужно: {PRICE}₽\n"
            f"💰 Ваш баланс: {balance}₽\n\n"
            f"Обратитесь к администратору для пополнения."
        )

@dp.callback_query(F.data == "get_code")
async def process_get_code(callback: types.CallbackQuery):
    user_info = user_data.get(callback.from_user.id)
    
    if not user_info:
        await callback.answer("❌ Аккаунт не найден", show_alert=True)
        return
    
    # Получаем код из активной сессии
    code = await account_manager.get_code_from_session(user_info['phone'])
    
    if code:
        # Даем код
        await callback.message.answer(
            f"🔑 Ваш код для входа:\n"
            f"`{code}`\n\n"
            f"⏳ Код действителен 5 минут",
            parse_mode="Markdown"
        )
        
        # Сразу даем пароль
        await callback.message.answer(
            f"🔐 Ваш пароль:\n"
            f"`{user_info['password']}`\n\n"
            f"📋 Для входа используйте:\n"
            f"• Номер: `{user_info['phone']}`\n"  
            f"• Код: `{code}`\n"
            f"• Пароль: `{user_info['password']}`",
            parse_mode="Markdown"
        )
        
        # Помечаем как проданный
        db.mark_account_sold(user_info['account_id'], callback.from_user.id, PRICE)
        del user_data[callback.from_user.id]
        
    else:
        await callback.message.answer("❌ Ошибка получения кода")

@dp.callback_query(F.data == "my_balance")
async def process_my_balance(callback: types.CallbackQuery):
    balance = db.get_balance(callback.from_user.id)
    await callback.message.answer(f"💰 Ваш баланс: {balance}₽")

# ================== START BOT ==================
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
