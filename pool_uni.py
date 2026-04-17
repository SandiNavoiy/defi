from __future__ import annotations

import json
import math
import time
from threading import Lock
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, redirect, render_template_string, request, url_for

BASE_DIR = Path(__file__).resolve().parent
FILE = BASE_DIR / "positions.json"
PRICE_TTL_SECONDS = 15


class PriceUnavailableError(RuntimeError):
    """Ошибка получения цен, когда нет даже кэшированных значений."""


def _build_http_session() -> requests.Session:
    """Создает HTTP-сессию с ретраями для временных ошибок и лимитов API."""
    retry = Retry(
        total=4,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = _build_http_session()
PRICE_CACHE: dict[str, dict[str, float]] = {}
CACHE_LOCK = Lock()

POSITION_CONFIG: dict[str, dict[str, float | str]] = {
    "eth_narrow": {"coin_id": "ethereum", "label": "ETH узкий", "plus": 25, "minus": 25, "step": 5},
    "eth_wide": {"coin_id": "ethereum", "label": "ETH широкий", "plus": 50, "minus": 30, "step": 5},
    "sol_narrow": {"coin_id": "solana", "label": "SOL узкий", "plus": 15, "minus": 15, "step": 0.5},
    "sol_wide": {"coin_id": "solana", "label": "SOL широкий", "plus": 60, "minus": 35, "step": 0.5},
}

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LP Tool</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: #fffdf8;
      --text: #1d1a16;
      --muted: #6f655a;
      --line: #d8cbbc;
      --accent: #b55d34;
      --ok: #2f7d4a;
      --warn: #b7791f;
      --bad: #b23a2f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: radial-gradient(circle at top, #fff7eb 0, var(--bg) 55%);
      color: var(--text);
    }
    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }
    h1 { margin: 0 0 8px; font-size: 40px; }
    p { color: var(--muted); }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 24px 0;
    }
    button, .link-btn {
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 10px 16px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(64, 45, 25, 0.06);
    }
    .status {
      display: inline-block;
      margin-top: 8px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 14px;
      color: white;
    }
    .in_range { background: var(--ok); }
    .above_range, .below_range { background: var(--bad); }
    dl {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 6px 10px;
      margin: 16px 0 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; text-align: right; }
    form { margin-top: 16px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>LP Tool</h1>
    <p>Отслеживает диапазоны ликвидности ETH и SOL и позволяет перезагружать позиции из браузера.</p>
    <div class="actions">
      <form method="post" action="{{ url_for('reset_all_route') }}">
        <button type="submit">Перезагрузить все позиции</button>
      </form>
      <a class="link-btn" href="{{ url_for('api_status') }}">Открыть JSON API</a>
    </div>
    <div class="cards">
      {% for item in items %}
      <section class="card">
        <h2>{{ item.label }}</h2>
        <span class="status {{ item.status_code }}">{{ item.status_label }}</span>
        <dl>
          <dt>Сейчас</dt><dd>{{ '%.2f'|format(item.current_price) }}</dd>
          <dt>Вход</dt><dd>{{ '%.2f'|format(item.entry) }}</dd>
          <dt>Диапазон</dt><dd>{{ '%.2f'|format(item.low) }} - {{ '%.2f'|format(item.high) }}</dd>
          <dt>IL</dt><dd>{{ '%.4f'|format(item.il) }}%</dd>
          <dt>Действие</dt><dd>{{ item.action }}</dd>
        </dl>
        <form method="post" action="{{ url_for('reset_position_route', key=item.key) }}">
          <button type="submit">Перезагрузить {{ item.label }}</button>
        </form>
      </section>
      {% endfor %}
    </div>
  </div>
</body>
</html>
"""


def get_price(coin_id: str) -> float:
    """Возвращает цену одной монеты в USD через общий механизм батч-запроса."""
    return get_prices([coin_id])[coin_id]


def get_prices(coin_ids: list[str], force_refresh: bool = False) -> dict[str, float]:
    """Возвращает цены монет, используя TTL-кэш и fallback на кэш при ошибке API."""
    unique_ids = list(dict.fromkeys(coin_ids))
    now = time.time()

    with CACHE_LOCK:
        has_fresh = all(
            coin_id in PRICE_CACHE and (now - PRICE_CACHE[coin_id]["timestamp"]) <= PRICE_TTL_SECONDS
            for coin_id in unique_ids
        )
        if has_fresh and not force_refresh:
            return {coin_id: PRICE_CACHE[coin_id]["price"] for coin_id in unique_ids}

        stale_prices = {
            coin_id: PRICE_CACHE[coin_id]["price"]
            for coin_id in unique_ids
            if coin_id in PRICE_CACHE
        }

    url = "https://api.coingecko.com/api/v3/simple/price"
    try:
        response = HTTP_SESSION.get(
            url,
            params={"ids": ",".join(unique_ids), "vs_currencies": "usd"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

        prices: dict[str, float] = {}
        for coin_id in unique_ids:
            prices[coin_id] = float(payload[coin_id]["usd"])

        with CACHE_LOCK:
            ts = time.time()
            for coin_id, price in prices.items():
                PRICE_CACHE[coin_id] = {"price": price, "timestamp": ts}
        return prices
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        if len(stale_prices) == len(unique_ids):
            return stale_prices
        raise PriceUnavailableError("Не удалось получить цены с CoinGecko") from exc


def calc_range(price: float, plus_pct: float, minus_pct: float) -> tuple[float, float]:
    """Считает верхнюю и нижнюю границу диапазона по процентам."""
    upper = price * (1 + plus_pct / 100)
    lower = price * (1 - minus_pct / 100)
    return lower, upper


def round_price(price: float, step: float, direction: str) -> float:
    """Округляет цену вниз или вверх с заданным шагом."""
    if direction == "down":
        return math.floor(price / step) * step
    return math.ceil(price / step) * step


def calc_il(entry_price: float, current_price: float) -> float:
    """Вычисляет Impermanent Loss в процентах для текущей цены."""
    ratio = current_price / entry_price
    return (2 * math.sqrt(ratio) / (1 + ratio) - 1) * 100


def load_positions() -> dict[str, Any]:
    """Читает сохраненные позиции из JSON-файла."""
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def save_positions(data: dict[str, Any]) -> None:
    """Сохраняет словарь позиций в JSON-файл."""
    FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def create_position(price: float, plus: float, minus: float, step: float) -> dict[str, float]:
    """Создает новую позицию с округленными границами диапазона."""
    low, high = calc_range(price, plus, minus)
    return {
        "entry": price,
        "low": round_price(low, step, "down"),
        "high": round_price(high, step, "up"),
    }


def ensure_positions(prices: dict[str, float]) -> dict[str, Any]:
    """Гарантирует, что все конфигурации позиций присутствуют в хранилище."""
    positions = load_positions()
    updated = False

    for key, config in POSITION_CONFIG.items():
        if key in positions:
            continue
        positions[key] = create_position(
            price=prices[str(config["coin_id"])],
            plus=float(config["plus"]),
            minus=float(config["minus"]),
            step=float(config["step"]),
        )
        updated = True

    if updated:
        save_positions(positions)

    return positions


def status_payload(key: str, position: dict[str, float], current_price: float) -> dict[str, Any]:
    """Формирует статус позиции: диапазон, IL и рекомендацию по действию."""
    low = float(position["low"])
    high = float(position["high"])
    entry = float(position["entry"])

    if current_price < low:
        status_code = "below_range"
        status_label = "Ниже диапазона"
        action = "Рынок упал. Стоит подумать о закрытии и переоткрытии ниже."
    elif current_price > high:
        status_code = "above_range"
        status_label = "Выше диапазона"
        action = "Рынок вырос. Позиция, вероятно, в стейблах. Перезагрузите выше."
    else:
        status_code = "in_range"
        status_label = "В диапазоне"
        action = "Ничего делать не нужно. Продолжайте фармить комиссии."

    il = calc_il(entry, current_price)
    return {
        "key": key,
        "label": str(POSITION_CONFIG[key]["label"]),
        "coin_id": str(POSITION_CONFIG[key]["coin_id"]),
        "entry": entry,
        "low": low,
        "high": high,
        "current_price": current_price,
        "status_code": status_code,
        "status_label": status_label,
        "il": il,
        "action": action,
    }


def fallback_payload(key: str, position: dict[str, float]) -> dict[str, Any]:
    """Формирует безопасный ответ, когда API цены временно недоступно."""
    entry = float(position["entry"])
    low = float(position["low"])
    high = float(position["high"])
    return {
        "key": key,
        "label": str(POSITION_CONFIG[key]["label"]),
        "coin_id": str(POSITION_CONFIG[key]["coin_id"]),
        "entry": entry,
        "low": low,
        "high": high,
        "current_price": entry,
        "status_code": "in_range",
        "status_label": "Нет данных API",
        "il": 0.0,
        "action": "CoinGecko временно недоступен. Показаны сохраненные значения позиции.",
    }


def fetch_snapshot_fallback() -> dict[str, Any]:
    """Собирает снимок из локальных данных без обращения к внешнему API."""
    positions = load_positions()
    items = []
    for key in POSITION_CONFIG:
        position = positions.get(key)
        if not position:
            continue
        items.append(fallback_payload(key, position))
    return {"prices": {}, "items": items}


def fetch_snapshot() -> dict[str, Any]:
    """Собирает актуальный снимок по всем позициям для UI и API."""
    prices = get_prices(["ethereum", "solana"])
    positions = ensure_positions(prices)

    items = [
        status_payload(key, positions[key], prices[str(config["coin_id"])])
        for key, config in POSITION_CONFIG.items()
    ]
    return {"prices": prices, "items": items}


def reset_position_by_key(key: str) -> dict[str, Any]:
    """Пересоздает одну позицию по текущей цене выбранного актива."""
    if key not in POSITION_CONFIG:
        raise KeyError(key)

    config = POSITION_CONFIG[key]
    current_price = get_prices([str(config["coin_id"])], force_refresh=True)[str(config["coin_id"])]
    positions = load_positions()
    positions[key] = create_position(
        price=current_price,
        plus=float(config["plus"]),
        minus=float(config["minus"]),
        step=float(config["step"]),
    )
    save_positions(positions)
    return status_payload(key, positions[key], current_price)


def reset_all_positions() -> dict[str, Any]:
    """Пересоздает все позиции за один проход с батч-запросом цен."""
    coin_ids = list(dict.fromkeys(str(config["coin_id"]) for config in POSITION_CONFIG.values()))
    prices = get_prices(coin_ids, force_refresh=True)

    positions = load_positions()
    results: dict[str, Any] = {}
    for key, config in POSITION_CONFIG.items():
        current_price = prices[str(config["coin_id"])]
        positions[key] = create_position(
            price=current_price,
            plus=float(config["plus"]),
            minus=float(config["minus"]),
            step=float(config["step"]),
        )
        results[key] = status_payload(key, positions[key], current_price)
    save_positions(positions)
    return results


def create_app() -> Flask:
    """Создает и конфигурирует Flask-приложение с маршрутами."""
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        """Рендерит главную страницу со статусом всех позиций."""
        try:
            snapshot = fetch_snapshot()
        except PriceUnavailableError:
            snapshot = fetch_snapshot_fallback()
        return render_template_string(PAGE_TEMPLATE, items=snapshot["items"])

    @app.get("/api/status")
    def api_status():
        """Возвращает JSON-статус по всем позициям."""
        try:
            return jsonify(fetch_snapshot())
        except PriceUnavailableError:
            return jsonify(fetch_snapshot_fallback()), 503

    @app.post("/reset/<key>")
    def reset_position_route(key: str):
        """Пересоздает одну позицию и возвращает JSON или редирект на главную."""
        try:
            result = reset_position_by_key(key)
        except PriceUnavailableError:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": "CoinGecko временно недоступен"}), 503
            return redirect(url_for("index"))
        if request.accept_mimetypes.best == "application/json":
            return jsonify(result)
        return redirect(url_for("index"))

    @app.post("/reset-all")
    def reset_all_route():
        """Пересоздает все позиции и возвращает JSON или редирект на главную."""
        try:
            results = reset_all_positions()
        except PriceUnavailableError:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": "CoinGecko временно недоступен"}), 503
            return redirect(url_for("index"))
        if request.accept_mimetypes.best == "application/json":
            return jsonify(results)
        return redirect(url_for("index"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
