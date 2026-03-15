import os
import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import unquote

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from supabase import create_client, Client

from model import run_nanobanana_edit

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Chybaju SUPABASE_URL alebo SUPABASE_SERVICE_ROLE_KEY v Environment Variables.")

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
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

CORS(
    app,
    supports_credentials=True,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return response


def load_users():
    result = supabase.table("users").select("email, password, role").execute()
    return result.data or []


def sanitize_user(user):
    return {
        "email": user.get("email", ""),
        "role": user.get("role", "user"),
    }


def count_admins(users):
    return sum(1 for user in users if user.get("role") == "admin")


def find_user_by_email(users, email):
    for index, user in enumerate(users):
        if user.get("email", "").lower() == email.lower():
            return index, user
    return None, None


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Neprihlaseny pouzivatel"}), 401
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        current_user = session.get("user", {})
        if current_user.get("role") != "admin":
            return jsonify({"error": "Pristup len pre admina"}), 403
        return func(*args, **kwargs)
    return wrapper


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Backend bezi OK"}), 200


@app.route("/me", methods=["GET"])
def me():
    user = session.get("user")
    if not user:
        return jsonify({"authenticated": False}), 200

    return jsonify({
        "authenticated": True,
        "user": user,
    }), 200


@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json(silent=True) or {}
        email = data.get("email", "").strip()
        password = data.get("password", "").strip()
        remember_me = bool(data.get("remember_me", False))

        if not email or not password:
            return jsonify({"error": "Email alebo heslo chyba"}), 400

        users = load_users()

        for user in users:
            if user.get("email") == email and user.get("password") == password:
                session["user"] = sanitize_user(user)
                session.permanent = remember_me

                return jsonify({
                    "message": "Prihlasenie uspesne",
                    "user": session["user"],
                    "remember_me": remember_me,
                }), 200

        return jsonify({"error": "Nespravny email alebo heslo"}), 401

    except Exception as e:
        print("LOGIN ERROR:", str(e))
        return jsonify({"error": f"Login chyba: {str(e)}"}), 500


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    session.clear()
    return jsonify({"message": "Odhlaseny"}), 200


@app.route("/admin/users", methods=["GET"])
@admin_required
def get_admin_users():
    users = load_users()
    safe_users = [sanitize_user(user) for user in users]

    return jsonify({
        "users": safe_users,
        "current_user": session.get("user"),
    }), 200


@app.route("/admin/users", methods=["POST"])
@admin_required
def create_admin_user():
    try:
        data = request.get_json(silent=True) or {}
        email = data.get("email", "").strip()
        password = data.get("password", "").strip()
        role = data.get("role", "user").strip().lower()

        if not email or not password:
            return jsonify({"error": "Email a heslo su povinne"}), 400

        if role not in ["admin", "user"]:
            return jsonify({"error": "Neplatna rola"}), 400

        existing = supabase.table("users").select("email").eq("email", email).execute()
        if existing.data:
            return jsonify({"error": "Pouzivatel s tymto emailom uz existuje"}), 409

        new_user = {
            "email": email,
            "password": password,
            "role": role,
        }

        supabase.table("users").insert(new_user).execute()

        return jsonify({
            "message": "Pouzivatel bol vytvoreny",
            "user": sanitize_user(new_user),
        }), 201

    except Exception as e:
        print("CREATE USER ERROR:", str(e))
        return jsonify({"error": f"Chyba pri vytvarani pouzivatela: {str(e)}"}), 500


@app.route("/admin/users/<path:email>", methods=["PATCH"])
@admin_required
def update_admin_user(email):
    try:
        target_email = unquote(email)
        data = request.get_json(silent=True) or {}

        new_password = data.get("password")
        new_role = data.get("role")

        users = load_users()
        _, existing_user = find_user_by_email(users, target_email)

        if existing_user is None:
            return jsonify({"error": "Pouzivatel neexistuje"}), 404

        current_session_user = session.get("user", {})
        current_email = current_session_user.get("email", "")

        update_payload = {}

        if new_role is not None:
            new_role = str(new_role).strip().lower()

            if new_role not in ["admin", "user"]:
                return jsonify({"error": "Neplatna rola"}), 400

            if (
                existing_user.get("role") == "admin"
                and new_role == "user"
                and count_admins(users) <= 1
            ):
                return jsonify({"error": "Musi existovat aspon jeden admin"}), 400

            update_payload["role"] = new_role

        if new_password is not None:
            new_password = str(new_password).strip()
            if not new_password:
                return jsonify({"error": "Heslo nemoze byt prazdne"}), 400
            update_payload["password"] = new_password

        if not update_payload:
            return jsonify({"error": "Nemas co menit"}), 400

        supabase.table("users").update(update_payload).eq("email", target_email).execute()

        updated = supabase.table("users").select("email, role").eq("email", target_email).single().execute()
        updated_user = updated.data

        if current_email.lower() == target_email.lower():
            session["user"] = sanitize_user(updated_user)

        return jsonify({
            "message": "Pouzivatel bol upraveny",
            "user": sanitize_user(updated_user),
        }), 200

    except Exception as e:
        print("UPDATE USER ERROR:", str(e))
        return jsonify({"error": f"Chyba pri uprave pouzivatela: {str(e)}"}), 500


@app.route("/admin/users/<path:email>", methods=["DELETE"])
@admin_required
def delete_admin_user(email):
    try:
        target_email = unquote(email)
        users = load_users()
        _, existing_user = find_user_by_email(users, target_email)

        if existing_user is None:
            return jsonify({"error": "Pouzivatel neexistuje"}), 404

        current_session_user = session.get("user", {})
        current_email = current_session_user.get("email", "")

        if current_email.lower() == target_email.lower():
            return jsonify({"error": "Nemozes zmazat sam seba"}), 400

        if existing_user.get("role") == "admin" and count_admins(users) <= 1:
            return jsonify({"error": "Nemozes zmazat posledneho admina"}), 400

        supabase.table("users").delete().eq("email", target_email).execute()

        return jsonify({
            "message": "Pouzivatel bol zmazany",
            "user": sanitize_user(existing_user),
        }), 200

    except Exception as e:
        print("DELETE USER ERROR:", str(e))
        return jsonify({"error": f"Chyba pri mazani pouzivatela: {str(e)}"}), 500


@app.route("/generate", methods=["OPTIONS"])
def generate_options():
    return ("", 204)


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    try:
        prompt = request.form.get("prompt", "").strip()
        iphone_style = request.form.get("iphone_style", "false").lower() == "true"
        aspect_ratio = request.form.get("aspect_ratio", "1:1").strip()
        quality = request.form.get("quality", "2K").strip()
        model_name = request.form.get("model_name", "flash").strip()
        safety_threshold = request.form.get("safety_threshold", "BLOCK_ONLY_HIGH").strip()

        batch_count_raw = request.form.get("batch_count", "1").strip()
        try:
            batch_count = int(batch_count_raw)
        except ValueError:
            return jsonify({"error": "batch_count musi byt cislo"}), 400

        if batch_count < 1 or batch_count > 5:
            return jsonify({"error": "batch_count musi byt v rozsahu 1 az 5"}), 400

        if not API_KEY:
            return jsonify({"error": "V backende chyba GEMINI_API_KEY v Environment Variables."}), 500

        if not prompt:
            return jsonify({"error": "Prompt je prazdny"}), 400

        images = request.files.getlist("images")

        if len(images) == 0:
            return jsonify({"error": "Musis nahrat aspon 1 reference image"}), 400

        if len(images) > 4:
            return jsonify({"error": "Maximum su 4 reference images"}), 400

        paths = []

        for img in images:
            if not img or not img.filename:
                continue

            original_name = secure_filename(img.filename)
            unique_name = f"{uuid.uuid4().hex}_{original_name}"
            save_path = os.path.join(OUTPUT_DIR, unique_name)

            img.save(save_path)
            paths.append(save_path)

        if not paths:
            return jsonify({"error": "Nepodarilo sa ulozit reference images."}), 400

        generated_images = []
        width = None
        height = None
        resolved_model = None

        for _ in range(batch_count):
            result_path, width, height, resolved_model = run_nanobanana_edit(
                prompt=prompt,
                image_paths=paths,
                api_key=API_KEY,
                iphone_style=iphone_style,
                aspect_ratio=aspect_ratio,
                quality=quality,
                model_name=model_name,
                safety_threshold=safety_threshold,
            )

            generated_images.append("/outputs/" + os.path.basename(result_path))

        return jsonify({
            "images": generated_images,
            "image": generated_images[0] if generated_images else None,
            "width": width,
            "height": height,
            "model": resolved_model,
            "safety_threshold": safety_threshold,
            "batch_count": batch_count,
        }), 200

    except Exception as e:
        print("BACKEND ERROR:", str(e))
        return jsonify({"error": f"Backend chyba: {str(e)}"}), 500


@app.route("/outputs/<path:filename>", methods=["GET"])
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
