import os
import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import unquote

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client, Client

from model import run_nanobanana_edit

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Chybaju SUPABASE_URL alebo SUPABASE_SERVICE_ROLE_KEY.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "nanobanana-secret-key")

app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

ALLOWED_ORIGINS = [
    "https://shortyofm.eu",
    "https://www.shortyofm.eu",
    "http://localhost:5173",
]

CORS(
    app,
    supports_credentials=True,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Neprihlaseny pouzivatel"}), 401
        return func(*args, **kwargs)
    return wrapper


def get_user_settings_by_email(email):
    result = supabase.table("user_settings").select("*").eq("user_email", email).execute()
    rows = result.data or []

    if rows:
        return rows[0]

    default_settings = {
        "user_email": email,
        "wavespeed_api_key": "",
    }

    supabase.table("user_settings").insert(default_settings).execute()
    return default_settings


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Backend bezi OK"}), 200


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()

    if not email or not password:
        return jsonify({"error": "Email alebo heslo chyba"}), 400

    result = supabase.table("users").select("*").eq("email", email).execute()
    users = result.data or []

    if not users:
        return jsonify({"error": "Pouzivatel neexistuje"}), 401

    user = users[0]

    if not check_password_hash(user.get("password", ""), password):
        return jsonify({"error": "Nespravne heslo"}), 401

    session["user"] = {
        "email": user["email"],
        "role": user["role"],
    }

    return jsonify({"message": "Prihlaseny", "user": session["user"]}), 200


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Odhlaseny"}), 200


@app.route("/settings", methods=["GET"])
@login_required
def get_settings():
    email = session["user"]["email"]
    settings = get_user_settings_by_email(email)

    return jsonify({
        "wavespeed_api_key": settings.get("wavespeed_api_key", "")
    })


@app.route("/settings", methods=["POST"])
@login_required
def save_settings():
    email = session["user"]["email"]
    data = request.get_json(silent=True) or {}

    wavespeed_api_key = data.get("wavespeed_api_key", "").strip()

    supabase.table("user_settings").upsert({
        "user_email": email,
        "wavespeed_api_key": wavespeed_api_key
    }).execute()

    return jsonify({"message": "Settings ulozene"})


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    try:

        prompt = request.form.get("prompt", "").strip()
        iphone_style = request.form.get("iphone_style", "false") == "true"
        aspect_ratio = request.form.get("aspect_ratio", "1:1")
        quality = request.form.get("quality", "2K")
        model_name = request.form.get("model_name", "flash")

        if not prompt:
            return jsonify({"error": "Prompt je prazdny"}), 400

        images = request.files.getlist("images")

        if not images:
            return jsonify({"error": "Musis nahrat reference image"}), 400

        paths = []

        for img in images:

            name = secure_filename(img.filename)
            unique = f"{uuid.uuid4().hex}_{name}"
            path = os.path.join(OUTPUT_DIR, unique)

            img.save(path)
            paths.append(path)

        current_user = session.get("user", {})
        email = current_user.get("email")

        settings = get_user_settings_by_email(email)

        normalized_model = model_name.lower()

        # NanoBanana ide cez Gemini
        if normalized_model in ["flash", "pro"]:
            provider = "gemini"
            api_key = os.getenv("GEMINI_API_KEY", "").strip()

        # Seedream ide cez WaveSpeed
        else:
            provider = "wavespeed"
            api_key = settings.get("wavespeed_api_key", "").strip()

        if not api_key:
            return jsonify({"error": "Chyba API key"}), 400

        result_path, width, height, resolved_model = run_nanobanana_edit(
            prompt=prompt,
            image_paths=paths,
            api_key=api_key,
            provider=provider,
            iphone_style=iphone_style,
            aspect_ratio=aspect_ratio,
            quality=quality,
            model_name=model_name,
            safety_threshold="BLOCK_ONLY_HIGH",
        )

        return jsonify({
            "image": "/outputs/" + os.path.basename(result_path),
            "width": width,
            "height": height,
            "model": resolved_model,
            "provider": provider
        })

    except Exception as e:
        print("BACKEND ERROR:", str(e))
        return jsonify({"error": f"Backend chyba: {str(e)}"}), 500


@app.route("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
