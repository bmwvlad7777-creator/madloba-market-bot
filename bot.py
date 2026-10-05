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

# Временные состояния пользователей
states = {}


# ============================================================
# КАТЕГОРИИ
# ============================================================

CATEGORIES = {

    "realestate": {
        "title": "🏠 Недвижимость",

        "types": [
            ("🔑 Сдам", "rent"),
            ("🔎 Сниму", "search"),
            ("🏡 Продам", "sell"),
            ("💰 Куплю", "buy"),
        ],

        "subcategories": [
            ("🏢 Квартира", "apartment"),
            ("🏡 Дом", "house"),
            ("🚪 Комната", "room"),
            ("🏢 Коммерция", "commercial"),
            ("🌳 Земля", "land"),
            ("🚗 Гараж / парковка", "garage"),
        ]
    },

    "auto": {
        "title": "🚗 Авто",

        "types": [
            ("🏷 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],

        "subcategories": [
            ("🚘 Автомобиль", "car"),
            ("🏍 Мото", "moto"),
            ("⚙️ Запчасти", "parts"),
            ("🚙 Другое", "other"),
        ]
    },

    "tech": {
        "title": "📱 Техника",

        "types": [
            ("🏷 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],

        "subcategories": [
            ("📱 Телефон", "phone"),
            ("💻 Компьютер", "computer"),
            ("📺 Телевизор", "tv"),
            ("🎮 Игры / приставки", "gaming"),
            ("⌚ Электроника", "electronics"),
            ("📦 Другое", "other"),
        ]
    },

    "home": {
        "title": "🛋 Дом и мебель",

        "types": [
            ("🏷 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],

        "subcategories": [
            ("🛋 Мебель", "furniture"),
            ("🔌 Бытовая техника", "appliances"),
            ("🏠 Для дома", "home"),
            ("📦 Другое", "other"),
        ]
    },

    "kids": {
        "title": "👶 Детское",

        "types": [
            ("🏷 Продам", "sell"),
            ("🔎 Куплю", "buy"),
        ],

        "subcategories": [
            ("👕 Одежда", "clothes"),
            ("👟 Обувь", "shoes"),
            ("🧸 Игрушки", "toys"),
            ("🚲 Детский транспорт", "transport"),
            ("🪑 Детская мебель", "furniture"),
            ("📦 Другое", "other"),
        ]
    },

    "work": {
        "title": "💼 Работа и услуги",

        "types": [
            ("💼 Работа", "job"),
            ("🛠 Услуги", "service"),
        ],

        "subcategories": [
            ("💼 Вакансия", "vacancy"),
            ("🔎 Ищу работу", "looking"),
            ("🛠 Услуги", "services"),
            ("📦 Другое", "other"),
        ]
    },

    "give": {
        "title": "🎁 Отдам",

        "types": [
            ("🎁 Отдам бесплатно", "give"),
        ],

        "subcategories": [
            ("📦 Вещи", "things"),
            ("👕 Одежда", "clothes"),
            ("🛋 Мебель", "furniture"),
            ("👶 Детское", "kids"),
            ("📦 Другое", "other"),
        ]
    },

    "search": {
        "title": "🔎 Ищу",

        "types": [
            ("🔎 Ищу", "search"),
        ],

        "subcategories": [
            ("🏠 Недвижимость", "realestate"),
            ("🚗 Авто", "auto"),
            ("📱 Техника", "tech"),
            ("🛋 Дом", "home"),
            ("👶 Детское", "kids"),
            ("📦 Другое", "other"),
        ]
    }
}


# ============================================================
# ДИНАМИЧЕСКИЕ ХАРАКТЕРИСТИКИ
# ============================================================

SECTION_FIELDS = {

    # -------------------------
    # НЕДВИЖИМОСТЬ
    # -------------------------

    "apartment": [
        ("rooms", "Количество комнат"),
        ("area", "Площадь, м²"),
        ("floor", "Этаж"),
        ("total_floors", "Этажность дома"),
        ("condition", "Состояние"),
    ],

    "house": [
        ("house_area", "Площадь дома, м²"),
        ("land_area", "Площадь участка, м²"),
        ("floors", "Количество этажей"),
        ("rooms", "Количество комнат"),
        ("condition", "Состояние"),
    ],

    "room": [
        ("rooms", "Количество комнат"),
        ("area", "Площадь, м²"),
        ("floor", "Этаж"),
        ("condition", "Состояние"),
    ],

    "commercial": [
        ("area", "Площадь, м²"),
        ("floor", "Этаж"),
        ("purpose", "Назначение"),
        ("condition", "Состояние"),
    ],

    "land": [
        ("land_area", "Площадь участка, м²"),
        ("purpose", "Назначение земли"),
    ],

    "garage": [
        ("area", "Площадь, м²"),
        ("type", "Тип"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # АВТО
    # -------------------------

    "car": [
        ("make_model", "Марка и модель"),
        ("year", "Год выпуска"),
        ("mileage", "Пробег, км"),
        ("engine", "Двигатель"),
        ("transmission", "Коробка передач"),
        ("condition", "Состояние"),
    ],

    "moto": [
        ("make_model", "Марка и модель"),
        ("year", "Год выпуска"),
        ("mileage", "Пробег, км"),
        ("engine", "Объём двигателя"),
        ("condition", "Состояние"),
    ],

    "parts": [
        ("part_name", "Название запчасти"),
        ("compatible", "Подходит для"),
        ("condition", "Состояние"),
    ],

    "auto_other": [
        ("make_model", "Марка / модель"),
        ("year", "Год"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # ТЕХНИКА
    # -------------------------

    "phone": [
        ("brand_model", "Модель"),
        ("memory", "Память"),
        ("condition", "Состояние"),
    ],

    "computer": [
        ("brand_model", "Модель"),
        ("processor", "Процессор"),
        ("ram", "Оперативная память"),
        ("storage", "Память / SSD"),
        ("condition", "Состояние"),
    ],

    "tv": [
        ("brand_model", "Модель"),
        ("size", "Диагональ"),
        ("condition", "Состояние"),
    ],

    "gaming": [
        ("brand_model", "Модель"),
        ("memory", "Комплектация"),
        ("condition", "Состояние"),
    ],

    "electronics": [
        ("brand_model", "Модель"),
        ("condition", "Состояние"),
    ],

    "tech_other": [
        ("brand_model", "Название / модель"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # ДОМ
    # -------------------------

    "furniture": [
        ("item", "Что продаёте"),
        ("material", "Материал"),
        ("dimensions", "Размеры"),
        ("condition", "Состояние"),
    ],

    "appliances": [
        ("item", "Что продаёте"),
        ("brand_model", "Марка / модель"),
        ("condition", "Состояние"),
    ],

    "home": [
        ("item", "Что продаёте"),
        ("dimensions", "Размеры"),
        ("condition", "Состояние"),
    ],

    "home_other": [
        ("item", "Что продаёте"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # ДЕТСКОЕ
    # -------------------------

    "clothes": [
        ("item", "Что продаёте"),
        ("size", "Размер"),
        ("condition", "Состояние"),
    ],

    "shoes": [
        ("item", "Что продаёте"),
        ("size", "Размер"),
        ("condition", "Состояние"),
    ],

    "toys": [
        ("item", "Что продаёте"),
        ("age", "Возраст"),
        ("condition", "Состояние"),
    ],

    "transport": [
        ("item", "Что продаёте"),
        ("age", "Возраст ребёнка"),
        ("condition", "Состояние"),
    ],

    "kids_furniture": [
        ("item", "Что продаёте"),
        ("dimensions", "Размеры"),
        ("condition", "Состояние"),
    ],

    "kids_other": [
        ("item", "Что продаёте"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # РАБОТА
    # -------------------------

    "vacancy": [
        ("service", "Название вакансии"),
        ("experience", "Опыт"),
        ("salary", "Зарплата"),
        ("schedule", "График"),
    ],

    "looking": [
        ("service", "Кем ищете работу"),
        ("experience", "Опыт"),
        ("schedule", "Желаемый график"),
    ],

    "services": [
        ("service", "Какие услуги"),
        ("experience", "Опыт"),
    ],

    "work_other": [
        ("service", "Описание"),
    ],

    # -------------------------
    # ОТДАМ
    # -------------------------

    "give_things": [
        ("item", "Что отдаёте"),
        ("condition", "Состояние"),
    ],

    "give_clothes": [
        ("item", "Что отдаёте"),
        ("size", "Размер"),
        ("condition", "Состояние"),
    ],

    "give_furniture": [
        ("item", "Что отдаёте"),
        ("dimensions", "Размеры"),
        ("condition", "Состояние"),
    ],

    "give_kids": [
        ("item", "Что отдаёте"),
        ("age", "Возраст"),
        ("condition", "Состояние"),
    ],

    "give_other": [
        ("item", "Что отдаёте"),
        ("condition", "Состояние"),
    ],

    # -------------------------
    # ИЩУ
    # -------------------------

    "search_realestate": [
        ("requirements", "Что ищете"),
        ("rooms", "Количество комнат"),
        ("area", "Желаемая площадь"),
    ],

    "search_auto": [
        ("requirements", "Что ищете"),
        ("year", "Желаемый год"),
    ],

    "search_tech": [
        ("requirements", "Что ищете"),
    ],

    "search_home": [
        ("requirements", "Что ищете"),
    ],

    "search_kids": [
        ("requirements", "Что ищете"),
    ],

    "search_other": [
        ("requirements", "Что ищете"),
    ]
}


# ============================================================
# НАЗВАНИЯ
# ============================================================

TYPE_NAMES = {
    "rent": "Сдам",
    "search": "Сниму",
    "sell": "Продам",
    "buy": "Куплю",
    "give": "Отдам бесплатно",
    "job": "Работа",
    "service": "Услуги"
}


SUBCATEGORY_NAMES = {

    "apartment": "Квартира",
    "house": "Дом",
    "room": "Комната",
    "commercial": "Коммерция",
    "land": "Земля",
    "garage": "Гараж / парковка",

    "car": "Автомобиль",
    "moto": "Мото",
    "parts": "Запчасти",
    "other": "Другое",

    "phone": "Телефон",
    "computer": "Компьютер",
    "tv": "Телевизор",
    "gaming": "Игры / приставки",
    "electronics": "Электроника",

    "furniture": "Мебель",
    "appliances": "Бытовая техника",
    "home": "Для дома",

    "clothes": "Одежда",
    "shoes": "Обувь",
    "toys": "Игрушки",
    "transport": "Детский транспорт",

    "vacancy": "Вакансия",
    "looking": "Ищу работу",
    "services": "Услуги",

    "things": "Вещи",
    "kids": "Детское",

    "realestate": "Недвижимость",
    "auto": "Авто",
    "tech": "Техника"
}


# ============================================================
# TELEGRAM API
# ============================================================

def tg(method, data=None):

    try:

        response = requests.post(
            f"{API}/{method}",
            json=data or {},
            timeout=30
        )

        return response.json()

    except Exception as e:

        print(
            "Telegram API error:",
            repr(e)
        )

        return {
            "ok": False,
            "description": str(e)
        }


def send_message(
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

    return tg(
        "sendMessage",
        data
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

def start_menu(user_id):

    return send_message(

        user_id,

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

        "<b>Купи • Продай • Сдай • Найди</b>",

        [
            [
                {
                    "text": "🚀 РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ",
                    "callback_data": "new_post"
                }
            ],

            [
                {
                    "text": "🏠 Недвижимость",
                    "callback_data": "postcat_realestate"
                },
                {
                    "text": "🚗 Авто",
                    "callback_data": "postcat_auto"
                }
            ],

            [
                {
                    "text": "📱 Техника",
                    "callback_data": "postcat_tech"
                },
                {
                    "text": "🛋 Дом и мебель",
                    "callback_data": "postcat_home"
                }
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
            ]
        ]
    )


def category_menu():

    rows = []

    keys = list(CATEGORIES.keys())

    for i in range(
        0,
        len(keys),
        2
    ):

        row = []

        for key in keys[i:i + 2]:

            row.append(
                {
                    "text": CATEGORIES[key]["title"],
                    "callback_data": f"postcat_{key}"
                }
            )

        rows.append(row)

    rows.append([
        {
            "text": "⬅️ Главное меню",
            "callback_data": "main_menu"
        }
    ])

    return rows


# ============================================================
# МЕНЮ ТИПОВ
# ============================================================

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
                "callback_data": f"posttype_{type_key}"
            }

            for label, type_key
            in types[i:i + 2]
        ])

    rows.append([
        {
            "text": "⬅️ Назад",
            "callback_data": "back_to_categories"
        }
    ])

    return rows


# ============================================================
# МЕНЮ ПОДКАТЕГОРИЙ
# ============================================================

def subcategory_menu(category):

    subcategories = CATEGORIES.get(
        category,
        {}
    ).get(
        "subcategories",
        []
    )

    rows = []

    for i in range(
        0,
        len(subcategories),
        2
    ):

        rows.append([

            {
                "text": label,
                "callback_data":
                    f"subcategory_{subcategory}"
            }

            for label, subcategory
            in subcategories[i:i + 2]
        ])

    rows.append([
        {
            "text": "⬅️ Назад",
            "callback_data":
                f"postcat_{category}"
        }
    ])

    return rows


# ============================================================
# ПОЛЯ
# ============================================================

def get_fields(data):

    category = data.get("category")
    subcategory = data.get("subcategory")

    if category == "auto" and subcategory == "other":
        return SECTION_FIELDS["auto_other"]

    if category == "tech" and subcategory == "other":
        return SECTION_FIELDS["tech_other"]

    if category == "home" and subcategory == "other":
        return SECTION_FIELDS["home_other"]

    if category == "kids" and subcategory == "furniture":
        return SECTION_FIELDS["kids_furniture"]

    if category == "kids" and subcategory == "other":
        return SECTION_FIELDS["kids_other"]

    if category == "work" and subcategory == "other":
        return SECTION_FIELDS["work_other"]

    if category == "give" and subcategory:
        return SECTION_FIELDS.get(
            f"give_{subcategory}",
            SECTION_FIELDS["give_other"]
        )

    if category == "search" and subcategory:
        return SECTION_FIELDS.get(
            f"search_{subcategory}",
            SECTION_FIELDS["search_other"]
        )

    return SECTION_FIELDS.get(
        subcategory,
        []
    )


# ============================================================
# ВАЛЮТА
# ============================================================

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


# ============================================================
# РЕДАКТИРОВАНИЕ
# ============================================================

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


# ============================================================
# НОВОЕ ОБЪЯВЛЕНИЕ
# ============================================================

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


def start_post(
    user_id,
    category
):

    states[user_id] = {

        "step": "post_type",

        "data": blank_listing()
    }

    states[user_id]["data"]["category"] = category

    return send_message(

        user_id,

        f"{CATEGORIES[category]['title']}\n\n"
        "<b>Какое объявление размещаете?</b>",

        post_type_menu(category)
    )


# ============================================================
# ЗАПРОС ХАРАКТЕРИСТИК
# ============================================================

def ask_detail(user_id):

    state = states.get(user_id)

    if not state:
        return

    data = state["data"]

    fields = get_fields(data)

    if not fields:

        return ask_price(user_id)

    index = state.get(
        "field_index",
        0
    )

    if index >= len(fields):

        return ask_price(user_id)

    field_key, field_label = fields[index]

    state["current_field"] = field_key

    state["step"] = "detail"

    return send_message(

        user_id,

        f"✏️ <b>{field_label}</b>\n\n"
        "Напишите значение:"
    )


# ============================================================
# ЦЕНА
# ============================================================

def ask_price(user_id):

    state = states.get(user_id)

    if not state:
        return

    state["step"] = "price_currency"

    return send_message(

        user_id,

        "💰 <b>Укажите цену</b>\n\n"
        "Выберите валюту:",

        currency_menu()
    )


def ask_amount(user_id):

    state = states.get(user_id)

    if not state:
        return

    currency = state["data"]["currency"]

    if currency in (
        "NEGOTIABLE",
        "FREE"
    ):

        return after_price(user_id)

    state["step"] = "price_amount"

    symbols = {

        "USD": "$",
        "GEL": "₾",
        "EUR": "€"
    }

    symbol = symbols.get(
        currency,
        ""
    )

    return send_message(

        user_id,

        "💰 <b>Введите сумму</b>\n\n"
        f"Например: <b>1200</b> {symbol}"
    )


def after_price(user_id):

    state = states.get(user_id)

    if not state:
        return

    state["step"] = "district"

    return send_message(

        user_id,

        "📍 <b>Укажите район</b>\n\n"
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

    return send_message(
        user_id,
        "📍 <b>Укажите район</b>"
    )


# ============================================================
# ОПИСАНИЕ
# ============================================================

def ask_description(user_id):

    state = states.get(user_id)

    if not state:
        return

    state["step"] = "description"

    return send_message(

        user_id,

        "📝 <b>Добавьте описание</b>\n\n"

        "Напишите всё, что важно указать:\n"
        "• состояние\n"
        "• комплектация\n"
        "• условия\n"
        "• особенности"
    )


# ============================================================
# ФОТО
# ============================================================

def ask_photos(user_id):

    state = states.get(user_id)

    if not state:
        return

    state["step"] = "photos"

    return send_message(

        user_id,

        "📸 <b>Добавьте фотографии</b>\n\n"
        "Можно отправить до 8 фотографий.\n\n"
        "После отправки нажмите «Готово».",

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


# ============================================================
# КОНТАКТ
# ============================================================

def ask_contact(user_id):

    state = states.get(user_id)

    if not state:
        return

    state["step"] = "contact"

    return send_message(

        user_id,

        "📞 <b>Контакт для связи</b>\n\n"
        "Введите номер телефона или "
        "@username Telegram."
    )


# ============================================================
# АВТОМАТИЧЕСКИЙ ЗАГОЛОВОК
# ============================================================

def make_title(data):

    category = data.get("category")

    subcategory = data.get(
        "subcategory"
    )

    ad_type = data.get("type")

    fields = data.get(
        "fields",
        {}
    )

    type_name = TYPE_NAMES.get(
        ad_type,
        ""
    )

    # -------------------------
    # НЕДВИЖИМОСТЬ
    # -------------------------

    if category == "realestate":

        if subcategory == "apartment":

            rooms = fields.get("rooms")

            if rooms:

                return (
                    f"{type_name} "
                    f"{rooms}-комнатную квартиру"
                )

            return f"{type_name} квартиру"

        if subcategory == "house":

            return f"{type_name} дом"

        if subcategory == "room":

            return f"{type_name} комнату"

        if subcategory == "commercial":

            return (
                f"{type_name} "
                f"коммерческую недвижимость"
            )

        if subcategory == "land":

            return (
                f"{type_name} "
                f"земельный участок"
            )

        if subcategory == "garage":

            return (
                f"{type_name} "
                f"гараж / парковку"
            )

        return "Недвижимость в Батуми"

    # -------------------------
    # АВТО
    # -------------------------

    if category == "auto":

        if subcategory == "parts":

            part = fields.get(
                "part_name"
            )

            if part:
                return f"Продам {part}"

            return "Продам автозапчасть"

        make_model = fields.get(
            "make_model"
        )

        if not make_model:

            if subcategory == "moto":
                make_model = "мотоцикл"

            else:
                make_model = "автомобиль"

        year = fields.get("year")

        title = (
            f"Продам {make_model}"
        )

        if year:
            title += f" {year}"

        return title

    # -------------------------
    # ТЕХНИКА
    # -------------------------

    if category == "tech":

        model = fields.get(
            "brand_model"
        )

        if model:
            return f"Продам {model}"

        return "Продам технику"

    # -------------------------
    # ДОМ
    # -------------------------

    if category == "home":

        item = fields.get("item")

        if item:
            return f"Продам {item}"

        return "Продам товар для дома"

    # -------------------------
    # ДЕТСКОЕ
    # -------------------------

    if category == "kids":

        item = fields.get("item")

        if item:
            return f"Продам {item}"

        return "Продам детский товар"

    # -------------------------
    # РАБОТА
    # -------------------------

    if category == "work":

        service = fields.get(
            "service"
        )

        if service:
            return service

        return "Работа и услуги"

    # -------------------------
    # ОТДАМ
    # -------------------------

    if category == "give":

        item = fields.get(
            "item"
        )

        if item:
            return (
                f"Отдам бесплатно: "
                f"{item}"
            )

        return "Отдам бесплатно"

    # -------------------------
    # ИЩУ
    # -------------------------

    if category == "search":

        requirements = fields.get(
            "requirements"
        )

        if requirements:
            return f"Ищу: {requirements}"

        return "Ищу"

    return "Объявление"


# ============================================================
# ЦЕНА В ТЕКСТЕ
# ============================================================

def price_text(data):

    currency = data.get(
        "currency"
    )

    amount = data.get(
        "amount"
    )

    if currency == "FREE":

        return "🎁 <b>Бесплатно</b>"

    if currency == "NEGOTIABLE":

        return "🤝 <b>Цена договорная</b>"

    symbols = {

        "USD": "$",
        "GEL": "₾",
        "EUR": "€"
    }

    symbol = symbols.get(
        currency,
        ""
    )

    if amount is None:
        return ""

    if isinstance(
        amount,
        float
    ) and amount.is_integer():

        amount = int(amount)

    result = (
        f"{amount} {symbol}"
    )

    if (
        data.get("category")
        == "realestate"
        and data.get("type")
        == "rent"
    ):

        result += " / месяц"

    return result


# ============================================================
# ХЭШТЕГИ
# ============================================================

def build_hashtags(data):

    tags = []

    category = data.get(
        "category"
    )

    subcategory = data.get(
        "subcategory"
    )

    ad_type = data.get(
        "type"
    )

    district = data.get(
        "district"
    )

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

        tags.append(
            category_tags[category]
        )

    type_tags = {

        "rent": "#сдам",
        "buy": "#куплю",
        "sell": "#продам",
        "search": "#сниму",
        "give": "#отдам",
        "job": "#работа",
        "service": "#услуги"
    }

    if ad_type in type_tags:

        tags.append(
            type_tags[ad_type]
        )

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

        "furniture": "#мебель",
        "clothes": "#одежда",
        "shoes": "#обувь",
        "toys": "#игрушки"
    }

    if subcategory in subcategory_tags:

        tags.append(
            subcategory_tags[subcategory]
        )

    if district:

        district_tag = (

            "#"
            + district
            .lower()
            .replace(" ", "")
            .replace("-", "")
        )

        tags.append(
            district_tag
        )

    tags.append("#батум")

    result = []

    for tag in tags:

        if tag not in result:

            result.append(tag)

    return " ".join(result)


# ============================================================
# ФОРМИРОВАНИЕ ОБЪЯВЛЕНИЯ
# ============================================================

def build_listing(data):

    category = data.get(
        "category"
    )

    ad_type = data.get(
        "type"
    )

    subcategory = data.get(
        "subcategory"
    )

    fields = data.get(
        "fields",
        {}
    )

    category_title = (

        CATEGORIES
        .get(category, {})
        .get(
            "title",
            "Объявление"
        )
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

    price = price_text(data)

    if price:

        lines.append(price)

    if data.get("district"):

        lines.append(

            f"📍 <b>"
            f"{html.escape(data['district'])}"
            f"</b>"
        )

    if data.get("description"):

        lines.append("")

        lines.append(

            f"📝 "
            f"{html.escape(data['description'])}"
        )

    if data.get("contact"):

        lines.append("")

        lines.append(

            f"📞 <b>Контакт:</b> "
            f"{html.escape(data['contact'])}"
        )

    hashtags = build_hashtags(
        data
    )

    if hashtags:

        lines.append("")

        lines.append(
            hashtags
        )

    return "\n".join(lines)


# ============================================================
# КНОПКИ ПРЕДПРОСМОТРА
# ============================================================

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


# ============================================================
# ОТПРАВКА АЛЬБОМА
# ============================================================

def send_album(
    chat_id,
    photos,
    caption=None
):

    if not photos:
        return None

    media = []

    for i, photo_id in enumerate(
        photos[:MAX_PHOTOS]
    ):

        item = {

            "type": "photo",

            "media": photo_id
        }

        if (
            i == 0
            and caption
        ):

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


# ============================================================
# ПРЕДПРОСМОТР
# ============================================================

def preview(user_id):

    state = states.get(
        user_id
    )

    if not state:
        return

    data = state["data"]

    text = build_listing(
        data
    )

    photos = data.get(
        "photos",
        []
    )

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


# ============================================================
# РЕДАКТИРОВАНИЕ ХАРАКТЕРИСТИК
# ============================================================

def edit_details_menu(user_id):

    state = states.get(
        user_id
    )

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
                "callback_data":
                    f"editfield_{key}"
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


# ============================================================
# ПУБЛИКАЦИЯ
# ============================================================

def publish(user_id):

    state = states.get(
        user_id
    )

    if not state:
        return

    data = state["data"]

    if not CHANNEL_USERNAME:

        return send_message(

            user_id,

            "⚠️ <b>Канал ещё не подключён.</b>\n\n"

            "В Render → Environment добавьте:\n\n"

            "<code>CHANNEL_USERNAME</code>\n\n"

            "Значение — username вашего "
            "канала с символом @."
        )

    text = build_listing(
        data
    )

    photos = data.get(
        "photos",
        []
    )

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
                "chat_id":
                    CHANNEL_USERNAME,

                "text":
                    text,

                "parse_mode":
                    "HTML"
            }
        )

    if result.get("ok"):

        send_message(

            user_id,

            "🎉 <b>Объявление опубликовано!</b>\n\n"

            "Оно появится в канале "
            "MADLOBA MARKET.",

            [
                [
                    {
                        "text":
                            "🚀 Разместить ещё одно",
                        "callback_data":
                            "new_post"
                    }
                ],

                [
                    {
                        "text":
                            "🏠 Главное меню",
                        "callback_data":
                            "main_menu"
                    }
                ]
            ]
        )

        states.pop(
            user_id,
            None
        )

    else:

        send_message(

            user_id,

            "❌ <b>Не удалось опубликовать "
            "объявление.</b>\n\n"

            f"<code>"
            f"{html.escape(str(result))}"
            f"</code>"
        )


# ============================================================
# ОБРАБОТКА ТЕКСТА
# ============================================================

def process_text(
    user_id,
    text
):

    state = states.get(
        user_id
    )

    if not state:

        return send_message(

            user_id,

            "Используйте /start "
            "для открытия меню."
        )

    step = state.get(
        "step"
    )

    data = state["data"]

    # -------------------------
    # ХАРАКТЕРИСТИКИ
    # -------------------------

    if step == "detail":

        key = state.get(
            "current_field"
        )

        if not key:
            return

        data["fields"][key] = (
            text.strip()
        )

        state["field_index"] = (

            state.get(
                "field_index",
                0
            ) + 1
        )

        return ask_detail(
            user_id
        )

    # -------------------------
    # ЦЕНА
    # -------------------------

    if step == "price_amount":

        clean = (

            text
            .strip()
            .replace(",", ".")
            .replace(" ", "")
        )

        try:

            amount = float(
                clean
            )

            if amount < 0:
                raise ValueError

        except ValueError:

            return send_message(

                user_id,

                "⚠️ <b>Введите только сумму.</b>\n\n"
                "Например: <b>1200</b>"
            )

        data["amount"] = amount

        return after_price(
            user_id
        )

    # -------------------------
    # РАЙОН
    # -------------------------

    if step == "district":

        data["district"] = (
            text.strip()
        )

        return ask_description(
            user_id
        )

    # -------------------------
    # ОПИСАНИЕ
    # -------------------------

    if step == "description":

        data["description"] = (
            text.strip()
        )

        return ask_photos(
            user_id
        )

    # -------------------------
    # КОНТАКТ
    # -------------------------

    if step == "contact":

        data["contact"] = (
            text.strip()
        )

        state["step"] = "preview"

        return preview(
            user_id
        )

    return send_message(

        user_id,

        "⚠️ Не понял сообщение.\n\n"
        "Продолжите по инструкции."
    )


# ============================================================
# CALLBACK-КНОПКИ
# ============================================================

def handle_callback(
    user_id,
    callback_id,
    callback_data
):

    state = states.get(
        user_id
    )

    # Убираем часики с кнопки
    tg(

        "answerCallbackQuery",

        {
            "callback_query_id":
                callback_id
        }
    )

    # -------------------------
    # ГЛАВНОЕ МЕНЮ
    # -------------------------

    if callback_data == "main_menu":

        states.pop(
            user_id,
            None
        )

        return start_menu(
            user_id
        )

    # -------------------------
    # НОВОЕ ОБЪЯВЛЕНИЕ
    # -------------------------

    if callback_data in (
        "new_post",
        "post"
    ):

        states[user_id] = {

            "step":
                "post_category",

            "data":
                blank_listing()
        }

        return send_message(

            user_id,

            "🚀 <b>Новое объявление</b>\n\n"
            "Выберите категорию:",

            category_menu()
        )

    # -------------------------
    # КАТЕГОРИЯ
    # -------------------------

    if callback_data.startswith(
        "postcat_"
    ):

        category = (
            callback_data
            .replace(
                "postcat_",
                ""
            )
        )

        if category not in CATEGORIES:
            return

        return start_post(
            user_id,
            category
        )

    # -------------------------
    # ТИП
    # -------------------------

    if callback_data.startswith(
        "posttype_"
    ):

        if not state:
            return

        type_key = (

            callback_data
            .replace(
                "posttype_",
                ""
            )
        )

        data = state["data"]

        data["type"] = type_key

        category = data["category"]

        subcategories = (

            CATEGORIES
            .get(category, {})
            .get(
                "subcategories",
                []
            )
        )

        if subcategories:

            state["step"] = (
                "subcategory"
            )

            return send_message(

                user_id,

                "📂 <b>Выберите подкатегорию:</b>",

                subcategory_menu(
                    category
                )
            )

        state["field_index"] = 0

        return ask_detail(
            user_id
        )

    # -------------------------
    # ПОДКАТЕГОРИЯ
    # -------------------------

    if callback_data.startswith(
        "subcategory_"
    ):

        if not state:
            return

        value = (

            callback_data
            .replace(
                "subcategory_",
                ""
            )
        )

        state["data"]["subcategory"] = (
            value
        )

        state["field_index"] = 0

        return ask_detail(
            user_id
        )

    # -------------------------
    # НАЗАД К КАТЕГОРИЯМ
    # -------------------------

    if callback_data == "back_to_categories":

        states.pop(
            user_id,
            None
        )

        return send_message(

            user_id,

            "📂 <b>Выберите категорию:</b>",

            category_menu()
        )

    # -------------------------
    # НАЗАД К ЦЕНЕ
    # -------------------------

    if callback_data == "back_to_post":

        return ask_price(
            user_id
        )

    # -------------------------
    # ВАЛЮТА
    # -------------------------

    if callback_data.startswith(
        "currency_"
    ):

        if not state:
            return

        currency = (

            callback_data
            .replace(
                "currency_",
                ""
            )
        )

        state["data"]["currency"] = (
            currency
        )

        if currency == "FREE":

            state["data"]["amount"] = None

            return after_price(
                user_id
            )

        if currency == "NEGOTIABLE":

            state["data"]["amount"] = None

            return after_price(
                user_id
            )

        return ask_amount(
            user_id
        )

    # -------------------------
    # ФОТО ГОТОВО
    # -------------------------

    if callback_data == "photos_done":

        if not state:
            return

        if not state["data"]["photos"]:

            return send_message(

                user_id,

                "📸 Сначала отправьте хотя бы "
                "одну фотографию или нажмите "
                "«⏭ Пропустить»."
            )

        return ask_contact(
            user_id
        )

    # -------------------------
    # ФОТО ПРОПУСТИТЬ
    # -------------------------

    if callback_data == "photos_skip":

        if not state:
            return

        state["data"]["photos"] = []

        return ask_contact(
            user_id
        )

    # -------------------------
    # ПРЕДПРОСМОТР
    # -------------------------

    if callback_data == "back_preview":

        return preview(
            user_id
        )

    # -------------------------
    # РЕДАКТИРОВАНИЕ
    # -------------------------

    if callback_data == "edit_menu":

        return send_message(

            user_id,

            "✏️ <b>Что хотите изменить?</b>",

            edit_menu()
        )

    if callback_data == "edit_details":

        return edit_details_menu(
            user_id
        )

    # -------------------------
    # РЕДАКТИРОВАТЬ ПОЛЕ
    # -------------------------

    if callback_data.startswith(
        "editfield_"
    ):

        if not state:
            return

        key = (

            callback_data
            .replace(
                "editfield_",
                ""
            )
        )

        field_labels = dict(
            get_fields(
                state["data"]
            )
        )

        if key not in field_labels:
            return

        state["current_field"] = key

        state["step"] = "detail"

        return send_message(

            user_id,

            f"✏️ <b>{field_labels[key]}</b>\n\n"
            "Введите новое значение:"
        )

    # -------------------------
    # ИЗМЕНИТЬ ЦЕНУ
    # -------------------------

    if callback_data == "edit_price":

        return ask_price(
            user_id
        )

    # -------------------------
    # ИЗМЕНИТЬ РАЙОН
    # -------------------------

    if callback_data == "edit_district":

        return ask_district(
            user_id
        )

    # -------------------------
    # ИЗМЕНИТЬ ОПИСАНИЕ
    # -------------------------

    if callback_data == "edit_description":

        return ask_description(
            user_id
        )

    # -------------------------
    # ИЗМЕНИТЬ ФОТО
    # -------------------------

    if callback_data == "edit_photos":

        if state:

            state["data"]["photos"] = []

        return ask_photos(
            user_id
        )

    # -------------------------
    # ИЗМЕНИТЬ КОНТАКТ
    # -------------------------

    if callback_data == "edit_contact":

        return ask_contact(
            user_id
        )

    # -------------------------
    # ПУБЛИКАЦИЯ
    # -------------------------

    if callback_data == "publish":

        return publish(
            user_id
        )

    # -------------------------
    # ОТМЕНА
    # -------------------------

    if callback_data == "cancel_post":

        states.pop(
            user_id,
            None
        )

        return send_message(

            user_id,

            "❌ <b>Объявление отменено.</b>\n\n"
            "Можно создать новое объявление.",

            [
                [
                    {
                        "text":
                            "🚀 Разместить объявление",
                        "callback_data":
                            "new_post"
                    }
                ],

                [
                    {
                        "text":
                            "🏠 Главное меню",
                        "callback_data":
                            "main_menu"
                    }
                ]
            ]
        )


# ============================================================
# ОБРАБОТКА UPDATE
# ============================================================

def handle(update):

    # -------------------------
    # CALLBACK
    # -------------------------

    callback = update.get(
        "callback_query"
    )

    if callback:

        user = callback.get(
            "from",
            {}
        )

        user_id = user.get(
            "id"
        )

        callback_id = callback.get(
            "id"
        )

        callback_data = callback.get(
            "data",
            ""
        )

        return handle_callback(

            user_id,

            callback_id,

            callback_data
        )

    # -------------------------
    # MESSAGE
    # -------------------------

    message = update.get(
        "message"
    )

    if not message:
        return

    user = message.get(
        "from",
        {}
    )

    user_id = user.get(
        "id"
    )

    # -------------------------
    # ФОТО
    # -------------------------

    if message.get("photo"):

        state = states.get(
            user_id
        )

        if not state:
            return

        if state.get(
            "step"
        ) != "photos":

            return

        photos = message["photo"]

        # Берём самое большое фото
        photo_id = photos[-1]["file_id"]

        current = state["data"].get(
            "photos",
            []
        )

        if len(current) < MAX_PHOTOS:

            current.append(
                photo_id
            )

            state["data"]["photos"] = (
                current
            )

        count = len(
            state["data"]["photos"]
        )

        return send_message(

            user_id,

            f"📸 Фото добавлено.\n\n"
            f"Сейчас: <b>{count}/{MAX_PHOTOS}</b>\n\n"
            "Можете отправить ещё или "
            "нажать «✅ Готово».",

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

    # -------------------------
    # TEXT
    # -------------------------

    text = message.get(
        "text"
    )

    if not text:
        return

    # -------------------------
    # START
    # -------------------------

    if text.startswith(
        "/start"
    ):

        states.pop(
            user_id,
            None
        )

        return start_menu(
            user_id
        )

    # -------------------------
    # CATEGORIES
    # -------------------------

    if text.startswith(
        "/categories"
    ):

        return send_message(

            user_id,

            "📂 <b>Категории:</b>",

            category_menu()
        )

    # -------------------------
    # POST
    # -------------------------

    if text.startswith(
        "/post"
    ):

        states[user_id] = {

            "step":
                "post_category",

            "data":
                blank_listing()
        }

        return send_message(

            user_id,

            "🚀 <b>Разместить объявление</b>\n\n"
            "Выберите категорию:",

            category_menu()
        )

    # -------------------------
    # RULES
    # -------------------------

    if text.startswith(
        "/rules"
    ):

        return send_message(

            user_id,

            "📋 <b>Правила MADLOBA MARKET</b>\n\n"

            "1. Размещайте реальные объявления.\n"
            "2. Не публикуйте запрещённые товары "
            "и услуги.\n"
            "3. Не вводите пользователей "
            "в заблуждение.\n"
            "4. Указывайте актуальную цену.\n"
            "5. Указывайте корректный контакт.\n"
            "6. Администрация может удалить "
            "объявление, нарушающее правила."
        )

    # -------------------------
    # HELP
    # -------------------------

    if text.startswith(
        "/help"
    ):

        return send_message(

            user_id,

            "ℹ️ <b>Помощь</b>\n\n"

            "/start — главное меню\n"
            "/categories — категории\n"
            "/post — разместить объявление\n"
            "/rules — правила\n"
            "/help — помощь"
        )

    # -------------------------
    # ОБЫЧНЫЙ ТЕКСТ
    # -------------------------

    return process_text(
        user_id,
        text
    )


# ============================================================
# FLASK
# ============================================================

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

        handle(
            update
        )

    except Exception as e:

        print(
            "HANDLE ERROR:",
            repr(e)
        )

    return "OK", 200


# ============================================================
# НАСТРОЙКА TELEGRAM
# ============================================================

def setup_bot():

    # Проверяем токен

    result = tg(
        "getMe"
    )

    print(
        "TELEGRAM getMe:",
        result
    )

    # Команды

    commands = [

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

    tg(

        "setMyCommands",

        {
            "commands":
                commands
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
                "url":
                    webhook_url
