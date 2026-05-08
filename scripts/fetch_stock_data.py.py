import yfinance as yf
import pandas as pd
from datetime import datetime
import os
import time

# -----------------------------
# CONFIGURATION
# -----------------------------
STOCKS = [
    "AAPL", "TSLA", "GOOGL", "AMZN", "META",
    "NVDA", "JPM", "V", "UNH", "HD",
    "DIS", "PYPL", "BAC", "ADBE", "NFLX",
    "INTC", "CMCSA", "PFE", "KO", "PEP",

    # Market Indices
    "^GSPC",   # S&P 500
    "^IXIC",   # Nasdaq
    "^DJI",    # Dow Jones
    "^VIX",    # VIX
]

BASE_PATH = r"C:\Users\USER\Documents\DataVisualizationProject\Tableau\Stock Market Dashboard\File"
PORTFOLIO_FILE = BASE_PATH + r"\portfolio.csv"
OUTPUT_FILE    = BASE_PATH + r"\dashboard_data.xlsx"

os.makedirs(BASE_PATH, exist_ok=True)

# -----------------------------
# SAFE FETCH FUNCTION
# -----------------------------
def fetch_data(symbol, retries=3):
    for attempt in range(retries):
        try:
            # longer wait between retries
            if attempt > 0:
                time.sleep(5 * attempt)

            df = yf.download(
                symbol,
                period="1y",
                interval="1d",
                progress=False,
                auto_adjust=True
            )

            if df is None or df.empty:
                raise ValueError("Empty data")

            # flatten MultiIndex if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]

            df.columns = [col.lower() for col in df.columns]
            df.reset_index(inplace=True)
            df.rename(columns={"date": "datetime"}, inplace=True)

            return df

        except Exception as e:
            print(f"Retry {attempt+1}/{retries} failed for {symbol}: {e}")
            time.sleep(3)

    print(f"✗ Failed completely for {symbol}")
    return None

# -----------------------------
# FETCH MARKET DATA
# -----------------------------
all_data = []

for symbol in STOCKS:
    print(f"Fetching {symbol}...")
    df = fetch_data(symbol)
    if df is None:
        continue

    df.reset_index(inplace=True)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    df.columns = [col.lower() for col in df.columns]
    df.rename(columns={"date": "datetime"}, inplace=True)
    df["symbol"] = symbol.upper()

    all_data.append(
        df[["datetime", "symbol", "open", "high", "low", "close", "volume"]]
    )
    print(f"✓ {symbol} fetched ({len(df)} rows)")
    time.sleep(1)

# -----------------------------
# COMBINE DATA
# -----------------------------
if not all_data:
    print("❌ No market data fetched.")
    exit()

market_df = pd.concat(all_data, ignore_index=True)

# ✅ FIX 1 — strip timezone before any processing
market_df["datetime"] = pd.to_datetime(
    market_df["datetime"], utc=True
).dt.tz_convert(None)

market_df = market_df.sort_values(
    ["symbol", "datetime"]
).reset_index(drop=True)

print(f"\nTotal rows fetched: {len(market_df)}")

# -----------------------------
# LOAD PORTFOLIO
# -----------------------------
try:
    portfolio_df = pd.read_csv(PORTFOLIO_FILE)
    portfolio_df["symbol"] = portfolio_df["symbol"].str.upper()
    portfolio_df = portfolio_df[[
        "symbol", "shares", "avg_buy_price", "target_price", "stop_loss"
    ]]
    print(f"Portfolio loaded — {len(portfolio_df)} positions")
except Exception as e:
    print("Error loading portfolio.csv:", e)
    exit()

# -----------------------------
# MERGE DATA
# -----------------------------
df = pd.merge(market_df, portfolio_df, on="symbol", how="left")

# Fill missing safely
for col in ["close", "shares", "avg_buy_price", "target_price", "stop_loss"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

df["data_status"] = df["close"].apply(
    lambda c: "OK" if c > 0 else "NO MARKET DATA"
)

# -----------------------------
# IS INDEX FLAG
# -----------------------------
index_symbols = ["^GSPC", "^IXIC", "^DJI", "^VIX"]
df["is_index"] = df["symbol"].isin(index_symbols)

# -----------------------------
# DAILY METRICS
# -----------------------------
df["pct_change"] = (
    (df["close"] - df["open"]) / df["open"].replace(0, 1) * 100
).replace([float("inf"), -float("inf")], 0).round(2)

df["day_gain"] = (df["close"] - df["open"]).round(2)

df["direction"] = df["pct_change"].apply(
    lambda x: "Up" if x > 0 else ("Down" if x < 0 else "Flat")
)

# -----------------------------
# ✅ FIX 2 — WEEKLY CHANGE (robust method, no groupby apply)
# -----------------------------
df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)

# shift(5) within each symbol group
df["close_5d_ago"] = df.groupby("symbol")["close"].shift(5)

df["weekly_change"] = (
    (df["close"] - df["close_5d_ago"]) /
    df["close_5d_ago"].apply(lambda x: x if pd.notna(x) and x > 1 else None) * 100
).round(2)

df["weekly_change"] = df["weekly_change"].clip(-50, 50)

df["weekly_direction"] = df.apply(
    lambda row: (
        "Up"   if pd.notna(row["weekly_change"]) and row["weekly_change"] > 0
        else "Down" if pd.notna(row["weekly_change"]) and row["weekly_change"] < 0
        else "Flat"
    ),
    axis=1
)

# Drop helper column
df.drop(columns=["close_5d_ago"], inplace=True)

# -----------------------------
# PORTFOLIO METRICS
# -----------------------------
df["current_value"] = (df["close"] * df["shares"]).round(2)
df["profit_loss"]   = (
    (df["close"] - df["avg_buy_price"]) * df["shares"]
).round(2)
df["return_pct"] = (
    (df["close"] - df["avg_buy_price"]) /
    df["avg_buy_price"].replace(0, 1) * 100
).replace([float("inf"), -float("inf")], 0).round(2)

# -----------------------------
# SIGNAL
# -----------------------------
def generate_signal(row):
    if row["target_price"] > 0 and row["close"] >= row["target_price"]:
        return "TAKE PROFIT"
    elif row["stop_loss"] > 0 and row["close"] <= row["stop_loss"]:
        return "STOP LOSS"
    elif row["target_price"] > 0 and row["close"] >= row["target_price"] * 0.95:
        return "NEAR TARGET"
    elif row["stop_loss"] > 0 and row["close"] <= row["stop_loss"] * 1.05:
        return "NEAR STOP"
    else:
        return "HOLD"

df["signal"] = df.apply(generate_signal, axis=1)

# -----------------------------
# COMPANY NAMES
# -----------------------------
company_names = {
    "AAPL":  "Apple Inc.",
    "TSLA":  "Tesla Inc.",
    "GOOGL": "Alphabet Inc.",
    "AMZN":  "Amazon.com Inc.",
    "META":  "Meta Platforms Inc.",
    "NVDA":  "Nvidia Corporation",
    "JPM":   "JP Morgan Chase",
    "V":     "Visa Inc.",
    "UNH":   "UnitedHealth Group",
    "HD":    "Home Depot Inc.",
    "DIS":   "The Walt Disney Co.",
    "PYPL":  "PayPal Holdings",
    "BAC":   "Bank of America",
    "ADBE":  "Adobe Inc.",
    "NFLX":  "Netflix Inc.",
    "INTC":  "Intel Corporation",
    "CMCSA": "Comcast Corporation",
    "PFE":   "Pfizer Inc.",
    "KO":    "Coca-Cola Co.",
    "PEP":   "PepsiCo Inc.",
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI":  "Dow Jones",
    "^VIX":  "VIX",
}

df["company_name"] = df["symbol"].map(company_names).fillna(df["symbol"])

# -----------------------------
# TIMESTAMP
# -----------------------------
df["last_updated"] = datetime.now()

# -----------------------------
# FINAL STRUCTURE
# -----------------------------
df = df[[
    "datetime", "symbol", "company_name",
    "open", "high", "low", "close", "volume",
    "pct_change", "day_gain", "direction",
    "weekly_change", "weekly_direction",
    "shares", "avg_buy_price", "current_value",
    "profit_loss", "return_pct",
    "target_price", "stop_loss", "signal",
    "is_index", "data_status", "last_updated"
]]

# -----------------------------
# DEBUG CHECK
# -----------------------------
latest = df.sort_values("datetime").groupby("symbol").tail(1)
print("\nLatest weekly_change per symbol:")
print(
    latest[["symbol", "datetime", "close", "weekly_change", "weekly_direction"]]
    .sort_values("weekly_change")
    .to_string(index=False)
)

up   = (latest["weekly_direction"] == "Up").sum()
down = (latest["weekly_direction"] == "Down").sum()
flat = (latest["weekly_direction"] == "Flat").sum()
print(f"\nUp: {up} | Down: {down} | Flat: {flat}")

# -----------------------------
# SAVE
# -----------------------------
df.to_excel(OUTPUT_FILE, index=False)

print(f"\n✓ Saved {len(df)} rows to dashboard_data.xlsx")
print(f"Date range : {df['datetime'].min().date()} → {df['datetime'].max().date()}")
print(f"Symbols    : {df['symbol'].nunique()}")


import yfinance as yf
import pandas as pd
from datetime import datetime
import os
import time

# -----------------------------
# CONFIGURATION
# -----------------------------
STOCKS = [
    "AAPL", "TSLA", "GOOGL", "AMZN", "META",
    "NVDA", "JPM", "V", "UNH", "HD",
    "DIS", "PYPL", "BAC", "ADBE", "NFLX",
    "INTC", "CMCSA", "PFE", "KO", "PEP",

    # Market Indices
    "^GSPC",   # S&P 500
    "^IXIC",   # Nasdaq
    "^DJI",    # Dow Jones
    "^VIX",    # VIX
]

BASE_PATH = r"C:\Users\USER\Documents\DataVisualizationProject\Tableau\Stock Market Dashboard\File"
PORTFOLIO_FILE = BASE_PATH + r"\portfolio.csv"
OUTPUT_FILE    = BASE_PATH + r"\dashboard_data.xlsx"
NEWS_FILE      = BASE_PATH + r"\news_data.xlsx"

# Stocks only — no indices for news
STOCK_SYMBOLS = [
    "AAPL", "TSLA", "GOOGL", "AMZN", "META",
    "NVDA", "JPM", "V", "UNH", "HD",
    "DIS", "PYPL", "BAC", "ADBE", "NFLX",
    "INTC", "CMCSA", "PFE", "KO", "PEP",
]

os.makedirs(BASE_PATH, exist_ok=True)

# -----------------------------
# SAFE FETCH FUNCTION
# -----------------------------
def fetch_data(symbol, retries=3):
    for attempt in range(retries):
        try:
            stock = yf.Ticker(symbol)
            df = stock.history(period="1y", interval="1d")
            if df is None or df.empty:
                raise ValueError("Empty data")
            return df
        except Exception as e:
            print(f"Retry {attempt+1}/{retries} failed for {symbol}: {e}")
            time.sleep(2)
    print(f"✗ Failed completely for {symbol}")
    return None

# -----------------------------
# FETCH MARKET DATA
# -----------------------------
all_data = []

for symbol in STOCKS:
    print(f"Fetching {symbol}...")
    df = fetch_data(symbol)
    if df is None:
        continue

    df.reset_index(inplace=True)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    df.columns = [col.lower() for col in df.columns]
    df.rename(columns={"date": "datetime"}, inplace=True)
    df["symbol"] = symbol.upper()

    all_data.append(
        df[["datetime", "symbol", "open", "high", "low", "close", "volume"]]
    )
    print(f"✓ {symbol} fetched ({len(df)} rows)")
    time.sleep(1)

# -----------------------------
# COMBINE DATA
# -----------------------------
if not all_data:
    print("❌ No market data fetched.")
    exit()

market_df = pd.concat(all_data, ignore_index=True)

# ✅ FIX 1 — strip timezone before any processing
market_df["datetime"] = pd.to_datetime(
    market_df["datetime"], utc=True
).dt.tz_convert(None)

market_df = market_df.sort_values(
    ["symbol", "datetime"]
).reset_index(drop=True)

print(f"\nTotal rows fetched: {len(market_df)}")

# -----------------------------
# LOAD PORTFOLIO
# -----------------------------
try:
    portfolio_df = pd.read_csv(PORTFOLIO_FILE)
    portfolio_df["symbol"] = portfolio_df["symbol"].str.upper()
    portfolio_df = portfolio_df[[
        "symbol", "shares", "avg_buy_price", "target_price", "stop_loss"
    ]]
    print(f"Portfolio loaded — {len(portfolio_df)} positions")
except Exception as e:
    print("Error loading portfolio.csv:", e)
    exit()

# -----------------------------
# MERGE DATA
# -----------------------------
df = pd.merge(market_df, portfolio_df, on="symbol", how="left")

# Fill missing safely
for col in ["close", "shares", "avg_buy_price", "target_price", "stop_loss"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

df["data_status"] = df["close"].apply(
    lambda c: "OK" if c > 0 else "NO MARKET DATA"
)

# -----------------------------
# IS INDEX FLAG
# -----------------------------
index_symbols = ["^GSPC", "^IXIC", "^DJI", "^VIX"]
df["is_index"] = df["symbol"].isin(index_symbols)

# -----------------------------
# DAILY METRICS
# -----------------------------
df["pct_change"] = (
    (df["close"] - df["open"]) / df["open"].replace(0, 1) * 100
).replace([float("inf"), -float("inf")], 0).round(2)

df["day_gain"] = (df["close"] - df["open"]).round(2)

df["direction"] = df["pct_change"].apply(
    lambda x: "Up" if x > 0 else ("Down" if x < 0 else "Flat")
)

# -----------------------------
# ✅ FIX 2 — WEEKLY CHANGE (robust method, no groupby apply)
# -----------------------------
df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)

# shift(5) within each symbol group
df["close_5d_ago"] = df.groupby("symbol")["close"].shift(5)

df["weekly_change"] = (
    (df["close"] - df["close_5d_ago"]) /
    df["close_5d_ago"].replace(0, 1) * 100
).round(2)

df["weekly_direction"] = df.apply(
    lambda row: (
        "Up"   if pd.notna(row["weekly_change"]) and row["weekly_change"] > 0
        else "Down" if pd.notna(row["weekly_change"]) and row["weekly_change"] < 0
        else "Flat"
    ),
    axis=1
)

# Drop helper column
df.drop(columns=["close_5d_ago"], inplace=True)

# -----------------------------
# PORTFOLIO METRICS
# -----------------------------
df["current_value"] = (df["close"] * df["shares"]).round(2)
df["profit_loss"]   = (
    (df["close"] - df["avg_buy_price"]) * df["shares"]
).round(2)
df["return_pct"] = (
    (df["close"] - df["avg_buy_price"]) /
    df["avg_buy_price"].replace(0, 1) * 100
).replace([float("inf"), -float("inf")], 0).round(2)

# -----------------------------
# SIGNAL
# -----------------------------
def generate_signal(row):
    if row["target_price"] > 0 and row["close"] >= row["target_price"]:
        return "TAKE PROFIT"
    elif row["stop_loss"] > 0 and row["close"] <= row["stop_loss"]:
        return "STOP LOSS"
    elif row["target_price"] > 0 and row["close"] >= row["target_price"] * 0.95:
        return "NEAR TARGET"
    elif row["stop_loss"] > 0 and row["close"] <= row["stop_loss"] * 1.05:
        return "NEAR STOP"
    else:
        return "HOLD"

df["signal"] = df.apply(generate_signal, axis=1)

# -----------------------------
# COMPANY NAMES
# -----------------------------
company_names = {
    "AAPL":  "Apple Inc.",
    "TSLA":  "Tesla Inc.",
    "GOOGL": "Alphabet Inc.",
    "AMZN":  "Amazon.com Inc.",
    "META":  "Meta Platforms Inc.",
    "NVDA":  "Nvidia Corporation",
    "JPM":   "JP Morgan Chase",
    "V":     "Visa Inc.",
    "UNH":   "UnitedHealth Group",
    "HD":    "Home Depot Inc.",
    "DIS":   "The Walt Disney Co.",
    "PYPL":  "PayPal Holdings",
    "BAC":   "Bank of America",
    "ADBE":  "Adobe Inc.",
    "NFLX":  "Netflix Inc.",
    "INTC":  "Intel Corporation",
    "CMCSA": "Comcast Corporation",
    "PFE":   "Pfizer Inc.",
    "KO":    "Coca-Cola Co.",
    "PEP":   "PepsiCo Inc.",
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI":  "Dow Jones",
    "^VIX":  "VIX",
}

df["company_name"] = df["symbol"].map(company_names).fillna(df["symbol"])

# -----------------------------
# TIMESTAMP
# -----------------------------
df["last_updated"] = datetime.now()

# -----------------------------
# FINAL STRUCTURE
# -----------------------------
df = df[[
    "datetime", "symbol", "company_name",
    "open", "high", "low", "close", "volume",
    "pct_change", "day_gain", "direction",
    "weekly_change", "weekly_direction",
    "shares", "avg_buy_price", "current_value",
    "profit_loss", "return_pct",
    "target_price", "stop_loss", "signal",
    "is_index", "data_status", "last_updated"
]]


# -----------------------------
# SAVE
# -----------------------------
df.to_excel(OUTPUT_FILE, index=False)

print(f"\n✓ Saved {len(df)} rows to dashboard_data.xlsx")
print(f"Date range : {df['datetime'].min().date()} → {df['datetime'].max().date()}")
print(f"Symbols    : {df['symbol'].nunique()}")

# -----------------------------
# FETCH YAHOO FINANCE NEWS
# -----------------------------
print("\nFetching news from Yahoo Finance...")

def fetch_yahoo_news(symbols, max_per_symbol=3):
    all_news = []
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            news = ticker.news

            if not news:
                print(f"  No news for {symbol}")
                continue

            count = 0
            for article in news:
                if count >= max_per_symbol:
                    break

                # handle both old and new yfinance news format
                content = article.get("content", article)

                title = (
                    content.get("title", "")
                    or article.get("title", "")
                )
                source = (
                    content.get("provider", {}).get("displayName", "")
                    or article.get("publisher", "")
                    or "Yahoo Finance"
                )
                url = (
                    content.get("canonicalUrl", {}).get("url", "")
                    or article.get("link", "")
                )
                pub_time = (
                    content.get("pubDate", "")
                    or article.get("providerPublishTime", 0)
                )

                # parse published time
                try:
                    if isinstance(pub_time, (int, float)):
                        published = pd.to_datetime(pub_time, unit="s")
                    else:
                        published = pd.to_datetime(pub_time, utc=True).tz_localize(None)
                except Exception:
                    published = datetime.now()

                if title:
                    all_news.append({
                        "symbol":    symbol,
                        "headline":  title,
                        "source":    source,
                        "published": published,
                        "url":       url,
                    })
                    count += 1

            print(f"  ✓ {symbol} — {count} articles")
            time.sleep(0.5)

        except Exception as e:
            print(f"  ✗ News failed for {symbol}: {e}")

    return pd.DataFrame(all_news)

news_df = fetch_yahoo_news(STOCK_SYMBOLS)

if not news_df.empty:
    # remove duplicates
    news_df = news_df.drop_duplicates(subset=["headline"]).reset_index(drop=True)

    # ✅ FIXED: was accidentally split across two lines
    news_df = news_df.sort_values("published", ascending=False).reset_index(drop=True)

    # clean published column
    news_df["published"] = pd.to_datetime(
        news_df["published"], utc=True, errors="coerce"
    ).dt.tz_localize(None)

    # format time ago label for Tableau
    news_df["time_ago"] = news_df["published"].apply(
        lambda x: (
            f"{int((datetime.now() - x).total_seconds() // 3600)}h ago"
            if pd.notna(x) and (datetime.now() - x).total_seconds() < 86400
            else x.strftime("%b %d") if pd.notna(x)
            else ""
        )
    )

    news_df.to_excel(NEWS_FILE, index=False)
    print(f"\n✓ Saved {len(news_df)} news articles to news_data.xlsx")
    print(news_df[["symbol", "source", "time_ago", "headline"]].head(10).to_string(index=False))
else:
    print("No news articles fetched.")