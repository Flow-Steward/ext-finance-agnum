# Changelog

## 0.1.1 — 2026-11-17

- Find client, Find or create client and the create-if-new steps stop without writing when Agnum answers a search with an empty body or `null`: only a JSON list or Agnum's not-found answer shows that nothing is there. The plain search steps still show such an answer as no records with a warning.
- Create purchase receipt if new reads every page of receipts with the number before it creates one, and stops without writing when time runs out between pages or a page repeats.
- Create purchase receipt if new tells suppliers apart by id, code and company code only. A shared e-mail no longer makes two companies one supplier; keys that contradict each other stop the step.
- A write whose answer is too large for the step output keeps its identifiers and the fact that the change was made, and says what was cut.

## 0.1.0 — 2026-11-16

First release, built on the Agnum DB API specification 1.47.2.

- Every one of the 39 documented operations is a step: clients and their documents, products, prices and stock, sales documents with tracking and PDF, purchase receipts, customer returns, production and processing norms, and employees.
- Everyday steps: Test connection, Find client, Find or create client, Find product, Create sales document if new and Create purchase receipt if new.
- Amounts are sent as exact decimals. Searches that find nothing return an empty list.
- Replacing, changing and deleting norms take an explicit norm ID and say what they delete.
