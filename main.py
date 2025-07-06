from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import bcrypt, uuid, random
from collections import Counter
from supabase_client import supabase

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

sessions = {}

# ----------------- Auth
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def get_current_user(request: Request):
    session_id = request.cookies.get("session_id")
    return sessions.get(session_id)

# ----------------- Helpers
def calculate_guess(secret: str, guess: str, traditional: bool = False):
    positions_correct = sum(1 for a, b in zip(secret, guess) if a == b)
    if traditional:
        secret_counts = Counter(secret)
        guess_counts = Counter(guess)
        numbers_correct = sum(min(secret_counts[d], guess_counts[d]) for d in secret_counts)
    else:
        numbers_correct = sum(1 for d in guess if d in secret)
    return numbers_correct, positions_correct

def generate_room_code():
    return ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))

def parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return False

# ----------------- Routes
@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/play")

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(username: str = Form(...), password: str = Form(...)):
    hashed = hash_password(password)
    user_id = str(uuid.uuid4())
    supabase.table("users").insert({
        "id": user_id, "username": username, "hashed_password": hashed
    }).execute()
    return RedirectResponse("/login", status_code=302)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    res = supabase.table("users").select("*").eq("username", username).execute()
    users = res.data
    if users and verify_password(password, users[0]["hashed_password"]):
        user_id = users[0]["id"]
        session_id = str(uuid.uuid4())
        sessions[session_id] = user_id
        response = RedirectResponse("/play", status_code=302)
        response.set_cookie("session_id", session_id, httponly=True)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid login"})

@app.get("/play", response_class=HTMLResponse)
async def play_page(request: Request):
    user_id = get_current_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    user_data = supabase.table("users").select("*").eq("id", user_id).single().execute().data
    username = user_data["username"]
    return templates.TemplateResponse("play.html", {"request": request, "user_id": user_id, "username": username})

# ----------------- Single player
@app.post("/start_game")
async def start_game(num_digits: int = Form(...), traditional: str = Form("false"), request: Request = None):
    traditional = parse_bool(traditional)
    user_id = get_current_user(request)
    secret = ''.join([str(random.randint(0,9)) for _ in range(num_digits)])
    game_id = str(uuid.uuid4())
    supabase.table("games").insert({
        "id": game_id,
        "user_id": user_id,
        "secret_number": secret,
        "num_digits": num_digits,
        "turns": 0,
        "is_completed": False,
        "traditional": traditional
    }).execute()
    return {"game_id": game_id}

@app.post("/guess")
async def guess(game_id: str = Form(...), guess: str = Form(...), request: Request = None):
    user_id = get_current_user(request)
    game = supabase.table("games").select("*").eq("id", game_id).single().execute().data
    secret = game["secret_number"]
    traditional = parse_bool(game.get("traditional", False))

    if len(guess) != len(secret):
        return {"error": f"Your guess must have exactly {len(secret)} digits."}

    numbers_correct, positions_correct = calculate_guess(secret, guess, traditional)
    turns = (game["turns"] or 0) + 1
    completed = (positions_correct == len(secret))

    supabase.table("guesses").insert({
        "id": str(uuid.uuid4()),
        "game_id": game_id,
        "guess": guess,
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct
    }).execute()
    supabase.table("games").update({
        "turns": turns,
        "is_completed": completed
    }).eq("id", game_id).execute()
    return {"numbers_correct": numbers_correct,"positions_correct": positions_correct,"completed": completed,"turns": turns}

# ----------------- Multiplayer
@app.post("/room_guess")
async def room_guess(room_id: str = Form(...), user_id: str = Form(...), username: str = Form(...), guess: str = Form(...)):
    room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    secret = room["secret_number"]
    traditional = parse_bool(room.get("traditional", False))

    if len(guess) != len(secret):
        return {"error": f"Your guess must have exactly {len(secret)} digits."}

    numbers_correct, positions_correct = calculate_guess(secret, guess, traditional)

    record_data = supabase.table("room_guesses").select("*").eq("room_id", room_id).eq("user_id", user_id).execute().data
    if not record_data:
        record = supabase.table("room_guesses").insert({
            "room_id": room_id, "user_id": user_id, "username": username,
            "turns": 0, "completed": False, "numbers_correct": 0, "positions_correct": 0
        }).execute().data[0]
    else:
        record = record_data[0]

    turns = (record["turns"] or 0) + 1
    completed = (positions_correct == len(secret))

    supabase.table("room_guesses").update({
        "turns": turns, "last_guess": guess,
        "numbers_correct": numbers_correct, "positions_correct": positions_correct,
        "completed": completed
    }).eq("id", record["id"]).execute()

    if room["winning_type"] == "fastest":
        if completed and not room["is_completed"]:
            supabase.table("rooms").update({"is_completed": True}).eq("id", room_id).execute()
    elif room["winning_type"] == "lowest":
        all_players = supabase.table("room_guesses").select("*").eq("room_id", room_id).execute().data
        finished_players = [p for p in all_players if p["completed"]]
        if len(finished_players) == len(all_players) or all(p["completed"] or p["turns"] > min(fp["turns"] for fp in finished_players) for p in all_players):
            supabase.table("rooms").update({"is_completed": True}).eq("id", room_id).execute()

    if completed and room["mode"] == "bot":
        existing_lb = supabase.table("leaderboard").select("*").eq("username", username).execute().data
        if not existing_lb:
            supabase.table("leaderboard").insert({
                "username": username, "best_turns": turns
            }).execute()
        elif turns < existing_lb[0]["best_turns"]:
            supabase.table("leaderboard").update({"best_turns": turns}).eq("username", username).execute()

    current_room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    return {
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct,
        "turns": turns,
        "completed": completed,
        "room_completed": current_room["is_completed"]
    }

# ----------------- Multiplayer management
@app.post("/create_room")
async def create_room(user_id: str = Form(...), username: str = Form(...), mode: str = Form(...),
                      num_digits: int = Form(...), winning_type: str = Form(...),
                      traditional: str = Form("false"), secret_number: str = Form(None)):
    traditional = parse_bool(traditional)
    room_code = generate_room_code()
    secret = ''.join([str(random.randint(0,9)) for _ in range(num_digits)]) if mode == "bot" else secret_number
    room = supabase.table("rooms").insert({
        "room_code": room_code, "mode": mode, "created_by": user_id,
        "secret_number": secret, "num_digits": num_digits, "winning_type": winning_type,
        "traditional": traditional
    }).execute().data[0]
    supabase.table("room_guesses").insert({
        "room_id": room["id"], "user_id": user_id, "username": username,
        "turns": 0, "completed": False
    }).execute()
    return {"room": room}

@app.post("/join_room")
async def join_room(room_code: str = Form(...), user_id: str = Form(...), username: str = Form(...)):
    room_result = supabase.table("rooms").select("*").eq("room_code", room_code).execute()
    if not room_result.data:
        return {"error": f"Room code {room_code} not found."}
    room = room_result.data[0]
    existing = supabase.table("room_guesses").select("*").eq("room_id", room["id"]).eq("user_id", user_id).execute().data
    if not existing:
        supabase.table("room_guesses").insert({
            "room_id": room["id"], "user_id": user_id, "username": username,
            "turns": 0, "completed": False, "numbers_correct": 0, "positions_correct": 0
        }).execute()
    return {"room": room}

@app.get("/room_status")
async def room_status(room_id: str):
    room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    if not room["is_completed"]:
        return {"status": "in_progress"}
    winner = supabase.table("room_guesses").select("*").eq("room_id", room_id).eq("completed", True).order("turns").limit(1).execute().data
    return {"status": "completed", "winner": winner[0] if winner else None}

@app.get("/leaderboard")
async def leaderboard_api():
    return supabase.table("leaderboard").select("*").order("best_turns").limit(10).execute().data

@app.post("/send_message")
async def send_message(room_id: str = Form(...), user_id: str = Form(...), username: str = Form(...), message: str = Form(...)):
    supabase.table("room_chat").insert({
        "room_id": room_id, "user_id": user_id, "username": username, "message": message
    }).execute()
    return {"status": "ok"}

@app.get("/get_messages")
async def get_messages(room_id: str):
    return supabase.table("room_chat").select("*").eq("room_id", room_id).order("created_at").execute().data

@app.get("/get_room_summary")
async def get_room_summary(room_id: str):
    return supabase.table("room_guesses").select("*").eq("room_id", room_id).order("turns").execute().data
