from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime

app = Flask(__name__)

# =========================
# ENVIRONMENT VARIABLES
# =========================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TV_SECRET = os.environ.get("TV_SECRET", "change-me")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

DHAN_WEBHOOK_URL = os.environ.get("DHAN_WEBHOOK_URL", "")
DHAN_SECRET = os.environ.get("DHAN_SECRET", "")

DHAN_SYMBOL = os.environ.get("DHAN_SYMBOL", "NIFTY")
DHAN_EXCHANGE = os.environ.get("DHAN_EXCHANGE", "NSE")
DHAN_INSTRUMENT = os.environ.get("DHAN_INSTRUMENT", "OPT")
DHAN_PRODUCT_TYPE = os.environ.get("DHAN_PRODUCT_TYPE", "I")
DHAN_ORDER_TYPE = os.environ.get("DHAN_ORDER_TYPE", "MKT")
DHAN_QTY = int(os.environ.get("DHAN_QTY", "20"))
DHAN_DEFAULT_EXPIRY = os.environ.get("DHAN_DEFAULT_EXPIRY", "")

# =========================
# SIMPLE MEMORY
# Note: resets if Railway restarts. Later we add persistent storage.
# =========================
STATE = {
    "trading_enabled": True,
    "allowed_option_type": None,
    "allowed_strike": None,
    "max_loss": None,
    "max_profit": None,
    "min_profit": None,
    "last_alert": None,
    "last_dhan_payload": None,
    "last_dhan_response": None
}


# =========================
# TELEGRAM SEND MESSAGE
# =========================
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured:", message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram response:", r.status_code, r.text)
    except Exception as e:
        print("Telegram error:", str(e))


# =========================
# BUILD DHAN WEBHOOK PAYLOAD
# =========================
def build_dhan_payload(alert_data, option_type, strike, side):
    expiry = alert_data.get("expiry") or DHAN_DEFAULT_EXPIRY
    qty = int(float(alert_data.get("qty") or DHAN_QTY))

    # TradingView side usually comes as BUY/SELL.
    # Dhan webhook examples commonly use B/S.
    transaction_type = "B" if side == "BUY" else "S"

    payload = {
        "secret": DHAN_SECRET,
        "alertType": "multi_leg_order",
        "order_legs": [
            {
                "transactionType": transaction_type,
                "orderType": DHAN_ORDER_TYPE,
                "quantity": qty,
                "exchange": DHAN_EXCHANGE,
                "symbol": DHAN_SYMBOL,
                "instrument": DHAN_INSTRUMENT,
                "productType": DHAN_PRODUCT_TYPE,
                "sort_order": "1",
                "price": "0",
                "option_type": option_type,
                "strike_price": str(float(strike)),
                "expiry_date": expiry
            }
        ]
    }

    return payload


# =========================
# SEND TO DHAN WEBHOOK
# =========================
def send_to_dhan(payload):
    STATE["last_dhan_payload"] = payload

    if DRY_RUN:
        response = {
            "dry_run": True,
            "message": "Dhan forwarding skipped because DRY_RUN=true"
        }
        STATE["last_dhan_response"] = response
        return response

    if not DHAN_WEBHOOK_URL:
        response = {
            "error": "DHAN_WEBHOOK_URL missing"
        }
        STATE["last_dhan_response"] = response
        return response

    try:
        r = requests.post(DHAN_WEBHOOK_URL, json=payload, timeout=10)

        try:
            response_body = r.json()
        except Exception:
            response_body = r.text

        response = {
            "status_code": r.status_code,
            "body": response_body
        }

        STATE["last_dhan_response"] = response
        return response

    except Exception as e:
        response = {
            "error": str(e)
        }
        STATE["last_dhan_response"] = response
        return response


# =========================
# HOME
# =========================
@app.route("/", methods=["GET"])
def home():
    return "TV-Dhan-Telegram bot is live ✅ Phase 3"


# =========================
# STATUS
# =========================
@app.route("/status", methods=["GET"])
def status():
    safe_state = dict(STATE)
    return jsonify({
        "status": "live",
        "dry_run": DRY_RUN,
        "state": safe_state
    })


# =========================
# TRADINGVIEW ALERT RECEIVER
# =========================
@app.route("/tv-alert", methods=["POST"])
def tv_alert():
    data = request.json or {}

    if data.get("secret") != TV_SECRET:
        return jsonify({
            "status": "rejected",
            "reason": "invalid secret"
        }), 401

    option_type = str(data.get("option_type", "")).upper()
    strike = data.get("strike")
    side = str(data.get("side", "")).upper()
    symbol = data.get("symbol", DHAN_SYMBOL)
    expiry = data.get("expiry", DHAN_DEFAULT_EXPIRY)
    qty = data.get("qty", DHAN_QTY)

    if option_type not in ["CE", "PE"]:
        return jsonify({
            "status": "rejected",
            "reason": "invalid option_type"
        }), 400

    if side not in ["BUY", "SELL"]:
        return jsonify({
            "status": "rejected",
            "reason": "invalid side"
        }), 400

    try:
        strike = int(float(strike))
    except Exception:
        return jsonify({
            "status": "rejected",
            "reason": "invalid strike"
        }), 400

    STATE["last_alert"] = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "qty": qty
    }

    if not STATE["trading_enabled"]:
        msg = f"🚫 Alert rejected: trading disabled\n{symbol} {option_type} {strike} {side}"
        send_telegram(msg)
        return jsonify({"status": "rejected", "reason": "trading disabled"})

    if STATE["allowed_option_type"] is None or STATE["allowed_strike"] is None:
        msg = f"⚠️ Alert received but filter not set\n{symbol} {option_type} {strike} {side}"
        send_telegram(msg)
        return jsonify({"status": "rejected", "reason": "filter not set"})

    if option_type != STATE["allowed_option_type"] or strike != STATE["allowed_strike"]:
        msg = (
            f"❌ Alert rejected by filter\n"
            f"Received: {symbol} {option_type} {strike} {side}\n"
            f"Allowed: {STATE['allowed_option_type']} {STATE['allowed_strike']}"
        )
        send_telegram(msg)
        return jsonify({
            "status": "rejected",
            "reason": "filter mismatch",
            "received": {
                "option_type": option_type,
                "strike": strike
            },
            "allowed": {
                "option_type": STATE["allowed_option_type"],
                "strike": STATE["allowed_strike"]
            }
        })

    dhan_payload = build_dhan_payload(data, option_type, strike, side)
    dhan_response = send_to_dhan(dhan_payload)

    msg = (
        f"✅ Alert accepted\n"
        f"{symbol} {option_type} {strike}\n"
        f"Side: {side}\n"
        f"Expiry: {expiry}\n"
        f"Qty: {qty}\n"
        f"DRY_RUN: {DRY_RUN}\n\n"
        f"Dhan response:\n{dhan_response}"
    )
    send_telegram(msg)

    return jsonify({
        "status": "accepted",
        "dry_run": DRY_RUN,
        "dhan_payload": dhan_payload,
        "dhan_response": dhan_response
    })


# =========================
# TELEGRAM WEBHOOK RECEIVER
# =========================
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}

    message = update.get("message", {})
    chat = message.get("chat", {})
    text = message.get("text", "")
    chat_id = str(chat.get("id", ""))

    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        return jsonify({"ok": True, "ignored": "unauthorized chat"})

    handle_telegram_command(text)

    return jsonify({"ok": True})


# =========================
# TELEGRAM COMMAND HANDLER
# =========================
def handle_telegram_command(text: str):
    parts = text.strip().split()

    if not parts:
        return

    cmd = parts[0].lower()

    if cmd == "/start":
        send_telegram(
            "Bot active ✅\n\n"
            "Commands:\n"
            "/set CE 24000\n"
            "/set PE 24000\n"
            "/status\n"
            "/stop\n"
            "/resume\n"
            "/dryrun on\n"
            "/dryrun off"
        )

    elif cmd == "/set":
        if len(parts) != 3:
            send_telegram("Use format: /set CE 24000")
            return

        option_type = parts[1].upper()

        if option_type not in ["CE", "PE"]:
            send_telegram("Option type must be CE or PE")
            return

        try:
            strike = int(float(parts[2]))
        except Exception:
            send_telegram("Strike must be a number. Example: /set CE 24000")
            return

        STATE["allowed_option_type"] = option_type
        STATE["allowed_strike"] = strike

        send_telegram(f"✅ Filter updated\nAllowed: {option_type} {strike}")

    elif cmd == "/status":
        send_telegram(
            f"📊 Current status\n"
            f"Trading enabled: {STATE['trading_enabled']}\n"
            f"DRY_RUN: {DRY_RUN}\n"
            f"Allowed option: {STATE['allowed_option_type']}\n"
            f"Allowed strike: {STATE['allowed_strike']}\n"
            f"Max loss: {STATE['max_loss']}\n"
            f"Max profit: {STATE['max_profit']}\n"
            f"Min profit: {STATE['min_profit']}\n"
            f"Last alert: {STATE['last_alert']}\n"
            f"Last Dhan response: {STATE['last_dhan_response']}"
        )

    elif cmd == "/stop":
        STATE["trading_enabled"] = False
        send_telegram("🛑 Trading disabled")

    elif cmd == "/resume":
        STATE["trading_enabled"] = True
        send_telegram("✅ Trading enabled")

    elif cmd == "/dryrun":
        send_telegram(
            "DRY_RUN is controlled from Railway Variables for safety.\n"
            "Set DRY_RUN=true or DRY_RUN=false in Railway, then redeploy."
        )

    else:
        send_telegram(
            "Unknown command.\n\n"
            "Use:\n"
            "/set CE 24000\n"
            "/status\n"
            "/stop\n"
            "/resume"
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram response:", r.status_code, r.text)
    except Exception as e:
        print("Telegram error:", str(e))


# =========================
# HOME
# =========================
@app.route("/", methods=["GET"])
def home():
    return "TV-Dhan-Telegram bot is live ✅"


# =========================
# STATUS
# =========================
@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "status": "live",
        "state": STATE
    })


# =========================
# TRADINGVIEW ALERT RECEIVER
# =========================
@app.route("/tv-alert", methods=["POST"])
def tv_alert():
    data = request.json or {}

    # Secret check
    if data.get("secret") != TV_SECRET:
        return jsonify({
            "status": "rejected",
            "reason": "invalid secret"
        }), 401

    option_type = str(data.get("option_type", "")).upper()
    strike = data.get("strike")
    side = str(data.get("side", "")).upper()
    symbol = data.get("symbol", "NIFTY")
    expiry = data.get("expiry", "")
    qty = data.get("qty", "")

    try:
        strike = int(float(strike))
    except Exception:
        return jsonify({
            "status": "rejected",
            "reason": "invalid strike"
        }), 400

    STATE["last_alert"] = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "qty": qty
    }

    # Trading enabled check
    if not STATE["trading_enabled"]:
        msg = f"🚫 Alert rejected: trading disabled\n{symbol} {option_type} {strike} {side}"
        send_telegram(msg)
        return jsonify({"status": "rejected", "reason": "trading disabled"})

    # Filter not set
    if STATE["allowed_option_type"] is None or STATE["allowed_strike"] is None:
        msg = f"⚠️ Alert received but filter not set\n{symbol} {option_type} {strike} {side}"
        send_telegram(msg)
        return jsonify({"status": "rejected", "reason": "filter not set"})

    # CE/PE + strike filter
    if option_type != STATE["allowed_option_type"] or strike != STATE["allowed_strike"]:
        msg = (
            f"❌ Alert rejected by filter\n"
            f"Received: {symbol} {option_type} {strike} {side}\n"
            f"Allowed: {STATE['allowed_option_type']} {STATE['allowed_strike']}"
        )
        send_telegram(msg)
        return jsonify({
            "status": "rejected",
            "reason": "filter mismatch",
            "received": {
                "option_type": option_type,
                "strike": strike
            },
            "allowed": {
                "option_type": STATE["allowed_option_type"],
                "strike": STATE["allowed_strike"]
            }
        })

    # Accepted
    msg = (
        f"✅ Alert accepted\n"
        f"{symbol} {option_type} {strike}\n"
        f"Side: {side}\n"
        f"Expiry: {expiry}\n"
        f"Qty: {qty}\n\n"
        f"Next phase: forward to Dhan"
    )
    send_telegram(msg)

    return jsonify({
        "status": "accepted",
        "message": "Matched current Telegram filter. Dhan forwarding not enabled yet.",
        "data": data
    })


# =========================
# TELEGRAM WEBHOOK RECEIVER
# =========================
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}

    message = update.get("message", {})
    chat = message.get("chat", {})
    text = message.get("text", "")

    chat_id = str(chat.get("id", ""))

    # Optional safety: only accept your own chat once TELEGRAM_CHAT_ID is set
    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        return jsonify({"ok": True, "ignored": "unauthorized chat"})

    handle_telegram_command(text, chat_id)

    return jsonify({"ok": True})


# =========================
# TELEGRAM COMMAND HANDLER
# =========================
def handle_telegram_command(text: str, chat_id: str):
    parts = text.strip().split()

    if not parts:
        return

    cmd = parts[0].lower()

    if cmd == "/start":
        send_telegram(
            "Bot active ✅\n\n"
            "Commands:\n"
            "/set CE 24000\n"
            "/set PE 24000\n"
            "/status\n"
            "/stop\n"
            "/resume"
        )

    elif cmd == "/set":
        if len(parts) != 3:
            send_telegram("Use format: /set CE 24000")
            return

        option_type = parts[1].upper()

        if option_type not in ["CE", "PE"]:
            send_telegram("Option type must be CE or PE")
            return

        try:
            strike = int(float(parts[2]))
        except Exception:
            send_telegram("Strike must be a number. Example: /set CE 24000")
            return

        STATE["allowed_option_type"] = option_type
        STATE["allowed_strike"] = strike

        send_telegram(f"✅ Filter updated\nAllowed: {option_type} {strike}")

    elif cmd == "/status":
        send_telegram(
            f"📊 Current status\n"
            f"Trading enabled: {STATE['trading_enabled']}\n"
            f"Allowed option: {STATE['allowed_option_type']}\n"
            f"Allowed strike: {STATE['allowed_strike']}\n"
            f"Max loss: {STATE['max_loss']}\n"
            f"Max profit: {STATE['max_profit']}\n"
            f"Min profit: {STATE['min_profit']}\n"
            f"Last alert: {STATE['last_alert']}"
        )

    elif cmd == "/stop":
        STATE["trading_enabled"] = False
        send_telegram("🛑 Trading disabled")

    elif cmd == "/resume":
        STATE["trading_enabled"] = True
        send_telegram("✅ Trading enabled")

    else:
        send_telegram(
            "Unknown command.\n\n"
            "Use:\n"
            "/set CE 24000\n"
            "/status\n"
            "/stop\n"
            "/resume"
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
