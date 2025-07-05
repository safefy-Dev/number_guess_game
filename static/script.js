// 🎯 Number Guess Game Frontend Logic
let gameId = null;

async function startGame(digits) {
    const form = new FormData();
    form.append("num_digits", digits);

    const res = await fetch("/start_game", {
        method: "POST",
        body: form
    });
    const data = await res.json();
    gameId = data.game_id;

    document.getElementById("game").style.display = "block";
    document.getElementById("history").innerHTML = "";
    alert("Game started with " + digits + " digits!");
}

async function makeGuess() {
    const guess = document.getElementById("guessInput").value;
    const form = new FormData();
    form.append("game_id", gameId);
    form.append("guess", guess);

    const res = await fetch("/guess", {
        method: "POST",
        body: form
    });
    const data = await res.json();

    const li = document.createElement("li");
    li.innerText = `Guess: ${guess} → In Number: ${data.numbers_correct}, Correct Place: ${data.positions_correct}`;
    document.getElementById("history").appendChild(li);

    if (data.completed) {
        alert("🎉 Congratulations! You guessed the number!");
    }
}
