(async function guard() {
    const client = await getSupabaseClient();
    const {
        data: { session },
    } = await client.auth.getSession();

    if (!session) {
        window.location.href = "login.html";
        return;
    }

    document.getElementById("logoutBtn").addEventListener("click", async () => {
        await client.auth.signOut();
        window.location.href = "login.html";
    });
})();
