from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TV_SECRET = os.environ.get("TV_SECRET", "change-me").strip()
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

DHAN_WEBHOOK_URL = os.environ.get("DHAN_WEBHOOK_URL", "").strip()
DHAN_SECRET = os.environ.get("DHAN_SECRET", "").strip()

STATE = {
    "trading_enabled": True,
    "allowed_option_type": None,
    "allowed_strike": None,
    "last_alert": None,
    "last_dhan_payload": None,
    "last_dhan_response": None,
    "last_telegram_chat_id": None
}


def send_telegram(message, chat_id=None):
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing")
        return False

    final_chat_id = chat_id or TELEGRAM_CHAT_ID or STATE.get("last_telegram_chat_id")

    if not final_chat_id:
        print("Telegram chat id missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": final_chat_id,
        "text": message
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", str(e))
        return False


def build_dhan_payload(data):
    side = str(data.get("side", "")).upper()
    option_type = str(data.get("option_type", "")).upper()
    strike = int(float(data.get("strike")))
    qty = int(float(data.get("qty", 20)))
    expiry = str(data.get("expiry", ""))

    transaction_type = "B" if side == "BUY" else "S"

    payload = {
        "secret": DHAN_SECRET,
        "alertType": "multi_leg_order",
        "order_legs": [
            {
                "transactionType": transaction_type,
                "orderType": "MKT",
                "quantity": qty,
                "exchange": "NSE",
                "symbol": "NIFTY",
                "instrument": "OPT",
                "productType": "I",
                "sort_order": "1",
                "price": "0",
                "option_type": option_type,
                "strike_price": str(float(strike)),
                "expiry_date": expiry
            }
        ]
    }

    return payload


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
        response = {"error": "DHAN_WEBHOOK_URL missing"}
        STATE["last_dhan_response"] = response
        return response

    try:
        r = requests.post(DHAN_WEBHOOK_URL, json=payload, timeout=10)

        try:
            body = r.json()
        except Exception:
            body = r.text

        response = {
            "status_code": r.status_code,
            "body": body
        }

        STATE["last_dhan_response"] = response
        return response

    except Exception as e:
        response = {"error": str(e)}
        STATE["last_dhan_response"] = response
        return response


@app.route("/", methods=["GET"])
def home():
    return "TV-Dhan-Telegram bot is live ✅"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "dry_run": DRY_RUN,
        "telegram_token_set": bool(TELEGRAM_BOT_TOKEN),
        "telegram_chat_id_set": bool(TELEGRAM_CHAT_ID),
        "dhan_webhook_set": bool(DHAN_WEBHOOK_URL),
        "dhan_secret_set": bool(DHAN_SECRET)
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "status": "live",
        "dry_run": DRY_RUN,
        "state": STATE
    })


@app.route("/tv-alert", methods=["POST"])
def tv_alert():
    data = request.json or {}

    if data.get("secret") != TV_SECRET:
        return jsonify({
            "status": "rejected",
            "reason": "invalid secret"
        }), 401

    option_type = str(data.get("option_type", "")).upper()
    side = str(data.get("side", "")).upper()

    try:
        strike = int(float(data.get("strike")))
    except Exception:
        return jsonify({
            "status": "rejected",
            "reason": "invalid strike"
        }), 400

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

    STATE["last_alert"] = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "expiry": data.get("expiry", ""),
        "qty": data.get("qty", "")
    }

    if not STATE["trading_enabled"]:
        send_telegram(f"🚫 Alert rejected: trading disabled\n{option_type} {strike} {side}")
        return jsonify({
            "status": "rejected",
            "reason": "trading disabled"
        })

    if STATE["allowed_option_type"] is None or STATE["allowed_strike"] is None:
        send_telegram(f"⚠️ Alert received but filter not set\n{option_type} {strike} {side}")
        return jsonify({
            "status": "rejected",
            "reason": "filter not set"
        })

    if option_type != STATE["allowed_option_type"] or strike != STATE["allowed_strike"]:
        send_telegram(
            f"❌ Alert rejected by filter\n"
            f"Received: {option_type} {strike} {side}\n"
            f"Allowed: {STATE['allowed_option_type']} {STATE['allowed_strike']}"
        )
        return jsonify({
            "status": "rejected",
            "reason": "filter mismatch"
        })

    dhan_payload = build_dhan_payload(data)
    dhan_response = send_to_dhan(dhan_payload)

    send_telegram(
        f"✅ Alert accepted\n"
        f"{option_type} {strike}\n"
        f"Side: {side}\n"
        f"DRY_RUN: {DRY_RUN}\n\n"
        f"Dhan response:\n{dhan_response}"
    )

    return jsonify({
        "status": "accepted",
        "dry_run": DRY_RUN,
        "dhan_payload": dhan_payload,
        "dhan_response": dhan_response
    })


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}

    print("Telegram update:", update)

    message = update.get("message", {})
    chat = message.get("chat", {})
    text = message.get("text", "")
    chat_id = str(chat.get("id", ""))

    if chat_id:
        STATE["last_telegram_chat_id"] = chat_id

    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        return jsonify({
            "ok": True,
            "ignored": "unauthorized chat"
        })

    handle_telegram_command(text, chat_id)

    return jsonify({"ok": True})


def handle_telegram_command(text, chat_id):
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
            "/resume",
            chat_id
        )

    elif cmd == "/set":
        if len(parts) != 3:
            send_telegram("Use format: /set CE 24000", chat_id)
            return

        option_type = parts[1].upper()

        if option_type not in ["CE", "PE"]:
            send_telegram("Option type must be CE or PE", chat_id)
            return

        try:
            strike = int(float(parts[2]))
        except Exception:
            send_telegram("Strike must be a number", chat_id)
            return

        STATE["allowed_option_type"] = option_type
        STATE["allowed_strike"] = strike

        send_telegram(f"✅ Filter updated\nAllowed: {option_type} {strike}", chat_id)

    elif cmd == "/status":
        send_telegram(
            f"📊 Status\n"
            f"Trading enabled: {STATE['trading_enabled']}\n"
            f"DRY_RUN: {DRY_RUN}\n"
            f"Allowed option: {STATE['allowed_option_type']}\n"
            f"Allowed strike: {STATE['allowed_strike']}\n"
            f"Last alert: {STATE['last_alert']}\n"
            f"Last Dhan response: {STATE['last_dhan_response']}",
            chat_id
        )

    elif cmd == "/stop":
        STATE["trading_enabled"] = False
        send_telegram("🛑 Trading disabled", chat_id)

    elif cmd == "/resume":
        STATE["trading_enabled"] = True
        send_telegram("✅ Trading enabled", chat_id)

    else:
        send_telegram("Unknown command. Use /status or /set CE 24000", chat_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
