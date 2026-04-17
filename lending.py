from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, redirect, render_template_string, request, url_for
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

AAVE_GRAPHQL_URL = "https://api.v3.aave.com/graphql"
COMPOUND_MARKETS_URL = "https://raw.githubusercontent.com/woof-software/compound-docs-aggregator/main/output.json"
FLUID_LENDING_URL = "https://api.fluid.instadapp.io/v2/lending/{chain_id}/tokens"
FLUID_BORROWING_URL = "https://api.fluid.instadapp.io/v2/borrowing/{chain_id}/vaults"

ARBITRUM_CHAIN_ID = 42161
BASE_CHAIN_ID = 8453
SUPPORTED_CHAINS = ("Arbitrum", "Base")
TARGET_PROTOCOLS = ("Aave", "Fluid", "Compound")

RPC_ENDPOINTS_BY_CHAIN = {
    "arbitrum": [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one-rpc.publicnode.com",
    ],
    "base": [
        "https://mainnet.base.org",
        "https://base-rpc.publicnode.com",
        "https://base.llamarpc.com",
    ],
}

CHAIN_NAME_BY_SLUG = {
    "arbitrum": "Arbitrum",
    "base": "Base",
}

CHAIN_ID_BY_NAME = {
    "Arbitrum": ARBITRUM_CHAIN_ID,
    "Base": BASE_CHAIN_ID,
}

SECONDS_PER_YEAR = 365 * 24 * 60 * 60
CACHE_TTL_SECONDS = 60

# Compound selectors (4-byte signatures)
SEL_GET_UTILIZATION = "0x7eb71131"

CACHE_LOCK = Lock()
RATES_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "updated_at": "n/a",
    "notes": [],
    "rows": [],
}

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lending Rates</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #1b2430;
      --muted: #5f6c7b;
      --line: #dce3ec;
      --accent: #0a66c2;
      --good: #0e9f6e;
      --warn: #b45309;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #eef4ff 0%, var(--bg) 50%);
      color: var(--text);
      font-family: "Segoe UI", Tahoma, sans-serif;
    }
    .wrap { max-width: 1350px; margin: 0 auto; padding: 24px 18px 40px; }
    h1 { margin: 0 0 8px; font-size: 34px; }
    p.meta { margin: 0; color: var(--muted); }
    .toolbar, .summary, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-top: 14px;
    }
    .toolbar form {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 10px;
      align-items: end;
    }
    label { font-size: 13px; color: var(--muted); display: block; margin-bottom: 4px; }
    input, select, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      font: inherit;
      background: #fff;
    }
    button {
      cursor: pointer;
      border: 0;
      background: var(--accent);
      color: white;
      font-weight: 600;
    }
    .ghost-btn {
      display: inline-block;
      text-decoration: none;
      text-align: center;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid var(--line);
      color: var(--text);
      background: #fff;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #fafcff;
    }
    .metric .k { color: var(--muted); font-size: 12px; }
    .metric .v { font-size: 20px; font-weight: 700; margin-top: 4px; }
    .metric .s { color: var(--muted); font-size: 12px; margin-top: 3px; }
    .notes {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 8px;
      font-size: 14px;
      white-space: nowrap;
    }
    th { color: var(--muted); font-weight: 600; }
    .num { text-align: right; }
    .ok { color: var(--good); font-weight: 600; }
    .warn { color: var(--warn); font-weight: 600; }
    .empty {
      color: var(--muted);
      padding: 12px 4px;
    }
    .table-scroll { overflow-x: auto; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Ставки займов и вкладов</h1>
    <p class="meta">Прямые источники: Aave API, Compound on-chain, Fluid API | Сети: Arbitrum, Base</p>
    <p class="meta">Обновлено: {{ updated_at }} | Записей: {{ rows|length }}</p>

    <div class="toolbar">
      <form method="get" action="{{ url_for('index') }}">
        <div>
          <label for="chains">Сети</label>
          <select id="chains" name="chains" multiple>
            {% for chain in all_chains %}
            <option value="{{ chain }}" {% if chain in selected_chains %}selected{% endif %}>{{ chain }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label for="protocols">Протоколы</label>
          <select id="protocols" name="protocols" multiple>
            {% for protocol in all_protocols %}
            <option value="{{ protocol }}" {% if protocol in selected_protocols %}selected{% endif %}>{{ protocol }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label for="asset">Актив</label>
          <input id="asset" name="asset" type="text" value="{{ asset_filter }}" placeholder="USDC, ETH...">
        </div>
        <div>
          <label for="min_tvl">Мин. TVL, USD</label>
          <input id="min_tvl" name="min_tvl" type="number" min="0" step="1000" value="{{ min_tvl }}">
        </div>
        <div>
          <label for="sort">Сортировка</label>
          <select id="sort" name="sort">
            <option value="supply_desc" {% if sort == 'supply_desc' %}selected{% endif %}>Вклад: выше</option>
            <option value="borrow_asc" {% if sort == 'borrow_asc' %}selected{% endif %}>Заем: ниже</option>
            <option value="tvl_desc" {% if sort == 'tvl_desc' %}selected{% endif %}>TVL: выше</option>
          </select>
        </div>
        <div>
          <label>&nbsp;</label>
          <button type="submit">Применить</button>
        </div>
      </form>
      <div style="margin-top:10px; display:flex; gap:10px;">
        <form method="post" action="{{ url_for('refresh_cache') }}" style="margin:0;">
          <button type="submit">Обновить данные</button>
        </form>
        <a class="ghost-btn" href="{{ url_for('api_rates') }}">JSON API</a>
      </div>
      {% if notes %}
      <div class="notes">
        {% for note in notes %}
        <div>{{ note }}</div>
        {% endfor %}
      </div>
      {% endif %}
    </div>

    <div class="summary">
      <div class="metric">
        <div class="k">Лучший вклад</div>
        <div class="v">{{ summary.best_supply_rate }}</div>
        <div class="s">{{ summary.best_supply_label }}</div>
      </div>
      <div class="metric">
        <div class="k">Самый дешевый заем</div>
        <div class="v">{{ summary.best_borrow_rate }}</div>
        <div class="s">{{ summary.best_borrow_label }}</div>
      </div>
      <div class="metric">
        <div class="k">Суммарный TVL</div>
        <div class="v">{{ summary.total_tvl }}</div>
        <div class="s">по отфильтрованным рынкам</div>
      </div>
    </div>

    <div class="panel table-scroll">
      {% if rows %}
      <table>
        <thead>
          <tr>
            <th>Сеть</th>
            <th>Протокол</th>
            <th>Актив</th>
            <th>Рынок</th>
            <th class="num">Вклад %</th>
            <th class="num">Заем %</th>
            <th class="num">TVL, USD</th>
          </tr>
        </thead>
        <tbody>
          {% for row in rows %}
          <tr>
            <td>{{ row.chain }}</td>
            <td>{{ row.protocol }}</td>
            <td>{{ row.symbol }}</td>
            <td>{{ row.pool_name }}</td>
            <td class="num ok">{{ row.supply_rate_text }}</td>
            <td class="num warn">{{ row.borrow_rate_text }}</td>
            <td class="num">{{ row.tvl_text }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="empty">Нет данных по выбранным фильтрам.</div>
      {% endif %}
    </div>
  </div>
</body>
</html>
"""


class DataUnavailableError(RuntimeError):
    """Ошибка, когда не удалось получить данные ни из одного источника."""


def build_http_session() -> requests.Session:
    """Создает HTTP-сессию с повторами на временные ошибки API."""
    retry = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = build_http_session()


def as_float(value: Any) -> float | None:
    """Безопасно приводит значение к float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_pct(value: float | None) -> str:
    """Форматирует процентную ставку."""
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def format_usd(value: float | None) -> str:
    """Форматирует сумму в USD."""
    if value is None:
        return "n/a"
    return f"${value:,.0f}"


def now_utc_str() -> str:
    """Возвращает текущее UTC-время в строковом формате."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def aave_rows() -> list[dict[str, Any]]:
    """Собирает ставки Aave напрямую через официальный GraphQL API."""
    query = """
    query {
      markets(request: { chainIds: [42161, 8453] }) {
        chain { name }
        reserves {
          underlyingToken { symbol }
          size { usd }
          supplyInfo { apy { value } }
          borrowInfo { apy { value } }
        }
      }
    }
    """
    response = HTTP_SESSION.post(AAVE_GRAPHQL_URL, json={"query": query}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise requests.HTTPError(str(payload["errors"]))

    rows: list[dict[str, Any]] = []
    for market in payload["data"]["markets"]:
        chain_name = str(market["chain"]["name"])
        if chain_name not in SUPPORTED_CHAINS:
            continue

        for reserve in market["reserves"]:
            symbol = str(reserve["underlyingToken"]["symbol"])
            supply_raw = as_float(reserve["supplyInfo"]["apy"]["value"]) or 0.0
            borrow_raw = None
            if reserve.get("borrowInfo"):
                borrow_raw = as_float(reserve["borrowInfo"]["apy"]["value"])

            supply_rate = supply_raw * 100.0
            borrow_rate = None if borrow_raw is None else borrow_raw * 100.0
            tvl_usd = as_float(reserve["size"]["usd"]) or 0.0

            rows.append(
                {
                    "chain": chain_name,
                    "protocol": "Aave",
                    "symbol": symbol,
                    "pool_name": "Aave V3",
                    "supply_rate": supply_rate,
                    "borrow_rate": borrow_rate,
                    "tvl_usd": tvl_usd,
                    "supply_rate_text": format_pct(supply_rate),
                    "borrow_rate_text": format_pct(borrow_rate),
                    "tvl_text": format_usd(tvl_usd),
                }
            )
    return rows


def eth_call(chain_slug: str, to_address: str, data: str) -> str:
    """Делает eth_call с fallback на несколько RPC-эндпоинтов сети."""
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": to_address, "data": data}, "latest"], "id": 1}
    last_error: Exception | None = None

    for rpc_url in RPC_ENDPOINTS_BY_CHAIN[chain_slug]:
        try:
            response = HTTP_SESSION.post(rpc_url, json=payload, timeout=25)
            if response.status_code == 429:
                last_error = requests.HTTPError(f"{rpc_url}: 429")
                continue
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                err_text = str(body["error"])
                if "429" in err_text:
                    last_error = requests.HTTPError(f"{rpc_url}: {err_text}")
                    continue
                raise requests.HTTPError(err_text)
            return str(body["result"])
        except requests.RequestException as exc:
            last_error = exc
            continue

    if last_error is None:
        raise requests.HTTPError(f"{chain_slug}: eth_call не выполнен")
    raise last_error


def decode_uint256(hex_value: str) -> int:
    """Декодирует uint256 из hex-строки."""
    return int(hex_value, 16)


def encode_uint256(value: int) -> str:
    """Кодирует uint256 в hex-представление calldata (64 hex символа)."""
    return format(value, "064x")


def per_second_to_apy_percent(rate_per_second_scaled_1e18: int) -> float:
    """Переводит Compound per-second rate (1e18) в APY (%)."""
    per_second = rate_per_second_scaled_1e18 / 1e18
    return (math.pow(1.0 + per_second, SECONDS_PER_YEAR) - 1.0) * 100.0


def compound_rate_per_second(utilization: int, kink: int, base: int, slope_low: int, slope_high: int) -> int:
    """Считает per-second rate для Compound по piecewise-кривой ставок."""
    if utilization <= kink:
        return base + (slope_low * utilization) // 10**18
    return base + (slope_low * kink) // 10**18 + (slope_high * (utilization - kink)) // 10**18


def compound_rows() -> list[dict[str, Any]]:
    """Собирает ставки Compound V3 напрямую on-chain через публичные RPC."""
    response = HTTP_SESSION.get(COMPOUND_MARKETS_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()

    rows: list[dict[str, Any]] = []
    for chain_slug in ("arbitrum", "base"):
        markets = payload["markets"].get(chain_slug, {})
        chain_name = CHAIN_NAME_BY_SLUG[chain_slug]

        for market_name, market_data in markets.items():
            comet = str(market_data["contracts"]["comet"])
            base_symbol = str(market_data["baseToken"]["symbol"])
            curve = market_data["curve"]

            utilization_raw = decode_uint256(eth_call(chain_slug, comet, SEL_GET_UTILIZATION))

            supply_rate_raw = compound_rate_per_second(
                utilization=utilization_raw,
                kink=int(curve["supplyKink"]["value"]),
                base=int(curve["supplyPerSecondInterestRateBase"]["value"]),
                slope_low=int(curve["supplyPerSecondInterestRateSlopeLow"]["value"]),
                slope_high=int(curve["supplyPerSecondInterestRateSlopeHigh"]["value"]),
            )
            borrow_rate_raw = compound_rate_per_second(
                utilization=utilization_raw,
                kink=int(curve["borrowKink"]["value"]),
                base=int(curve["borrowPerSecondInterestRateBase"]["value"]),
                slope_low=int(curve["borrowPerSecondInterestRateSlopeLow"]["value"]),
                slope_high=int(curve["borrowPerSecondInterestRateSlopeHigh"]["value"]),
            )

            supply_apy = per_second_to_apy_percent(supply_rate_raw)
            borrow_apy = per_second_to_apy_percent(borrow_rate_raw)

            rows.append(
                {
                    "chain": chain_name,
                    "protocol": "Compound",
                    "symbol": base_symbol,
                    "pool_name": market_name,
                    "supply_rate": supply_apy,
                    "borrow_rate": borrow_apy,
                    "tvl_usd": None,
                    "supply_rate_text": format_pct(supply_apy),
                    "borrow_rate_text": format_pct(borrow_apy),
                    "tvl_text": "n/a",
                }
            )
    return rows


def fluid_rows() -> list[dict[str, Any]]:
    """Собирает ставки Fluid напрямую из официального Fluid API."""
    rows: list[dict[str, Any]] = []

    for chain_name in SUPPORTED_CHAINS:
        chain_id = CHAIN_ID_BY_NAME[chain_name]

        lending_resp = HTTP_SESSION.get(FLUID_LENDING_URL.format(chain_id=chain_id), timeout=30)
        lending_resp.raise_for_status()
        lending_payload = lending_resp.json()
        for token in lending_payload.get("data", []):
            symbol = str(token.get("asset", {}).get("symbol") or token.get("symbol") or "")
            supply_rate = (as_float(token.get("totalRate")) or 0.0) / 100.0

            total_assets = as_float(token.get("totalAssets")) or 0.0
            price = as_float(token.get("asset", {}).get("price")) or 0.0
            decimals = as_float(token.get("asset", {}).get("decimals")) or 18.0
            tvl_usd = (total_assets / (10**int(decimals))) * price if total_assets and price else None

            rows.append(
                {
                    "chain": chain_name,
                    "protocol": "Fluid",
                    "symbol": symbol,
                    "pool_name": "Lending",
                    "supply_rate": supply_rate,
                    "borrow_rate": None,
                    "tvl_usd": tvl_usd,
                    "supply_rate_text": format_pct(supply_rate),
                    "borrow_rate_text": "n/a",
                    "tvl_text": format_usd(tvl_usd),
                }
            )

        borrowing_resp = HTTP_SESSION.get(FLUID_BORROWING_URL.format(chain_id=chain_id), timeout=30)
        borrowing_resp.raise_for_status()
        borrowing_payload = borrowing_resp.json()
        for vault in borrowing_payload:
            supply_symbol = str(vault.get("supplyToken", {}).get("token0", {}).get("symbol") or "")
            borrow_symbol = str(vault.get("borrowToken", {}).get("token0", {}).get("symbol") or "")
            symbol = f"{supply_symbol}->{borrow_symbol}" if supply_symbol and borrow_symbol else "Vault"

            supply_rate = as_float(vault.get("supplyRate", {}).get("vault", {}).get("rate"))
            borrow_rate = as_float(vault.get("borrowRate", {}).get("vault", {}).get("rate"))
            supply_rate = None if supply_rate is None else supply_rate / 100.0
            borrow_rate = None if borrow_rate is None else borrow_rate / 100.0

            rows.append(
                {
                    "chain": chain_name,
                    "protocol": "Fluid",
                    "symbol": symbol,
                    "pool_name": f"Vault #{vault.get('id', 'n/a')}",
                    "supply_rate": supply_rate,
                    "borrow_rate": borrow_rate,
                    "tvl_usd": None,
                    "supply_rate_text": format_pct(supply_rate),
                    "borrow_rate_text": format_pct(borrow_rate),
                    "tvl_text": "n/a",
                }
            )

    return rows


def fetch_rates(force_refresh: bool = False) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Возвращает объединенные ставки с кэшированием и статусами источников."""
    now = time.time()
    with CACHE_LOCK:
        if not force_refresh and RATES_CACHE["rows"] and now < float(RATES_CACHE["expires_at"]):
            return list(RATES_CACHE["rows"]), str(RATES_CACHE["updated_at"]), list(RATES_CACHE["notes"])
        stale_rows = list(RATES_CACHE["rows"])
        stale_updated = str(RATES_CACHE["updated_at"])
        stale_notes = list(RATES_CACHE["notes"])

    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    errors: list[str] = []

    for source_name, fn in (("Aave", aave_rows), ("Compound", compound_rows), ("Fluid", fluid_rows)):
        try:
            source_rows = fn()
            rows.extend(source_rows)
            notes.append(f"{source_name}: ok ({len(source_rows)} записей)")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_name}: {exc}")

    if not rows:
        if stale_rows:
            return stale_rows, stale_updated, stale_notes
        raise DataUnavailableError("Не удалось получить данные ни из одного источника")

    if errors:
        notes.extend(f"Ошибка источника: {msg}" for msg in errors)

    updated_at = now_utc_str()
    with CACHE_LOCK:
        RATES_CACHE["rows"] = rows
        RATES_CACHE["updated_at"] = updated_at
        RATES_CACHE["notes"] = notes
        RATES_CACHE["expires_at"] = time.time() + CACHE_TTL_SECONDS

    return rows, updated_at, notes


def apply_filters(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Применяет фильтры из query-параметров к списку ставок."""
    selected_chains = request.args.getlist("chains") or list(SUPPORTED_CHAINS)
    selected_protocols = request.args.getlist("protocols") or list(TARGET_PROTOCOLS)
    asset_filter = request.args.get("asset", "").strip().upper()
    sort_key = request.args.get("sort", "supply_desc").strip()
    min_tvl = as_float(request.args.get("min_tvl", "0")) or 0.0

    filtered = [
        row
        for row in rows
        if row["chain"] in selected_chains
        and row["protocol"] in selected_protocols
        and ((row["tvl_usd"] or 0.0) >= min_tvl)
        and (not asset_filter or asset_filter in row["symbol"].upper())
    ]

    if sort_key == "borrow_asc":
        filtered.sort(key=lambda x: x["borrow_rate"] if x["borrow_rate"] is not None else 10**9)
    elif sort_key == "tvl_desc":
        filtered.sort(key=lambda x: x["tvl_usd"] if x["tvl_usd"] is not None else -1, reverse=True)
    else:
        filtered.sort(key=lambda x: x["supply_rate"] if x["supply_rate"] is not None else -1, reverse=True)

    return filtered, {
        "selected_chains": selected_chains,
        "selected_protocols": selected_protocols,
        "asset_filter": asset_filter,
        "sort": sort_key,
        "min_tvl": int(min_tvl),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Считает ключевые метрики по отфильтрованным строкам."""
    if not rows:
        return {
            "best_supply_rate": "n/a",
            "best_supply_label": "нет данных",
            "best_borrow_rate": "n/a",
            "best_borrow_label": "нет данных",
            "total_tvl": "$0",
        }

    supply_candidates = [row for row in rows if row["supply_rate"] is not None]
    borrow_candidates = [row for row in rows if row["borrow_rate"] is not None]
    tvl_values = [row["tvl_usd"] for row in rows if row["tvl_usd"] is not None]

    best_supply = max(supply_candidates, key=lambda x: float(x["supply_rate"])) if supply_candidates else None
    best_borrow = min(borrow_candidates, key=lambda x: float(x["borrow_rate"])) if borrow_candidates else None
    total_tvl = sum(float(v) for v in tvl_values) if tvl_values else 0.0

    return {
        "best_supply_rate": format_pct(best_supply["supply_rate"]) if best_supply else "n/a",
        "best_supply_label": (
            f"{best_supply['protocol']} / {best_supply['chain']} / {best_supply['symbol']}"
            if best_supply
            else "нет данных"
        ),
        "best_borrow_rate": format_pct(best_borrow["borrow_rate"]) if best_borrow else "n/a",
        "best_borrow_label": (
            f"{best_borrow['protocol']} / {best_borrow['chain']} / {best_borrow['symbol']}"
            if best_borrow
            else "нет данных"
        ),
        "total_tvl": format_usd(total_tvl),
    }


def create_app() -> Flask:
    """Создает Flask-приложение для мониторинга lending-ставок."""
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        """Показывает страницу со ставками по протоколам и сетям."""
        try:
            rows, updated_at, notes = fetch_rates()
        except DataUnavailableError as exc:
            return f"<h1>Ошибка данных</h1><p>{exc}</p>", 503

        filtered_rows, filter_state = apply_filters(rows)
        summary = build_summary(filtered_rows)

        return render_template_string(
            PAGE_TEMPLATE,
            rows=filtered_rows,
            updated_at=updated_at,
            notes=notes,
            all_chains=SUPPORTED_CHAINS,
            all_protocols=TARGET_PROTOCOLS,
            summary=summary,
            **filter_state,
        )

    @app.get("/api/rates")
    def api_rates():
        """Возвращает JSON со ставками и состоянием источников."""
        try:
            rows, updated_at, notes = fetch_rates()
        except DataUnavailableError:
            return jsonify({"error": "Источники данных недоступны"}), 503

        filtered_rows, filter_state = apply_filters(rows)
        return jsonify(
            {
                "updated_at": updated_at,
                "notes": notes,
                "count": len(filtered_rows),
                "filters": filter_state,
                "summary": build_summary(filtered_rows),
                "items": filtered_rows,
            }
        )

    @app.post("/refresh")
    def refresh_cache():
        """Сбрасывает TTL-кэш и принудительно обновляет данные."""
        with CACHE_LOCK:
            RATES_CACHE["expires_at"] = 0.0
        try:
            fetch_rates(force_refresh=True)
        except DataUnavailableError:
            pass
        return redirect(url_for("index"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
