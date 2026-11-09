"""Lets a run be steered from somewhere other than the terminal (see telegram_bot.py).

When `active` is set, the places that normally call input() ask it instead. When it's None,
which is every normal terminal or scheduled run, they behave exactly as they always have.
"""
from __future__ import annotations

active = None  # the running TelegramBot, while it is driving a job


class Cancelled(BaseException):
    """You sent /cancel. A BaseException so "one bad story shouldn't stop the others" handlers
    let it through."""
