import requests
import math
import json
import os

"""
LP TOOL — полуавтоматическая система работы с пулами ликвидности

Что делает:
1. Запрашивает текущую цену ETH и SOL
2. Создаёт позиции (если их нет)
3. Хранит entry и диапазоны
4. Считает IL (impermanent loss)
5. Показывает статус:
   - в диапазоне
   - выше
   - ниже
6. Даёт действие
7. Позволяет делать reset позиции

Как использовать:
1. Первый запуск → создаёт позиции
2. Дальше просто запускаешь и смотришь статус
3. Если пул выбило → закрываешь вручную (в DeFi)
4. Потом вызываешь reset_position()
"""

FILE = "positions.json"


# =========================
# API
# =========================

def get_price(coin_id):
    """Получаем цену с CoinGecko"""
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    return requests.get(url).json()[coin_id]["usd"]


# =========================
# МАТЕМАТИКА
# =========================

def calc_range(price, plus_pct, minus_pct):
    """Считаем диапазон от entry"""
    upper = price * (1 + plus_pct / 100)
    lower = price * (1 - minus_pct / 100)
    return lower, upper


def round_price(price, step, direction):
    """
    Округление под шаг (tick)

    direction:
    - down → вниз (для lower)
    - up   → вверх (для upper)
    """
    if direction == "down":
        return math.floor(price / step) * step
    return math.ceil(price / step) * step


def calc_il(entry_price, current_price):
    """
    Impermanent Loss

    формула:
    IL = 2*sqrt(r)/(1+r) - 1
    """
    r = current_price / entry_price
    return (2 * math.sqrt(r) / (1 + r) - 1) * 100


# =========================
# ХРАНЕНИЕ
# =========================

def load_positions():
    """Загружаем позиции из файла"""
    if not os.path.exists(FILE):
        return {}
    with open(FILE, "r") as f:
        return json.load(f)


def save_positions(data):
    """Сохраняем позиции"""
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)


# =========================
# СОЗДАНИЕ ПОЗИЦИИ
# =========================

def create_position(price, plus, minus, step):
    """Создаём новую позицию"""
    low, high = calc_range(price, plus, minus)

    low = round_price(low, step, "down")
    high = round_price(high, step, "up")

    return {
        "entry": price,
        "low": low,
        "high": high
    }


# =========================
# АНАЛИЗ ПОЗИЦИИ
# =========================

def check_position(name, pos, current_price):
    """
    Проверяем позицию и даём действие
    """
    low = pos["low"]
    high = pos["high"]
    entry = pos["entry"]

    # статус
    if current_price < low:
        status = "ниже диапазона"
    elif current_price > high:
        status = "выше диапазона"
    else:
        status = "в диапазоне"

    il = calc_il(entry, current_price)
    broken = status != "в диапазоне"

    print(f"{name}")
    print(f"  Entry: {entry:.2f}")
    print(f"  Сейчас: {current_price:.2f}")
    print(f"  Диапазон: {low:.2f} — {high:.2f}")
    print(f"  Статус: {status}")
    if abs(il) < 0.01:
        print(f"  IL: {il:.5f}%")
    else:
        print(f"  IL: {il:.2f}%")

    print("  Действие:")

    if not broken:
        print("    → ничего не делать (фарм комиссий)")

    else:
        print("    ⚠ пул вне диапазона")

        if status == "выше диапазона":
            print("    → рынок вырос")
            print("    → позиция в стейблах")
            print("    → закрыть и открыть выше")

        else:
            print("    → рынок упал")
            print("    → позиция в активе")

            if il < -5:
                print("    → IL заметный")
                print("    → можно перезайти ниже")
            else:
                print("    → можно держать (накопление)")

    print()
    return broken


# =========================
# RESET ПОЗИЦИИ
# =========================

def reset_position(positions, key, current_price, plus, minus, step):
    """
    Пересоздание позиции

    Когда использовать:
    - вышел вверх → всегда
    - вниз → по ситуации
    """
    print(f"\n[RESET] {key}")

    low, high = calc_range(current_price, plus, minus)

    low = round_price(low, step, "down")
    high = round_price(high, step, "up")

    positions[key] = {
        "entry": current_price,
        "low": low,
        "high": high
    }

    print(f"  Новый entry: {current_price:.2f}")
    print(f"  Новый диапазон: {low:.2f} — {high:.2f}\n")


# =========================
# ОСНОВНОЙ СЦЕНАРИЙ
# =========================

def main():
    positions = load_positions()

    eth_price = get_price("ethereum")
    sol_price = get_price("solana")

    # ===== СОЗДАНИЕ (если нет) =====

    if "eth_narrow" not in positions:
        positions["eth_narrow"] = create_position(eth_price, 10, 8, 5)

    if "eth_wide" not in positions:
        positions["eth_wide"] = create_position(eth_price, 60, 35, 5)

    if "sol_narrow" not in positions:
        positions["sol_narrow"] = create_position(sol_price, 15, 15, 0.5)

    if "sol_wide" not in positions:
        positions["sol_wide"] = create_position(sol_price, 60, 40, 0.5)

    save_positions(positions)

    # ===== ПРОВЕРКА =====

    print("\n========== ETH ==========\n")
    check_position("ETH узкий", positions["eth_narrow"], eth_price)
    check_position("ETH длинный", positions["eth_wide"], eth_price)

    print("\n========== SOL ==========\n")
    check_position("SOL узкий", positions["sol_narrow"], sol_price)
    check_position("SOL длинный", positions["sol_wide"], sol_price)


def main():
    positions = load_positions()

    eth_price = get_price("ethereum")
    sol_price = get_price("solana")

    # создание позиций (если нет)
    if "eth_narrow" not in positions:
        positions["eth_narrow"] = create_position(eth_price, 10, 8, 5)

    if "eth_wide" not in positions:
        positions["eth_wide"] = create_position(eth_price, 60, 35, 5)

    if "sol_narrow" not in positions:
        positions["sol_narrow"] = create_position(sol_price, 15, 15, 0.5)

    if "sol_wide" not in positions:
        positions["sol_wide"] = create_position(sol_price, 60, 40, 0.5)

    save_positions(positions)

    # ===== МЕНЮ =====
    print("\n===== МЕНЮ =====")
    print("1 — показать статус")
    print("2 — reset eth_narrow")
    print("3 — reset eth_wide")
    print("4 — reset sol_narrow")
    print("5 — reset sol_wide")
    print("0 — выход")

    choice = input("\nВыбор: ")

    # ===== ДЕЙСТВИЯ =====

    if choice == "1":
        print("\n========== ETH ==========\n")
        check_position("ETH узкий", positions["eth_narrow"], eth_price)
        check_position("ETH длинный", positions["eth_wide"], eth_price)

        print("\n========== SOL ==========\n")
        check_position("SOL узкий", positions["sol_narrow"], sol_price)
        check_position("SOL длинный", positions["sol_wide"], sol_price)

    elif choice == "2":
        reset_position(positions, "eth_narrow", eth_price, 10, 8, 5)

    elif choice == "3":
        reset_position(positions, "eth_wide", eth_price, 60, 35, 5)

    elif choice == "4":
        reset_position(positions, "sol_narrow", sol_price, 15, 15, 0.5)

    elif choice == "5":
        reset_position(positions, "sol_wide", sol_price, 60, 40, 0.5)

    elif choice == "0":
        print("Выход")
        return

    else:
        print("Неверный выбор")

    # сохраняем после любого действия
    save_positions(positions)




if __name__ == "__main__":
    main()