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
</style>
</head>
<body>
<div class="wrap"><div class="top"><div class="brand">MADLOBA <span>MARKET</span></div><div style="display:flex;gap:7px;align-items:center"><select class="city" id="langSelect" aria-label="Language"><option value="ru">🇷🇺 RU</option><option value="en">🇬🇧 EN</option><option value="ka">🇬🇪 KA</option></select><button class="city" id="cityBtn">📍 <span id="cityName">Batumi</span>⌄</button></div></div><div class="hero"><h1 data-i18n="hero_title">Объявления рядом с вами</h1><p data-i18n="hero_subtitle">Покупайте, продавайте и находите нужное прямо в Telegram.</p><div class="search">🔎 <input id="search" data-i18n-placeholder="search_placeholder" placeholder="Что ищете? Например: квартира" autocomplete="off"></div><div class="quick-actions"><button class="quick-action" data-cat="realestate">🏠 <span>Недвижимость</span></button><button class="quick-action" data-cat="auto">🚗 <span>Авто</span></button><button class="quick-action" data-cat="tech">📱 <span>Техника</span></button><button class="quick-action" data-cat="work">💼 <span>Работа</span></button><button class="quick-action" data-cat="">▦ <span>Все</span></button></div><div class="filter-bar" id="filterBar"><button class="filter-btn" id="filterToggle" type="button">⚙️ <span data-i18n="filters">Фильтры</span><span id="filterActive"></span></button><div class="filter-panel" id="filterPanel"><div id="realestateFilterFields" class="filter-grid"><div class="filter-field"><label data-i18n="deal">Сделка</label><select id="filterDeal"><option value="" data-i18n="all">Все</option><option value="rent" data-i18n="rent">Сдам</option><option value="seek" data-i18n="seek">Сниму</option><option value="sell" data-i18n="sell">Продам</option><option value="buy" data-i18n="buy">Куплю</option></select></div><div class="filter-field"><label data-i18n="property_type">Тип</label><select id="filterSub"><option value="" data-i18n="all">Все</option><option value="apartment">🏢 Квартира</option><option value="house">🏡 Дом</option><option value="room">🛏 Комната</option><option value="commercial">🏬 Коммерция</option><option value="land">🌳 Земля</option><option value="garage">🚗 Гараж / парковка</option></select></div><div class="filter-field"><label data-i18n="price_from">Цена от</label><input id="filterMinPrice" inputmode="decimal" placeholder="0"></div><div class="filter-field"><label data-i18n="price_to">Цена до</label><input id="filterMaxPrice" inputmode="decimal" placeholder="∞"></div><div class="filter-field"><label data-i18n="rooms">Комнаты</label><select id="filterRooms"><option value="" data-i18n="all">Все</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4+</option></select></div><div class="filter-field"><label data-i18n="district">Район</label><input id="filterDistrict" data-i18n-placeholder="district_placeholder" placeholder="Например: Новый Бульвар"></div><div class="filter-field"><label data-i18n="area_from">Площадь от, м²</label><input id="filterMinArea" inputmode="decimal" placeholder="0"></div><div class="filter-field"><label data-i18n="area_to">Площадь до, м²</label><input id="filterMaxArea" inputmode="decimal" placeholder="∞"></div></div><div id="autoFilterFields" class="filter-grid" style="display:none">
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
    
