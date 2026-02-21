"""DeFi dashboard for Arbitrum wallet positions.

Run:
    pip install streamlit requests pandas
    streamlit run main.py
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import pandas as pd
import requests
import streamlit as st

NETWORKS = {
    "Arbitrum": {
        "chain_label": "arbitrum",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-arbitrum",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-arbitrum-one",
        "supports_gmx": True,
    },
    "Ethereum": {
        "chain_label": "ethereum",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
        "supports_gmx": False,
    },
    "Polygon": {
        "chain_label": "polygon",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-polygon",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/messari/uniswap-v3-polygon",
        "supports_gmx": False,
    },
    "Optimism": {
        "chain_label": "optimism",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-optimism",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/messari/uniswap-v3-optimism",
        "supports_gmx": False,
    },
}


@st.cache_data(ttl=300, show_spinner=False)
def cached_post_json(url: str, payload_json: str, timeout: int) -> dict[str, Any]:
    response = requests.post(url, json=json.loads(payload_json), timeout=timeout)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def cached_get_json(url: str, params_json: str | None, timeout: int) -> dict[str, Any]:
    params = json.loads(params_json) if params_json else None
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


@dataclass
class ProtocolResult:
    protocol: str
    rows: list[dict[str, Any]]
    error: str | None = None


class ApiClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return cached_post_json(url, json.dumps(payload, sort_keys=True), self.timeout)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params_json = json.dumps(params, sort_keys=True) if params else None
        return cached_get_json(url, params_json, self.timeout)


def fetch_aave(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    endpoint = NETWORKS[network]["aave_subgraph"]
    query = """
    query($user: String!) {
      userReserves(where: { user: $user }) {
        reserve {
          symbol
          name
        }
        currentATokenBalance
        currentTotalDebt
      }
    }
    """

    try:
        payload = client.post_json(endpoint, {"query": query, "variables": {"user": wallet.lower()}})
        rows = []
        for item in payload.get("data", {}).get("userReserves", []):
            rows.append(
                {
                    "Asset": item["reserve"]["symbol"],
                    "Supply": float(item["currentATokenBalance"]),
                    "Debt": float(item["currentTotalDebt"]),
                }
            )
        return ProtocolResult(protocol="Aave", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Aave", rows=[], error=str(exc))


def fetch_gmx(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    if not NETWORKS[network]["supports_gmx"]:
        return ProtocolResult(protocol="GMX", rows=[], error=f"GMX недоступен в сети {network}")

    endpoint = "https://arbitrum-api.gmxinfra2.io/user/positions"
    try:
        payload = client.get_json(endpoint, params={"account": wallet.lower()})
        positions = payload.get("positions", []) if isinstance(payload, dict) else payload
        rows = []
        for pos in positions or []:
            rows.append(
                {
                    "Market": pos.get("market", "n/a"),
                    "Side": pos.get("isLong", "n/a"),
                    "SizeUsd": pos.get("sizeInUsd", pos.get("sizeUsd", 0)),
                    "PnlUsd": pos.get("pnlAfterPriceImpactUsd", pos.get("pnlUsd", 0)),
                }
            )
        return ProtocolResult(protocol="GMX", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="GMX", rows=[], error=str(exc))


def fetch_curve(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    endpoint = "https://api.curve.finance/api/getUserLiquidityPools"
    try:
        payload = client.get_json(endpoint, params={"blockchainId": NETWORKS[network]["chain_label"], "address": wallet})
        pools = payload.get("data", {}).get("poolData", [])
        rows = []
        for pool in pools:
            rows.append(
                {
                    "Pool": pool.get("name", "n/a"),
                    "LpBalance": pool.get("lpBalance", 0),
                    "Usd": pool.get("usdTotal", 0),
                }
            )
        return ProtocolResult(protocol="Curve", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Curve", rows=[], error=str(exc))


def fetch_uniswap(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    endpoint = NETWORKS[network]["uniswap_subgraph"]
    query = """
    query($owner: String!) {
      positions(where: { owner: $owner, liquidity_gt: 0 }) {
        id
        liquidity
        pool {
          token0 { symbol }
          token1 { symbol }
          feeTier
        }
      }
    }
    """

    try:
        payload = client.post_json(endpoint, {"query": query, "variables": {"owner": wallet.lower()}})
        rows = []
        for item in payload.get("data", {}).get("positions", []):
            rows.append(
                {
                    "Pool": f"{item['pool']['token0']['symbol']}/{item['pool']['token1']['symbol']}",
                    "FeeTier": item["pool"]["feeTier"],
                    "Liquidity": item["liquidity"],
                }
            )
        return ProtocolResult(protocol="Uniswap", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Uniswap", rows=[], error=str(exc))


def render_filtered_table(df: pd.DataFrame, key_prefix: str) -> None:
    query = st.text_input("Фильтр по тексту", key=f"{key_prefix}_query", placeholder="например, ETH")
    filtered = df
    if query:
        row_match = df.astype(str).apply(lambda row: row.str.contains(query, case=False, na=False).any(), axis=1)
        filtered = df[row_match]

    sort_col = st.selectbox("Сортировать по", options=list(filtered.columns), key=f"{key_prefix}_sort")
    ascending = st.checkbox("По возрастанию", value=False, key=f"{key_prefix}_asc")
    filtered = filtered.sort_values(by=sort_col, ascending=ascending, kind="stable")
    st.dataframe(filtered, use_container_width=True)


def show_protocol(result: ProtocolResult, key_prefix: str) -> None:
    with st.expander(result.protocol, expanded=True):
        if result.error:
            st.error(result.error)
            return

        if not result.rows:
            st.info("Данные не найдены")
            return

        render_filtered_table(pd.DataFrame(result.rows), key_prefix=key_prefix)


def show_charts(results: list[ProtocolResult]) -> None:
    st.subheader("Визуализация")
    all_rows: list[dict[str, Any]] = []
    for result in results:
        for row in result.rows:
            all_rows.append({"Protocol": result.protocol, **row})

    if not all_rows:
        st.info("Недостаточно данных для графиков")
        return

    df = pd.DataFrame(all_rows)
    numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])]
    if not numeric_cols:
        st.info("В данных нет числовых колонок для построения графиков")
        return

    balance_col = st.selectbox("Метрика баланса", options=numeric_cols, key="balance_metric")
    balances = df.groupby("Protocol", as_index=False)[balance_col].sum().set_index("Protocol")
    st.caption("Баланс по протоколам")
    st.bar_chart(balances)

    st.caption("Распределение активов")
    asset_col_candidates = ["Asset", "Pool", "Market"]
    asset_col = next((col for col in asset_col_candidates if col in df.columns), None)
    if asset_col:
        allocation = df.groupby(asset_col, as_index=False)[balance_col].sum().set_index(asset_col)
        st.bar_chart(allocation)
    else:
        st.info("Не удалось определить колонку актива для построения распределения")


def main() -> None:
    st.set_page_config(page_title="DeFi Dashboard", layout="wide")
    st.title("📊 DeFi wallet dashboard")
    st.caption("Aave / GMX / Curve / Uniswap")

    network = st.selectbox("Сеть", options=list(NETWORKS.keys()), index=0)
    wallets_raw = st.text_area("Адреса кошельков (по одному в строке или через запятую)", placeholder="0x...\n0x...")

    if not wallets_raw.strip():
        st.info("Введите минимум один адрес кошелька")
        return

    wallets = [w.strip() for chunk in wallets_raw.splitlines() for w in chunk.split(",") if w.strip()]
    invalid_wallets = [wallet for wallet in wallets if not (wallet.startswith("0x") and len(wallet) == 42)]
    if invalid_wallets:
        st.warning(f"Некорректные EVM-адреса: {', '.join(invalid_wallets)}")
        return

    client = ApiClient()
    protocol_map: dict[str, list[dict[str, Any]]] = {"Aave": [], "GMX": [], "Curve": [], "Uniswap": []}
    protocol_errors: dict[str, str] = {}

    with st.spinner("Собираю данные..."):
        for wallet in wallets:
            wallet_results = [
                fetch_aave(client, wallet, network),
                fetch_gmx(client, wallet, network),
                fetch_curve(client, wallet, network),
                fetch_uniswap(client, wallet, network),
            ]
            for result in wallet_results:
                if result.error:
                    protocol_errors[result.protocol] = result.error
                    continue
                for row in result.rows:
                    protocol_map[result.protocol].append({"Wallet": wallet, **row})

    results = []
    for protocol, rows in protocol_map.items():
        results.append(ProtocolResult(protocol=protocol, rows=rows, error=protocol_errors.get(protocol)))

    totals = []
    for result in results:
        totals.append({"Protocol": result.protocol, "Rows": len(result.rows), "Error": bool(result.error)})

    st.subheader("Сводка")
    st.dataframe(pd.DataFrame(totals), use_container_width=True)

    st.subheader("Детали")
    for result in results:
        show_protocol(result, key_prefix=f"{network}_{result.protocol}")

    show_charts(results)


if __name__ == "__main__":
    main()
