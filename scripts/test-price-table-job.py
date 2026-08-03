#!/usr/bin/env python3
import requests
import sys
import json
from pathlib import Path

ORCHESTRATOR_URL = "http://localhost:8000"

# Upload your real 2025 NOK price table
with open("prices-2025-nok.csv", "rb") as f:
    upload = requests.post(f"{ORCHESTRATOR_URL}/uploads/csv", files={"file": f})
    prices_path = upload.json()["path"]

# Create job for your real XRPL wallet
job = requests.post(f"{ORCHESTRATOR_URL}/jobs", json={
    "xrpl_accounts": ["rYourMainWallet"],
    "tax_year": 2025,
    "country": "norway",
    "valuation_mode": "price_table",
    "csv_prices_path": prices_path,
    "case_name": "2025 NO - price table test"
}).json()

print(f"Created job {job['id']}")

# Dry run first
requests.post(f"{ORCHESTRATOR_URL}/jobs/{job['id']}/start")
print("✅ Dry run logged commands to output/{job_id}/execution.log")

# Real run (uncomment when ready)
# requests.post(f"{ORCHESTRATOR_URL}/jobs/{job['id']}/start")
# print("✅ Reports ready - download from /jobs/{job_id}/files/")
