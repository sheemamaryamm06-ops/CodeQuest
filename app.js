(function() {
    async function api(path, options) {
        const response = await fetch(path, Object.assign({
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin"
        }, options || {}));
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || "The request could not be completed.");
        }
        return result;
    }

    async function currentPlayer() {
        try {
            return await api("/api/me");
        } catch (error) {
            if (error.message === "Please log in to continue.") {
                return null;
            }
            throw error;
        }
    }

    async function requirePlayer() {
        try {
            const player = await currentPlayer();
            if (!player) {
                window.location.href = "/login.html";
            }
            return player;
        } catch (error) {
            alert("Could not connect to the CodeQuest server. Start it with: python server.py");
            return null;
        }
    }

    window.CodeQuest = {
        api: api,
        currentPlayer: currentPlayer,
        requirePlayer: requirePlayer
    };
})();
