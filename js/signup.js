const form = document.getElementById("signupForm");
const errorMsg = document.getElementById("errorMsg");

form.addEventListener("submit", async function (e) {
    e.preventDefault();

    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    const passwordConfirm = document.getElementById("passwordConfirm").value;

    errorMsg.textContent = "";

    if (email === "" || password === "") {
        errorMsg.textContent = "이메일과 비밀번호를 입력해주세요.";
        return;
    }

    if (password !== passwordConfirm) {
        errorMsg.textContent = "비밀번호가 일치하지 않습니다.";
        return;
    }

    const submitBtn = form.querySelector("button");
    submitBtn.disabled = true;

    try {
        const client = await getSupabaseClient();
        const { error } = await client.auth.signUp({ email, password });

        if (error) {
            errorMsg.textContent = "회원가입 실패: " + error.message;
            return;
        }

        alert("회원가입이 완료되었습니다. (이메일 인증이 켜져 있다면 메일함을 확인해주세요)");
        window.location.href = "login.html";
    } finally {
        submitBtn.disabled = false;
    }
});
