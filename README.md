# NFT Ticketing Platform

NFT Ticketing Platform — проєкт системи продажу квитків для подій, де квиток
представлено як NFT у мережі Solana. Проєкт поєднує вебзастосунок для користувачів
та організаторів, Django API, смартконтракт Anchor та ESP32-сканер QR-кодів.

Система дозволяє організатору створити подію й типи квитків, прийняти оплату у SOL,
випустити NFT-квиток власнику гаманця та перевірити квиток під час входу.

## Основні можливості

- авторизація через підпис повідомлення в Phantom Wallet;
- перегляд активних подій і пошук за назвою чи описом;
- створення події та завантаження афіші;
- налаштування кількох типів квитків із ціною та лімітом випуску;
- покупка квитка з частковим підписом транзакції у браузері;
- зберігання афіш і NFT-метаданих через Pinata/IPFS;
- особистий кабінет із NFT-квитками та QR-кодами;
- повернення коштів і скасування події;
- автоматичне або ручне завершення події;
- перевірка сканування квитка ESP32 через Django API;
- темна і світла тема інтерфейсу;
- синхронізація станів подій і транзакцій із мережі Solana.

## Архітектура проєкту

```text
Phantom Wallet
      │
      ▼
React + Vite frontend ── REST API ──> Django backend
                                          │
                       Pinata/IPFS        │
                            │             │
                            ▼             ▼
                         Афіші     Solana RPC / Anchor
                                       │
                                       ▼
                                  NFT Ticket Program

ESP32 QR scanner ── shared demo token ──> Django scan API
```

### Компоненти

| Компонент                      | Технології                                                      | Призначення                                                            |
| ------------------------------ | --------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `frontend/`                    | React 19, Vite, Solana Web3.js                                  | Інтерфейс користувача, підпис транзакцій, Phantom-автентифікація       |
| `backend/nft_tickets_backend/` | Django 5, Django REST Framework, PostgreSQL/SQLite, APScheduler | API, база даних, авторизація, IPFS-публікація, синхронізація блокчейну |
| `nft-tickets/`                 | Anchor 1.1.2, Rust, MPL Core                                    | On-chain логіка подій, типів квитків, продажів, повернень і сканування |
| `ESP-32/ESP-32.ino`            | Arduino для ESP32, HTTPClient                                   | Читання QR-кодів і передача короткоживучих токенів у API               |

Приватні ключі Metaplex collection і ticket asset створюються в памʼяті браузера.
Вони не надсилаються на backend. Backend-захищений authority-keypair використовується
лише сервером для адміністративних on-chain інструкцій.

## Структура каталогу

```text
.
├── frontend/                   # React/Vite клієнт
├── backend/                    # Django API та локальні налаштування
├── nft-tickets/                # Anchor/Rust смартконтракт
├── ESP-32/                     # Firmware демонстраційного сканера
└── test-ledger-with-core/      # Дані локальної Solana validator
```

## Вимоги

- Node.js і npm;
- Python 3.11+;
- PostgreSQL для основного режиму або SQLite для локальних експериментів;
- Rust через rustup і потрібну версію toolchain;
- Solana CLI та Anchor CLI;
- браузер із Phantom Wallet;
- ESP32 та QR-сканер із UART для hardware-входу.

## Локальний запуск

### 1. Клонування та змінні середовища

Backend налаштовується у `backend/.env`. Початковий приклад знаходиться у
`backend/.env.example`:

```bash
cp backend/.env.example backend/.env
```

Frontend налаштовується у `frontend/.env.local`:

```bash
cp frontend/.env.example frontend/.env.local
```

Обовʼязково замініть прикладні секрети реальними локальними значеннями.
Не зберігайте приватні ключі, Pinata JWT або scanner token у frontend-коді.

Основні параметри backend:

- `DJANGO_SECRET_KEY` — секрет Django;
- `DB_*` — параметри PostgreSQL або SQLite;
- `SOLANA_RPC_URL` — URL RPC validator;
- `SOLANA_PROGRAM_ID` — адреса розгорнутого smart contract;
- `SOLANA_BACKEND_KEYPAIR_PATH` — локальний keypair backend authority;
- `PINATA_JWT` і `PINATA_GATEWAY_URL` — публікація NFT-даних і афіш;
- `TICKET_QR_VALIDITY_SECONDS` — строк дії одноразового QR-токена;
- `TICKET_SCANNER_API_TOKEN` — спільний секрет демонстраційного ESP32-сканера.

### 2. Solana local validator

У каталозі `nft-tickets/` підніміть validator і розгорніть програму відповідно до
налаштувань `Anchor.toml`:

```bash
cd nft-tickets
solana-test-validator
anchor build
anchor deploy
```

Під час підстановки нового program ID оновіть `declare_id!` в Rust-програмі,
конфігурацію Anchor і `SOLANA_PROGRAM_ID` у backend.

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

API буде доступне за адресою `http://127.0.0.1:8000/api/`.

Для SQLite тимчасово встановіть у `backend/.env`:

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

Vite за замовчуванням проксує `/api` на `http://127.0.0.1:8000`.
Для deployed frontend передайте backend-адресу через `VITE_API_BASE_URL`.

Phantom має використовувати той самий Solana cluster, що й backend.
Для localnet установіть RPC validator у Phantom і поповніть test-гаманець SOL.

### 5. ESP32-сканер

У `ESP-32/ESP-32.ino` встановіть назву й пароль Wi-Fi, адресу backend,
а також значення `TICKET_SCANNER_API_TOKEN` з `backend/.env`.

Прошивка очікує QR-послідовність через UART2:

- RX: GPIO 16;
- TX: GPIO 17;
- baud rate: 9600;
- формат: 8N1.

Сканер надсилає короткоживучий QR-токен у
`POST /api/tickets/scan/` із заголовком `X-Scanner-Token`.
Якщо on-chain транзакція ще очікує підтвердження, firmware повторно перевіряє
статус за `chain_transaction_id`; доступ дозволяється лише після accepted-результату.

Схема є демонстраційною: спільний scanner secret дає доступ до входу на всі події.
У production-середовищі потрібні HTTPS, індивідуальні пристрої та revocable,
event-scoped credentials.

## Перевірка

Запуск Django-тестів:

```bash
cd backend/nft_tickets_backend
python3 manage.py test
```

Перевірка frontend:

```bash
cd frontend
npm run lint
npm run build
```

Перевірка Rust-программи:

```bash
cd nft-tickets
cargo test
```

## Безпека та demo-обмеження

- не комітьте `.env`, приватні keypair або токени до Git;
- змінійте Pinata JWT і інші прикладні секрети до використання;
- Django налаштований для локальної розробки; перед deployment потрібно
  відключити DEBUG, обмежити ALLOWED_HOSTS і увімкнути HTTPS;
- APScheduler у backend є тимчасовим рішенням; для кількох backend-instance
  доцільне окреме завдання з Redis/Celery;
- тестові ledger-файли й logs не слід використовувати як production-дані;
- finalization, refunds, disputes і frozen-events є частиною дипломної логики
  й потребують додаткового аудиту перед реальним фінансовим запуском.

## Документація компонентів

- [Anchor config](nft-tickets/Anchor.toml) — local cluster, program ID і provider;
- [ESP32 scanner](ESP-32/ESP-32.ino) — конфігурація мережі і демонстраційного API.
