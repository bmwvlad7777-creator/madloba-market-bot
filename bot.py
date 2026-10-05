# -*- coding: utf-8 -*-
import os
import html
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip()

MAX_PHOTOS = 8

app = Flask(__name__)
states = {}


# ============================================================
# CATEGORIES
# ============================================================

CATEGORIES = {
    "realestate": {
        "name": "🏠 Недвижимость",
        "subs": {
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
        "subs": {
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
        "subs": {
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
        "subs": {
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
        "subs": {
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
        "subs": {
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
        "subs": {
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
        "subs": {
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
# CATEGORY-SPECIFIC CHARACTERISTICS
# ============================================================

DETAIL_FIELDS = {

    "realestate": [
        ("rooms", "🛏 Количество комнат"),
        ("area", "📐 Площадь, м²"),
        ("floor", "🏢 Этаж")
    ],

    "auto": [
        ("make_model", "🚗 Марка и модель"),
        ("year", "📅 Год"),
        ("mileage", "🛣 Пробег")
    ],

    "tech": [
        ("brand_model", "📱 Марка и модель"),
        ("condition", "✨ Состояние"),
        ("warranty", "🛡 Гарантия")
    ],

    "home": [
        ("condition", "✨ Состояние"),
        ("dimensions", "📏 Размеры / габариты")
    ],

    "kids": [
        ("condition", "✨ Состояние"),
        ("age", "👶 Возраст ребёнка")
    ],

    "work": [
        ("service", "🛠 Что предлагаете / ищете"),
        ("experience", "⭐ Опыт")
    ],

    "give": [
        ("condition", "✨ Состояние")
    ],

    "search": [
        ("requirements", "📋 Что именно ищете")
    ]
}


# ============================================================
# TELEGRAM API
# ============================================================

def api(method, data=None):

    try:

        response = requests.post(
            f"{API}/{method}",
            json=data or {},
            timeout=25
        )

        result = response.json()

        print(method, result)

        return result

    except Exception as e:

        print(method, e)

        return {
            "ok": False,
            "error": str(e)
        }


def send(chat_id, text, keyboard=None):

    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:

        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return api(
        "sendMessage",
        data
    )


def answer(callback_id):

    return api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


def esc(value):

    return html.escape(
        str(value or "")
    )


def slug(value):

    value = str(
        value or ""
    ).lower()

    value = value.replace(
        "ё",
        "е"
    )

    return "".join(
        c for c in value
        if c.isalnum()
    )


# ============================================================
# MENUS
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


def pair_buttons(items, prefix):

    rows = []

    for i in range(
        0,
        len(items),
        2
    ):

        rows.append(
            [
                {
                    "text": value,
                    "callback_data":
                        f"{prefix}{key}"
                }

                for key, value
                in items[i:i + 2]
            ]
        )

    return rows


def category_menu(category_key):

    rows = pair_buttons(
        list(
            CATEGORIES[
                category_key
            ]["subs"].items()
        ),
        f"browse_{category_key}_"
    )

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


def post_category_menu():

    rows = pair_buttons(
        [
            (
                key,
                value["name"]
            )

            for key, value
            in CATEGORIES.items()
        ],
        "postcat_"
    )

    rows.append(
        [
            {
                "text": "❌ Отмена",
                "callback_data": "cancel_post"
            }
        ]
    )

    return rows


def post_sub_menu(category_key):

    rows = pair_buttons(
        list(
            CATEGORIES[
                category_key
            ]["subs"].items()
        ),
        f"postsub_{category_key}_"
    )

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

    rows = pair_buttons(
        CATEGORIES[
            category_key
        ]["types"],
        "posttype_"
    )

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
# CREATE LISTING
# ============================================================

def new_state(chat_id):

    states[chat_id] = {

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

            "details": {},

            "description": "",

            "photos": [],

            "contact": ""
        }
    }


def start_post(chat_id):

    new_state(chat_id)

    send(
        chat_id,

        "<b>➕ НОВОЕ ОБЪЯВЛЕНИЕ</b>\n\n"
        "Выберите категорию:",

        post_category_menu()
    )


def ask(chat_id, step, text, keyboard=None):

    states[
        chat_id
    ]["step"] = step

    send(
        chat_id,
        text,
        keyboard
    )


def next_after_type(chat_id):

    data = states[
        chat_id
    ]["data"]

    fields = DETAIL_FIELDS.get(
        data["category_key"],
        []
    )

    if fields:

        ask(
            chat_id,

            "detail_0",

            f"<b>3 · {esc(fields[0][1])}</b>\n\n"
            "Введите значение."
        )

    else:

        ask(
            chat_id,

            "title",

            "<b>3 · Заголовок</b>\n\n"
            "Напишите короткий и понятный заголовок.\n\n"
            "<i>Например: Сдам квартиру у моря</i>"
        )


def next_detail(chat_id, index):

    data = states[
        chat_id
    ]["data"]

    fields = DETAIL_FIELDS.get(
        data["category_key"],
        []
    )

    if index < len(fields):

        ask(
            chat_id,

            f"detail_{index}",

            f"<b>{index + 3} · "
            f"{esc(fields[index][1])}</b>\n\n"
            "Введите значение."
        )

    else:

        ask(
            chat_id,

            "title",

            f"<b>{len(fields) + 3} · Заголовок</b>\n\n"
            "Напишите короткий и понятный заголовок."
        )


def ask_price(chat_id):

    ask(
        chat_id,

        "price",

        "<b>Цена</b>\n\n"
        "Укажите цену и валюту.\n\n"
        "<i>Например: 550 $ / месяц</i>\n"
        "<i>20 000 $</i>\n"
        "<i>Договорная</i>\n"
        "<i>Бесплатно</i>"
    )


def ask_district(chat_id):

    ask(
        chat_id,

        "district",

        "<b>Локация</b>\n\n"
        "Укажите район или ориентир в Батуми.\n\n"
        "<i>Например: Пиросмани</i>"
    )


def ask_description(chat_id):

    ask(
        chat_id,

        "description",

        "<b>Описание</b>\n\n"
        "Расскажите о предложении: "
        "состояние, комплектация и другие "
        "важные детали."
    )


def ask_photos(chat_id):

    count = len(
        states[
            chat_id
        ]["data"]["photos"]
    )

    ask(
        chat_id,

        "photos",

        f"<b>📷 Фотографии</b>\n\n"
        f"Добавлено: <b>{count}/{MAX_PHOTOS}</b>\n\n"
        "Отправляйте фотографии по одной.\n"
        "Когда закончите — нажмите <b>Готово</b>.",

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

    ask(
        chat_id,

        "contact",

        "<b>📞 Контакт</b>\n\n"
        "Укажите телефон, Telegram или WhatsApp."
    )


# ============================================================
# EDIT MENU
# ============================================================

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
                "text": "📋 Характеристики",
                "callback_data": "edit_details"
            },
            {
                "text": "📷 Фотографии",
                "callback_data": "edit_photos"
            }
        ],

        [
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


# ============================================================
# LISTING TEXT
# ============================================================

def hashtags(data):

    tags = [

        "#" + slug(
            data["category"].split(
                " ",
                1
            )[-1]
        ),

        "#" + slug(
            data["subcategory"].split(
                " ",
                1
            )[-1]
        )
    ]

    type_tags = {

        "rent": "#сдам",
        "seek": "#ищу",
        "sell": "#продам",
        "buy": "#куплю",
        "give": "#отдам",
        "offer": "#услуги"
    }

    tags.append(
        type_tags.get(
            data["type_key"],
            ""
        )
    )

    if data["district"]:

        tags.append(
            "#" + slug(
                data["district"]
            )
        )

    tags.append(
        "#батум"
    )

    return " ".join(
        dict.fromkeys(
            tag
            for tag in tags
            if tag
        )
    )


def build_listing(data):

    lines = [

        f"<b>{esc(data['category'])} · "
        f"{esc(data['type'])}</b>",

        f"<i>{esc(data['subcategory'])}</i>",

        ""
    ]

    fields = DETAIL_FIELDS.get(
        data["category_key"],
        []
    )

    if data["details"]:

        for key, label in fields:

            value = data[
                "details"
            ].get(
                key
            )

            if value:

                lines.append(
                    f"{esc(label)}: "
                    f"<b>{esc(value)}</b>"
                )

        lines.append("")

    lines += [

        f"<b>{esc(data['title'])}</b>",

        "",

        f"💰 <b>{esc(data['price'])}</b>",

        f"📍 <b>{esc(data['district'])}</b>",

        "",

        esc(
            data["description"]
        ),

        "",

        f"📞 <b>{esc(data['contact'])}</b>",

        "<i>Связаться · WhatsApp / Telegram</i>",

        "",

        hashtags(data)
    ]

    return "\n".join(
        lines
    )


# ============================================================
# PREVIEW
# ============================================================

def preview_keyboard():

    return [

        [
            {
                "text":
                    "✅ Опубликовать объявление",
                "callback_data":
                    "publish_post"
            }
        ],

        [
            {
                "text":
                    "✏️ Изменить данные",
                "callback_data":
                    "edit_menu"
            }
        ],

        [
            {
                "text":
                    "❌ Отмена",
                "callback_data":
                    "cancel_post"
            }
        ]
    ]


def preview(chat_id):

    if chat_id not in states:

        return send(
            chat_id,
            "Сессия закончилась. "
            "Начните новое объявление."
        )

    data = states[
        chat_id
    ]["data"]

    text = build_listing(
        data
    )

    photos = data[
        "photos"
    ]

    if len(text) > 1000:

        text = text[:997] + "..."

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

                item[
                    "caption"
                ] = text

                item[
                    "parse_mode"
                ] = "HTML"

            media.append(
                item
            )

        result = api(
            "sendMediaGroup",
            {
                "chat_id": chat_id,
                "media": media
            }
        )

        if not result.get("ok"):

            api(
                "sendPhoto",
                {
                    "chat_id":
                        chat_id,

                    "photo":
                        photos[0],

                    "caption":
                        text,

                    "parse_mode":
                        "HTML"
                }
            )

        send(

            chat_id,

            "<b>📋 ПРЕДПРОСМОТР</b>\n\n"
            f"📷 Фотографий: "
            f"<b>{len(photos)}</b>\n\n"
            "Проверьте объявление:",

            preview_keyboard()
        )

    else:

        send(
            chat_id,
            text,
            preview_keyboard()
        )


# ============================================================
# PUBLISH
# ============================================================

def publish(chat_id):

    if chat_id not in states:

        return

    data = states[
        chat_id
    ]["data"]

    text = build_listing(
        data
    )

    photos = data[
        "photos"
    ]

    if not CHANNEL_USERNAME:

        return send(

            chat_id,

            "⚠️ <b>Канал пока не подключён.</b>\n\n"
            "Добавьте <b>CHANNEL_USERNAME</b> "
            "в Render → Environment."
        )

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

                item[
                    "caption"
                ] = text

                item[
                    "parse_mode"
                ] = "HTML"

            media.append(
                item
            )

        result = api(

            "sendMediaGroup",

            {
                "chat_id":
                    CHANNEL_USERNAME,

                "media":
                    media
            }
        )

    else:

        result = api(

            "sendMessage",

            {
                "chat_id":
                    CHANNEL_USERNAME,

                "text":
                    text,

                "parse_mode":
                    "HTML"
            }
        )

    if not result.get("ok"):

        return send(

            chat_id,

            "⚠️ <b>Не удалось опубликовать.</b>\n\n"
            "Проверьте, что бот является "
            "администратором канала и "
            "CHANNEL_USERNAME указан правильно."
        )

    del states[
        chat_id
    ]

    send(

        chat_id,

        "🎉 <b>Объявление опубликовано!</b>\n\n"
        "Оно добавлено в "
        "MADLOBA MARKET | БАТУМИ.",

        main_menu()
    )


# ============================================================
# TEXT PROCESSING
# ============================================================

def process_text(chat_id, text):

    if chat_id not in states:

        return False

    state = states[
        chat_id
    ]

    data = state[
        "data"
    ]

    step = state[
        "step"
    ]

    text = text.strip()

    if text.lower() in [
        "отмена",
        "cancel"
    ]:

        del states[
            chat_id
        ]

        send(
            chat_id,

            "❌ Объявление отменено.",

            main_menu()
        )

        return True

    # Category-specific characteristics
    if step.startswith(
        "detail_"
    ):

        index = int(
            step.split("_")[1]
        )

        fields = DETAIL_FIELDS.get(
            data["category_key"],
            []
        )

        if index < len(fields):

            key = fields[index][0]

            data[
                "details"
            ][key] = text

        next_detail(
            chat_id,
            index + 1
        )

        return True

    # Edit category-specific characteristic
    if step.startswith(
        "edit_detail_"
    ):

        index = int(
            step.split("_")[-1]
        )

        fields = DETAIL_FIELDS.get(
            data["category_key"],
            []
        )

        if index < len(fields):

            key = fields[index][0]

            data[
                "details"
            ][key] = text

        preview(
            chat_id
        )

        return True

    if step == "title":

        data[
            "title"
        ] = text

        ask_price(
            chat_id
        )

        return True

    if step == "price":

        data[
            "price"
        ] = text

        ask_district(
            chat_id
        )

        return True

    if step == "district":

        data[
            "district"
        ] = text

        ask_description(
            chat_id
        )

        return True

    if step == "description":

        data[
            "description"
        ] = text

        ask_photos(
            chat_id
        )

        return True

    if step == "photos":

        if text.lower() in [
            "пропустить",
            "skip"
        ]:

            ask_contact(
                chat_id
            )

        else:

            send(

                chat_id,

                "📷 Отправьте фотографию "
                "или нажмите <b>Готово</b>."
            )

        return True

    if step == "contact":

        data[
            "contact"
        ] = text

        preview(
            chat_id
        )

        return True

    if step.startswith(
        "edit_"
    ):

        field = step[
            5:
        ]

        data[
            field
        ] = text

        preview(
            chat_id
        )

        return True

    return False


# ============================================================
# UPDATE HANDLER
# ============================================================

def handle(update):

    # ========================================================
    # MESSAGE
    # ========================================================

    if "message" in update:

        message = update[
            "message"
        ]

        chat_id = message[
            "chat"
        ]["id"]

        text = message.get(
            "text",
            ""
        )

        # ----------------------------------------------------
        # PHOTOS
        # ----------------------------------------------------

        if "photo" in message:

            if (
                chat_id in states
                and
                states[
                    chat_id
                ]["step"] == "photos"
            ):

                photos = states[
                    chat_id
                ]["data"]["photos"]

                if len(photos) < MAX_PHOTOS:

                    photos.append(
                        message[
                            "photo"
                        ][-1]["file_id"]
                    )

                count = len(
                    photos
                )

                send(

                    chat_id,

                    f"📷 Фото добавлено: "
                    f"<b>{count}/{MAX_PHOTOS}</b>",

                    [
                        [
                            {
                                "text":
                                    "✅ Готово",

                                "callback_data":
                                    "photos_done"
                            }
                        ]
                    ]
                )

            return

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if (
            text.startswith(
                "/start"
            )
            or
            text.strip()
            ==
            "🏠 Главное меню"
        ):

            states.pop(
                chat_id,
                None
            )

            send(

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

        if chat_id in states:

            if process_text(
                chat_id,
                text
            ):

                return

        # ----------------------------------------------------
        # COMMANDS
        # ----------------------------------------------------

        if text.startswith(
            "/categories"
        ):

            send(
                chat_id,

                "📂 <b>Выберите категорию:</b>",

                main_menu()
            )

            return

        if text.startswith(
            "/post"
        ):

            start_post(
                chat_id
            )

            return

        if text.startswith(
            "/rules"
        ):

            send(

                chat_id,

                "<b>📋 Правила MADLOBA MARKET</b>\n\n"

                "• Только реальные объявления.\n"
                "• Запрещены мошенничество "
                "и незаконные товары.\n"
                "• Не публикуйте чужие "
                "персональные данные.\n"
                "• Не размещайте спам."
            )

            return

        if text.startswith(
            "/help"
        ):

            send(

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

    if "callback_query" not in update:

        return

    query = update[
        "callback_query"
    ]

    chat_id = query[
        "message"
    ]["chat"]["id"]

    data = query.get(
        "data",
        ""
    )

    answer(
        query["id"]
    )

    # --------------------------------------------------------
    # MAIN MENU
    # --------------------------------------------------------

    if data == "back_main":

        send(

            chat_id,

            "<b>🛒 MADLOBA MARKET | БАТУМИ</b>\n\n"
            "Выберите категорию:",

            main_menu()
        )

        return

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

    if data.startswith(
        "cat_"
    ):

        category_key = data[
            4:
        ]

        if category_key in CATEGORIES:

            send(

                chat_id,

                f"<b>"
                f"{esc(CATEGORIES[category_key]['name'])}"
                f"</b>\n\n"
                "Выберите раздел:",

                category_menu(
                    category_key
                )
            )

        return

    # --------------------------------------------------------
    # BROWSE
    # --------------------------------------------------------

    if data.startswith(
        "browse_"
    ):

        parts = data.split(
            "_",
            2
        )

        if len(parts) < 3:

            return

        category_key = parts[1]

        sub_key = parts[2]

        if category_key not in CATEGORIES:

            return

        if sub_key == "all":

            label = (
                "📋 Все объявления"
            )

        else:

            label = CATEGORIES[
                category_key
            ]["subs"].get(
                sub_key,
                "Раздел"
            )

        send(

            chat_id,

            f"<b>{esc(label)}</b>\n\n"
            "Пока здесь нет опубликованных объявлений.",

            [
                [
                    {
                        "text":
                            "⬅️ Назад",

                        "callback_data":
                            f"cat_{category_key}"
                    }
                ],

                [
                    {
                        "text":
                            "🏠 Главное меню",

                        "callback_data":
                            "back_main"
                    }
                ]
            ]
        )

        return

    # --------------------------------------------------------
    # START POST
    # --------------------------------------------------------

    if data == "post":

        start_post(
            chat_id
        )

        return

    # --------------------------------------------------------
    # POST CATEGORY
    # --------------------------------------------------------

    if data.startswith(
        "postcat_"
    ):

        category_key = data[
            8:
        ]

        if category_key not in CATEGORIES:

            return

        states[
            chat_id
        ] = {

            "step":
                "subcategory",

            "data": {

                "category_key":
                    category_key,

                "category":
                    CATEGORIES[
                        category_key
                    ]["name"],

                "subcategory_key":
                    "",

                "subcategory":
                    "",

                "type_key":
                    "",

                "type":
                    "",

                "title":
                    "",

                "price":
                    "",

                "district":
                    "",

                "details":
                    {},

                "description":
                    "",

                "photos":
                    [],

                "contact":
                    ""
            }
        }

        send(

            chat_id,

            "<b>1 · Категория</b>\n\n"
            "Выберите раздел:",

            post_sub_menu(
                category_key
            )
        )

        return

    # --------------------------------------------------------
    # POST SUBCATEGORY
    # --------------------------------------------------------

    if data.startswith(
        "postsub_"
    ):

        parts = data.split(
            "_",
            2
        )

        if len(parts) < 3:

            return

        category_key = parts[1]

        subcategory_key = parts[2]

        if (
            chat_id not in states
            or
            category_key not in CATEGORIES
            or
            subcategory_key
            not in
            CATEGORIES[
                category_key
            ]["subs"]
        ):

            return

        listing = states[
            chat_id
        ]["data"]

        listing[
            "subcategory_key"
        ] = subcategory_key

        listing[
            "subcategory"
        ] = CATEGORIES[
            category_key
        ]["subs"][
            subcategory_key
        ]

        states[
            chat_id
        ]["step"] = "type"

        send(

            chat_id,

            "<b>2 · Тип объявления</b>\n\n"
            "Что вы хотите сделать?",

            post_type_menu(
                category_key
            )
        )

        return

    # --------------------------------------------------------
    # POST TYPE
    # --------------------------------------------------------

    if data.startswith(
        "posttype_"
    ):

        type_key = data[
            9:
        ]

        if chat_id not in states:

            return

        listing = states[
            chat_id
        ]["data"]

        listing[
            "type_key"
        ] = type_key

        listing[
            "type"
        ] = TYPE_NAMES.get(
            type_key,
            type_key
        )

        next_after_type(
            chat_id
        )

        return

    # --------------------------------------------------------
    # PHOTOS
    # --------------------------------------------------------

    if data == "photos_done":

        if (
            chat_id in states
            and
            states[
                chat_id
            ]["step"] == "photos"
        ):

            ask_contact(
                chat_id
            )

        return

    if data == "photos_skip":

        if chat_id in states:

            states[
                chat_id
            ]["data"]["photos"] = []

            ask_contact(
                chat_id
            )

        return

    # --------------------------------------------------------
    # EDIT MENU
    # --------------------------------------------------------

    if data == "edit_menu":

        if chat_id in states:

            send(

                chat_id,

                "<b>✏️ Что хотите изменить?</b>",

                edit_menu()
            )

        return

    # --------------------------------------------------------
    # EDIT STANDARD FIELDS
    # --------------------------------------------------------

    if data in [
        "edit_title",
        "edit_price",
        "edit_district",
        "edit_description",
        "edit_contact"
    ]:

        if chat_id not in states:

            return

        field = data[
            5:
        ]

        states[
            chat_id
        ]["step"] = (
            "edit_" + field
        )

        prompts = {

            "title":
                "<b>✏️ Новый заголовок</b>",

            "price":
                "<b>💰 Новая цена</b>",

            "district":
                "<b>📍 Новая локация</b>",

            "description":
                "<b>📝 Новое описание</b>",

            "contact":
                "<b>📞 Новый контакт</b>"
        }

        send(
            chat_id,
            prompts[field]
        )

        return

    # --------------------------------------------------------
    # EDIT DETAILS MENU
    # --------------------------------------------------------

    if data == "edit_details":

        if chat_id not in states:

            return

        category_key = states[
            chat_id
        ]["data"][
            "category_key"
        ]

        fields = DETAIL_FIELDS.get(
            category_key,
            []
        )

        if not fields:

            send(

                chat_id,

                "Для этой категории "
                "дополнительных характеристик нет.",

                [
                    [
                        {
                            "text":
                                "⬅️ Назад",

                            "callback_data":
                                "edit_menu"
                        }
                    ]
                ]
            )

            return

        rows = []

        for index, field in enumerate(
            fields
        ):

            rows.append(
                [
                    {
                        "text":
                            field[1],

                        "callback_data":
                            f"edit_detail_{index}"
                    }
                ]
            )

        rows.append(
            [
                {
                    "text":
                        "⬅️ Назад",

                    "callback_data":
                        "edit_menu"
                }
            ]
        )

        send(
            chat_id,
            "<b>📋 Что изменить?</b>",
            rows
        )

        return

    # --------------------------------------------------------
    # EDIT ONE DETAIL
    # --------------------------------------------------------

    if data.startswith(
        "edit_detail_"
    ):

        if chat_id not in states:

            return

        index = int(
            data.rsplit(
                "_",
                1
            )[1]
        )

        category_key = states[
            chat_id
        ]["data"][
            "category_key"
        ]

        fields = DETAIL_FIELDS.get(
            category_key,
            []
        )

        if index < len(fields):

            states[
                chat_id
            ]["step"] = (
                f"edit_detail_{index}"
            )

            send(

                chat_id,

                f"<b>{esc(fields[index][1])}</b>\n\n"
                "Введите новое значение."
            )

        return

    # --------------------------------------------------------
    # EDIT PHOTOS
    # --------------------------------------------------------

    if data == "edit_photos":

        if chat_id in states:

            states[
                chat_id
            ]["data"]["photos"] = []

            ask_photos(
                chat_id
            )

        return

    # --------------------------------------------------------
    # SHOW PREVIEW
    # --------------------------------------------------------

    if data == "show_preview":

        preview(
            chat_id
        )

        return

    # --------------------------------------------------------
    # RESTART
    # --------------------------------------------------------

    if data == "restart_post":

        start_post(
            chat_id
        )

        return

    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------

    if data == "cancel_post":

        states.pop(
            chat_id,
            None
        )

        send(

            chat_id,

            "❌ <b>Размещение отменено.</b>",

            main_menu()
        )

        return

    # --------------------------------------------------------
    # PUBLISH
    # --------------------------------------------------------

    if data == "publish_post":

        publish(
            chat_id
        )

        return


# ============================================================
# WEBHOOK
# ============================================================

@app.get("/")
def home():

    return (
        "MADLOBA MARKET BOT is running."
    )


@app.post("/webhook")
def webhook():

    update = request.get_json(
        silent=True
    )

    if update:

        try:

            handle(
                update
            )

        except Exception as e:

            print(
                "UPDATE ERROR:",
                repr(e)
            )

    return "OK"


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

print(
    "===== BOT START ====="
)

api(
    "getMe"
)

api(
    "setMyCommands",
    {
        "commands": [

            {
                "command":
                    "start",

                "description":
                    "Главное меню"
            },

            {
                "command":
                    "categories",

                "description":
                    "Категории"
            },

            {
                "command":
                    "post",

                "description":
                    "Разместить объявление"
            },

            {
                "command":
                    "rules",

                "description":
                    "Правила"
            },

            {
                "command":
                    "help",

                "description":
                    "Помощь"
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

webhook_url = (
    f"{render_url}/webhook"
)

print(
    "WEBHOOK URL:",
    webhook_url
)

print(
    "SET WEBHOOK:",
    api(
        "setWebhook",
        {
            "url":
                webhook_url
        }
    )
)

print(
    "WEBHOOK INFO:",
    api(
        "getWebhookInfo"
    )
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

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )
