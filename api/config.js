// Vercel Serverless Function. 배포 환경에서 main.py(FastAPI)의 /config 역할을 대신한다.
// 실제 값은 이 파일이 아니라 Vercel 프로젝트의 Environment Variables 에 설정한다.
module.exports = (req, res) => {
    res.status(200).json({
        supabaseUrl: process.env.SUPABASE_URL,
        supabaseAnonKey: process.env.SUPABASE_ANON_KEY,
    });
};
