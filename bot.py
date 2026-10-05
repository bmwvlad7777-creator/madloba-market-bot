import os
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)


# =========================
# TELEGRAM API
# =========================

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
        return {
            "ok": False,
            "error": str(e)
        }


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
        {
            "callback_query_id": callback_id
        }
    )


# =========================
# MAIN MENU
# =========================

def main_menu():
    return [
        [
            {
                "text": "🏠 Недвижимость",
                "callback_data": "realestate"
            },
            {
                "text": "🚗 Авто",
                "callback_data": "auto"
            }
        ],
        [
            {
                "text": "📱 Техника",
                "callback_data": "tech"
            },
            {
                "text": "🛋 Дом и мебель",
                "callback_data": "home"
            }
        ],
        [
            {
                "text": "👶 Детское",
                "callback_data": "kids"
            },
            {
                "text": "💼 Работа и услуги",
                "callback_data": "work"
            }
        ],
        [
            {
                "text": "🎁 Отдам",
                "callback_data": "give"
            },
            {
                "text": "🔎 Ищу",
                "callback_data": "search"
            }
        ],
        [
            {
                "text": "➕ Разместить объявление",
                "callback_data": "post"
            }
        ]
    ]


# =========================
# REAL ESTATE MENU
# =========================

def realestate_menu():
    return [
        [
            {
                "text": "📋 Все объявления",
                "callback_data": "realestate_all"
            }
        ],
        [
            {
                "text": "🔑 Сдам",
                "callback_data": "realestate_rent"
            },
            {
                "text": "🔎 Сниму",
                "callback_data": "realestate_seek"
            }
        ],
        [
            {
                "text": "🏡 Продам",
                "callback_data": "realestate_sell"
            },
            {
                "text": "💰 Куплю",
                "callback_data": "realestate_buy"
            }
        ],
        [
            {
                "text": "⬅️ Назад",
                "callback_data": "back_main"
            }
        ]
    ]


# =========================
# OTHER CATEGORIES
# =========================

CATEGORY_NAMES = {
    "auto": "🚗 Авто",
    "tech": "📱 Техника",
    "home": "🛋 Дом и мебель",
    "kids": "👶 Детское",
    "work": "💼 Работа и услуги",
    "give": "🎁 Отдам",
    "search": "🔎 Ищу"
}


# =========================
# REAL ESTATE TEXT
# =========================

REAL_ESTATE_TEXT = {
    "realestate_all":
        "🏠 Недвижимость\n\n"
        "Все объявления Батуми.\n\n"
        "Здесь будут показываться актуальные объявления "
        "по недвижимости.",

    "realestate_rent":
        "🔑 Сдам\n\n"
        "Объявления о сдаче недвижимости в Батуми.\n\n"
        "🏢 Квартира\n"
        "🏡 Дом\n"
        "🛏 Комната\n"
        "🏬 Коммерческая недвижимость",

    "realestate_seek":
        "🔎 Сниму\n\n"
        "Объявления от людей, которые ищут жильё "
        "в Батуми.\n\n"
        "🏢 Квартира\n"
        "🏡 Дом\n"
        "🛏 Комната\n"
        "🏬 Коммерция",

    "realestate_sell":
        "🏡 Продам\n\n"
        "Продажа недвижимости в Батуми.\n\n"
        "🏢 Квартира\n"
        "🏡 Дом\n"
        "🌳 Земля\n"
        "🏬 Коммерческая недвижимость",

    "realestate_buy":
        "💰 Куплю\n\n"
        "Объявления от людей, которые ищут "
        "недвижимость для покупки."
}


# =========================
# CATEGORY MENU
# =========================

def category_menu(category):
    name = CATEGORY_NAMES.get(category, "Раздел")

    return [
        [
            {
                "text": "📋 Все объявления",
                "callback_data": f"{category}_all"
            }
        ],
        [
            {
                "text": "⬅️ Назад",
                "callback_data": "back_main"
            }
        ]
    ]


# =========================
# PROCESS UPDATE
# =========================

def process_update(update):

    # -------------------------
    # обычное сообщение
    # -------------------------

    if "message" in update:

        message = update["message"]

        chat_id = message["chat"]["id"]

        text = message.get("text", "")

        # /start
        if text.startswith("/start"):

            send_message(
                chat_id,
                "🛒 MADLOBA MARKET | БАТУМИ\n\n"
                "Главная доска объявлений Батуми.\n\n"
                "Выберите категорию:",
                main_menu()
            )

            return

        # /categories
        if text.startswith("/categories"):

            send_message(
                chat_id,
                "📂 Выберите категорию:",
                main_menu()
            )

            return

        # /post
        if text.startswith("/post"):

            send_message(
                chat_id,
                "➕ Размещение объявления\n\n"
                "Форма размещения будет добавлена следующим этапом."
            )

            return

        # /rules
        if text.startswith("/rules"):

            send_message(
                chat_id,
                "📋 Правила MADLOBA MARKET\n\n"
                "• Только реальные объявления.\n"
                "• Запрещены мошенничество и запрещённые товары.\n"
                "• Не публикуйте чужие персональные данные.\n"
                "• Администрация может удалить объявление, "
                "нарушающее правила."
            )

            return

        # /help
        if text.startswith("/help"):

            send_message(
                chat_id,
                "ℹ️ MADLOBA MARKET\n\n"
                "/start — главное меню\n"
                "/categories — категории\n"
                "/post — разместить объявление\n"
                "/rules — правила\n"
                "/help — помощь"
            )

            return

    # -------------------------
    # callback buttons
    # -------------------------

    if "callback_query" in update:

        callback = update["callback_query"]

        chat_id = callback["message"]["chat"]["id"]

        data = callback.get("data", "")

        answer_callback(callback["id"])

        # Главное меню
        if data == "back_main":

            send_message(
                chat_id,
                "🛒 MADLOBA MARKET | БАТУМИ\n\n"
                "Выберите категорию:",
                main_menu()
            )

            return

        # -------------------------
        # Недвижимость
        # -------------------------

        if data == "realestate":

            send_message(
                chat_id,
                "🏠 Недвижимость\n\n"
                "Выберите раздел:",
                realestate_menu()
            )

            return

        # -------------------------
        # Недвижимость — подкатегории
        # -------------------------

        if data in REAL_ESTATE_TEXT:

            send_message(
                chat_id,
                REAL_ESTATE_TEXT[data],
                [
                    [
                        {
                            "text": "⬅️ Недвижимость",
                            "callback_data": "realestate"
                        }
                    ],
                    [
                        {
                            "text": "🏠 Главное меню",
                            "callback_data": "back_main"
                        }
                    ]
                ]
            )

            return

        # -------------------------
        # Остальные категории
        # -------------------------

        if data in CATEGORY_NAMES:

            name = CATEGORY_NAMES[data]

            send_message(
                chat_id,
                f"{name}\n\n"
                "Раздел готов.\n"
                "Подкатегории добавим следующим этапом.",
                category_menu(data)
            )

            return

        # -------------------------
        # Остальные категории — все
        # -------------------------

        if data.endswith("_all"):

            category = data[:-4]

            if category in CATEGORY_NAMES:

                send_message(
                    chat_id,
                    f"{CATEGORY_NAMES[category]}\n\n"
                    "Пока объявлений нет."
                )

                return

        # -------------------------
        # Разместить объявление
        # -------------------------

        if data == "post":

            send_message(
                chat_id,
                "➕ Разместить объявление\n\n"
                "Скоро здесь появится пошаговая форма "
                "размещения объявления.\n\n"
                "Вы сможете указать:\n"
                "📷 Фото\n"
                "📝 Описание\n"
                "💰 Цену\n"
                "📍 Район\n"
                "📞 Контакт"
            )

            return


# =========================
# WEBHOOK
# =========================

@app.route("/", methods=["GET"])
def home():
    return "MADLOBA MARKET BOT is running."


@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if update:
        process_update(update)

    return "OK"


# =========================
# WEBHOOK SETUP
# =========================

print("===== BOT START =====")

telegram("getMe")

render_url = os.environ.get(
    "RENDER_EXTERNAL_URL",
    "https://madloba-market-bot.onrender.com"
)

webhook_url = f"{render_url}/webhook"

print("WEBHOOK URL:", webhook_url)

set_webhook_result = telegram(
    "setWebhook",
    {
        "url": webhook_url
    }
)

print("SET WEBHOOK RESULT:", set_webhook_result)

webhook_info = telegram("getWebhookInfo")

print("WEBHOOK INFO:", webhook_info)

print("===== WEBHOOK SETUP FINISHED =====")


# =========================
# LOCAL START
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
