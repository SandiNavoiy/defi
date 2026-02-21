"""DeFi dashboard for Arbitrum wallet positions.

Run:
    pip install streamlit requests pandas
    streamlit run main.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
import streamlit as st

ARBITRUM_CHAIN_LABEL = "arbitrum"


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
                if isinstance(error, dict):
                    messages.append(str(error.get("message", error)))
                else:
                    messages.append(str(error))
            raise ValueError(f"GraphQL query failed: {'; '.join(messages)}")
        return data

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def fetch_aave(client: ApiClient, wallet: str) -> ProtocolResult:
    endpoint = "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-arbitrum"
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


def fetch_gmx(client: ApiClient, wallet: str) -> ProtocolResult:
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


def fetch_curve(client: ApiClient, wallet: str) -> ProtocolResult:
    endpoint = "https://api.curve.finance/api/getUserLiquidityPools"
    try:
        payload = client.get_json(endpoint, params={"blockchainId": ARBITRUM_CHAIN_LABEL, "address": wallet})
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


def fetch_uniswap(client: ApiClient, wallet: str) -> ProtocolResult:
    endpoint = "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-arbitrum-one"
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


def show_protocol(result: ProtocolResult) -> None:
    with st.expander(result.protocol, expanded=True):
        if result.error:
            st.error(result.error)
            return

        if not result.rows:
            st.info("Данные не найдены")
            return

        st.dataframe(pd.DataFrame(result.rows), use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Arbitrum DeFi Dashboard", layout="wide")
    st.title("📊 Arbitrum wallet dashboard")
    st.caption("Aave / GMX / Curve / Uniswap")

    wallet = st.text_input("Адрес кошелька", placeholder="0x...")

    if not wallet:
        st.info("Введите адрес кошелька в сети Arbitrum")
        return

    if not (wallet.startswith("0x") and len(wallet) == 42):
        st.warning("Похоже, это не EVM-адрес")
        return

    client = ApiClient()

    with st.spinner("Собираю данные..."):
        results = [
            fetch_aave(client, wallet),
            fetch_gmx(client, wallet),
            fetch_curve(client, wallet),
            fetch_uniswap(client, wallet),
        ]

    totals = []
    for result in results:
        totals.append({"Protocol": result.protocol, "Rows": len(result.rows), "Error": bool(result.error)})

    st.subheader("Сводка")
    st.dataframe(pd.DataFrame(totals), use_container_width=True)

    st.subheader("Детали")
    for result in results:
        show_protocol(result)


if __name__ == "__main__":
    main()
