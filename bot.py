import os
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Позже сюда добавим username нашего канала через Render Environment
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "")

app = Flask(__name__)

# Временное хранение данных пользователей
user_states = {}


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


def send_photo(chat_id, photo, caption=None, keyboard=None):
    data = {
        "chat_id": chat_id,
        "photo": photo
    }

    if caption:
        data["caption"] = caption

    if keyboard:
        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram("sendPhoto", data)


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
# CATEGORY NAMES
# =========================

CATEGORY_NAMES = {
    "realestate": "🏠 Недвижимость",
    "auto": "🚗 Авто",
    "tech": "📱 Техника",
    "home": "🛋 Дом и мебель",
    "kids": "👶 Детское",
    "work": "💼 Работа и услуги",
    "give": "🎁 Отдам",
    "search": "🔎 Ищу"
}


# =========================
# POST CATEGORIES
# =========================

def post_category_menu():
    return [
        [
            {
                "text": "🏠 Недвижимость",
                "callback_data": "postcat_realestate"
            }
        ],
        [
            {
                "text": "🚗 Авто",
                "callback_data": "postcat_auto"
            },
            {
                "text": "📱 Техника",
                "callback_data": "postcat_tech"
            }
        ],
        [
            {
                "text": "🛋 Дом и мебель",
                "callback_data": "postcat_home"
            },
        ],
        [
            {
                "text": "👶 Детское",
                "callback_data": "postcat_kids"
            },
            {
                "text": "💼 Работа и услуги",
                "callback_data": "postcat_work"
            }
        ],
        [
            {
                "text": "🎁 Отдам",
                "callback_data": "postcat_give"
            },
            {
                "text": "🔎 Ищу",
                "callback_data": "postcat_search"
            }
        ],
        [
            {
                "text": "❌ Отмена",
                "callback_data": "cancel_post"
            }
        ]
    ]


# =========================
# POST TYPES
# =========================

def post_type_menu(category):
    if category == "realestate":
        return [
            [
                {
                    "text": "🔑 Сдам",
                    "callback_data": "type_rent"
                },
                {
                    "text": "🔎 Сниму",
                    "callback_data": "type_seek"
                }
            ],
            [
                {
                    "text": "🏡 Продам",
                    "callback_data": "type_sell"
                },
                {
                    "text": "💰 Куплю",
                    "callback_data": "type_buy"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "auto":
        return [
            [
                {
                    "text": "🚗 Продам",
                    "callback_data": "type_auto_sell"
                },
                {
                    "text": "🔎 Куплю",
                    "callback_data": "type_auto_buy"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "tech":
        return [
            [
                {
                    "text": "📱 Продам",
                    "callback_data": "type_tech_sell"
                },
                {
                    "text": "🔎 Куплю",
                    "callback_data": "type_tech_buy"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "home":
        return [
            [
                {
                    "text": "🛋 Продам",
                    "callback_data": "type_home_sell"
                },
                {
                    "text": "🔎 Куплю",
                    "callback_data": "type_home_buy"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "kids":
        return [
            [
                {
                    "text": "👶 Продам",
                    "callback_data": "type_kids_sell"
                },
                {
                    "text": "🔎 Куплю",
                    "callback_data": "type_kids_buy"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "work":
        return [
            [
                {
                    "text": "💼 Предлагаю услугу",
                    "callback_data": "type_work_offer"
                }
            ],
            [
                {
                    "text": "🔎 Ищу работу/услугу",
                    "callback_data": "type_work_seek"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "give":
        return [
            [
                {
                    "text": "🎁 Отдам бесплатно",
                    "callback_data": "type_give"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    if category == "search":
        return [
            [
                {
                    "text": "🔎 Ищу товар",
                    "callback_data": "type_search"
                }
            ],
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": "post"
                }
            ]
        ]

    return []


# =========================
# POST FLOW
# =========================

def start_post(chat_id):
    user_states[chat_id] = {
        "step": "category",
        "data": {}
    }

    send_message(
        chat_id,
        "➕ РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ\n\n"
        "Шаг 1 из 7\n\n"
        "Выберите категорию:",
        post_category_menu()
    )


def ask_title(chat_id):
    state = user_states[chat_id]
    state["step"] = "title"

    send_message(
        chat_id,
        "📝 Шаг 3 из 7\n\n"
        "Напишите короткий заголовок объявления.\n\n"
        "Например:\n"
        "«Сдам 2-комнатную квартиру у моря»"
    )


def ask_price(chat_id):
    state = user_states[chat_id]
    state["step"] = "price"

    send_message(
        chat_id,
        "💰 Шаг 4 из 7\n\n"
        "Укажите цену.\n\n"
        "Например:\n"
        "800 USD\n"
        "1200 GEL\n"
        "Бесплатно\n"
        "Договорная"
    )


def ask_district(chat_id):
    state = user_states[chat_id]
    state["step"] = "district"

    send_message(
        chat_id,
        "📍 Шаг 5 из 7\n\n"
        "Укажите район или ориентир в Батуми.\n\n"
        "Например:\n"
        "Новый бульвар\n"
        "Старый город\n"
        "Аэропорт\n"
        "Химшиашвили"
    )


def ask_description(chat_id):
    state = user_states[chat_id]
    state["step"] = "description"

    send_message(
        chat_id,
        "📝 Шаг 6 из 7\n\n"
        "Напишите описание объявления.\n\n"
        "Можно указать площадь, количество комнат,\n"
        "состояние, особенности и другую важную информацию."
    )


def ask_photo(chat_id):
    state = user_states[chat_id]
    state["step"] = "photo"

    send_message(
        chat_id,
        "📷 Шаг 7 из 7\n\n"
        "Отправьте фотографию объявления.\n\n"
        "Можно отправить одну фотографию.\n\n"
        "Если фото нет — напишите:\n"
        "Пропустить"
    )


def ask_contact(chat_id):
    state = user_states[chat_id]
    state["step"] = "contact"

    send_message(
        chat_id,
        "📞 Последний шаг\n\n"
        "Укажите контакт для связи.\n\n"
        "Например:\n"
        "@username\n"
        "+995 555 123456\n"
        "WhatsApp: +995 555 123456"
    )


def show_preview(chat_id):
    state = user_states[chat_id]
    data = state["data"]

    category = data.get("category", "")
    post_type = data.get("type", "")
    title = data.get("title", "")
    price = data.get("price", "")
    district = data.get("district", "")
    description = data.get("description", "")
    contact = data.get("contact", "")

    preview = (
        "📋 ПРЕДПРОСМОТР ОБЪЯВЛЕНИЯ\n\n"
        f"{category}\n"
        f"{post_type}\n\n"
        f"🔹 {title}\n"
        f"💰 {price}\n"
        f"📍 {district}\n\n"
        f"{description}\n\n"
        f"📞 {contact}"
    )

    keyboard = [
        [
            {
                "text": "✅ Опубликовать",
                "callback_data": "publish_post"
            }
        ],
        [
            {
                "text": "✏️ Заполнить заново",
                "callback_data": "restart_post"
            }
        ],
        [
            {
                "text": "❌ Отмена",
                "callback_data": "cancel_post"
            }
        ]
    ]

    if data.get("photo"):
        send_photo(
            chat_id,
            data["photo"],
            preview,
            keyboard
        )
    else:
        send_message(
            chat_id,
            preview,
            keyboard
        )


# =========================
# PUBLISH
# =========================

def publish_post(chat_id):

    if chat_id not in user_states:
        send_message(
            chat_id,
            "Сессия размещения закончилась. "
            "Нажмите /start и начните заново."
        )
        return

    state = user_states[chat_id]
    data = state["data"]

    category = data.get("category", "")
    post_type = data.get("type", "")
    title = data.get("title", "")
    price = data.get("price", "")
    district = data.get("district", "")
    description = data.get("description", "")
    contact = data.get("contact", "")

    post_text = (
        f"{category}\n"
        f"{post_type}\n\n"
        f"🔹 {title}\n"
        f"💰 {price}\n"
        f"📍 {district}\n\n"
        f"{description}\n\n"
        f"📞 {contact}\n\n"
        "#Батуми #MadlobaMarket"
    )

    # Если канал уже указан в Render
    if CHANNEL_USERNAME:

        if data.get("photo"):
            result = send_photo(
                CHANNEL_USERNAME,
                data["photo"],
                post_text
            )
        else:
            result = send_message(
                CHANNEL_USERNAME,
                post_text
            )

        if result.get("ok"):

            send_message(
                chat_id,
                "✅ Объявление опубликовано!\n\n"
                "Спасибо за использование MADLOBA MARKET."
            )

        else:

            send_message(
                chat_id,
                "⚠️ Объявление подготовлено, "
                "но публикация в канале пока недоступна.\n\n"
                "Администратор должен проверить настройки канала."
            )

    else:

        # Пока канал не подключен
        send_message(
            chat_id,
            "✅ Объявление заполнено и готово к публикации.\n\n"
            "Следующим шагом подключим автоматическую "
            "публикацию в канал MADLOBA MARKET."
        )

        print("READY TO PUBLISH:")
        print(post_text)

    del user_states[chat_id]


# =========================
# PROCESS TEXT
# =========================

def process_text(chat_id, text):

    if chat_id not in user_states:
        return False

    state = user_states[chat_id]
    step = state["step"]
    data = state["data"]

    if text.lower() in ["отмена", "cancel"]:

        del user_states[chat_id]

        send_message(
            chat_id,
            "❌ Размещение объявления отменено.",
            main_menu()
        )

        return True

    if step == "title":

        data["title"] = text
        ask_price(chat_id)
        return True

    if step == "price":

        data["price"] = text
        ask_district(chat_id)
        return True

    if step == "district":

        data["district"] = text
        ask_description(chat_id)
        return True

    if step == "description":

        data["description"] = text
        ask_photo(chat_id)
        return True

    if step == "photo":

        if text.lower() in [
            "пропустить",
            "без фото",
            "skip"
        ]:
            data["photo"] = None
            ask_contact(chat_id)
        else:
            send_message(
                chat_id,
                "📷 Пожалуйста, отправьте фотографию "
                "или напишите «Пропустить»."
            )

        return True

    if step == "contact":

        data["contact"] = text
        show_preview(chat_id)
        return True

    return False


# =========================
# PROCESS UPDATE
# =========================

def process_update(update):

    # =========================
    # MESSAGE
    # =========================

    if "message" in update:

        message = update["message"]

        chat_id = message["chat"]["id"]

        text = message.get("text", "")

        # Фото
        if "photo" in message:

            if chat_id in user_states:

                state = user_states[chat_id]

                if state["step"] == "photo":

                    photo = message["photo"][-1]

                    state["data"]["photo"] = photo["file_id"]

                    ask_contact(chat_id)

                    return

            return

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

        # Если пользователь находится внутри формы
        if chat_id in user_states:

            if process_text(chat_id, text):
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

            start_post(chat_id)
            return

        # /rules
        if text.startswith("/rules"):

            send_message(
                chat_id,
                "📋 Правила MADLOBA MARKET\n\n"
                "• Только реальные объявления.\n"
                "• Запрещены мошенничество и запрещённые товары.\n"
                "• Не публикуйте чужие персональные данные.\n"
                "• Запрещены незаконные товары и услуги.\n"
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

    # =========================
    # CALLBACK
    # =========================

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

        # =========================
        # НЕДВИЖИМОСТЬ
        # =========================

        if data == "realestate":

            send_message(
                chat_id,
                "🏠 Недвижимость\n\n"
                "Выберите раздел:",
                realestate_menu()
            )

            return

        if data in [
            "realestate_all",
            "realestate_rent",
            "realestate_seek",
            "realestate_sell",
            "realestate_buy"
        ]:

            texts = {
                "realestate_all":
                    "📋 Все объявления\n\n"
                    "Пока объявлений нет.",

                "realestate_rent":
                    "🔑 Сдам\n\n"
                    "Пока объявлений нет.",

                "realestate_seek":
                    "🔎 Сниму\n\n"
                    "Пока объявлений нет.",

                "realestate_sell":
                    "🏡 Продам\n\n"
                    "Пока объявлений нет.",

                "realestate_buy":
                    "💰 Куплю\n\n"
                    "Пока объявлений нет."
            }

            send_message(
                chat_id,
                texts[data],
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

        # =========================
        # РАЗМЕЩЕНИЕ
        # =========================

        if data == "post":

            start_post(chat_id)
            return

        # Выбор категории объявления
        if data.startswith("postcat_"):

            category = data.replace(
                "postcat_",
                ""
            )

            user_states[chat_id] = {
                "step": "type",
                "data": {
                    "category":
                        CATEGORY_NAMES.get(
                            category,
                            category
                        ),
                    "category_key": category
                }
            }

            send_message(
                chat_id,
                "📂 Шаг 2 из 7\n\n"
                f"{CATEGORY_NAMES.get(category, category)}\n\n"
                "Выберите тип объявления:",
                post_type_menu(category)
            )

            return

        # Выбор типа объявления
        if data.startswith("type_"):

            if chat_id not in user_states:
                start_post(chat_id)
                return

            type_names = {
                "type_rent": "🔑 Сдам",
                "type_seek": "🔎 Сниму",
                "type_sell": "🏡 Продам",
                "type_buy": "💰 Куплю",
                "type_auto_sell": "🚗 Продам",
                "type_auto_buy": "🚗 Куплю",
                "type_tech_sell": "📱 Продам",
                "type_tech_buy": "📱 Куплю",
                "type_home_sell": "🛋 Продам",
                "type_home_buy": "🛋 Куплю",
                "type_kids_sell": "👶 Продам",
                "type_kids_buy": "👶 Куплю",
                "type_work_offer": "💼 Предлагаю услугу",
                "type_work_seek": "💼 Ищу работу/услугу",
                "type_give": "🎁 Отдам бесплатно",
                "type_search": "🔎 Ищу товар"
            }

            user_states[chat_id]["data"]["type"] = \
                type_names.get(data, data)

            ask_title(chat_id)
            return

        # Перезапуск формы
        if data == "restart_post":

            start_post(chat_id)
            return

        # Отмена формы
        if data == "cancel_post":

            if chat_id in user_states:
                del user_states[chat_id]

            send_message(
                chat_id,
                "❌ Размещение объявления отменено.",
                main_menu()
            )

            return

        # Публикация
        if data == "publish_post":

            publish_post(chat_id)
            return

        # Остальные категории
        if data in [
            "auto",
            "tech",
            "home",
            "kids",
            "work",
            "give",
            "search"
        ]:

            name = CATEGORY_NAMES[data]

            send_message(
                chat_id,
                f"{name}\n\n"
                "Раздел готов.\n"
                "Подкатегории добавим следующим этапом.",
                [
                    [
                        {
                            "text": "⬅️ Назад",
                            "callback_data": "back_main"
                        }
                    ]
                ]
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

print(
    "SET WEBHOOK RESULT:",
    set_webhook_result
)

webhook_info = telegram(
    "getWebhookInfo"
)

print(
    "WEBHOOK INFO:",
    webhook_info
)

print(
    "===== WEBHOOK SETUP FINISHED ====="
)


# =========================
# START
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
