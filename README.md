# Agnum for Flow Steward

Connect Flow Steward workflows to [Agnum](https://www.agnum.lt/), the Lithuanian business
management and accounting system, through its **Agnum DB API** (specification 1.47.2).

## What it does

- **Everyday steps:** Test connection, Find client, Find or create client, Find product, Create
  sales document if new, Create purchase receipt if new, Save sales document PDF.
- **Every documented operation as a step (39):** clients and their documents, products, prices
  and stock, sales documents and tracking, purchase receipts, customer returns, production and
  processing norms, employees and working time.

Each step makes one documented call (the everyday steps two). Writes are sent once and never
retried; test runs never send. Amounts are sent as exact decimals. Searches that find nothing
return an empty list.

Find or create client and the create-if-new steps look before they write: every record Agnum
returns is checked again, and an ambiguous or untrustworthy answer stops the step without
writing anything. An empty answer is never taken as "nothing there"; the receipt check reads
every page of the number.

A write whose answer is too large for the step output keeps its identifiers (id, code,
numbers) and says what was cut; the change itself is reported as made.

Replacing, changing and deleting a production or processing norm take an explicit norm ID; they
delete stored rows and cannot be undone.

## Setup

1. Get the Agnum DB API address (the Swagger page's address without `/swagger-ui/index.html`, for
   example `https://server.example.lt/agnumAPIv2`) and an API key with the rights your workflows
   need from your Agnum administrator or provider.
2. Add them to a project connection and run **Test connection** (it needs the right `READ_KL`).

The **Setup Guide** page lists the rights each area needs.

## Development

The steps are generated from the pinned specification:

```
python3 tests/reference/extract_reference.py   # needs tests/reference/sources/agnum-api-docs.json
python3 tests/reference/build_contracts.py
python -m pytest tests
```

## License and support

Issues: <https://github.com/Flow-Steward/ext-finance-agnum/issues>. Agnum and its marks belong to
their owner; see `BRANDING.md`.
