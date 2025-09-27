import os

import sqlite3
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ----------------------------
# App config
# ----------------------------

print(">>> LOADED app.py")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["EXPLAIN_TEMPLATE_LOADING"] = True

os.makedirs(os.path.join("static", "backgrounds"), exist_ok=True)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


# Uploads
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB



# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), "barkr.db")


# ----------------------------
# DB helpers (sqlite3)
# ----------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def bootstrap():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        hash TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS dogs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT,
        breed TEXT,
        personality TEXT,
        bio TEXT,
        photo TEXT, -- /static/uploads/filename.jpg
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)
    db.commit()

with app.app_context():
    bootstrap()


# ----------------------------
# Utilities
# ----------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.after_request
def after_request(response):
    # avoid browser caching during dev
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ----------------------------
# Routes
# ----------------------------


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("me"))
    return render_template("index.html")

# ---- Auth ----
@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            flash("Please provide username and password.", "error")
            return render_template("login.html")

        db = get_db()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not check_password_hash(row["hash"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session["user_id"] = row["id"]
        session["username"] = row["username"]
        return redirect(url_for("me"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Create user and the user's dog in one go.
    Fields:
      username, password, confirmation
      dog_photo, dog_name, dog_age, dog_gender, dog_breed, dog_personality, dog_bio
    """
    if request.method == "POST":
        # user fields
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirmation = request.form.get("confirmation") or ""
        if not username or not password or not confirmation:
            flash("Please fill all user fields.", "error")
            return render_template("register.html")

        if password != confirmation:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        # dog fields
        dog_name = (request.form.get("dog_name") or "").strip()
        if not dog_name:
            flash("Dog name is required.", "error")
            return render_template("register.html")

        dog_age = request.form.get("dog_age")
        dog_age_val = None
        if dog_age and dog_age.isdigit():
            dog_age_val = int(dog_age)

        dog_gender = (request.form.get("dog_gender") or "").strip()
        dog_breed = (request.form.get("dog_breed") or "").strip()
        dog_personality = (request.form.get("dog_personality") or "").strip()
        dog_bio = (request.form.get("dog_bio") or "").strip()

        # photo upload (optional)
        photo_path = None
        file = request.files.get("dog_photo")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Unsupported file type (png/jpg/jpeg/gif/webp).", "error")
                return render_template("register.html")
            safe_name = secure_filename(file.filename)
            base, ext = os.path.splitext(safe_name)
            final_name = safe_name
            i = 1
            while os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], final_name)):
                final_name = f"{base}_{i}{ext}"
                i += 1
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], final_name))
            photo_path = f"/static/uploads/{final_name}"

        # create user + dog
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, hash) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )
            user_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            db.execute(
                """INSERT INTO dogs (user_id, name, age, gender, breed, personality, bio, photo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, dog_name, dog_age_val, dog_gender, dog_breed, dog_personality, dog_bio, photo_path)
            )
            db.commit()
        except sqlite3.IntegrityError:
            # likely username duplicate
            db.rollback()
            flash("Username already exists.", "error")
            return render_template("register.html")

        session["user_id"] = user_id
        session["username"] = username
        flash("Welcome! Profile created.", "ok")
        return redirect(url_for("me"))

    return render_template("register.html")

# ---- Profile (read-only) ----
@app.route("/me")
@login_required
def me():
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    dog = db.execute("SELECT * FROM dogs WHERE user_id = ?", (session["user_id"],)).fetchone()
    return render_template("me.html", user=user, dog=dog)

# Optional: serve uploads directly (not needed if using /static/uploads)
@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

if __name__ == "__main__":
    app.run(debug=True)
