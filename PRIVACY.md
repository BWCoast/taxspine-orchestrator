# Privacy and Data Processing — taxspine-orchestrator

## What this system is

taxspine-orchestrator is a private, self-hosted tax calculation system used
exclusively by its owner for personal Norwegian and UK cryptocurrency tax
reporting. It is not a service offered to third parties.

---

## GDPR Lawful Basis (Article 6)

Processing of personal financial data in this system is grounded in:

**Article 6(1)(c) — Legal obligation**

The controller (system owner) is subject to Norwegian tax law (Ligningsloven /
Skatteforvaltningsloven) which requires accurate reporting of gains, losses,
income, and wealth from virtual currencies to Skatteetaten via form RF-1159.
Processing transaction history, computing FIFO cost basis, and producing RF-1159
export documents is necessary to comply with this legal obligation.

**Article 6(1)(f) — Legitimate interests** (secondary basis)

Where processing extends beyond the minimum required for tax compliance
(e.g., historical lot snapshots for multi-year carry-forward, audit log
retention), the controller's legitimate interest in maintaining accurate
multi-year tax records justifies the processing. This does not override
data subject rights; the controller and data subject are the same person.

---

## Categories of data processed

| Category | Examples | Basis |
|---|---|---|
| Transaction history | Buy/sell/transfer events, timestamps, amounts | Legal obligation |
| Wallet addresses | XRPL r-addresses | Legal obligation |
| Exchange account identifiers | Source labels in CSV imports | Legal obligation |
| Computed tax figures | Gains, losses, wealth (formue) in NOK | Legal obligation |
| FIFO lot snapshots | Carry-forward lot state by tax year | Legitimate interests |
| Job history | Audit log of pipeline runs | Legitimate interests |

---

## Data flows to third parties

The system makes outbound requests to the following services for price data.
No personal transaction data is transmitted — only asset symbols and dates.

| Service | Data sent | Purpose |
|---|---|---|
| Kraken OHLC API | Asset symbol, date range | NOK price lookup (Tier 1) |
| Norges Bank API | Currency pair (USD/NOK), date | Exchange rate lookup |
| OnTheDEX API | XRPL asset spec (SYMBOL.rIssuer), date | XRPL IOU price (Tier 2) |
| XRPL.to API | XRPL asset MD5 | XRPL IOU price fallback (Tier 2) |
| XRPL node (xrplcluster.com) | XRPL account address | On-chain transaction fetch |

None of these services receive names, tax identification numbers, or full
transaction history.

---

## Retention

Tax records must be retained for **5 years** under Norsk Ligningsloven §14-6.
The NAS backup policy is configured to meet this retention period.

Job output files (HTML reports, RF-1159 JSON) may be deleted after the
relevant tax return is filed and any appeal period has elapsed, but lot
snapshots should be retained for the full 5-year period to support
carry-forward FIFO continuity.

---

## Data subject rights

As the controller and sole data subject are the same person, data subject
rights (access, rectification, erasure, portability) are exercised directly:

- **Access / portability**: Use `GET /lots/{year}/carry-forward` and the
  job output files to export structured data.
- **Erasure**: Use `DELETE /audit` to remove job records. SQLite databases
  can be deleted from the NAS data directory.
- **Rectification**: Re-run the pipeline with corrected input CSV files.

---

*Last reviewed: 2026-03-23*
