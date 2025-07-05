from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import bcrypt, uuid, random
from supabase_client import supabase

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Simple in-memory sessions (swap for secure JWT / Redis in prod)
sessions = {}

# ----------------- Auth helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def get_current_user(request: Request):
    session_id = request.cookies.get("session_id")
    return sessions.get(session_id)

# ----------------- Register
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(username: str = Form(...), password: str = Form(...)):
    hashed = hash_password(password)
    user_id = str(uuid.uuid4())
    supabase.table("users").insert({
        "id": user_id,
        "username": username,
        "hashed_password": hashed
    }).execute()
    return RedirectResponse("/login", status_code=302)

# ----------------- Login
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

# ----------------- Game Play
@app.get("/play", response_class=HTMLResponse)
async def play_page(request: Request):
    user_id = get_current_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("play.html", {"request": request})

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

@app.post("/guess")
async def guess(game_id: str = Form(...), guess: str = Form(...), request: Request = None):
    user_id = get_current_user(request)
    game = supabase.table("games").select("*").eq("id", game_id).single().execute().data
    secret = game["secret_number"]

    # Core game logic
    numbers_correct = sum(min(secret.count(d), guess.count(d)) for d in set(guess))
    positions_correct = sum(1 for a, b in zip(secret, guess) if a == b)

    supabase.table("guesses").insert({
        "id": str(uuid.uuid4()),
        "game_id": game_id,
        "guess": guess,
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct
    }).execute()

    turns = game["turns"] + 1
    completed = (positions_correct == len(secret))
    supabase.table("games").update({
        "turns": turns,
        "is_completed": completed
    }).eq("id", game_id).execute()

    return {
        "numbers_correct": numbers_correct,
        "positions_correct": positions_correct,
        "completed": completed
    }

# ----------------- Leaderboard
@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    leaderboard = supabase.table("leaderboard").select("*").execute().data
    return templates.TemplateResponse("leaderboard.html", {"request": request, "players": leaderboard})
