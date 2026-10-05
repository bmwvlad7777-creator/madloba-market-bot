import os
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)


# ---------- TELEGRAM ----------

def telegram(method, data=None):
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=data or {},
        timeout=30
    )
    return response.json()


def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram("sendMessage", data)


def edit_message(chat_id, message_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram("editMessageText", data)


def answer_callback(callback_id):
    return telegram(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


# ---------- MENUS ----------

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
            {"text": "🎁 Отдам", "callback_data": "free"},
            {"text": "🔎 Ищу", "callback_data": "wanted"}
        ],
        [
            {"text": "➕ Разместить объявление", "callback_data": "post"}
        ]
    ]


def back_button():
    return [
        [
            {"text": "◀️ Назад", "callback_data": "back"}
        ]
    ]


def category_menu(category):
    categories = {
        "realestate": (
            "🏠 <b>НЕДВИЖИМОСТЬ</b>\n\n"
            "Выберите раздел:"
        ),
        "auto": (
            "🚗 <b>АВТО</b>\n\n"
            "Выберите раздел:"
        ),
        "tech": (
            "📱 <b>ТЕХНИКА</b>\n\n"
            "Выберите раздел:"
        ),
        "home": (
            "🛋 <b>ДОМ И МЕБЕЛЬ</b>\n\n"
            "Выберите раздел:"
        ),
        "kids": (
            "👶 <b>ДЕТСКОЕ</b>\n\n"
            "Выберите раздел:"
        ),
        "work": (
            "💼 <b>РАБОТА И УСЛУГИ</b>\n\n"
            "Выберите раздел:"
        ),
        "free": (
            "🎁 <b>ОТДАМ</b>\n\n"
            "Здесь будут бесплатные объявления."
        ),
        "wanted": (
            "🔎 <b>ИЩУ</b>\n\n"
            "Здесь будут объявления «Ищу»."
        )
    }

    keyboard = []

    if category == "realestate":
        keyboard = [
            [
                {"text": "Сдам", "callback_data": "rent"},
                {"text": "Сниму", "callback_data": "rental"}
            ],
            [
                {"text": "Продам", "callback_data": "sell"},
                {"text": "Куплю", "callback_data": "buy"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    elif category == "auto":
        keyboard = [
            [
                {"text": "Продам авто", "callback_data": "carsell"},
                {"text": "Куплю авто", "callback_data": "carbuy"}
            ],
            [
                {"text": "Запчасти", "callback_data": "parts"},
                {"text": "Услуги", "callback_data": "autoservice"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    elif category == "tech":
        keyboard = [
            [
                {"text": "Телефоны", "callback_data": "phones"},
                {"text": "Компьютеры", "callback_data": "computers"}
            ],
            [
                {"text": "Техника", "callback_data": "electronics"},
                {"text": "Другое", "callback_data": "techother"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    elif category == "home":
        keyboard = [
            [
                {"text": "Мебель", "callback_data": "furniture"},
                {"text": "Для дома", "callback_data": "house"}
            ],
            [
                {"text": "Декор", "callback_data": "decor"},
                {"text": "Другое", "callback_data": "homeother"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    elif category == "kids":
        keyboard = [
            [
                {"text": "Одежда", "callback_data": "kidsclothes"},
                {"text": "Игрушки", "callback_data": "toys"}
            ],
            [
                {"text": "Коляски", "callback_data": "strollers"},
                {"text": "Другое", "callback_data": "kidsother"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    elif category == "work":
        keyboard = [
            [
                {"text": "Работа", "callback_data": "jobs"},
                {"text": "Услуги", "callback_data": "services"}
            ],
            [
                {"text": "Ищу сотрудника", "callback_data": "employee"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back"}
            ]
        ]

    else:
        keyboard = back_button()

    return categories.get(category, "Раздел"), keyboard


# ---------- COMMANDS ----------

def start(chat_id):
    text = (
        "🛒 <b>MADLOBA MARKET | БАТУМИ</b>\n\n"
        "Главная доска объявлений Батуми.\n\n"
        "🏠 Недвижимость\n"
        "🚗 Авто\n"
        "📱 Техника\n"
        "🛋 Дом и мебель\n"
        "👶 Детское\n"
        "💼 Работа и услуги\n"
        "🎁 Отдам\n"
        "🔎 Ищу\n\n"
        "Выберите категорию:"
    )

    send_message(chat_id, text, main_menu())


def rules(chat_id):
    text = (
        "📋 <b>ПРАВИЛА MADLOBA MARKET</b>\n\n"
        "1. Размещайте только реальные объявления.\n"
        "2. Указывайте честную цену.\n"
        "3. Не размещайте запрещённые товары.\n"
        "4. Не публикуйте мошеннические объявления.\n"
        "5. За содержание объявления отвечает автор.\n\n"
        "🛡 Будьте внимательны при сделках."
    )

    send_message(chat_id, text, back_button())


def help_command(chat_id):
    text = (
        "❓ <b>ПОМОЩЬ</b>\n\n"
        "Чтобы разместить объявление, нажмите:\n"
        "➕ Разместить объявление\n\n"
        "По вопросам работы канала обращайтесь к администрации."
    )

    send_message(chat_id, text, back_button())


def post_ad(chat_id):
    text = (
        "➕ <b>РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ</b>\n\n"
        "На следующем этапе мы сделаем полноценную форму:\n\n"
        "1️⃣ Категория\n"
        "2️⃣ Фото\n"
        "3️⃣ Заголовок\n"
        "4️⃣ Описание\n"
        "5️⃣ Цена\n"
        "6️⃣ Район\n"
        "7️⃣ Контакт\n\n"
        "После проверки объявление будет опубликовано в MADLOBA MARKET."
    )

    send_message(chat_id, text, back_button())


# ---------- UPDATE PROCESSING ----------

def process_update(update):

    # Обычное сообщение
    if "message" in update:
        message = update["message"]

        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text.startswith("/start"):
            start(chat_id)

        elif text.startswith("/categories"):
            start(chat_id)

        elif text.startswith("/post"):
            post_ad(chat_id)

        elif text.startswith("/rules"):
            rules(chat_id)

        elif text.startswith("/help"):
            help_command(chat_id)

        else:
            send_message(
                chat_id,
                "👋 Добро пожаловать в <b>MADLOBA MARKET | БАТУМИ</b>!\n\n"
                "Выберите нужный раздел:",
                main_menu()
            )

    # Нажатие inline-кнопки
    elif "callback_query" in update:

        callback = update["callback_query"]

        callback_id = callback["id"]
        data = callback.get("data")

        message = callback["message"]
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]

        answer_callback(callback_id)

        if data == "back":
            text = (
                "🛒 <b>MADLOBA MARKET | БАТУМИ</b>\n\n"
                "Выберите категорию:"
            )

            edit_message(
                chat_id,
                message_id,
                text,
                main_menu()
            )
            return

        if data == "post":
            text = (
                "➕ <b>РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ</b>\n\n"
                "Скоро здесь появится удобная форма "
                "для публикации объявления."
            )

            edit_message(
                chat_id,
                message_id,
                text,
                back_button()
            )
            return

        if data in [
            "realestate",
            "auto",
            "tech",
            "home",
            "kids",
            "work",
            "free",
            "wanted"
        ]:
            text, keyboard = category_menu(data)

            edit_message(
                chat_id,
                message_id,
                text,
                keyboard
            )
            return

        # Пока подкатегории
        edit_message(
            chat_id,
            message_id,
            "🔎 <b>Раздел готовится</b>\n\n"
            "Здесь будут объявления этой категории.",
            back_button()
        )


# ---------- WEBHOOK ----------

@app.route("/", methods=["GET"])
def home():
    return "MADLOBA MARKET BOT is running."


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    if update:
        process_update(update)

    return "OK"


# ---------- START ----------

if __name__ == "__main__":

    webhook_url = (
        os.environ.get("RENDER_EXTERNAL_URL", "")
        + "/webhook"
    )

    if webhook_url:
        telegram(
            "setWebhook",
            {"url": webhook_url}
        )

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
