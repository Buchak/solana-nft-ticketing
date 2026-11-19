# NFT Ticketing Platform

NFT Ticketing Platform is a diploma project for event ticketing in which a ticket
is represented as an NFT on the Solana network. The project combines a web
application for users and organizers, a Django API, an Anchor smart contract,
and an ESP32 QR-code scanner.

The system allows an organizer to create an event and ticket types, accept SOL
payments, issue an NFT ticket to a wallet owner, and validate the ticket at the
event entrance.

## Key Features

- Authentication through a signed Phantom Wallet message;
- browsing active events and searching by title or description;
- event creation and poster upload;
- configuring multiple ticket types with price and supply limits;
- ticket purchase with partial transaction signing in the browser;
- storing posters and NFT metadata through Pinata/IPFS;
- a personal account with NFT tickets and QR codes;
- refunds and event cancellation;
- automatic or manual event finalization;
- ESP32-based ticket scan validation through the Django API;
- dark and light interface themes;
- synchronization of event and transaction states with Solana.

## Project Architecture

```text
Phantom Wallet
      |
      v
React + Vite frontend -- REST API --> Django backend
                                          |
                       Pinata/IPFS        |
                            |             |
                            v             v
                         Posters       Solana RPC / Anchor
                                       |
                                       v
                                  NFT Ticket Program

ESP32 QR scanner -- shared demo token --> Django scan API
```

### Components

| Component                      | Technologies                                                    | Purpose                                                                    |
| ------------------------------ | --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `frontend/`                    | React 19, Vite, Solana Web3.js                                  | User interface, transaction signing, Phantom authentication                |
| `backend/nft_tickets_backend/` | Django 5, Django REST Framework, PostgreSQL/SQLite, APScheduler | API, database, authentication, IPFS publishing, blockchain synchronization |
| `nft-tickets/`                 | Anchor 1.1.2, Rust, MPL Core                                    | On-chain event, ticket type, sale, refund, and scan logic                  |
| `ESP-32/ESP-32.ino`            | Arduino for ESP32, HTTPClient                                   | QR reading and submission of short-lived tokens to the API                 |

Metaplex collection and ticket asset private keys are created in browser memory.
They are not sent to the backend. The protected backend authority keypair is used
only by the server for administrative on-chain instructions.

## Directory Structure

```text
.
├── frontend/                   # React/Vite client
├── backend/                    # Django API and local settings
├── nft-tickets/                # Anchor/Rust smart contract
├── ESP-32/                     # Demonstration scanner firmware
└── test-ledger-with-core/      # Local Solana validator data
```

## Requirements

- Node.js and npm;
- Python 3.11+;
- PostgreSQL for the main setup or SQLite for local experiments;
- Rust through rustup and the required toolchain;
- Solana CLI and Anchor CLI;
- a browser with Phantom Wallet;
- an ESP32 and a UART-compatible QR scanner for hardware validation.

## Local Setup

### 1. Environment variables

The backend is configured through `backend/.env`. A template is available at
`backend/.env.example`:

```bash
cp backend/.env.example backend/.env
```

The frontend is configured through `frontend/.env.local`:

```bash
cp frontend/.env.example frontend/.env.local
```

Replace all example secrets with secure local values. Do not store private keys,
the Pinata JWT, or the scanner token in frontend code.

Key backend settings:

- `DJANGO_SECRET_KEY` — Django secret;
- `DB_*` — PostgreSQL or SQLite parameters;
- `SOLANA_RPC_URL` — validator RPC URL;
- `SOLANA_PROGRAM_ID` — deployed smart-contract address;
- `SOLANA_BACKEND_KEYPAIR_PATH` — local backend authority keypair;
- `PINATA_JWT` and `PINATA_GATEWAY_URL` — NFT data and poster publishing;
- `TICKET_QR_VALIDITY_SECONDS` — lifetime of a one-time QR token;
- `TICKET_SCANNER_API_TOKEN` — shared secret for the demonstration ESP32 scanner.

### 2. Solana local validator

In the `nft-tickets/` directory, start a validator and deploy the program according
to `Anchor.toml`:

```bash
cd nft-tickets
solana-test-validator
anchor build
anchor deploy
```

If a new program ID is generated, update `declare_id!` in the Rust program,
the Anchor configuration, and `SOLANA_PROGRAM_ID` in the backend.

### 3. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd nft_tickets_backend
python3 manage.py migrate
python3 manage.py runserver
```

The API is available at `http://127.0.0.1:8000/api/`.

For a temporary SQLite setup, add the following to `backend/.env`:

```text
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

By default, Vite proxies `/api` to `http://127.0.0.1:8000`.
For a deployed frontend, set `VITE_API_BASE_URL` to the backend URL.

Phantom must use the same Solana cluster as the backend. For localnet, configure
the validator RPC URL in Phantom and fund the test wallet with local SOL.

### 5. ESP32 scanner

In `ESP-32/ESP-32.ino`, set the Wi-Fi name and password, backend address, and
the `TICKET_SCANNER_API_TOKEN` value from `backend/.env`.

The firmware reads QR data through UART2:

- RX: GPIO 16;
- TX: GPIO 17;
- baud rate: 9600;
- format: 8N1.

The scanner sends a short-lived QR token to
`POST /api/tickets/scan/` with the `X-Scanner-Token` header. If the on-chain
transaction is still awaiting confirmation, the firmware polls the status using
`chain_transaction_id`; entry is allowed only after an accepted result.

This setup is for demonstration purposes: the shared scanner secret grants entry
access across all events. A production deployment requires HTTPS, individually
managed devices, and revocable, event-scoped credentials.

## Testing

Run Django tests:

```bash
cd backend/nft_tickets_backend
python3 manage.py test
```

Check the frontend:

```bash
cd frontend
npm run lint
npm run build
```

Check the Rust program:

```bash
cd nft-tickets
cargo test
```

## Security and Demonstration Limitations

- Do not commit `.env`, private keypairs, or tokens to Git;
- replace Pinata JWTs and all example secrets before use;
- Django is configured for local development; for deployment, disable DEBUG,
  restrict ALLOWED_HOSTS, and enable HTTPS;
- APScheduler is a temporary solution in the backend; Redis/Celery with durable
  task execution is recommended for multiple backend instances;
- test ledger files and logs must not be treated as production data;
- finalization, refunds, disputes, and frozen-event handling are diploma
  workflow components and require additional auditing before real financial use.

## Component Documentation

- [Anchor configuration](nft-tickets/Anchor.toml) — local cluster, program ID,
  and provider;
- [ESP32 scanner](ESP-32/ESP-32.ino) — network and demonstration API configuration.
