"""DeFi wallet dashboard.

Run:
    pip install -r requirements.txt
    streamlit run main.py
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import pandas as pd
import requests
import streamlit as st

NETWORKS: dict[str, dict[str, str]] = {
    "Ethereum": {
        "label": "ethereum",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "native_symbol": "ETH",
        "coingecko_id": "ethereum",
    },
    "Arbitrum": {
        "label": "arbitrum",
        "rpc": "https://arbitrum-one-rpc.publicnode.com",
        "native_symbol": "ETH",
        "coingecko_id": "ethereum",
    },
    "Optimism": {
        "label": "optimism",
        "rpc": "https://optimism-rpc.publicnode.com",
        "native_symbol": "ETH",
        "coingecko_id": "ethereum",
    },
    "Polygon": {
        "label": "polygon",
        "rpc": "https://polygon-bor-rpc.publicnode.com",
        "native_symbol": "MATIC",
        "coingecko_id": "matic-network",
    },
}


@dataclass
class ProtocolResult:
    protocol: str
    rows: list[dict[str, Any]]
    error: str | None = None


class ApiClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def is_valid_evm_wallet(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value))


def parse_wallets(raw: str) -> list[str]:
    tokens = [part.strip() for chunk in raw.splitlines() for part in chunk.split(",")]
    wallets = [token for token in tokens if token]
    return list(dict.fromkeys(wallets))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _native_balance(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    cfg = NETWORKS[network]
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [wallet, "latest"]}

    try:
        data = client.post_json(cfg["rpc"], payload)
        if "error" in data:
            return ProtocolResult(protocol=f"Native ({network})", rows=[], error=str(data["error"]))

        wei_hex = str(data.get("result", "0x0"))
        balance = int(wei_hex, 16) / 1e18
        return ProtocolResult(
            protocol=f"Native ({network})",
            rows=[
                {
                    "Network": network,
                    "Asset": cfg["native_symbol"],
                    "Balance": balance,
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol=f"Native ({network})", rows=[], error=str(exc))


def _native_prices_usd(client: ApiClient) -> dict[str, float]:
    ids = sorted({cfg["coingecko_id"] for cfg in NETWORKS.values()})
    data = client.get_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": "usd"},
    )

    prices: dict[str, float] = {}
    for cfg in NETWORKS.values():
        prices[cfg["native_symbol"]] = _safe_float(data.get(cfg["coingecko_id"], {}).get("usd"), 0.0)
    return prices


def _ethplorer_tokens(client: ApiClient, wallet: str) -> ProtocolResult:
    endpoint = f"https://api.ethplorer.io/getAddressInfo/{wallet}"

    try:
        payload = client.get_json(endpoint, params={"apiKey": "freekey"})
        tokens = payload.get("tokens", [])
        rows: list[dict[str, Any]] = []
        for item in tokens:
            info = item.get("tokenInfo", {})
            symbol = info.get("symbol") or info.get("name") or "UNKNOWN"
            decimals_raw = info.get("decimals", "0")
            decimals = int(decimals_raw) if str(decimals_raw).isdigit() else 0
            raw_balance = _safe_float(item.get("rawBalance"), 0.0)
            balance = raw_balance / (10**decimals) if decimals >= 0 else 0.0
            usd_rate = _safe_float(info.get("price", {}).get("rate"), 0.0)
            usd_value = balance * usd_rate
            if balance <= 0:
                continue
            rows.append({"Asset": symbol, "Balance": balance, "Usd": usd_value})

        rows.sort(key=lambda x: x["Usd"], reverse=True)
        return ProtocolResult(protocol="Ethereum tokens", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Ethereum tokens", rows=[], error=str(exc))


@st.cache_data(ttl=120)
def fetch_wallet_data(wallet: str) -> list[ProtocolResult]:
    client = ApiClient()
    results = [_native_balance(client, wallet, network) for network in NETWORKS]
    results.append(_ethplorer_tokens(client, wallet))

    try:
        prices = _native_prices_usd(client)
        for result in results:
            if result.error or not result.rows:
                continue
            if result.protocol.startswith("Native"):
                for row in result.rows:
                    sym = str(row.get("Asset", ""))
                    bal = _safe_float(row.get("Balance"), 0.0)
                    row["Usd"] = bal * prices.get(sym, 0.0)
    except Exception:
        pass

    return results


def _value_column(df: pd.DataFrame) -> str | None:
    for col in ["Usd", "Balance"]:
        if col in df.columns:
            return col
    return None


def show_protocol(result: ProtocolResult, wallet: str) -> None:
    with st.expander(f"{result.protocol} - {wallet}", expanded=False):
        if result.error:
            st.error(result.error)
            return
        if not result.rows:
            st.info("Данные не найдены")
            return

        df = pd.DataFrame(result.rows)
        sort_col = st.selectbox("Сортировка", options=list(df.columns), key=f"sort-{wallet}-{result.protocol}")
        desc = st.checkbox("По убыванию", value=True, key=f"desc-{wallet}-{result.protocol}")
        df = df.sort_values(sort_col, ascending=not desc)
        st.dataframe(df, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="DeFi Wallet Dashboard", layout="wide")
    st.title("DeFi wallet dashboard")
    st.caption("Native balances (EVM) + Ethereum token balances")

    st.warning(
        "Aave / GMX / Curve / Uniswap временно скрыты: старые публичные endpoint'ы отключены. "
        "Для их возврата нужны новые интеграции с API-ключами."
    )

    raw_wallets = st.text_area("Адреса кошельков (по одному в строке или через запятую)", placeholder="0x...\n0x...")
    wallets = parse_wallets(raw_wallets)

    if not wallets:
        st.info("Введите хотя бы один EVM-адрес")
        return

    invalid = [w for w in wallets if not is_valid_evm_wallet(w)]
    if invalid:
        st.warning(f"Некорректные EVM-адреса: {', '.join(invalid)}")
        return

    all_results: dict[str, list[ProtocolResult]] = {}
    with st.spinner("Собираю данные..."):
        for wallet in wallets:
            all_results[wallet] = fetch_wallet_data(wallet)

    summary_rows: list[dict[str, Any]] = []
    protocol_balance_rows: list[dict[str, Any]] = []

    for wallet, results in all_results.items():
        for result in results:
            summary_rows.append(
                {"Wallet": wallet, "Section": result.protocol, "Rows": len(result.rows), "Error": bool(result.error)}
            )
            if result.error or not result.rows:
                continue

            df = pd.DataFrame(result.rows)
            val_col = _value_column(df)
            if val_col:
                total = pd.to_numeric(df[val_col], errors="coerce").fillna(0).sum()
                protocol_balance_rows.append({"Wallet": wallet, "Section": result.protocol, "Value": total})

    st.subheader("Сводка")
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    if protocol_balance_rows:
        st.subheader("Баланс по секциям")
        chart_df = pd.DataFrame(protocol_balance_rows)
        st.bar_chart(chart_df, x="Section", y="Value", color="Wallet")

    st.subheader("Детали")
    for wallet, results in all_results.items():
        for result in results:
            show_protocol(result, wallet)


if __name__ == "__main__":
    main()
