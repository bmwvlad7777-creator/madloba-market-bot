import os
import html
import requests
from flask import Flask, request

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip()

app = Flask(__name__)

# Temporary storage for active listing forms.
# For the first version this is enough; later we can move listings to a database.
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
        return {"ok": False, "error": str(e)}


def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if keyboard:
        data["reply_markup"] = {"inline_keyboard": keyboard}
    return telegram("sendMessage", data)


def send_photo(chat_id, photo, caption=None, keyboard=None):
    data = {
        "chat_id": chat_id,
        "photo": photo
    }
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    if keyboard:
        data["reply_markup"] = {"inline_keyboard": keyboard}
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
        "Ñ": "Ðµ"
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


# ============================================================
# CATEGORY STRUCTURE
# ============================================================

CATEGORIES = {
    "realestate": {
        "name": "ð  ÐÐµÐ´Ð²Ð¸Ð¶Ð¸Ð¼Ð¾ÑÑÑ",
        "subcategories": {
            "apartment": "ð¢ ÐÐ²Ð°ÑÑÐ¸ÑÑ",
            "house": "ð¡ ÐÐ¾Ð¼Ð°",
            "room": "ð ÐÐ¾Ð¼Ð½Ð°ÑÑ",
            "commercial": "ð¬ ÐÐ¾Ð¼Ð¼ÐµÑÑÐ¸Ñ",
            "land": "ð³ ÐÐµÐ¼Ð»Ñ",
            "garage": "ð ÐÐ°ÑÐ°Ð¶Ð¸ Ð¸ Ð¿Ð°ÑÐºÐ¾Ð²ÐºÐ¸"
        },
        "types": [
            ("ð Ð¡Ð´Ð°Ð¼", "rent"),
            ("ð Ð¡Ð½Ð¸Ð¼Ñ", "seek"),
            ("ð¡ ÐÑÐ¾Ð´Ð°Ð¼", "sell"),
            ("ð° ÐÑÐ¿Ð»Ñ", "buy")
        ]
    },
    "auto": {
        "name": "ð ÐÐ²ÑÐ¾",
        "subcategories": {
            "cars": "ð ÐÐµÐ³ÐºÐ¾Ð²ÑÐµ",
            "suv": "ð ÐÑÐ¾ÑÑÐ¾Ð²ÐµÑÑ Ð¸ SUV",
            "commercial": "ð ÐÐ¾Ð¼Ð¼ÐµÑÑÐµÑÐºÐ¸Ð¹ ÑÑÐ°Ð½ÑÐ¿Ð¾ÑÑ",
            "moto": "ð ÐÐ¾ÑÐ¾",
            "parts": "âï¸ ÐÐ°Ð¿ÑÐ°ÑÑÐ¸",
            "rental": "ð ÐÑÐµÐ½Ð´Ð°"
        },
        "types": [
            ("ð° ÐÑÐ¾Ð´Ð°Ð¼", "sell"),
            ("ð ÐÑÐ¿Ð»Ñ", "buy"),
            ("ð Ð¡Ð´Ð°Ð¼", "rent"),
            ("ð ÐÑÑ", "seek")
        ]
    },
    "tech": {
        "name": "ð± Ð¢ÐµÑÐ½Ð¸ÐºÐ°",
        "subcategories": {
            "phones": "ð± Ð¢ÐµÐ»ÐµÑÐ¾Ð½Ñ Ð¸ Ð¿Ð»Ð°Ð½ÑÐµÑÑ",
            "computers": "ð» ÐÐ¾Ð¼Ð¿ÑÑÑÐµÑÑ",
            "tv": "ðº Ð¢Ð Ð¸ Ð°ÑÐ´Ð¸Ð¾",
            "appliances": "ð§º ÐÑÑÐ¾Ð²Ð°Ñ ÑÐµÑÐ½Ð¸ÐºÐ°",
            "photo": "ð· Ð¤Ð¾ÑÐ¾ Ð¸ Ð²Ð¸Ð´ÐµÐ¾",
            "other": "ð ÐÑÑÐ³Ð°Ñ ÑÐµÑÐ½Ð¸ÐºÐ°"
        },
        "types": [
            ("ð° ÐÑÐ¾Ð´Ð°Ð¼", "sell"),
            ("ð ÐÑÐ¿Ð»Ñ", "buy")
        ]
    },
    "home": {
        "name": "ð ÐÐ¾Ð¼ Ð¸ Ð¼ÐµÐ±ÐµÐ»Ñ",
        "subcategories": {
            "furniture": "ð ÐÐµÐ±ÐµÐ»Ñ",
            "household": "ð  ÐÐ»Ñ Ð´Ð¾Ð¼Ð°",
            "repair": "ð¨ Ð ÐµÐ¼Ð¾Ð½Ñ",
            "decor": "ð¼ ÐÐµÐºÐ¾Ñ",
            "garden": "ð¿ Ð¡Ð°Ð´ Ð¸ Ð´Ð°ÑÐ°",
            "other": "ð¦ ÐÑÑÐ³Ð¾Ðµ"
        },
        "types": [
            ("ð° ÐÑÐ¾Ð´Ð°Ð¼", "sell"),
            ("ð ÐÑÐ¿Ð»Ñ", "buy")
        ]
    },
    "kids": {
        "name": "ð¶ ÐÐµÑÑÐºÐ¾Ðµ",
        "subcategories": {
            "clothes": "ð ÐÐ´ÐµÐ¶Ð´Ð° Ð¸ Ð¾Ð±ÑÐ²Ñ",
            "toys": "ð§¸ ÐÐ³ÑÑÑÐºÐ¸",
            "strollers": "ð¼ ÐÐ¾Ð»ÑÑÐºÐ¸ Ð¸ Ð°Ð²ÑÐ¾ÐºÑÐµÑÐ»Ð°",
            "furniture": "ð ÐÐµÑÑÐºÐ°Ñ Ð¼ÐµÐ±ÐµÐ»Ñ",
            "sports": "â½ï¸ Ð¡Ð¿Ð¾ÑÑ",
            "other": "ð ÐÑÑÐ³Ð¾Ðµ"
        },
        "types": [
            ("ð° ÐÑÐ¾Ð´Ð°Ð¼", "sell"),
            ("ð ÐÑÐ¿Ð»Ñ", "buy"),
            ("ð ÐÑÐ´Ð°Ð¼", "give")
        ]
    },
    "work": {
        "name": "ð¼ Ð Ð°Ð±Ð¾ÑÐ° Ð¸ ÑÑÐ»ÑÐ³Ð¸",
        "subcategories": {
            "jobs": "ð¼ ÐÐ°ÐºÐ°Ð½ÑÐ¸Ð¸",
            "services": "ð  Ð£ÑÐ»ÑÐ³Ð¸",
            "construction": "ð¨ Ð ÐµÐ¼Ð¾Ð½Ñ Ð¸ ÑÑÑÐ¾Ð¸ÑÐµÐ»ÑÑÑÐ²Ð¾",
            "beauty": "ð ÐÑÐ°ÑÐ¾ÑÐ°",
            "education": "ð ÐÐ±ÑÑÐµÐ½Ð¸Ðµ",
            "transport": "ð Ð¢ÑÐ°Ð½ÑÐ¿Ð¾ÑÑ Ð¸ Ð´Ð¾ÑÑÐ°Ð²ÐºÐ°",
            "it": "ð» IT",
            "other": "ð ÐÑÑÐ³Ð¾Ðµ"
        },
        "types": [
            ("ð¼ ÐÑÐµÐ´Ð»Ð°Ð³Ð°Ñ", "offer"),
            ("ð ÐÑÑ", "seek")
        ]
    },
    "give": {
        "name": "ð ÐÑÐ´Ð°Ð¼",
        "subcategories": {
            "home": "ð  ÐÐ»Ñ Ð´Ð¾Ð¼Ð°",
            "clothes": "ð ÐÐ´ÐµÐ¶Ð´Ð°",
            "kids": "ð¶ ÐÐµÑÑÐºÐ¾Ðµ",
            "tech": "ð± Ð¢ÐµÑÐ½Ð¸ÐºÐ°",
            "other": "ð¦ ÐÑÑÐ³Ð¾Ðµ"
        },
        "types": [
            ("ð ÐÑÐ´Ð°Ð¼ Ð±ÐµÑÐ¿Ð»Ð°ÑÐ½Ð¾", "give")
        ]
    },
    "search": {
        "name": "ð ÐÑÑ",
        "subcategories": {
            "realestate": "ð  ÐÐµÐ´Ð²Ð¸Ð¶Ð¸Ð¼Ð¾ÑÑÑ",
            "auto": "ð ÐÐ²ÑÐ¾",
            "tech": "ð± Ð¢ÐµÑÐ½Ð¸ÐºÐ°",
            "home": "ð ÐÐ¾Ð¼ Ð¸ Ð¼ÐµÐ±ÐµÐ»Ñ",
            "kids": "ð¶ ÐÐµÑÑÐºÐ¾Ðµ",
            "services": "ð  Ð£ÑÐ»ÑÐ³Ð¸",
            "other": "ð¦ ÐÑÑÐ³Ð¾Ðµ"
        },
        "types": [
            ("ð ÐÑÑ", "seek")
        ]
    }
}


TYPE_NAMES = {
    "rent": "ð Ð¡Ð´Ð°Ð¼",
    "seek": "ð ÐÑÑ",
    "sell": "ð° ÐÑÐ¾Ð´Ð°Ð¼",
    "buy": "ð° ÐÑÐ¿Ð»Ñ",
    "give": "ð ÐÑÐ´Ð°Ð¼ Ð±ÐµÑÐ¿Ð»Ð°ÑÐ½Ð¾",
    "offer": "ð¼ ÐÑÐµÐ´Ð»Ð°Ð³Ð°Ñ",
}


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    return [
        [
            {"text": "ð  ÐÐµÐ´Ð²Ð¸Ð¶Ð¸Ð¼Ð¾ÑÑÑ", "callback_data": "cat_realestate"},
            {"text": "ð ÐÐ²ÑÐ¾", "callback_data": "cat_auto"}
        ],
        [
            {"text": "ð± Ð¢ÐµÑÐ½Ð¸ÐºÐ°", "callback_data": "cat_tech"},
            {"text": "ð ÐÐ¾Ð¼ Ð¸ Ð¼ÐµÐ±ÐµÐ»Ñ", "callback_data": "cat_home"}
        ],
        [
            {"text": "ð¶ ÐÐµÑÑÐºÐ¾Ðµ", "callback_data": "cat_kids"},
            {"text": "ð¼ Ð Ð°Ð±Ð¾ÑÐ° Ð¸ ÑÑÐ»ÑÐ³Ð¸", "callback_data": "cat_work"}
        ],
        [
            {"text": "ð ÐÑÐ´Ð°Ð¼", "callback_data": "cat_give"},
            {"text": "ð ÐÑÑ", "callback_data": "cat_search"}
        ],
        [
            {"text": "â Ð Ð°Ð·Ð¼ÐµÑÑÐ¸ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ", "callback_data": "post"}
        ]
    ]


def category_menu(category_key):
    category = CATEGORIES[category_key]
    rows = []

    items = list(category["subcategories"].items())

    for i in range(0, len(items), 2):
        row = []
        for key, label in items[i:i + 2]:
            row.append({
                "text": label,
                "callback_data": f"browse_{category_key}_{key}"
            })
        rows.append(row)

    rows.append([
        {"text": "ð ÐÑÐµ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ", "callback_data": f"browse_{category_key}_all"}
    ])

    rows.append([
        {"text": "â¬ï¸ ÐÐ»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ", "callback_data": "back_main"}
    ])

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
            row.append({
                "text": category["name"],
                "callback_data": f"postcat_{key}"
            })
        rows.append(row)

    rows.append([
        {"text": "â ÐÑÐ¼ÐµÐ½Ð°", "callback_data": "cancel_post"}
    ])
    return rows


def post_subcategory_menu(category_key):
    category = CATEGORIES[category_key]
    rows = []
    items = list(category["subcategories"].items())

    for i in range(0, len(items), 2):
        row = []
        for key, label in items[i:i + 2]:
            row.append({
                "text": label,
                "callback_data": f"postsub_{category_key}_{key}"
            })
        rows.append(row)

    rows.append([
        {"text": "â¬ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": "post"}
    ])
    return rows


def post_type_menu(category_key):
    rows = []
    types = CATEGORIES[category_key]["types"]

    for i in range(0, len(types), 2):
        row = []
        for label, type_key in types[i:i + 2]:
            row.append({
                "text": label,
                "callback_data": f"posttype_{type_key}"
            })
        rows.append(row)

    rows.append([
        {"text": "â¬ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"postcat_{category_key}"}
    ])
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
        "<b>â ÐÐÐÐÐ ÐÐÐªÐ¯ÐÐÐÐÐÐ</b>\n\n"
        "ÐÑÐ±ÐµÑÐ¸ÑÐµ ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ:",
        post_category_menu()
    )


def ask_title(chat_id):
    user_states[chat_id]["step"] = "title"
    send_message(
        chat_id,
        "<b>3 Â· ÐÐ°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº</b>\n\n"
        "ÐÐ°Ð¿Ð¸ÑÐ¸ÑÐµ ÐºÐ¾ÑÐ¾ÑÐºÐ¸Ð¹ Ð¸ Ð¿Ð¾Ð½ÑÑÐ½ÑÐ¹ Ð·Ð°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº.\n\n"
        "<i>ÐÐ°Ð¿ÑÐ¸Ð¼ÐµÑ: Ð¡Ð´Ð°Ð¼ 3-ÐºÐ¾Ð¼Ð½Ð°ÑÐ½ÑÑ ÐºÐ²Ð°ÑÑÐ¸ÑÑ Ñ Ð¼Ð¾ÑÑ</i>"
    )


def ask_price(chat_id):
    user_states[chat_id]["step"] = "price"
    send_message(
        chat_id,
        "<b>4 Â· Ð¦ÐµÐ½Ð°</b>\n\n"
        "Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÐµÐ½Ñ Ð¸ Ð²Ð°Ð»ÑÑÑ.\n\n"
        "<i>ÐÐ°Ð¿ÑÐ¸Ð¼ÐµÑ: 550 $ / Ð¼ÐµÑÑÑ</i>\n"
        "<i>ÐÐ»Ð¸: ÐÐ¾Ð³Ð¾Ð²Ð¾ÑÐ½Ð°Ñ</i>\n"
        "<i>ÐÐ»Ð¸: ÐÐµÑÐ¿Ð»Ð°ÑÐ½Ð¾</i>"
    )


def ask_district(chat_id):
    user_states[chat_id]["step"] = "district"
    send_message(
        chat_id,
        "<b>5 Â· ÐÐ¾ÐºÐ°ÑÐ¸Ñ</b>\n\n"
        "Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÐ°Ð¹Ð¾Ð½ Ð¸Ð»Ð¸ Ð¾ÑÐ¸ÐµÐ½ÑÐ¸Ñ Ð² ÐÐ°ÑÑÐ¼Ð¸.\n\n"
        "<i>ÐÐ°Ð¿ÑÐ¸Ð¼ÐµÑ: ÐÐ¸ÑÐ¾ÑÐ¼Ð°Ð½Ð¸</i>"
    )


def ask_description(chat_id):
    user_states[chat_id]["step"] = "description"
    send_message(
        chat_id,
        "<b>6 Â· ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ</b>\n\n"
        "Ð Ð°ÑÑÐºÐ°Ð¶Ð¸ÑÐµ Ð¾ Ð¿ÑÐµÐ´Ð»Ð¾Ð¶ÐµÐ½Ð¸Ð¸.\n\n"
        "Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÐ°ÑÐ°ÐºÑÐµÑÐ¸ÑÑÐ¸ÐºÐ¸, ÑÐ¾ÑÑÐ¾ÑÐ½Ð¸Ðµ, ÐºÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ°ÑÐ¸Ñ "
        "Ð¸ Ð´ÑÑÐ³Ð¸Ðµ Ð²Ð°Ð¶Ð½ÑÐµ Ð´ÐµÑÐ°Ð»Ð¸."
    )


def ask_photos(chat_id):
    user_states[chat_id]["step"] = "photos"
    count = len(user_states[chat_id]["data"]["photos"])

    send_message(
        chat_id,
        f"<b>7 Â· Ð¤Ð¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¸</b>\n\n"
        f"ÐÐ¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾: <b>{count}/{MAX_PHOTOS}</b>\n\n"
        "ÐÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ ÑÐ¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¸ Ð¿Ð¾ Ð¾Ð´Ð½Ð¾Ð¹.\n"
        "ÐÐ¾Ð³Ð´Ð° Ð·Ð°ÐºÐ¾Ð½ÑÐ¸ÑÐµ â Ð½Ð°Ð¶Ð¼Ð¸ÑÐµ ÐºÐ½Ð¾Ð¿ÐºÑ <b>ÐÐ¾ÑÐ¾Ð²Ð¾</b>.\n\n"
        "ÐÐ¾Ð¶Ð½Ð¾ ÑÐ°ÐºÐ¶Ðµ Ð½Ð°Ð¿Ð¸ÑÐ°ÑÑ <b>ÐÑÐ¾Ð¿ÑÑÑÐ¸ÑÑ</b>.",
        [
            [
                {"text": "â ÐÐ¾ÑÐ¾Ð²Ð¾", "callback_data": "photos_done"}
            ],
            [
                {"text": "â­ ÐÑÐ¾Ð¿ÑÑÑÐ¸ÑÑ", "callback_data": "photos_skip"}
            ]
        ]
    )


def ask_contact(chat_id):
    user_states[chat_id]["step"] = "contact"
    send_message(
        chat_id,
        "<b>8 Â· ÐÐ¾Ð½ÑÐ°ÐºÑ</b>\n\n"
        "Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÐµÐ»ÐµÑÐ¾Ð½, Telegram Ð¸Ð»Ð¸ WhatsApp.\n\n"
        "<i>ÐÐ°Ð¿ÑÐ¸Ð¼ÐµÑ: +995 599 024 723</i>"
    )


# ============================================================
# PREVIEW
# ============================================================

def preview_keyboard():
    return [
        [
            {
                "text": "â ÐÐ¿ÑÐ±Ð»Ð¸ÐºÐ¾Ð²Ð°ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ",
                "callback_data": "publish_post"
            }
        ],
        [
            {
                "text": "âï¸ ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ Ð´Ð°Ð½Ð½ÑÐµ",
                "callback_data": "edit_menu"
            }
        ],
        [
            {
                "text": "â ÐÑÐ¼ÐµÐ½Ð°",
                "callback_data": "cancel_post"
            }
        ]
    ]


def edit_menu():
    return [
        [
            {"text": "âï¸ ÐÐ°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº", "callback_data": "edit_title"},
            {"text": "ð° Ð¦ÐµÐ½Ð°", "callback_data": "edit_price"}
        ],
        [
            {"text": "ð ÐÐ¾ÐºÐ°ÑÐ¸Ñ", "callback_data": "edit_district"},
            {"text": "ð ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ", "callback_data": "edit_description"}
        ],
        [
            {"text": "ð· Ð¤Ð¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¸", "callback_data": "edit_photos"},
            {"text": "ð ÐÐ¾Ð½ÑÐ°ÐºÑ", "callback_data": "edit_contact"}
        ],
        [
            {"text": "ð ÐÐ°ÑÐ°ÑÑ Ð·Ð°Ð½Ð¾Ð²Ð¾", "callback_data": "restart_post"}
        ],
        [
            {"text": "â¬ï¸ Ð Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ", "callback_data": "show_preview"}
        ]
    ]


def build_hashtags(data):
    tags = []

    category_key = data.get("category_key", "")
    subcategory_key = data.get("subcategory_key", "")
    type_key = data.get("type_key", "")
    district = data.get("district", "")

    if category_key:
        tags.append("#" + slug(
            data.get("category", "").split(" ", 1)[-1]
        ))

    if subcategory_key:
        tags.append("#" + slug(
            data.get("subcategory", "").split(" ", 1)[-1]
        ))

    type_map = {
        "rent": "#ÑÐ´Ð°Ð¼",
        "seek": "#Ð¸ÑÑ",
        "sell": "#Ð¿ÑÐ¾Ð´Ð°Ð¼",
        "buy": "#ÐºÑÐ¿Ð»Ñ",
        "give": "#Ð¾ÑÐ´Ð°Ð¼",
        "offer": "#ÑÑÐ»ÑÐ³Ð¸"
    }

    if type_key in type_map:
        tags.append(type_map[type_key])

    if district:
        tags.append("#" + slug(district))

    tags.append("#Ð±Ð°ÑÑÐ¼")

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
        f"<b>{category}  Â·  {post_type}</b>\n"
        f"<i>{subcategory}</i>\n\n"
        f"<b>{title}</b>\n\n"
        f"ð° <b>{price}</b>\n"
        f"ð <b>{district}</b>\n\n"
        f"{description}\n\n"
        f"ð <b>{contact}</b>\n"
        f"<i>Ð¡Ð²ÑÐ·Ð°ÑÑÑÑ Â· WhatsApp / Telegram</i>\n\n"
        f"{hashtags}"
    )


def show_preview(chat_id):
    if chat_id not in user_states:
        send_message(
            chat_id,
            "Ð¡ÐµÑÑÐ¸Ñ Ð·Ð°ÐºÐ¾Ð½ÑÐ¸Ð»Ð°ÑÑ. ÐÐ°ÑÐ½Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ."
        )
        return

    data = user_states[chat_id]["data"]
    listing = build_listing(data)
    photos = data.get("photos", [])

    # Telegram photo caption has a size limit.
    if len(listing) > 1000:
        listing = listing[:997] + "..."

    if photos:
        # Show the first photo in preview.
        send_photo(
            chat_id,
            photos[0],
            listing,
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
        "title": (
            "<b>âï¸ ÐÐ°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº</b>\n\n"
            "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÐ¹ Ð·Ð°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº."
        ),
        "price": (
            "<b>ð° Ð¦ÐµÐ½Ð°</b>\n\n"
            "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÑ ÑÐµÐ½Ñ."
        ),
        "district": (
            "<b>ð ÐÐ¾ÐºÐ°ÑÐ¸Ñ</b>\n\n"
            "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÐ¹ ÑÐ°Ð¹Ð¾Ð½."
        ),
        "description": (
            "<b>ð ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ</b>\n\n"
            "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ."
        ),
        "contact": (
            "<b>ð ÐÐ¾Ð½ÑÐ°ÐºÑ</b>\n\n"
            "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÐ¹ ÐºÐ¾Ð½ÑÐ°ÐºÑ."
        )
    }

    if field == "photos":
        user_states[chat_id]["data"]["photos"] = []
        ask_photos(chat_id)
        return

    send_message(
        chat_id,
        prompts.get(field, "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð·Ð½Ð°ÑÐµÐ½Ð¸Ðµ.")
    )


# ============================================================
# PUBLISH
# ============================================================

def publish_post(chat_id):
    if chat_id not in user_states:
        send_message(
            chat_id,
            "Ð¡ÐµÑÑÐ¸Ñ Ð·Ð°ÐºÐ¾Ð½ÑÐ¸Ð»Ð°ÑÑ. ÐÐ°ÑÐ½Ð¸ÑÐµ Ð½Ð¾Ð²Ð¾Ðµ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ."
        )
        return

    data = user_states[chat_id]["data"]
    listing = build_listing(data)
    photos = data.get("photos", [])

    if not CHANNEL_USERNAME:
        send_message(
            chat_id,
            "â <b>ÐÐ±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ Ð¿Ð¾Ð»Ð½Ð¾ÑÑÑÑ Ð³Ð¾ÑÐ¾Ð²Ð¾.</b>\n\n"
            "ÐÑÑÐ°Ð»Ð¾ÑÑ Ð¿Ð¾Ð´ÐºÐ»ÑÑÐ¸ÑÑ ÐºÐ°Ð½Ð°Ð» MADLOBA MARKET "
            "Ð´Ð»Ñ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¾Ð¹ Ð¿ÑÐ±Ð»Ð¸ÐºÐ°ÑÐ¸Ð¸."
        )
        print("CHANNEL_USERNAME is empty.")
        print("LISTING READY:")
        print(listing)
        return

    if photos:
        first = send_photo(
            CHANNEL_USERNAME,
            photos[0],
            listing
        )

        if not first.get("ok"):
            print("PUBLISH ERROR:", first)
            send_message(
                chat_id,
                "â ï¸ ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¾Ð¿ÑÐ±Ð»Ð¸ÐºÐ¾Ð²Ð°ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ.\n\n"
                "ÐÑÐ¾Ð²ÐµÑÑÑÐµ Ð¿ÑÐ°Ð²Ð° Ð±Ð¾ÑÐ° Ð² ÐºÐ°Ð½Ð°Ð»Ðµ."
            )
            return

        # Publish additional photos after the first one.
        for photo in photos[1:]:
            extra = telegram(
                "sendPhoto",
                {
                    "chat_id": CHANNEL_USERNAME,
                    "photo": photo
                }
            )
            if not extra.get("ok"):
                print("EXTRA PHOTO ERROR:", extra)

    else:
        result = send_message(
            CHANNEL_USERNAME,
            listing
        )

        if not result.get("ok"):
            print("PUBLISH ERROR:", result)
            send_message(
                chat_id,
                "â ï¸ ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¾Ð¿ÑÐ±Ð»Ð¸ÐºÐ¾Ð²Ð°ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ.\n\n"
                "ÐÑÐ¾Ð²ÐµÑÑÑÐµ Ð¿ÑÐ°Ð²Ð° Ð±Ð¾ÑÐ° Ð² ÐºÐ°Ð½Ð°Ð»Ðµ."
            )
            return

    send_message(
        chat_id,
        "ð <b>ÐÐ±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ Ð¾Ð¿ÑÐ±Ð»Ð¸ÐºÐ¾Ð²Ð°Ð½Ð¾!</b>\n\n"
        "ÐÐ½Ð¾ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ð² MADLOBA MARKET | ÐÐÐ¢Ð£ÐÐ.",
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

    if clean.lower() in ["Ð¾ÑÐ¼ÐµÐ½Ð°", "cancel"]:
        del user_states[chat_id]
        send_message(
            chat_id,
            "â ÐÐ±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ Ð¾ÑÐ¼ÐµÐ½ÐµÐ½Ð¾.",
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
        if clean.lower() in ["Ð¿ÑÐ¾Ð¿ÑÑÑÐ¸ÑÑ", "skip"]:
            ask_contact(chat_id)
            return True

        send_message(
            chat_id,
            "ð· ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾ Ð¸Ð»Ð¸ Ð½Ð°Ð¶Ð¼Ð¸ÑÐµ <b>ÐÐ¾ÑÐ¾Ð²Ð¾</b>."
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

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # Photos during listing creation.
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
                            f"ð· ÐÐ¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ <b>{count}/{MAX_PHOTOS}</b> ÑÐ¾ÑÐ¾.\n\n"
                            "ÐÐ°Ð¶Ð¼Ð¸ÑÐµ <b>ÐÐ¾ÑÐ¾Ð²Ð¾</b>.",
                            [
                                [
                                    {
                                        "text": "â ÐÐ¾ÑÐ¾Ð²Ð¾",
                                        "callback_data": "photos_done"
                                    }
                                ]
                            ]
                        )
                    else:
                        send_message(
                            chat_id,
                            f"ð· Ð¤Ð¾ÑÐ¾ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾: <b>{count}/{MAX_PHOTOS}</b>\n\n"
                            "ÐÐ¾Ð¶ÐµÑÐµ Ð¾ÑÐ¿ÑÐ°Ð²Ð¸ÑÑ ÐµÑÑ ÑÐ¾ÑÐ¾ Ð¸Ð»Ð¸ Ð½Ð°Ð¶Ð°ÑÑ <b>ÐÐ¾ÑÐ¾Ð²Ð¾</b>.",
                            [
                                [
                                    {
                                        "text": "â ÐÐ¾ÑÐ¾Ð²Ð¾",
                                        "callback_data": "photos_done"
                                    }
                                ]
                            ]
                        )
                    return

            return

        # /start
        if text.startswith("/start"):
            if chat_id in user_states:
                del user_states[chat_id]

            send_message(
                chat_id,
                "<b>ð MADLOBA MARKET | ÐÐÐ¢Ð£ÐÐ</b>\n\n"
                "ÐÐ»Ð°Ð²Ð½Ð°Ñ Ð´Ð¾ÑÐºÐ° Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ð¹ ÐÐ°ÑÑÐ¼Ð¸.\n\n"
                "ÐÑÐ¿Ð¸ Â· ÐÑÐ¾Ð´Ð°Ð¹ Â· Ð¡Ð´Ð°Ð¹ Â· ÐÐ°Ð¹Ð´Ð¸\n\n"
                "<b>ÐÑÐ±ÐµÑÐ¸ÑÐµ ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ:</b>",
                main_menu()
            )
            return

        # Active form.
        if chat_id in user_states:
            if process_form_text(chat_id, text):
                return

        # Commands.
        if text.startswith("/categories"):
            send_message(
                chat_id,
                "ð <b>ÐÑÐ±ÐµÑÐ¸ÑÐµ ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ:</b>",
                main_menu()
            )
            return

        if text.startswith("/post"):
            start_post(chat_id)
            return

        if text.startswith("/rules"):
            send_message(
                chat_id,
                "<b>ð ÐÑÐ°Ð²Ð¸Ð»Ð° MADLOBA MARKET</b>\n\n"
                "â¢ Ð¢Ð¾Ð»ÑÐºÐ¾ ÑÐµÐ°Ð»ÑÐ½ÑÐµ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ.\n"
                "â¢ ÐÐ°Ð¿ÑÐµÑÐµÐ½Ñ Ð¼Ð¾ÑÐµÐ½Ð½Ð¸ÑÐµÑÑÐ²Ð¾ Ð¸ Ð½ÐµÐ·Ð°ÐºÐ¾Ð½Ð½ÑÐµ ÑÐ¾Ð²Ð°ÑÑ.\n"
                "â¢ ÐÐµ Ð¿ÑÐ±Ð»Ð¸ÐºÑÐ¹ÑÐµ ÑÑÐ¶Ð¸Ðµ Ð¿ÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐµ Ð´Ð°Ð½Ð½ÑÐµ.\n"
                "â¢ ÐÐµ ÑÐ°Ð·Ð¼ÐµÑÐ°Ð¹ÑÐµ ÑÐ¿Ð°Ð¼.\n"
                "â¢ ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¸Ñ Ð¼Ð¾Ð¶ÐµÑ ÑÐ´Ð°Ð»Ð¸ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ, "
                "Ð½Ð°ÑÑÑÐ°ÑÑÐµÐµ Ð¿ÑÐ°Ð²Ð¸Ð»Ð°."
            )
            return

        if text.startswith("/help"):
            send_message(
                chat_id,
                "<b>â¹ï¸ MADLOBA MARKET</b>\n\n"
                "/start â Ð³Ð»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ\n"
                "/categories â ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ð¸\n"
                "/post â ÑÐ°Ð·Ð¼ÐµÑÑÐ¸ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ðµ\n"
                "/rules â Ð¿ÑÐ°Ð²Ð¸Ð»Ð°\n"
                "/help â Ð¿Ð¾Ð¼Ð¾ÑÑ"
            )
            return

    # --------------------------------------------------------
    # CALLBACK
    # --------------------------------------------------------

    if "callback_query" in update:
        callback = update["callback_query"]
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data", "")

        answer_callback(callback["id"])

        # Main menu.
        if data == "back_main":
            send_message(
                chat_id,
                "<b>ð MADLOBA MARKET | ÐÐÐ¢Ð£ÐÐ</b>\n\n"
                "ÐÑÐ±ÐµÑÐ¸ÑÐµ ÐºÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ:",
                main_menu()
            )
            return

        # Browse category.
        if data.startswith("cat_"):
            category_key = data.replace("cat_", "")

            if category_key not in CATEGORIES:
                return

            category = CATEGORIES[category_key]

            send_message(
                chat_id,
                f"<b>{esc(category['name'])}</b>\n\n"
                "ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐ°Ð·Ð´ÐµÐ»:",
                category_menu(category_key)
            )
            return

        # Browse subcategory/all.
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
                label = "ð ÐÑÐµ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ"
            else:
                label = category["subcategories"].get(
                    sub_key,
                    "Ð Ð°Ð·Ð´ÐµÐ»"
                )

            send_message(
                chat_id,
                f"<b>{esc(label)}</b>\n\n"
                "ÐÐ¾ÐºÐ° Ð·Ð´ÐµÑÑ Ð½ÐµÑ Ð¾Ð¿ÑÐ±Ð»Ð¸ÐºÐ¾Ð²Ð°Ð½Ð½ÑÑ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ð¹.\n\n"
                "ÐÐ¾Ð³Ð´Ð° Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ Ð¿Ð¾ÑÐ²ÑÑÑÑ, Ð¾Ð½Ð¸ Ð±ÑÐ´ÑÑ "
                "Ð¿Ð¾ÐºÐ°Ð·ÑÐ²Ð°ÑÑÑÑ Ð² ÑÑÐ¾Ð¼ ÑÐ°Ð·Ð´ÐµÐ»Ðµ.",
                [
                    [
                        {
                            "text": "â¬ï¸ ÐÐ°Ð·Ð°Ð´",
                            "callback_data": f"cat_{category_key}"
                        }
                    ],
                    [
                        {
                            "text": "ð  ÐÐ»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ",
                            "callback_data": "back_main"
                        }
                    ]
                ]
            )
            return

        # Start post.
        if data == "post":
            start_post(chat_id)
            return

        # Post category.
        if data.startswith("postcat_"):
            category_key = data.replace("postcat_", "")

            if category_key not in CATEGORIES:
                return

            user_states[chat_id] = {
                "step": "subcategory",
                "data": {
                    "category_key": category_key,
                    "category": CATEGORIES[category_key]["name"],
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
                f"<b>1 Â· ÐÐ°ÑÐµÐ³Ð¾ÑÐ¸Ñ</b>\n\n"
                f"{esc(CATEGORIES[category_key]['name'])}\n\n"
                "Ð¢ÐµÐ¿ÐµÑÑ Ð²ÑÐ±ÐµÑÐ¸ÑÐµ ÑÐ°Ð·Ð´ÐµÐ»:",
                post_subcategory_menu(category_key)
            )
            return

        # Post subcategory.
        if data.startswith("postsub_"):
            parts = data.split("_", 2)

            if len(parts) < 3:
                return

            category_key = parts[1]
            subcategory_key = parts[2]

            if (
                category_key not in CATEGORIES
                or subcategory_key not in CATEGORIES[category_key]["subcategories"]
            ):
                return

            state = user_states.get(chat_id)

            if not state:
                start_post(chat_id)
                return

            state["step"] = "type"
            state["data"]["subcategory_key"] = subcategory_key
            state["data"]["subcategory"] = (
                CATEGORIES[category_key]["subcategories"][subcategory_key]
            )

            send_message(
                chat_id,
                "<b>2 Â· Ð¢Ð¸Ð¿ Ð¾Ð±ÑÑÐ²Ð»ÐµÐ½Ð¸Ñ</b>\n\n"
                f"{esc(state['data']['subcategory'])}\n\n"
                "Ð§ÑÐ¾ Ð²Ñ ÑÐ¾ÑÐ¸ÑÐµ ÑÐ´ÐµÐ»Ð°ÑÑ?",
                post_type_menu(category_key)
            )
            return

        # Post type.
        if data.startswith("posttype_"):
            type_key = data.replace("posttype_", "")

            if type_key not in TYPE_NAMES:
                return

            if chat_id not in user_states:
                start_post(chat_id)
                return

            state = user_states[chat_id]
            state["step"] = "title"
            state["data"]["type_key"] = type_key
            state["data"]["type"] = TYPE_NAMES[type_key]

            ask_title(chat_id)
            return

        # Photos.
        if data == "photos_done":
            if chat_id not in user_states:
                return

            if user_states[chat_id]["step"] != "photos":
                return

            ask_contact(chat_id)
            return

        if data == "photos_skip":
            if chat_id not in user_states:
                return

            if user_states[chat_id]["step"] != "photos":
                return

            user_states[chat_id]["data"]["photos"] = []
            ask_contact(chat_id)
            return

        # Edit menu.
        if data == "edit_menu":
            if chat_id not in user_states:
                start_post(chat_id)
                return

            send_message(
                chat_id,
                "<b>âï¸ Ð§ÑÐ¾ ÑÐ¾ÑÐ¸ÑÐµ Ð¸Ð·Ð¼ÐµÐ½Ð¸ÑÑ?</b>",
                edit_menu()
            )
            return

        # Edit fields.
        if data in [
            "edit_title",
            "edit_price",
            "edit_district",
            "edit_description",
            "edit_contact",
            "edit_photos"
        ]:
            field = data.replace("edit_", "")
            start_edit(chat_id, field)
            return

        # Show preview.
        if data == "show_preview":
            show_preview(chat_id)
            return

        # Restart.
        if data == "restart_post":
            start_post(chat_id)
            return

        # Cancel.
        if data == "cancel_post":
            if chat_id in user_states:
                del user_states[chat_id]

            send_message(
                chat_id,
                "â <b>Ð Ð°Ð·Ð¼ÐµÑÐµÐ½Ð¸Ðµ Ð¾ÑÐ¼ÐµÐ½ÐµÐ½Ð¾.</b>\n\n"
                "ÐÑÐ±ÐµÑÐ¸ÑÐµ Ð´ÐµÐ¹ÑÑÐ²Ð¸Ðµ:",
                main_menu()
            )
            return

        # Publish.
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
            print("PROCESS UPDATE ERROR:", e)

    return "OK"


# ============================================================
# WEBHOOK SETUP
# ============================================================

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
    {"url": webhook_url}
)

print(
    "SET WEBHOOK RESULT:",
    set_webhook_result
)

webhook_info = telegram("getWebhookInfo")

print(
    "WEBHOOK INFO:",
    webhook_info
)

print(
    "CHANNEL USERNAME:",
    CHANNEL_USERNAME
)

print("===== WEBHOOK SETUP FINISHED =====")


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
