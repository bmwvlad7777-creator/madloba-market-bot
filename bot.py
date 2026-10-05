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

CATEGORIES = {
    "realestate": {
        "name": "🏠 Недвижимость",
        "subs": {
            "apartment": "🏢 Квартиры",
            "house": "🏡 Дома",
            "room": "🛏 Комнаты",
            "commercial": "🏬 Коммерция",
            "land": "🌳 Земля",
            "garage": "🚗 Гаражи и парковки",
        },
        "types": [
            ("🔑 Сдам", "rent"),
            ("🔎 Сниму", "seek"),
            ("🏡 Продам", "sell"),
            ("💰 Куплю", "buy"),
        ],
        "fields": [
            ("rooms", "🛏 Комнаты"),
            ("area", "📐 Площадь, м²"),
            ("floor", "🏢 Этаж"),
        ],
    },

    "auto": {
        "name": "🚗 Авто",
        "subs": {
            "cars": "🚘 Легковые",
            "suv": "🚙 Кроссоверы и SUV",
            "commercial": "🚚 Коммерческий транспорт",
            "moto": "🏍 Мото",
            "parts": "⚙️ Запчасти",
            "rental": "🔑 Аренда",
        },
        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🔑 Сдам", "rent"),
            ("🚗 Ищу", "seek"),
        ],
        "fields": [
            ("make_model", "🚗 Марка и модель"),
            ("year", "📅 Год"),
            ("mileage", "🛣 Пробег"),
        ],
    },

    "tech": {
        "name": "📱 Техника",
        "subs": {
            "phones": "📱 Телефоны и планшеты",
            "computers": "💻 Компьютеры",
            "tv": "📺 ТВ и аудио",
            "appliances": "🧺 Бытовая техника",
            "photo": "📷 Фото и видео",
            "other": "🔌 Другая техника",
        },
        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],
        "fields": [
            ("brand_model", "📱 Марка и модель"),
            ("condition", "✨ Состояние"),
            ("warranty", "🛡 Гарантия"),
        ],
    },

    "home": {
        "name": "🛋 Дом и мебель",
        "subs": {
            "furniture": "🛋 Мебель",
            "household": "🏠 Для дома",
            "repair": "🔨 Ремонт",
            "decor": "🖼 Декор",
            "garden": "🌿 Сад и дача",
            "other": "📦 Другое",
        },
        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],
        "fields": [
            ("condition", "✨ Состояние"),
            ("dimensions", "📏 Размеры / габариты"),
        ],
    },

    "kids": {
        "name": "👶 Детское",
        "subs": {
            "clothes": "👕 Одежда и обувь",
            "toys": "🧸 Игрушки",
            "strollers": "🍼 Коляски и автокресла",
            "furniture": "🛏 Детская мебель",
            "sports": "⚽️ Спорт",
            "other": "🎈 Другое",
        },
        "types": [
            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🎁 Отдам", "give"),
        ],
        "fields": [
            ("condition", "✨ Состояние"),
            ("age", "👶 Возраст"),
        ],
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
            "other": "📌 Другое",
        },
        "types": [
            ("💼 Предлагаю", "offer"),
            ("🔎 Ищу", "seek"),
        ],
        "fields": [
            ("service", "🛠 Что предлагаете / ищете"),
            ("experience", "⭐ Опыт"),
        ],
    },

    "give": {
        "name": "🎁 Отдам",
        "subs": {
            "home": "🏠 Для дома",
            "clothes": "👕 Одежда",
            "kids": "👶 Детское",
            "tech": "📱 Техника",
            "other": "📦 Другое",
        },
        "types": [
            ("🎁 Отдам бесплатно", "give"),
        ],
        "fields": [
            ("condition", "✨ Состояние"),
        ],
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
            "other": "📦 Другое",
        },
        "types": [
            ("🔎 Ищу", "seek"),
        ],
        "fields": [
            ("requirements", "📋 Что именно ищете"),
        ],
    },
}

TYPE_NAMES = {
    "rent": "🔑 Сдам",
    "seek": "🔎 Сниму",
    "sell": "🏡 Продам",
    "buy": "💰 Куплю",
    "give": "🎁 Отдам бесплатно",
    "offer": "💼 Предлагаю",
}

CURRENCIES = {
    "usd": "$",
    "gel": "₾",
    "eur": "€",
}


def api(method, data=None):
    try:
        r = requests.post(
            f"{API}/{method}",
            json=data or {},
            timeout=25
        )

        result = r.json()

        print(method, result)

        return result

    except Exception as e:

        print(
            "API ERROR:",
            method,
            repr(e)
        )

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

    value = (
        str(value or "")
        .lower()
        .replace("ё", "е")
    )

    return "".join(
        c for c in value
        if c.isalnum()
    )


def pair_buttons(items, prefix):

    return [
        [
            {
                "text": label,
                "callback_data": f"{prefix}{key}"
            }

            for key, label
            in items[i:i + 2]
        ]

        for i in range(
            0,
            len(items),
            2
        )
    ]


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
        ],
    ]


def category_menu(key):

    rows = pair_buttons(
        list(
            CATEGORIES[key]["subs"].items()
        ),
        f"browse_{key}_"
    )

    rows += [

        [
            {
                "text": "📋 Все объявления",
                "callback_data":
                    f"browse_{key}_all"
            }
        ],

        [
            {
                "text": "⬅️ Главное меню",
                "callback_data":
                    "back_main"
            }
        ],
    ]

    return rows


def post_category_menu():

    rows = pair_buttons(

        [
            (
                k,
                v["name"]
            )

            for k, v
            in CATEGORIES.items()
        ],

        "postcat_"
    )

    rows.append(
        [
            {
                "text": "❌ Отмена",
                "callback_data":
                    "cancel_post"
            }
        ]
    )

    return rows


def post_sub_menu(key):

    rows = pair_buttons(

        list(
            CATEGORIES[key]["subs"].items()
        ),

        f"postsub_{key}_"
    )

    rows.append(
        [
            {
                "text": "⬅️ Назад",
                "callback_data":
                    "post"
            }
        ]
    )

    return rows


def post_type_menu(key):

    # Здесь пользователь видит русский текст.
    # rent / sell / buy / seek используются
    # только внутри программы.

    types = CATEGORIES[key]["types"]

    rows = []

    for i in range(
        0,
        len(types),
        2
    ):

        rows.append(

            [
                {
                    "text": label,
                    "callback_data":
                        f"posttype_{type_key}"
                }

                for label, type_key
                in types[i:i + 2]
            ]
        )

    rows.append(
        [
            {
                "text": "⬅️ Назад",
                "callback_data":
                    f"postcat_{key}"
            }
        ]
    )

    return rows


def currency_menu():

    return [

        [
            {
                "text": "🇺🇸 USD ($)",
                "callback_data":
                    "currency_usd"
            },

            {
                "text": "🇬🇪 GEL (₾)",
                "callback_data":
                    "currency_gel"
            }
        ],

        [
            {
                "text": "🇪🇺 EUR (€)",
                "callback_data":
                    "currency_eur"
            },

            {
                "text": "🤝 Договорная",
                "callback_data":
                    "currency_negotiable"
            }
        ],

        [
            {
                "text": "🎁 Бесплатно",
                "callback_data":
                    "currency_free"
            }
        ],
    ]


def edit_menu():

    return [

        [
            {
                "text": "💰 Цена",
                "callback_data":
                    "edit_price"
            },

            {
                "text": "📍 Локация",
                "callback_data":
                    "edit_district"
            }
        ],

        [
            {
                "text": "📋 Характеристики",
                "callback_data":
                    "edit_details"
            },

            {
                "text": "📝 Описание",
                "callback_data":
                    "edit_description"
            }
        ],

        [
            {
                "text": "📷 Фотографии",
                "callback_data":
                    "edit_photos"
            },

            {
                "text": "📞 Контакт",
                "callback_data":
                    "edit_contact"
            }
        ],

        [
            {
                "text": "🔄 Начать заново",
                "callback_data":
                    "restart_post"
            }
        ],

        [
            {
                "text": "⬅️ К объявлению",
                "callback_data":
                    "show_preview"
            }
        ],
    ]


def blank_listing():

    return {

        "category_key": "",
        "category": "",

        "subcategory_key": "",
        "subcategory": "",

        "type_key": "",
        "type": "",

        "details": {},

        "price": "",
        "currency": "",

        "district": "",

        "description": "",

        "photos": [],

        "contact": "",
    }


def start_post(chat_id):

    states[chat_id] = {

        "step": "category",

        "data": blank_listing()
    }

    send(

        chat_id,

        "<b>➕ НОВОЕ ОБЪЯВЛЕНИЕ</b>\n\n"
        "Выберите категорию:",

        post_category_menu()
    )


def ask(
    chat_id,
    step,
    text,
    keyboard=None
):

    states[
        chat_id
    ]["step"] = step

    send(
        chat_id,
        text,
        keyboard
    )


def ask_detail(
    chat_id,
    index=0
):

    data = states[
        chat_id
    ]["data"]

    fields = CATEGORIES[
        data["category_key"]
    ]["fields"]

    if index < len(fields):

        _, label = fields[
            index
        ]

        ask(

            chat_id,

            f"detail_{index}",

            f"<b>{index + 3} · "
            f"{esc(label)}</b>\n\n"
            "Введите значение."
        )

    else:

        ask_price(
            chat_id
        )


def ask_price(chat_id):

    data = states[
        chat_id
    ]["data"]

    if data[
        "type_key"
    ] == "give":

        data[
            "price"
        ] = "Бесплатно"

        data[
            "currency"
        ] = ""

        ask_district(
            chat_id
        )

        return

    ask(

        chat_id,

        "currency",

        "<b>💰 Цена</b>\n\n"
        "Выберите валюту или вариант цены:",

        currency_menu()
    )


def ask_amount(
    chat_id,
    editing=False
):

    data = states[
        chat_id
    ]["data"]

    if data[
        "currency"
    ] in (
        "negotiable",
        "free"
    ):

        if editing:

            preview(
                chat_id
            )

        else:

            ask_district(
                chat_id
            )

        return

    symbol = CURRENCIES.get(
        data["currency"],
        ""
    )

    ask(

        chat_id,

        "edit_amount"
        if editing
        else "amount",

        f"<b>💰 Сумма в {symbol}</b>\n\n"
        "Введите только число.\n\n"
        "<i>Например: 660</i>"
    )


def ask_district(chat_id):

    ask(

        chat_id,

        "district",

        "<b>📍 Локация</b>\n\n"
        "Укажите район или ориентир в Батуми.\n\n"
        "<i>Например: Пиросмани 18а</i>"
    )


def ask_description(chat_id):

    ask(

        chat_id,

        "description",

        "<b>📝 Описание</b>\n\n"
        "Расскажите о предложении: "
        "состояние, комплектация и другие важные детали."
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
        "Когда закончите — нажмите <b>Готово</b>.\n\n"
        "Telegram покажет их компактным альбомом.",

        [
            [
                {
                    "text": "✅ Готово",
                    "callback_data":
                        "photos_done"
                }
            ],

            [
                {
                    "text": "⏭ Пропустить",
                    "callback_data":
                        "photos_skip"
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
# АВТОМАТИЧЕСКИЙ ЗАГОЛОВОК
# ============================================================

def make_title(data):

    category = data[
        "category_key"
    ]

    typ = data[
        "type_key"
    ]

    details = data[
        "details"
    ]

    if category == "realestate":

        rooms = details.get(
            "rooms",
            ""
        )

        action = {

            "rent":
                "Сдам",

            "seek":
                "Ищу",

            "sell":
                "Продам",

            "buy":
                "Куплю"

        }.get(
            typ,
            "Объявление"
        )

        if rooms:

            return (
                f"{action} "
                f"{rooms}-комнатную квартиру"
            )

        return (
            f"{action} квартиру"
        )

    if category == "auto":

        model = (
            details.get(
                "make_model",
                ""
            )
            or
            "автомобиль"
        )

        year = details.get(
            "year",
            ""
        )

        action = {

            "sell":
                "Продам",

            "buy":
                "Куплю",

            "rent":
                "Сдам",

            "seek":
                "Ищу"

        }.get(
            typ,
            "Авто"
        )

        result = (
            f"{action} {model}"
        )

        if year:

            result += (
                f" · {year}"
            )

        return result

    if category == "tech":

        model = (
            details.get(
                "brand_model",
                ""
            )
            or
            "технику"
        )

        action = {

            "sell":
                "Продам",

            "buy":
                "Куплю"

        }.get(
            typ,
            "Техника"
        )

        return (
            f"{action} {model}"
        )

    if category == "work":

        return (
            details.get(
                "service",
                ""
            )
            or
            "Работа / услуга в Батуми"
        )

    if category == "search":

        req = details.get(
            "requirements",
            ""
        )

        if req:

            return (
                f"Ищу: {req}"
            )

        return "Ищу"

    if typ == "give":

        return "Отдам бесплатно"

    return {

        "sell":
            "Продам",

        "buy":
            "Куплю"

    }.get(
        typ,
        "Объявление"
    )


# ============================================================
# ЦЕНА
# ============================================================

def price_text(data):

    if data[
        "price"
    ] == "Бесплатно":

        return (
            "🎁 <b>Бесплатно</b>"
        )

    if data[
        "price"
    ] == "Договорная":

        return (
            "🤝 <b>Договорная</b>"
        )

    symbol = CURRENCIES.get(
        data["currency"],
        ""
    )

    suffix = ""

    if data[
        "type_key"
    ] == "rent":

        suffix = " / месяц"

    return (
        f"💰 <b>"
        f"{esc(data['price'])} "
        f"{symbol}"
        f"{suffix}</b>"
    )


# ============================================================
# ХЭШТЕГИ
# ============================================================

def build_hashtags(data):

    tags = [

        "#"
        +
        slug(
            data[
                "category"
            ].split(
                " ",
                1
            )[-1]
        ),

        "#"
        +
        slug(
            data[
                "subcategory"
            ].split(
                " ",
                1
            )[-1]
        )
    ]

    type_tags = {

        "rent":
            "#сдам",

        "seek":
            "#ищу",

        "sell":
            "#продам",

        "buy":
            "#куплю",

        "give":
            "#отдам",

        "offer":
            "#услуги"
    }

    if data[
        "type_key"
    ] in type_tags:

        tags.append(
            type_tags[
                data[
                    "type_key"
                ]
            ]
        )

    if data[
        "district"
    ]:

        tags.append(
            "#"
            +
            slug(
                data[
                    "district"
                ]
            )
        )

    tags.append(
        "#батум"
    )

    return " ".join(
        dict.fromkeys(
            tags
        )
    )


# ============================================================
# КАРТОЧКА ОБЪЯВЛЕНИЯ
# ============================================================

def build_listing(data):

    lines = [

        f"<b>"
        f"{esc(data['category'])} · "
        f"{esc(data['type'])}"
        f"</b>",

        f"<i>"
        f"{esc(data['subcategory'])}"
        f"</i>",

        ""
    ]

    # Характеристики
    for key, label in CATEGORIES[
        data["category_key"]
    ]["fields"]:

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

    if data[
        "details"
    ]:

        lines.append("")

    # Заголовок создаётся автоматически.
    # Пользователь отдельно его не вводит.
    lines += [

        f"<b>"
        f"{esc(make_title(data))}"
        f"</b>",

        "",

        price_text(data),

        f"📍 <b>"
        f"{esc(data['district'])}"
        f"</b>"
    ]

    # Описание
    if data[
        "description"
    ]:

        lines += [

            "",

            esc(
                data[
                    "description"
                ]
            )
        ]

    # Контакт
    if data[
        "contact"
    ]:

        lines += [

            "",

            f"📞 <b>"
            f"{esc(data['contact'])}"
            f"</b>",

            "<i>"
            "WhatsApp / Telegram"
            "</i>"
        ]

    # Хэштеги
    lines += [

        "",

        build_hashtags(
            data
        )
    ]

    return "\n".join(
        lines
    )


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


def send_album(
    chat_id,
    photos,
    caption
):

    media = []

    for i, photo in enumerate(
        photos[
            :MAX_PHOTOS
        ]
    ):

        item = {

            "type":
                "photo",

            "media":
                photo
        }

        if i == 0:

            item[
                "caption"
            ] = caption

            item[
                "parse_mode"
            ] = "HTML"

        media.append(
            item
        )

    return api(

        "sendMediaGroup",

        {
            "chat_id":
                chat_id,

            "media":
                media
        }
    )


def preview(chat_id):

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

    if len(text) > 1000:

        text = (
            text[:997]
            +
            "..."
        )

    if photos:

        result = send_album(

            chat_id,

            photos,

            text
        )

        if not result.get(
            "ok"
        ):

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

            "<b>"
            "📋 ПРЕДПРОСМОТР"
            "</b>\n\n"

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
# РЕДАКТИРОВАНИЕ
# ============================================================

def edit_details_menu(chat_id):

    data = states[
        chat_id
    ]["data"]

    fields = CATEGORIES[
        data["category_key"]
    ]["fields"]

    rows = [

        [
            {
                "text":
                    label,

                "callback_data":
                    f"edit_detail_{i}"
            }
        ]

        for i, (
            _,
            label
        )

        in enumerate(
            fields
        )
    ]

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

        "<b>"
        "📋 Характеристики"
        "</b>\n\n"
        "Что хотите изменить?",

        rows
    )


# ============================================================
# ПУБЛИКАЦИЯ
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

        send(

            chat_id,

            "<b>"
            "⚠️ Канал пока не подключён."
            "</b>\n\n"

            "Укажите "
            "<b>CHANNEL_USERNAME</b> "
            "в Render → Environment."
        )

        return

    if photos:

        result = send_album(

            CHANNEL_USERNAME,

            photos,

            text
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

    if not result.get(
        "ok"
    ):

        send(

            chat_id,

            "<b>"
            "⚠️ Не удалось опубликовать."
            "</b>\n\n"

            "Проверьте права бота "
            "в канале и "
            "CHANNEL_USERNAME."
        )

        return

    states.pop(
        chat_id,
        None
    )

    send(

        chat_id,

        "<b>"
        "🎉 Объявление опубликовано!"
        "</b>\n\n"

        "Оно добавлено в "
        "MADLOBA MARKET | БАТУМИ.",

        main_menu()
    )


# ============================================================
# ОБРАБОТКА ТЕКСТА
# ============================================================

def process_text(
    chat_id,
    text
):

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

    if text.lower() in (
        "отмена",
        "cancel"
    ):

        states.pop(
            chat_id,
            None
        )

        send(

            chat_id,

            "❌ Объявление отменено.",

            main_menu()
        )

        return True

    # Характеристики
    if step.startswith(
        "detail_"
    ):

        index = int(
            step.split(
                "_"
            )[1]
        )

        fields = CATEGORIES[
            data["category_key"]
        ]["fields"]

        if index < len(fields):

            data[
                "details"
            ][
                fields[index][0]
            ] = text

        ask_detail(

            chat_id,

            index + 1
        )

        return True

    # Редактирование характеристик
    if step.startswith(
        "edit_detail_"
    ):

        index = int(
            step.rsplit(
                "_",
                1
            )[1]
        )

        fields = CATEGORIES[
            data["category_key"]
        ]["fields"]

        if index < len(fields):

            data[
                "details"
            ][
                fields[index][0]
            ] = text

        preview(
            chat_id
        )

        return True

    # Цена
    if step in (
        "amount",
        "edit_amount"
    ):

        data[
            "price"
        ] = text

        if step == "amount":

            ask_district(
                chat_id
            )

        else:

            preview(
                chat_id
            )

        return True

    # Локация
    if step in (
        "district",
        "edit_district"
    ):

        data[
            "district"
        ] = text

        if step == "district":

            ask_description(
                chat_id
            )

        else:

            preview(
                chat_id
            )

        return True

    # Описание
    if step in (
        "description",
        "edit_description"
    ):

        data[
            "description"
        ] = text

        if step == "description":

            ask_photos(
                chat_id
            )

        else:

            preview(
                chat_id
            )

        return True

    # Фотографии
    if step == "photos":

        send(

            chat_id,

            "📷 Отправьте фото "
            "или нажмите <b>Готово</b>."
        )

        return True

    # Контакт
    if step in (
        "contact",
        "edit_contact"
    ):

        data[
            "contact"
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

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    if "message" in update:

        message = update[
            "message"
        ]

        chat_id = message[
            "chat"
        ]["id"]

        # Фото
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

                if len(
                    photos
                ) < MAX_PHOTOS:

                    photos.append(

                        message[
                            "photo"
                        ][-1][
                            "file_id"
                        ]
                    )

                send(

                    chat_id,

                    f"📷 Фото добавлено: "
                    f"<b>{len(photos)}/{MAX_PHOTOS}</b>",

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

        text = message.get(
            "text",
            ""
        )

        # Главное меню
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

                "<b>"
                "🛒 MADLOBA MARKET | БАТУМИ"
                "</b>\n\n"

                "Главная доска объявлений Батуми.\n\n"

                "Купи · Продай · Сдай · Найди\n\n"

                "<b>"
                "Выберите категорию:"
                "</b>",

                main_menu()
            )

            return

        # Если пользователь находится внутри формы
        if (
            chat_id in states
            and
            process_text(
                chat_id,
                text
            )
        ):

            return

        # Команды
        if text.startswith(
            "/categories"
        ):

            send(

                chat_id,

                "📂 <b>"
                "Выберите категорию:"
                "</b>",

                main_menu()
            )

        elif text.startswith(
            "/post"
        ):

            start_post(
                chat_id
            )

        elif text.startswith(
            "/rules"
        ):

            send(

                chat_id,

                "<b>"
                "📋 Правила MADLOBA MARKET"
                "</b>\n\n"

                "• Только реальные объявления.\n"
                "• Запрещены мошенничество "
                "и незаконные товары.\n"
                "• Не публикуйте чужие "
                "персональные данные.\n"
                "• Не размещайте спам."
            )

        elif text.startswith(
            "/help"
        ):

            send(

                chat_id,

                "<b>"
                "ℹ️ MADLOBA MARKET"
                "</b>\n\n"

                "/start — главное меню\n"
                "/categories — категории\n"
                "/post — разместить объявление\n"
                "/rules — правила\n"
                "/help — помощь"
            )

        return

    # --------------------------------------------------------
    # CALLBACK
    # --------------------------------------------------------

    if "callback_query" not in update:

        return

    q = update[
        "callback_query"
    ]

    chat_id = q[
        "message"
    ]["chat"]["id"]

    data = q.get(
        "data",
        ""
    )

    answer(
        q["id"]
    )

    # Главное меню
    if data == "back_main":

        send(

            chat_id,

            "<b>"
            "🛒 MADLOBA MARKET | БАТУМИ"
            "</b>\n\n"
            "Выберите категорию:",

            main_menu()
        )

        return

    # Категория
    if data.startswith(
        "cat_"
    ):

        key = data[
            4:
        ]

        if key in CATEGORIES:

            send(

                chat_id,

                f"<b>"
                f"{esc(CATEGORIES[key]['name'])}"
                f"</b>\n\n"
                "Выберите раздел:",

                category_menu(
                    key
                )
            )

        return

    # Просмотр категорий
    if data.startswith(
        "browse_"
    ):

        parts = data.split(
            "_",
            2
        )

        if len(parts) < 3:

            return

        key = parts[
            1
        ]

        sub = parts[
            2
        ]

        if key not in CATEGORIES:

            return

        if sub == "all":

            label = (
                "📋 Все объявления"
            )

        else:

            label = CATEGORIES[
                key
            ]["subs"].get(
                sub,
                "Раздел"
            )

        send(

            chat_id,

            f"<b>"
            f"{esc(label)}"
            f"</b>\n\n"

            "Пока здесь нет "
            "опубликованных объявлений.",

            [
                [
                    {
                        "text":
                            "⬅️ Назад",

                        "callback_data":
                            f"cat_{key}"
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

    # Размещение
    if data == "post":

        start_post(
            chat_id
        )

        return

    # Выбор категории при размещении
    if data.startswith(
        "postcat_"
    ):

        key = data[
            8:
        ]

        if key not in CATEGORIES:

            return

        states[
            chat_id
        ] = {

            "step":
                "subcategory",

            "data":
                blank_listing()
        }

        states[
            chat_id
        ]["data"][
            "category_key"
        ] = key

        states[
            chat_id
        ]["data"][
            "category"
        ] = CATEGORIES[
            key
        ]["name"]

        send(

            chat_id,

            "<b>"
            "1 · Категория"
            "</b>\n\n"
            "Выберите раздел:",

            post_sub_menu(
                key
            )
        )

        return

    # Подкатегория
    if data.startswith(
        "postsub_"
    ):

        parts = data.split(
            "_",
            2
        )

        if len(parts) < 3:

            return

        key = parts[
            1
        ]

        sub = parts[
            2
        ]

        if (
            chat_id not in states
            or
            key not in CATEGORIES
            or
            sub not in CATEGORIES[
                key
            ]["subs"]
        ):

            return

        listing = states[
            chat_id
        ]["data"]

        listing[
            "subcategory_key"
        ] = sub

        listing[
            "subcategory"
        ] = CATEGORIES[
            key
        ]["subs"][
            sub
        ]

        states[
            chat_id
        ]["step"] = "type"

        send(

            chat_id,

            "<b>"
            "2 · Тип объявления"
            "</b>\n\n"
            "Выберите действие:",

            post_type_menu(
                key
            )
        )

        return

    # Тип объявления
    if data.startswith(
        "posttype_"
    ):

        if chat_id not in states:

            return

        type_key = data[
            9:
        ]

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

        ask_detail(
            chat_id,
            0
        )

        return

    # Валюта
    if data.startswith(
        "currency_"
    ):

        if chat_id not in states:

            return

        currency = data[
            9:
        ]

        listing = states[
            chat_id
        ]["data"]

        editing = (
            states[
                chat_id
            ]["step"]
            ==
            "edit_currency"
        )

        listing[
            "currency"
        ] = currency

        if currency == "negotiable":

            listing[
                "price"
            ] = "Договорная"

            if editing:

                preview(
                    chat_id
                )

            else:

                ask_district(
                    chat_id
                )

            return

        if currency == "free":

            listing[
                "price"
            ] = "Бесплатно"

            if editing:

                preview(
                    chat_id
                )

            else:

                ask_district(
                    chat_id
                )

            return

        ask_amount(
            chat_id,
            editing
        )

        return

    # Фото готовы
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

    # Фото пропустить
    if data == "photos_skip":

        if chat_id in states:

            states[
                chat_id
            ]["data"][
                "photos"
            ] = []

            ask_contact(
                chat_id
            )

        return

    # Меню редактирования
    if data == "edit_menu":

        if chat_id in states:

            send(

                chat_id,

                "<b>"
                "✏️ Что хотите изменить?"
                "</b>",

                edit_menu()
            )

        return

    # Характеристики
    if data == "edit_details":

        if chat_id in states:

            edit_details_menu(
                chat_id
            )

        return

    # Цена
    if data == "edit_price":

        if chat_id in states:

            states[
                chat_id
            ]["step"] = (
                "edit_currency"
            )

            send(

                chat_id,

                "<b>"
                "💰 Цена"
                "</b>\n\n"
                "Выберите валюту или вариант:",

                currency_menu()
            )

        return

    # Локация / описание / контакт
    if data in (
        "edit_district",
        "edit_description",
        "edit_contact"
    ):

        if chat_id not in states:

            return

        prompts = {

            "edit_district":
                "<b>"
                "📍 Локация"
                "</b>\n\n"
                "Введите новую локацию.",

            "edit_description":
                "<b>"
                "📝 Описание"
                "</b>\n\n"
                "Введите новое описание.",

            "edit_contact":
                "<b>"
                "📞 Контакт"
                "</b>\n\n"
                "Введите новый контакт."
        }

        states[
            chat_id
        ]["step"] = data

        send(
            chat_id,
            prompts[data]
        )

        return

    # Фотографии
    if data == "edit_photos":

        if chat_id in states:

            states[
                chat_id
            ]["data"][
                "photos"
            ] = []

            ask_photos(
                chat_id
            )

        return

    # Отдельная характеристика
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

        fields = CATEGORIES[
            states[
                chat_id
            ]["data"][
                "category_key"
            ]
        ]["fields"]

        if index < len(fields):

            states[
                chat_id
            ]["step"] = (
                f"edit_detail_{index}"
            )

            send(

                chat_id,

                f"<b>"
                f"{esc(fields[index][1])}"
                f"</b>\n\n"
                "Введите новое значение."
            )

        return

    # Предпросмотр
    if data == "show_preview":

        preview(
            chat_id
        )

        return

    # Начать заново
    if data == "restart_post":

        start_post(
            chat_id
        )

        return

    # Отмена
    if data == "cancel_post":

        states.pop(
            chat_id,
            None
        )

        send(

            chat_id,

            "❌ <b>"
            "Размещение отменено."
            "</b>",

            main_menu()
        )

        return

    # Публикация
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
# STARTUP
# ============================================================

print(
    "===== BOT START ====="
)

print(
    "GET ME:",
    api(
        "getMe"
    )
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


if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(

            os.environ.get(
                "PORT",
                "10000"
            )
        )
    )
