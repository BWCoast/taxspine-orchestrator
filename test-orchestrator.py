import requests
from pathlib import Path

URL = "http://localhost:8001"
WALLET = "rM9ih3NUkZQm386zvvKNNkF91P3dZDpuNY"

# Create test prices
prices = """date,asset_id,fiat_currency,price_fiat,source
2025-01-01,XRP,NOK,7.50,kraken
2025-01-01,BTC,NOK,850000,kraken"""
Path("prices.csv").write_text(prices)

print("📤 Upload prices...")
with open("prices.csv", "rb") as f:
    upload = requests.post(f"{URL}/uploads/csv", files={"file": f})
prices_path = upload.json()["path"]
print(f"   → {prices_path}")

print("🔨 Create job...")
job = requests.post(f"{URL}/jobs", json={
    "xrpl_accounts": [WALLET],
    "tax_year": 2025,
    "country": "norway",
    "valuation_mode": "price_table",
    "csv_prices_path": str(prices_path),
    "case_name": "Full stack test"
}).json()
print(f"   → Job: {job['id']}")

print("🔍 Dry run...")
requests.post(f"{URL}/jobs/{job['id']}/start")
print(f"✅ Log: http://localhost:8001/jobs/{job['id']}/files/log")
