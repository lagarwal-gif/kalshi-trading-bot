import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY_ID = os.environ["KALSHI_API_KEY_ID"]
KALSHI_PRIVATE_KEY_PATH = os.environ["KALSHI_PRIVATE_KEY_PATH"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]

USE_SANDBOX = os.getenv("USE_SANDBOX", "true").lower() == "true"
BUDGET_CAP = float(os.getenv("BUDGET_CAP", "50.0"))
MAX_SINGLE_TRADE = float(os.getenv("MAX_SINGLE_TRADE", "10.0"))
CYCLE_INTERVAL_MINUTES = int(os.getenv("CYCLE_INTERVAL_MINUTES", "30"))

KALSHI_SANDBOX_HOST = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_HOST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_HOST = KALSHI_SANDBOX_HOST if USE_SANDBOX else KALSHI_PROD_HOST
