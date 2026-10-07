# -*- coding: utf-8 -*-

import os
import html
import json
import time
import hmac
import hashlib
import urllib.parse
import re

import requests

from flask import Flask, request, Response, jsonify


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

# Telegram ID администратора, которому приходят объявления
# на модерацию.
ADMIN_CHAT_ID = os.environ.get(
    "ADMIN_CHAT_ID",
    ""
).strip()

# Максимальное количество фотографий
MAX_PHOTOS = 8


app = Flask(__name__)

# Временные данные пользователей.
# Для первой версии этого достаточно.
states = {}

# Заявки, ожидающие решения администратора.
# Важно: эти данные хранятся в памяти Render.
moderation_requests = {}

# Текущее редактирование объявления модератором.
moderator_sessions = {}

# Счётчик заявок на модерацию.
next_moderation_id = 1

# Опубликованные объявления.
# Хранятся отдельно от states, потому что states очищается
# после успешной публикации.
# ============================================================
# ХРАНИЛИЩЕ ОБЪЯВЛЕНИЙ — SUPABASE
# ============================================================

# Supabase подключается через Render Environment.
# НИКОГДА не вставляйте ключ прямо в этот файл.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip() or os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
).strip()

# Пока Mini App не выбирает город при создании объявления,
# объявления из Telegram-бота считаем объявлениями Батуми.
DEFAULT_CITY_SLUG = os.environ.get("DEFAULT_CITY_SLUG", "batumi").strip().lower() or "batumi"

# Локальный файл оставляем как резервный fallback.
LISTINGS_FILE = os.environ.get(
    "LISTINGS_FILE",
    "listings.json"
).strip() or "listings.json"

published_listings = []
next_listing_id = 1


def supabase_enabled():
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def supabase_request(method, path, params=None, payload=None):
    if not supabase_enabled():
        return None

    url = f"{SUPABASE_URL}/rest/v1/{path.lstrip('/')}"
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    if method.upper() in {"POST", "PATCH", "DELETE"}:
        headers["Prefer"] = "return=representation"

    try:
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            params=params or {},
            json=payload,
            timeout=25,
        )
        if not response.ok:
            print(
                "SUPABASE ERROR:",
                response.status_code,
                response.text[:1000],
            )
            return None

        if not response.text:
            return []

        return response.json()
    except Exception as error:
        print("SUPABASE REQUEST ERROR:", repr(error))
        return None


def load_local_published_listings():
    global published_listings, next_listing_id

    try:
        with open(LISTINGS_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)

        if isinstance(payload, dict):
            published_listings = payload.get("listings", [])
            next_listing_id = int(payload.get("next_id", 1))
        elif isinstance(payload, list):
            published_listings = payload
            next_listing_id = 1
        else:
            published_listings = []
            next_listing_id = 1

        if not isinstance(published_listings, list):
            published_listings = []

        if published_listings:
            max_id = max(
                int(item.get("id", 0))
                for item in published_listings
                if str(item.get("id", "")).isdigit()
            )
            next_listing_id = max(next_listing_id, max_id + 1)

    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        published_listings = []
        next_listing_id = 1


def save_local_published_listings():
    payload = {
        "next_id": next_listing_id,
        "listings": published_listings,
    }

    try:
        directory = os.path.dirname(LISTINGS_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)

        temporary = LISTINGS_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        os.replace(temporary, LISTINGS_FILE)
        return True
    except OSError as error:
        print("LISTINGS SAVE ERROR:", repr(error))
        return False


def _price_number(value):
    try:
        if value in (None, "", "Бесплатно"):
            return None
        cleaned = str(value).replace(" ", "").replace(",", ".")
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _supabase_city_id(slug_value):
    rows = supabase_request(
        "GET",
        "cities",
        params={
            "select": "id,slug",
            "slug": f"eq.{slug_value}",
            "limit": "1",
        },
    )
    return rows[0]["id"] if rows else None


def _supabase_category_id(category_key):
    # Ключи категорий внутри Telegram-бота отличаются от slug
    # категорий в Supabase, поэтому явно сопоставляем их.
    category_slug_map = {
        "realestate": "real-estate",
        "auto": "cars",
        "tech": "electronics",
        "home": "home",
        "kids": "kids",
        "work": "jobs-services",
        "give": "free",
        "search": "wanted",
    }

    category_slug = category_slug_map.get(
        str(category_key).strip().lower(),
        str(category_key).strip().lower(),
    )

    rows = supabase_request(
        "GET",
        "categories",
        params={
            "select": "id,slug",
            "slug": f"eq.{category_slug}",
            "limit": "1",
        },
    )
    return rows[0]["id"] if rows else None


def _supabase_user_id(telegram_id):
    if not telegram_id:
        return None

    telegram_id = int(telegram_id)

    existing = supabase_request(
        "GET",
        "users",
        params={
            "select": "id,telegram_id",
            "telegram_id": f"eq.{telegram_id}",
            "limit": "1",
        },
    )
    if existing:
        return existing[0]["id"]

    created = supabase_request(
        "POST",
        "users",
        payload={"telegram_id": telegram_id},
    )
    if created:
        return created[0]["id"]

    # Race condition / повторная попытка.
    existing = supabase_request(
        "GET",
        "users",
        params={
            "select": "id,telegram_id",
            "telegram_id": f"eq.{telegram_id}",
            "limit": "1",
        },
    )
    return existing[0]["id"] if existing else None


def _row_to_listing(row):
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    item = dict(metadata)
    item["id"] = row.get("id", item.get("id"))
    item["status"] = row.get("status", "published")
    item["channel_message_id"] = row.get("channel_message_id")
    item["channel_post_url"] = row.get("channel_url", "") or ""

    # Данные из нормализованных колонок имеют приоритет.
    if row.get("address"):
        item["district"] = row["address"]
    if row.get("description") is not None:
        item["description"] = row.get("description") or ""
    if row.get("currency"):
        item["currency"] = row["currency"]
    if row.get("price") is not None:
        item["price"] = str(row["price"])

    photos = []
    for photo in row.get("listing_photos", []) or []:
        if photo.get("photo_url"):
            photos.append(photo["photo_url"])
    if photos:
        item["photos"] = photos

    return item


def load_supabase_published_listings():
    global published_listings, next_listing_id

    if not supabase_enabled():
        return False

    # В память загружаем только базовую информацию.
    # Полный каталог теперь запрашивается постранично через
    # load_supabase_catalog_page(), чтобы 10 000+ объявлений
    # не загружались целиком при старте бота.
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select": "id,status,channel_message_id,channel_url,description,currency,price,address,metadata,created_at",
            "status": "eq.published",
            "order": "created_at.desc",
            "limit": "1",
        },
    )

    if rows is None:
        return False

    published_listings = [_row_to_listing(row) for row in rows]
    numeric_ids = [
        int(item["id"])
        for item in published_listings
        if str(item.get("id", "")).isdigit()
    ]
    next_listing_id = max(numeric_ids, default=0) + 1
    return True


def _supabase_photo_rows(listing_ids):
    if not listing_ids:
        return []

    values = ",".join(str(int(value)) for value in listing_ids)
    return supabase_request(
        "GET",
        "listing_photos",
        params={
            "select": "listing_id,photo_url,sort_order",
            "listing_id": f"in.({values})",
            "order": "sort_order.asc",
        },
    ) or []


def _attach_catalog_photos(rows):
    photos_by_listing = {}
    for photo in _supabase_photo_rows([row.get("id") for row in rows]):
        listing_id = photo.get("listing_id")
        photo_url = photo.get("photo_url")
        if listing_id and photo_url:
            photos_by_listing.setdefault(listing_id, []).append(photo_url)

    result = []
    for row in rows:
        item = _row_to_listing(row)
        item["photos"] = photos_by_listing.get(row.get("id"), [])
        result.append(item)
    return result


def load_supabase_catalog_page(key, sub, page=0, per_page=20):
    """
    Загружает только текущую страницу каталога из Supabase.
    Никаких 10 000 объявлений и фотографий в память.
    Возвращает (items, has_next).
    """
    if not supabase_enabled():
        return [], False

    page = max(0, int(page))
    per_page = max(1, min(int(per_page), 50))
    offset = page * per_page

    # Обычные категории фильтруются по индексируемому category_id.
    if key != "search":
        category_id = _supabase_category_id(key)
        if not category_id:
            return [], False

        params = {
            "select": "id,status,channel_message_id,channel_url,description,currency,price,address,metadata,created_at",
            "status": "eq.published",
            "category_id": f"eq.{category_id}",
            "order": "created_at.desc",
            "offset": str(offset),
            "limit": str(per_page + 1),
        }

        if sub != "all":
            params["metadata->>subcategory_key"] = f"eq.{sub}"

        rows = supabase_request("GET", "listings", params=params) or []
        has_next = len(rows) > per_page
        rows = rows[:per_page]
        return _attach_catalog_photos(rows), has_next

    # Раздел "Ищу" хранится отдельной категорией.
    # Для конкретной тематики объединяем обычную категорию и wanted-объявления.
    # Для первой версии достаточно ограниченных страниц: запрос никогда не
    # вытаскивает весь каталог.
    if sub == "all":
        rows = supabase_request(
            "GET",
            "listings",
            params={
                "select": "id,status,channel_message_id,channel_url,description,currency,price,address,metadata,created_at",
                "status": "eq.published",
                "order": "created_at.desc",
                "offset": str(offset),
                "limit": str(per_page + 1),
            },
        ) or []
        has_next = len(rows) > per_page
        rows = rows[:per_page]
        return _attach_catalog_photos(rows), has_next

    normal_category_id = _supabase_category_id(sub)
    wanted_category_id = _supabase_category_id("search")
    if not normal_category_id and not wanted_category_id:
        return [], False

    candidates = []
    wanted_needed = offset + per_page + 1

    if normal_category_id:
        candidates.extend(supabase_request(
            "GET",
            "listings",
            params={
                "select": "id,status,channel_message_id,channel_url,description,currency,price,address,metadata,created_at",
                "status": "eq.published",
                "category_id": f"eq.{normal_category_id}",
                "order": "created_at.desc",
                "offset": "0",
                "limit": str(wanted_needed),
            },
        ) or [])

    if wanted_category_id:
        candidates.extend(supabase_request(
            "GET",
            "listings",
            params={
                "select": "id,status,channel_message_id,channel_url,description,currency,price,address,metadata,created_at",
                "status": "eq.published",
                "category_id": f"eq.{wanted_category_id}",
                "metadata->>subcategory_key": f"eq.{sub}",
                "order": "created_at.desc",
                "offset": "0",
                "limit": str(wanted_needed),
            },
        ) or [])

    # Объединяем и сортируем только ограниченный набор кандидатов.
    unique = {}
    for row in candidates:
        unique[str(row.get("id"))] = row

    rows = sorted(
        unique.values(),
        key=lambda row: row.get("created_at", ""),
        reverse=True,
    )
    page_rows = rows[offset:offset + per_page]
    has_next = len(rows) > offset + per_page
    return _attach_catalog_photos(page_rows), has_next

def save_listing_to_supabase(data, publish_result, user_chat_id=None):
    """Создаёт опубликованное объявление и его фотографии в Supabase."""

    if not supabase_enabled():
        return None

    city_slug = str(data.get("city_slug") or DEFAULT_CITY_SLUG).strip().lower()
    city_id = _supabase_city_id(city_slug)
    category_id = _supabase_category_id(data.get("category_key", ""))

    if not city_id:
        print("SUPABASE: city not found:", city_slug)
        return None
    if not category_id:
        print("SUPABASE: category not found:", data.get("category_key"))
        return None

    user_id = _supabase_user_id(user_chat_id)

    result = publish_result.get("result") if isinstance(publish_result, dict) else None
    message_id = None
    if isinstance(result, list) and result:
        message_id = result[0].get("message_id")
    elif isinstance(result, dict):
        message_id = result.get("message_id")

    channel_url = ""
    if CHANNEL_USERNAME.startswith("@") and message_id:
        channel_url = f"https://t.me/{CHANNEL_USERNAME[1:]}/{message_id}"

    metadata = dict(data)
    metadata.pop("_telegram_id", None)
    metadata["legacy_id"] = data.get("id")

    payload = {
        "user_id": user_id,
        "city_id": city_id,
        "category_id": category_id,
        "title": listing_title(data),
        "description": data.get("description", "") or "",
        "price": _price_number(data.get("price")),
        "currency": data.get("currency", "") or "USD",
        "phone": data.get("contact", "") or "",
        "address": data.get("district", "") or "",
        "status": "published",
        "channel_message_id": message_id,
        "channel_url": channel_url,
        "metadata": metadata,
    }

    created = supabase_request("POST", "listings", payload=payload)
    if not created:
        return None

    row = created[0]
    listing_id = row.get("id")

    photos = [
        {
            "listing_id": listing_id,
            "photo_url": str(photo),
            "sort_order": index,
        }
        for index, photo in enumerate(data.get("photos", []))
        if photo
    ]

    if photos:
        inserted_photos = supabase_request(
            "POST",
            "listing_photos",
            payload=photos,
        )
        if inserted_photos is None:
            print("SUPABASE: listing created, but photos were not saved")

    return _row_to_listing({**row, "listing_photos": photos})


def register_published_listing(data, publish_result, user_chat_id=None):
    global next_listing_id

    item = dict(data)
    item["details"] = dict(data.get("details", {}))
    item["photos"] = list(data.get("photos", []))
    item["status"] = "published"

    result = publish_result.get("result") if isinstance(publish_result, dict) else None
    message_id = None
    if isinstance(result, list) and result:
        message_id = result[0].get("message_id")
    elif isinstance(result, dict):
        message_id = result.get("message_id")

    item["channel_message_id"] = message_id
    if CHANNEL_USERNAME.startswith("@"):
        item["channel_post_url"] = (
            f"https://t.me/{CHANNEL_USERNAME[1:]}/{message_id}"
            if message_id else ""
        )
    else:
        item["channel_post_url"] = ""

    # Сначала пытаемся сохранить в Supabase.
    supabase_item = save_listing_to_supabase(
        data,
        publish_result,
        user_chat_id=user_chat_id,
    )

    if supabase_item:
        item = supabase_item
        # Обновляем память из БД, чтобы каталог сразу видел новое объявление.
        if not load_supabase_published_listings():
            published_listings.insert(0, item)
    else:
        # Резервный режим — старый listings.json.
        item["id"] = next_listing_id
        next_listing_id += 1
        published_listings.insert(0, item)
        save_local_published_listings()
        print("SUPABASE: fallback to local listings.json")

    return item


def load_published_listings():
    """Загрузка каталога. Supabase — основной источник, JSON — fallback."""
    load_local_published_listings()
    if supabase_enabled():
        if load_supabase_published_listings():
            print("LISTINGS STORAGE: Supabase")
        else:
            print("LISTINGS STORAGE: local fallback")
    else:
        print("LISTINGS STORAGE: local listings.json (Supabase env not set)")


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
    callback_id,
    text=None,
    show_alert=False
):

    payload = {
        "callback_query_id": callback_id
    }

    if text:
        payload["text"] = str(text)

    if show_alert:
        payload["show_alert"] = True

    return api(
        "answerCallbackQuery",
        payload
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

CHANNEL_MENU_TEXT = (
    "<b>🛒 MADLOBA MARKET | БАТУМИ</b>\n\n"
    "<b>Главная доска объявлений Батуми</b>\n\n"
    "Покупайте • Продавайте • Сдавайте • Находите\n\n"
    "<b>Выберите категорию:</b>"
)


def main_menu():

    return [

        [

            {

                "text":
                    "🏠 Недвижимость",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_realestate"
            },

            {

                "text":
                    "🚗 Авто",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_auto"
            }
        ],

        [

            {

                "text":
                    "📱 Техника",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_tech"
            },

            {

                "text":
                    "🛋 Дом и мебель",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_home"
            }
        ],

        [

            {

                "text":
                    "👶 Детское",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_kids"
            },

            {

                "text":
                    "💼 Работа и услуги",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_work"
            }
        ],

        [

            {

                "text":
                    "🎁 Отдам",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_give"
            },

            {

                "text":
                    "🔎 Ищу",

                "url":
                    "https://t.me/MadlobaMarketBot?start=cat_search"
            }
        ],

        [

            {

                "text":
                    "🚀 РАЗМЕСТИТЬ ОБЪЯВЛЕНИЕ",

                "url":
                    "https://t.me/MadlobaMarketBot?start=post"
            }
        ],
    ]


# ============================================================
# ЗАГРУЗКА КАТАЛОГА ИЗ SUPABASE
# ============================================================

# CATEGORIES уже объявлен к этому моменту, поэтому каталог можно
# безопасно синхронизировать при старте приложения.

def listing_matches(item, key, sub):
    category = item.get("category_key", "")
    subcategory = item.get("subcategory_key", "")

    if key == "search" and sub == "all":
        return True

    if key == "search":
        if sub == "services":
            return category == "work"
        if sub == "other":
            return category not in {
                "realestate", "auto", "tech", "home", "kids", "work"
            }
        return category == sub or (
            category == "search" and subcategory == sub
        )

    if sub == "all":
        return category == key

    return category == key and subcategory == sub


def listing_title(item):
    category = item.get("category", "Объявление")
    subcategory = item.get("subcategory", "")
    type_name = item.get("type", "")
    parts = [category]
    if subcategory:
        parts.append(subcategory)
    if type_name:
        parts.append(type_name)
    return " · ".join(parts)


def listing_short_text(item):
    details = item.get("details", {}) or {}
    lines = [f"<b>#{item.get('id', '')} {esc(listing_title(item))}</b>"]

    detail_parts = []
    if isinstance(details, dict):
        for value in details.values():
            if value not in (None, ""):
                detail_parts.append(str(value))
    if detail_parts:
        lines.append(" · ".join(esc(value) for value in detail_parts))

    price = item.get("price", "")
    currency = item.get("currency", "")
    if price:
        lines.append(f"💰 <b>{esc(price)} {esc(currency)}</b>")

    district = item.get("district", "")
    if district:
        lines.append(f"📍 {esc(district)}")

    description = item.get("description", "")
    if description:
        compact = " ".join(str(description).split())
        if len(compact) > 120:
            compact = compact[:117] + "..."
        lines.append(esc(compact))

    return "\n".join(lines)


def listing_keyboard(key, sub, page, item):
    rows = []
    url = item.get("channel_post_url", "")
    if url:
        rows.append([{"text": "📣 Открыть в канале", "url": url}])
    return rows


def send_listing_catalog(chat_id, key, sub, page=0):
    # 20 объявлений на одну страницу.
    # Supabase при этом загружает только текущую страницу, а не весь каталог.
    per_page = 20

    if supabase_enabled():
        current, has_next = load_supabase_catalog_page(
            key, sub, page=page, per_page=per_page
        )
    else:
        # Старый локальный режим сохраняем только как fallback.
        matches = [
            item for item in published_listings
            if listing_matches(item, key, sub)
        ]
        start = page * per_page
        current = matches[start:start + per_page]
        has_next = start + per_page < len(matches)

    if key == "search":
        label = (
            "📋 Все объявления"
            if sub == "all"
            else CATEGORIES["search"]["subs"].get(sub, "Раздел")
        )
    else:
        label = (
            "📋 Все объявления"
            if sub == "all"
            else CATEGORIES[key]["subs"].get(sub, "Раздел")
        )

    if not current:
        send(
            chat_id,
            f"<b>{esc(label)}</b>\n\n"
            "Пока нет опубликованных объявлений.\n\n"
            "Разместите первое объявление — и оно появится здесь.",
            [
                [{"text": "⬅️ Назад", "callback_data": f"cat_{key}"}],
                [{"text": "🏠 Главное меню", "callback_data": "back_main"}],
            ],
        )
        return

    send(
        chat_id,
        f"<b>{esc(label)}</b>\n\n"
        f"Страница <b>{page + 1}</b>",
        [[{"text": "⬅️ Назад", "callback_data": f"cat_{key}"}]],
    )

    for item in current:
        photos = item.get("photos", []) or []
        text_value = listing_short_text(item)
        keyboard = listing_keyboard(key, sub, page, item)
        if photos:
            result = send_album(chat_id, photos, text_value)
            if result.get("ok") and keyboard:
                send(chat_id, "Выберите действие:", keyboard)
            elif not result.get("ok"):
                send(chat_id, text_value, keyboard)
        else:
            send(chat_id, text_value, keyboard)

    navigation = []
    if page > 0:
        navigation.append({
            "text": "⬅️ Предыдущие",
            "callback_data": f"browsepage_{key}_{sub}_{page - 1}",
        })
    if has_next:
        navigation.append({
            "text": "Следующие ➡️",
            "callback_data": f"browsepage_{key}_{sub}_{page + 1}",
        })

    keyboard = []
    if navigation:
        keyboard.append(navigation)
    keyboard.append([
        {"text": "⬅️ Назад", "callback_data": f"cat_{key}"},
        {"text": "🏠 Меню", "callback_data": "back_main"},
    ])
    send(chat_id, "Выберите действие:", keyboard)

load_published_listings()


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

                "url":
                    "https://t.me/MadlobaMarketBot?start=post"
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

        "city_slug":
            DEFAULT_CITY_SLUG,

        "city":
            "Batumi" if DEFAULT_CITY_SLUG == "batumi" else DEFAULT_CITY_SLUG.title(),

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

    remove_moderation_requests_for_user(
        chat_id
    )

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

    details = data[
        "details"
    ]

    # Заголовок — только суть объявления.
    # Тип действия (Сдам / Сниму / Продам / Куплю / Ищу)
    # уже показывается в верхней строке карточки, поэтому
    # повторять его здесь не нужно.

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
            "apartment": "квартира",
            "house": "дом",
            "room": "комната",
            "commercial": "коммерческое помещение",
            "land": "земельный участок",
            "garage": "гараж / парковка",
        }

        property_name = property_names.get(
            subcategory,
            "объект недвижимости"
        )

        if subcategory == "apartment" and rooms:
            return f"{rooms}-комнатная квартира"

        if subcategory == "room" and rooms:
            return f"{rooms}-комнатная комната"

        return property_name

    if category == "auto":

        model = (
            details.get(
                "make_model",
                ""
            )
            or "автомобиль"
        )

        year = details.get(
            "year",
            ""
        )

        title = model

        if year:
            title += f" · {year}"

        return title

    if category == "tech":

        return (
            details.get(
                "brand_model",
                ""
            )
            or "Техника"
        )

    if category == "work":

        return (
            details.get(
                "service",
                ""
            )
            or "Работа / услуга в Батуми"
        )

    if category == "search":

        return (
            details.get(
                "requirements",
                ""
            )
            or ""
        )

    # Для «Отдам» отдельный заголовок не нужен:
    # тип и подкатегория уже показаны выше.
    if category == "give":
        return ""

    return ""


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
                    "📨 Отправить на проверку",

                "callback_data":
                    "submit_moderation"
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
# КНОПКИ МОДЕРАЦИИ
# ============================================================

def moderation_keyboard(
    request_id
):

    return [
        [
            {
                "text": "✏️ РЕДАКТИРОВАТЬ",
                "callback_data": f"mod_edit_{request_id}"
            }
        ],
        [
            {
                "text": "🟢 ОПУБЛИКОВАТЬ",
                "callback_data": f"mod_publish_{request_id}"
            }
        ],
        [
            {
                "text": "🔴 ОТКЛОНИТЬ",
                "callback_data": f"mod_reject_{request_id}"
            }
        ]
    ]


def moderation_edit_menu(request_id):

    return [
        [
            {"text": "💰 Цена", "callback_data": f"mod_field_price_{request_id}"},
            {"text": "📍 Локация", "callback_data": f"mod_field_district_{request_id}"}
        ],
        [
            {"text": "📝 Описание", "callback_data": f"mod_field_description_{request_id}"},
            {"text": "📞 Контакт", "callback_data": f"mod_field_contact_{request_id}"}
        ],
        [
            {"text": "📋 Характеристики", "callback_data": f"mod_details_{request_id}"}
        ],
        [
            {"text": "⬅️ К объявлению", "callback_data": f"mod_back_{request_id}"}
        ]
    ]


def moderation_detail_menu(request_id, data):

    fields = CATEGORIES[data["category_key"]]["fields"]
    rows = []

    for i, (_, label) in enumerate(fields):
        rows.append([
            {
                "text": label,
                "callback_data": f"mod_detail_{request_id}_{i}"
            }
        ])

    rows.append([
        {
            "text": "⬅️ Назад",
            "callback_data": f"mod_edit_{request_id}"
        }
    ])

    return rows


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

def publish_listing(
    data
):

    text = build_listing(
        data
    )

    photos = data[
        "photos"
    ]

    # Канал не указан
    if not CHANNEL_USERNAME:

        return {

            "ok":
                False,

            "description":
                "CHANNEL_USERNAME is empty"
        }

    # Публикация с фотографиями
    if photos:

        return send_album(

            CHANNEL_USERNAME,

            photos,

            text
        )

    # Публикация без фотографий
    return api(

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


def remove_moderation_requests_for_user(
    chat_id
):

    for request_id, item in list(
        moderation_requests.items()
    ):

        if item.get(
            "user_chat_id"
        ) == chat_id:

            moderation_requests.pop(
                request_id,
                None
            )


def edit_moderation_message(
    chat_id,
    message_id,
    text,
    keyboard=None
):

    if not chat_id or not message_id:
        return

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

    api(
        "editMessageText",
        data
    )


def submit_for_moderation(
    chat_id
):

    global next_moderation_id

    if chat_id not in states:
        return

    if not ADMIN_CHAT_ID:

        send(

            chat_id,

            "<b>⚠️ Модератор пока не настроен.</b>\n\n"

            "В Render → Environment добавьте "
            "переменную <code>ADMIN_CHAT_ID</code>."
        )

        return

    if states[chat_id]["step"] == "moderation":

        send(

            chat_id,

            "⏳ <b>Объявление уже отправлено "
            "на проверку.</b>\n\n"

            "Дождитесь решения администратора."
        )

        return

    data = states[
        chat_id
    ]["data"]

    # Нужен для связи объявления с пользователем в Supabase.
    data["_telegram_id"] = chat_id

    request_id = next_moderation_id

    next_moderation_id += 1

    moderation_requests[
        request_id
    ] = {

        "user_chat_id":
            chat_id,

        "message_chat_id":
            None,

        "message_id":
            None
    }

    states[
        chat_id
    ]["step"] = "moderation"

    text = build_listing(
        data
    )

    photos = data[
        "photos"
    ]

    admin_header = (

        "<b>🛡 НОВОЕ ОБЪЯВЛЕНИЕ "
        "НА МОДЕРАЦИЮ</b>\n\n"

        f"👤 ID автора: <code>{chat_id}</code>\n"

        f"🔢 Заявка: <code>#{request_id}</code>\n\n"
    )

    # Если есть фотографии — отправляем их администратору
    # компактным альбомом.
    if photos:

        result = send_album(

            ADMIN_CHAT_ID,

            photos,

            text
        )

        if not result.get(
            "ok"
        ):

            moderation_requests.pop(
                request_id,
                None
            )

            states[
                chat_id
            ]["step"] = "preview"

            send(

                chat_id,

                "<b>⚠️ Не удалось отправить "
                "объявление администратору.</b>\n\n"

                "Попробуйте ещё раз."
            )

            return

        control = send(

            ADMIN_CHAT_ID,

            admin_header +

            f"📷 Фотографий: "
            f"<b>{len(photos)}</b>\n\n"

            "Выберите действие:",

            moderation_keyboard(
                request_id
            )
        )

    # Без фотографий — всё объявление сразу
    # отправляем администратору вместе с кнопками.
    else:

        control = send(

            ADMIN_CHAT_ID,

            admin_header +

            text +

            "\n\n"
            "Выберите действие:",

            moderation_keyboard(
                request_id
            )
        )

    if not control.get(
        "ok"
    ):

        moderation_requests.pop(
            request_id,
            None
        )

        states[
            chat_id
        ]["step"] = "preview"

        send(

            chat_id,

            "<b>⚠️ Не удалось отправить "
            "объявление на модерацию.</b>\n\n"

            "Попробуйте ещё раз."
        )

        return

    result_data = control.get(
        "result",
        {}
    )

    moderation_requests[
        request_id
    ]["message_chat_id"] = (
        result_data
        .get("chat", {})
        .get("id")
    )

    moderation_requests[
        request_id
    ]["message_id"] = (
        result_data
        .get("message_id")
    )

    send(

        chat_id,

        "<b>📨 Объявление отправлено "
        "на проверку.</b>\n\n"

        "После решения администратора "
        "вы получите уведомление.",

        main_menu()
    )



def moderator_is_allowed(admin_user_id):
    try:
        return bool(ADMIN_CHAT_ID) and int(admin_user_id) == int(ADMIN_CHAT_ID)
    except Exception:
        return False


def moderation_control_text(request_id):
    item = moderation_requests.get(request_id)
    if not item:
        return None
    user_chat_id = item["user_chat_id"]
    if user_chat_id not in states:
        return None
    data = states[user_chat_id]["data"]
    return (
        "<b>🛡 ОБЪЯВЛЕНИЕ НА МОДЕРАЦИИ</b>\n\n"
        f"👤 ID автора: <code>{user_chat_id}</code>\n"
        f"🔢 Заявка: <code>#{request_id}</code>\n\n"
        f"{build_listing(data)}\n\n"
        "Выберите действие:"
    )


def show_moderation_editor(admin_chat_id, request_id):
    if not moderator_is_allowed(admin_chat_id):
        return
    item = moderation_requests.get(request_id)
    if not item or item["user_chat_id"] not in states:
        send(admin_chat_id, "⚠️ Заявка уже обработана или недоступна.")
        return
    moderator_sessions[admin_chat_id] = {"request_id": request_id, "step": "menu"}
    send(
        admin_chat_id,
        "<b>✏️ РЕДАКТИРОВАНИЕ ОБЪЯВЛЕНИЯ</b>\n\nВыберите, что нужно исправить:",
        moderation_edit_menu(request_id)
    )


def show_updated_moderation(admin_chat_id, request_id):
    text = moderation_control_text(request_id)
    item = moderation_requests.get(request_id)
    if not text or not item:
        return
    edit_moderation_message(
        item.get("message_chat_id"),
        item.get("message_id"),
        text,
        moderation_keyboard(request_id)
    )


def process_moderator_text(admin_chat_id, text):
    session = moderator_sessions.get(admin_chat_id)
    if not session:
        return False
    request_id = session.get("request_id")
    item = moderation_requests.get(request_id)
    if not item or item["user_chat_id"] not in states:
        moderator_sessions.pop(admin_chat_id, None)
        send(admin_chat_id, "⚠️ Заявка уже обработана или недоступна.")
        return True

    data = states[item["user_chat_id"]]["data"]
    step = session.get("step")
    text = text.strip()

    if not text:
        send(admin_chat_id, "Введите новое значение.")
        return True

    if step == "price":
        data["price"] = text.replace(" ", "").replace(",", ".")
    elif step == "district":
        data["district"] = text
    elif step == "description":
        data["description"] = "" if text.lower() in {"ничего", "нет", "нечего", "без описания", "пропустить", "-"} else text
    elif step == "contact":
        data["contact"] = text
    elif step.startswith("detail_"):
        try:
            index = int(step.split("_", 1)[1])
        except ValueError:
            moderator_sessions.pop(admin_chat_id, None)
            return True
        fields = CATEGORIES[data["category_key"]]["fields"]
        if index < len(fields):
            data["details"][fields[index][0]] = text
    else:
        return False

    moderator_sessions.pop(admin_chat_id, None)
    send(admin_chat_id, "✅ <b>Изменение сохранено.</b>\n\nПроверьте объявление ещё раз.")
    show_updated_moderation(admin_chat_id, request_id)
    return True

def moderate_publish(
    request_id,
    admin_user_id,
    callback_id
):

    # Проверяем, что кнопку нажал именно
    # назначенный администратор.
    try:

        if int(
            admin_user_id
        ) != int(
            ADMIN_CHAT_ID
        ):

            answer(

                callback_id,

                "⛔ У вас нет прав администратора.",

                True
            )

            return

    except Exception:

        answer(

            callback_id,

            "⚠️ ADMIN_CHAT_ID настроен неправильно.",

            True
        )

        return

    item = moderation_requests.get(
        request_id
    )

    if not item:

        answer(

            callback_id,

            "Заявка уже обработана.",

            True
        )

        return

    user_chat_id = item[
        "user_chat_id"
    ]

    if user_chat_id not in states:

        moderation_requests.pop(
            request_id,
            None
        )

        answer(

            callback_id,

            "Объявление больше недоступно.",

            True
        )

        return

    data = states[
        user_chat_id
    ]["data"]

    result = publish_listing(
        data
    )

    if not result.get(
        "ok"
    ):

        answer(

            callback_id,

            "⚠️ Не удалось опубликовать.",

            True
        )

        send(

            admin_user_id,

            "<b>⚠️ Не удалось опубликовать "
            "объявление.</b>\n\n"

            "Проверьте:\n"
            "• бот является администратором канала;\n"
            "• у бота есть право публиковать сообщения;\n"
            "• CHANNEL_USERNAME указан правильно."
        )

        return

    # Сохраняем объявление в каталоге ДО очистки states.
    register_published_listing(
        data,
        result,
        user_chat_id=user_chat_id
    )

    moderation_requests.pop(
        request_id,
        None
    )

    moderator_sessions.pop(
        int(admin_user_id),
        None
    )

    states.pop(
        user_chat_id,
        None
    )

    answer(

        callback_id,

        "🟢 Опубликовано!"
    )

    edit_moderation_message(

        item.get(
            "message_chat_id"
        ),

        item.get(
            "message_id"
        ),

        "<b>🟢 ОПУБЛИКОВАНО</b>\n\n"

        f"Заявка: <code>#{request_id}</code>\n"

        f"Автор ID: <code>{user_chat_id}</code>"
    )

    send(

        user_chat_id,

        "<b>🎉 Объявление опубликовано!</b>\n\n"

        "Оно прошло модерацию и добавлено "
        "в <b>MADLOBA MARKET | БАТУМИ</b>.",

        main_menu()
    )


def moderate_reject(
    request_id,
    admin_user_id,
    callback_id
):

    try:

        if int(
            admin_user_id
        ) != int(
            ADMIN_CHAT_ID
        ):

            answer(

                callback_id,

                "⛔ У вас нет прав администратора.",

                True
            )

            return

    except Exception:

        answer(

            callback_id,

            "⚠️ ADMIN_CHAT_ID настроен неправильно.",

            True
        )

        return

    item = moderation_requests.get(
        request_id
    )

    if not item:

        answer(

            callback_id,

            "Заявка уже обработана.",

            True
        )

        return

    user_chat_id = item[
        "user_chat_id"
    ]

    # Удаляем заявку из очереди.
    # Само объявление сохраняем, чтобы автор
    # мог его исправить и отправить повторно.
    moderation_requests.pop(
        request_id,
        None
    )

    moderator_sessions.pop(
        int(admin_user_id),
        None
    )

    if user_chat_id in states:

        states[
            user_chat_id
        ]["step"] = "preview"

        send(

            user_chat_id,

            "<b>❌ Объявление не прошло "
            "модерацию.</b>\n\n"

            "Администратор отклонил объявление.\n\n"

            "Вы можете изменить данные "
            "и отправить его на проверку повторно.",

            [

                [

                    {

                        "text":
                            "✏️ Изменить объявление",

                        "callback_data":
                            "edit_menu"
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
                            "❌ Удалить",

                        "callback_data":
                            "cancel_post"
                    }
                ]
            ]
        )

    answer(

        callback_id,

        "🔴 Объявление отклонено."
    )

    edit_moderation_message(

        item.get(
            "message_chat_id"
        ),

        item.get(
            "message_id"
        ),

        "<b>🔴 ОТКЛОНЕНО</b>\n\n"

        f"Заявка: <code>#{request_id}</code>\n"

        f"Автор ID: <code>{user_chat_id}</code>"
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


    # ========================================================
    # ОЖИДАНИЕ МОДЕРАЦИИ
    # ========================================================

    if step == "moderation":

        send(

            chat_id,

            "⏳ <b>Объявление находится "
            "на модерации.</b>\n\n"

            "Дождитесь решения администратора."
        )

        return True


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
        # РЕДАКТИРОВАНИЕ ОБЪЯВЛЕНИЯ МОДЕРАТОРОМ
        # ====================================================

        if (
            ADMIN_CHAT_ID
            and str(chat_id) == str(ADMIN_CHAT_ID)
            and chat_id in moderator_sessions
            and process_moderator_text(chat_id, text)
        ):
            return


        # ====================================================
        # TELEGRAM DEEP LINKS
        # ====================================================

        if text.startswith("/start") and len(text.split(" ", 1)) > 1:

            start_param = text.split(" ", 1)[1].strip()

            if start_param == "post":
                start_post(chat_id)
                return

            if start_param.startswith("cat_"):
                key = start_param[4:]
                if key in CATEGORIES:
                    send(
                        chat_id,
                        f"<b>{esc(CATEGORIES[key]['name'])}</b>\n\n"
                        "Выберите раздел:",
                        category_menu(key)
                    )
                return

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

            remove_moderation_requests_for_user(
                chat_id
            )

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
        # ПУБЛИКАЦИЯ ГЛАВНОГО МЕНЮ В КАНАЛ
        # ====================================================

        if text.strip() == "/channel_menu":

            if not ADMIN_CHAT_ID or str(chat_id) != str(ADMIN_CHAT_ID):

                send(
                    chat_id,
                    "⛔ Эта команда доступна только администратору."
                )
                return

            if not CHANNEL_USERNAME:

                send(
                    chat_id,
                    "❌ Не указан CHANNEL_USERNAME в Render."
                )
                return

            result = send(
                CHANNEL_USERNAME,
                CHANNEL_MENU_TEXT,
                channel_main_menu()
            )

            if result.get("ok") and result.get("result", {}).get("message_id"):

                message_id = result["result"]["message_id"]

                pin_result = api(
                    "pinChatMessage",
                    {
                        "chat_id": CHANNEL_USERNAME,
                        "message_id": message_id,
                        "disable_notification": True
                    }
                )

                if pin_result.get("ok"):
                    send(
                        chat_id,
                        "✅ Главное меню опубликовано и закреплено в канале."
                    )
                else:
                    send(
                        chat_id,
                        "✅ Главное меню опубликовано в канале.\n\n"
                        "⚠️ Автоматически закрепить его не удалось. "
                        "Проверь права бота на управление публикациями."
                    )
            else:
                send(
                    chat_id,
                    "❌ Не удалось опубликовать главное меню.\n\n"
                    f"<code>{esc(str(result))}</code>"
                )

            return


        # ====================================================
        # TELEGRAM ID
        # ====================================================

        if text.strip() == "/id":

            send(

                chat_id,

                "🆔 Ваш Telegram ID:\n\n"
                f"<code>{chat_id}</code>\n\n"
                "Этот номер нужно указать в Render "
                "как <code>ADMIN_CHAT_ID</code>."
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

    callback_from_id = callback[
        "from"
    ]["id"]


    # ========================================================
    # МОДЕРАЦИЯ
    # ========================================================

    if data.startswith("mod_edit_"):
        try:
            request_id = int(data[len("mod_edit_"):])
        except ValueError:
            answer(callback["id"], "Некорректный номер заявки.", True)
            return
        if not moderator_is_allowed(callback_from_id):
            answer(callback["id"], "⛔ У вас нет прав администратора.", True)
            return
        answer(callback["id"])
        show_moderation_editor(callback_from_id, request_id)
        return


    if data.startswith("mod_back_"):
        try:
            request_id = int(data[len("mod_back_"):])
        except ValueError:
            answer(callback["id"], "Некорректный номер заявки.", True)
            return
        if not moderator_is_allowed(callback_from_id):
            answer(callback["id"], "⛔ У вас нет прав администратора.", True)
            return
        moderator_sessions.pop(callback_from_id, None)
        answer(callback["id"])
        text = moderation_control_text(request_id)
        if text:
            send(callback_from_id, text, moderation_keyboard(request_id))
        return


    if data.startswith("mod_field_"):
        parts = data.split("_")
        if len(parts) != 4:
            answer(callback["id"], "Некорректная команда.", True)
            return
        field = parts[2]
        try:
            request_id = int(parts[3])
        except ValueError:
            answer(callback["id"], "Некорректный номер заявки.", True)
            return
        if not moderator_is_allowed(callback_from_id):
            answer(callback["id"], "⛔ У вас нет прав администратора.", True)
            return
        if request_id not in moderation_requests:
            answer(callback["id"], "Заявка уже обработана.", True)
            return
        prompts = {
            "price": "💰 <b>Новая цена</b>\n\nВведите только сумму. Например: <b>750</b>",
            "district": "📍 <b>Новая локация</b>\n\nВведите район или адрес.",
            "description": "📝 <b>Новое описание</b>\n\nВведите новый текст. Чтобы убрать описание — напишите <b>Пропустить</b>.",
            "contact": "📞 <b>Новый контакт</b>\n\nВведите телефон, Telegram или WhatsApp."
        }
        if field not in prompts:
            answer(callback["id"], "Неизвестное поле.", True)
            return
        moderator_sessions[callback_from_id] = {"request_id": request_id, "step": field}
        answer(callback["id"])
        send(callback_from_id, prompts[field])
        return


    if data.startswith("mod_details_"):
        try:
            request_id = int(data[len("mod_details_"):])
        except ValueError:
            answer(callback["id"], "Некорректный номер заявки.", True)
            return
        if not moderator_is_allowed(callback_from_id):
            answer(callback["id"], "⛔ У вас нет прав администратора.", True)
            return
        item = moderation_requests.get(request_id)
        if not item or item["user_chat_id"] not in states:
            answer(callback["id"], "Заявка уже обработана.", True)
            return
        moderator_sessions[callback_from_id] = {"request_id": request_id, "step": "details_menu"}
        answer(callback["id"])
        send(
            callback_from_id,
            "<b>📋 Характеристики</b>\n\nЧто изменить?",
            moderation_detail_menu(request_id, states[item["user_chat_id"]]["data"])
        )
        return


    if data.startswith("mod_detail_"):
        parts = data.split("_")
        if len(parts) != 4:
            answer(callback["id"], "Некорректная команда.", True)
            return
        try:
            request_id = int(parts[2])
            index = int(parts[3])
        except ValueError:
            answer(callback["id"], "Некорректная заявка.", True)
            return
        if not moderator_is_allowed(callback_from_id):
            answer(callback["id"], "⛔ У вас нет прав администратора.", True)
            return
        item = moderation_requests.get(request_id)
        if not item or item["user_chat_id"] not in states:
            answer(callback["id"], "Заявка уже обработана.", True)
            return
        data_listing = states[item["user_chat_id"]]["data"]
        fields = CATEGORIES[data_listing["category_key"]]["fields"]
        if index >= len(fields):
            answer(callback["id"], "Характеристика не найдена.", True)
            return
        moderator_sessions[callback_from_id] = {"request_id": request_id, "step": f"detail_{index}"}
        answer(callback["id"])
        send(callback_from_id, f"<b>{esc(fields[index][1])}</b>\n\nВведите новое значение.")
        return


    if data.startswith(
        "mod_publish_"
    ):

        try:

            request_id = int(
                data[
                    len("mod_publish_"):
                ]
            )

        except ValueError:

            answer(

                callback["id"],

                "Некорректный номер заявки.",

                True
            )

            return

        moderate_publish(

            request_id,

            callback_from_id,

            callback["id"]
        )

        return


    if data.startswith(
        "mod_reject_"
    ):

        try:

            request_id = int(
                data[
                    len("mod_reject_"):
                ]
            )

        except ValueError:

            answer(

                callback["id"],

                "Некорректный номер заявки.",

                True
            )

            return

        moderate_reject(

            request_id,

            callback_from_id,

            callback["id"]
        )

        return


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
        "browsepage_"
    ):

        parts = data.split(
            "_",
            3
        )

        if len(parts) != 4:
            return

        key = parts[1]
        sub = parts[2]

        try:
            page = int(parts[3])
        except ValueError:
            return

        if key not in CATEGORIES:
            return

        send_listing_catalog(
            chat_id,
            key,
            sub,
            page
        )

        return



    if data.startswith(
        "browse_"
    ):

        parts = data.split(
            "_",
            2
        )

        if len(parts) < 3:
            return

        key = parts[1]
        sub = parts[2]

        if key not in CATEGORIES:
            return

        send_listing_catalog(
            chat_id,
            key,
            sub,
            0
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

        remove_moderation_requests_for_user(
            chat_id
        )

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
    # ОТПРАВКА НА МОДЕРАЦИЮ
    # ========================================================

    if data == "submit_moderation":

        submit_for_moderation(
            chat_id
        )

        return



# ============================================================
# MINI APP — MADLOBA MARKET
# ============================================================

MINI_APP_PER_PAGE = 20
MINI_APP_URL = os.environ.get(
    "MINI_APP_URL",
    "https://madloba-market-bot.onrender.com/app",
).strip()


def validate_telegram_init_data(init_data, max_age=86400):
    # Проверяет Telegram.WebApp.initData на сервере.
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.pop("hash", [""])[0]
        if not received_hash:
            return None
        data_check_string = "\n".join(
            f"{key}={parsed[key][0]}" for key in sorted(parsed.keys())
        )
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256
        ).digest()
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        auth_date = int(parsed.get("auth_date", ["0"])[0])
        if auth_date <= 0 or time.time() - auth_date > max_age:
            return None
        user_raw = parsed.get("user", [""])[0]
        user = json.loads(user_raw) if user_raw else {}
        if not isinstance(user, dict) or not user.get("id"):
            return None
        return user
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


MINI_APP_HTML = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0b73f6">
<title>Madloba Market</title>
<script src="https://telegram.org/js/telegram-web-app.js?64"></script>
<style>
:root{--blue:#0b73f6;--text:#111827;--muted:#6b7280;--bg:#f5f7fb;--line:#e8edf5}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif}body{min-height:100vh;padding-bottom:88px}button,input{font:inherit}button{border:0;cursor:pointer}.wrap{max-width:760px;margin:auto;padding:14px 16px 24px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 16px}.brand{font-weight:900;font-size:20px;letter-spacing:-.5px}.brand span{color:var(--blue)}.city{display:flex;gap:6px;background:#fff;border:1px solid var(--line);border-radius:999px;padding:9px 12px;font-weight:800}.hero{background:linear-gradient(135deg,#0b73f6,#2d8cff);border-radius:24px;padding:20px;color:#fff;box-shadow:0 12px 30px rgba(11,115,246,.22);margin-bottom:16px}.hero h1{font-size:25px;line-height:1.05;margin:0 0 7px;font-weight:900}.hero p{margin:0 0 16px;opacity:.9;font-size:14px}.search{display:flex;align-items:center;gap:9px;background:#fff;border-radius:15px;padding:0 13px;height:50px;color:#111}.search input{border:0;outline:0;width:100%;background:transparent;font-size:16px}.section-head{display:flex;align-items:center;justify-content:space-between;margin:20px 2px 10px}.section-head h2{font-size:18px;margin:0;font-weight:900}.section-head small{color:var(--muted)}.cats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.cat{background:#fff;border:1px solid var(--line);border-radius:18px;padding:15px;text-align:left;min-height:86px;box-shadow:0 4px 14px rgba(15,23,42,.035)}.cat .ico{font-size:25px;display:block;margin-bottom:7px}.cat b{font-size:14px}.cat small{display:block;color:var(--muted);margin-top:3px}.list{display:grid;gap:12px}.card{cursor:pointer;background:#fff;border:1px solid var(--line);border-radius:20px;overflow:hidden;box-shadow:0 5px 18px rgba(15,23,42,.045)}.photo{height:170px;background:linear-gradient(135deg,#eaf3ff,#f7f9fc);display:flex;align-items:center;justify-content:center;font-size:38px;color:#9bb8df;overflow:hidden}.photo img{width:100%;height:100%;object-fit:cover;display:block}.cardbody{padding:14px}.tag{font-size:12px;color:var(--blue);font-weight:800;margin-bottom:5px}.title{font-size:17px;font-weight:900;margin-bottom:6px}.desc{font-size:14px;color:#4b5563;line-height:1.4}.meta{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}.pill{background:#f4f7fb;border-radius:999px;padding:6px 9px;font-size:12px;color:#475569}.price{margin-top:12px;font-size:19px;font-weight:900}.loc{color:#64748b;font-size:13px;margin-top:6px}.more{text-align:center;margin:16px 0}.more button{background:#fff;border:1px solid var(--line);border-radius:13px;padding:11px 18px;font-weight:800}.empty{text-align:center;padding:35px 15px;color:var(--muted)}.detail{display:none}.detail.show{display:block}.detail-top{display:flex;align-items:center;gap:10px;margin:4px 0 14px}.detail-back{background:#fff;border:1px solid var(--line);border-radius:12px;padding:9px 12px;color:var(--blue);font-weight:800}.detail-photo{height:290px;background:linear-gradient(135deg,#eaf3ff,#f7f9fc);border-radius:22px;overflow:hidden;position:relative;display:flex;align-items:center;justify-content:center}.detail-photo img{width:100%;height:100%;object-fit:cover}.gallery-btn{position:absolute;top:50%;transform:translateY(-50%);width:42px;height:42px;border-radius:50%;background:rgba(17,24,39,.58);color:#fff;font-size:22px}.gallery-btn.prev{left:12px}.gallery-btn.next{right:12px}.gallery-count{position:absolute;right:12px;bottom:12px;background:rgba(17,24,39,.65);color:#fff;border-radius:999px;padding:5px 9px;font-size:12px}.detail-body{background:#fff;border:1px solid var(--line);border-radius:22px;margin-top:12px;padding:18px;box-shadow:0 5px 18px rgba(15,23,42,.045)}.detail-tag{color:var(--blue);font-size:13px;font-weight:800;margin-bottom:7px}.detail-title{font-size:24px;line-height:1.15;font-weight:900;margin-bottom:10px}.detail-price{font-size:25px;font-weight:900;margin:12px 0}.detail-meta{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.detail-desc{font-size:15px;line-height:1.55;color:#374151;white-space:pre-wrap;margin-top:14px}.detail-loc{font-size:14px;color:#64748b;margin-top:10px}.contacts{display:grid;gap:9px;margin-top:18px}.contact-btn{display:block;text-align:center;text-decoration:none;background:var(--blue);color:#fff;border-radius:14px;padding:13px 14px;font-weight:900}.contact-btn.secondary{background:#eef5ff;color:var(--blue)}@media(min-width:620px){.detail-photo{height:380px}}.bottom{position:fixed;z-index:20;left:0;right:0;bottom:0;background:rgba(255,255,255,.94);backdrop-filter:blur(16px);border-top:1px solid var(--line);padding:8px 10px calc(8px + env(safe-area-inset-bottom));display:grid;grid-template-columns:repeat(5,1fr)}.nav{background:transparent;color:#7a8494;font-size:10px;font-weight:800;padding:5px 2px}.nav .ni{display:block;font-size:20px;line-height:22px}.nav.active{color:var(--blue)}.toast{position:fixed;z-index:50;left:50%;bottom:95px;transform:translateX(-50%);background:#111827;color:#fff;padding:10px 14px;border-radius:12px;font-size:13px;opacity:0;pointer-events:none;transition:.2s;max-width:90%;text-align:center}.toast.show{opacity:1}.back{display:none;margin-bottom:12px;background:transparent;color:var(--blue);font-weight:800;padding:0}.back.show{display:block}@media(min-width:620px){.cats{grid-template-columns:repeat(4,minmax(0,1fr))}.photo{height:210px}}
</style>
</head>
<body>
<div class="wrap"><div class="top"><div class="brand">MADLOBA <span>MARKET</span></div><button class="city" id="cityBtn">📍 <span id="cityName">Batumi</span>⌄</button></div><div class="hero"><h1>Объявления рядом с вами</h1><p>Покупайте, продавайте и находите нужное прямо в Telegram.</p><div class="search">🔎 <input id="search" placeholder="Что ищете? Например: квартира" autocomplete="off"></div></div><button class="back" id="backBtn">← Все категории</button><section><div class="section-head"><h2>Категории</h2><small id="countLabel"></small></div><div class="cats" id="cats"></div><div class="section-head"><h2>Свежие объявления</h2><small>новые</small></div><div class="list" id="list"></div><div class="more"><button id="moreBtn" style="display:none">Показать ещё</button></div></section><section class="detail" id="detailView"><div class="detail-top"><button class="detail-back" id="detailBack">← Назад</button><div class="section-head" style="margin:0"><h2>Объявление</h2></div></div><div id="detailContent"></div></section></div>
<nav class="bottom"><button class="nav active" data-nav="home"><span class="ni">⌂</span>Главная</button><button class="nav" data-nav="favorites"><span class="ni">♡</span>Избранное</button><button class="nav" data-nav="add"><span class="ni">＋</span>Разместить</button><button class="nav" data-nav="mine"><span class="ni">▤</span>Мои</button><button class="nav" data-nav="profile"><span class="ni">◉</span>Профиль</button></nav><div class="toast" id="toast"></div>
<script>
const tg=window.Telegram&&window.Telegram.WebApp;if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#0b73f6');tg.setBackgroundColor('#f5f7fb')}catch(e){}}
const state={city:localStorage.getItem('mm_city')||'batumi',page:0,q:'',category:'',loading:false};const cats=[['realestate','🏠','Недвижимость','Квартиры, дома, аренда'],['auto','🚗','Авто','Машины, мото, запчасти'],['tech','📱','Техника','Телефоны, электроника'],['home','🛋️','Дом и мебель','Мебель и всё для дома'],['kids','🧸','Детское','Детские товары'],['work','💼','Работа и услуги','Услуги и вакансии'],['give','🎁','Отдам','Бесплатно'],['search','🔎','Ищу','Нужные вещи и услуги']];const cityNames={batumi:'Batumi',tbilisi:'Tbilisi'};const $=id=>document.getElementById(id);function toast(t){$('toast').textContent=t;$('toast').classList.add('show');clearTimeout(window.__toast);window.__toast=setTimeout(()=>$('toast').classList.remove('show'),1800)}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function money(v,c){const m={usd:'$',gel:'₾',eur:'€',USD:'$',GEL:'₾',EUR:'€'};return esc(v)+' '+(m[c]||esc(c||''))}function renderCats(){$('cats').innerHTML=cats.map(c=>`<button class="cat" data-cat="${c[0]}"><span class="ico">${c[1]}</span><b>${c[2]}</b><small>${c[3]}</small></button>`).join('');document.querySelectorAll('[data-cat]').forEach(b=>b.onclick=()=>{state.category=b.dataset.cat;$('backBtn').classList.add('show');load(true)})}function card(x){const photo=(x.photos||[])[0];const title=x.title||x.category||'Объявление';const d=x.details||{};const pills=Object.values(d).filter(v=>v!==''&&v!=null).slice(0,4).map(v=>`<span class="pill">${esc(v)}</span>`).join('');return `<article class="card" data-id="${esc(x.id)}"><div class="photo">${photo?`<img src="/media/${encodeURIComponent(x.id)}/0" loading="lazy" onerror="this.parentElement.innerHTML='🏠'">`:'🏠'}</div><div class="cardbody"><div class="tag">${esc(x.category_name||'Объявление')}</div><div class="title">${esc(title)}</div>${x.description?`<div class="desc">${esc(String(x.description).slice(0,180))}</div>`:''}<div class="meta">${pills}</div>${x.price?`<div class="price">${money(x.price,x.currency)}</div>`:''}${x.address?`<div class="loc">📍 ${esc(x.address)}</div>`:''}</div></article>`}function bindCards(){document.querySelectorAll('[data-id]').forEach(el=>el.onclick=()=>openDetail(el.dataset.id))}async function openDetail(id){try{const r=await fetch('/api/listing/'+encodeURIComponent(id));if(!r.ok)throw 0;const x=await r.json();renderDetail(x);window.scrollTo({top:0,behavior:'smooth'});$('detailView').classList.add('show');document.querySelector('section:not(.detail)').style.display='none'}catch(e){toast('Не удалось открыть объявление')}}function renderDetail(x){const photos=x.photos||[];const d=x.details||{};const pills=Object.entries(d).filter(([k,v])=>v!==''&&v!=null).map(([k,v])=>`<span class="pill">${esc(v)}</span>`).join('');const phone=String(x.phone||'').trim();const whatsapp=String(x.whatsapp||'').trim().replace(/[^0-9]/g,'');const telegram=String(x.telegram||'').trim().replace(/^@/,'');const contact=[];if(phone)contact.push(`<a class="contact-btn" href="tel:${encodeURIComponent(phone)}">📞 Позвонить</a>`);if(whatsapp)contact.push(`<a class="contact-btn" href="https://wa.me/${whatsapp}">💬 WhatsApp</a>`);if(telegram)contact.push(`<a class="contact-btn secondary" href="https://t.me/${encodeURIComponent(telegram)}">✈️ Telegram</a>`);if(x.channel_post_url)contact.push(`<a class="contact-btn secondary" href="${esc(x.channel_post_url)}" target="_blank">📣 Открыть в канале</a>`);$('detailContent').innerHTML=`<div class="detail-photo" id="detailPhoto">${photos.length?`<img id="detailImg" src="/media/${encodeURIComponent(x.id)}/0" onerror="this.parentElement.innerHTML='🏠'">`:'🏠'}${photos.length>1?`<button class="gallery-btn prev" id="prevPhoto">‹</button><button class="gallery-btn next" id="nextPhoto">›</button><span class="gallery-count" id="photoCount">1/${photos.length}</span>`:''}</div><div class="detail-body"><div class="detail-tag">${esc(x.category_name||x.category||'Объявление')}</div><div class="detail-title">${esc(x.title||'Объявление')}</div>${x.price?`<div class="detail-price">${money(x.price,x.currency)}</div>`:''}<div class="detail-meta">${pills}</div>${x.address?`<div class="detail-loc">📍 ${esc(x.address)}</div>`:''}${x.description?`<div class="detail-desc">${esc(x.description)}</div>`:''}${contact.length?`<div class="contacts">${contact.join('')}</div>`:''}</div>`;if(photos.length>1){let idx=0;const img='detailImg';const update=()=>{$(img).src='/media/'+encodeURIComponent(x.id)+'/'+idx;$('photoCount').textContent=(idx+1)+'/'+photos.length};$('prevPhoto').onclick=e=>{e.stopPropagation();idx=(idx-1+photos.length)%photos.length;update()};$('nextPhoto').onclick=e=>{e.stopPropagation();idx=(idx+1)%photos.length;update()}}}async function load(reset=true){if(state.loading)return;state.loading=true;if(reset){state.page=0;$('list').innerHTML=''}const p=new URLSearchParams({city:state.city,page:state.page,per_page:20});if(state.category)p.set('category',state.category);if(state.q)p.set('q',state.q);try{const r=await fetch('/api/listings?'+p);if(!r.ok)throw 0;const data=await r.json();if(reset)$('list').innerHTML='';$('list').insertAdjacentHTML('beforeend',(data.items||[]).map(card).join(''));bindCards();$('moreBtn').style.display=data.has_next?'inline-block':'none';$('countLabel').textContent=data.total_hint?data.total_hint+'+':'';if(reset&&!data.items?.length)$('list').innerHTML='<div class="empty">Пока нет объявлений.<br>Попробуйте другую категорию или город.</div>'}catch(e){if(reset)$('list').innerHTML='<div class="empty">Не удалось загрузить объявления.<br>Попробуйте ещё раз.</div>'}finally{state.loading=false}}$('cityBtn').onclick=()=>{state.city=state.city==='batumi'?'tbilisi':'batumi';localStorage.setItem('mm_city',state.city);$('cityName').textContent=cityNames[state.city];load(true);toast('Город: '+cityNames[state.city])};$('search').oninput=e=>{state.q=e.target.value.trim();clearTimeout(window.__search);window.__search=setTimeout(()=>load(true),350)};$('moreBtn').onclick=()=>{state.page++;load(false)};$('backBtn').onclick=()=>{state.category='';$('backBtn').classList.remove('show');load(true)};$('detailBack').onclick=()=>{ $('detailView').classList.remove('show'); document.querySelector('section:not(.detail)').style.display=''; window.scrollTo({top:0,behavior:'smooth'}) };document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{const n=b.dataset.nav;if(n==='home'){state.category='';$('backBtn').classList.remove('show');$('detailView').classList.remove('show');document.querySelector('section:not(.detail)').style.display='';load(true)}else if(n==='add'){toast('Размещение откроется через бота')}else{toast('Этот раздел готовится')}});$('cityName').textContent=cityNames[state.city];renderCats();load(true);
</script></body></html>'''




@app.get("/app")
def mini_app():
    return Response(MINI_APP_HTML, mimetype="text/html")


@app.get("/api/config")
def mini_app_config():
    return jsonify({"city":"batumi","per_page":MINI_APP_PER_PAGE,"app_url":MINI_APP_URL})


@app.post("/api/auth")
def mini_app_auth():
    payload = request.get_json(silent=True) or {}
    user = validate_telegram_init_data(str(payload.get("init_data", "")))
    if not user:
        return jsonify({"ok":False,"error":"invalid_init_data"}), 401
    return jsonify({"ok":True,"user":user})


@app.get("/api/listings")
def mini_app_listings():
    if not supabase_enabled():
        return jsonify({"items":[],"has_next":False,"total_hint":0})
    city = str(request.args.get("city", DEFAULT_CITY_SLUG)).strip().lower()
    if city not in {"batumi", "tbilisi"}:
        city = DEFAULT_CITY_SLUG
    category = str(request.args.get("category", "")).strip().lower()
    q = str(request.args.get("q", "")).strip()[:80]
    try:
        page = max(0, int(request.args.get("page", "0")))
        per_page = min(20, max(1, int(request.args.get("per_page", "20"))))
    except ValueError:
        page, per_page = 0, 20
    city_id = _supabase_city_id(city)
    if not city_id:
        return jsonify({"items":[],"has_next":False,"total_hint":0})
    params={"select":"id,title,description,price,currency,address,metadata,channel_url,created_at,category_id,city_id,status","status":"eq.published","city_id":f"eq.{city_id}","order":"created_at.desc","offset":str(page*per_page),"limit":str(per_page+1)}
    if category:
        category_id=_supabase_category_id(category)
        if not category_id:return jsonify({"items":[],"has_next":False,"total_hint":0})
        params["category_id"]=f"eq.{category_id}"
    if q:
        safe_q=q.replace("*","").replace(","," ").strip()
        if safe_q:params["or"]=f"(title.ilike.*{safe_q}*,description.ilike.*{safe_q}*,address.ilike.*{safe_q}*)"
    rows=supabase_request("GET","listings",params=params)
    if rows is None:return jsonify({"items":[],"has_next":False,"total_hint":0,"error":"db_unavailable"}),503
    has_next=len(rows)>per_page;rows=rows[:per_page]
    items=_attach_catalog_photos(rows)
    for idx,item in enumerate(items):
        row=rows[idx];item["title"]=row.get("title") or item.get("title") or listing_title(item);item["description"]=row.get("description") or item.get("description") or "";item["price"]=str(row.get("price")) if row.get("price") is not None else item.get("price","");item["currency"]=row.get("currency") or item.get("currency","");item["address"]=row.get("address") or item.get("district","");item["category_name"]=item.get("category") or "Объявление"
    return jsonify({"items":items,"has_next":has_next,"total_hint":per_page*(page+1)+(1 if has_next else 0)})


@app.get("/api/listing/<int:listing_id>")
def mini_app_listing_detail(listing_id):
    if not supabase_enabled():
        return jsonify({"error":"db_unavailable"}), 503
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select":"id,title,description,price,currency,address,metadata,channel_url,created_at,category_id,city_id,status,phone,whatsapp,telegram",
            "id":f"eq.{listing_id}",
            "status":"eq.published",
            "limit":"1",
        },
    )
    if not rows:
        return jsonify({"error":"not_found"}), 404
    item = _attach_catalog_photos(rows)[0]
    row = rows[0]
    item["title"] = row.get("title") or item.get("title") or listing_title(item)
    item["description"] = row.get("description") or item.get("description") or ""
    item["price"] = str(row.get("price")) if row.get("price") is not None else item.get("price", "")
    item["currency"] = row.get("currency") or item.get("currency", "")
    item["address"] = row.get("address") or item.get("district", "")
    item["phone"] = row.get("phone") or item.get("phone", "") or ""
    item["whatsapp"] = row.get("whatsapp") or item.get("whatsapp", "") or ""
    item["telegram"] = row.get("telegram") or item.get("telegram", "") or ""
    item["category_name"] = item.get("category") or "Объявление"
    return jsonify(item)


@app.get("/media/<int:listing_id>/<int:index>")
def mini_app_media(listing_id, index):
    if index < 0 or index >= MAX_PHOTOS:return "Not found",404
    published=supabase_request("GET","listings",params={"select":"id","id":f"eq.{listing_id}","status":"eq.published","limit":"1"})
    if not published:return "Not found",404
    rows=supabase_request("GET","listing_photos",params={"select":"photo_url,sort_order","listing_id":f"eq.{listing_id}","order":"sort_order.asc"})
    if rows is None or index >= len(rows):return "Not found",404
    photo_ref=str(rows[index].get("photo_url") or "")
    if not photo_ref:return "Not found",404
    try:
        if photo_ref.startswith("http://") or photo_ref.startswith("https://"):
            upstream=requests.get(photo_ref,timeout=15)
        else:
            file_info=api("getFile",{"file_id":photo_ref});result=file_info.get("result") if isinstance(file_info,dict) else None;file_path=result.get("file_path") if isinstance(result,dict) else None
            if not file_path:return "Not found",404
            upstream=requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",timeout=20)
        if not upstream.ok:return "Not found",404
        return Response(upstream.content,content_type=upstream.headers.get("Content-Type","image/jpeg"),headers={"Cache-Control":"public, max-age=86400","X-Content-Type-Options":"nosniff"})
    except Exception as error:
        print("MEDIA ERROR:",repr(error));return "Media unavailable",503

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
                    "id",

                "description":
                    "Мой Telegram ID"
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
# MINI APP — КНОПКА В МЕНЮ БОТА
# ============================================================

print(
    "SET MINI APP MENU BUTTON:",
    api(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "MADLOBA MARKET",
                "web_app": {"url": MINI_APP_URL},
            }
        },
    )
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

    "ADMIN CHAT ID:",

    ADMIN_CHAT_ID
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
    
