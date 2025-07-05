from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import bcrypt, uuid, random
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
def calculate_guess(secret: str, guess: str, strict: bool = True):
    positions_correct = sum(1 for a, b in zip(secret, guess) if a == b)
    if strict:
        numbers_correct = sum(min(secret.count(d), guess.count(d)) for d in set(secret))
    else:
        numbers_correct = sum(1 for d in guess if d in secret)
    return numbers_correct, positions_correct

def generate_room_code():
    return ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))

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
async def start_game(num_digits: int = Form(...), request: Request = None):
    user_id = get_current_user(request)
    secret = ''.join([str(random.randint(0,9)) for _ in range(num_digits)])
    game_id = str(uuid.uuid4())
    supabase.table("games").insert({
        "id": game_id,
        "user_id": user_id,
        "secret_number": secret,
        "num_digits": num_digits,
        "turns": 0,
        "is_completed": False
    }).execute()
    return {"game_id": game_id}

# ----------------- Single Player
@app.post("/guess")
async def guess(game_id: str = Form(...), guess: str = Form(...), request: Request = None):
    user_id = get_current_user(request)
    game = supabase.table("games").select("*").eq("id", game_id).single().execute().data
    secret = game["secret_number"]

    # Enforce length match
    if len(guess) != len(secret):
        return {"error": f"Your guess must have exactly {len(secret)} digits."}

    numbers_correct, positions_correct = calculate_guess(secret, guess, strict=False)

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
async def room_guess(room_id: str = Form(...), user_id: str = Form(...), guess: str = Form(...)):
    room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    secret = room["secret_number"]

    # Enforce length match
    if len(guess) != len(secret):
        return {"error": f"Your guess must have exactly {len(secret)} digits."}

    # Totally relaxed: same as single player
    numbers_correct, positions_correct = calculate_guess(secret, guess, strict=False)

    # Upsert or update player record
    record_data = supabase.table("room_guesses").select("*").eq("room_id", room_id).eq("user_id", user_id).execute().data
    if not record_data:
        record = supabase.table("room_guesses").insert({
            "room_id": room_id,
            "user_id": user_id,
            "username": "",
            "turns": 0,
            "completed": False,
            "numbers_correct": 0,
            "positions_correct": 0
        }).execute().data[0]
    else:
        record = record_data[0]

    turns = (record["turns"] or 0) + 1
    completed = (positions_correct == len(secret))

    supabase.table("room_guesses").update({
        "turns": turns,
        "last_guess": guess,
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct,
        "completed": completed
    }).eq("id", record["id"]).execute()

    # Check if room is completed
    if completed and room["winning_type"] == "fastest" and not room["is_completed"]:
        supabase.table("rooms").update({"is_completed": True}).eq("id", room_id).execute()

    # If versus bot AND completed, update leaderboard
    if completed and room["mode"] == "bot":
        # Check existing
        existing_lb = supabase.table("leaderboard").select("*").eq("user_id", user_id).execute().data
        if not existing_lb:
            # New entry
            supabase.table("leaderboard").insert({
                "user_id": user_id,
                "username": "",
                "best_turns": turns
            }).execute()
        else:
            # Update only if this is better
            current_best = existing_lb[0]["best_turns"]
            if turns < current_best:
                supabase.table("leaderboard").update({
                    "best_turns": turns
                }).eq("user_id", user_id).execute()

    current_room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    return {
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct,
        "turns": turns,
        "completed": completed,
        "room_completed": current_room["is_completed"]
    }


@app.post("/create_room")
async def create_room(
    user_id: str = Form(...),
    username: str = Form(...),
    mode: str = Form(...),
    num_digits: int = Form(...),
    winning_type: str = Form(...),
    secret_number: str = Form(None)
):
    room_code = generate_room_code()
    if mode == "bot":
        secret = ''.join([str(random.randint(0,9)) for _ in range(num_digits)])
    else:
        secret = secret_number

    # Insert the room into Supabase
    room = supabase.table("rooms").insert({
        "room_code": room_code,
        "mode": mode,
        "created_by": user_id,
        "secret_number": secret,
        "num_digits": num_digits,
        "winning_type": winning_type
    }).execute().data[0]

    # Also immediately register the creator into room_guesses
    supabase.table("room_guesses").insert({
        "room_id": room["id"],
        "user_id": user_id,
        "username": username,
        "turns": 0,
        "completed": False
    }).execute()

    # Return full room object so JavaScript can do:
    #   roomId = data.room.id;
    #   roomCode = data.room.room_code;
    #   requiredLength = data.room.num_digits;
    return {"room": room}



@app.post("/join_room")
async def join_room(room_code: str = Form(...), user_id: str = Form(...), username: str = Form(...)):
    room = supabase.table("rooms").select("*").eq("room_code", room_code).single().execute().data
    existing = supabase.table("room_guesses").select("*").eq("room_id", room["id"]).eq("user_id", user_id).execute().data
    if not existing:
        supabase.table("room_guesses").insert({
            "room_id": room["id"],
            "user_id": user_id,
            "username": username,
            "turns": 0,
            "completed": False,
            "numbers_correct": 0,
            "positions_correct": 0
        }).execute()
    return {"room": room}


@app.get("/room_status")
async def room_status(room_id: str):
    room = supabase.table("rooms").select("*").eq("id", room_id).single().execute().data
    if not room["is_completed"]:
        return {"status": "in_progress"}
    winner = supabase.table("room_guesses").select("*").eq("room_id", room_id).eq("completed", True).order("created_at").limit(1).execute().data
    return {"status": "completed", "winner": winner[0] if winner else None}

@app.get("/leaderboard")
async def leaderboard_api():
    leaderboard = supabase.table("leaderboard").select("*").order("best_turns").limit(10).execute().data
    return leaderboard

@app.post("/send_message")
async def send_message(room_id: str = Form(...), user_id: str = Form(...), username: str = Form(...), message: str = Form(...)):
    supabase.table("room_chat").insert({
        "room_id": room_id,
        "user_id": user_id,
        "username": username,
        "message": message
    }).execute()
    return {"status": "ok"}

@app.get("/get_messages")
async def get_messages(room_id: str):
    msgs = supabase.table("room_chat").select("*").eq("room_id", room_id).order("created_at").execute().data
    return msgs

@app.get("/get_room_summary")
async def get_room_summary(room_id: str):
    players = supabase.table("room_guesses").select("*").eq("room_id", room_id).order("turns").execute().data
    return players
