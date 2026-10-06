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

# В Render можно указать:
# CHANNEL_USERNAME=@ваш_канал
CHANNEL_USERNAME = os.environ.get(
    "CHANNEL_USERNAME",
    ""
).strip()

# Максимальное количество фотографий
MAX_PHOTOS = 8


app = Flask(__name__)

# Временные данные пользователей.
# Для первой версии этого достаточно.
states = {}


# ============================================================
# КАТЕГОРИИ
# ============================================================

CATEGORIES = {

    # --------------------------------------------------------
    # НЕДВИЖИМОСТЬ
    # --------------------------------------------------------

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

            ("rooms", "🛏 Количество комнат"),
            ("area", "📐 Площадь, м²"),
            ("floor", "🏢 Этаж"),
        ],
    },


    # --------------------------------------------------------
    # АВТО
    # --------------------------------------------------------

    "auto": {

        "name": "🚗 Авто",

        "subs": {

            "cars": "🚘 Легковые автомобили",
            "suv": "🚙 Кроссоверы и SUV",
            "commercial": "🚚 Коммерческий транспорт",
            "moto": "🏍 Мотоциклы",
            "parts": "⚙️ Запчасти",
            "rental": "🔑 Аренда авто",
        },

        "types": [

            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🔑 Сдам", "rent"),
            ("🚗 Ищу", "seek"),
        ],

        "fields": [

            ("make_model", "🚗 Марка и модель"),
            ("year", "📅 Год выпуска"),
            ("mileage", "🛣 Пробег"),
        ],
    },


    # --------------------------------------------------------
    # ТЕХНИКА
    # --------------------------------------------------------

    "tech": {

        "name": "📱 Техника",

        "subs": {

            "phones": "📱 Телефоны и планшеты",
            "computers": "💻 Компьютеры и ноутбуки",
            "tv": "📺 Телевизоры и аудио",
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


    # --------------------------------------------------------
    # ДОМ И МЕБЕЛЬ
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # ДЕТСКОЕ
    # --------------------------------------------------------

    "kids": {

        "name": "👶 Детское",

        "subs": {

            "clothes": "👕 Одежда и обувь",
            "toys": "🧸 Игрушки",
            "strollers": "🍼 Коляски и автокресла",
            "furniture": "🛏 Детская мебель",
            "sports": "⚽️ Детский спорт",
            "other": "🎈 Другое",
        },

        "types": [

            ("💰 Продам", "sell"),
            ("🔎 Куплю", "buy"),
            ("🎁 Отдам", "give"),
        ],

        "fields": [

            ("condition", "✨ Состояние"),
            ("age", "👶 Возраст ребёнка"),
        ],
    },


    # --------------------------------------------------------
    # РАБОТА И УСЛУГИ
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # ОТДАМ
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # ИЩУ
    # --------------------------------------------------------

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


# ============================================================
# НАЗВАНИЯ ТИПОВ
# ============================================================

TYPE_NAMES = {

    "rent": "🔑 Сдам",
    "seek": "🔎 Ищу",
    "sell": "🏡 Продам",
    "buy": "💰 Куплю",
    "give": "🎁 Отдам бесплатно",
    "offer": "💼 Предлагаю",
}


# ============================================================
# ВАЛЮТЫ
# ============================================================

CURRENCIES = {

    "usd": "$",
    "gel": "₾",
    "eur": "€",
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

        print(
            method,
            result
        )

        return result

    except Exception as error:

        print(
            "API ERROR:",
            method,
            repr(error)
        )

        return {

            "ok": False,

            "error":
                str(error)
        }


# ============================================================
# ОТПРАВКА СООБЩЕНИЯ
# ============================================================

def send(
    chat_id,
    text,
    keyboard=None
):

    data = {

        "chat_id":
            chat_id,

        "text":
            text,

        "parse_mode":
            "HTML"
    }

    if keyboard:

        data[
            "reply_markup"
        ] = {

            "inline_keyboard":
                keyboard
        }

    return api(

        "sendMessage",

        data
    )


# ============================================================
# CALLBACK
# ============================================================

def answer(
    callback_id
):

    return api(

        "answerCallbackQuery",

        {

            "callback_query_id":
                callback_id
        }
    )


# ============================================================
# БЕЗОПАСНЫЙ HTML
# ============================================================

def esc(value):

    return html.escape(
        str(value or "")
    )


# ============================================================
# ХЭШТЕГ
# ============================================================

def slug(value):

    value = (
        str(value or "")
        .lower()
        .replace(
            "ё",
            "е"
        )
    )

    return "".join(

        char

        for char in value

        if char.isalnum()
    )


# ============================================================
# УНИКАЛЬНЫЕ ХЭШТЕГИ
# ============================================================

def unique_hashtags(tags):

    result = []
    seen = set()

    for tag in tags:

        tag = str(
            tag or ""
        ).strip()

        if not tag:
            continue

        if not tag.startswith("#"):
            tag = "#" + tag

        key = tag.lower()

        if key not in seen:

            seen.add(key)

            result.append(tag)

    return result


# ============================================================
# КНОПКИ 2 В РЯД
# ============================================================

def pair_buttons(
    items,
    prefix
):

    return [

        [

            {

                "text":
                    label,

                "callback_data":
                    f"{prefix}{key}"
            }

            for key, label
            in items[
                i:i + 2
            ]
        ]

        for i in range(

            0,

            len(items),

            2
        )
    ]


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

def main_menu():

    return [

        [

            {

                "text":
                    "🏠 Недвижимость",

                "callback_data":
                    "cat_realestate"
            },

            {

                "text":
                    "🚗 Авто",

                "callback_data":
                    "cat_auto"
            }
        ],

        [

            {

                "text":
                    "📱 Техника",

                "callback_data":
                    "cat_tech"
            },

            {

                "text":
                    "🛋 Дом и мебель",

                "callback_data":
                    "cat_home"
            }
        ],

        [

            {

                "text":
                    "👶 Детское",

                "callback_data":
                    "cat_kids"
            },

            {

                "text":
                    "💼 Работа и услуги",

                "callback_data":
                    "cat_work"
            }
        ],

        [

            {

                "text":
                    "🎁 Отдам",

                "callback_data":
                    "cat_give"
            },

            {

                "text":
                    "🔎 Ищу",

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


# ============================================================
# ПРОСМОТР КАТЕГОРИИ
# ============================================================

def category_menu(
    key
):

    rows = pair_buttons(

        list(
            CATEGORIES[
                key
            ]["subs"].items()
        ),

        f"browse_{key}_"
    )

    rows.append(

        [

            {

                "text":
                    "📋 Все объявления",

                "callback_data":
                    f"browse_{key}_all"
            }
        ]
    )

    rows.append(

        [

            {

                "text":
                    "⬅️ Главное меню",

                "callback_data":
                    "back_main"
            }
        ]
    )

    return rows


# ============================================================
# КАТЕГОРИЯ ПРИ СОЗДАНИИ
# ============================================================

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

                "text":
                    "❌ Отмена",

                "callback_data":
                    "cancel_post"
            }
        ]
    )

    return rows


# ============================================================
# ПОДКАТЕГОРИЯ
# ============================================================

def post_sub_menu(
    key
):

    rows = pair_buttons(

        list(
            CATEGORIES[
                key
            ]["subs"].items()
        ),

        f"postsub_{key}_"
    )

    rows.append(

        [

            {

                "text":
                    "⬅️ Назад",

                "callback_data":
                    "post"
            }
        ]
    )

    return rows


# ============================================================
# ТИП ОБЪЯВЛЕНИЯ
# ============================================================

def post_type_menu(
    key
):

    types = CATEGORIES[
        key
    ]["types"]

    rows = []

    for i in range(

        0,

        len(types),

        2
    ):

        rows.append(

            [

                {

                    "text":
                        label,

                    "callback_data":
                        f"posttype_{type_key}"
                }

                for label, type_key
                in types[
                    i:i + 2
                ]
            ]
        )

    rows.append(

        [

            {

                "text":
                    "⬅️ Назад",

                "callback_data":
                    f"postcat_{key}"
            }
        ]
    )

    return rows


# ============================================================
# ВЫБОР ЦЕНЫ
# ============================================================

def currency_menu():

    return [

        [

            {

                "text":
                    "🇺🇸 USD ($)",

                "callback_data":
                    "currency_usd"
            },

            {

                "text":
                    "🇬🇪 GEL (₾)",

                "callback_data":
                    "currency_gel"
            }
        ],

        [

            {

                "text":
                    "🇪🇺 EUR (€)",

                "callback_data":
                    "currency_eur"
            },

            {

                "text":
                    "🤝 Договорная",

                "callback_data":
                    "currency_negotiable"
            }
        ],

        [

            {

                "text":
                    "🎁 Бесплатно",

                "callback_data":
                    "currency_free"
            }
        ]
    ]


# ============================================================
# МЕНЮ РЕДАКТИРОВАНИЯ
# ============================================================

def edit_menu():

    return [

        [

            {

                "text":
                    "💰 Цена",

                "callback_data":
                    "edit_price"
            },

            {

                "text":
                    "📍 Локация",

                "callback_data":
                    "edit_district"
            }
        ],

        [

            {

                "text":
                    "📋 Характеристики",

                "callback_data":
                    "edit_details"
            },

            {

                "text":
                    "📝 Описание",

                "callback_data":
                    "edit_description"
            }
        ],

        [

            {

                "text":
                    "📷 Фотографии",

                "callback_data":
                    "edit_photos"
            },

            {

                "text":
                    "📞 Контакт",

                "callback_data":
                    "edit_contact"
            }
        ],

        [

            {

                "text":
                    "🔄 Начать заново",

                "callback_data":
                    "restart_post"
            }
        ],

        [

            {

                "text":
                    "⬅️ К объявлению",

                "callback_data":
                    "show_preview"
            }
        ]
    ]


# ============================================================
# ПУСТОЕ ОБЪЯВЛЕНИЕ
# ============================================================

def blank_listing():

    return {

        "category_key":
            "",

        "category":
            "",

        "subcategory_key":
            "",

        "subcategory":
            "",

        "type_key":
            "",

        "type":
            "",

        "details":
            {},

        "price":
            "",

        "currency":
            "",

        "district":
            "",

        "description":
            "",

        "photos":
            [],

        "contact":
            "",
    }


# ============================================================
# НАЧАЛО СОЗДАНИЯ
# ============================================================

def start_post(
    chat_id
):

    states[
        chat_id
    ] = {

        "step":
            "category",

        "data":
            blank_listing()
    }

    send(

        chat_id,

        "<b>➕ НОВОЕ ОБЪЯВЛЕНИЕ</b>\n\n"
        "Выберите категорию:",

        post_category_menu()
    )


# ============================================================
# ЗАПРОС
# ============================================================

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


# ============================================================
# ХАРАКТЕРИСТИКИ
# ============================================================

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


# ============================================================
# ЦЕНА
# ============================================================

def ask_price(
    chat_id
):

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
        "Выберите валюту или вариант:",

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

        data[
            "currency"
        ],

        ""
    )

    ask(

        chat_id,

        (
            "edit_amount"
            if editing
            else "amount"
        ),

        f"<b>💰 Сумма в {symbol}</b>\n\n"
        "Введите только число.\n\n"
        "<i>Например: 660</i>"
    )


# ============================================================
# ЛОКАЦИЯ
# ============================================================

def ask_district(
    chat_id
):

    ask(

        chat_id,

        "district",

        "<b>📍 Локация</b>\n\n"
        "Укажите район или ориентир в Батуми.\n\n"
        "<i>Например: Пиросмани 18а</i>"
    )


# ============================================================
# ОПИСАНИЕ
# ============================================================

def ask_description(
    chat_id
):

    ask(

        chat_id,

        "description",

        "<b>📝 Описание</b>\n\n"
        "Напишите несколько важных деталей "
        "объявления.\n\n"
        "<i>Если описания нет — напишите "
        "«Пропустить».</i>"
    )


# ============================================================
# ФОТО
# ============================================================

def ask_photos(
    chat_id
):

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
        "Когда закончите — нажмите «Готово».\n\n"
        "После публикации Telegram покажет "
        "их компактным альбомом.",

        [

            [

                {

                    "text":
                        "✅ Готово",

                    "callback_data":
                        "photos_done"
                }
            ],

            [

                {

                    "text":
                        "⏭ Пропустить",

                    "callback_data":
                        "photos_skip"
                }
            ]
        ]
    )


# ============================================================
# КОНТАКТ
# ============================================================

def ask_contact(
    chat_id
):

    ask(

        chat_id,

        "contact",

        "<b>📞 Контакт</b>\n\n"
        "Укажите телефон, Telegram или WhatsApp."
    )


# ============================================================
# АВТОМАТИЧЕСКИЙ ЗАГОЛОВОК
# ============================================================

def make_title(
    data
):

    category = data[
        "category_key"
    ]

    typ = data[
        "type_key"
    ]

    details = data[
        "details"
    ]


    # -------------------------
    # НЕДВИЖИМОСТЬ
    # -------------------------

    if category == "realestate":

        rooms = details.get(
            "rooms",
            ""
        )

        subcategory = data.get(
            "subcategory_key",
            ""
        )

        property_names = {

            "apartment":
                "квартиру",

            "house":
                "дом",

            "room":
                "комнату",

            "commercial":
                "коммерческое помещение",

            "land":
                "земельный участок",

            "garage":
                "гараж / парковку",
        }

        property_name = property_names.get(
            subcategory,
            "объект недвижимости"
        )

        action = {

            "rent":
                "Сдам",

            "seek":
                "Сниму",

            "sell":
                "Продам",

            "buy":
                "Куплю"

        }.get(

            typ,

            "Объявление"
        )

        if subcategory == "apartment" and rooms:

            title = (
                f"{action} "
                f"{rooms}-комнатную квартиру"
            )

        elif subcategory == "room" and rooms:

            title = (
                f"{action} "
                f"{rooms}-комнатную комнату"
            )

        else:

            title = (
                f"{action} "
                f"{property_name}"
            )

        area = details.get(
            "area",
            ""
        )

        if area:

            title += (
                f" · {area} м²"
            )

        return title


    # -------------------------
    # АВТО
    # -------------------------

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

        title = (
            f"{action} {model}"
        )

        if year:

            title += (
                f" · {year}"
            )

        return title


    # -------------------------
    # ТЕХНИКА
    # -------------------------

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


    # -------------------------
    # РАБОТА
    # -------------------------

    if category == "work":

        return (

            details.get(
                "service",
                ""
            )

            or

            "Работа / услуга в Батуми"
        )


    # -------------------------
    # ИЩУ
    # -------------------------

    if category == "search":

        requirement = details.get(
            "requirements",
            ""
        )

        if requirement:

            return (
                f"Ищу: {requirement}"
            )

        return "Ищу"


    # -------------------------
    # ОТДАМ
    # -------------------------

    if typ == "give":

        return "Отдам бесплатно"


    # -------------------------
    # ОСТАЛЬНОЕ
    # -------------------------

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
# ОТОБРАЖЕНИЕ ЦЕНЫ
# ============================================================

def price_text(
    data
):

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

        data[
            "currency"
        ],

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
        f"{suffix}"
        f"</b>"
    )


# ============================================================
# ХЭШТЕГИ
# ============================================================

def build_hashtags(
    data
):

    tags = []


    # Категория
    category_tag = slug(
        data.get(
            "category",
            ""
        ).split(
            " ",
            1
        )[-1]
    )

    if category_tag:

        tags.append(
            "#" + category_tag
        )


    # Подкатегория
    subcategory_tag = slug(
        data.get(
            "subcategory",
            ""
        ).split(
            " ",
            1
        )[-1]
    )

    if subcategory_tag:

        tags.append(
            "#" + subcategory_tag
        )


    # Тип объявления
    type_tags = {

        "rent":
            "#сдам",

        "seek":
            (
                "#сниму"
                if data.get(
                    "category_key"
                ) == "realestate"
                else "#ищу"
            ),

        "sell":
            "#продам",

        "buy":
            "#куплю",

        "give":
            "#отдам",

        "offer":
            "#услуги"
    }


    type_tag = type_tags.get(

        data.get(
            "type_key"
        )
    )

    if type_tag:

        tags.append(
            type_tag
        )


    # Район
    district = data.get(
        "district",
        ""
    ).strip()

    if district:

        district_tag = slug(
            district
        )

        if district_tag:

            tags.append(
                "#" + district_tag
            )


    # Батуми
    tags.append(
        "#батум"
    )


    # Убираем любые повторы
    return " ".join(
        unique_hashtags(
            tags
        )
    )


# ============================================================
# ФИНАЛЬНАЯ КАРТОЧКА
# ============================================================

def build_listing(
    data
):

    lines = [

        # Категория + тип
        (
            f"<b>"
            f"{esc(data['category'])}"
            f" · "
            f"{esc(data['type'])}"
            f"</b>"
        ),

        # Подкатегория
        (
            f"<i>"
            f"{esc(data['subcategory'])}"
            f"</i>"
        ),

        ""
    ]


    # ========================================================
    # ХАРАКТЕРИСТИКИ
    # ========================================================

    details_lines = []

    fields = CATEGORIES[
        data[
            "category_key"
        ]
    ]["fields"]


    for key, label in fields:

        value = data[
            "details"
        ].get(
            key
        )

        if value:

            details_lines.append(

                f"{esc(label)}: "
                f"<b>{esc(value)}</b>"
            )


    if details_lines:

        lines.extend(
            details_lines
        )

        lines.append("")


    # ========================================================
    # АВТОМАТИЧЕСКИЙ ЗАГОЛОВОК
    # ========================================================

    title = make_title(
        data
    )

    if title:

        lines.append(

            f"<b>"
            f"{esc(title)}"
            f"</b>"
        )

        lines.append("")


    # ========================================================
    # ЦЕНА
    # ========================================================

    price = price_text(
        data
    )

    if price:

        lines.append(
            price
        )


    # ========================================================
    # ЛОКАЦИЯ
    # ========================================================

    if data[
        "district"
    ]:

        lines.append(

            f"📍 <b>"
            f"{esc(data['district'])}"
            f"</b>"
        )


    # ========================================================
    # ОПИСАНИЕ
    # ========================================================

    description = str(
        data.get(
            "description",
            ""
        )
        or
        ""
    ).strip()


    # Если пользователь написал одно из этих значений,
    # описание не публикуем.
    skip_descriptions = {

        "ничего",
        "нет",
        "нечего",
        "без описания",
        "пропустить",
        "-"
    }


    if (

        description

        and

        description.lower()
        not in skip_descriptions

    ):

        lines.extend(

            [

                "",

                esc(
                    description
                )
            ]
        )


    # ========================================================
    # КОНТАКТ
    # ========================================================

    if data[
        "contact"
    ]:

        lines.extend(

            [

                "",

                f"📞 <b>"
                f"{esc(data['contact'])}"
                f"</b>",

                "<i>"
                "WhatsApp / Telegram"
                "</i>"
            ]
        )


    # ========================================================
    # ХЭШТЕГИ
    # ========================================================

    hashtags = build_hashtags(
        data
    )

    if hashtags:

        lines.extend(

            [

                "",

                hashtags
            ]
        )


    return "\n".join(
        lines
    )


# ============================================================
# КНОПКИ ПРЕДПРОСМОТРА
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


# ============================================================
# АЛЬБОМ ФОТОГРАФИЙ
# ============================================================

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


        # Текст ставим только под первой фотографией.

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


# ============================================================
# ПРЕДПРОСМОТР
# ============================================================

def preview(
    chat_id
):

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


    # Telegram caption имеет ограничение.
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
# РЕДАКТИРОВАНИЕ ХАРАКТЕРИСТИК
# ============================================================

def edit_details_menu(
    chat_id
):

    data = states[
        chat_id
    ]["data"]


    fields = CATEGORIES[
        data[
            "category_key"
        ]
    ]["fields"]


    rows = []


    for i, (_, label) in enumerate(
        fields
    ):

        rows.append(

            [

                {

                    "text":
                        label,

                    "callback_data":
                        f"edit_detail_{i}"
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

        "<b>📋 Характеристики</b>\n\n"
        "Что хотите изменить?",

        rows
    )


# ============================================================
# ПУБЛИКАЦИЯ В КАНАЛ
# ============================================================

def publish(
    chat_id
):

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


    # Канал не указан
    if not CHANNEL_USERNAME:

        send(

            chat_id,

            "<b>⚠️ Канал пока не подключён.</b>\n\n"

            "В Render → Environment "
            "нужно добавить переменную:\n\n"

            "<code>CHANNEL_USERNAME</code>\n\n"

            "и указать username твоего "
            "публичного канала."
        )

        return


    # Публикация с фотографиями
    if photos:

        result = send_album(

            CHANNEL_USERNAME,

            photos,

            text
        )


    # Публикация без фотографий
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


    # Ошибка
    if not result.get(
        "ok"
    ):

        send(

            chat_id,

            "<b>⚠️ Не удалось опубликовать.</b>\n\n"

            "Проверьте:\n"
            "• бот является администратором канала;\n"
            "• у бота есть право публиковать сообщения;\n"
            "• CHANNEL_USERNAME указан правильно."
        )

        return


    # Удаляем временное объявление
    states.pop(
        chat_id,
        None
    )


    send(

        chat_id,

        "<b>🎉 Объявление опубликовано!</b>\n\n"

        "Оно добавлено в "
        "<b>MADLOBA MARKET | БАТУМИ</b>.",

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


    # Отмена
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


    # ========================================================
    # ХАРАКТЕРИСТИКИ
    # ========================================================

    if step.startswith(
        "detail_"
    ):

        index = int(

            step.split(
                "_"
            )[1]
        )


        fields = CATEGORIES[
            data[
                "category_key"
            ]
        ]["fields"]


        if index < len(
            fields
        ):

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


    # ========================================================
    # РЕДАКТИРОВАНИЕ ХАРАКТЕРИСТИК
    # ========================================================

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
            data[
                "category_key"
            ]
        ]["fields"]


        if index < len(
            fields
        ):

            data[
                "details"
            ][
                fields[index][0]
            ] = text


        preview(
            chat_id
        )


        return True


    # ========================================================
    # СУММА
    # ========================================================

    if step in (
        "amount",
        "edit_amount"
    ):

        # Оставляем только нормальную цену.
        cleaned = text.replace(
            " ",
            ""
        ).replace(
            ",",
            "."
        )

        data[
            "price"
        ] = cleaned


        if step == "amount":

            ask_district(
                chat_id
            )

        else:

            preview(
                chat_id
            )


        return True


    # ========================================================
    # ЛОКАЦИЯ
    # ========================================================

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


    # ========================================================
    # ОПИСАНИЕ
    # ========================================================

    if step in (
        "description",
        "edit_description"
    ):

        # Пустые/служебные ответы не публикуем.
        if text.lower() in {

            "ничего",
            "нет",
            "нечего",
            "без описания",
            "пропустить",
            "-"

        }:

            data[
                "description"
            ] = ""

        else:

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


    # ========================================================
    # ФОТО
    # ========================================================

    if step == "photos":

        send(

            chat_id,

            "📷 Отправьте фотографию "
            "или нажмите <b>✅ Готово</b>."
        )

        return True


    # ========================================================
    # КОНТАКТ
    # ========================================================

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
# ОБРАБОТКА UPDATE
# ============================================================

def handle(
    update
):

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


        # ====================================================
        # ФОТО
        # ====================================================

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
                ]["data"][
                    "photos"
                ]


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


        # ====================================================
        # START
        # ====================================================

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


        # ====================================================
        # АКТИВНАЯ ФОРМА
        # ====================================================

        if (

            chat_id in states

            and

            process_text(
                chat_id,
                text
            )

        ):

            return


        # ====================================================
        # КОМАНДЫ
        # ====================================================

        if text.startswith(
            "/categories"
        ):

            send(

                chat_id,

                "📂 <b>Выберите категорию:</b>",

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

                "<b>📋 Правила MADLOBA MARKET</b>\n\n"

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

                "<b>ℹ️ MADLOBA MARKET</b>\n\n"

                "/start — главное меню\n"
                "/categories — категории\n"
                "/post — разместить объявление\n"
                "/rules — правила\n"
                "/help — помощь"
            )


        return


    # ========================================================
    # CALLBACK QUERY
    # ========================================================

    if "callback_query" not in update:

        return


    callback = update[
        "callback_query"
    ]


    chat_id = callback[
        "message"
    ]["chat"]["id"]


    data = callback.get(
        "data",
        ""
    )


    answer(
        callback["id"]
    )


    # ========================================================
    # ГЛАВНОЕ МЕНЮ
    # ========================================================

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


    # ========================================================
    # КАТЕГОРИЯ
    # ========================================================

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


    # ========================================================
    # ПРОСМОТР КАТЕГОРИИ
    # ========================================================

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

            "Здесь будут отображаться "
            "объявления этой категории.",

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


    # ========================================================
    # СОЗДАНИЕ ОБЪЯВЛЕНИЯ
    # ========================================================

    if data == "post":

        start_post(
            chat_id
        )

        return


    # ========================================================
    # ВЫБОР КАТЕГОРИИ
    # ========================================================

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


    # ========================================================
    # ПОДКАТЕГОРИЯ
    # ========================================================

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


    # ========================================================
    # ТИП ОБЪЯВЛЕНИЯ
    # ========================================================

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


        # Для недвижимости seek = Сниму.
        if (

            listing[
                "category_key"
            ]

            ==

            "realestate"

            and

            type_key
            ==
            "seek"

        ):

            listing[
                "type"
            ] = "🔎 Сниму"

        else:

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


    # ========================================================
    # ВАЛЮТА
    # ========================================================

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


        # Договорная
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


        # Бесплатно
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


        # Обычная цена
        ask_amount(

            chat_id,

            editing
        )


        return


    # ========================================================
    # ФОТО ГОТОВЫ
    # ========================================================

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


    # ========================================================
    # ФОТО ПРОПУСТИТЬ
    # ========================================================

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


    # ========================================================
    # МЕНЮ РЕДАКТИРОВАНИЯ
    # ========================================================

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


    # ========================================================
    # ХАРАКТЕРИСТИКИ
    # ========================================================

    if data == "edit_details":

        if chat_id in states:

            edit_details_menu(
                chat_id
            )


        return


    # ========================================================
    # ЦЕНА
    # ========================================================

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


    # ========================================================
    # ЛОКАЦИЯ / ОПИСАНИЕ / КОНТАКТ
    # ========================================================

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


    # ========================================================
    # ФОТО
    # ========================================================

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


    # ========================================================
    # ОТДЕЛЬНАЯ ХАРАКТЕРИСТИКА
    # ========================================================

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


        if index < len(
            fields
        ):

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


    # ========================================================
    # ПРЕДПРОСМОТР
    # ========================================================

    if data == "show_preview":

        preview(
            chat_id
        )

        return


    # ========================================================
    # НАЧАТЬ ЗАНОВО
    # ========================================================

    if data == "restart_post":

        start_post(
            chat_id
        )

        return


    # ========================================================
    # ОТМЕНА
    # ========================================================

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


    # ========================================================
    # ПУБЛИКАЦИЯ
    # ========================================================

    if data == "publish_post":

        publish(
            chat_id
        )

        return


# ============================================================
# WEB
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

        except Exception as error:

            print(
                "UPDATE ERROR:",
                repr(error)
            )


    return "OK"


# ============================================================
# ЗАПУСК
# ============================================================

print(
    "===== BOT START ====="
)


# Проверяем токен
print(

    "GET ME:",

    api(
        "getMe"
    )
)


# ============================================================
# КОМАНДЫ TELEGRAM
# ============================================================

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
# WEBHOOK RENDER
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
                "10000"
            )
        )
    )
    
