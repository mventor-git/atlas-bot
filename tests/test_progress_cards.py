"""A2: progress-card helper + quote Threading smoke test (ponytail: one file)."""
import asyncio

from app.bot.keyboards import send_step, finish_step, cap_buttons
from telegram import InlineKeyboardButton


class FakeMsg:
    message_id = 7
    def __init__(self):
        self.kwargs = None
        self.edits = []
    async def reply_text(self, text, **kw):
        self.kwargs = kw
        self.text = text
        return self
    async def edit_text(self, text, **kw):
        self.edits.append(text)


def test_send_step_quotes_and_timestamps():
    m = FakeMsg()
    card = asyncio.run(send_step(m, "Building preview PDF", quote_id=7))
    assert card.text.startswith("\u23f3 Building preview PDF\u2026")
    assert card.kwargs.get("reply_to_message_id") == 7
    asyncio.run(finish_step(card, "Preview ready"))
    assert card.edits and card.edits[0].startswith("\u2705 Preview ready")


def test_touched_keyboards_max_3():
    btns = [InlineKeyboardButton(f"B{i}", callback_data=f"b{i}") for i in range(5)]
    kb = cap_buttons(btns, 3)
    assert len(kb.inline_keyboard) == 3
