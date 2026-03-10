#!/usr/bin/env python3
import requests, json
from pathlib import Path

URL = "http://localhost:8000"
YOUR_XRPL_WALLET = "rYourActualWalletHere"  # ← YOU FILL THIS

# 1. Start orchestrator if not running
print("🟢 Starting orchestrator...")
# Run: uvicorn taxspine_orchestrator.main:app --reload

# 2. Create minimal NOK price table
prices_csv = """date,asset_id,fiat_currency,price_fiat,source
2025-01-01,XRP,NOK,7.50,kraken
2025-01-01,BTC,NOK,850000,kraken
2025-01-15,XRP,NOK,8.20,kraken"""
Path("test-prices.csv").write_text(prices_csv)

# 3. Upload price table
print("📤 Uploading price table...")
with open("test-prices.csv", "rb") as f:
    upload = requests.post(f"{URL}/uploads/csv", files={"file": f})
prices_path = upload.json()["path"]
print(f"   → {prices_path}")

# 4. Create job
print("🔨 Creating job...")
job_data = {
    "xrpl_accounts": [YOUR_XRPL_WALLET],
    "tax_year": 2025,
    "country": "norway",
    "valuation_mode": "price_table",
    "csv_prices_path": prices_path,
    "case_name": "Full stack test - price table"
}
job = requests.post(f"{URL}/jobs", json=job_data).json()
print(f"   → Job {job['id']}")

# 5. Dry run first (check the command)
print("🔍 Dry run...")
requests.post(f"{URL}/jobs/{job['id']}/start")
print("   → Check output/{job_id}/execution.log for exact CLI command")

# 6. Real run (uncomment when dry-run looks good)
# print("🚀 Real run...")
# requests.post(f"{URL}/jobs/{job['id']}/start")
# print("   → Download: curl http://localhost:8000/jobs/{job['id']}/files/gains -O")

print("\n✅ Ready to test! Check dry-run log, then uncomment real run.")
