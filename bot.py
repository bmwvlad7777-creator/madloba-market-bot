# -*- coding: utf-8 -*-

import os
import html
import requests

from flask import Flask, request


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHANNEL_USERNAME = os.environ.get(
    "CHANNEL_USERNAME",
    ""
).strip()

MAX_PHOTOS = 8

app = Flask(__name__)

states = {}


# ============================================================
# КАТЕГОРИИ
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
            "garage": "🚗 Гаражи и парковки",
        },

        "types": [
            ("🔑 Сдам", "rent"),
            ("🔎 Сниму", "seek"),
            ("🏡 Продам", "sell"),
            ("💰 Куплю", "buy"),
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
    },
}


# ============================================================
# ХАРАКТЕРИСТИКИ КАЖДОГО РАЗДЕЛА
# ============================================================

SECTION_FIELDS = {

    # НЕДВИЖИМОСТЬ

    "apartment": [
        ("rooms", "🛏 Комнаты"),
        ("area", "📐 Площадь, м²"),
        ("floor", "🏢 Этаж"),
    ],

    "house": [
        ("area", "📐 Площадь дома, м²"),
        ("land_area", "🌳 Площадь участка, м²"),
        ("floors", "🏢 Этажей"),
    ],

    "room": [
        ("area", "📐 Площадь, м²"),
        ("floor", "🏢 Этаж"),
    ],

    "commercial": [
        ("area", "📐 Площадь, м²"),
        ("floor", "🏢 Этаж"),
    ],

    "land": [
        ("land_area", "🌳 Площадь участка, м²"),
        ("purpose", "📋 Назначение"),
    ],

    "garage": [
        ("area", "📐 Площадь, м²"),
        ("type", "🚗 Тип"),
    ],

    # АВТО

    "cars": [
        ("make_model", "🚗 Марка и модель"),
        ("year", "📅 Год"),
        ("mileage", "🛣 Пробег"),
    ],

    "suv": [
        ("make_model", "🚙 Марка и модель"),
        ("year", "📅 Год"),
        ("mileage", "🛣 Пробег"),
    ],

    "commercial_auto": [
        ("make_model", "🚚 Марка и модель"),
        ("year", "📅 Год"),
        ("mileage", "🛣 Пробег"),
    ],

    "moto": [
        ("make_model", "🏍 Марка и модель"),
        ("year", "📅 Год"),
        ("mileage", "🛣 Пробег"),
    ],

    "parts": [
        ("part_name", "⚙️ Название запчасти"),
        ("make_model", "🚗 Для какой модели"),
        ("condition", "✨ Состояние"),
    ],

    "rental": [
        ("make_model", "🚗 Марка и модель"),
        ("year", "📅 Год"),
        ("rental_period", "📅 Срок аренды"),
    ],

    # ТЕХНИКА

    "phones": [
        ("brand_model", "📱 Марка и модель"),
        ("memory", "💾 Память"),
        ("condition", "✨ Состояние"),
    ],

    "computers": [
        ("brand_model", "💻 Марка и модель"),
        ("specs", "⚙️ Характеристики"),
        ("condition", "✨ Состояние"),
    ],

    "tv": [
        ("brand_model", "📺 Марка и модель"),
        ("size", "📏 Диагональ"),
        ("condition", "✨ Состояние"),
    ],

    "appliances": [
        ("brand_model", "🧺 Марка и модель"),
        ("condition", "✨ Состояние"),
        ("warranty", "🛡 Гарантия"),
    ],

    "photo": [
        ("brand_model", "📷 Марка и модель"),
        ("condition", "✨ Состояние"),
        ("specs", "⚙️ Характеристики"),
    ],

    "tech_other": [
        ("brand_model", "📱 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    # ДОМ

    "furniture": [
        ("item", "🛋 Что продаёте"),
        ("condition", "✨ Состояние"),
        ("dimensions", "📏 Размеры"),
    ],

    "household": [
        ("item", "🏠 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    "repair": [
        ("service", "🔨 Какая работа"),
        ("experience", "⭐ Опыт"),
    ],

    "decor": [
        ("item", "🖼 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    "garden": [
        ("item", "🌿 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    "home_other": [
        ("item", "📦 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    # ДЕТСКОЕ

    "clothes": [
        ("item", "👕 Что продаёте"),
        ("size", "📏 Размер"),
        ("condition", "✨ Состояние"),
    ],

    "toys": [
        ("item", "🧸 Какая игрушка"),
        ("age", "👶 Возраст"),
        ("condition", "✨ Состояние"),
    ],

    "strollers": [
        ("item", "🍼 Что продаёте"),
        ("condition", "✨ Состояние"),
        ("age", "👶 Возраст"),
    ],

    "kids_furniture": [
        ("item", "🛏 Что продаёте"),
        ("condition", "✨ Состояние"),
        ("dimensions", "📏 Размеры"),
    ],

    "sports": [
        ("item", "⚽️ Что продаёте"),
        ("condition", "✨ Состояние"),
        ("size", "📏 Размер"),
    ],

    "kids_other": [
        ("item", "🎈 Что продаёте"),
        ("condition", "✨ Состояние"),
    ],

    # РАБОТА

    "jobs": [
        ("service", "💼 Какая вакансия"),
        ("experience", "⭐ Требуемый опыт"),
    ],

    "services": [
        ("service", "🛠 Какая услуга"),
        ("experience", "⭐ Опыт"),
    ],

    "construction": [
        ("service", "🔨 Какая работа"),
        ("experience", "⭐ Опыт"),
    ],

    "beauty": [
        ("service", "💇 Какая услуга"),
        ("experience", "⭐ Опыт"),
    ],

    "education": [
        ("service", "🎓 Что преподаёте"),
        ("experience", "⭐ Опыт"),
    ],

    "transport": [
        ("service", "🚚 Какая услуга"),
        ("experience", "⭐ Опыт"),
    ],

    "it": [
        ("service", "💻 Какая услуга / вакансия"),
        ("experience", "⭐ Опыт"),
    ],

    "work_other": [
        ("service", "📌 Что предлагаете"),
        ("experience", "⭐ Опыт"),
    ],

    # ОТДАМ

    "give_home": [
        ("item", "🏠 Что отдаёте"),
        ("condition", "✨ Состояние"),
    ],

    "give_clothes": [
        ("item", "👕 Что отдаёте"),
        ("size", "📏 Размер"),
        ("condition", "✨ Состояние"),
    ],

    "give_kids": [
        ("item", "👶 Что отдаёте"),
        ("age", "👶 Возраст"),
        ("condition", "✨ Состояние"),
    ],

    "give_tech": [
        ("item", "📱 Что отдаёте"),
        ("condition", "✨ Состояние"),
    ],

    "give_other": [
        ("item", "📦 Что отдаёте"),
        ("condition", "✨ Состояние"),
    ],

    # ИЩУ

    "search_realestate": [
        ("requirements", "🏠 Что ищете"),
        ("budget", "💰 Бюджет"),
    ],

    "search_auto": [
        ("requirements", "🚗 Какой автомобиль"),
        ("budget", "💰 Бюджет"),
    ],

    "search_tech": [
        ("requirements", "📱 Что ищете"),
        ("budget", "💰 Бюджет"),
    ],

    "search_home": [
        ("requirements", "🛋 Что ищете"),
        ("budget", "💰 Бюджет"),
    ],

    "search_kids": [
        ("requirements", "👶 Что ищете"),
        ("budget", "💰 Бюджет"),
    ],

    "search_services": [
        ("requirements", "🛠 Какая услуга нужна"),
        ("budget", "💰 Бюджет"),
    ],

    "search_other": [
        ("requirements", "🔎 Что ищете"),
        ("budget", "💰 Бюджет"),
    ],
}


# ============================================================
# НАЗВАНИЯ ТИПОВ И ВАЛЮТ
# ============================================================

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


# ============================================================
# ПОЛУЧЕНИЕ ПОЛЕЙ
# ============================================================

def get_fields(data):

    category = data.get(
        "category_key",
        ""
    )

    subcategory = data.get(
        "subcategory_key",
        ""
    )

    key = subcategory

    if category == "auto" and subcategory == "commercial":
        key = "commercial_auto"

    elif category == "tech" and subcategory == "other":
        key = "tech_other"

    elif category == "home" and subcategory == "other":
        key = "home_other"

    elif category == "kids" and subcategory == "furniture":
        key = "kids_furniture"

    elif category == "kids" and subcategory == "other":
        key = "kids_other"

    elif category == "give":
        key = f"give_{subcategory}"

    elif category == "search":
        key = f"search_{subcategory}"

    elif category == "work" and subcategory == "other":
        key = "work_other"

    return SECTION_FIELDS.get(
        key,
        []
    )


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

        print(
            method,
            result
        )

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


def send(
    chat_id,
    text,
    keyboard=None
):

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


# ============================================================
# КНОПКИ
# ============================================================

def pair_buttons(
    items,
    prefix
):

    return [

        [
            {
                "text": label,
                "callback_data":
                    f"{prefix}{key}"
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
                "callback_data":
                    "cat_realestate"
            },
            {
                "text": "🚗 Авто",
                "callback_data":
                    "cat_auto"
            }
        ],

        [
            {
                "text": "📱 Техника",
                "callback_data":
                    "cat_tech"
            },
            {
                "text": "🛋 Дом и мебель",
                "callback_data":
                    "cat_home"
            }
        ],

        [
            {
                "text": "👶 Детское",
                "callback_data":
                    "cat_kids"
            },
            {
                "text": "💼 Работа и услуги",
                "callback_data":
                    "cat_work"
            }
        ],

        [
            {
                "text": "🎁 Отдам",
                "callback_data":
                    "cat_give"
            },
            {
                "text": "🔎 Ищу",
                "callback_data":
                    "cat_search"
            }
        ],

        [
            {
                "text":
                    "🚀 РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ",
                "callback_data":
                    "post"
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

    rows.append([
        {
            "text":
                "📋 Все объявления",
            "callback_data":
                f"browse_{key}_all"
        }
    ])

    rows.append([
        {
            "text":
                "⬅️ Главное меню",
            "callback_data":
                "back_main"
        }
    ])

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

    rows.append([
        {
            "text":
                "❌ Отмена",
            "callback_data":
                "cancel_post"
        }
    ])

    return rows


def post_sub_menu(key):

    rows = pair_buttons(

        list(
            CATEGORIES[key]["subs"].items()
        ),

        f"postsub_{key}_"
    )

    rows.append([
        {
            "text":
                "⬅️ Назад",
            "callback_data":
                "post"
        }
    ])

    return rows


def post_type_menu(key):

    types = CATEGORIES[key]["types"]

    rows = []

    for i in range(
        0,
        len(types),
        2
    ):

        rows.append([

            {
                "text": label,
                "callback_data":
                    f"posttype_{type_key}"
            }

            for label, type_key
            in types[i:i + 2]
        ])

    rows.append([
        {
            "text":
                "⬅️ Назад",
            "callback_data":
                f"postcat_{key}"
        }
    ])

    return rows


def currency_menu():
    return [
        [
            {
                "text": "🇺🇸 USD ($)",
                "callback_data": "currency_USD"
            },
            {
                "text": "🇬🇪 GEL (₾)",
                "callback_data": "currency_GEL"
            }
        ],
        [
            {
                "text": "🇪🇺 EUR (€)",
                "callback_data": "currency_EUR"
            },
            {
                "text": "🤝 Договорная",
                "callback_data": "currency_NEGOTIABLE"
            }
        ],
        [
            {
                "text": "🎁 Бесплатно",
                "callback_data": "currency_FREE"
            }
        ],
        [
            {
                "text": "⬅️ Назад",
                "callback_data": "back_to_post"
            }
        ]
    ]


def edit_menu():
    return [
        [
            {
                "text": "📋 Характеристики",
                "callback_data": "edit_details"
            }
        ],
        [
            {
                "text": "💰 Цена",
                "callback_data": "edit_price"
            }
        ],
        [
            {
                "text": "📍 Район",
                "callback_data": "edit_district"
            }
        ],
        [
            {
                "text": "📝 Описание",
                "callback_data": "edit_description"
            }
        ],
        [
            {
                "text": "📸 Фото",
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
                "text": "⬅️ Вернуться к просмотру",
                "callback_data": "back_preview"
            }
        ]
    ]


def blank_listing():
    return {
        "category": None,
        "type": None,
        "subcategory": None,
        "fields": {},
        "currency": None,
        "amount": None,
        "district": None,
        "description": None,
        "photos": [],
        "contact": None
    }


def start_post(user_id, category):
    states[user_id] = {
        "step": "post_type",
        "data": blank_listing()
    }

    states[user_id]["data"]["category"] = category

    return send_message(
        user_id,
        CATEGORIES[category]["title"],
        post_type_menu(category)
    )


def ask(user_id, text):
    return send_message(
        user_id,
        text
    )


def ask_detail(user_id):
    state = states.get(user_id)

    if not state:
        return

    data = state["data"]
    fields = get_fields(data)

    if not fields:
        state["step"] = "price_currency"

        return send_message(
            user_id,
            "💰 <b>Укажите цену</b>\n\nВыберите валюту:",
            currency_menu()
        )

    index = state.get("field_index", 0)

    if index >= len(fields):
        state["step"] = "price_currency"

        return send_message(
            user_id,
            "💰 <b>Укажите цену</b>\n\nВыберите валюту:",
            currency_menu()
        )

    field_key, field_label = fields[index]

    state["current_field"] = field_key
    state["step"] = "detail"

    return ask(
        user_id,
        f"✏️ <b>{field_label}</b>\n\n"
        f"Напишите значение:"
    )


def ask_price(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "price_currency"

    return send_message(
        user_id,
        "💰 <b>Цена</b>\n\nВыберите валюту:",
        currency_menu()
    )


def ask_amount(user_id):
    state = states.get(user_id)

    if not state:
        return

    if state["data"]["currency"] in (
        "NEGOTIABLE",
        "FREE"
    ):
        return after_price(user_id)

    state["step"] = "price_amount"

    symbol = {
        "USD": "$",
        "GEL": "₾",
        "EUR": "€"
    }.get(
        state["data"]["currency"],
        ""
    )

    return ask(
        user_id,
        f"💰 <b>Введите сумму</b>\n\n"
        f"Например: <b>1200</b>\n"
        f"Валюта: {symbol}"
    )


def after_price(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "district"

    return ask(
        user_id,
        "📍 <b>В каком районе находится объект?</b>\n\n"
        "Например:\n"
        "Новый Бульвар\n"
        "Старый Батуми\n"
        "Аэропорт\n"
        "Агмашенебели"
    )


def ask_district(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "district"

    return ask(
        user_id,
        "📍 <b>Укажите район</b>"
    )


def ask_description(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "description"

    return ask(
        user_id,
        "📝 <b>Добавьте описание</b>\n\n"
        "Напишите всё, что важно указать в объявлении.\n\n"
        "Например:\n"
        "• состояние\n"
        "• комплектация\n"
        "• условия\n"
        "• особенности"
    )


def ask_photos(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "photos"

    return ask(
        user_id,
        "📸 <b>Добавьте фотографии</b>\n\n"
        "Можно отправить до 8 фотографий.\n\n"
        "После отправки фотографий нажмите:\n"
        "✅ <b>Готово</b>",
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


def ask_contact(user_id):
    state = states.get(user_id)

    if not state:
        return

    state["step"] = "contact"

    return ask(
        user_id,
        "📞 <b>Контакт для связи</b>\n\n"
        "Введите номер телефона или @username Telegram."
    )


def make_title(data):
    category = data.get("category")
    subcategory = data.get("subcategory")
    ad_type = data.get("type")
    fields = data.get("fields", {})

    if category == "realestate":

        if subcategory == "apartment":

            rooms = fields.get("rooms")

            if rooms:
                return (
                    f"{TYPE_NAMES.get(ad_type, '')} "
                    f"{rooms}-комнатную квартиру"
                )

            return (
                f"{TYPE_NAMES.get(ad_type, '')} "
                f"квартиру"
            )

        if subcategory == "house":
            return (
                f"{TYPE_NAMES.get(ad_type, '')} дом"
            )

        if subcategory == "room":
            return (
                f"{TYPE_NAMES.get(ad_type, '')} комнату"
            )

        if subcategory == "commercial":
            return (
                f"{TYPE_NAMES.get(ad_type, '')} "
                f"коммерческую недвижимость"
            )

        if subcategory == "land":
            return (
                f"{TYPE_NAMES.get(ad_type, '')} "
                f"земельный участок"
            )

        if subcategory == "garage":
            return (
                f"{TYPE_NAMES.get(ad_type, '')} "
                f"гараж / парковку"
            )

        return "Недвижимость в Батуми"

    if category == "auto":

        if subcategory == "parts":
            part = fields.get("part_name")

            if part:
                return f"Продам {part}"

            return "Продам автозапчасть"

        make_model = fields.get("make_model")

        if not make_model:
            if subcategory == "moto":
                make_model = "мотоцикл"
            else:
                make_model = "автомобиль"

        year = fields.get("year")

        title = f"Продам {make_model}"

        if year:
            title += f" {year}"

        return title

    if category == "tech":

        brand_model = fields.get("brand_model")

        if brand_model:
            return f"Продам {brand_model}"

        return "Продам технику"

    if category == "home":

        item = fields.get("item")

        if item:
            return f"Продам {item}"

        return "Продам товар для дома"

    if category == "kids":

        item = fields.get("item")

        if item:
            return f"Продам {item}"

        return "Продам детский товар"

    if category == "work":

        service = fields.get("service")

        if service:
            return service

        return "Работа и услуги"

    if category == "give":

        item = fields.get("item")

        if item:
            return f"Отдам бесплатно: {item}"

        return "Отдам бесплатно"

    if category == "search":

        requirements = fields.get("requirements")

        if requirements:
            return f"Ищу: {requirements}"

        return "Ищу"

    return "Объявление"


def price_text(data):
    currency = data.get("currency")
    amount = data.get("amount")

    if currency == "FREE":
        return "🎁 <b>Бесплатно</b>"

    if currency == "NEGOTIABLE":
        return "🤝 <b>Цена договорная</b>"

    symbols = {
        "USD": "$",
        "GEL": "₾",
        "EUR": "€"
    }

    symbol = symbols.get(currency, "")

    result = f"{amount} {symbol}"

    if (
        data.get("category") == "realestate"
        and data.get("type") == "rent"
    ):
        result += " / месяц"

    return result


def build_hashtags(data):
    tags = []

    category = data.get("category")
    subcategory = data.get("subcategory")
    ad_type = data.get("type")
    district = data.get("district")

    category_tags = {
        "realestate": "#недвижимость",
        "auto": "#авто",
        "tech": "#техника",
        "home": "#дом",
        "kids": "#детское",
        "work": "#работа",
        "give": "#отдам",
        "search": "#ищу"
    }

    if category in category_tags:
        tags.append(category_tags[category])

    type_tags = {
        "rent": "#сдам",
        "buy": "#куплю",
        "sell": "#продам",
        "search": "#сниму",
        "give": "#отдам",
        "service": "#услуги"
    }

    if ad_type in type_tags:
        tags.append(type_tags[ad_type])

    subcategory_tags = {
        "apartment": "#квартира",
        "house": "#дом",
        "room": "#комната",
        "commercial": "#коммерция",
        "land": "#земля",
        "garage": "#гараж",
        "car": "#авто",
        "moto": "#мото",
        "parts": "#запчасти",
        "phone": "#телефон",
        "computer": "#компьютер",
        "other": "#товары"
    }

    if subcategory in subcategory_tags:
        tags.append(subcategory_tags[subcategory])

    if district:
        district_tag = (
            "#"
            + district
            .lower()
            .replace(" ", "")
            .replace("-", "")
        )

        tags.append(district_tag)

    tags.append("#батум")

    # убираем дубли
    result = []

    for tag in tags:
        if tag not in result:
            result.append(tag)

    return " ".join(result)


def build_listing(data):
    category = data.get("category")
    ad_type = data.get("type")
    subcategory = data.get("subcategory")
    fields = data.get("fields", {})

    category_title = CATEGORIES.get(
        category,
        {}
    ).get(
        "title",
        "Объявление"
    )

    type_name = TYPE_NAMES.get(
        ad_type,
        ""
    )

    title = make_title(data)

    lines = []

    lines.append(
        f"<b>{html.escape(category_title)}</b>"
    )

    if type_name:
        lines.append(
            f"🏷 <b>{html.escape(type_name)}</b>"
        )

    if subcategory:
        sub_name = SUBCATEGORY_NAMES.get(
            subcategory,
            subcategory
        )

        lines.append(
            f"📂 {html.escape(sub_name)}"
        )

    lines.append("")
    lines.append(
        f"<b>{html.escape(title)}</b>"
    )

    lines.append("")

    # Динамические характеристики
    field_labels = dict(
        get_fields(data)
    )

    for key, value in fields.items():

        if not value:
            continue

        label = field_labels.get(
            key,
            key
        )

        lines.append(
            f"• <b>{html.escape(label)}</b>: "
            f"{html.escape(str(value))}"
        )

    lines.append("")

    if data.get("amount") is not None:
        lines.append(
            f"💰 <b>{price_text(data)}</b>"
        )

    if data.get("district"):
        lines.append(
            f"📍 <b>{html.escape(data['district'])}</b>"
        )

    if data.get("description"):
        lines.append("")
        lines.append(
            f"📝 {html.escape(data['description'])}"
        )

    if data.get("contact"):
        lines.append("")
        lines.append(
            f"📞 <b>Контакт:</b> "
            f"{html.escape(data['contact'])}"
        )

    hashtags = build_hashtags(data)

    if hashtags:
        lines.append("")
        lines.append(hashtags)

    return "\n".join(lines)


def preview_keyboard():
    return [
        [
            {
                "text": "✅ Опубликовать объявление",
                "callback_data": "publish"
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


def send_album(chat_id, photos, caption=None):
    if not photos:
        return None

    media = []

    for i, photo_id in enumerate(photos[:MAX_PHOTOS]):

        item = {
            "type": "photo",
            "media": photo_id
        }

        if i == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = "HTML"

        media.append(item)

    return tg(
        "sendMediaGroup",
        {
            "chat_id": chat_id,
            "media": media
        }
    )


def preview(user_id):
    state = states.get(user_id)

    if not state:
        return

    data = state["data"]

    text = build_listing(data)

    photos = data.get("photos", [])

    if photos:
        send_album(
            user_id,
            photos,
            text
        )

        return send_message(
            user_id,
            "👆 <b>Предпросмотр объявления</b>\n\n"
            "Проверьте данные перед публикацией.",
            preview_keyboard()
        )

    return send_message(
        user_id,
        "👁 <b>Предпросмотр объявления</b>\n\n"
        + text,
        preview_keyboard()
    )


def edit_details_menu(user_id):
    state = states.get(user_id)

    if not state:
        return

    fields = get_fields(
        state["data"]
    )

    rows = []

    for key, label in fields:

        rows.append([
            {
                "text": f"✏️ {label}",
                "callback_data": f"editfield_{key}"
            }
        ])

    rows.append([
        {
            "text": "⬅️ Назад",
            "callback_data": "edit_menu"
        }
    ])

    return send_message(
        user_id,
        "📋 <b>Что изменить?</b>",
        rows
    )


def publish(user_id):
    state = states.get(user_id)

    if not state:
        return

    data = state["data"]

    if not CHANNEL_USERNAME:
        return send_message(
            user_id,
            "⚠️ <b>Канал ещё не подключён.</b>\n\n"
            "В Render → Environment добавьте:\n\n"
            "<code>CHANNEL_USERNAME</code>\n\n"
            "Значение должно быть username вашего "
            "канала с символом @."
        )

    text = build_listing(data)

    photos = data.get("photos", [])

    if photos:

        result = send_album(
            CHANNEL_USERNAME,
            photos,
            text
        )

    else:

        result = tg(
            "sendMessage",
            {
                "chat_id": CHANNEL_USERNAME,
                "text": text,
                "parse_mode": "HTML"
            }
        )

    if result.get("ok"):

        send_message(
            user_id,
            "🎉 <b>Объявление опубликовано!</b>\n\n"
            "Оно появится в канале MADLOBA MARKET.",
            [
                [
                    {
                        "text": "🚀 Разместить ещё одно",
                        "callback_data": "new_post"
                    }
                ],
                [
                    {
                        "text": "🏠 Главное меню",
                        "callback_data": "main_menu"
                    }
                ]
            ]
        )

        states.pop(user_id, None)

    else:

        send_message(
            user_id,
            "❌ <b>Не удалось опубликовать объявление.</b>\n\n"
            f"<code>{html.escape(str(result))}</code>"
        ) 


def process_text(user_id, text):
    state = states.get(user_id)

    if not state:
        return send_message(
            user_id,
            "Используйте /start для открытия меню."
        )

    step = state.get("step")
    data = state["data"]

    # =========================
    # ХАРАКТЕРИСТИКИ
    # =========================

    if step == "detail":

        key = state.get("current_field")

        if not key:
            return

        data["fields"][key] = text.strip()

        state["field_index"] = (
            state.get("field_index", 0) + 1
        )

        return ask_detail(user_id)

    # =========================
    # ЦЕНА
    # =========================

    if step == "price_amount":

        clean = (
            text
            .strip()
            .replace(",", ".")
            .replace(" ", "")
        )

        try:
            amount = float(clean)

            if amount < 0:
                raise ValueError

        except ValueError:

            return ask(
                user_id,
                "⚠️ Введите только сумму.\n\n"
                "Например: <b>1200</b>"
            )

        data["amount"] = amount

        return after_price(user_id)

    # =========================
    # РАЙОН
    # =========================

    if step == "district":

        data["district"] = text.strip()

        return ask_description(user_id)

    # =========================
    # ОПИСАНИЕ
    # =========================

    if step == "description":

        data["description"] = text.strip()

        return ask_photos(user_id)

    # =========================
    # КОНТАКТ
    # =========================

    if step == "contact":

        data["contact"] = text.strip()

        state["step"] = "preview"

        return preview(user_id)

    # =========================
    # НЕПОНЯТНЫЙ ШАГ
    # =========================

    return send_message(
        user_id,
        "⚠️ Не понял сообщение.\n\n"
        "Используйте кнопки выше или продолжите "
        "по инструкции."
    )


def handle_callback(user_id, callback_id, callback_data):
    state = states.get(user_id)

    # Всегда закрываем "часики" на кнопке
    tg(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )

    # =========================
    # ГЛАВНОЕ МЕНЮ
    # =========================

    if callback_data == "main_menu":

        states.pop(user_id, None)

        return start_menu(user_id)

    # =========================
    # НОВОЕ ОБЪЯВЛЕНИЕ
    # =========================

    if callback_data in (
        "new_post",
        "post"
    ):

        states[user_id] = {
            "step": "post_category",
            "data": blank_listing()
        }

        return send_message(
            user_id,
            "🚀 <b>Новое объявление</b>\n\n"
            "Выберите категорию:",
            category_menu()
        )

    # =========================
    # ВЫБОР КАТЕГОРИИ
    # =========================

    if callback_data.startswith("postcat_"):

        category = callback_data.replace(
            "postcat_",
            ""
        )

        if category not in CATEGORIES:
            return

        return start_post(
            user_id,
            category
        )

    # =========================
    # ТИП ОБЪЯВЛЕНИЯ
    # =========================

    if callback_data.startswith("posttype_"):

        if not state:
            return

        type_key = callback_data.replace(
            "posttype_",
            ""
        )

        data = state["data"]

        data["type"] = type_key

        category = data["category"]

        # После типа выбираем подкатегорию,
        # если она предусмотрена.
        subcategories = (
            CATEGORIES
            .get(category, {})
            .get("subcategories", [])
        )

        if subcategories:

            state["step"] = "subcategory"

            return send_message(
                user_id,
                "📂 <b>Выберите подкатегорию:</b>",
                subcategory_menu(category)
            )

        state["field_index"] = 0

        return ask_detail(user_id)

    # =========================
    # ПОДКАТЕГОРИЯ
    # =========================

    if callback_data.startswith("subcategory_"):

        if not state:
            return

        value = callback_data.replace(
            "subcategory_",
            ""
        )

        state["data"]["subcategory"] = value

        state["field_index"] = 0

        return ask_detail(user_id)

    # =========================
    # НАЗАД К КАТЕГОРИЯМ
    # =========================

    if callback_data == "back_to_categories":

        states.pop(user_id, None)

        return send_message(
            user_id,
            "📂 <b>Выберите категорию:</b>",
            category_menu()
        )

    # =========================
    # НАЗАД
    # =========================

    if callback_data == "back_to_post":

        if not state:
            return

        return ask_price(user_id)

    # =========================
    # ВАЛЮТА
    # =========================

    if callback_data.startswith("currency_"):

        if not state:
            return

        currency = callback_data.replace(
            "currency_",
            ""
        )

        state["data"]["currency"] = currency

        if currency == "FREE":

            state["data"]["amount"] = None

            return after_price(user_id)

        if currency == "NEGOTIABLE":

            state["data"]["amount"] = None

            return after_price(user_id)

        return ask_amount(user_id)

    # =========================
    # ФОТО ГОТОВО
    # =========================

    if callback_data == "photos_done":

        if not state:
            return

        if not state["data"]["photos"]:

            return ask(
                user_id,
                "📸 Сначала отправьте хотя бы одну фотографию "
                "или нажмите «Пропустить»."
            )

        return ask_contact(user_id)

    # =========================
    # ФОТО ПРОПУСТИТЬ
    # =========================

    if callback_data == "photos_skip":

        if not state:
            return

        state["data"]["photos"] = []

        return ask_contact(user_id)

    # =========================
    # ПРЕДПРОСМОТР
    # =========================

    if callback_data == "back_preview":

        return preview(user_id)

    # =========================
    # РЕДАКТИРОВАНИЕ
    # =========================

    if callback_data == "edit_menu":

        return send_message(
            user_id,
            "✏️ <b>Что хотите изменить?</b>",
            edit_menu()
        )

    if callback_data == "edit_details":

        return edit_details_menu(user_id)

    # =========================
    # ИЗМЕНИТЬ ОТДЕЛЬНОЕ ПОЛЕ
    # =========================

    if callback_data.startswith("editfield_"):

        if not state:
            return

        key = callback_data.replace(
            "editfield_",
            ""
        )

        field_labels = dict(
            get_fields(state["data"])
        )

        if key not in field_labels:
            return

        state["current_field"] = key
        state["step"] = "detail"

        return ask(
            user_id,
            f"✏️ <b>{field_labels[key]}</b>\n\n"
            "Введите новое значение:"
        )

    # =========================
    # ИЗМЕНИТЬ ЦЕНУ
    # =========================

    if callback_data == "edit_price":

        return ask_price(user_id)

    # =========================
    # ИЗМЕНИТЬ РАЙОН
    # =========================

    if callback_data == "edit_district":

        return ask_district(user_id)

    # =========================
    # ИЗМЕНИТЬ ОПИСАНИЕ
    # =========================

    if callback_data == "edit_description":

        return ask_description(user_id)

    # =========================
    # ИЗМЕНИТЬ ФОТО
    # =========================

    if callback_data == "edit_photos":

        if state:
            state["data"]["photos"] = []

        return ask_photos(user_id)

    # =========================
    # ИЗМЕНИТЬ КОНТАКТ
    # =========================

    if callback_data == "edit_contact":

        return ask_contact(user_id)

    # =========================
    # ПУБЛИКАЦИЯ
    # =========================

    if callback_data == "publish":

        return publish(user_id)

    # =========================
    # ОТМЕНА
    # =========================

    if callback_data == "cancel_post":

        states.pop(user_id, None)

        return send_message(
            user_id,
            "❌ <b>Объявление отменено.</b>\n\n"
            "Можно создать новое объявление в любой момент.",
            [
                [
                    {
                        "text": "🚀 Разместить объявление",
                        "callback_data": "new_post"
                    }
                ],
                [
                    {
                        "text": "🏠 Главное меню",
                        "callback_data": "main_menu"
                    }
                ]
            ]
        )


def handle(update):
    # =========================
    # CALLBACK QUERY
    # =========================

    callback = update.get("callback_query")

    if callback:

        user = callback.get("from", {})
        user_id = user.get("id")

        callback_id = callback.get("id")
        callback_data = callback.get(
            "data",
            ""
        )

        return handle_callback(
            user_id,
            callback_id,
            callback_data
        )

    # =========================
    # MESSAGE
    # =========================

    message = update.get("message")

    if not message:
        return

    user = message.get("from", {})
    user_id = user.get("id")

    # =========================
    # PHOTO
    # =========================

    if message.get("photo"):

        state = states.get(user_id)

        if not state:
            return

        if state.get("step") != "photos":
            return

        photos = message["photo"]

        # Берём самое большое доступное фото
        photo_id = photos[-1]["file_id"]

        current = state["data"].get(
            "photos",
            []
        )

        if len(current) < MAX_PHOTOS:

            current.append(photo_id)

            state["data"]["photos"] = current

        count = len(
            state["data"]["photos"]
        )

        return send_message(
            user_id,
            f"📸 Фото добавлено.\n\n"
            f"Сейчас: <b>{count}/{MAX_PHOTOS}</b>\n\n"
            "Можете отправить ещё или нажать "
            "«✅ Готово».",
            [
                [
                    {
                        "text": "✅ Готово",
                        "callback_data": "photos_done"
                    }
                ],
                [
                    {
                        "text": "❌ Очистить фото",
                        "callback_data": "edit_photos"
                    }
                ]
            ]
        )

    # =========================
    # TEXT
    # =========================

    text = message.get("text")

    if not text:
        return

    # =========================
    # /START
    # =========================

    if text.startswith("/start"):

        states.pop(user_id, None)

        return start_menu(user_id)

    # =========================
    # /CATEGORIES
    # =========================

    if text.startswith("/categories"):

        return send_message(
            user_id,
            "📂 <b>Категории:</b>",
            category_menu()
        )

    # =========================
    # /POST
    # =========================

    if text.startswith("/post"):

        states[user_id] = {
            "step": "post_category",
            "data": blank_listing()
        }

        return send_message(
            user_id,
            "🚀 <b>Разместить объявление</b>\n\n"
            "Выберите категорию:",
            category_menu()
        )

    # =========================
    # /RULES
    # =========================

    if text.startswith("/rules"):

        return send_message(
            user_id,
            "📋 <b>Правила MADLOBA MARKET</b>\n\n"
            "1. Размещайте реальные объявления.\n"
            "2. Не публикуйте запрещённые товары и услуги.\n"
            "3. Не вводите пользователей в заблуждение.\n"
            "4. Указывайте актуальную цену.\n"
            "5. Указывайте корректный контакт.\n"
            "6. Администрация может удалить объявление, "
            "нарушающее правила."
        )

    # =========================
    # /HELP
    # =========================

    if text.startswith("/help"):

        return send_message(
            user_id,
            "ℹ️ <b>Помощь</b>\n\n"
            "/start — главное меню\n"
            "/categories — категории\n"
            "/post — разместить объявление\n"
            "/rules — правила\n"
            "/help — помощь"
        )

    # =========================
    # ОБЫЧНЫЙ ТЕКСТ
    # =========================

    return process_text(
        user_id,
        text
    )


@app.route(
    "/",
    methods=["GET"]
)
def index():

    return (
        "MADLOBA MARKET BOT is running.",
        200
    )


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    )

    if not update:
        return "OK", 200

    try:

        handle(update)

    except Exception as e:

        print(
            "ERROR:",
            repr(e)
        )

    return "OK", 200


def setup_bot():

    # Проверяем токен
    result = tg(
        "getMe"
    )

    print(
        "TELEGRAM getMe:",
        result
    )

    # Команды бота
    commands = [
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

    tg(
        "setMyCommands",
        {
            "commands": commands
        }
    )

    # Webhook Render
    render_url = os.environ.get(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if render_url:

        webhook_url = (
            render_url.rstrip("/")
            + "/webhook"
        )

        result = tg(
            "setWebhook",
            {
                "url": webhook_url
            }
        )

        print(
            "WEBHOOK:",
            result
        )


# ВАЖНО:
# Render запускает Flask через Gunicorn.
# Поэтому setup_bot() должен выполняться
# при загрузке модуля.

try:

    setup_bot()

except Exception as e:

    print(
        "STARTUP ERROR:",
        repr(e)
    )


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
