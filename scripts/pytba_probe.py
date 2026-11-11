"""Evidence probe used by the 4.33.0 -> 4.37.1 upgrade audit.

Run with the pyTelegramBotAPI interpreter BEFORE and AFTER the upgrade; send the
output to docs/pytba-upgrade-audit.md. Read-only: it builds and serializes types
only, never calls the Telegram API.

    python scripts/pytba_probe.py
"""

import inspect
import json

import telebot
import telebot.types as T
import telebot.version as telebot_version


def line(label, value):
    print(f"{label}: {value}")


def main():
    line("library_version", telebot_version.__version__)

    ikb_sig = inspect.signature(T.InlineKeyboardButton.__init__).parameters
    line("InlineKeyboardButton.has_style", "style" in ikb_sig)
    line("InlineKeyboardButton.has_disabled", "disabled" in ikb_sig)
    line("styled_button", T.InlineKeyboardButton("x", callback_data="c", style="success").to_dict())

    line("WebAppInfo", T.WebAppInfo("https://example.com/app").to_dict())
    line("web_app_button",
         T.InlineKeyboardButton("open", web_app=T.WebAppInfo("https://example.com/app")).to_dict())

    for name in ("MenuButtonDefault", "MenuButtonCommands", "MenuButtonWebApp"):
        line(name, hasattr(T, name))
    if hasattr(T, "MenuButtonWebApp"):
        line("MenuButtonWebApp.sig", inspect.signature(T.MenuButtonWebApp.__init__))
        try:
            button = T.MenuButtonWebApp(text="WebApp",
                                        web_app=T.WebAppInfo("https://example.com/app"))
        except TypeError:
            button = T.MenuButtonWebApp("web_app", text="WebApp",
                                        web_app=T.WebAppInfo("https://example.com/app"))
        line("MenuButtonWebApp", button.to_dict())
    for name in ("MenuButtonCommands", "MenuButtonDefault"):
        if hasattr(T, name):
            try:
                line(name + ".to_dict", getattr(T, name)().to_dict())
            except TypeError:
                line(name + ".sig", inspect.signature(getattr(T, name).__init__))

    for name in ("set_chat_menu_button", "set_my_commands", "answer_web_app_query"):
        method = getattr(telebot.TeleBot, name, None)
        line("Bot." + name, list(inspect.signature(method).parameters) if method else None)

    line("BotCommand", T.BotCommand("start", "بدء").to_dict())
    line("BotCommand.sig", inspect.signature(T.BotCommand.__init__))
    line("BotCommandScopeDefault", json.loads(T.BotCommandScopeDefault().to_json()))
    line("BotCommandScopeChat", json.loads(T.BotCommandScopeChat(-100123).to_json()))

    for name in ("DisabledButton", "EphemeralMessageParameters", "WidgetInfo", "InputRichMessage"):
        line("has_" + name, hasattr(T, name))

    line("InlineQueryResultCachedVideo",
         T.InlineQueryResultCachedVideo(id="7", video_file_id="fk", title="t",
                                        description="d", caption="c",
                                        parse_mode="HTML").to_json())


if __name__ == "__main__":
    main()
