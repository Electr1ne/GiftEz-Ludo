import asyncio
import datetime
import logging
import os
import random
import sqlite3
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

# ==================== НАСТРОЙКИ ИЗ .ENV ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", ""))
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

# Конфигурация подарков и привязанных к ним файлов
GIFTS_CONFIG = {
    "bear": {
        "title": "🧸 Мишка",
        "stars": 15,
        "payout": 13,
        "image": os.getenv("IMG_BEAR", "bear.png"),
    },
    "heart": {
        "title": "💝 Сердце",
        "stars": 15,
        "payout": 13,
        "image": os.getenv("IMG_HEART", "heart.png"),
    },
    "present": {
        "title": "🎁 Подарок",
        "stars": 25,
        "payout": 21,
        "image": os.getenv("IMG_GIFT", "gift.png"),
    },
    "rose": {
        "title": "🌹 Роза",
        "stars": 25,
        "payout": 21,
        "image": os.getenv("IMG_ROSE", "rose.png"),
    },
    "bottle": {
        "title": "🍾 Бутылка",
        "stars": 50,
        "payout": 43,
        "image": os.getenv("IMG_BOTTLE", "bottle.png"),
    },
    "cake": {
        "title": "🎂 Торт",
        "stars": 50,
        "payout": 43,
        "image": os.getenv("IMG_CAKE", "cake.png"),
    },
    "rocket": {
        "title": "🚀 Ракета",
        "stars": 50,
        "payout": 43,
        "image": os.getenv("IMG_ROCKET", "rocket.png"),
    },
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ==================== FSM СОСТОЯНИЯ ====================
class AdminStates(StatesGroup):
    waiting_for_reject_reason = State()
    waiting_for_broadcast_target = State()
    waiting_for_broadcast_message = State()
    waiting_for_spin_price = State()
    waiting_for_mod_user_id = State()
    waiting_for_unwarn_amount = State()
    waiting_for_mute_time = State()


# ==================== БАЗА ДАННЫХ И МИГРАЦИИ (SQLITE3) ====================
def _db_init_sync():
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                spins_count INTEGER DEFAULT 0,
                warnings INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                gift_key TEXT,
                stars INTEGER,
                payout INTEGER,
                status TEXT DEFAULT 'available'
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """
        )

        default_settings = [
            ('spin_price', '1'),
            ('max_forwards', '1'),
            ('forward_punishment', 'warn'),
            ('forward_mute_time', '60'),
            ('warn_limit', '4'),
            ('warn_punishment', 'mute'),
            ('warn_mute_time', '1440'),
        ]
        for key, val in default_settings:
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val),
            )

        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        if "warnings" not in columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN warnings INTEGER DEFAULT 0"
            )
        if "is_banned" not in columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0"
            )

        db.commit()


async def init_db():
    await asyncio.to_thread(_db_init_sync)


def _get_setting_sync(key: str, default: str = "") -> str:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default


async def get_setting(key: str, default: str = "") -> str:
    return await asyncio.to_thread(_get_setting_sync, key, default)


def _set_setting_sync(key: str, value: str):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        db.commit()


async def set_setting(key: str, value: str):
    await asyncio.to_thread(_set_setting_sync, key, value)


async def get_spin_price() -> int:
    val = await get_setting('spin_price', '1')
    return int(val)


async def set_spin_price(price: int):
    await set_setting('spin_price', str(price))


def _register_user_sync(user_id: int, username: Optional[str], full_name: str):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, username, full_name, spins_count, warnings, is_banned)
            VALUES (?, ?, ?, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """,
            (user_id, username or "", full_name),
        )
        db.commit()


async def register_user(user_id: int, username: Optional[str], full_name: str):
    await asyncio.to_thread(_register_user_sync, user_id, username, full_name)


def _is_user_banned_sync(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row else False


async def is_user_banned(user_id: int) -> bool:
    return await asyncio.to_thread(_is_user_banned_sync, user_id)


def _increment_user_spins_sync(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE users SET spins_count = spins_count + 1 WHERE user_id = ?",
            (user_id,),
        )
        db.commit()
        cursor.execute("SELECT spins_count FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 1


async def increment_user_spins(user_id: int) -> int:
    return await asyncio.to_thread(_increment_user_spins_sync, user_id)


def _reset_user_spins_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE users SET spins_count = 0 WHERE user_id = ?", (user_id,)
        )
        db.commit()


async def reset_user_spins(user_id: int):
    await asyncio.to_thread(_reset_user_spins_sync, user_id)


def _register_chat_sync(chat_id: int, title: str):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)",
            (chat_id, title),
        )
        db.commit()


async def register_chat(chat_id: int, title: str):
    await asyncio.to_thread(_register_chat_sync, chat_id, title)


def _add_item_to_inventory_sync(user_id: int, username: str, gift_key: str) -> int:
    config = GIFTS_CONFIG[gift_key]
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO inventory (user_id, username, gift_key, stars, payout, status)
            VALUES (?, ?, ?, ?, ?, 'available')
        """,
            (
                user_id,
                username,
                gift_key,
                config["stars"],
                config["payout"],
            ),
        )
        db.commit()
        return cursor.lastrowid


async def add_item_to_inventory(user_id: int, username: str, gift_key: str) -> int:
    return await asyncio.to_thread(_add_item_to_inventory_sync, user_id, username, gift_key)


def _update_item_status_sync(item_id: int, status: str):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE inventory SET status = ? WHERE id = ?", (status, item_id)
        )
        db.commit()


async def update_item_status(item_id: int, status: str):
    await asyncio.to_thread(_update_item_status_sync, item_id, status)


def _delete_item_sync(item_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        db.commit()


async def delete_item(item_id: int):
    await asyncio.to_thread(_delete_item_sync, item_id)


def _clear_user_inventory_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
        db.commit()


async def clear_user_inventory(user_id: int):
    await asyncio.to_thread(_clear_user_inventory_sync, user_id)


def _get_item_by_id_sync(item_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, user_id, username, gift_key, stars, payout, status FROM inventory WHERE id = ?",
            (item_id,),
        )
        return cursor.fetchone()


async def get_item_by_id(item_id: int):
    return await asyncio.to_thread(_get_item_by_id_sync, item_id)


def _get_user_items_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, gift_key, stars, payout, status FROM inventory WHERE user_id = ?",
            (user_id,),
        )
        return cursor.fetchall()


async def get_user_items(user_id: int):
    return await asyncio.to_thread(_get_user_items_sync, user_id)


def _get_pending_withdrawals_count_sync() -> int:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE status = 'pending'")
        row = cursor.fetchone()
        return row[0] if row else 0


async def get_pending_withdrawals_count() -> int:
    return await asyncio.to_thread(_get_pending_withdrawals_count_sync)


# ==================== ДИНАМИЧЕСКИЙ РАСЧЕТ ШАНСОВ ====================
def select_gift_by_spins(spent_stars: int) -> str:
    if spent_stars >= 55:
        weights = {
            "bear": 50,
            "heart": 50,
            "present": 300,
            "rose": 300,
            "bottle": 120,
            "cake": 120,
            "rocket": 60,
        }
    elif spent_stars >= 20:
        weights = {
            "bear": 200,
            "heart": 200,
            "present": 250,
            "rose": 250,
            "bottle": 40,
            "cake": 40,
            "rocket": 20,
        }
    else:
        weights = {
            "bear": 350,
            "heart": 350,
            "present": 110,
            "rose": 110,
            "bottle": 35,
            "cake": 35,
            "rocket": 10,
        }

    keys = list(weights.keys())
    w_list = [weights[k] for k in keys]
    return random.choices(keys, weights=w_list, k=1)[0]


# ==================== КЛАВИАТУРЫ ====================
def get_bot_start_kb(user_id: int):
    buttons = [
        [
            InlineKeyboardButton(
                text="🎒 Мой Инвентарь", callback_data="open_inventory"
            ),
            InlineKeyboardButton(
                text="👤 Мой Профиль", callback_data="open_profile"
            ),
        ]
    ]
    if user_id == ADMIN_ID:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Админ Панель", callback_data="admin_main_menu"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_instant_withdraw_kb(item_id: int, gift_title: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💸 Вывести {gift_title}",
                    callback_data=f"user_withdraw_{item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎒 Открыть инвентарь", callback_data="open_inventory"
                )
            ],
        ]
    )


def get_inventory_kb(items):
    keyboard = []
    for item_id, gift_key, stars, payout, status in items:
        if status == "available":
            gift_title = GIFTS_CONFIG.get(gift_key, {}).get(
                "title", "🎁 Подарок"
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"Вывести {gift_title} ({stars} ⭐ -> {payout} ⭐)",
                        callback_data=f"user_withdraw_{item_id}",
                    )
                ]
            )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🗑️ Очистить мой инвентарь", callback_data="clear_my_inventory"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить", callback_data="open_inventory"
            ),
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data="back_to_start"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_approval_kb(item_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Выдано", callback_data=f"admin_approve_{item_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отказать", callback_data=f"admin_reject_{item_id}"
                ),
            ]
        ]
    )


def get_reject_decision_kb(item_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💾 Сохранить в инвентаре",
                    callback_data=f"keep_item_{item_id}",
                ),
                InlineKeyboardButton(
                    text="🗑️ Удалить предмет",
                    callback_data=f"delete_item_{item_id}",
                ),
            ]
        ]
    )


async def get_admin_panel_kb(spin_price: int):
    pending_cnt = await get_pending_withdrawals_count()
    withdraw_btn_text = f"📥 Заявки на вывод ({pending_cnt})" if pending_cnt > 0 else "📥 Заявки на вывод (0)"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💎 Стоимость прокрута: {spin_price} ⭐",
                    callback_data="change_spin_price",
                )
            ],
            [
                InlineKeyboardButton(
                    text=withdraw_btn_text,
                    callback_data="admin_withdrawals_0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡️ Управление Модерацией и Наказаниями",
                    callback_data="admin_mod_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Список пользователей и Статистика",
                    callback_data="users_list_0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Рассылка в ЛС", callback_data="broadcast_pm"
                ),
                InlineKeyboardButton(
                    text="💬 Рассылка в Группы", callback_data="broadcast_chats"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Общая Статистика", callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В Главное Меню", callback_data="back_to_start"
                )
            ],
        ]
    )


def get_mod_menu_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Настройка правил за пересылки",
                    callback_data="config_forwards",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Настройка Варнов (Лимит: 4)",
                    callback_data="config_warns",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Забанить по ID", callback_data="mod_act_ban"
                ),
                InlineKeyboardButton(
                    text="🟢 Разбанить по ID", callback_data="mod_act_unban"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Выдать Варн по ID", callback_data="mod_act_warn"
                ),
                InlineKeyboardButton(
                    text="🧹 Снять 1 Варн по ID", callback_data="mod_act_unwarn_one"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Снять N Варнов по ID", callback_data="mod_act_unwarn_num"
                ),
                InlineKeyboardButton(
                    text="✨ Снять Все Варны по ID", callback_data="mod_act_unwarn_all"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔇 Замутить по ID", callback_data="mod_act_mute"
                ),
                InlineKeyboardButton(
                    text="🔊 Размутить по ID", callback_data="mod_act_unmute"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ Очистить инвентарь юзера", callback_data="mod_act_clear_inv"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в Админку", callback_data="admin_back"
                )
            ],
        ]
    )


# ==================== ВСПОМОГАТЕЛЬНЫЕ НАКАЗАНИЯ ====================
def _apply_warn_sync(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE users SET warnings = warnings + 1 WHERE user_id = ?", (user_id,)
        )
        db.commit()
        cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 1


def _reset_warns_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("UPDATE users SET warnings = 0 WHERE user_id = ?", (user_id,))
        db.commit()


def _set_banned_sync(user_id: int, banned: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (banned, user_id))
        db.commit()


async def apply_warn(user_id: int, chat_id: Optional[int] = None) -> str:
    warns = await asyncio.to_thread(_apply_warn_sync, user_id)
    limit = int(await get_setting("warn_limit", "4"))

    if warns >= limit:
        action = await get_setting("warn_punishment", "mute")
        mute_mins = int(await get_setting("warn_mute_time", "1440"))

        await asyncio.to_thread(_reset_warns_sync, user_id)

        if action == "ban":
            await asyncio.to_thread(_set_banned_sync, user_id, 1)
            if chat_id:
                try:
                    await bot.ban_chat_member(chat_id, user_id)
                except Exception:
                    pass
            return f"⚠️ Достигнут лимит варнов ({limit}/{limit})! Пользователь заблокирован."
        else:
            if chat_id:
                try:
                    until = datetime.datetime.now() + datetime.timedelta(
                        minutes=mute_mins
                    )
                    await bot.restrict_chat_member(
                        chat_id,
                        user_id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=until,
                    )
                except Exception:
                    pass
            return f"⚠️ Достигнут лимит варнов ({limit}/{limit})! Пользователь замучен на {mute_mins} мин."

    return f"⚠️ Выдан варн ({warns}/{limit})."


# ==================== ХЭНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ====================
@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type == "private":
        if await is_user_banned(message.from_user.id):
            await message.answer("❌ Вы заблокированы в боте.")
            return

        await register_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )
        await message.answer(
            "👋 <b>Добро пожаловать в GiftEz Ludo Bot!</b>\n\n"
            "🎰 Крутите слот 🎰 в нашей группе. Если выпадает <b>777</b>, вы получаете подарок из Звёзд!\n"
            "Все выигрыши сохраняются в вашем инвентаре.",
            parse_mode="HTML",
            reply_markup=get_bot_start_kb(message.from_user.id),
        )


@router.callback_query(F.data == "back_to_start")
async def process_back_to_start(callback: CallbackQuery):
    if await is_user_banned(callback.from_user.id):
        return
    try:
        await callback.message.edit_text(
            "👋 <b>Главное меню GiftEz Ludo Bot</b>\n\n"
            "🎰 Крутите слот 🎰 в нашей группе. Если выпадает <b>777</b>, вы получаете подарок из Звёзд!\n"
            "Все выигрыши сохраняются в вашем инвентаре.",
            parse_mode="HTML",
            reply_markup=get_bot_start_kb(callback.from_user.id),
        )
    except TelegramBadRequest:
        pass


@router.message(Command("inventory"))
async def cmd_inventory(message: Message):
    if message.chat.type == "private":
        if await is_user_banned(message.from_user.id):
            await message.answer("❌ Вы заблокированы в боте.")
            return
        await show_inventory(message.from_user.id, message)


async def show_inventory(user_id: int, message_or_call):
    items = await get_user_items(user_id)
    available_items = [i for i in items if i[4] == "available"]

    if not available_items:
        text = "🎒 <b>Ваш инвентарь пуст.</b>\n\nКрутите 🎰 в группе, чтобы выиграть призы!"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Обновить", callback_data="open_inventory"
                    ),
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="back_to_start"
                    ),
                ]
            ]
        )
    else:
        text = "🎒 <b>Ваш инвентарь доступных призов:</b>\n\n"
        for item_id, gift_key, stars, payout, status in available_items:
            title = GIFTS_CONFIG.get(gift_key, {}).get("title", "🎁")
            text += f"• {title}: Номинал <b>{stars} ⭐</b> (Выплата: <b>{payout} ⭐</b>)\n"

        text += (
            "\nℹ️ <i>Примечание: Вывод может занять до месяца в зависимости от загруженности "
            "и наличия звёзд у администратора, но обычно обработка занимает от 7 до 10 дней.</i>"
        )
        kb = get_inventory_kb(available_items)

    if isinstance(message_or_call, CallbackQuery):
        try:
            await message_or_call.message.edit_text(
                text, parse_mode="HTML", reply_markup=kb
            )
        except TelegramBadRequest:
            pass
    else:
        await message_or_call.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "open_inventory")
async def process_open_inventory(callback: CallbackQuery):
    if await is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы заблокированы.", show_alert=True)
        return

    await show_inventory(callback.from_user.id, callback)
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "clear_my_inventory")
async def process_clear_my_inventory(callback: CallbackQuery):
    if await is_user_banned(callback.from_user.id):
        return
    await clear_user_inventory(callback.from_user.id)
    await callback.answer("🗑️ Ваш инвентарь успешно очищен!", show_alert=True)
    await show_inventory(callback.from_user.id, callback)


def _get_profile_data_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "SELECT spins_count, warnings, is_banned FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        spins = row[0] if row else 0
        warns = row[1] if row else 0

        cursor.execute(
            "SELECT COUNT(*) FROM inventory WHERE user_id = ?", (user_id,)
        )
        inv_cnt = cursor.fetchone()[0]
        return spins, warns, inv_cnt


@router.callback_query(F.data == "open_profile")
async def process_open_profile(callback: CallbackQuery):
    if await is_user_banned(callback.from_user.id):
        return

    user_id = callback.from_user.id
    spins, warns, inv_cnt = await asyncio.to_thread(_get_profile_data_sync, user_id)

    limit = await get_setting("warn_limit", "4")
    username = f"@{callback.from_user.username}" if callback.from_user.username else "Отсутствует"

    text = (
        f"👤 <b>Ваш Профиль:</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Юзернейм: {username}\n"
        f"⚠️ Предупреждения: <b>{warns}/{limit}</b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎒 Мой Инвентарь", callback_data="open_inventory"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="back_to_start"
                )
            ],
        ]
    )

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("claim_win_"))
async def process_claim_win(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[2])
    if callback.from_user.id != target_user_id:
        await callback.answer(
            "❌ Эта кнопка не для вас! Выбить приз может только тот, кому повезло.",
            show_alert=True,
        )
        return

    await callback.answer(
        "📥 Приз отправлен в ваш инвентарь! Перейдите в ЛС бота, чтобы вывести его.",
        show_alert=True,
    )


# ==================== ОБРАБОТКА ИГРЫ В ГРУППАХ ====================
@router.message(F.dice & (F.dice.emoji == "🎰"))
async def handle_dice(message: Message):
    if await is_user_banned(message.from_user.id):
        return

    # Защита от пересылки сообщений
    if message.forward_origin is not None:
        try:
            await message.delete()
        except Exception:
            pass

        punishment = await get_setting("forward_punishment", "warn")
        mute_time = int(await get_setting("forward_mute_time", "60"))

        if punishment == "ban":
            await asyncio.to_thread(_set_banned_sync, message.from_user.id, 1)
            try:
                await message.chat.ban_member(message.from_user.id)
            except Exception:
                pass
            await message.answer(
                f"⛔ {message.from_user.mention_html()} забанен за пересылку сообщений!",
                parse_mode="HTML",
            )
        elif punishment == "mute":
            until = datetime.datetime.now() + datetime.timedelta(minutes=mute_time)
            try:
                await message.chat.restrict(
                    message.from_user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
            except Exception:
                pass
            await message.answer(
                f"🔇 {message.from_user.mention_html()} замучен на {mute_time} мин. за пересылку сообщений!",
                parse_mode="HTML",
            )
        else:
            res = await apply_warn(message.from_user.id, message.chat.id)
            await message.answer(
                f"⚠️ {message.from_user.mention_html()}, пересылка запрещена! {res}",
                parse_mode="HTML",
            )
        return

    if message.chat.type in ["group", "supergroup"]:
        await register_chat(message.chat.id, message.chat.title or "Группа")

    await register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )

    spins_cnt = await increment_user_spins(message.from_user.id)
    spin_price = await get_spin_price()
    spent_stars = spins_cnt * spin_price

    if message.dice.value == 64:
        gift_key = select_gift_by_spins(spent_stars)
        gift = GIFTS_CONFIG[gift_key]

        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else message.from_user.full_name
        )

        item_id = await add_item_to_inventory(
            message.from_user.id, username, gift_key
        )
        await reset_user_spins(message.from_user.id)

        caption_text = (
            f"🎉 <b>ДЖЕКПОТ 777!</b>\n\n"
            f"🏆 Победитель: {username}\n"
            f"🎁 Выигрыш: <b>{gift['title']}</b> (Номинал: <b>{gift['stars']} ⭐</b>)\n"
            f"💳 К выплате: <b>{gift['payout']} ⭐</b>\n\n"
            f"Приз отправлен в ваш инвентарь! Перейдите в ЛС бота, чтобы забрать его."
        )

        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📥 Забрать приз в ЛС",
                        callback_data=f"claim_win_{message.from_user.id}",
                    )
                ]
            ]
        )

        img_path = gift.get("image", "")
        if os.path.exists(img_path):
            photo_file = FSInputFile(img_path)
            await message.reply_photo(
                photo=photo_file,
                caption=caption_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            await message.reply(
                caption_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

        try:
            await bot.send_message(
                message.from_user.id,
                f"🎉 <b>ПОЗДРАВЛЯЕМ С ВЫИГРЫШЕМ!</b>\n\n"
                f"Вы выбили 777 и получили: <b>{gift['title']}</b>!\n"
                f"• Номинал: <b>{gift['stars']} ⭐</b>\n"
                f"• К выплате: <b>{gift['payout']} ⭐</b>\n\n"
                f"ℹ️ <i>Обратите внимание: Вывод средств обычно занимает от 7 до 10 дней, "
                f"но в связи с возможностью загруженности администратора или очереди выплат процесс может достигать до 1 месяца.</i>",
                parse_mode="HTML",
                reply_markup=get_instant_withdraw_kb(item_id, gift["title"]),
            )
        except Exception:
            pass


# ==================== ВЫВОД ПРИЗОВ ====================
@router.callback_query(F.data.startswith("user_withdraw_"))
async def process_user_withdraw(callback: CallbackQuery):
    if await is_user_banned(callback.from_user.id):
        await callback.answer("❌ Вы заблокированы.", show_alert=True)
        return

    item_id = int(callback.data.split("_")[2])
    item = await get_item_by_id(item_id)

    if not item or item[6] != "available":
        await callback.answer(
            "❌ Этот приз недоступен или уже обрабатывается.",
            show_alert=True,
        )
        return

    await update_item_status(item_id, "pending")
    gift_key = item[3]
    stars = item[4]
    payout = item[5]
    username = item[2]
    gift_title = GIFTS_CONFIG.get(gift_key, {}).get("title", "🎁 Приз")

    try:
        await callback.message.edit_text(
            f"⏳ <b>Заявка на вывод принята!</b>\n\n"
            f"🎁 Приз: <b>{gift_title}</b> ({stars} ⭐)\n"
            f"💳 Сумма к получению: <b>{payout} ⭐</b>\n\n"
            f"ℹ️ <i>Заявка передана администратору. Обычный срок вывода составляет 7–10 дней "
            f"(в зависимости от загруженности и наличия звёзд — до 1 месяца).</i>",
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    await bot.send_message(
        ADMIN_ID,
        f"🚨 <b>НОВАЯ ЗАЯВКА НА ВЫВОД!</b>\n\n"
        f"👤 Пользователь: {username} (ID: <code>{item[1]}</code>)\n"
        f"🎁 Подарок: <b>{gift_title}</b>\n"
        f"⭐ Номинал: <b>{stars} ⭐</b>\n"
        f"💳 К выплате: <b>{payout} ⭐</b>",
        parse_mode="HTML",
        reply_markup=get_admin_approval_kb(item_id),
    )


# ==================== ИМИТАЦИЯ И ТЕСТ ЛУДКИ ====================
@router.message(Command("testludka"))
async def cmd_testludka(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    sim_count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 100

    spin_price = await get_spin_price()

    # Имитация реального демо-прокрута в чате
    spins_before_win = random.randint(1, 40)
    spent_stars = spins_before_win * spin_price
    gift_key = select_gift_by_spins(spent_stars)
    gift = GIFTS_CONFIG[gift_key]

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.full_name
    )

    # Зачисление подарка в инвентарь админа
    item_id = await add_item_to_inventory(
        message.from_user.id, username, gift_key
    )

    demo_chat_text = (
        f"🎰 <b>[ДЕМО ПРОКРУТ ИМИТАЦИЯ] 777!</b>\n\n"
        f"🏆 Игрок: {username}\n"
        f"🎁 Выбит приз: <b>{gift['title']}</b> (Номинал: <b>{gift['stars']} ⭐</b>)\n"
        f"💳 К выплате: <b>{gift['payout']} ⭐</b>\n\n"
        f"Демо-подарок отправлен в ваш инвентарь!"
    )

    reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Забрать приз в ЛС",
                    callback_data=f"claim_win_{message.from_user.id}",
                )
            ]
        ]
    )

    await message.answer(demo_chat_text, parse_mode="HTML", reply_markup=reply_markup)

    # Проведение массовой симуляции для отчета
    total_jackpots = 0
    total_spins = 0
    gift_counts = {k: 0 for k in GIFTS_CONFIG.keys()}

    for _ in range(sim_count):
        spins = 0
        while True:
            spins += 1
            total_spins += 1
            if random.randint(1, 64) == 64:
                total_jackpots += 1
                stars_spent = spins * spin_price
                g_key = select_gift_by_spins(stars_spent)
                gift_counts[g_key] += 1
                break

    report = (
        f"📊 <b>ОТЧЁТ ТЕСТОВОЙ ЛУДКИ ({sim_count} побед)</b>\n\n"
        f"🎲 Всего прокрутов сделано: <b>{total_spins}</b>\n"
        f"🎰 Реализовано побед (777): <b>{total_jackpots}</b>\n"
        f"💎 Среднее число спинов до победы: <b>{round(total_spins / sim_count, 2)}</b>\n"
        f"⭐ Средние затраты игрока до джекпота: <b>{round((total_spins / sim_count) * spin_price, 2)} ⭐</b>\n\n"
        f"<b>🎁 Распределение выпавших призов:</b>\n"
    )

    for k, v in gift_counts.items():
        pct = round((v / sim_count) * 100, 2)
        title = GIFTS_CONFIG[k]["title"]
        report += f"• {title}: <b>{v} шт.</b> ({pct}%)\n"

    # ОТПРАВКА ОТЧЕТА И ВЫДАЧА ДЕМО-ПОДАРКА СТРОГО В ЛС АДМИНУ
    try:
        await bot.send_message(
            message.from_user.id,
            f"🎁 <b>ВАШ ДЕМО ПОДАРОК ВЫДАН В ИНВЕНТАРЬ!</b>\n\n"
            f"Вы выбили: <b>{gift['title']}</b> ({gift['stars']} ⭐)\n"
            f"Вы можете проверить его через /inventory или кнопкой ниже:\n\n"
            f"{report}",
            parse_mode="HTML",
            reply_markup=get_instant_withdraw_kb(item_id, gift["title"]),
        )
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить демо-отчет в ЛС. Начните диалог с ботом в ЛС: {e}")


# ==================== УПРАВЛЕНИЕ ЗАЯВКАМИ НА ВЫВОД B АДМИНКЕ ====================
def _get_admin_withdrawals_sync(limit: int, offset: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE status='pending'")
        total_pending = cursor.fetchone()[0]

        cursor.execute(
            "SELECT id, user_id, username, gift_key, stars, payout FROM inventory WHERE status='pending' LIMIT ? OFFSET ?",
            (limit, offset),
        )
        items = cursor.fetchall()
        return total_pending, items


@router.callback_query(F.data.startswith("admin_withdrawals_"))
async def process_admin_withdrawals_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    page = int(callback.data.split("_")[2])
    limit = 5
    offset = page * limit

    total_pending, items = await asyncio.to_thread(_get_admin_withdrawals_sync, limit, offset)

    if not items:
        text = "📥 <b>Активных заявок на вывод нет.</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад в Админку", callback_data="admin_back"
                    )
                ]
            ]
        )
    else:
        text = f"📥 <b>Заявки на вывод (Страница {page + 1}):</b>\n\nВыберите заявку из списка ниже для просмотра и управления:"
        keyboard = []
        for item_id, u_id, u_name, g_key, stars, payout in items:
            g_title = GIFTS_CONFIG.get(g_key, {}).get("title", "🎁")
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"Заявка #{item_id} | {u_name} | {g_title} ({payout} ⭐)",
                        callback_data=f"view_withdrawal_{item_id}",
                    )
                ]
            )

        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=f"admin_withdrawals_{page - 1}"
                )
            )
        if offset + limit < total_pending:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед ➡️", callback_data=f"admin_withdrawals_{page + 1}"
                )
            )

        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append(
            [InlineKeyboardButton(text="⬅️ Назад в Админку", callback_data="admin_back")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("view_withdrawal_"))
async def process_view_withdrawal(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    item_id = int(callback.data.split("_")[2])
    item = await get_item_by_id(item_id)

    if not item:
        await callback.answer("Заявка не найдена или обработана.", show_alert=True)
        return

    gift_title = GIFTS_CONFIG.get(item[3], {}).get("title", "🎁")

    text = (
        f"📄 <b>ЗАЯВКА НА ВЫВОД #{item[0]}</b>\n\n"
        f"👤 Пользователь: {item[2]} (ID: <code>{item[1]}</code>)\n"
        f"🎁 Подарок: <b>{gift_title}</b>\n"
        f"⭐ Номинал: <b>{item[4]} ⭐</b>\n"
        f"💳 К выплате: <b>{item[5]} ⭐</b>\n"
        f"📌 Статус: <b>Ожидает обработки</b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Выдано", callback_data=f"admin_approve_{item_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отказать", callback_data=f"admin_reject_{item_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку заявок", callback_data="admin_withdrawals_0"
                )
            ],
        ]
    )

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


# ==================== АДМИН-КОМАНДЫ МОДЕРАЦИИ В ЧАТАХ ====================
async def get_target_user_id(message: Message) -> Optional[int]:
    if message.reply_to_message:
        return message.reply_to_message.from_user.id
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        return int(args[1])
    return None


@router.message(Command("warn"))
async def cmd_warn(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = await get_target_user_id(message)
    if not target_id:
        await message.reply(
            "❌ Ответьте на сообщение пользователя или введите его ID: `/warn ID`",
            parse_mode="Markdown",
        )
        return

    res = await apply_warn(target_id, message.chat.id)
    await message.reply(
        f"👤 Пользователю <code>{target_id}</code> выдан варн.\n{res}",
        parse_mode="HTML",
    )


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = await get_target_user_id(message)
    if not target_id:
        await message.reply(
            "❌ Ответьте на сообщение или введите ID: `/mute ID [минуты]`",
            parse_mode="Markdown",
        )
        return

    args = message.text.split()
    mins = int(args[-1]) if len(args) > 1 and args[-1].isdigit() else 60
    until = datetime.datetime.now() + datetime.timedelta(minutes=mins)

    try:
        await message.chat.restrict(
            target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        await message.reply(
            f"🔇 Пользователь <code>{target_id}</code> замучен на {mins} минут.",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка выполнения мута: {e}")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = await get_target_user_id(message)
    if not target_id:
        await message.reply(
            "❌ Ответьте на сообщение или введите ID: `/unmute ID`",
            parse_mode="Markdown",
        )
        return

    try:
        await message.chat.restrict(
            target_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await message.reply(
            f"🔊 Пользователь <code>{target_id}</code> размучен.",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка при снятии мута: {e}")


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = await get_target_user_id(message)
    if not target_id:
        await message.reply(
            "❌ Ответьте на сообщение или введите ID: `/ban ID`", parse_mode="Markdown"
        )
        return

    await asyncio.to_thread(_set_banned_sync, target_id, 1)

    try:
        await message.chat.ban_member(target_id)
    except Exception:
        pass

    await message.reply(
        f"🚫 Пользователь <code>{target_id}</code> забанен везде.", parse_mode="HTML"
    )


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    target_id = await get_target_user_id(message)
    if not target_id:
        await message.reply(
            "❌ Ответьте на сообщение или введите ID: `/unban ID`",
            parse_mode="Markdown",
        )
        return

    await asyncio.to_thread(_set_banned_sync, target_id, 0)

    try:
        await message.chat.unban_member(target_id)
    except Exception:
        pass

    await message.reply(
        f"🟢 Пользователь <code>{target_id}</code> разбанен.", parse_mode="HTML"
    )


# ==================== СПИСОК ПОЛЬЗОВАТЕЛЕЙ (ПАГИНАЦИЯ) ====================
def _get_users_list_sync(limit: int, offset: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute(
            "SELECT user_id, username, full_name, spins_count, warnings, is_banned FROM users LIMIT ? OFFSET ?",
            (limit, offset),
        )
        users = cursor.fetchall()
        return total_users, users


@router.callback_query(F.data.startswith("users_list_"))
async def process_users_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    page = int(callback.data.split("_")[2])
    limit = 5
    offset = page * limit

    total_users, users = await asyncio.to_thread(_get_users_list_sync, limit, offset)

    if not users:
        text = "👥 <b>Список пользователей пуст.</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад в Админку", callback_data="admin_back"
                    )
                ]
            ]
        )
    else:
        text = f"👥 <b>Список пользователей (Страница {page + 1}):</b>\n\n"
        for uid, uname, fname, spins, warns, banned in users:
            u_str = f"@{uname}" if uname else fname
            status = "🔴 ЗАБАНЕН" if banned else "🟢 Активен"
            text += (
                f"👤 <b>{u_str}</b> (ID: <code>{uid}</code>)\n"
                f"├ Статус: {status}\n"
                f"├ Варны: <b>{warns}</b> | Невыигрышных спинов: <b>{spins}</b>\n\n"
            )

        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=f"users_list_{page - 1}"
                )
            )
        if offset + limit < total_users:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед ➡️", callback_data=f"users_list_{page + 1}"
                )
            )

        kb_rows = []
        if nav_buttons:
            kb_rows.append(nav_buttons)
        kb_rows.append(
            [InlineKeyboardButton(text="⬅️ Назад в Админку", callback_data="admin_back")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


# ==================== АДМИН-ПАНЕЛЬ И МОДЕРАЦИЯ ====================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id == ADMIN_ID:
        spin_price = await get_spin_price()
        kb = await get_admin_panel_kb(spin_price)
        await message.answer(
            "⚙️ <b>Панель Администратора</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )


@router.callback_query(F.data == "admin_main_menu")
async def process_admin_main_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    spin_price = await get_spin_price()
    kb = await get_admin_panel_kb(spin_price)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Панель Администратора</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin_back")
async def process_admin_back(callback: CallbackQuery):
    await process_admin_main_menu(callback)


@router.callback_query(F.data == "change_spin_price")
async def process_change_spin_price(
    callback: CallbackQuery, state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_spin_price)
    await callback.message.reply(
        "✏️ Введите новую стоимость 1 прокрута в звёздах (целое число):"
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_spin_price)
async def process_set_spin_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Пожалуйста, введите положительное число.")
        return

    price = int(message.text)
    await set_spin_price(price)
    await message.answer(
        f"✅ Стоимость 1 прокрута успешно изменена на <b>{price} ⭐</b>!",
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "admin_mod_menu")
async def process_mod_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        await callback.message.edit_text(
            "🛡️ <b>Панель Модерации и Наказаний</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=get_mod_menu_kb(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "config_forwards")
async def process_config_forwards(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    p = await get_setting("forward_punishment", "warn")
    m = await get_setting("forward_mute_time", "60")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Наказание: {p.upper()}",
                    callback_data="toggle_forward_punish",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Время Мута: {m} мин",
                    callback_data="change_forward_mute_time",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="admin_mod_menu"
                )
            ],
        ]
    )

    try:
        await callback.message.edit_text(
            "⚙️ <b>Настройки защиты от пересылок сообщений:</b>\n\n"
            "Выберите тип наказания и длительность ограничения при спаме пересылками.",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "toggle_forward_punish")
async def process_toggle_forward_punish(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    curr = await get_setting("forward_punishment", "warn")
    next_p = {"warn": "mute", "mute": "ban", "ban": "warn"}[curr]
    await set_setting("forward_punishment", next_p)
    await process_config_forwards(callback)


@router.callback_query(F.data == "change_forward_mute_time")
async def process_change_forward_mute_time(
    callback: CallbackQuery, state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_mute_time)
    await callback.message.reply("⏱️ Введите время мута за пересылки в минутах:")
    await callback.answer()


@router.message(AdminStates.waiting_for_mute_time)
async def process_save_forward_mute_time(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.text.isdigit():
        await message.answer("❌ Введите число!")
        return
    await set_setting("forward_mute_time", message.text)
    await message.answer(
        f"✅ Время мута за пересылки установлено: <b>{message.text} мин.</b>",
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "config_warns")
async def process_config_warns(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    limit = await get_setting("warn_limit", "4")
    punish = await get_setting("warn_punishment", "mute")
    mute_time = await get_setting("warn_mute_time", "1440")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Наказание: {punish.upper()}",
                    callback_data="toggle_warn_punish",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Время мута: {mute_time} мин",
                    callback_data="change_warn_mute_time",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="admin_mod_menu"
                )
            ],
        ]
    )

    try:
        await callback.message.edit_text(
            f"⚠️ <b>Настройки системы варнов</b>\n\n"
            f"• Лимит варнов: <b>{limit}</b> (фиксировано)\n"
            f"• Действие при достижении {limit} варнов: <b>{punish.upper()}</b>\n"
            f"• Время мута (если выбрано MUTE): <b>{mute_time} мин.</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "toggle_warn_punish")
async def process_toggle_warn_punish(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    curr = await get_setting("warn_punishment", "mute")
    next_p = "ban" if curr == "mute" else "mute"
    await set_setting("warn_punishment", next_p)
    await process_config_warns(callback)


@router.callback_query(F.data == "change_warn_mute_time")
async def process_change_warn_mute_time(
    callback: CallbackQuery, state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_mute_time)
    await callback.message.reply(
        "⏱️ Введите время мута при 4 варнах в минутах (например 1440 = 24ч):"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mod_act_"))
async def process_mod_action_select(
    callback: CallbackQuery, state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return

    action = callback.data.replace("mod_act_", "")
    await state.update_data(mod_action=action)

    if action == "unwarn_num":
        await state.set_state(AdminStates.waiting_for_mod_user_id)
        await callback.message.reply("👤 Введите ID пользователя для снятия некоторого кол-ва варнов:")
    else:
        await state.set_state(AdminStates.waiting_for_mod_user_id)
        action_names = {
            "ban": "блокировки",
            "unban": "разблокировки",
            "warn": "выдачи варна",
            "unwarn_one": "снятия 1 варна",
            "unwarn_all": "снятия ВСЕХ варнов",
            "mute": "мута",
            "unmute": "снятия мута",
            "clear_inv": "очистки инвентаря",
        }
        await callback.message.reply(
            f"👤 Введите Telegram ID пользователя для {action_names.get(action, 'операции')}:"
        )
    await callback.answer()


def _unwarn_user_sync(user_id: int, amount: int = 1):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE users SET warnings = MAX(0, warnings - ?) WHERE user_id = ?",
            (amount, user_id),
        )
        db.commit()


def _clear_warns_sync(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("UPDATE users SET warnings = 0 WHERE user_id = ?", (user_id,))
        db.commit()


@router.message(AdminStates.waiting_for_mod_user_id)
async def process_execute_mod_action(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if not message.text.isdigit():
        await message.answer("❌ Введите корректный числовой ID.")
        return

    target_id = int(message.text)
    data = await state.get_data()
    action = data.get("mod_action")

    if action == "ban":
        await asyncio.to_thread(_set_banned_sync, target_id, 1)
        await message.answer(f"🚫 Пользователь <code>{target_id}</code> забанен.")
        await state.clear()
    elif action == "unban":
        await asyncio.to_thread(_set_banned_sync, target_id, 0)
        await message.answer(f"🟢 Пользователь <code>{target_id}</code> разбанен.")
        await state.clear()
    elif action == "warn":
        res = await apply_warn(target_id)
        await message.answer(
            f"⚠️ Выдан варн пользователю <code>{target_id}</code>. {res}"
        )
        await state.clear()
    elif action == "unwarn_one":
        await asyncio.to_thread(_unwarn_user_sync, target_id, 1)
        await message.answer(f"🧹 Снят 1 варн с пользователя <code>{target_id}</code>.")
        await state.clear()
    elif action == "unwarn_all":
        await asyncio.to_thread(_clear_warns_sync, target_id)
        await message.answer(f"✨ Сняты ВСЕ варны с пользователя <code>{target_id}</code>.")
        await state.clear()
    elif action == "unwarn_num":
        await state.update_data(target_id=target_id)
        await state.set_state(AdminStates.waiting_for_unwarn_amount)
        await message.answer("🔢 Введите количество варнов, которое нужно снять:")
        return
    elif action == "mute":
        await message.answer(
            f"🔇 Воспользуйтесь командой <code>/mute {target_id} [минуты]</code> прямо в чате."
        )
        await state.clear()
    elif action == "unmute":
        await message.answer(
            f"🔊 Воспользуйтесь командой <code>/unmute {target_id}</code> прямо в чате."
        )
        await state.clear()
    elif action == "clear_inv":
        await clear_user_inventory(target_id)
        await message.answer(f"🗑️ Инвентарь пользователя <code>{target_id}</code> полностью очищен.")
        await state.clear()


@router.message(AdminStates.waiting_for_unwarn_amount)
async def process_execute_unwarn_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if not message.text.isdigit():
        await message.answer("❌ Введите число.")
        return

    amount = int(message.text)
    data = await state.get_data()
    target_id = data.get("target_id")

    await asyncio.to_thread(_unwarn_user_sync, target_id, amount)

    await message.answer(
        f"🧹 Снято <b>{amount}</b> варнов у пользователя <code>{target_id}</code>.",
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin_approve_"))
async def process_admin_approve(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    item_id = int(callback.data.split("_")[2])
    item = await get_item_by_id(item_id)

    if not item:
        await callback.answer("Предмет не найден.")
        return

    await update_item_status(item_id, "completed")
    gift_title = GIFTS_CONFIG.get(item[3], {}).get("title", "🎁")

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n✅ <b>СТАТУС: ВЫДАНО</b>",
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    try:
        await bot.send_message(
            item[1],
            f"✅ <b>Ваша заявка была выполнена!</b>\n\n"
            f"Подарок <b>{gift_title}</b> ({item[4]} ⭐) отправлен.\n"
            f"Спасибо, что вы с нами!",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_reject_"))
async def process_admin_reject(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    item_id = int(callback.data.split("_")[2])
    await state.update_data(item_id=item_id)
    await state.set_state(AdminStates.waiting_for_reject_reason)

    await callback.message.reply("📝 Введите причину отказа:")
    await callback.answer()


@router.message(AdminStates.waiting_for_reject_reason)
async def process_reject_reason(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    item_id = data["item_id"]
    reason = message.text

    item = await get_item_by_id(item_id)
    if not item:
        await message.answer("Предмет не найден.")
        await state.clear()
        return

    gift_title = GIFTS_CONFIG.get(item[3], {}).get("title", "🎁")

    try:
        await bot.send_message(
            item[1],
            f"❌ <b>Заявка на вывод отклонена</b>\n\n"
            f"Приз: <b>{gift_title}</b> ({item[4]} ⭐)\n"
            f"Причина: <i>{reason}</i>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await message.answer(
        f"Отказ зафиксирован. Что сделать с предметом <b>{gift_title}</b> в инвентаре?",
        parse_mode="HTML",
        reply_markup=get_reject_decision_kb(item_id),
    )
    await state.clear()


@router.callback_query(F.data.startswith("keep_item_"))
async def process_keep_item(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    item_id = int(callback.data.split("_")[2])
    await update_item_status(item_id, "available")
    try:
        await callback.message.edit_text(
            "💾 Предмет сохранён в инвентаре пользователя для повторного вывода."
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("delete_item_"))
async def process_delete_item(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    item_id = int(callback.data.split("_")[2])
    await delete_item(item_id)
    try:
        await callback.message.edit_text(
            "🗑️ Предмет окончательно удалён из инвентаря."
        )
    except TelegramBadRequest:
        pass


def _get_admin_stats_sync():
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        users_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM chats")
        chats_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM inventory")
        total_wins = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE status='pending'")
        pending_cnt = cursor.fetchone()[0]
        return users_cnt, chats_cnt, total_wins, pending_cnt


@router.callback_query(F.data == "admin_stats")
async def process_admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    users_cnt, chats_cnt, total_wins, pending_cnt = await asyncio.to_thread(_get_admin_stats_sync)

    spin_price = await get_spin_price()
    kb = await get_admin_panel_kb(spin_price)
    try:
        await callback.message.edit_text(
            f"📊 <b>Статистика Бота</b>\n\n"
            f"• Пользователей: <b>{users_cnt}</b>\n"
            f"• Подключённых Групп: <b>{chats_cnt}</b>\n"
            f"• Всего выигрышей: <b>{total_wins}</b>\n"
            f"• Ожидают вывода: <b>{pending_cnt}</b>\n"
            f"• Стоимость прокрута: <b>{spin_price} ⭐</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.in_(["broadcast_pm", "broadcast_chats"]))
async def process_broadcast_start(
    callback: CallbackQuery, state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return

    target = "pm" if callback.data == "broadcast_pm" else "chats"
    await state.update_data(target=target)
    await state.set_state(AdminStates.waiting_for_broadcast_message)

    target_name = (
        "в Личные Сообщения пользователям"
        if target == "pm"
        else "в Группы/Чаты"
    )
    await callback.message.reply(
        f"📢 Отправьте сообщение для рассылки {target_name}:"
    )
    await callback.answer()


def _get_broadcast_targets_sync(target: str):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.cursor()
        if target == "pm":
            cursor.execute("SELECT user_id FROM users")
        else:
            cursor.execute("SELECT chat_id FROM chats")
        return [row[0] for row in cursor.fetchall()]


@router.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    target = data["target"]

    targets = await asyncio.to_thread(_get_broadcast_targets_sync, target)

    success, failed = 0, 0
    for tid in targets:
        try:
            await message.copy_to(chat_id=tid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\nУспешно: <b>{success}</b>\nОшибок: <b>{failed}</b>",
        parse_mode="HTML",
    )
    await state.clear()


# ==================== ЗАПУСК ====================
async def main():
    await init_db()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
