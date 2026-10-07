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
  background-image:linear-gradient(90deg,rgba(5,25,58,.72) 0%,rgba(5,25,58,.38) 46%,rgba(5,25,58,.10) 100%),url("https://commons.wikimedia.org/wiki/Special:FilePath/Batumi_sunset_2.jpg?width=1600");base64,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");
  background-size:cover;
  background-position:center 54%;
  min-height:390px;
  padding:24px 18px 18px;
  border-radius:30px;
  box-shadow:0 18px 40px rgba(15,70,150,.22);
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
    
