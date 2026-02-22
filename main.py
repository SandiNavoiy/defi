"""DeFi dashboard for EVM wallet positions.

Run:
    pip install -r requirements.txt
    streamlit run main.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
import streamlit as st

NETWORKS: dict[str, dict[str, str]] = {
    "Arbitrum": {
        "label": "arbitrum",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-arbitrum",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-arbitrum-one",
    },
    "Ethereum": {
        "label": "ethereum",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-ethereum",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
    },
    "Polygon": {
        "label": "polygon",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-polygon",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon",
    },
    "Optimism": {
        "label": "optimism",
        "aave_subgraph": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-optimism",
        "uniswap_subgraph": "https://api.thegraph.com/subgraphs/name/ianlapham/optimism-post-regenesis",
    },
}


@dataclass
class ProtocolResult:
    protocol: str
    rows: list[dict[str, Any]]
    error: str | None = None


class ApiClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            messages = []
            for error in errors:
                messages.append(str(error.get("message", error)) if isinstance(error, dict) else str(error))
            raise ValueError(f"GraphQL query failed: {'; '.join(messages)}")
        return data

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


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
        rows = [
            {
                "Asset": item["reserve"]["symbol"],
                "Supply": float(item["currentATokenBalance"]),
                "Debt": float(item["currentTotalDebt"]),
                "Net": float(item["currentATokenBalance"]) - float(item["currentTotalDebt"]),
            }
            for item in payload.get("data", {}).get("userReserves", [])
        ]
        return ProtocolResult(protocol="Aave", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Aave", rows=[], error=str(exc))


def fetch_gmx(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    if network != "Arbitrum":
        return ProtocolResult(protocol="GMX", rows=[], error="GMX API доступен только для Arbitrum")

    endpoint = "https://arbitrum-api.gmxinfra2.io/user/positions"
    try:
        payload = client.get_json(endpoint, params={"account": wallet.lower()})
        positions = payload.get("positions", []) if isinstance(payload, dict) else payload
        rows = [
            {
                "Market": pos.get("market", "n/a"),
                "Side": "Long" if pos.get("isLong") else "Short",
                "SizeUsd": float(pos.get("sizeInUsd", pos.get("sizeUsd", 0)) or 0),
                "PnlUsd": float(pos.get("pnlAfterPriceImpactUsd", pos.get("pnlUsd", 0)) or 0),
            }
            for pos in (positions or [])
        ]
        return ProtocolResult(protocol="GMX", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="GMX", rows=[], error=str(exc))


def fetch_curve(client: ApiClient, wallet: str, network: str) -> ProtocolResult:
    endpoint = "https://api.curve.finance/api/getUserLiquidityPools"
    try:
        payload = client.get_json(endpoint, params={"blockchainId": NETWORKS[network]["label"], "address": wallet})
        pools = payload.get("data", {}).get("poolData", [])
        rows = [
            {
                "Pool": pool.get("name", "n/a"),
                "LpBalance": float(pool.get("lpBalance", 0) or 0),
                "Usd": float(pool.get("usdTotal", 0) or 0),
            }
            for pool in pools
        ]
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
        rows = [
            {
                "Pool": f"{item['pool']['token0']['symbol']}/{item['pool']['token1']['symbol']}",
                "FeeTier": int(item["pool"]["feeTier"]),
                "Liquidity": float(item["liquidity"]),
            }
            for item in payload.get("data", {}).get("positions", [])
        ]
        return ProtocolResult(protocol="Uniswap", rows=rows)
    except Exception as exc:  # noqa: BLE001
        return ProtocolResult(protocol="Uniswap", rows=[], error=str(exc))


@st.cache_data(ttl=120)
def fetch_wallet_data(wallet: str, network: str) -> list[ProtocolResult]:
    client = ApiClient()
    return [
        fetch_aave(client, wallet, network),
        fetch_gmx(client, wallet, network),
        fetch_curve(client, wallet, network),
        fetch_uniswap(client, wallet, network),
    ]


def _value_column(df: pd.DataFrame) -> str | None:
    for col in ["Usd", "Net", "SizeUsd", "Supply", "Liquidity", "LpBalance", "PnlUsd"]:
        if col in df.columns:
            return col
    return None


def show_protocol(result: ProtocolResult, wallet: str) -> None:
    with st.expander(f"{result.protocol} — {wallet}", expanded=False):
        if result.error:
            st.error(result.error)
            return
        if not result.rows:
            st.info("Данные не найдены")
            return

        df = pd.DataFrame(result.rows)
        columns = [c for c in df.columns if df[c].dtype == "object"]
        filter_col = st.selectbox("Фильтр по колонке", options=["(нет)"] + columns, key=f"flt-{wallet}-{result.protocol}")
        if filter_col != "(нет)":
            query = st.text_input("Содержит", key=f"q-{wallet}-{result.protocol}").strip().lower()
            if query:
                df = df[df[filter_col].astype(str).str.lower().str.contains(query, na=False)]

        sort_col = st.selectbox("Сортировка", options=list(df.columns), key=f"sort-{wallet}-{result.protocol}")
        desc = st.checkbox("По убыванию", value=True, key=f"desc-{wallet}-{result.protocol}")
        df = df.sort_values(sort_col, ascending=not desc)

        st.dataframe(df, use_container_width=True)


def parse_wallets(raw: str) -> list[str]:
    tokens = [part.strip() for chunk in raw.splitlines() for part in chunk.split(",")]
    wallets = [token for token in tokens if token]
    return list(dict.fromkeys(wallets))


def main() -> None:
    st.set_page_config(page_title="DeFi Wallet Dashboard", layout="wide")
    st.title("📊 DeFi wallet dashboard")
    network = st.selectbox("Сеть", options=list(NETWORKS.keys()), index=0)
    st.caption("Aave / GMX / Curve / Uniswap")

    raw_wallets = st.text_area("Адреса кошельков (по одному на строку или через запятую)", placeholder="0x...\n0x...")
    wallets = parse_wallets(raw_wallets)

    if not wallets:
        st.info("Введите хотя бы один EVM-адрес")
        return

    invalid = [w for w in wallets if not (w.startswith("0x") and len(w) == 42)]
    if invalid:
        st.warning(f"Некорректные адреса: {', '.join(invalid)}")
        return

    all_results: dict[str, list[ProtocolResult]] = {}
    with st.spinner("Собираю данные..."):
        for wallet in wallets:
            all_results[wallet] = fetch_wallet_data(wallet, network)

    summary_rows: list[dict[str, Any]] = []
    protocol_balance_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []

    for wallet, results in all_results.items():
        for result in results:
            summary_rows.append(
                {"Wallet": wallet, "Protocol": result.protocol, "Rows": len(result.rows), "Error": bool(result.error)}
            )
            if result.error or not result.rows:
                continue

            df = pd.DataFrame(result.rows)
            val_col = _value_column(df)
            if val_col:
                total = pd.to_numeric(df[val_col], errors="coerce").fillna(0).sum()
                protocol_balance_rows.append({"Wallet": wallet, "Protocol": result.protocol, "Value": total})

            label_col = next((c for c in ["Asset", "Pool", "Market"] if c in df.columns), None)
            if label_col and val_col:
                local = (
                    df[[label_col, val_col]]
                    .rename(columns={label_col: "Asset", val_col: "Value"})
                    .assign(Value=lambda x: pd.to_numeric(x["Value"], errors="coerce").fillna(0))
                )
                local = local.groupby("Asset", as_index=False)["Value"].sum()
                asset_rows.extend(local.to_dict("records"))

    st.subheader("Сводка")
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    if protocol_balance_rows:
        st.subheader("График баланса по протоколам")
        chart_df = pd.DataFrame(protocol_balance_rows)
        st.bar_chart(chart_df, x="Protocol", y="Value", color="Wallet")

    if asset_rows:
        st.subheader("Распределение активов (top-15)")
        assets_df = pd.DataFrame(asset_rows).groupby("Asset", as_index=False)["Value"].sum().sort_values("Value", ascending=False).head(15)
        st.bar_chart(assets_df, x="Asset", y="Value")

    st.subheader("Детали")
    for wallet, results in all_results.items():
        for result in results:
            show_protocol(result, wallet)


if __name__ == "__main__":
    main()
