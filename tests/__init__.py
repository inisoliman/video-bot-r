"""Test package bootstrap.

The suite must stay fully offline even when production credentials are present in
the environment. Importing this package sets ``TELEGRAM_HTTP_BLOCKED=1`` before any
production module is imported; ``tests.support.block_telegram_http`` then makes the
telebot HTTP layer raise should anything try a real network call.
"""

import os

os.environ["TELEGRAM_HTTP_BLOCKED"] = "1"
