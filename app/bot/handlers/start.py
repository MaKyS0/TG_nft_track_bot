from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards import main_menu
from app.config import Settings

router = Router(name="start")

WELCOME = (
    "👋 <b>Привет! Я NFT Tracker.</b>\n\n"
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
WEBAPP_HINT = "\n\n📱 Всё то же самое доступно в удобном мини-приложении: кнопка «Мини-приложение» ниже."


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    text = WELCOME + (WEBAPP_HINT if settings.webapp_url else "")
    await message.answer(text, reply_markup=main_menu(settings.webapp_url))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu(settings.webapp_url))
