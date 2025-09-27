import os
import sqlite3
import random
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ----------------------------
# App config
# ----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Uploads
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

# Audio folder (you provide mp3s)
AUDIO_FOLDER = os.path.join("static", "audio")
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# SQLite DB path
DB_PATH = os.path.join(os.path.dirname(__file__), "barkr.db")


# ----------------------------
# Audio snippet catalog
# artist -> list of {key,title,file,answers}
# Make sure the files exist in static/audio/
# ----------------------------
ARTIST_SONGS = {
    "Drake": [
        {"key": "drake_godsplan", "title": "God's Plan",
         "file": "/static/audio/godsplan.mp3",
         "answers": ["god's plan", "gods plan"]},
  
    ],
    "Miley Cyrus": [
        {"key": "miley_flowers", "title": "Flowers",
         "file": "/static/audio/flowers.mp3",
         "answers": ["Flowers", "flowers"]},

    ],
    "Bruno Mars": [
        {"key": "bruno_uptownf", "title": "Uptown Funk",
         "file": "/static/audio/uptownfunk.mp3",
         "answers": ["Uptown Funk", "uptown funk"]},
    ],
    "The Weeknd": [
        {"key": "weeknd_blinding_lights", "title": "Blinding Lights",
         "file": "/static/audio/blindinglights.mp3",
         "answers": ["blinding lights"]},
    ],
    "Justin Timberlake": [
        {"key": "justin_cstf", "title": "Cant Stop The Feeling",
         "file": "/static/audio/cstf.mp3",
         "answers": ["Cant Stop The Feeling", "Can't Stop The Feeling", "cant stop the feeling"]},
    ],
    "Dua Lipa": [
        {"key": "dua_levitating", "title": "Levitating",
         "file": "/static/audio/levitating.mp3",
         "answers": ["levitating"]},
    ],
    "Ed Sheeran": [
        {"key": "ed_shape_of_you", "title": "Shape of You",
         "file": "/static/audio/shapeofyou.mp3",
         "answers": ["shape of you"]},
    ],

}


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
        photo TEXT,
        favorite_artist TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS likes (
        user_id        INTEGER NOT NULL,
        target_dog_id  INTEGER NOT NULL,
        value          INTEGER NOT NULL CHECK (value IN (-1, 1)), -- 1 like, -1 dislike
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, target_dog_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (target_dog_id) REFERENCES dogs(id)
    );
    """)
    # backfill favorite_artist if the column didn't exist previously
    cols = [r["name"] for r in db.execute("PRAGMA table_info(dogs)").fetchall()]
    if "favorite_artist" not in cols:
        db.execute("ALTER TABLE dogs ADD COLUMN favorite_artist TEXT")
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

def my_dog_id():
    db = get_db()
    row = db.execute("SELECT id FROM dogs WHERE user_id = ?", (session["user_id"],)).fetchone()
    return row["id"] if row else None

@app.context_processor
def inject_match_count():
    """Makes `match_count` available in all templates for the logged-in user."""
    try:
        if not session.get("user_id"):
            return dict(match_count=0)
        db = get_db()
        mydog = my_dog_id()
        if not mydog:
            return dict(match_count=0)

        # Count mutual likes (same logic as /matches but COUNT)
        row = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM likes l1
            JOIN dogs d   ON d.id = l1.target_dog_id
            JOIN likes l2 ON l2.user_id = d.user_id
                         AND l2.target_dog_id = ?
            WHERE l1.user_id = ?
              AND l1.value = 1
              AND l2.value = 1
              AND d.user_id != ?
            """,
            (mydog, session["user_id"], session["user_id"])
        ).fetchone()
        return dict(match_count=row["c"] if row else 0)
    except Exception:
        # don't break rendering if something goes wrong
        return dict(match_count=0)


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
        return redirect(url_for("discover"))  # make the feed the home
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
        return redirect(url_for("discover"))
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
      favorite_artist
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
        dog_age_val = int(dog_age) if (dog_age and dog_age.isdigit()) else None
        dog_gender = (request.form.get("dog_gender") or "").strip()
        dog_breed = (request.form.get("dog_breed") or "").strip()
        dog_personality = (request.form.get("dog_personality") or "").strip()
        dog_bio = (request.form.get("dog_bio") or "").strip()
        dog_fav_artist = (request.form.get("favorite_artist") or "").strip()
        if not dog_fav_artist:
            flash("Please pick a favourite artist.", "error")
            return render_template("register.html")

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
                """INSERT INTO dogs (user_id, name, age, gender, breed, personality, bio, photo, favorite_artist)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, dog_name, dog_age_val, dog_gender, dog_breed, dog_personality, dog_bio, photo_path, dog_fav_artist)
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Username already exists.", "error")
            return render_template("register.html")

        session["user_id"] = user_id
        session["username"] = username
        flash("Welcome! Profile created.", "ok")
        return redirect(url_for("discover"))

    return render_template("register.html")


# ---- Discover / Swiping ----
@app.route("/discover")
@login_required
def discover():
    """
    Show the next dog card that:
      - is not your own dog
      - you haven't already swiped on
    """
    db = get_db()
    next_dog = db.execute(
        """
        SELECT d.*, u.username
        FROM dogs d
        JOIN users u ON u.id = d.user_id
        WHERE d.user_id != ?
          AND d.id NOT IN (
              SELECT target_dog_id FROM likes WHERE user_id = ?
          )
        ORDER BY d.created_at DESC
        LIMIT 1
        """,
        (session["user_id"], session["user_id"])
    ).fetchone()

    return render_template("discover.html", dog=next_dog)


@app.route("/swipe", methods=["POST"])
@login_required
def swipe():
    """
    Handles like/dislike.
    If like would be mutual, redirect to song gate BEFORE finalizing the like.
    """
    dog_id = request.form.get("dog_id")
    action = request.form.get("action")
    if not dog_id or action not in {"like", "dislike"}:
        flash("Invalid swipe.", "error")
        return redirect(url_for("discover"))
    dog_id = int(dog_id)

    db = get_db()

    if action == "dislike":
        db.execute("""
            INSERT INTO likes (user_id, target_dog_id, value)
            VALUES (?, ?, -1)
            ON CONFLICT(user_id, target_dog_id)
            DO UPDATE SET value=-1, created_at=CURRENT_TIMESTAMP
        """, (session["user_id"], dog_id))
        db.commit()
        return redirect(url_for("discover"))

    # action == "like"
    mine = my_dog_id()
    if not mine:
        flash("Set up your dog profile first.", "error")
        return redirect(url_for("register"))

    target_owner = db.execute("SELECT user_id FROM dogs WHERE id = ?", (dog_id,)).fetchone()
    if not target_owner:
        return redirect(url_for("discover"))
    target_user_id = target_owner["user_id"]

    # Did the target's owner already like my dog?
    mutual = db.execute("""
      SELECT 1 FROM likes
      WHERE user_id = ? AND target_dog_id = ? AND value = 1
      LIMIT 1
    """, (target_user_id, mine)).fetchone()

    if mutual:
        # Go to the song gate; if correct, we'll finalize and show match
        return redirect(url_for("songgate", dog_id=dog_id))

    # Not mutual yet → just record the like
    db.execute("""
        INSERT INTO likes (user_id, target_dog_id, value)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, target_dog_id)
        DO UPDATE SET value=1, created_at=CURRENT_TIMESTAMP
    """, (session["user_id"], dog_id))
    db.commit()
    return redirect(url_for("discover"))


# ---- Song Gate ----
@app.route("/songgate/<int:dog_id>", methods=["GET", "POST"])
@login_required
def songgate(dog_id):
    db = get_db()
    target = db.execute("SELECT * FROM dogs WHERE id = ?", (dog_id,)).fetchone()
    if not target:
        flash("That profile is gone.", "error")
        return redirect(url_for("discover"))

    artist = (target["favorite_artist"] or "").strip()
    choices = ARTIST_SONGS.get(artist, [])
    if not choices:
        # No snippets configured -> skip gate and finalize
        return finalize_like_then_redirect(dog_id, matched=True)

    # Pick a snippet (stick to one per session by passing ?key=)
    key = request.args.get("key")
    if not key:
        pick = random.choice(choices)
        return redirect(url_for("songgate", dog_id=dog_id, key=pick["key"]))

    snippet = next((c for c in choices if c["key"] == key), None)
    if not snippet:
        flash("Audio not found.", "error")
        return redirect(url_for("discover"))

    if request.method == "POST":
        guess = (request.form.get("answer") or "").strip().lower()
        normalized = [a.lower() for a in snippet["answers"]]
        if guess in normalized:
            return finalize_like_then_redirect(dog_id, matched=True)
        else:
            flash("Close, but not the song we were looking for. Try again!", "error")

    return render_template("songgate.html", dog=target, artist=artist, snippet=snippet)


def finalize_like_then_redirect(target_dog_id, matched=False):
    db = get_db()
    db.execute("""
      INSERT INTO likes (user_id, target_dog_id, value)
      VALUES (?, ?, 1)
      ON CONFLICT(user_id, target_dog_id)
      DO UPDATE SET value=1, created_at=CURRENT_TIMESTAMP
    """, (session["user_id"], target_dog_id))
    db.commit()

    if matched:
        flash("It’s a match! 🎉", "ok")
        return redirect(url_for("matches"))
    return redirect(url_for("discover"))


# ---- Matches ----
@app.route("/matches")
@login_required
def matches():
    """
    Show two lists:
      - pending_matches: they liked me, but I haven't completed the song gate (i.e., I haven't recorded a like=1 yet)
      - confirmed_matches: mutual like (both recorded like=1)
    """
    db = get_db()
    mydog = my_dog_id()
    if not mydog:
        flash("Create your profile first.", "error")
        return redirect(url_for("register"))

    # 1) PENDING: they -> me == 1 AND (I haven't liked them yet)
    pending = db.execute(
        """
        SELECT d.*, u.username, l_other.created_at AS liked_at
        FROM dogs d
        JOIN users u ON u.id = d.user_id
        JOIN likes l_other
             ON l_other.user_id = d.user_id
            AND l_other.target_dog_id = ?
            AND l_other.value = 1
        LEFT JOIN likes l_me
             ON l_me.user_id = ?
            AND l_me.target_dog_id = d.id
            AND l_me.value = 1
        WHERE d.user_id != ?
          AND l_me.user_id IS NULL      -- I haven't recorded a like yet (so gate not completed)
        ORDER BY l_other.created_at DESC
        """,
        (mydog, session["user_id"], session["user_id"])
    ).fetchall()

    # 2) CONFIRMED: mutual likes (both recorded like=1)
    confirmed = db.execute(
        """
        SELECT d.*, u.username
        FROM likes l1
        JOIN dogs d        ON d.id = l1.target_dog_id
        JOIN users u       ON u.id = d.user_id
        JOIN likes l2      ON l2.user_id = d.user_id
                          AND l2.target_dog_id = ?
        WHERE l1.user_id = ?
          AND l1.value = 1
          AND l2.value = 1
          AND d.user_id != ?
        ORDER BY d.created_at DESC
        """,
        (mydog, session["user_id"], session["user_id"])
    ).fetchall()

    return render_template("matches.html",
                           pending_matches=pending,
                           confirmed_matches=confirmed)



# ---- Profile (read-only) ----
@app.route("/me")
@login_required
def me():
    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    dog = db.execute("SELECT * FROM dogs WHERE user_id = ?", (session["user_id"],)).fetchone()
    return render_template("me.html", user=user, dog=dog)


# ---- Serve uploads directly (optional) ----
@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ----------------------------
# Run
# ----------------------------
if __name__ == "__main__":
    app.run(debug=True)
