# Changelog

## 0.1.0 — 2026-11-16

First release, built on the Agnum DB API specification 1.47.2.

- Every one of the 39 documented operations is a step: clients and their documents, products, prices and stock, sales documents with tracking and PDF, purchase receipts, customer returns, production and processing norms, and employees.
- Everyday steps: Test connection, Find client, Find or create client, Find product, Create sales document if new and Create purchase receipt if new.
- Amounts are sent as exact decimals. Searches that find nothing return an empty list.
- Replacing, changing and deleting norms take an explicit norm ID and say what they delete.
