# Data Processing Agreement Template

**Note:** This template is provided for reference. taxspine-orchestrator is a
self-hosted personal tax tool. As controller and data subject are the same
person, a formal DPA between parties is not required for personal use. This
template covers the third-party data processors the system calls and the
applicable data-minimisation posture for each.

---

## Third-Party Data Processors

The following external services are called by taxspine-orchestrator. No
personal transaction data, names, or tax identification numbers are transmitted
to any of these services. Only asset symbols, date ranges, and anonymous
account addresses are sent.

### 1. Kraken OHLC API

| Field | Value |
|---|---|
| Processor | Payward Ltd. (Kraken) |
| Purpose | Fetch historical OHLC price data for BTC, ETH, XRP, ADA, LTC in USD |
| Data sent | Asset symbol (e.g. "XRPUSD"), date range |
| Personal data | None |
| Legal basis | Article 6(1)(c) — price data required for lawful tax computation |
| Data retention by processor | Subject to Kraken's own privacy policy |
| DPA required | No — no personal data transmitted |

### 2. Norges Bank API (data.norges-bank.no)

| Field | Value |
|---|---|
| Processor | Norges Bank (Norwegian central bank) |
| Purpose | Fetch USD/NOK exchange rates for NOK valuation |
| Data sent | Currency pair (USD/NOK), date |
| Personal data | None |
| Legal basis | Article 6(1)(c) — exchange rates required for lawful tax computation |
| Data retention by processor | Public dataset; no personal data involved |
| DPA required | No — public API, no personal data transmitted |

### 3. OnTheDEX API (onthedex.com)

| Field | Value |
|---|---|
| Processor | OnTheDEX |
| Purpose | Fetch XRPL IOU token OHLC prices (Tier 2 price source) |
| Data sent | XRPL asset spec in format `SYMBOL.rIssuer` |
| Personal data | XRPL issuer address (pseudonymous, not linked to identity) |
| Legal basis | Article 6(1)(c) — required for valuation of XRPL-native tokens |
| Data retention by processor | Subject to OnTheDEX terms of service |
| DPA required | No — issuer addresses are pseudonymous public blockchain data |

### 4. XRPL.to API (xrpl.to)

| Field | Value |
|---|---|
| Processor | XRPL.to |
| Purpose | Fallback XRPL IOU token price lookup when OnTheDEX has no data |
| Data sent | XRPL asset MD5 identifier |
| Personal data | None — MD5 of asset spec, no wallet addresses |
| Legal basis | Article 6(1)(c) — valuation fallback for XRPL tokens |
| DPA required | No |

### 5. XRPL Node (xrplcluster.com)

| Field | Value |
|---|---|
| Processor | XRPL Labs / xrplcluster.com |
| Purpose | Fetch on-chain transaction history for registered XRPL wallet addresses |
| Data sent | XRPL r-address (wallet address) |
| Personal data | XRPL addresses are pseudonymous; linkable to identity if the address is publicly associated with the owner |
| Legal basis | Article 6(1)(c) — transaction history required for FIFO tax computation |
| Data retention by processor | XRPL ledger is public and immutable; xrplcluster.com does not store submitted addresses beyond the API response |
| DPA required | No — querying a public ledger; no personal data submitted that is not already public |
| Mitigation | XRPL addresses are redacted from internal job logs (`_XRPL_ADDR_RE` redaction in `services.py`) |

---

## Data Minimisation Practices

The system enforces the following data-minimisation controls:

1. **No transmission of transaction history to third parties.** Price APIs
   receive only asset symbols and date ranges — never amounts, counterparties,
   or wallet balances.

2. **XRPL address redaction in logs.** All XRPL wallet addresses in job
   execution logs are replaced with `[XRPL-ADDRESS]` before writing to disk
   (`_redact_xrpl_addresses()` in `services.py`).

3. **Local processing.** All tax computation (FIFO, gain/loss, formue) is
   performed locally on the NAS. No tax figures are transmitted externally.

4. **Minimal data retained in databases.** SQLite stores contain computed
   tax artefacts, not raw transaction data beyond what is necessary for
   FIFO lot continuity.

---

## Controller Information

| Field | Value |
|---|---|
| Controller | System owner (same person as data subject) |
| Lawful basis | Article 6(1)(c) legal obligation + 6(1)(f) legitimate interests |
| Full privacy notice | `PRIVACY.md` in repository root |

---

*Last reviewed: 2026-03-23*
