# TradeBot — Setup Guide

Step-by-step instructions for setting up the project on a new machine.

---

## Requirements

- Python 3.12+
- Interactive Brokers TWS or IB Gateway installed and running
- A paper trading account (for development) or live account (for production)

---

## 1. Clone the repo

```bash
git clone https://github.com/gzion2719/Trad_Bot_wClaude.git
cd Trad_Bot_wClaude
```

---

## 2. Create a virtual environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS (for VPS deployment):**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt. Always activate the venv before running the bot.

---

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Configure environment

Copy the example config and fill in your values:

```bash
copy .env.example .env      # Windows
# or
cp .env.example .env        # Linux/macOS
```

Edit `.env`:

```
IB_HOST=127.0.0.1
IB_PORT=7497          # 7497 = paper trading, 7496 = live trading
IB_CLIENT_ID=1
```

> **Warning:** Never commit your `.env` file to Git. It is already in `.gitignore`.

---

## 5. Configure TWS / IB Gateway

1. Open TWS or IB Gateway and log in
2. Go to **Edit → Global Configuration → API → Settings**
3. Check **Enable ActiveX and Socket Clients**
4. Set **Socket port** to match your `IB_PORT` (7497 for paper)
5. Add `127.0.0.1` to the **Trusted IP Addresses** list
6. Click **OK** and restart TWS if prompted

---

## 6. Run the bot

Make sure your venv is active and TWS is running, then:

```bash
python main.py
```

---

## 7. Run the test suite

```bash
cd tests
python run_tests.py
```

All 40 tests should pass. If any fail, check that TWS is running and logged in.

---

## Daily workflow

```bash
# Open a new terminal
cd TradeBot
venv\Scripts\activate       # Windows
# or: source venv/bin/activate

# Start the bot
python main.py
```

---

## Updating dependencies

If new packages are added to `requirements.txt`:

```bash
pip install -r requirements.txt
```

---

## VPS deployment (Hostinger)

See Sprint 5 tasks in `TODO.md` for the full VPS setup checklist.
Key items: IBC for headless IB Gateway, `systemd` for process management,
and ensuring the nightly TWS reset is handled by the `ReconnectManager`.
