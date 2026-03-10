# Tiny CSV with BTC, XRP, ETH prices for a few days in 2025
prices = [
    ["date", "asset_id", "fiat_currency", "price_fiat", "source"],
    ["2025-01-01", "BTC", "NOK", "850000", "kraken"],
    ["2025-01-01", "XRP", "NOK", "7.5", "kraken"],
    ["2025-01-02", "BTC", "NOK", "860000", "kraken"],
]
Path("prices-2025-nok.csv").write_text("\n".join(",".join(row) for row in prices))
