import os
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def telegram(method, data=None):
    try:
        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=data or {},
            timeout=20
        )
        result = response.json()
        print(f"TELEGRAM {method}:", result)
        return result
    except Exception as e:
        print(f"TELEGRAM ERROR {method}:", e)
        return {"ok": False, "error": str(e)}


def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram("sendMessage", data)


def answer_callback(callback_id):
    return telegram(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


def main_menu():
    return [
        [
            {"text": "🏠 Недвижимость", "callback_data": "realestate"},
            {"text": "🚗 Авто", "callback_data": "auto"}
        ],
        [
            {"text": "📱 Техника", "callback_data": "tech"},
            {"text": "🛋 Дом и мебель", "callback_data": "home"}
        ],
        [
            {"text": "👶 Детское", "callback_data": "kids"},
            {"text": "💼 Работа и услуги", "callback_data": "work"}
        ],
        [
            {"text": "🎁 Отдам", "callback_data": "give"},
            {"text": "🔎 Ищу", "callback_data": "search"}
        ],
        [
            {"text": "➕ Разместить объявление", "callback_data": "post"}
        ]
    ]


def process_update(update):
    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text.startswith("/start"):
            send_message(
                chat_id,
                "🛒 MADLOBA MARKET | БАТУМИ\n\n"
                "Главная доска объявлений Батуми.\n\n"
                "Выберите категорию:",
                main_menu()
            )

        elif text.startswith("/categories"):
            send_message(
                chat_id,
                "📂 Выберите категорию:",
                main_menu()
            )

        elif text.startswith("/post"):
            send_message(
                chat_id,
                "➕ Размещение объявления\n\n"
                "Скоро здесь появится форма размещения объявления."
            )

        elif text.startswith("/help"):
            send_message(
                chat_id,
                "ℹ️ MADLOBA MARKET | БАТУМИ\n\n"
                "Здесь можно будет покупать, продавать, сдавать "
                "и искать товары и услуги в Батуми."
            )

    elif "callback_query" in update:
        callback = update["callback_query"]
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data")

        answer_callback(callback["id"])

        if data == "post":
            send_message(
                chat_id,
                "➕ Разместить объявление\n\n"
                "Форма размещения будет добавлена следующим этапом."
            )
        elif data in [
            "realestate",
            "auto",
            "tech",
            "home",
            "kids",
            "work",
            "give",
            "search"
        ]:
            names = {
                "realestate": "🏠 Недвижимость",
                "auto": "🚗 Авто",
                "tech": "📱 Техника",
                "home": "🛋 Дом и мебель",
                "kids": "👶 Детское",
                "work": "💼 Работа и услуги",
                "give": "🎁 Отдам",
                "search": "🔎 Ищу"
            }

            send_message(
                chat_id,
                f"{names[data]}\n\n"
                "Раздел готов. Подкатегории добавим следующим этапом."
            )


@app.route("/", methods=["GET"])
def home():
    return "MADLOBA MARKET BOT is running."


@app.route("/health", methods=["GET"])
def health():
    return "OK"


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    print("WEBHOOK UPDATE:", update)

    if update:
        process_update(update)

    return "OK"


# Регистрируем webhook сразу при запуске Gunicorn
def setup_webhook():
    print("===== BOT START =====")

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing!")
        return

    me = telegram("getMe")

    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not render_url:
        print("ERROR: RENDER_EXTERNAL_URL is missing!")
        return

    webhook_url = render_url.rstrip("/") + "/webhook"

    print("WEBHOOK URL:", webhook_url)

    result = telegram(
        "setWebhook",
        {"url": webhook_url}
    )

    print("SET WEBHOOK RESULT:", result)

    info = telegram("getWebhookInfo")

    print("WEBHOOK INFO:", info)

    print("===== WEBHOOK SETUP FINISHED =====")


setup_webhook()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )
