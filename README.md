# Agnum integration for Flow Steward

<img src="icon.png" width="96" alt="Agnum integration for Flow Steward">

[![CI](https://img.shields.io/github/actions/workflow/status/Flow-Steward/ext-finance-agnum/ci.yml?branch=main&label=CI)](https://github.com/Flow-Steward/ext-finance-agnum/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Flow-Steward/ext-finance-agnum)](https://github.com/Flow-Steward/ext-finance-agnum/releases/latest)

Automate Agnum with Flow Steward workflows and AI agents: create sales documents and purchase receipts, sync clients, stock and prices — without writing code.

This Flow Steward extension is an ERP and accounting integration for companies in Lithuania that
work with Agnum. It connects Agnum to workflow automation through the Agnum DB API, so sales,
purchases, warehouse and production data move without manual entry.

## What is this

Agnum is a Lithuanian business management and accounting system by Elit sistema, covering sales,
purchases, warehouse and production. This extension is a connector: it adds ready workflow steps
to Flow Steward, and the same steps are available as tools for AI agents. Learn more about Flow
Steward at [docs.flowsteward.org](https://docs.flowsteward.org).

## What you can automate

- Create sales invoices, orders or offers in Agnum from orders in your shop — the client is found or created, no retyping.
- Register supplier deliveries as purchase receipts, once per supplier and document number.
- Send stock quantities from Agnum to your online shop or marketplace.
- Read and update product prices from your price list.
- Save the PDF of a sales document, ready to e-mail to the customer or archive.
- Record customer returns and follow the tracking of sales documents.
- Record employee working time from your time-tracking system.

## Why teams use it

- **No duplicate documents.** The create-if-new steps look the document number up first and stop, writing nothing, when Agnum already has it; an answer that cannot be trusted stops the step too.
- **Exact amounts.** Amounts are sent as exact decimals, never rounded on the way.
- **A change is never sent twice.** Writes are sent one at a time and never retried.
- **A test run changes nothing.** Every write is skipped in test runs, so you can try a workflow safely.
- **The whole API.** Each of the 39 operations of the Agnum DB API has its own step.
- **The key stays secret.** It is stored encrypted and kept out of what the steps return.

## What's inside

Everyday steps:

- **Test connection** — checks the address and the key with one search.
- **Find client**, **Find or create client** — one exact match by code, company code or e-mail; a near-duplicate stops the step.
- **Find product** — by code and barcode, optionally in one warehouse.
- **Create sales document if new** — an invoice, order or offer, once per document number.
- **Create purchase receipt if new** — once per supplier and document number.
- **Save sales document PDF** — the PDF as a workflow file.

All 39 Agnum DB API operations are available as steps (45 steps in total): clients and their payment documents, products, prices and stock, sales documents and tracking, purchase receipts, customer returns, production and processing norms, employees and working time.

## Get started

1. In Flow Steward, open **Extensions → Catalog** and install **Agnum**.
2. Ask your Agnum administrator or provider for the Agnum DB API address and an API key. In the extension's **Connection** page, add a connection with the address (the Swagger page's address without `/swagger-ui/index.html`, for example `https://server.example.lt/agnumAPIv2`) and the key.
3. Press **Test connection**.
4. Build your first workflow, for example **Find or create client** followed by **Create sales document if new**.

## Requirements

- An Agnum DB API reachable over https under a public host name with a valid certificate. The steps follow the Agnum DB API specification 1.47.2.
- An API key with the rights your steps need: READ_ALL, SAVE_ALL, EDIT_ALL and DEL_ALL cover everything, or give only the rights of each area (for example READ_KL and SAVE_KL for clients, READ_PRK_QTY for stock). **Test connection** needs READ_KL or READ_ALL.
- Agnum changes only the price levels enabled in its API settings.
- Replacing, changing or deleting a production or processing norm deletes stored rows and cannot be undone; these steps take an explicit norm ID.
- Flow Steward 0.9.16 or later.

## FAQ

**How do I create sales invoices in Agnum automatically from an online shop?**
Use **Find or create client** and then **Create sales document if new** with the order number as the document number. A repeated order with the same number stops the step instead of creating a second document.

**Where do I find the Agnum API address?**
Your Agnum administrator or provider gives you the Swagger page of the Agnum DB API. The address for the connection is that page's address without `/swagger-ui/index.html`.

**Which rights does the Agnum API key need?**
Each step says which right it needs; a step whose right is missing is refused and changes nothing. The **Setup Guide** page lists the rights of every area.

**Can I sync stock and prices from Agnum to my e-shop?**
Yes. **Get stock quantities** and **Search product prices** read them, and **Update product prices** writes new prices back to Agnum.

**Why did a price update leave some prices unchanged?**
Agnum changes only the price levels enabled in its API settings and ignores the others; the step reminds you of this in its warnings.

## In your language

Agnum integracija su Flow Steward per Agnum DB API automatizuoja pardavimus, pirkimus, sandėlį ir gamybą. Pardavimo dokumentai, pirkimo dokumentai, klientai, likučiai ir kainos tvarkomi be rankinio įvedimo. Tai Flow Steward plėtinys Lietuvos įmonėms, dirbančioms su Agnum.

## Links

- [Flow Steward documentation](https://docs.flowsteward.org)
- [Flow Steward extension catalog](https://github.com/Flow-Steward/extensions)
- [Issues](https://github.com/Flow-Steward/ext-finance-agnum/issues)
- [Agnum DB API specification](https://srv1.elit.lt/agnumAPIv2/v3/api-docs) · [Agnum](https://www.agnum.lt/)

## For developers

The steps are generated from the pinned specification; do not edit the contracts by hand.

```bash
python3 tests/reference/extract_reference.py   # needs tests/reference/sources/agnum-api-docs.json
python3 tests/reference/build_contracts.py     # regenerate contracts and pages
```

From a Flow Steward checkout:

```bash
./flow-steward extensions test flowsteward.agnum --root <parent>
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root <abs path>
```

Agnum and its marks belong to their owner; see [BRANDING.md](BRANDING.md).
