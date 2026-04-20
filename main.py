from __future__ import annotations

from flask import Flask, render_template_string
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from lending import create_app as create_lending_app
from pool_uni import create_app as create_pool_app

HOME_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DeFi Hub</title>
  <style>
    :root {
      --bg: #f5f8ff;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #dbe4f0;
      --accent: #2563eb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--text);
      background: linear-gradient(180deg, #ebf2ff 0%, var(--bg) 65%);
    }
    .wrap {
      max-width: 900px;
      margin: 0 auto;
      padding: 28px 16px 40px;
    }
    h1 { margin: 0 0 8px; font-size: 34px; }
    p { margin: 0; color: var(--muted); }
    .grid {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }
    .title { margin: 0 0 6px; font-size: 18px; }
    .desc { margin: 0 0 12px; color: var(--muted); }
    .btn {
      display: inline-block;
      text-decoration: none;
      border-radius: 10px;
      padding: 8px 12px;
      background: var(--accent);
      color: #fff;
      font-weight: 600;
    }
    code {
      background: #f3f4f6;
      border-radius: 6px;
      padding: 2px 6px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>DeFi Hub</h1>
    <p>Единая точка входа для модулей. Базовые префиксы: <code>/pool</code> и <code>/lending</code>.</p>
    <div class="grid">
      <section class="card">
        <h2 class="title">LP Tool</h2>
        <p class="desc">Мониторинг диапазонов ликвидности (ETH/SOL).</p>
        <a class="btn" href="/pool/">Открыть /pool</a>
      </section>
      <section class="card">
        <h2 class="title">Lending Rates</h2>
        <p class="desc">Ставки вкладов и займов по Aave, Fluid, Compound.</p>
        <a class="btn" href="/lending/">Открыть /lending</a>
      </section>
    </div>
  </div>
</body>
</html>
"""


def create_main_app() -> Flask:
    """Создает корневое Flask-приложение и подключает модульные подприложения."""
    app = Flask(__name__)

    @app.get("/")
    def home() -> str:
        """Главная страница-хаб с переходами в подприложения."""
        return render_template_string(HOME_TEMPLATE)

    return app


pool_app = create_pool_app()
lending_app = create_lending_app()
app = create_main_app()

# Единый WSGI роутер: каждое подприложение живет под своим URL-префиксом.
app.wsgi_app = DispatcherMiddleware(
    app.wsgi_app,
    {
        "/pool": pool_app,
        "/lending": lending_app,
    },
)


if __name__ == "__main__":
    app.run(debug=True)
