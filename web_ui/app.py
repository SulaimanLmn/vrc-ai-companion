"""Flask web interface for NeuroClone."""

import json
import threading
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit


def create_app(neuro):
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "neuro-clone-secret"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    # Wire status broadcasts to SocketIO
    neuro.on_status_change = lambda status: socketio.emit("status_update", status)
    neuro.on_chat_entry = lambda role, text: socketio.emit("chat_entry", {"role": role, "text": text})

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        return jsonify(neuro.get_status())

    @app.route("/api/chat_log")
    def api_chat_log():
        return jsonify(neuro.get_chat_log())

    @app.route("/api/toggle_enabled", methods=["POST"])
    def api_toggle_enabled():
        neuro.toggle_enabled()
        return jsonify(neuro.get_status())

    @app.route("/api/toggle_ptt", methods=["POST"])
    def api_toggle_ptt():
        neuro.toggle_ptt()
        return jsonify(neuro.get_status())

    @app.route("/api/send_text", methods=["POST"])
    def api_send_text():
        data = request.get_json()
        text = data.get("text", "")
        neuro.send_text(text)
        return jsonify({"ok": True})

    @app.route("/api/reset", methods=["POST"])
    def api_reset():
        neuro.reset_conversation()
        return jsonify({"ok": True})

    return app
