const form = document.getElementById("loginForm");
const errorMsg = document.getElementById("errorMsg");

form.addEventListener("submit", async function (e) {
    e.preventDefault();

    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;

    errorMsg.textContent = "";

    if (email === "" || password === "") {
        errorMsg.textContent = "이메일과 비밀번호를 입력해주세요.";
        return;
    }

    const submitBtn = form.querySelector("button");
    submitBtn.disabled = true;

    try {
        const client = await getSupabaseClient();
        const { error } = await client.auth.signInWithPassword({ email, password });

        if (error) {
            errorMsg.textContent = "로그인 실패: " + error.message;
            return;
        }

        window.location.href = "index.html";
    } finally {
        submitBtn.disabled = false;
    }
});
