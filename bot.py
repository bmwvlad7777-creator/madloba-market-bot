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
        return response.json()
    except Exception as e:
        print(f"Telegram API error: {e}")
        return {"ok": False, "error": str(e)}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram("sendMessage", data)


def answer_callback(callback_id):
    return telegram(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


def main_menu():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🏠 Недвижимость",
                    "callback_data": "cat_realestate"
                },
                {
                    "text": "🚗 Авто",
                    "callback_data": "cat_auto"
                }
            ],
            [
                {
                    "text": "📱 Техника",
                    "callback_data": "cat_tech"
                },
                {
                    "text": "🛋 Дом и мебель",
                    "callback_data": "cat_home"
                }
            ],
            [
                {
                    "text": "👶 Детское",
                    "callback_data": "cat_kids"
                },
                {
                    "text": "💼 Работа и услуги",
                    "callback_data": "cat_work"
                }
            ],
            [
                {
                    "text": "🎁 Отдам",
                    "callback_data": "cat_give"
                },
                {
                    "text": "🔎 Ищу",
                    "callback_data": "cat_search"
                }
            ],
            [
                {
                    "text": "➕ Разместить объявление",
                    "callback_data": "post"
                }
            ]
        ]
    }


def category_menu(category):
    if category == "realestate":
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Все",
                        "callback_data": "realestate_all"
                    },
                    {
                        "text": "Сдам",
                        "callback_data": "realestate_rent"
                    }
                ],
                [
                    {
                        "text": "Сниму",
                        "callback_data": "realestate_wanted"
                    },
                    {
                        "text": "Продам",
                        "callback_data": "realestate_sale"
                    }
                ],
                [
                    {
                        "text": "Куплю",
                        "callback_data": "realestate_buy"
                    }
                ],
                [
                    {
                        "text": "⬅️ Назад",
                        "callback_data": "back"
                    }
                ]
            ]
        }

    return {
        "inline_keyboard": [
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "back"
                }
            ]
        ]
    }


def process_update(update):

    if "message" in update:

        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text == "/start":

            send_message(
                chat_id,
                "🛒 MADLOBA MARKET | БАТУМИ\n\n"
                "Главная доска объявлений Батуми.\n\n"
                "Выберите категорию:",
                main_menu()
            )

        elif text == "/categories":

            send_message(
                chat_id,
                "📂 Выберите категорию:",
                main_menu()
            )

        elif text == "/post":

            send_message(
                chat_id,
                "➕ Разместить объявление\n\n"
                "Пока это тестовый режим.\n"
                "Скоро здесь появится пошаговая форма подачи объявления."
            )

        elif text == "/rules":

            send_message(
                chat_id,
                "📋 Правила MADLOBA MARKET\n\n"
                "1. Только реальные объявления.\n"
                "2. Запрещены мошенничество и незаконные товары.\n"
                "3. Указывайте актуальную цену.\n"
                "4. Не размещайте чужие личные данные.\n"
                "5. Администрация может удалить нарушающие правила объявления."
            )

        elif text == "/help":

            send_message(
                chat_id,
                "ℹ️ Помощь\n\n"
                "/start — главное меню\n"
                "/categories — категории\n"
                "/post — разместить объявление\n"
                "/rules — правила\n"
                "/help — помощь"
            )


    elif "callback_query" in update:

        callback = update["callback_query"]

        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data", "")

        answer_callback(callback_id)

        if data == "back":

            send_message(
                chat_id,
                "📂 Выберите категорию:",
                main_menu()
            )

        elif data == "cat_realestate":

            send_message(
                chat_id,
                "🏠 Недвижимость\n\n"
                "Выберите раздел:",
                category_menu("realestate")
            )

        elif data == "post":

            send_message(
                chat_id,
                "➕ Разместить объявление\n\n"
                "Форма подачи объявления будет добавлена следующим этапом."
            )

        else:

            send_message(
                chat_id,
                f"Вы выбрали: {data}\n\n"
                "Этот раздел будет подключён следующим этапом."
            )


@app.route("/", methods=["GET"])
def home():
    return "MADLOBA MARKET BOT is running."


@app.route("/healthz", methods=["GET"])
def healthz():
    return "OK"


@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if update:
        print("INCOMING TELEGRAM UPDATE")
        process_update(update)

    return "OK"


def setup_webhook():

    print("===== MADLOBA MARKET BOT STARTING =====")

    if not BOT_TOKEN:

        print("ERROR: BOT_TOKEN is missing")
        return

    # Проверяем токен
    me = telegram("getMe")

    print("BOT INFO:", me)

    # Получаем адрес Render
    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not render_url:

        print("ERROR: RENDER_EXTERNAL_URL is missing")
        return

    webhook_url = render_url.rstrip("/") + "/webhook"

    print("WEBHOOK URL:", webhook_url)

    # Устанавливаем webhook
    result = telegram(
        "setWebhook",
        {
            "url": webhook_url
        }
    )

    print("SET WEBHOOK RESULT:", result)

    # Проверяем webhook
    info = telegram("getWebhookInfo")

    print("WEBHOOK INFO:", info)

    print("===== WEBHOOK SETUP FINISHED =====")


# ВАЖНО:
# Этот код выполняется и при запуске через Gunicorn.
setup_webhook()


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
