# -*- coding: utf-8 -*-
import os
import html
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Optional. If empty, the bot will prepare the listing but will not publish it.
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip()

app = Flask(__name__)

# Temporary storage for active listing forms.
# Later this can be replaced with a database.
user_states = {}

MAX_PHOTOS = 8


# ============================================================
# TELEGRAM API
# ============================================================

def telegram(method, data=None):
    try:
        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=data or {},
            timeout=25
        )

        result = response.json()

        print(f"TELEGRAM {method}: {result}")

        return result

    except Exception as e:
        print(f"TELEGRAM ERROR {method}: {e}")

        return {
            "ok": False,
            "error": str(e)
        }


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


def send_photo(chat_id, photo, caption=None):
    data = {
        "chat_id": chat_id,
        "photo": photo
    }

    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"

    return telegram("sendPhoto", data)


def answer_callback(callback_id):
    return telegram(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


# ============================================================
# HELPERS
# ============================================================

def esc(value):
    return html.escape(str(value or ""))


def slug(value):
    value = str(value or "").lower()

    replacements = {
        " ": "",
        "-": "",
        ",": "",
        ".": "",
        "/": "",
        "\\": "",
        "ё": "е"
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return value


# ============================================================
# CATEGORIES
# ============================================================

CATEGORIES = {

    "realestate": {
        "name": "🏠 Недвижимость",

        "subcategories": {
            "apartment": "🏢 Квартиры",
            "house": "🏡 Дома",
            "room": "🛏 Комнаты",
            "commercial": "🏬 Коммерция",
            "land": "🌳 Земля",
            "garage": "🚗 Гаражи и парковки"
        },

        "types": [
            ("🔑 Сдам", "rent"),
            ("🔎 Сниму", "seek"),
            ("🏡 Продам", "sell"),
            ("💰 Куплю", "buy")
        ]
    },

    "auto": {
        "name": "🚗 Авто",

        "subcategories": {
            "cars": "🚘 Легковые",
            "suv": "🚙 Кроссоверы и SUV",
            "commercial": "🚚 Коммерческий транспорт",
            "moto": "🏍 Мото",
            "parts": "⚙️ Запчасти",
            "rental": "🔑 Аренда"
        },

        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🔑 Сдам", "rent"),
            ("🚗 Ищу", "seek")
        ]
    },

    "tech": {
        "name": "📱 Техника",

        "subcategories": {
            "phones": "📱 Телефоны и планшеты",
            "computers": "💻 Компьютеры",
            "tv": "📺 ТВ и аудио",
            "appliances": "🧺 Бытовая техника",
            "photo": "📷 Фото и видео",
            "other": "🔌 Другая техника"
        },

        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy")
        ]
    },

    "home": {
        "name": "🛋 Дом и мебель",

        "subcategories": {
            "furniture": "🛋 Мебель",
            "household": "🏠 Для дома",
            "repair": "🔨 Ремонт",
            "decor": "🖼 Декор",
            "garden": "🌿 Сад и дача",
            "other": "📦 Другое"
        },

        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy")
        ]
    },

    "kids": {
        "name": "👶 Детское",

        "subcategories": {
            "clothes": "👕 Одежда и обувь",
            "toys": "🧸 Игрушки",
            "strollers": "🍼 Коляски и автокресла",
            "furniture": "🛏 Детская мебель",
            "sports": "⚽️ Спорт",
            "other": "🎈 Другое"
        },

        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🎁 Отдам", "give")
        ]
    },

    "work": {
        "name": "💼 Работа и услуги",

        "subcategories": {
            "jobs": "💼 Вакансии",
            "services": "🛠 Услуги",
            "construction": "🔨 Ремонт и строительство",
            "beauty": "💇 Красота",
            "education": "🎓 Обучение",
            "transport": "🚚 Транспорт и доставка",
            "it": "💻 IT",
            "other": "📌 Другое"
        },

        "types": [
            ("💼 Предлагаю", "offer"),
            ("🔎 Ищу", "seek")
        ]
    },

    "give": {
        "name": "🎁 Отдам",

        "subcategories": {
            "home": "🏠 Для дома",
            "clothes": "👕 Одежда",
            "kids": "👶 Детское",
            "tech": "📱 Техника",
            "other": "📦 Другое"
        },

        "types": [
            ("🎁 Отдам бесплатно", "give")
        ]
    },

    "search": {
        "name": "🔎 Ищу",

        "subcategories": {
            "realestate": "🏠 Недвижимость",
            "auto": "🚗 Авто",
            "tech": "📱 Техника",
            "home": "🛋 Дом и мебель",
            "kids": "👶 Детское",
            "services": "🛠 Услуги",
            "other": "📦 Другое"
        },

        "types": [
            ("🔎 Ищу", "seek")
        ]
    }
}


TYPE_NAMES = {
    "rent": "🔑 Сдам",
    "seek": "🔎 Ищу",
    "sell": "💰 Продам",
    "buy": "💰 Куплю",
    "give": "🎁 Отдам бесплатно",
    "offer": "💼 Предлагаю"
}


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():

    return [
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
                "text": "🚀 РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ",
                "callback_data": "post"
            }
        ]
    ]


def category_menu(category_key):

    category = CATEGORIES[category_key]

    rows = []

    items = list(category["subcategories"].items())

    for i in range(0, len(items), 2):

        row = []

        for key, label in items[i:i + 2]:

            row.append(
                {
                    "text": label,
                    "callback_data":
                        f"browse_{category_key}_{key}"
                }
            )

        rows.append(row)

    rows.append(
        [
            {
                "text": "📋 Все объявления",
                "callback_data":
                    f"browse_{category_key}_all"
            }
        ]
    )

    rows.append(
        [
            {
                "text": "⬅️ Главное меню",
                "callback_data": "back_main"
            }
        ]
    )

    return rows


# ============================================================
# POST MENUS
# ============================================================

def post_category_menu():

    rows = []

    items = list(CATEGORIES.items())

    for i in range(0, len(items), 2):

        row = []

        for key, category in items[i:i + 2]:

            row.append(
                {
                    "text": category["name"],
                    "callback_data":
                        f"postcat_{key}"
                }
            )

        rows.append(row)

    rows.append(
        [
            {
                "text": "❌ Отмена",
                "callback_data": "cancel_post"
            }
        ]
    )

    return rows


def post_subcategory_menu(category_key):

    category = CATEGORIES[category_key]

    rows = []

    items = list(category["subcategories"].items())

    for i in range(0, len(items), 2):

        row = []

        for key, label in items[i:i + 2]:

            row.append(
                {
                    "text": label,
                    "callback_data":
                        f"postsub_{category_key}_{key}"
                }
            )

        rows.append(row)

    rows.append(
        [
            {
                "text": "⬅️ Назад",
                "callback_data": "post"
            }
        ]
    )

    return rows


def post_type_menu(category_key):

    rows = []

    types = CATEGORIES[category_key]["types"]

    for i in range(0, len(types), 2):

        row = []

        for label, type_key in types[i:i + 2]:

            row.append(
                {
                    "text": label,
                    "callback_data":
                        f"posttype_{type_key}"
                }
            )

        rows.append(row)

    rows.append(
        [
            {
                "text": "⬅️ Назад",
                "callback_data":
                    f"postcat_{category_key}"
            }
        ]
    )

    return rows


# ============================================================
# LISTING FORM
# ============================================================

def start_post(chat_id):

    user_states[chat_id] = {

        "step": "category",

        "data": {

            "category_key": "",
            "category": "",

            "subcategory_key": "",
            "subcategory": "",

            "type_key": "",
            "type": "",

            "title": "",
            "price": "",
            "district": "",
            "description": "",

            "photos": [],

            "contact": ""
        }
    }

    send_message(
        chat_id,

        "<b>➕ НОВОЕ ОБЪЯВЛЕНИЕ</b>\n\n"
        "Выберите категорию:",

        post_category_menu()
    )


def ask_title(chat_id):

    user_states[chat_id]["step"] = "title"

    send_message(
        chat_id,

        "<b>3 · Заголовок</b>\n\n"
        "Напишите короткий и понятный заголовок.\n\n"
        "<i>Например: Сдам 3-комнатную квартиру у моря</i>"
    )


def ask_price(chat_id):

    user_states[chat_id]["step"] = "price"

    send_message(
        chat_id,

        "<b>4 · Цена</b>\n\n"
        "Укажите цену и валюту.\n\n"
        "<i>Например: 550 $ / месяц</i>\n"
        "<i>Или: Договорная</i>\n"
        "<i>Или: Бесплатно</i>"
    )


def ask_district(chat_id):

    user_states[chat_id]["step"] = "district"

    send_message(
        chat_id,

        "<b>5 · Локация</b>\n\n"
        "Укажите район или ориентир в Батуми.\n\n"
        "<i>Например: Пиросмани</i>"
    )


def ask_description(chat_id):

    user_states[chat_id]["step"] = "description"

    send_message(
        chat_id,

        "<b>6 · Описание</b>\n\n"
        "Расскажите о предложении.\n\n"
        "Укажите характеристики, состояние, комплектацию "
        "и другие важные детали."
    )


def ask_photos(chat_id):

    user_states[chat_id]["step"] = "photos"

    count = len(
        user_states[chat_id]["data"]["photos"]
    )

    send_message(
        chat_id,

        f"<b>7 · Фотографии</b>\n\n"
        f"Добавлено: <b>{count}/{MAX_PHOTOS}</b>\n\n"
        "Отправляйте фотографии по одной.\n"
        "Когда закончите — нажмите кнопку <b>Готово</b>.\n\n"
        "Можно также написать <b>Пропустить</b>.",

        [
            [
                {
                    "text": "✅ Готово",
                    "callback_data": "photos_done"
                }
            ],

            [
                {
                    "text": "⏭ Пропустить",
                    "callback_data": "photos_skip"
                }
            ]
        ]
    )


def ask_contact(chat_id):

    user_states[chat_id]["step"] = "contact"

    send_message(
        chat_id,

        "<b>8 · Контакт</b>\n\n"
        "Укажите телефон, Telegram или WhatsApp.\n\n"
        "<i>Например: +995 599 024 723</i>"
    )


# ============================================================
# PREVIEW
# ============================================================

def preview_keyboard():

    return [

        [
            {
                "text": "✅ Опубликовать объявление",
                "callback_data": "publish_post"
            }
        ],

        [
            {
                "text": "✏️ Изменить данные",
                "callback_data": "edit_menu"
            }
        ],

        [
            {
                "text": "❌ Отмена",
                "callback_data": "cancel_post"
            }
        ]
    ]


def edit_menu():

    return [

        [
            {
                "text": "✏️ Заголовок",
                "callback_data": "edit_title"
            },
            {
                "text": "💰 Цена",
                "callback_data": "edit_price"
            }
        ],

        [
            {
                "text": "📍 Локация",
                "callback_data": "edit_district"
            },
            {
                "text": "📝 Описание",
                "callback_data": "edit_description"
            }
        ],

        [
            {
                "text": "📷 Фотографии",
                "callback_data": "edit_photos"
            },
            {
                "text": "📞 Контакт",
                "callback_data": "edit_contact"
            }
        ],

        [
            {
                "text": "🔄 Начать заново",
                "callback_data": "restart_post"
            }
        ],

        [
            {
                "text": "⬅️ К объявлению",
                "callback_data": "show_preview"
            }
        ]
    ]


def build_hashtags(data):

    tags = []

    category_key = data.get(
        "category_key",
        ""
    )

    subcategory_key = data.get(
        "subcategory_key",
        ""
    )

    type_key = data.get(
        "type_key",
        ""
    )

    district = data.get(
        "district",
        ""
    )

    if category_key:

        tags.append(
            "#"
            +
            slug(
                data.get(
                    "category",
                    ""
                ).split(
                    " ",
                    1
                )[-1]
            )
        )

    if subcategory_key:

        tags.append(
            "#"
            +
            slug(
                data.get(
                    "subcategory",
                    ""
                ).split(
                    " ",
                    1
                )[-1]
            )
        )

    type_map = {

        "rent": "#сдам",
        "seek": "#ищу",
        "sell": "#продам",
        "buy": "#куплю",
        "give": "#отдам",
        "offer": "#услуги"
    }

    if type_key in type_map:

        tags.append(
            type_map[type_key]
        )

    if district:

        tags.append(
            "#"
            +
            slug(district)
        )

    tags.append("#батум")

    result = []

    for tag in tags:

        if tag and tag not in result:

            result.append(tag)

    return " ".join(result[:7])


def build_listing(data):

    category = esc(data.get("category"))
    subcategory = esc(data.get("subcategory"))
    post_type = esc(data.get("type"))

    title = esc(data.get("title"))
    price = esc(data.get("price"))
    district = esc(data.get("district"))
    description = esc(data.get("description"))
    contact = esc(data.get("contact"))

    hashtags = build_hashtags(data)

    return (
        f"<b>{category} · {post_type}</b>\n"
        f"<i>{subcategory}</i>\n\n"

        f"<b>{title}</b>\n\n"

        f"💰 <b>{price}</b>\n"
        f"📍 <b>{district}</b>\n\n"

        f"{description}\n\n"

        f"📞 <b>{contact}</b>\n"
        f"<i>Связаться · WhatsApp / Telegram</i>\n\n"

        f"{hashtags}"
    )


def show_preview(chat_id):

    if chat_id not in user_states:

        send_message(
            chat_id,
            "Сессия закончилась. "
            "Начните новое объявление."
        )

        return

    data = user_states[chat_id]["data"]

    listing = build_listing(data)

    photos = data.get("photos", [])

    if len(listing) > 1000:

        listing = listing[:997] + "..."

    if photos:

        media = []

        for index, photo in enumerate(
            photos[:MAX_PHOTOS]
        ):

            item = {
                "type": "photo",
                "media": photo
            }

            if index == 0:

                item["caption"] = listing
                item["parse_mode"] = "HTML"

            media.append(item)

        album_result = telegram(
            "sendMediaGroup",
            {
                "chat_id": chat_id,
                "media": media
            }
        )

        if not album_result.get("ok"):

            print(
                "PREVIEW ALBUM ERROR:",
                album_result
            )

            send_photo(
                chat_id,
                photos[0],
                listing
            )

        send_message(
            chat_id,

            f"<b>📋 ПРЕДПРОСМОТР ОБЪЯВЛЕНИЯ</b>\n\n"
            f"📷 Фотографий: <b>{len(photos)}</b>\n\n"
            "Проверьте объявление "
            "и выберите действие:",

            preview_keyboard()
        )

    else:

        send_message(
            chat_id,
            listing,
            preview_keyboard()
        )


# ============================================================
# EDITING
# ============================================================

def start_edit(chat_id, field):

    if chat_id not in user_states:

        start_post(chat_id)

        return

    user_states[chat_id]["step"] = field

    prompts = {

        "title":
            "<b>✏️ Заголовок</b>\n\n"
            "Введите новый заголовок.",

        "price":
            "<b>💰 Цена</b>\n\n"
            "Введите новую цену.",

        "district":
            "<b>📍 Локация</b>\n\n"
            "Введите новый район.",

        "description":
            "<b>📝 Описание</b>\n\n"
            "Введите новое описание.",

        "contact":
            "<b>📞 Контакт</b>\n\n"
            "Введите новый контакт."
    }

    if field == "photos":

        user_states[chat_id]["data"]["photos"] = []

        ask_photos(chat_id)

        return

    send_message(
        chat_id,
        prompts.get(
            field,
            "Введите новое значение."
        )
    )


# ============================================================
# PUBLISH
# ============================================================

def publish_post(chat_id):

    if chat_id not in user_states:

        send_message(
            chat_id,
            "Сессия закончилась. "
            "Начните новое объявление."
        )

        return

    data = user_states[chat_id]["data"]

    listing = build_listing(data)

    photos = data.get("photos", [])

    if not CHANNEL_USERNAME:

        send_message(
            chat_id,

            "⚠️ <b>Канал пока не подключён.</b>\n\n"
            "Объявление готово, но "
            "CHANNEL_USERNAME не указан в Render."
        )

        print("CHANNEL_USERNAME is empty.")
        print("LISTING READY:")
        print(listing)

        return

    if photos:

        media = []

        for index, photo in enumerate(
            photos[:MAX_PHOTOS]
        ):

            item = {
                "type": "photo",
                "media": photo
            }

            if index == 0:

                item["caption"] = listing
                item["parse_mode"] = "HTML"

            media.append(item)

        result = telegram(
            "sendMediaGroup",
            {
                "chat_id": CHANNEL_USERNAME,
                "media": media
            }
        )

        if not result.get("ok"):

            print(
                "PUBLISH ALBUM ERROR:",
                result
            )

            send_message(
                chat_id,

                "⚠️ <b>Не удалось опубликовать объявление.</b>\n\n"
                "Проверьте, что бот является "
                "администратором канала."
            )

            return

    else:

        result = send_message(
            CHANNEL_USERNAME,
            listing
        )

        if not result.get("ok"):

            print(
                "PUBLISH ERROR:",
                result
            )

            send_message(
                chat_id,

                "⚠️ <b>Не удалось опубликовать объявление.</b>\n\n"
                "Проверьте права бота в канале."
            )

            return

    send_message(
        chat_id,

        "🎉 <b>Объявление опубликовано!</b>\n\n"
        "Оно добавлено в "
        "MADLOBA MARKET | БАТУМИ.",

        main_menu()
    )

    del user_states[chat_id]


# ============================================================
# FORM TEXT PROCESSING
# ============================================================

def process_form_text(chat_id, text):

    if chat_id not in user_states:

        return False

    state = user_states[chat_id]
    data = state["data"]
    step = state["step"]

    clean = text.strip()

    if clean.lower() in ["отмена", "cancel"]:

        del user_states[chat_id]

        send_message(
            chat_id,

            "❌ Объявление отменено.",

            main_menu()
        )

        return True

    if step == "title":

        data["title"] = clean
        ask_price(chat_id)

        return True

    if step == "price":

        data["price"] = clean
        ask_district(chat_id)

        return True

    if step == "district":

        data["district"] = clean
        ask_description(chat_id)

        return True

    if step == "description":

        data["description"] = clean
        ask_photos(chat_id)

        return True

    if step == "photos":

        if clean.lower() in [
            "пропустить",
            "skip"
        ]:

            ask_contact(chat_id)

            return True

        send_message(
            chat_id,

            "📷 Отправьте фотографию "
            "или нажмите <b>Готово</b>."
        )

        return True

    if step == "contact":

        data["contact"] = clean
        show_preview(chat_id)

        return True

    return False


# ============================================================
# PROCESS UPDATE
# ============================================================

def process_update(update):

    if "message" in update:

        message = update["message"]

        chat_id = message["chat"]["id"]

        text = message.get("text", "")

        # ----------------------------------------------------
        # PHOTO
        # ----------------------------------------------------

        if "photo" in message:

            if chat_id in user_states:

                state = user_states[chat_id]

                if state["step"] == "photos":

                    photos = state["data"]["photos"]

                    if len(photos) < MAX_PHOTOS:

                        photos.append(
                            message["photo"][-1]["file_id"]
                        )

                    count = len(photos)

                    if count >= MAX_PHOTOS:

                        send_message(
                            chat_id,

                            f"📷 Добавлено "
                            f"<b>{count}/{MAX_PHOTOS}</b> фото.\n\n"
                            "Максимум достигнут. "
                            "Нажмите <b>Готово</b>.",

                            [
                                [
                                    {
                                        "text": "✅ Готово",
                                        "callback_data":
                                            "photos_done"
                                    }
                                ]
                            ]
                        )

                    else:

                        send_message(
                            chat_id,

                            f"📷 Фото добавлено: "
                            f"<b>{count}/{MAX_PHOTOS}</b>\n\n"
                            "Можете отправить ещё "
                            "фото или нажать <b>Готово</b>.",

                            [
                                [
                                    {
                                        "text": "✅ Готово",
                                        "callback_data":
                                            "photos_done"
                                    }
                                ]
                            ]
                        )

                    return

            return

        # ----------------------------------------------------
        # START / HOME
        # ----------------------------------------------------

        if (
            text.startswith("/start")
            or
            text.strip() == "🏠 Главное меню"
        ):

            if chat_id in user_states:

                del user_states[chat_id]

            send_message(
                chat_id,

                "<b>🛒 MADLOBA MARKET | БАТУМИ</b>\n\n"
                "Главная доска объявлений Батуми.\n\n"
                "Купи · Продай · Сдай · Найди\n\n"
                "<b>Выберите категорию:</b>",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # ACTIVE FORM
        # ----------------------------------------------------

        if chat_id in user_states:

            if process_form_text(
                chat_id,
                text
            ):

                return

        # ----------------------------------------------------
        # COMMANDS
        # ----------------------------------------------------

        if text.startswith("/categories"):

            send_message(
                chat_id,

                "📂 <b>Выберите категорию:</b>",

                main_menu()
            )

            return

        if text.startswith("/post"):

            start_post(chat_id)

            return

        if text.startswith("/rules"):

            send_message(
                chat_id,

                "<b>📋 Правила MADLOBA MARKET</b>\n\n"

                "• Только реальные объявления.\n"
                "• Запрещены мошенничество "
                "и незаконные товары.\n"
                "• Не публикуйте чужие "
                "персональные данные.\n"
                "• Не размещайте спам.\n"
                "• Администрация может удалить "
                "объявление, нарушающее правила."
            )

            return

        if text.startswith("/help"):

            send_message(
                chat_id,

                "<b>ℹ️ MADLOBA MARKET</b>\n\n"

                "/start — главное меню\n"
                "/categories — категории\n"
                "/post — разместить объявление\n"
                "/rules — правила\n"
                "/help — помощь"
            )

            return

    # ========================================================
    # CALLBACK
    # ========================================================

    if "callback_query" in update:

        callback = update["callback_query"]

        chat_id = callback["message"]["chat"]["id"]

        data = callback.get("data", "")

        answer_callback(callback["id"])

        # ----------------------------------------------------
        # MAIN MENU
        # ----------------------------------------------------

        if data == "back_main":

            send_message(
                chat_id,

                "<b>🛒 MADLOBA MARKET | БАТУМИ</b>\n\n"
                "Выберите категорию:",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if data.startswith("cat_"):

            category_key = data.replace(
                "cat_",
                ""
            )

            if category_key not in CATEGORIES:

                return

            category = CATEGORIES[category_key]

            send_message(
                chat_id,

                f"<b>{esc(category['name'])}</b>\n\n"
                "Выберите раздел:",

                category_menu(category_key)
            )

            return

        # ----------------------------------------------------
        # BROWSE
        # ----------------------------------------------------

        if data.startswith("browse_"):

            parts = data.split("_", 2)

            if len(parts) < 3:

                return

            category_key = parts[1]
            sub_key = parts[2]

            if category_key not in CATEGORIES:

                return

            category = CATEGORIES[category_key]

            if sub_key == "all":

                label = "📋 Все объявления"

            else:

                label = category[
                    "subcategories"
                ].get(
                    sub_key,
                    "Раздел"
                )

            send_message(
                chat_id,

                f"<b>{esc(label)}</b>\n\n"
                "Пока здесь нет опубликованных объявлений.\n\n"
                "Когда объявления появятся, "
                "они будут показываться "
                "в этом разделе.",

                [
                    [
                        {
                            "text": "⬅️ Назад",
                            "callback_data":
                                f"cat_{category_key}"
                        }
                    ],

                    [
                        {
                            "text": "🏠 Главное меню",
                            "callback_data":
                                "back_main"
                        }
                    ]
                ]
            )

            return

        # ----------------------------------------------------
        # START POST
        # ----------------------------------------------------

        if data == "post":

            start_post(chat_id)

            return

        # ----------------------------------------------------
        # POST CATEGORY
        # ----------------------------------------------------

        if data.startswith("postcat_"):

            category_key = data.replace(
                "postcat_",
                ""
            )

            if category_key not in CATEGORIES:

                return

            user_states[chat_id] = {

                "step": "subcategory",

                "data": {

                    "category_key": category_key,

                    "category":
                        CATEGORIES[
                            category_key
                        ]["name"],

                    "subcategory_key": "",

                    "subcategory": "",

                    "type_key": "",

                    "type": "",

                    "title": "",

                    "price": "",

                    "district": "",

                    "description": "",

                    "photos": [],

                    "contact": ""
                }
            }

            send_message(
                chat_id,

                "<b>1 · Категория</b>\n\n"
                f"{esc(CATEGORIES[category_key]['name'])}\n\n"
                "Теперь выберите раздел:",

                post_subcategory_menu(category_key)
            )

            return

        # ----------------------------------------------------
        # POST SUBCATEGORY
        # ----------------------------------------------------

        if data.startswith("postsub_"):

            parts = data.split("_", 2)

            if len(parts) < 3:

                return

            category_key = parts[1]
            subcategory_key = parts[2]

            if (
                category_key not in CATEGORIES
                or
                subcategory_key
                not in
                CATEGORIES[
                    category_key
                ]["subcategories"]
            ):

                return

            state = user_states.get(chat_id)

            if not state:

                start_post(chat_id)

                return

            state["step"] = "type"

            state["data"][
                "subcategory_key"
            ] = subcategory_key

            state["data"][
                "subcategory"
            ] = CATEGORIES[
                category_key
            ]["subcategories"][
                subcategory_key
            ]

            send_message(
                chat_id,

                "<b>2 · Тип объявления</b>\n\n"
                f"{esc(state['data']['subcategory'])}\n\n"
                "Что вы хотите сделать?",

                post_type_menu(category_key)
            )

            return

        # ----------------------------------------------------
        # POST TYPE
        # ----------------------------------------------------

        if data.startswith("posttype_"):

            type_key = data.replace(
                "posttype_",
                ""
            )

            if type_key not in TYPE_NAMES:

                return

            if chat_id not in user_states:

                start_post(chat_id)

                return

            state = user_states[chat_id]

            state["step"] = "title"

            state["data"][
                "type_key"
            ] = type_key

            state["data"][
                "type"
            ] = TYPE_NAMES[type_key]

            ask_title(chat_id)

            return

        # ----------------------------------------------------
        # PHOTOS
        # ----------------------------------------------------

        if data == "photos_done":

            if chat_id not in user_states:

                return

            if user_states[
                chat_id
            ]["step"] != "photos":

                return

            ask_contact(chat_id)

            return

        if data == "photos_skip":

            if chat_id not in user_states:

                return

            if user_states[
                chat_id
            ]["step"] != "photos":

                return

            user_states[
                chat_id
            ]["data"]["photos"] = []

            ask_contact(chat_id)

            return

        # ----------------------------------------------------
        # EDIT
        # ----------------------------------------------------

        if data == "edit_menu":

            if chat_id not in user_states:

                start_post(chat_id)

                return

            send_message(
                chat_id,

                "<b>✏️ Что хотите изменить?</b>",

                edit_menu()
            )

            return

        if data in [
            "edit_title",
            "edit_price",
            "edit_district",
            "edit_description",
            "edit_contact",
            "edit_photos"
        ]:

            field = data.replace(
                "edit_",
                ""
            )

            start_edit(
                chat_id,
                field
            )

            return

        # ----------------------------------------------------
        # PREVIEW
        # ----------------------------------------------------

        if data == "show_preview":

            show_preview(chat_id)

            return

        # ----------------------------------------------------
        # RESTART
        # ----------------------------------------------------

        if data == "restart_post":

            start_post(chat_id)

            return

        # ----------------------------------------------------
        # CANCEL
        # ----------------------------------------------------

        if data == "cancel_post":

            if chat_id in user_states:

                del user_states[chat_id]

            send_message(
                chat_id,

                "❌ <b>Размещение отменено.</b>\n\n"
                "Выберите действие:",

                main_menu()
            )

            return

        # ----------------------------------------------------
        # PUBLISH
        # ----------------------------------------------------

        if data == "publish_post":

            publish_post(chat_id)

            return


# ============================================================
# WEBHOOK
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return "MADLOBA MARKET BOT is running."


@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if update:

        try:

            process_update(update)

        except Exception as e:

            print(
                "PROCESS UPDATE ERROR:",
                e
            )

    return "OK"


# ============================================================
# BOT COMMAND MENU
# ============================================================

print("===== BOT START =====")

telegram("getMe")

telegram(
    "setMyCommands",
    {
        "commands": [
            {
                "command": "start",
                "description": "Главное меню"
            },
            {
                "command": "categories",
                "description": "Категории"
            },
            {
                "command": "post",
                "description": "Разместить объявление"
            },
            {
                "command": "rules",
                "description": "Правила"
            },
            {
                "command": "help",
                "description": "Помощь"
            }
        ]
    }
)


# ============================================================
# WEBHOOK SETUP
# ============================================================

render_url = os.environ.get(
    "RENDER_EXTERNAL_URL",
    "https://madloba-market-bot.onrender.com"
)

webhook_url = f"{render_url}/webhook"

print(
    "WEBHOOK URL:",
    webhook_url
)

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
    "CHANNEL USERNAME:",
    CHANNEL_USERNAME
)

print(
    "===== WEBHOOK SETUP FINISHED ====="
)


# ============================================================
# LOCAL START
# ============================================================

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
