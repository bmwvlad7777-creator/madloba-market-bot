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
LAST_SUPABASE_ERROR = ""


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
            global LAST_SUPABASE_ERROR
            LAST_SUPABASE_ERROR = f"HTTP {response.status_code}: {response.text[:700]}"
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
        LAST_SUPABASE_ERROR = f"REQUEST ERROR: {repr(error)}"
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
:root{--blue:#0b73f6;--blue2:#2f8cff;--ink:#111827;--muted:#697386;--bg:#f5f7fb;--card:#fff;--line:#e7ecf4;--soft:#eef5ff;--shadow:0 8px 24px rgba(15,23,42,.06)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif}body{min-height:100vh;padding-bottom:96px}button,input,select,textarea{font:inherit}button{border:0;cursor:pointer}.wrap{max-width:760px;margin:auto;padding:calc(82px + var(--tg-content-safe-area-inset-top,0px)) 14px 26px}.top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:4px 2px 15px}.brand{font-weight:950;font-size:19px;letter-spacing:-.65px;white-space:nowrap;flex:1;min-width:0}.brand span{color:var(--blue)}.top>div:last-child{display:flex;gap:6px;align-items:center;flex:0 0 auto}.city{display:flex;align-items:center;justify-content:center;gap:5px;background:rgba(255,255,255,.96);border:1px solid var(--line);border-radius:999px;padding:9px 10px;font-weight:850;font-size:14px;white-space:nowrap;box-shadow:0 3px 12px rgba(15,23,42,.04)}.city#langSelect{width:74px;padding-left:7px;padding-right:7px}.city#cityBtn{min-width:114px}.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,#0b73f6 0%,#2789ff 60%,#4c9dff 100%);border-radius:28px;padding:22px 18px 17px;color:#fff;box-shadow:0 18px 38px rgba(11,115,246,.22);margin-bottom:19px}.hero:after{content:"";position:absolute;width:150px;height:150px;border-radius:50%;right:-65px;top:-65px;background:rgba(255,255,255,.09);pointer-events:none}.hero h1{position:relative;z-index:1;font-size:27px;line-height:1.04;margin:0 0 7px;font-weight:950;letter-spacing:-.7px}.hero p{position:relative;z-index:1;margin:0 0 16px;opacity:.92;font-size:14px;line-height:1.4;max-width:520px}.search{position:relative;z-index:2;display:flex;align-items:center;gap:10px;background:#fff;border-radius:17px;padding:0 14px;height:54px;color:#111;box-shadow:0 8px 20px rgba(0,0,0,.09)}.search input{border:0;outline:0;width:100%;background:transparent;font-size:16px;color:#111}.search input::placeholder{color:#9aa3b1}.quick-actions{position:relative;z-index:2;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:11px}.quick-action{min-width:0;background:rgba(255,255,255,.15);color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:13px;padding:8px 5px;font-size:11px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;backdrop-filter:blur(8px)}.quick-action:active{transform:scale(.97);background:rgba(255,255,255,.24)}.filter-bar{display:none;margin-top:11px}.filter-bar.show{display:block}.filter-btn{width:100%;background:#fff;border:1px solid var(--line);border-radius:15px;padding:12px 14px;color:var(--blue);font-weight:900;text-align:left;box-shadow:0 4px 14px rgba(15,23,42,.04)}.filter-panel{display:none;background:#fff;border:1px solid var(--line);border-radius:18px;padding:14px;margin-top:8px;box-shadow:0 10px 24px rgba(15,23,42,.08)}.filter-panel.show{display:block}.filter-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.filter-field{display:flex;flex-direction:column;gap:5px}.filter-field.full{grid-column:1/-1}.filter-field label{font-size:12px;color:var(--muted);font-weight:800}.filter-field input,.filter-field select{width:100%;border:1px solid #dfe6ef;border-radius:12px;background:#f8fafc;padding:11px 10px;outline:0;color:var(--ink);min-height:46px}.filter-field select:disabled{color:#9aa3b1;background:#f1f4f8}.filter-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.filter-actions button{min-height:48px}.filter-apply{background:var(--blue);color:#fff;border-radius:12px;padding:11px;font-weight:900}.filter-reset{background:#eef2f7;color:#475569;border-radius:12px;padding:11px;font-weight:900}.filter-active{font-size:11px;color:var(--muted);margin-top:6px;padding-left:2px}.section-head{display:flex;align-items:center;justify-content:space-between;margin:19px 2px 10px}.section-head h2{font-size:21px;margin:0;font-weight:950;letter-spacing:-.45px}.section-head small{color:var(--muted);font-weight:700}.cats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.cat{position:relative;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:15px 14px 14px;text-align:left;min-height:104px;box-shadow:var(--shadow);transition:transform .15s ease,box-shadow .15s ease}.cat:after{content:'›';position:absolute;right:13px;bottom:11px;color:#b2bccb;font-size:21px;font-weight:500}.cat:active{transform:scale(.985);box-shadow:0 4px 14px rgba(15,23,42,.05)}.cat .ico{width:42px;height:42px;border-radius:14px;background:#f0f6ff;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:9px}.cat b{display:block;font-size:15px;color:var(--blue);letter-spacing:-.15px}.cat small{display:block;color:var(--muted);margin-top:4px;font-size:12px;line-height:1.25;padding-right:15px}.results-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:20px}.results-head .section-head{margin:0;flex:1}.sort-select{display:none;border:1px solid #dfe6ef;border-radius:12px;background:#fff;color:var(--blue);padding:9px 10px;font-weight:800;max-width:165px}.list{display:grid;gap:13px}.card{position:relative;cursor:pointer;background:#fff;border:1px solid var(--line);border-radius:22px;overflow:hidden;box-shadow:0 8px 24px rgba(15,23,42,.055)}.fav-btn{position:absolute;z-index:3;top:11px;right:11px;width:42px;height:42px;border-radius:50%;background:rgba(255,255,255,.96);border:1px solid rgba(232,237,245,.95);box-shadow:0 6px 16px rgba(15,23,42,.13);font-size:23px;line-height:42px;padding:0;color:#596579}.fav-btn.active{color:#ef4444}.photo{height:190px;background:linear-gradient(135deg,#eaf3ff,#f7f9fc);display:flex;align-items:center;justify-content:center;font-size:38px;color:#9bb8df;overflow:hidden}.photo img{width:100%;height:100%;object-fit:cover;display:block}.photo-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#9bb8df}.photo-empty span{font-size:34px}.photo-empty small{font-size:12px;color:#94a3b8}.cardbody{padding:15px 15px 16px}.tag{display:inline-flex;background:#eef5ff;color:var(--blue);border-radius:999px;padding:5px 8px;font-size:11px;font-weight:850;margin-bottom:8px}.title{font-size:18px;font-weight:950;line-height:1.2;margin-bottom:6px;letter-spacing:-.2px}.desc{font-size:13px;color:#566174;line-height:1.4}.meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:12px}.spec{background:#f7f9fc;border:1px solid #edf1f7;border-radius:13px;padding:9px 8px;min-width:0}.spec-value{font-size:14px;font-weight:900;color:#111827;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.spec-label{font-size:10px;color:#7b8494;margin-top:3px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.price{margin-top:13px;font-size:21px;font-weight:950;letter-spacing:-.35px}.loc{color:#64748b;font-size:12px;margin-top:6px}.more{text-align:center;margin:17px 0}.more button{background:#fff;border:1px solid #dfe6ef;border-radius:14px;padding:12px 20px;font-weight:850;color:var(--blue);box-shadow:0 4px 14px rgba(15,23,42,.04)}.empty{text-align:center;padding:38px 15px;color:var(--muted)}.detail{display:none}.detail.show{display:block}.detail-top{display:flex;align-items:center;gap:10px;margin:4px 0 14px}.detail-back{background:#fff;border:1px solid var(--line);border-radius:13px;padding:9px 12px;color:var(--blue);font-weight:850}.detail-head-row{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.detail-fav{width:46px;height:46px;flex:0 0 46px;border-radius:50%;background:#f7f9fc;border:1px solid #e8edf5;font-size:25px}.detail-fav.active{color:#ef4444;background:#fff1f2;border-color:#ffe0e5}.detail-photo{height:300px;background:linear-gradient(135deg,#eaf3ff,#f7f9fc);border-radius:22px;overflow:hidden;position:relative;display:flex;align-items:center;justify-content:center}.detail-photo img{width:100%;height:100%;object-fit:cover}.detail-photo .photo-empty{height:100%;width:100%}.gallery-btn{position:absolute;top:50%;transform:translateY(-50%);width:42px;height:42px;border-radius:50%;background:rgba(17,24,39,.58);color:#fff;font-size:22px}.gallery-btn.prev{left:12px}.gallery-btn.next{right:12px}.gallery-count{position:absolute;right:12px;bottom:12px;background:rgba(17,24,39,.65);color:#fff;border-radius:999px;padding:5px 9px;font-size:12px}.detail-body{background:#fff;border:1px solid var(--line);border-radius:22px;margin-top:12px;padding:18px;box-shadow:0 8px 24px rgba(15,23,42,.055)}.detail-tag{display:inline-flex;background:#eef5ff;color:var(--blue);border-radius:999px;padding:5px 8px;font-size:12px;font-weight:850;margin-bottom:8px}.detail-title{font-size:25px;line-height:1.15;font-weight:950;margin-bottom:10px;letter-spacing:-.4px}.detail-price{font-size:27px;font-weight:950;margin:12px 0}.detail-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:14px 0}.detail-spec{background:#f7f9fc;border:1px solid #edf1f7;border-radius:15px;padding:11px 10px;min-width:0}.detail-spec-value{font-size:17px;font-weight:900;line-height:1.15;word-break:break-word}.detail-spec-label{font-size:11px;color:#7b8494;margin-top:4px;line-height:1.15}.detail-desc{font-size:15px;line-height:1.55;color:#374151;white-space:pre-wrap;margin-top:14px}.detail-loc{font-size:14px;color:#64748b;margin-top:10px}.contacts{display:grid;gap:9px;margin-top:18px}.contact-btn{display:block;text-align:center;text-decoration:none;background:var(--blue);color:#fff;border-radius:14px;padding:14px;font-weight:900;box-shadow:0 8px 18px rgba(11,115,246,.18)}.contact-btn.secondary{background:#eef5ff;color:var(--blue);box-shadow:none}.view{display:none}.view.show{display:block}.account-head{display:flex;align-items:center;gap:12px;margin:8px 0 16px}.account-avatar{width:54px;height:54px;border-radius:18px;background:#eaf3ff;color:var(--blue);display:flex;align-items:center;justify-content:center;font-size:23px;font-weight:900}.account-name{font-size:20px;font-weight:900}.account-sub{font-size:13px;color:var(--muted);margin-top:3px}.view-title{font-size:25px;font-weight:950;margin:4px 0 16px;letter-spacing:-.4px}.view-back{background:#fff;border:1px solid var(--line);border-radius:13px;padding:9px 12px;color:var(--blue);font-weight:850;margin-bottom:14px}.mine-list{display:grid;gap:10px}.mine-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:13px}.mine-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:11px}.mine-action{background:#f4f7fb;color:#334155;border:1px solid #e6ebf3;border-radius:11px;padding:9px 8px;font-size:12px;font-weight:800}.mine-action.primary{background:#eef5ff;color:var(--blue);border-color:#dceaff}.mine-action.danger{background:#fff1f2;color:#be123c;border-color:#ffe0e5}.mine-action.warn{background:#fff8e8;color:#a16207;border-color:#fce8b2}.mine-row{display:flex;gap:12px;align-items:center}.mine-thumb{width:76px;height:76px;border-radius:14px;background:#eef5ff;display:flex;align-items:center;justify-content:center;overflow:hidden;flex:0 0 76px}.mine-thumb img{width:100%;height:100%;object-fit:cover}.mine-info{min-width:0;flex:1}.mine-title{font-weight:900;font-size:15px;line-height:1.2}.mine-price{font-weight:900;font-size:16px;margin-top:5px}.status{display:inline-flex;align-items:center;margin-top:6px;padding:5px 8px;border-radius:999px;font-size:11px;font-weight:800;background:#eef5ff;color:var(--blue)}.edit-panel{background:#fff;border:1px solid var(--line);border-radius:20px;padding:16px;margin-bottom:14px}.edit-title{font-size:20px;font-weight:900;margin-bottom:12px}.edit-field{margin-bottom:11px}.edit-field label{display:block;font-size:12px;color:var(--muted);font-weight:700;margin-bottom:5px}.edit-field input,.edit-field textarea,.edit-field select{width:100%;border:1px solid #dfe6ef;border-radius:12px;background:#f8fafc;padding:11px 12px;outline:0}.edit-field textarea{min-height:110px;resize:vertical}.edit-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}.edit-save{background:var(--blue);color:#fff;border-radius:12px;padding:12px;font-weight:900}.edit-cancel{background:#eef2f7;color:#475569;border-radius:12px;padding:12px;font-weight:900}.profile-card{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--shadow)}.profile-item{padding:12px 0;border-bottom:1px solid var(--line)}.profile-item:last-child{border-bottom:0}.profile-label{font-size:12px;color:var(--muted);margin-bottom:4px}.profile-value{font-size:16px;font-weight:800;word-break:break-word}.bottom{position:fixed;z-index:20;left:0;right:0;bottom:0;background:rgba(255,255,255,.96);backdrop-filter:blur(18px);border-top:1px solid rgba(219,225,235,.9);padding:7px 8px calc(7px + env(safe-area-inset-bottom));display:grid;grid-template-columns:repeat(5,1fr);box-shadow:0 -10px 30px rgba(15,23,42,.07)}.nav{position:relative;background:transparent;color:#7a8494;font-size:10px;font-weight:850;padding:5px 2px}.nav .ni{display:flex;align-items:center;justify-content:center;width:38px;height:30px;margin:0 auto 2px;border-radius:12px;font-size:22px;line-height:22px}.nav.active{color:var(--blue)}.nav.active .ni{background:#eef5ff}.nav[data-nav="add"] .ni{width:48px;height:38px;margin-top:-13px;border-radius:16px;background:var(--blue);color:#fff;font-size:27px;box-shadow:0 8px 18px rgba(11,115,246,.28);border:4px solid #f5f7fb}.toast{position:fixed;z-index:50;left:50%;bottom:105px;transform:translateX(-50%);background:#111827;color:#fff;padding:10px 14px;border-radius:12px;font-size:13px;opacity:0;pointer-events:none;transition:.2s;max-width:90%;text-align:center}.toast.show{opacity:1}.back{display:none;margin-bottom:12px;background:transparent;color:var(--blue);font-weight:800;padding:0}.back.show{display:block}.fav-card{position:relative}.fav-remove{position:absolute;right:12px;top:12px;width:38px;height:38px;border-radius:50%;background:#fff1f2;color:#ef4444;border:1px solid #ffe0e5;font-size:20px}.fav-empty{padding:50px 18px;text-align:center}.fav-empty .heart{font-size:46px;display:block;margin-bottom:10px}.fav-empty b{display:block;font-size:18px;margin-bottom:6px}.fav-empty span{color:var(--muted);font-size:14px}@media(max-width:480px){.hero h1{font-size:26px}.quick-action{font-size:10px}.cats{gap:9px}.cat{min-height:102px;padding:14px 12px}.cat .ico{width:40px;height:40px}.photo{height:185px}.filter-actions{grid-template-columns:1fr 1fr}.filter-reset,.filter-apply{font-size:15px}}@media(min-width:620px){.cats{grid-template-columns:repeat(4,minmax(0,1fr))}.photo{height:220px}.wrap{padding-left:20px;padding-right:20px}}

/* MADLOBA MARKET premium home design — visual layer only. Existing JS/functionality untouched. */
.hero{
  position:relative;
  overflow:hidden;
  background-image:linear-gradient(90deg,rgba(4,24,55,.68) 0%,rgba(4,24,55,.34) 52%,rgba(4,24,55,.08) 100%),url("data:image/jpeg;base64,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");
  background-size:cover;
  background-position:center center;
  min-height:350px;
  padding:22px 18px 16px;
  border-radius:30px;
  box-shadow:0 18px 40px rgba(15,70,150,.20);
}
.hero h1{font-size:31px;line-height:1.04;text-shadow:0 3px 18px rgba(0,0,0,.35);max-width:620px;margin-top:78px}
.hero p{font-size:16px;max-width:560px;text-shadow:0 2px 10px rgba(0,0,0,.30)}
.top{margin-bottom:12px}
.brand{font-size:20px}
.city{font-size:15px;padding:10px 13px;box-shadow:0 6px 18px rgba(15,23,42,.10)}
.city#langSelect{width:82px}
.city#cityBtn{min-width:122px}
.search{height:58px;border-radius:20px;padding:0 15px;box-shadow:0 10px 28px rgba(0,0,0,.16)}
.search input{font-size:17px}
.quick-actions{grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:12px}
.quick-action{min-height:54px;padding:8px 4px;border-radius:16px;background:rgba(255,255,255,.17);border-color:rgba(255,255,255,.38);font-size:11px;backdrop-filter:blur(10px)}
@media (max-width:520px){
 .wrap{padding-left:12px;padding-right:12px}
 .top{gap:5px}
 .brand{font-size:18px}
 .city#langSelect{width:78px}
 .city#cityBtn{min-width:112px}
 .hero{min-height:350px;padding:20px 16px 15px}
 .hero h1{font-size:30px;margin-top:72px}
 .hero p{font-size:15px}
 .quick-actions{gap:6px}
 .quick-action{font-size:10px;min-height:52px}
}
.hero:after{background:rgba(255,255,255,.08)}
.hero h1{font-size:31px;line-height:1.05;text-shadow:0 3px 18px rgba(0,0,0,.32);max-width:620px;margin-top:74px}
.hero p{font-size:16px;max-width:560px;text-shadow:0 2px 10px rgba(0,0,0,.28)}
.top{margin-bottom:12px}
.brand{font-size:20px}
.city{font-size:15px;padding:10px 13px;box-shadow:0 6px 18px rgba(15,23,42,.10)}
.city#langSelect{width:82px}
.city#cityBtn{min-width:122px}
.search{height:58px;border-radius:20px;padding:0 15px;box-shadow:0 10px 28px rgba(15,23,42,.16)}
.search input{font-size:17px}
.quick-actions{grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:12px}
.quick-action{min-height:58px;padding:8px 4px;border-radius:16px;background:rgba(255,255,255,.19);border-color:rgba(255,255,255,.35);font-size:11px;backdrop-filter:blur(10px)}
.section-head{margin-top:24px;margin-bottom:12px}
.section-head h2{font-size:24px}
.cats{gap:12px}
.cat{min-height:118px;border-radius:22px;padding:17px 16px;box-shadow:0 10px 28px rgba(15,23,42,.07)}
.cat .ico{width:46px;height:46px;border-radius:15px}
.cat b{font-size:16px}
.cat small{font-size:13px}
.results-head{margin-top:26px}
.card{border-radius:22px;box-shadow:0 10px 28px rgba(15,23,42,.07)}
.bottom-nav{z-index:1000}
@media (max-width:520px){
 .wrap{padding-left:12px;padding-right:12px}
 .top{gap:5px}
 .brand{font-size:18px}
 .city#langSelect{width:78px}
 .city#cityBtn{min-width:112px}
 .hero{min-height:390px;padding:20px 16px 16px}
 .hero h1{font-size:30px;margin-top:72px}
 .hero p{font-size:15px}
 .quick-actions{gap:6px}
 .quick-action{font-size:10px;min-height:56px}
}

/* FINAL HERO VISUAL POLISH — background only, no functional changes */
.brand{display:flex;align-items:baseline;gap:7px;font-weight:950;font-size:21px;letter-spacing:-.75px;white-space:nowrap;flex:1;min-width:0}
.brand-main{color:#111827}
.brand-market{color:#1677f5;font-weight:950;letter-spacing:-.8px}
.hero{
  background-image:linear-gradient(90deg,rgba(5,22,48,.70) 0%,rgba(5,22,48,.38) 48%,rgba(5,22,48,.08) 100%),url("data:image/jpeg;base64,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") !important;
  background-size:cover !important;
  background-position:center center !important;
  min-height:390px !important;
  padding:22px 18px 16px !important;
  border-radius:30px !important;
  box-shadow:0 18px 40px rgba(15,70,150,.18) !important;
}
.hero:after{background:linear-gradient(135deg,rgba(255,255,255,.10),rgba(255,255,255,0)) !important}
.hero h1{font-size:30px !important;line-height:1.06 !important;max-width:590px !important;margin-top:86px !important;text-shadow:0 3px 18px rgba(0,0,0,.42) !important}
.hero p{font-size:15px !important;max-width:520px !important;text-shadow:0 2px 12px rgba(0,0,0,.35) !important}
.search{height:58px !important;border-radius:20px !important}
.quick-action{min-height:54px !important;background:rgba(255,255,255,.20) !important;border-color:rgba(255,255,255,.34) !important}
@media (max-width:520px){
 .brand{font-size:18px;gap:5px;letter-spacing:-.65px}
 .hero{min-height:390px !important;padding:20px 16px 15px !important;background-position:center center !important}
 .hero h1{font-size:30px !important;margin-top:84px !important}
 .hero p{font-size:15px !important}
}

</style>
</head>
<body>
<div class="wrap"><div class="top"><div class="brand"><span class="brand-main">MADLOBA</span><span class="brand-market">MARKET</span></div><div style="display:flex;gap:7px;align-items:center"><select class="city" id="langSelect" aria-label="Language"><option value="ru">🇷🇺 RU</option><option value="en">🇬🇧 EN</option><option value="ka">🇬🇪 KA</option></select><button class="city" id="cityBtn">📍 <span id="cityName">Batumi</span>⌄</button></div></div><div class="hero"><h1 data-i18n="hero_title">Объявления рядом с вами</h1><p data-i18n="hero_subtitle">Покупайте, продавайте и находите нужное прямо в Telegram.</p><div class="search">🔎 <input id="search" data-i18n-placeholder="search_placeholder" placeholder="Что ищете? Например: квартира" autocomplete="off"></div><div class="quick-actions"><button class="quick-action" data-cat="realestate">🏠 <span>Недвижимость</span></button><button class="quick-action" data-cat="auto">🚗 <span>Авто</span></button><button class="quick-action" data-cat="tech">📱 <span>Техника</span></button><button class="quick-action" data-cat="work">💼 <span>Работа</span></button><button class="quick-action" data-cat="">▦ <span>Все</span></button></div><div class="filter-bar" id="filterBar"><button class="filter-btn" id="filterToggle" type="button">⚙️ <span data-i18n="filters">Фильтры</span><span id="filterActive"></span></button><div class="filter-panel" id="filterPanel"><div id="realestateFilterFields" class="filter-grid"><div class="filter-field"><label data-i18n="deal">Сделка</label><select id="filterDeal"><option value="" data-i18n="all">Все</option><option value="rent" data-i18n="rent">Сдам</option><option value="seek" data-i18n="seek">Сниму</option><option value="sell" data-i18n="sell">Продам</option><option value="buy" data-i18n="buy">Куплю</option></select></div><div class="filter-field"><label data-i18n="property_type">Тип</label><select id="filterSub"><option value="" data-i18n="all">Все</option><option value="apartment">🏢 Квартира</option><option value="house">🏡 Дом</option><option value="room">🛏 Комната</option><option value="commercial">🏬 Коммерция</option><option value="land">🌳 Земля</option><option value="garage">🚗 Гараж / парковка</option></select></div><div class="filter-field"><label data-i18n="price_from">Цена от</label><input id="filterMinPrice" inputmode="decimal" placeholder="0"></div><div class="filter-field"><label data-i18n="price_to">Цена до</label><input id="filterMaxPrice" inputmode="decimal" placeholder="∞"></div><div class="filter-field"><label data-i18n="rooms">Комнаты</label><select id="filterRooms"><option value="" data-i18n="all">Все</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4+</option></select></div><div class="filter-field"><label data-i18n="district">Район</label><input id="filterDistrict" data-i18n-placeholder="district_placeholder" placeholder="Например: Новый Бульвар"></div><div class="filter-field"><label data-i18n="area_from">Площадь от, м²</label><input id="filterMinArea" inputmode="decimal" placeholder="0"></div><div class="filter-field"><label data-i18n="area_to">Площадь до, м²</label><input id="filterMaxArea" inputmode="decimal" placeholder="∞"></div></div><div id="autoFilterFields" class="filter-grid" style="display:none">
<div class="filter-field" style="grid-column:1/-1"><label data-i18n="deal">Сделка</label><select id="autoFilterDeal"><option value="" data-i18n="all">Все</option><option value="sell" data-i18n="sell">Продам</option><option value="buy" data-i18n="buy">Куплю</option></select></div>
<div class="filter-field"><label data-i18n="auto_make">Марка</label><select id="autoFilterMake"><option value="" data-i18n="auto_select_make">Выберите марку</option></select></div>
<div class="filter-field"><label data-i18n="auto_model">Модель</label><select id="autoFilterModel" disabled><option value="" data-i18n="auto_select_model">Сначала выберите марку</option></select></div>
<div class="filter-field"><label data-i18n="price_from">Цена от</label><input id="autoFilterMinPrice" inputmode="decimal" placeholder="0"></div>
<div class="filter-field"><label data-i18n="price_to">Цена до</label><input id="autoFilterMaxPrice" inputmode="decimal" placeholder="∞"></div>
<div class="filter-field"><label data-i18n="year_from">Год от</label><input id="autoFilterMinYear" inputmode="numeric" placeholder="2010"></div>
<div class="filter-field"><label data-i18n="year_to">Год до</label><input id="autoFilterMaxYear" inputmode="numeric" placeholder="2026"></div>
<div class="filter-field"><label data-i18n="mileage_from">Пробег от, км</label><input id="autoFilterMinMileage" inputmode="numeric" placeholder="0"></div>
<div class="filter-field"><label data-i18n="mileage_to">Пробег до, км</label><input id="autoFilterMaxMileage" inputmode="numeric" placeholder="∞"></div>
</div><div class="filter-actions"><button class="filter-reset" id="filterReset" type="button" data-i18n="reset">Сбросить</button><button class="filter-apply" id="filterApply" type="button" data-i18n="apply">Применить</button></div></div></div></div><button class="back" id="backBtn" data-i18n="all_categories">← Все категории</button><section id="homeView"><div id="categoriesBlock"><div class="section-head"><h2 data-i18n="categories">Категории</h2><small id="countLabel"></small></div><div class="cats" id="cats"></div></div><div class="results-head"><div class="section-head"><h2 id="resultsTitle" data-i18n="fresh_listings">Свежие объявления</h2><small id="resultsCount"></small></div><select id="sortSelect" class="sort-select" aria-label="Sort"><option value="new" data-i18n="sort_new">🆕 Сначала новые</option><option value="price_asc" data-i18n="sort_price_asc">💰 Цена: дешевле</option><option value="price_desc" data-i18n="sort_price_desc">💰 Цена: дороже</option></select></div><div class="list" id="list"></div><div class="more"><button id="moreBtn" style="display:none" data-i18n="show_more">Показать ещё</button></div></section><section class="detail" id="detailView"><div class="detail-top"><button class="detail-back" id="detailBack" data-i18n="back">← Назад</button><div class="section-head" style="margin:0"><h2 data-i18n="listing">Объявление</h2></div></div><div id="detailContent"></div></section><section class="view" id="favoritesView"><button class="view-back" id="favoritesBack" data-i18n="back">← Назад</button><div class="view-title" data-i18n="favorites">Избранное</div><div class="list" id="favoritesList"></div></section><section class="view" id="mineView"><button class="view-back" id="mineBack" data-i18n="back">← Назад</button><div class="view-title" data-i18n="my_listings">Мои объявления</div><div class="mine-list" id="mineList"></div></section><section class="view" id="profileView"><button class="view-back" id="profileBack" data-i18n="profile">← Назад</button><div class="view-title" data-i18n="profile">Профиль</div><div id="profileContent"></div></section></div>
<nav class="bottom"><button class="nav active" data-nav="home"><span class="ni">⌂</span><span data-i18n="home">Главная</span></button><button class="nav" data-nav="favorites"><span class="ni">♡</span><span data-i18n="favorites">Избранное</span></button><button class="nav" data-nav="add"><span class="ni">＋</span><span data-i18n="post">Разместить</span></button><button class="nav" data-nav="mine"><span class="ni">▤</span><span data-i18n="mine_short">Мои</span></button><button class="nav" data-nav="profile"><span class="ni">◉</span><span data-i18n="profile">Профиль</span></button></nav><div class="toast" id="toast"></div>
<script>
const tg=window.Telegram&&window.Telegram.WebApp;if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#0b73f6');tg.setBackgroundColor('#f5f7fb')}catch(e){}}
const I18N={
ru:{hero_title:'Объявления рядом с вами',hero_subtitle:'Покупайте, продавайте и находите нужное прямо в Telegram.',search_placeholder:'Что ищете? Например: квартира',all_categories:'← Все категории',categories:'Категории',fresh_listings:'Свежие объявления',new_label:'новые',show_more:'Показать ещё',sort_new:'Сначала новые',sort_price_asc:'Цена: дешевле',sort_price_desc:'Цена: дороже',back:'← Назад',listing:'Объявление',favorites:'Избранное',my_listings:'Мои объявления',profile:'Профиль',home:'Главная',post:'Разместить',mine_short:'Мои',language:'Язык',filters:'Фильтры',deal:'Сделка',property_type:'Тип',price_from:'Цена от',price_to:'Цена до',rooms:'Комнаты',district:'Район',district_placeholder:'Например: Новый Бульвар',area_from:'Площадь от, м²',area_to:'Площадь до, м²',auto_make:'Марка',auto_model:'Модель',year_from:'Год от',year_to:'Год до',mileage_from:'Пробег от, км',mileage_to:'Пробег до, км',auto_make_placeholder:'Например: Toyota',auto_model_placeholder:'Например: Camry',auto_select_make:'Выберите марку',auto_select_model:'Сначала выберите марку',apply:'Применить',reset:'Сбросить',all:'Все',rent:'Сдам',seek:'Сниму',sell:'Продам',buy:'Куплю',empty_list:'Пока нет объявлений.<br>Попробуйте другую категорию или город.',photo_missing:'Фото не добавлено',favorite_add:'Добавить в избранное',favorite_remove:'Убрать из избранного',open:'Открыть',edit:'Редактировать',hide:'Снять',republish:'Вернуть в публикацию',delete:'Удалить',published:'Опубликовано',pending:'На модерации',rejected:'Отклонено',draft:'Черновик',archived:'Снято с публикации',save:'Сохранить',cancel:'Отмена',description:'Описание',price:'Цена',currency:'Валюта',address:'Адрес / район',phone:'Телефон',whatsapp:'WhatsApp',telegram:'Telegram',save_ok:'Объявление сохранено',save_fail:'Не удалось сохранить',login_telegram:'Откройте MADLOBA MARKET из Telegram',fav_added:'Добавлено в избранное',fav_removed:'Убрано из избранного',favorite_fail:'Не удалось изменить избранное',fav_empty_title:'Здесь пока пусто',fav_empty_text:'Нажимайте ♡ на понравившихся объявлениях.',fav_telegram:'Избранное доступно из Telegram',fav_telegram_text:'Откройте MADLOBA MARKET через Telegram.',loading:'Загружаем…',my_loading:'Загружаем ваши объявления…',my_empty:'У вас пока нет объявлений.',post_hint:'Разместите первое объявление через кнопку «Разместить».',profile_telegram:'Профиль доступен при запуске Mini App из Telegram.',profile_fail:'Не удалось загрузить профиль.',user:'Пользователь',status:'Статус',telegram_user:'Telegram-пользователь',status_user:'Пользователь MADLOBA MARKET',city:'Город',telegram_id:'Telegram ID',search_again:'Не удалось загрузить объявления.<br>Попробуйте ещё раз.',favorites_fail:'Не удалось загрузить избранное. Попробуйте ещё раз.',my_fail:'Не удалось загрузить ваши объявления. Попробуйте ещё раз.',open_fail:'Не удалось открыть объявление',edit_fail:'Не удалось открыть редактирование',hide_confirm:'Снять объявление с публикации? Оно исчезнет из каталога.',republish_confirm:'Вернуть объявление в публикацию? Оно снова появится в каталоге и будет опубликовано в канале.',delete_confirm:'Удалить объявление без возможности восстановления?',hide_ok:'Объявление снято с публикации',hide_fail:'Не удалось снять объявление',republish_ok:'Объявление снова опубликовано',republish_fail:'Не удалось вернуть объявление в публикацию',delete_ok:'Объявление удалено',delete_fail:'Не удалось удалить объявление',channel_fail:'Не удалось опубликовать в канале',placing:'Размещение откроется через бота',city_changed:'Город: ',no_listing:'Объявление',spec_rooms:'Комнаты',spec_area:'Площадь',spec_floor:'Этаж',spec_make_model:'Марка и модель',spec_year:'Год',spec_mileage:'Пробег',spec_brand_model:'Модель',spec_condition:'Состояние',spec_warranty:'Гарантия',spec_dimensions:'Размеры',spec_age:'Возраст',spec_service:'Услуга',spec_experience:'Опыт',spec_requirements:'Что ищете',category_realestate:'Недвижимость',category_auto:'Авто',category_tech:'Техника',category_home:'Дом и мебель',category_kids:'Детское',category_work:'Работа и услуги',category_give:'Отдам',category_search:'Ищу',desc_realestate:'Квартиры, дома, аренда',desc_auto:'Машины, мото, запчасти',desc_tech:'Телефоны, электроника',desc_home:'Мебель и всё для дома',desc_kids:'Детские товары',desc_work:'Услуги и вакансии',desc_give:'Бесплатно',desc_search:'Нужные вещи и услуги',contact_call:'📞 Позвонить',contact_whatsapp:'💬 WhatsApp',contact_telegram:'✈️ Telegram',open_channel:'📣 Открыть в канале'},
en:{hero_title:'Classifieds near you',hero_subtitle:'Buy, sell and find what you need right in Telegram.',search_placeholder:'What are you looking for? e.g. apartment',all_categories:'← All categories',categories:'Categories',fresh_listings:'Fresh listings',new_label:'NEW',show_more:'Show more',sort_new:'Newest first',sort_price_asc:'Price: low to high',sort_price_desc:'Price: high to low',back:'← Back',listing:'Listing',favorites:'Favorites',my_listings:'My listings',profile:'Profile',home:'Home',post:'Post',mine_short:'My listings',language:'Language',filters:'Filters',deal:'Deal',property_type:'Type',price_from:'Price from',price_to:'Price to',rooms:'Rooms',district:'District',district_placeholder:'e.g. New Boulevard',area_from:'Area from, m²',area_to:'Area to, m²',auto_make:'Make',auto_model:'Model',year_from:'Year from',year_to:'Year to',mileage_from:'Mileage from, km',mileage_to:'Mileage to, km',auto_make_placeholder:'e.g. Toyota',auto_model_placeholder:'e.g. Camry',auto_select_make:'Select make',auto_select_model:'Select a make first',apply:'Apply',reset:'Reset',all:'All',rent:'Rent out',seek:'Rent',sell:'Sell',buy:'Buy',empty_list:'No listings yet.<br>Try another category or city.',photo_missing:'No photo added',favorite_add:'Add to favorites',favorite_remove:'Remove from favorites',open:'Open',edit:'Edit',hide:'Unpublish',republish:'Republish',delete:'Delete',published:'Published',pending:'Pending moderation',rejected:'Rejected',draft:'Draft',archived:'Unpublished',save:'Save',cancel:'Cancel',description:'Description',price:'Price',currency:'Currency',address:'Address / area',phone:'Phone',whatsapp:'WhatsApp',telegram:'Telegram',save_ok:'Listing saved',save_fail:'Could not save',login_telegram:'Open MADLOBA MARKET from Telegram',fav_added:'Added to favorites',fav_removed:'Removed from favorites',favorite_fail:'Could not change favorites',fav_empty_title:'Nothing here yet',fav_empty_text:'Tap ♡ on listings you like.',fav_telegram:'Favorites are available in Telegram',fav_telegram_text:'Open MADLOBA MARKET through Telegram.',loading:'Loading…',my_loading:'Loading your listings…',my_empty:'You have no listings yet.',post_hint:'Post your first listing using the “Post” button.',profile_telegram:'Profile is available when the Mini App is opened from Telegram.',profile_fail:'Could not load profile.',user:'User',status:'Status',telegram_user:'Telegram user',status_user:'MADLOBA MARKET user',city:'City',telegram_id:'Telegram ID',search_again:'Could not load listings.<br>Try again.',favorites_fail:'Could not load favorites. Try again.',my_fail:'Could not load your listings. Try again.',open_fail:'Could not open listing',edit_fail:'Could not open editing',hide_confirm:'Unpublish this listing? It will disappear from the catalog.',republish_confirm:'Republish this listing? It will appear in the catalog again and be published to the channel.',delete_confirm:'Delete this listing permanently?',hide_ok:'Listing unpublished',hide_fail:'Could not unpublish listing',republish_ok:'Listing republished',republish_fail:'Could not republish listing',delete_ok:'Listing deleted',delete_fail:'Could not delete listing',channel_fail:'Could not publish to the channel',placing:'Posting will open through the bot',city_changed:'City: ',no_listing:'Listing',spec_rooms:'Rooms',spec_area:'Area',spec_floor:'Floor',spec_make_model:'Make and model',spec_year:'Year',spec_mileage:'Mileage',spec_brand_model:'Model',spec_condition:'Condition',spec_warranty:'Warranty',spec_dimensions:'Dimensions',spec_age:'Age',spec_service:'Service',spec_experience:'Experience',spec_requirements:'What are you looking for',category_realestate:'Real estate',category_auto:'Cars',category_tech:'Electronics',category_home:'Home & furniture',category_kids:'Kids',category_work:'Jobs & services',category_give:'Free',category_search:'Wanted',desc_realestate:'Apartments, houses, rentals',desc_auto:'Cars, motorcycles, parts',desc_tech:'Phones, electronics',desc_home:'Furniture and home goods',desc_kids:'Kids products',desc_work:'Services and jobs',desc_give:'Free items',desc_search:'Wanted items and services',contact_call:'📞 Call',contact_whatsapp:'💬 WhatsApp',contact_telegram:'✈️ Telegram',open_channel:'📣 Open in channel'},
ka:{hero_title:'განცხადებები თქვენთან ახლოს',hero_subtitle:'იყიდეთ, გაყიდეთ და იპოვეთ სასურველი პირდაპირ Telegram-ში.',search_placeholder:'რას ეძებთ? მაგალითად: ბინა',all_categories:'← ყველა კატეგორია',categories:'კატეგორიები',fresh_listings:'ახალი განცხადებები',new_label:'ახალი',show_more:'მეტის ჩვენება',sort_new:'ჯერ ახალი',sort_price_asc:'ფასი: იაფიდან',sort_price_desc:'ფასი: ძვირიდან',back:'← უკან',listing:'განცხადება',favorites:'რჩეულები',my_listings:'ჩემი განცხადებები',profile:'პროფილი',home:'მთავარი',post:'განთავსება',mine_short:'ჩემი',language:'ენა',filters:'ფილტრები',deal:'გარიგება',property_type:'ტიპი',price_from:'ფასი მინ.',price_to:'ფასი მაქს.',rooms:'ოთახები',district:'რაიონი',district_placeholder:'მაგ. New Boulevard',area_from:'ფართობი მინ., მ²',area_to:'ფართობი მაქს., მ²',auto_make:'მარკა',auto_model:'მოდელი',year_from:'წელი მინ.',year_to:'წელი მაქს.',mileage_from:'გარბენი მინ., კმ',mileage_to:'გარბენი მაქს., კმ',auto_make_placeholder:'მაგ. Toyota',auto_model_placeholder:'მაგ. Camry',auto_select_make:'აირჩიეთ მარკა',auto_select_model:'ჯერ აირჩიეთ მარკა',apply:'გამოყენება',reset:'გასუფთავება',all:'ყველა',rent:'ვაქირავებ',seek:'ვიქირავებ',sell:'ვყიდი',buy:'ვყიდულობ',empty_list:'განცხადებები ჯერ არ არის.<br>სცადეთ სხვა კატეგორია ან ქალაქი.',photo_missing:'ფოტო არ არის დამატებული',favorite_add:'რჩეულებში დამატება',favorite_remove:'რჩეულებიდან წაშლა',open:'გახსნა',edit:'რედაქტირება',hide:'გამოქვეყნების მოხსნა',republish:'ხელახლა გამოქვეყნება',delete:'წაშლა',published:'გამოქვეყნებული',pending:'მოდერაციაზეა',rejected:'უარყოფილი',draft:'დრაფტი',archived:'გამოქვეყნებიდან მოხსნილი',save:'შენახვა',cancel:'გაუქმება',description:'აღწერა',price:'ფასი',currency:'ვალუტა',address:'მისამართი / რაიონი',phone:'ტელეფონი',whatsapp:'WhatsApp',telegram:'Telegram',save_ok:'განცხადება შენახულია',save_fail:'შენახვა ვერ მოხერხდა',login_telegram:'გახსენით MADLOBA MARKET Telegram-იდან',fav_added:'დაემატა რჩეულებში',fav_removed:'წაიშალა რჩეულებიდან',favorite_fail:'რჩეულების შეცვლა ვერ მოხერხდა',fav_empty_title:'აქ ჯერ არაფერია',fav_empty_text:'დააჭირეთ ♡ სასურველ განცხადებებზე.',fav_telegram:'რჩეულები ხელმისაწვდომია Telegram-ში',fav_telegram_text:'გახსენით MADLOBA MARKET Telegram-იდან.',loading:'იტვირთება…',my_loading:'თქვენი განცხადებები იტვირთება…',my_empty:'თქვენ ჯერ განცხადებები არ გაქვთ.',post_hint:'პირველი განცხადება დაამატეთ ღილაკით „განთავსება“.',profile_telegram:'პროფილი ხელმისაწვდომია Mini App-ის Telegram-იდან გახსნისას.',profile_fail:'პროფილის ჩატვირთვა ვერ მოხერხდა.',user:'მომხმარებელი',status:'სტატუსი',telegram_user:'Telegram მომხმარებელი',status_user:'MADLOBA MARKET-ის მომხმარებელი',city:'ქალაქი',telegram_id:'Telegram ID',search_again:'განცხადებების ჩატვირთვა ვერ მოხერხდა.<br>სცადეთ ხელახლა.',favorites_fail:'რჩეულების ჩატვირთვა ვერ მოხერხდა. სცადეთ ხელახლა.',my_fail:'თქვენი განცხადებების ჩატვირთვა ვერ მოხერხდა. სცადეთ ხელახლა.',open_fail:'განცხადების გახსნა ვერ მოხერხდა',edit_fail:'რედაქტირების გახსნა ვერ მოხერხდა',hide_confirm:'მოხსნათ განცხადება გამოქვეყნებიდან? ის კატალოგიდან გაქრება.',republish_confirm:'ხელახლა გამოაქვეყნოთ განცხადება? ის ისევ გამოჩნდება კატალოგში და არხში.',delete_confirm:'წავშალოთ განცხადება სამუდამოდ?',hide_ok:'განცხადება გამოქვეყნებიდან მოიხსნა',hide_fail:'გამოქვეყნებიდან მოხსნა ვერ მოხერხდა',republish_ok:'განცხადება ხელახლა გამოქვეყნდა',republish_fail:'ხელახლა გამოქვეყნება ვერ მოხერხდა',delete_ok:'განცხადება წაიშალა',delete_fail:'განცხადების წაშლა ვერ მოხერხდა',channel_fail:'არხში გამოქვეყნება ვერ მოხერხდა',placing:'განთავსება გაიხსნება ბოტის საშუალებით',city_changed:'ქალაქი: ',no_listing:'განცხადება',spec_rooms:'ოთახები',spec_area:'ფართობი',spec_floor:'სართული',spec_make_model:'მარკა და მოდელი',spec_year:'წელი',spec_mileage:'გარბენი',spec_brand_model:'მოდელი',spec_condition:'მდგომარეობა',spec_warranty:'გარანტია',spec_dimensions:'ზომები',spec_age:'ასაკი',spec_service:'სერვისი',spec_experience:'გამოცდილება',spec_requirements:'რას ეძებთ',category_realestate:'უძრავი ქონება',category_auto:'ავტო',category_tech:'ტექნიკა',category_home:'სახლი და ავეჯი',category_kids:'ბავშვები',category_work:'სამუშაო და სერვისები',category_give:'გაჩუქება',category_search:'ვეძებ',desc_realestate:'ბინები, სახლები, ქირა',desc_auto:'მანქანები, მოტო, ნაწილები',desc_tech:'ტელეფონები, ელექტრონიკა',desc_home:'ავეჯი და სახლის ნივთები',desc_kids:'ბავშვთა ნივთები',desc_work:'სერვისები და ვაკანსიები',desc_give:'უფასოდ',desc_search:'საჭირო ნივთები და სერვისები',contact_call:'📞 დარეკვა',contact_whatsapp:'💬 WhatsApp',contact_telegram:'✈️ Telegram',open_channel:'📣 არხში გახსნა'}
};
const state={city:localStorage.getItem('mm_city')||'batumi',lang:localStorage.getItem('mm_lang')||'ru',page:0,q:'',category:'',loading:false,sort:'new',filters:{deal:'',sub:'',min_price:'',max_price:'',rooms:'',min_area:'',max_area:'',district:'',make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''}};
function t(k){return (I18N[state.lang]&&I18N[state.lang][k])||I18N.ru[k]||k}
function applyLang(){document.documentElement.lang=state.lang;document.querySelectorAll('[data-i18n]').forEach(el=>el.innerHTML=t(el.dataset.i18n));document.querySelectorAll('[data-i18n-placeholder]').forEach(el=>el.placeholder=t(el.dataset.i18nPlaceholder));$('langSelect').value=state.lang;$('cityName').textContent=cityNames[state.city][state.lang]||cityNames[state.city].ru;renderCats();updateFilterVisibility()}

const cats=[['realestate','🏠'],['auto','🚗'],['tech','📱'],['home','🛋️'],['kids','🧸'],['work','💼'],['give','🎁'],['search','🔎']];
const cityNames={batumi:{ru:'Batumi',en:'Batumi',ka:'ბათუმი'},tbilisi:{ru:'Tbilisi',en:'Tbilisi',ka:'თბილისი'}};
const $=id=>document.getElementById(id);
const tgInit=()=>window.Telegram&&window.Telegram.WebApp?window.Telegram.WebApp.initData:'';
function toast(t){$('toast').textContent=t;$('toast').classList.add('show');clearTimeout(window.__toast);window.__toast=setTimeout(()=>$('toast').classList.remove('show'),1800)}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function money(v,c){const m={usd:'$',gel:'₾',eur:'€',USD:'$',GEL:'₾',EUR:'€'};return esc(v)+' '+(m[c]||esc(c||''))}
function apiFetch(url,options={}){const headers=new Headers(options.headers||{});const init=tgInit();if(init)headers.set('X-Telegram-Init-Data',init);return fetch(url,{...options,headers})}
function hideViews(){['homeView','detailView','favoritesView','mineView','profileView'].forEach(id=>$(id).classList.remove('show'));$('homeView').style.display='none'}
function showHome(){hideViews();$('homeView').style.display='block';$('detailView').classList.remove('show');$('backBtn').classList.remove('show');state.category='';state.sort='new';state.filters={deal:'',sub:'',min_price:'',max_price:'',rooms:'',min_area:'',max_area:'',district:'',make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''};updateFilterVisibility();load(true)}
function showView(id){hideViews();$(id).classList.add('show')}
function realEstateFiltersActive(){return Object.entries(state.filters).some(([k,v])=>['deal','sub','min_price','max_price','rooms','min_area','max_area','district'].includes(k)&&String(v||'').trim()!=='')}
function autoFiltersActive(){return Object.entries(state.filters).some(([k,v])=>['deal','make','model','min_price','max_price','min_year','max_year','min_mileage','max_mileage'].includes(k)&&String(v||'').trim()!=='')}
async function loadAutoOptions(make=''){
 try{
  const p=new URLSearchParams({city:state.city});
  if(make)p.set('make',make);
  const r=await apiFetch('/api/auto-options?'+p.toString());
  if(!r.ok)throw 0;
  const data=await r.json();
  const makeEl=$('autoFilterMake'), modelEl=$('autoFilterModel');
  if(!make){
   const current=state.filters.make||'';
   makeEl.innerHTML=`<option value="">${esc(t('auto_select_make'))}</option>`+(data.makes||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
   makeEl.value=current;
  }
  modelEl.innerHTML=`<option value="">${esc(t('auto_select_model'))}</option>`+(data.models||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
  modelEl.disabled=!make;
  if(make && state.filters.model)modelEl.value=state.filters.model;
 }catch(e){}
}
function syncFilterUI(){
 const f=state.filters;
 const map={filterDeal:'deal',filterSub:'sub',filterMinPrice:'min_price',filterMaxPrice:'max_price',filterRooms:'rooms',filterMinArea:'min_area',filterMaxArea:'max_area',filterDistrict:'district',autoFilterDeal:'deal',autoFilterMake:'make',autoFilterModel:'model',autoFilterMinPrice:'min_price',autoFilterMaxPrice:'max_price',autoFilterMinYear:'min_year',autoFilterMaxYear:'max_year',autoFilterMinMileage:'min_mileage',autoFilterMaxMileage:'max_mileage'};
 Object.entries(map).forEach(([id,key])=>{const el=$(id);if(el)el.value=f[key]||''});
 $('filterActive').textContent=(state.category==='realestate'?realEstateFiltersActive():state.category==='auto'?autoFiltersActive():false)?' • ✓':'';
}
function updateFilterVisibility(){
 const show=['realestate','auto'].includes(state.category);
 $('filterBar').classList.toggle('show',show);
 if(!show)$('filterPanel').classList.remove('show');
 $('realestateFilterFields').style.display=state.category==='realestate'?'grid':'none';
 $('autoFilterFields').style.display=state.category==='auto'?'grid':'none';
 $('categoriesBlock').style.display=state.category?'none':'block';
 $('resultsTitle').textContent=state.category?t('category_'+state.category):t('fresh_listings');
 $('sortSelect').style.display=state.category?'block':'none';
 syncFilterUI();if(state.category==='auto')loadAutoOptions(state.filters.make||'')
}
function renderCats(){$('cats').innerHTML=cats.map(c=>`<button class="cat" data-cat="${c[0]}"><span class="ico">${c[1]}</span><b>${t('category_'+c[0])}</b><small>${t('desc_'+c[0])}</small></button>`).join('');document.querySelectorAll('[data-cat]').forEach(b=>b.onclick=()=>{showHome();state.category=b.dataset.cat;$('backBtn').classList.add('show');updateFilterVisibility();load(true)})}
const FIELD_LABELS={rooms:['🛏','spec_rooms'],area:['📐','spec_area'],floor:['🏢','spec_floor'],make_model:['🚗','spec_make_model'],year:['📅','spec_year'],mileage:['🛣','spec_mileage'],brand_model:['📱','spec_brand_model'],condition:['✨','spec_condition'],warranty:['🛡','spec_warranty'],dimensions:['📏','spec_dimensions'],age:['👶','spec_age'],service:['🛠','spec_service'],experience:['⭐','spec_experience'],requirements:['📋','spec_requirements']};
function formatSpecValue(k,v){const value=String(v??'').trim();if(!value)return '';if(k==='area'&&!/м²|m²|м2|m2/i.test(value))return value+' м²';if(k==='mileage'&&!/км|km/i.test(value))return value+' км';return value}
function detailSpecs(d,detail=false){return Object.entries(d||{}).filter(([k,v])=>v!==''&&v!=null).slice(0,6).map(([k,v])=>{const meta=FIELD_LABELS[k]||['•',k];const cls=detail?'detail-spec':'spec';const vc=detail?'detail-spec-value':'spec-value';const lc=detail?'detail-spec-label':'spec-label';const formatted=formatSpecValue(k,v);return `<div class="${cls}"><div class="${vc}">${esc(meta[0])} ${esc(formatted)}</div><div class="${lc}">${esc(t(meta[1]))}</div></div>`}).join('')}
function isFav(x){return !!x.is_favorite}
function favButton(x,detail=false){const active=isFav(x);const cls=detail?'detail-fav':'fav-btn';return `<button class="${cls}${active?' active':''}" data-fav="${esc(x.id)}" aria-label="${active?t('favorite_remove'):t('favorite_add')}">${active?'♥':'♡'}</button>`}
function localizedCategory(v){const m={'Недвижимость':'category_realestate','Авто':'category_auto','Техника':'category_tech','Дом и мебель':'category_home','Детское':'category_kids','Работа и услуги':'category_work','Отдам':'category_give','Ищу':'category_search'};return m[v]?t(m[v]):(v||t('no_listing'))}
function card(x){const photo=(x.photos||[])[0];const title=x.title||x.category||t('no_listing');const d=x.details||{};const specs=detailSpecs(d,false);return `<article class="card" data-id="${esc(x.id)}">${favButton(x)}<div class="photo">${photo?`<img src="/media/${encodeURIComponent(x.id)}/0" loading="lazy" onerror="this.parentElement.innerHTML='<span>📷</span>'">`:`<div class="photo-empty"><span>📷</span><small>${t('photo_missing')}</small></div>`}</div><div class="cardbody"><div class="tag">${esc(localizedCategory(x.category_name||x.category||''))}</div><div class="title">${esc(title)}</div>${x.description?`<div class="desc">${esc(String(x.description).slice(0,180))}</div>`:''}${specs?`<div class="meta">${specs}</div>`:''}${x.price?`<div class="price">${money(x.price,x.currency)}</div>`:''}${x.address?`<div class="loc">📍 ${esc(x.address)}</div>`:''}</div></article>`}
function bindCards(){document.querySelectorAll('#list [data-id]').forEach(el=>el.onclick=()=>openDetail(el.dataset.id));document.querySelectorAll('#list [data-fav]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();toggleFavorite(btn.dataset.fav,btn)})}
async function openDetail(id){try{const r=await apiFetch('/api/listing/'+encodeURIComponent(id));if(!r.ok)throw 0;const x=await r.json();renderDetail(x);hideViews();$('detailView').classList.add('show');window.scrollTo({top:0,behavior:'smooth'})}catch(e){toast(t('open_fail'))}}
function renderDetail(x){const photos=x.photos||[];const d=x.details||{};const specs=detailSpecs(d,true);const phone=String(x.phone||'').trim();const whatsapp=String(x.whatsapp||'').trim().replace(/[^0-9]/g,'');const telegram=String(x.telegram||'').trim().replace(/^@/,'');const contact=[];if(phone)contact.push(`<a class="contact-btn" href="tel:${encodeURIComponent(phone)}">${t('contact_call')}</a>`);if(whatsapp)contact.push(`<a class="contact-btn" href="https://wa.me/${whatsapp}">${t('contact_whatsapp')}</a>`);if(telegram)contact.push(`<a class="contact-btn secondary" href="https://t.me/${encodeURIComponent(telegram)}">${t('contact_telegram')}</a>`);if(x.channel_post_url)contact.push(`<a class="contact-btn secondary" href="${esc(x.channel_post_url)}" target="_blank">${t('open_channel')}</a>`);$('detailContent').innerHTML=`<div class="detail-photo" id="detailPhoto">${photos.length?`<img id="detailImg" src="/media/${encodeURIComponent(x.id)}/0" onerror="this.parentElement.innerHTML='<span>📷</span>'">`:`<div class="photo-empty"><span>📷</span><small>${t('photo_missing')}</small></div>`}${photos.length>1?`<button class="gallery-btn prev" id="prevPhoto">‹</button><button class="gallery-btn next" id="nextPhoto">›</button><span class="gallery-count" id="photoCount">1/${photos.length}</span>`:''}</div><div class="detail-body"><div class="detail-head-row"><div style="min-width:0;flex:1"><div class="detail-tag">${esc(localizedCategory(x.category_name||x.category||''))}</div><div class="detail-title">${esc(x.title||t('no_listing'))}</div></div>${favButton(x,true)}</div>${x.price?`<div class="detail-price">${money(x.price,x.currency)}</div>`:''}${specs?`<div class="detail-meta">${specs}</div>`:''}${x.address?`<div class="detail-loc">📍 ${esc(x.address)}</div>`:''}${x.description?`<div class="detail-desc">${esc(x.description)}</div>`:''}${contact.length?`<div class="contacts">${contact.join('')}</div>`:''}</div>`;const fav=$('detailContent').querySelector('[data-fav]');if(fav)fav.onclick=e=>{e.stopPropagation();toggleFavorite(x.id,fav,x)};if(photos.length>1){let idx=0;const img='detailImg';const update=()=>{$(img).src='/media/'+encodeURIComponent(x.id)+'/'+idx;$('photoCount').textContent=(idx+1)+'/'+photos.length};$('prevPhoto').onclick=e=>{e.stopPropagation();idx=(idx-1+photos.length)%photos.length;update()};$('nextPhoto').onclick=e=>{e.stopPropagation();idx=(idx+1)%photos.length;update()}}}
async function toggleFavorite(id,button,x=null){try{const active=button.classList.contains('active');const r=await apiFetch('/api/favorite/'+encodeURIComponent(id),{method:active?'DELETE':'POST'});if(r.status===401){toast(t('login_telegram'));return}if(!r.ok){const er=await r.json().catch(()=>({}));throw new Error(er.error||'favorite_failed')}const now=!active;button.classList.toggle('active',now);button.textContent=now?'♥':'♡';button.setAttribute('aria-label',now?t('favorite_remove'):t('favorite_add'));if(x)x.is_favorite=now;toast(now?t('fav_added'):t('fav_removed'));if(document.getElementById('favoritesView').classList.contains('show'))await loadFavorites()}catch(e){toast(t('favorite_fail'))}}
function favoriteCard(x){return `<div class="fav-card">${card(x)}</div>`}
async function loadFavorites(){showView('favoritesView');$('favoritesList').innerHTML='<div class="empty">'+t('loading')+'</div>';try{const r=await apiFetch('/api/favorites');if(r.status===401){$('favoritesList').innerHTML=`<div class="fav-empty"><span class="heart">♡</span><b>${t('fav_telegram')}</b><span>${t('fav_telegram_text')}</span></div>`;return}if(!r.ok)throw 0;const data=await r.json();if(!data.items?.length){$('favoritesList').innerHTML=`<div class="fav-empty"><span class="heart">♡</span><b>${t('fav_empty_title')}</b><span>${t('fav_empty_text')}</span></div>`;return}$('favoritesList').innerHTML=data.items.map(favoriteCard).join('');document.querySelectorAll('#favoritesList [data-id]').forEach(el=>el.onclick=()=>openDetail(el.dataset.id));document.querySelectorAll('#favoritesList [data-fav]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();toggleFavorite(btn.dataset.fav,btn)});}catch(e){$('favoritesList').innerHTML=`<div class="empty">${t('favorites_fail')}</div>`}}
async function load(reset=true){if(state.loading)return;state.loading=true;if(reset){state.page=0;$('list').innerHTML=''}const p=new URLSearchParams({city:state.city,page:state.page,per_page:20,sort:state.sort});if(state.category)p.set('category',state.category);if(state.q)p.set('q',state.q);if(['realestate','auto'].includes(state.category)){Object.entries(state.filters).forEach(([k,v])=>{if(String(v||'').trim())p.set(k,String(v).trim())})}try{const r=await apiFetch('/api/listings?'+p);if(!r.ok)throw 0;const data=await r.json();if(reset)$('list').innerHTML='';$('list').insertAdjacentHTML('beforeend',(data.items||[]).map(card).join(''));bindCards();$('moreBtn').style.display=data.has_next?'inline-block':'none';$('countLabel').textContent=state.category?'':(data.total_hint?data.total_hint+'+':'');$('resultsCount').textContent=data.items&&data.items.length?(data.items.length+(data.has_next?'+':'')):'0';if(reset&&!data.items?.length)$('list').innerHTML=`<div class="empty">${t('empty_list')}</div>`}catch(e){if(reset)$('list').innerHTML=`<div class="empty">${t('search_again')}</div>`}finally{state.loading=false}}
function statusText(status){const map={published:'published',pending:'pending',rejected:'rejected',draft:'draft',archived:'archived'};return t(map[String(status||'').toLowerCase()]||'no_listing')}
function mineCard(x){const photo=(x.photos||[])[0];const st=String(x.status||'').toLowerCase();const open=st==='published'?`<button class="mine-action primary" data-action="open">👁 ${t('open')}</button>`:'';const edit=`<button class="mine-action" data-action="edit">✏️ ${t('edit')}</button>`;const hide=st==='published'?`<button class="mine-action warn" data-action="unpublish">⏸ ${t('hide')}</button>`:'';const republish=st==='archived'?`<button class="mine-action primary" data-action="republish">📣 ${t('republish')}</button>`:'';const del=`<button class="mine-action danger" data-action="delete">🗑 ${t('delete')}</button>`;return `<div class="mine-card" data-id="${esc(x.id)}"><div class="mine-row"><div class="mine-thumb">${photo&&st==='published'?`<img src="/media/${encodeURIComponent(x.id)}/0" loading="lazy">`:'📷'}</div><div class="mine-info"><div class="mine-title">${esc(x.title||t('no_listing'))}</div>${x.price?`<div class="mine-price">${money(x.price,x.currency)}</div>`:''}<span class="status">${esc(statusText(x.status))}</span></div></div><div class="mine-actions">${open}${edit}${hide}${republish}${del}</div></div>`}
async function loadMine(){showView('mineView');$('mineList').innerHTML='<div class="empty">'+t('my_loading')+'</div>';try{const r=await apiFetch('/api/my-listings');if(r.status===401){$('mineList').innerHTML=`<div class="empty">${t('login_telegram')}</div>`;return}if(!r.ok)throw 0;const data=await r.json();if(!data.items?.length){$('mineList').innerHTML=`<div class="empty">${t('my_empty')}<br><br>${t('post_hint')}</div>`;return}$('mineList').innerHTML=data.items.map(mineCard).join('');document.querySelectorAll('#mineList .mine-card').forEach(card=>{const id=card.dataset.id;card.querySelectorAll('[data-action]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();const a=btn.dataset.action;if(a==='open')openDetail(id);if(a==='edit')openEdit(id);if(a==='unpublish')unpublishMine(id);if(a==='republish')republishMine(id);if(a==='delete')deleteMine(id)})})}catch(e){$('mineList').innerHTML=`<div class="empty">${t('my_fail')}</div>`}}
async function openEdit(id){try{const r=await apiFetch('/api/my-listing/'+encodeURIComponent(id));if(!r.ok)throw 0;const x=await r.json();showView('mineView');$('mineList').innerHTML=`<div class="edit-panel"><div class="edit-title">✏️ ${t('edit')}</div><div class="edit-field"><label>${t('description')}</label><textarea id="editDescription">${esc(x.description||'')}</textarea></div><div class="edit-field"><label>${t('price')}</label><input id="editPrice" inputmode="decimal" value="${esc(x.price||'')}"></div><div class="edit-field"><label>${t('currency')}</label><select id="editCurrency"><option value="USD" ${String(x.currency).toUpperCase()==='USD'?'selected':''}>USD ($)</option><option value="GEL" ${String(x.currency).toUpperCase()==='GEL'?'selected':''}>GEL (₾)</option><option value="EUR" ${String(x.currency).toUpperCase()==='EUR'?'selected':''}>EUR (€)</option></select></div><div class="edit-field"><label>${t('address')}</label><input id="editAddress" value="${esc(x.address||'')}"></div><div class="edit-field"><label>${t('phone')}</label><input id="editPhone" inputmode="tel" value="${esc(x.phone||'')}"></div><div class="edit-field"><label>${t('whatsapp')}</label><input id="editWhatsapp" value="${esc(x.whatsapp||'')}"></div><div class="edit-field"><label>${t('telegram')}</label><input id="editTelegram" value="${esc(x.telegram||'')}"></div><div class="edit-actions"><button class="edit-cancel" id="editCancel">${t('cancel')}</button><button class="edit-save" id="editSave">${t('save')}</button></div></div>`;$('editCancel').onclick=loadMine;$('editSave').onclick=async()=>{const payload={description:$('editDescription').value,price:$('editPrice').value,currency:$('editCurrency').value,address:$('editAddress').value,phone:$('editPhone').value,whatsapp:$('editWhatsapp').value,telegram:$('editTelegram').value};$('editSave').disabled=true;$('editSave').textContent=t('loading');try{const rr=await apiFetch('/api/my-listing/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!rr.ok){const er=await rr.json().catch(()=>({}));throw new Error(er.detail||er.error||'save_failed')}toast(t('save_ok'));await loadMine()}catch(e){toast(e.message && e.message!=='save_failed'?e.message:t('save_fail'));$('editSave').disabled=false;$('editSave').textContent=t('save')}}}catch(e){toast(t('edit_fail'))}}
async function unpublishMine(id){if(!confirm(t('hide_confirm')))return;try{const r=await apiFetch('/api/my-listing/'+encodeURIComponent(id)+'/unpublish',{method:'POST'});if(!r.ok)throw 0;toast(t('hide_ok'));await loadMine()}catch(e){toast(t('hide_fail'))}}
async function republishMine(id){if(!confirm(t('republish_confirm')))return;try{const r=await apiFetch('/api/my-listing/'+encodeURIComponent(id)+'/republish',{method:'POST'});if(!r.ok){const er=await r.json().catch(()=>({}));throw new Error(er.error||'republish_failed')}toast(t('republish_ok'));await loadMine()}catch(e){toast(e.message==='channel_publish_failed'?t('channel_fail'):t('republish_fail'))}}
async function deleteMine(id){if(!confirm(t('delete_confirm')))return;try{const r=await apiFetch('/api/my-listing/'+encodeURIComponent(id),{method:'DELETE'});if(!r.ok)throw 0;toast(t('delete_ok'));await loadMine()}catch(e){toast(t('delete_fail'))}}
async function loadProfile(){showView('profileView');$('profileContent').innerHTML='<div class="empty">'+t('loading')+'</div>';try{const r=await apiFetch('/api/me');if(r.status===401){$('profileContent').innerHTML=`<div class="empty">${t('profile_telegram')}</div>`;return}if(!r.ok)throw 0;const u=await r.json();const initials=esc(((u.first_name||'')+' '+(u.last_name||'')).trim().split(/\s+/).map(x=>x[0]).join('').slice(0,2).toUpperCase()||'MM');$('profileContent').innerHTML=`<div class="account-head"><div class="account-avatar">${initials}</div><div><div class="account-name">${esc(([u.first_name,u.last_name].filter(Boolean).join(' ')||t('user')))}</div><div class="account-sub">${u.username?'@'+esc(u.username):t('telegram_user')}</div></div></div><div class="profile-card"><div class="profile-item"><div class="profile-label">${t('telegram_id')}</div><div class="profile-value">${esc(u.id)}</div></div><div class="profile-item"><div class="profile-label">${t('city')}</div><div class="profile-value">${esc(cityNames[state.city][state.lang]||state.city)}</div></div><div class="profile-item"><div class="profile-label">${t('status')}</div><div class="profile-value">${t('status_user')}</div></div></div>`}catch(e){$('profileContent').innerHTML=`<div class="empty">${t('profile_fail')}</div>`}}
$('cityBtn').onclick=()=>{state.city=state.city==='batumi'?'tbilisi':'batumi';localStorage.setItem('mm_city',state.city);$('cityName').textContent=cityNames[state.city][state.lang];state.filters={deal:'',sub:'',min_price:'',max_price:'',rooms:'',min_area:'',max_area:'',district:'',make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''};updateFilterVisibility();load(true);toast(t('city_changed')+cityNames[state.city][state.lang])};
$('langSelect').onchange=e=>{state.lang=e.target.value;localStorage.setItem('mm_lang',state.lang);applyLang();load(true);};
$('search').oninput=e=>{state.q=e.target.value.trim();clearTimeout(window.__search);window.__search=setTimeout(()=>load(true),350)};
$('sortSelect').onchange=()=>{state.sort=$('sortSelect').value;load(true)};
$('filterToggle').onclick=()=>{if(['realestate','auto'].includes(state.category))$('filterPanel').classList.toggle('show')};
$('autoFilterMake').onchange=async e=>{state.filters.make=e.target.value;state.filters.model='';$('autoFilterModel').value='';await loadAutoOptions(e.target.value)};
$('autoFilterModel').onchange=e=>{state.filters.model=e.target.value};
$('filterApply').onclick=()=>{
 if(state.category==='realestate'){
  state.filters={deal:$('filterDeal').value,sub:$('filterSub').value,min_price:$('filterMinPrice').value.trim(),max_price:$('filterMaxPrice').value.trim(),rooms:$('filterRooms').value,min_area:$('filterMinArea').value.trim(),max_area:$('filterMaxArea').value.trim(),district:$('filterDistrict').value.trim(),make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''};
 }else if(state.category==='auto'){
  state.filters={deal:$('autoFilterDeal').value,sub:'',min_price:$('autoFilterMinPrice').value.trim(),max_price:$('autoFilterMaxPrice').value.trim(),rooms:'',min_area:'',max_area:'',district:'',make:$('autoFilterMake').value.trim(),model:$('autoFilterModel').value.trim(),min_year:$('autoFilterMinYear').value.trim(),max_year:$('autoFilterMaxYear').value.trim(),min_mileage:$('autoFilterMinMileage').value.trim(),max_mileage:$('autoFilterMaxMileage').value.trim()};
 }
 $('filterPanel').classList.remove('show');syncFilterUI();load(true)
};
$('filterReset').onclick=()=>{state.filters={deal:'',sub:'',min_price:'',max_price:'',rooms:'',min_area:'',max_area:'',district:'',make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''};syncFilterUI();load(true)};
$('moreBtn').onclick=()=>{state.page++;load(false)};
$('backBtn').onclick=()=>{state.category='';state.sort='new';state.filters={deal:'',sub:'',min_price:'',max_price:'',rooms:'',min_area:'',max_area:'',district:'',make:'',model:'',min_year:'',max_year:'',min_mileage:'',max_mileage:''};$('sortSelect').value='new';$('backBtn').classList.remove('show');updateFilterVisibility();load(true)};
$('detailBack').onclick=()=>showHome();
$('favoritesBack').onclick=()=>showHome();
$('mineBack').onclick=()=>showHome();
$('profileBack').onclick=()=>showHome();
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{const n=b.dataset.nav;if(n==='home')showHome();else if(n==='mine')loadMine();else if(n==='profile')loadProfile();else if(n==='add'){toast('Размещение откроется через бота')}else if(n==='favorites')loadFavorites()});
applyLang();load(true);</script></body></html>'''




@app.get("/app")
def mini_app():
    return Response(MINI_APP_HTML, mimetype="text/html")


@app.get("/api/config")
def mini_app_config():
    return jsonify({"city":"batumi","per_page":MINI_APP_PER_PAGE,"app_url":MINI_APP_URL})


def _mini_app_authenticated_user():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        payload = request.get_json(silent=True) or {}
        init_data = str(payload.get("init_data", ""))
    return validate_telegram_init_data(init_data)


@app.post("/api/auth")
def mini_app_auth():
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"ok":False,"error":"invalid_init_data"}), 401
    if supabase_enabled():
        _supabase_user_id(user.get("id"))
    return jsonify({"ok":True,"user":user})


@app.get("/api/me")
def mini_app_me():
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if supabase_enabled():
        _supabase_user_id(user.get("id"))
    return jsonify(user)


def _mini_app_owned_row(listing_id):
    """Возвращает объявление, если оно принадлежит текущему Telegram-пользователю."""
    if not supabase_enabled():
        return None, None, None
    user = _mini_app_authenticated_user()
    if not user:
        return None, None, None
    user_id = _supabase_user_id(user.get("id"))
    if not user_id:
        return None, user, None
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select":"id,user_id,title,description,price,currency,address,metadata,channel_url,channel_message_id,created_at,category_id,city_id,status,phone,whatsapp,telegram",
            "id":f"eq.{int(listing_id)}",
            "user_id":f"eq.{user_id}",
            "limit":"1",
        },
    )
    return (rows[0] if rows else None), user, user_id


def _delete_listing_channel_messages(row):
    """Удаляет связанные сообщения объявления из канала, насколько их IDs сохранены."""
    ids = []
    metadata = row.get("metadata") or {}
    if isinstance(metadata, dict):
        raw = metadata.get("channel_message_ids") or []
        if isinstance(raw, list):
            ids.extend(raw)
    if row.get("channel_message_id"):
        ids.append(row.get("channel_message_id"))
    seen = set()
    for message_id in ids:
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            continue
        if message_id in seen:
            continue
        seen.add(message_id)
        if CHANNEL_USERNAME:
            api("deleteMessage", {"chat_id": CHANNEL_USERNAME, "message_id": message_id})


def _sync_edited_listing_to_channel(row, data):
    """Обновляет текст опубликованного объявления в Telegram, когда это возможно."""
    if not CHANNEL_USERNAME or not row.get("channel_message_id"):
        return
    try:
        text_value = build_listing(data)
        photos = data.get("photos") or []
        if photos:
            api("editMessageCaption", {
                "chat_id": CHANNEL_USERNAME,
                "message_id": row.get("channel_message_id"),
                "caption": text_value,
                "parse_mode": "HTML",
            })
        else:
            api("editMessageText", {
                "chat_id": CHANNEL_USERNAME,
                "message_id": row.get("channel_message_id"),
                "text": text_value,
                "parse_mode": "HTML",
            })
    except Exception as error:
        print("MINI APP CHANNEL SYNC ERROR:", repr(error))


@app.get("/api/my-listing/<int:listing_id>")
def mini_app_my_listing(listing_id):
    row, user, user_id = _mini_app_owned_row(listing_id)
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if not row:
        return jsonify({"error":"not_found"}), 404
    item = _attach_catalog_photos([row])[0]
    item["title"] = row.get("title") or item.get("title") or listing_title(item)
    item["description"] = row.get("description") or item.get("description") or ""
    item["price"] = str(row.get("price")) if row.get("price") is not None else item.get("price", "")
    item["currency"] = row.get("currency") or item.get("currency", "")
    item["address"] = row.get("address") or item.get("district", "")
    item["phone"] = row.get("phone") or ""
    # WhatsApp/Telegram в Supabase могут быть boolean-полями.
    # Для Mini App берём пользовательские контакты из metadata,
    # где они хранятся как строки.
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    item["whatsapp"] = row_metadata.get("whatsapp", "")
    item["telegram"] = row_metadata.get("telegram", "")
    item["status"] = row.get("status") or "draft"
    item["category_name"] = item.get("category") or "Объявление"
    return jsonify(item)


@app.patch("/api/my-listing/<int:listing_id>")
def mini_app_update_listing(listing_id):
    row, user, user_id = _mini_app_owned_row(listing_id)
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if not row:
        return jsonify({"error":"not_found"}), 404
    payload = request.get_json(silent=True) or {}
    description = str(payload.get("description", "")).strip()[:5000]
    address = str(payload.get("address", "")).strip()[:300]
    phone = str(payload.get("phone", "")).strip()[:80]
    whatsapp = str(payload.get("whatsapp", "")).strip()[:80]
    telegram = str(payload.get("telegram", "")).strip()[:80]
    currency = str(payload.get("currency", row.get("currency") or "USD")).upper().strip()
    if currency not in {"USD", "GEL", "EUR"}:
        return jsonify({"error":"invalid_currency"}), 400
    raw_price = str(payload.get("price", "")).strip().replace(",", ".")
    price = None
    if raw_price:
        try:
            price = float(raw_price)
        except ValueError:
            return jsonify({"error":"invalid_price"}), 400
        if price < 0 or price > 100000000:
            return jsonify({"error":"invalid_price"}), 400

    metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
    metadata["description"] = description
    metadata["district"] = address
    metadata["contact"] = phone
    metadata["whatsapp"] = whatsapp
    metadata["telegram"] = telegram
    metadata["currency"] = currency
    metadata["price"] = raw_price
    metadata.pop("_telegram_id", None)

    # Владелец уже проверен выше в _mini_app_owned_row().
    # Поэтому при самом UPDATE не дублируем фильтр user_id: это
    # делает запрос устойчивее к типам/ограничениям PostgREST.
    update_payload = {
        "description": description,
        "price": price,
        "currency": currency,
        "phone": phone,
        "address": address,
        "metadata": metadata,
    }

    updated = supabase_request(
        "PATCH",
        "listings",
        params={"id":f"eq.{listing_id}"},
        payload=update_payload,
    )

    # Если старые данные metadata мешают PATCH, повторяем только
    # по основным колонкам. Само объявление при этом всё равно
    # обновляется, а расширенные данные останутся в текущем metadata.
    if updated is None:
        core_payload = dict(update_payload)
        core_payload.pop("metadata", None)
        updated = supabase_request(
            "PATCH",
            "listings",
            params={"id":f"eq.{listing_id}"},
            payload=core_payload,
        )

    if updated is None:
        # Секрет Supabase сюда никогда не попадает; возвращаем только статус/текст ошибки.
        return jsonify({
            "error":"db_update_failed",
            "detail": LAST_SUPABASE_ERROR or "Supabase отклонил UPDATE",
        }), 503

    # metadata обновляем отдельным запросом только если основной
    # UPDATE прошёл без него. Это защищает редактирование от проблем
    # с JSONB/старыми metadata в конкретной записи.
    if "metadata" not in (updated[0] if isinstance(updated, list) and updated else {}):
        metadata_result = supabase_request(
            "PATCH",
            "listings",
            params={"id":f"eq.{listing_id}"},
            payload={"metadata": metadata},
        )
        if metadata_result is None:
            print("MINI APP: metadata update skipped after successful core update")

    # Синхронизируем уже опубликованный текст с каналом.
    if str(row.get("status") or "").lower() == "published":
        edited = dict(metadata)
        edited["category_key"] = edited.get("category_key") or ""
        edited["details"] = dict(edited.get("details") or {})
        edited["photos"] = list(edited.get("photos") or [])
        edited["category"] = edited.get("category") or "Объявление"
        edited["type"] = edited.get("type") or ""
        edited["subcategory"] = edited.get("subcategory") or ""
        edited["description"] = description
        edited["district"] = address
        edited["contact"] = phone
        edited["whatsapp"] = whatsapp
        edited["telegram"] = telegram
        edited["currency"] = currency
        edited["price"] = raw_price
        _sync_edited_listing_to_channel(row, edited)

    return jsonify({"ok":True})


@app.post("/api/my-listing/<int:listing_id>/unpublish")
def mini_app_unpublish_listing(listing_id):
    row, user, user_id = _mini_app_owned_row(listing_id)
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if not row:
        return jsonify({"error":"not_found"}), 404
    status = str(row.get("status") or "").lower()
    if status != "published":
        return jsonify({"ok":True})
    _delete_listing_channel_messages(row)
    updated = supabase_request(
        "PATCH",
        "listings",
        params={"id":f"eq.{listing_id}","user_id":f"eq.{user_id}"},
        payload={"status":"archived","channel_url":"","channel_message_id":None},
    )
    if updated is None:
        return jsonify({"error":"db_update_failed"}), 503
    return jsonify({"ok":True})


@app.post("/api/my-listing/<int:listing_id>/republish")
def mini_app_republish_listing(listing_id):
    """Возвращает снятое объявление в канал и каталог без создания дубля в БД."""
    row, user, user_id = _mini_app_owned_row(listing_id)
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if not row:
        return jsonify({"error":"not_found"}), 404
    status = str(row.get("status") or "").lower()
    if status == "published":
        return jsonify({"ok":True})
    if status != "archived":
        return jsonify({"error":"not_republishable"}), 400

    metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
    data = dict(metadata)
    data["id"] = row.get("id")
    data["description"] = row.get("description") or metadata.get("description") or ""
    data["price"] = str(row.get("price")) if row.get("price") is not None else metadata.get("price", "")
    data["currency"] = row.get("currency") or metadata.get("currency") or "USD"
    data["district"] = row.get("address") or metadata.get("district") or ""
    data["contact"] = row.get("phone") or metadata.get("contact") or ""
    data["whatsapp"] = metadata.get("whatsapp", "")
    data["telegram"] = metadata.get("telegram", "")

    photo_rows = supabase_request(
        "GET", "listing_photos",
        params={"select":"photo_url,sort_order", "listing_id":f"eq.{int(listing_id)}", "order":"sort_order.asc"},
    ) or []
    data["photos"] = [str(x.get("photo_url")) for x in photo_rows if x.get("photo_url")]

    result = publish_listing(data)
    if not result or not result.get("ok"):
        print("MINI APP REPUBLISH ERROR:", result)
        return jsonify({"error":"channel_publish_failed"}), 503

    tg_result = result.get("result") if isinstance(result, dict) else None
    message_id = None
    if isinstance(tg_result, list) and tg_result:
        message_id = tg_result[0].get("message_id")
    elif isinstance(tg_result, dict):
        message_id = tg_result.get("message_id")

    channel_url = ""
    if CHANNEL_USERNAME.startswith("@") and message_id:
        channel_url = f"https://t.me/{CHANNEL_USERNAME[1:]}/{message_id}"

    updated = supabase_request(
        "PATCH", "listings",
        params={"id":f"eq.{int(listing_id)}", "user_id":f"eq.{user_id}"},
        payload={"status":"published", "channel_url":channel_url, "channel_message_id":message_id},
    )
    if updated is None:
        # Не оставляем новое сообщение в канале без связи с объявлением.
        if message_id and CHANNEL_USERNAME:
            try:
                api("deleteMessage", {"chat_id":CHANNEL_USERNAME, "message_id":message_id})
            except Exception:
                pass
        return jsonify({"error":"db_update_failed"}), 503

    load_supabase_published_listings()
    return jsonify({"ok":True, "channel_url":channel_url})


@app.delete("/api/my-listing/<int:listing_id>")
def mini_app_delete_listing(listing_id):
    row, user, user_id = _mini_app_owned_row(listing_id)
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    if not row:
        return jsonify({"error":"not_found"}), 404
    _delete_listing_channel_messages(row)
    photos_deleted = supabase_request(
        "DELETE",
        "listing_photos",
        params={"listing_id":f"eq.{listing_id}"},
    )
    deleted = supabase_request(
        "DELETE",
        "listings",
        params={"id":f"eq.{listing_id}","user_id":f"eq.{user_id}"},
    )
    if deleted is None:
        return jsonify({"error":"db_delete_failed"}), 503
    return jsonify({"ok":True})


@app.get("/api/favorites")
def mini_app_favorites():
    if not supabase_enabled():
        return jsonify({"items":[]})
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    user_id = _supabase_user_id(user.get("id"))
    if not user_id:
        return jsonify({"items":[]})
    fav_rows = supabase_request(
        "GET",
        "favorites",
        params={
            "select":"listing_id,created_at",
            "user_id":f"eq.{user_id}",
            "order":"created_at.desc",
            "limit":"200",
        },
    )
    if fav_rows is None:
        return jsonify({"items":[],"error":"db_unavailable"}), 503
    ids=[int(row["listing_id"]) for row in fav_rows if str(row.get("listing_id","")).isdigit()]
    if not ids:
        return jsonify({"items":[]})
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select":"id,title,description,price,currency,address,metadata,channel_url,created_at,category_id,city_id,status",
            "id":f"in.({','.join(map(str,ids))})",
            "status":"eq.published",
            "limit":"200",
        },
    )
    if rows is None:
        return jsonify({"items":[],"error":"db_unavailable"}), 503
    by_id={int(row["id"]):row for row in rows if str(row.get("id","")).isdigit()}
    ordered=[by_id[i] for i in ids if i in by_id]
    items=_attach_catalog_photos(ordered)
    for idx,item in enumerate(items):
        row=ordered[idx]
        item["title"]=row.get("title") or item.get("title") or listing_title(item)
        item["description"]=row.get("description") or item.get("description") or ""
        item["price"]=str(row.get("price")) if row.get("price") is not None else item.get("price","")
        item["currency"]=row.get("currency") or item.get("currency","")
        item["address"]=row.get("address") or item.get("district","")
        item["category_name"]=item.get("category") or "Объявление"
        item["channel_post_url"]=row.get("channel_url") or ""
        item["is_favorite"]=True
    return jsonify({"items":items})


@app.post("/api/favorite/<int:listing_id>")
def mini_app_add_favorite(listing_id):
    if not supabase_enabled():
        return jsonify({"error":"db_unavailable"}), 503
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    user_id = _supabase_user_id(user.get("id"))
    if not user_id:
        return jsonify({"error":"user_not_found"}), 400
    published = supabase_request(
        "GET","listings",
        params={"select":"id","id":f"eq.{listing_id}","status":"eq.published","limit":"1"},
    )
    if not published:
        return jsonify({"error":"listing_not_found"}), 404
    existing = supabase_request(
        "GET","favorites",
        params={"select":"id","user_id":f"eq.{user_id}","listing_id":f"eq.{listing_id}","limit":"1"},
    )
    if existing:
        return jsonify({"ok":True,"favorite":True})
    created = supabase_request(
        "POST","favorites",
        payload={"user_id":user_id,"listing_id":listing_id},
    )
    if created is None:
        return jsonify({"error":"db_insert_failed"}), 503
    return jsonify({"ok":True,"favorite":True})


@app.delete("/api/favorite/<int:listing_id>")
def mini_app_remove_favorite(listing_id):
    if not supabase_enabled():
        return jsonify({"error":"db_unavailable"}), 503
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    user_id = _supabase_user_id(user.get("id"))
    if not user_id:
        return jsonify({"error":"user_not_found"}), 400
    deleted = supabase_request(
        "DELETE","favorites",
        params={"user_id":f"eq.{user_id}","listing_id":f"eq.{listing_id}"},
    )
    if deleted is None:
        return jsonify({"error":"db_delete_failed"}), 503
    return jsonify({"ok":True,"favorite":False})


@app.get("/api/my-listings")
def mini_app_my_listings():
    if not supabase_enabled():
        return jsonify({"items":[]})
    user = _mini_app_authenticated_user()
    if not user:
        return jsonify({"error":"invalid_init_data"}), 401
    user_id = _supabase_user_id(user.get("id"))
    if not user_id:
        return jsonify({"items":[]})
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select":"id,title,description,price,currency,address,metadata,channel_url,channel_message_id,created_at,category_id,city_id,status,user_id,phone,whatsapp,telegram",
            "user_id":f"eq.{user_id}",
            "order":"created_at.desc",
            "limit":"100",
        },
    )
    if rows is None:
        return jsonify({"items":[],"error":"db_unavailable"}), 503
    items = _attach_catalog_photos(rows)
    for idx,item in enumerate(items):
        row=rows[idx]
        item["title"] = row.get("title") or item.get("title") or listing_title(item)
        item["description"] = row.get("description") or item.get("description") or ""
        item["price"] = str(row.get("price")) if row.get("price") is not None else item.get("price","")
        item["currency"] = row.get("currency") or item.get("currency","")
        item["address"] = row.get("address") or item.get("district","")
        item["status"] = row.get("status") or item.get("status") or "published"
        item["category_name"] = item.get("category") or "Объявление"
        item["channel_post_url"] = row.get("channel_url") or item.get("channel_post_url") or ""
    return jsonify({"items":items})


@app.get("/api/auto-options")
def mini_app_auto_options():
    """Возвращает марки и модели для фильтра Авто. Марки берутся из опубликованных
    объявлений; если объявлений пока нет, показываем базовый список популярных марок,
    чтобы пользователь мог выбрать марку уже сейчас. Модели остаются привязанными
    к реально найденным объявлениям.
    """
    city = str(request.args.get("city", DEFAULT_CITY_SLUG)).strip().lower()
    if city not in {"batumi", "tbilisi"}:
        city = DEFAULT_CITY_SLUG

    # Большой базовый каталог марок и популярных моделей.
    # Важно: это именно fallback-каталог — объявления пользователей дополняют его,
    # а не заменяют его. Поэтому даже при одном объявлении Audi пользователь всё равно
    # видит весь список марок и основные модели.
    fallback_data = {
        "AC": ["Ace", "Cobra", "378 GT Zagato"],
        "Acura": ["ILX", "Integra", "TL", "TLX", "TSX", "RL", "RLX", "MDX", "RDX", "NSX", "ZDX"],
        "Alfa Romeo": ["147", "156", "159", "166", "4C", "Giulia", "Giulietta", "Stelvio", "Tonale", "MiTo"],
        "Aston Martin": ["DB7", "DB9", "DB11", "DB12", "V8 Vantage", "V12 Vantage", "Vanquish", "Valkyrie", "DBX"],
        "Audi": ["A1", "A3", "A4", "A5", "A6", "A7", "A8", "Q2", "Q3", "Q4", "Q5", "Q7", "Q8", "TT", "R8", "e-tron", "Q8 e-tron"],
        "BAIC": ["X3", "X5", "X7", "BJ40", "BJ60", "U5", "EU5"],
        "Bentley": ["Bentayga", "Continental GT", "Continental Flying Spur", "Flying Spur", "Mulsanne", "Arnage"],
        "BMW": ["1 Series", "2 Series", "3 Series", "4 Series", "5 Series", "6 Series", "7 Series", "8 Series", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "Z3", "Z4", "i3", "i4", "i5", "i7", "iX", "iX1", "iX3", "M2", "M3", "M4", "M5", "M8"],
        "BYD": ["Atto 3", "Dolphin", "D1", "E6", "F3", "F5", "F6", "Han", "Seal", "Seagull", "Song", "Tang", "Yuan Plus"],
        "Cadillac": ["ATS", "CT4", "CT5", "CT6", "CTS", "DeVille", "Escalade", "SRX", "XT4", "XT5", "XT6", "XTS"],
        "Changan": ["Alsvin", "CS35", "CS55", "CS75", "CS85", "CS95", "UNI-T", "UNI-K", "UNI-V", "Hunter"],
        "Chery": ["Arrizo 5", "Arrizo 8", "Tiggo 2", "Tiggo 4", "Tiggo 7", "Tiggo 8", "Tiggo 9", "Omoda 5", "Omoda C5"],
        "Chevrolet": ["Aveo", "Blazer", "Camaro", "Captiva", "Cobalt", "Cruze", "Equinox", "Impala", "Malibu", "Niva", "Silverado", "Spark", "Suburban", "Tahoe", "Trailblazer", "Traverse", "Trax", "Volt"],
        "Chrysler": ["200", "300", "300C", "Pacifica", "PT Cruiser", "Sebring", "Town & Country", "Voyager"],
        "Citroen": ["C1", "C3", "C4", "C5", "C5 Aircross", "Berlingo", "Jumper", "Jumpy", "DS3", "DS4", "DS7"],
        "Dacia": ["Dokker", "Duster", "Jogger", "Lodgy", "Logan", "Sandero", "Spring"],
        "Daewoo": ["Damas", "Gentra", "Lanos", "Leganza", "Matiz", "Nexia", "Nubira", "Tico"],
        "Daihatsu": ["Boon", "Charade", "Copen", "Cuore", "Mira", "Rocky", "Sirion", "Terios", "YRV"],
        "Dodge": ["Avenger", "Challenger", "Charger", "Dart", "Durango", "Grand Caravan", "Journey", "Nitro", "Ram", "Viper"],
        "DongFeng": ["AX7", "DF6", "H30 Cross", "Rich", "T5 EVO", "T5", "T7"],
        "DS": ["DS 3", "DS 4", "DS 5", "DS 7", "DS 9"],
        "FAW": ["Bestune B70", "Bestune T55", "Bestune T77", "Bestune T99", "X40", "X80"],
        "Ferrari": ["458 Italia", "488 GTB", "812 Superfast", "California", "F8 Tributo", "GTC4Lusso", "Portofino", "Roma", "SF90 Stradale", "296 GTB"],
        "Fiat": ["500", "500X", "500L", "Albea", "Bravo", "Doblo", "Ducato", "Fiorino", "Linea", "Panda", "Punto", "Tipo"],
        "Ford": ["Bronco", "EcoSport", "Edge", "Escape", "Expedition", "Explorer", "F-150", "Fiesta", "Focus", "Fusion", "Kuga", "Maverick", "Mondeo", "Mustang", "Ranger", "Transit"],
        "GAC": ["GS3", "GS4", "GS5", "GS8", "Empow", "Aion S", "Aion Y", "Aion V"],
        "Geely": ["Atlas", "Coolray", "Emgrand", "Geometry C", "Monjaro", "Okavango", "Tugella", "Starray"],
        "Genesis": ["G70", "G80", "G90", "GV60", "GV70", "GV80"],
        "GMC": ["Acadia", "Canyon", "Hummer EV", "Sierra", "Terrain", "Yukon"],
        "Great Wall": ["C30", "C50", "Hover H3", "Hover H5", "M4", "Poer", "Wingle 5", "Wingle 7"],
        "Haval": ["F7", "F7x", "H2", "H5", "H6", "H9", "Jolion", "Dargo"],
        "Honda": ["Accord", "Civic", "CR-V", "CR-Z", "Fit", "HR-V", "Insight", "Jazz", "Odyssey", "Pilot", "Ridgeline"],
        "Hongqi": ["H5", "H6", "H9", "HS5", "HS7", "E-HS9"],
        "Hyundai": ["Accent", "Avante", "Creta", "Elantra", "Getz", "Ioniq", "Ioniq 5", "Kona", "Palisade", "Santa Fe", "Sonata", "Staria", "Tucson", "Veloster"],
        "Infiniti": ["EX", "FX", "G25", "G35", "G37", "JX", "Q30", "Q50", "Q60", "QX30", "QX50", "QX60", "QX70", "QX80"],
        "Isuzu": ["D-Max", "MU-X", "Trooper", "Rodeo", "NPR"],
        "JAC": ["J5", "J7", "JS3", "JS4", "JS6", "S3", "S5", "T6", "T8"],
        "Jaguar": ["E-Pace", "F-Pace", "F-Type", "I-Pace", "XE", "XF", "XJ"],
        "Jeep": ["Avenger", "Cherokee", "Compass", "Gladiator", "Grand Cherokee", "Renegade", "Wrangler"],
        "Kia": ["Carens", "Carnival", "Ceed", "Cerato", "EV6", "Forte", "K3", "K5", "K8", "K9", "Mohave", "Niro", "Optima", "Picanto", "Rio", "Sorento", "Soul", "Sportage", "Stinger", "Stonic", "Telluride"],
        "Lada": ["Granta", "Kalina", "Largus", "Niva", "Niva Travel", "Priora", "Vesta", "XRAY"],
        "Lamborghini": ["Aventador", "Gallardo", "Huracan", "Revuelto", "Urus"],
        "Land Rover": ["Defender", "Discovery", "Discovery Sport", "Freelander", "Range Rover", "Range Rover Evoque", "Range Rover Sport", "Range Rover Velar"],
        "Lexus": ["CT", "ES", "GS", "GX", "IS", "LC", "LS", "LX", "NX", "RC", "RX", "RZ", "UX"],
        "Lincoln": ["Aviator", "Continental", "Corsair", "MKC", "MKS", "MKX", "MKZ", "Nautilus", "Navigator", "Town Car"],
        "Li Auto": ["L6", "L7", "L8", "L9", "Mega"],
        "Lotus": ["Elise", "Emira", "Evora", "Exige", "Eletre"],
        "Lucid": ["Air", "Gravity"],
        "Lynk & Co": ["01", "02", "03", "05", "06", "09"],
        "Mahindra": ["Scorpio", "Thar", "XUV300", "XUV500", "XUV700"],
        "Maserati": ["Ghibli", "GranTurismo", "Grecale", "Levante", "MC20", "Quattroporte"],
        "Mazda": ["2", "3", "6", "CX-3", "CX-30", "CX-5", "CX-7", "CX-9", "CX-60", "CX-90", "MX-5"],
        "McLaren": ["570S", "600LT", "650S", "720S", "765LT", "Artura", "GT", "P1"],
        "Mercedes-Benz": ["A-Class", "B-Class", "C-Class", "CLA", "CLS", "E-Class", "EQA", "EQB", "EQC", "EQE", "EQS", "G-Class", "GLA", "GLB", "GLC", "GLE", "GLS", "S-Class", "SL", "Sprinter", "V-Class"],
        "MG": ["3", "4", "5", "6", "ZS", "HS", "Marvel R", "Cyberster"],
        "MINI": ["Cooper", "Clubman", "Countryman", "Paceman", "Hatch", "Convertible"],
        "Mitsubishi": ["ASX", "Eclipse Cross", "Galant", "Lancer", "Outlander", "Pajero", "Pajero Sport", "L200"],
        "NIO": ["ET5", "ET7", "EL6", "EL7", "EL8", "ES6", "ES7", "ES8"],
        "Nissan": ["350Z", "370Z", "Altima", "Juke", "Leaf", "Micra", "Murano", "Navara", "Note", "Pathfinder", "Qashqai", "Rogue", "Sentra", "Serena", "Skyline", "X-Trail"],
        "Opel": ["Adam", "Astra", "Corsa", "Crossland", "Grandland", "Insignia", "Mokka", "Omega", "Vectra", "Zafira"],
        "Peugeot": ["108", "208", "2008", "308", "3008", "408", "508", "5008", "Partner", "Rifter", "Traveller"],
        "Polestar": ["1", "2", "3", "4"],
        "Porsche": ["718 Boxster", "718 Cayman", "911", "Cayenne", "Macan", "Panamera", "Taycan"],
        "RAM": ["1500", "2500", "3500", "ProMaster"],
        "Renault": ["Arkana", "Captur", "Clio", "Duster", "Fluence", "Kadjar", "Koleos", "Megane", "Sandero", "Scenic", "Symbol", "Talisman", "Zoe"],
        "Rivian": ["R1T", "R1S", "R2"],
        "Rolls-Royce": ["Cullinan", "Dawn", "Ghost", "Phantom", "Spectre", "Wraith"],
        "Seat": ["Alhambra", "Arona", "Ateca", "Ibiza", "Leon", "Tarraco", "Toledo"],
        "Skoda": ["Fabia", "Kamiq", "Karoq", "Kodiaq", "Octavia", "Rapid", "Scala", "Superb", "Yeti"],
        "Smart": ["ForTwo", "ForFour", "#1", "#3"],
        "SsangYong": ["Actyon", "Korando", "Musso", "Rexton", "Tivoli", "Torres"],
        "Subaru": ["BRZ", "Forester", "Impreza", "Legacy", "Levorg", "Outback", "WRX", "XV"],
        "Suzuki": ["Alto", "Baleno", "Celerio", "Grand Vitara", "Ignis", "Jimny", "S-Cross", "Swift", "Vitara"],
        "Tesla": ["Model 3", "Model S", "Model X", "Model Y", "Cybertruck"],
        "Toyota": ["4Runner", "Alphard", "Aqua", "Avalon", "Avensis", "C-HR", "Camry", "Corolla", "Crown", "FJ Cruiser", "Fortuner", "GR86", "Hiace", "Highlander", "Hilux", "Land Cruiser", "Land Cruiser Prado", "Prius", "RAV4", "Sequoia", "Sienna", "Supra", "Tacoma", "Tundra", "Venza", "Yaris"],
        "UAZ": ["469", "Hunter", "Patriot", "Pickup", "Profi"],
        "Volkswagen": ["Amarok", "Arteon", "Caddy", "Golf", "Jetta", "Passat", "Polo", "T-Cross", "T-Roc", "Taigo", "Tiguan", "Touareg", "Transporter", "ID.3", "ID.4", "ID.5", "ID.7"],
        "Volvo": ["C30", "C40", "S40", "S60", "S80", "S90", "V40", "V60", "V70", "V90", "XC40", "XC60", "XC70", "XC90"],
        "XPeng": ["P5", "P7", "G3", "G6", "G9", "X9"],
        "Zeekr": ["001", "007", "009", "X", "7X"],
        "Zotye": ["T200", "T600", "T700", "SR7", "Coupa"],
        "Москвич": ["3", "3e", "6", "8"],
        "ВАЗ": ["2107", "2110", "2114", "2115", "Niva", "Vesta", "Granta", "Priora"]
    }
    fallback_makes = sorted(fallback_data.keys(), key=str.casefold)
    fallback_models = {k.casefold(): v for k, v in fallback_data.items()}

    if not supabase_enabled():
        return jsonify({"makes": fallback_makes, "models": []})

    city_id = _supabase_city_id(city)
    category_id = _supabase_category_id("auto")
    if not city_id or not category_id:
        return jsonify({"makes": fallback_makes, "models": []})

    make_filter = str(request.args.get("make", "")).strip().casefold()
    rows = supabase_request(
        "GET",
        "listings",
        params={
            "select":"metadata,title",
            "status":"eq.published",
            "city_id":f"eq.{city_id}",
            "category_id":f"eq.{category_id}",
            "limit":"1000",
        },
    )
    if rows is None:
        return jsonify({"makes": fallback_makes, "models": [], "error":"db_unavailable"}), 503

    pairs = set()
    for row in rows:
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        details = meta.get("details") if isinstance(meta.get("details"), dict) else {}

        make = str(details.get("make") or "").strip()
        model = str(details.get("model") or "").strip()
        combined = str(details.get("make_model") or "").strip()

        if (not make or not model) and combined:
            # Поддержка старого формата "Toyota Camry".
            parts = combined.split()
            if not make and parts:
                make = parts[0]
            if not model and len(parts) > 1:
                model = " ".join(parts[1:])

        if not make:
            title = str(row.get("title") or "").strip()
            if title:
                clean = title.split("·", 1)[0].strip()
                parts = clean.split()
                if parts:
                    make = parts[0]
                    model = " ".join(parts[1:]) if len(parts) > 1 else model

        if make:
            pairs.add((make, model))

    db_makes = {make for make, _ in pairs if make}
    # База объявлений только дополняет каталог. Она не должна скрывать марки,
    # которые ещё никто не разместил.
    makes = sorted(set(fallback_makes) | db_makes, key=str.casefold)

    db_models = {
        model for make, model in pairs
        if make_filter and make.casefold() == make_filter and model
    }
    base_models = set(fallback_models.get(make_filter, [])) if make_filter else set()
    models = sorted(base_models | db_models, key=str.casefold)
    return jsonify({"makes": makes, "models": models})


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

    category_id = None
    if category:
        category_id = _supabase_category_id(category)
        if not category_id:
            return jsonify({"items":[],"has_next":False,"total_hint":0})

    params = {
        "select":"id,title,description,price,currency,address,metadata,channel_url,created_at,category_id,city_id,status",
        "status":"eq.published",
        "city_id":f"eq.{city_id}",
        "order":"created_at.desc",
    }
    if category_id:
        params["category_id"] = f"eq.{category_id}"

    if q:
        safe_q = q.replace("*", "").replace(",", " ").strip()
        if safe_q:
            params["or"] = f"(title.ilike.*{safe_q}*,description.ilike.*{safe_q}*,address.ilike.*{safe_q}*)"

    sort_mode = str(request.args.get("sort", "new")).strip().lower()
    if sort_mode not in {"new", "price_asc", "price_desc"}:
        sort_mode = "new"
    params["order"] = "created_at.desc" if sort_mode == "new" else ("price.asc.nullslast,created_at.desc" if sort_mode == "price_asc" else "price.desc.nullslast,created_at.desc")

    # Фильтры недвижимости применяем в Python после получения ограниченного
    # набора кандидатов. Это специально сделано так, чтобы не зависеть от
    # типов колонок/JSON-path синтаксиса PostgREST и не получать HTTP 400.
    # Для площади и 4+ комнат нужен числовой разбор JSON metadata.
    # Берём ограниченный набор кандидатов, уже отфильтрованный Supabase
    # по городу/категории/цене/типу/району.
    has_python_filters = category == "realestate" and any(
        str(request.args.get(name, "")).strip()
        for name in ("deal", "sub", "min_price", "max_price", "rooms", "min_area", "max_area", "district")
    )
    has_auto_filters = category == "auto" and any(
        str(request.args.get(name, "")).strip()
        for name in ("deal", "min_price", "max_price", "make", "model", "min_year", "max_year", "min_mileage", "max_mileage")
    )

    if has_python_filters:
        candidate_limit = min(1000, max(100, (page + 1) * per_page * 8 + 1))
        params["limit"] = str(candidate_limit)
        rows = supabase_request("GET", "listings", params=params)
        if rows is None:
            return jsonify({"items":[],"has_next":False,"total_hint":0,"error":"db_unavailable"}), 503

        min_area = request.args.get("min_area", "").strip()
        max_area = request.args.get("max_area", "").strip()
        try: min_area_n = float(min_area) if min_area else None
        except ValueError: min_area_n = None
        try: max_area_n = float(max_area) if max_area else None
        except ValueError: max_area_n = None

        filtered = []
        selected_deal = str(request.args.get("deal", "")).strip().lower()
        selected_sub = str(request.args.get("sub", "")).strip().lower()
        selected_rooms = str(request.args.get("rooms", "")).strip()
        for row in rows:
            meta = row.get("metadata") or {}
            meta = meta if isinstance(meta, dict) else {}
            details = meta.get("details") if isinstance(meta.get("details"), dict) else {}

            # Цена
            price_value = row.get("price")
            try:
                price_n = float(str(price_value).replace(",", ".").strip())
            except (TypeError, ValueError):
                price_n = None
            min_price_raw = str(request.args.get("min_price", "")).strip()
            max_price_raw = str(request.args.get("max_price", "")).strip()
            try: min_price_n = float(min_price_raw) if min_price_raw else None
            except ValueError: min_price_n = None
            try: max_price_n = float(max_price_raw) if max_price_raw else None
            except ValueError: max_price_n = None
            if min_price_n is not None and (price_n is None or price_n < min_price_n):
                continue
            if max_price_n is not None and (price_n is None or price_n > max_price_n):
                continue

            # Район / адрес
            selected_district = str(request.args.get("district", "")).strip().lower()
            if selected_district:
                address_text = str(row.get("address", "") or "").lower()
                if selected_district not in address_text:
                    continue

            if selected_deal and selected_deal in {"rent", "seek", "sell", "buy"}:
                if str(meta.get("type_key", "")).strip().lower() != selected_deal:
                    continue
            if selected_sub and selected_sub in {"apartment", "house", "room", "commercial", "land", "garage"}:
                if str(meta.get("subcategory_key", "")).strip().lower() != selected_sub:
                    continue

            if selected_rooms in {"1", "2", "3"}:
                try:
                    if float(str(details.get("rooms", "")).replace(",", ".")) != float(selected_rooms):
                        continue
                except (TypeError, ValueError):
                    continue
            elif selected_rooms == "4":
                try:
                    if float(str(details.get("rooms", "")).replace(",", ".")) < 4:
                        continue
                except (TypeError, ValueError):
                    continue
            if min_area_n is not None or max_area_n is not None:
                try: area_n = float(str(details.get("area", "")).replace(",", ".").replace("м²", "").replace("m²", "").strip())
                except (TypeError, ValueError):
                    continue
                if min_area_n is not None and area_n < min_area_n: continue
                if max_area_n is not None and area_n > max_area_n: continue
            filtered.append(row)
        def _sort_price(row):
            try:
                return float(str(row.get("price") or "").replace(",", ".").strip())
            except (TypeError, ValueError):
                return None
        if sort_mode == "price_asc":
            filtered.sort(key=lambda r: (_sort_price(r) is None, _sort_price(r) if _sort_price(r) is not None else 0))
        elif sort_mode == "price_desc":
            filtered.sort(key=lambda r: (_sort_price(r) is not None, _sort_price(r) if _sort_price(r) is not None else 0), reverse=True)

        offset = page * per_page
        page_rows = filtered[offset:offset + per_page]
        has_next = len(filtered) > offset + per_page
        rows = page_rows
    elif has_auto_filters:
        candidate_limit = min(1000, max(100, (page + 1) * per_page * 8 + 1))
        params["limit"] = str(candidate_limit)
        rows = supabase_request("GET", "listings", params=params)
        if rows is None:
            return jsonify({"items":[],"has_next":False,"total_hint":0,"error":"db_unavailable"}), 503

        def _num(v):
            try:
                return float(str(v).replace(",", ".").replace("km","").replace("км","").strip())
            except (TypeError, ValueError):
                return None

        selected_deal = str(request.args.get("deal", "")).strip().lower()
        selected_make = str(request.args.get("make", "")).strip().lower()
        selected_model = str(request.args.get("model", "")).strip().lower()
        min_price_n = _num(request.args.get("min_price", ""))
        max_price_n = _num(request.args.get("max_price", ""))
        min_year_n = _num(request.args.get("min_year", ""))
        max_year_n = _num(request.args.get("max_year", ""))
        min_mileage_n = _num(request.args.get("min_mileage", ""))
        max_mileage_n = _num(request.args.get("max_mileage", ""))

        filtered = []
        for row in rows:
            meta = row.get("metadata") or {}
            meta = meta if isinstance(meta, dict) else {}
            details = meta.get("details") if isinstance(meta.get("details"), dict) else {}
            if selected_deal in {"sell","buy"} and str(meta.get("type_key","")).strip().lower() != selected_deal:
                continue
            price_n = _num(row.get("price"))
            if min_price_n is not None and (price_n is None or price_n < min_price_n): continue
            if max_price_n is not None and (price_n is None or price_n > max_price_n): continue
            make_text = str(details.get("make") or details.get("make_model") or "").strip().lower()
            model_text = str(details.get("model") or details.get("make_model") or "").strip().lower()
            combined = f"{make_text} {model_text}".strip()
            if selected_make and selected_make not in combined: continue
            if selected_model and selected_model not in combined: continue
            year_n = _num(details.get("year"))
            if min_year_n is not None and (year_n is None or year_n < min_year_n): continue
            if max_year_n is not None and (year_n is None or year_n > max_year_n): continue
            mileage_n = _num(details.get("mileage"))
            if min_mileage_n is not None and (mileage_n is None or mileage_n < min_mileage_n): continue
            if max_mileage_n is not None and (mileage_n is None or mileage_n > max_mileage_n): continue
            filtered.append(row)

        if sort_mode == "price_asc":
            filtered.sort(key=lambda r: (_num(r.get("price")) is None, _num(r.get("price")) if _num(r.get("price")) is not None else 0))
        elif sort_mode == "price_desc":
            filtered.sort(key=lambda r: (_num(r.get("price")) is not None, _num(r.get("price")) if _num(r.get("price")) is not None else 0), reverse=True)

        offset = page * per_page
        page_rows = filtered[offset:offset + per_page]
        has_next = len(filtered) > offset + per_page
        rows = page_rows
    else:
        offset = page * per_page
        params["offset"] = str(offset)
        params["limit"] = str(per_page + 1)
        rows = supabase_request("GET", "listings", params=params)
        if rows is None:
            return jsonify({"items":[],"has_next":False,"total_hint":0,"error":"db_unavailable"}), 503
        has_next = len(rows) > per_page
        rows = rows[:per_page]

    favorite_ids = set()
    current_user = _mini_app_authenticated_user()
    if current_user and rows:
        current_user_id = _supabase_user_id(current_user.get("id"))
        listing_ids = [str(r.get("id")) for r in rows if r.get("id") is not None]
        if current_user_id and listing_ids:
            fav_rows = supabase_request(
                "GET",
                "favorites",
                params={
                    "select":"listing_id",
                    "user_id":f"eq.{current_user_id}",
                    "listing_id":f"in.({','.join(listing_ids)})",
                },
            ) or []
            favorite_ids = {int(r["listing_id"]) for r in fav_rows if str(r.get("listing_id","")).isdigit()}

    items = _attach_catalog_photos(rows)
    for item in items:
        item["is_favorite"] = int(item.get("id", 0)) in favorite_ids
    for idx, item in enumerate(items):
        row = rows[idx]
        item["title"] = row.get("title") or item.get("title") or listing_title(item)
        item["description"] = row.get("description") or item.get("description") or ""
        item["price"] = str(row.get("price")) if row.get("price") is not None else item.get("price", "")
        item["currency"] = row.get("currency") or item.get("currency", "")
        item["address"] = row.get("address") or item.get("district", "")
        item["category_name"] = item.get("category") or "Объявление"

    return jsonify({
        "items":items,
        "has_next":has_next,
        "total_hint":per_page * (page + 1) + (1 if has_next else 0),
    })


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
    current_user=_mini_app_authenticated_user()
    item["is_favorite"]=False
    if current_user:
        current_user_id=_supabase_user_id(current_user.get("id"))
        if current_user_id:
            fav=supabase_request("GET","favorites",params={"select":"id","user_id":f"eq.{current_user_id}","listing_id":f"eq.{listing_id}","limit":"1"}) or []
            item["is_favorite"]=bool(fav)
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
    
