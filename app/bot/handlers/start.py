from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards import main_menu

router = Router(name="start")

WELCOME = (
    "👋 <b>Привет! Я Gift Track Bot.</b>\n\n"
    "Слежу за маркетплейсом улучшенных Telegram-подарков и пишу, когда появляется предложение, "
    "подходящее под ваши фильтры.\n\n"
    "<b>Что я умею:</b>\n"
    "🎁 показать свежие предложения по вашим фильтрам\n"
    "⚙️ фильтры: коллекция, модель, фон, символ, цена, редкость\n"
    "🔔 уведомления о новых подходящих лотах без дублей\n"
    "📊 статистика\n\n"
    "<b>С чего начать:</b> откройте «⚙️ Настройки фильтров» и создайте фильтр.\n\n"
    "ℹ️ Бот проверяет рынок периодически, а не мгновенно, и сделок не проводит — "
    "покупка выполняется в самом Telegram по ссылке из уведомления."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())
