// 이 파일을 사용하는 페이지는 아래 순서로 스크립트를 로드해야 합니다.
//   1) https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2  (전역 `supabase.createClient` 제공)
//   2) js/supabaseClient.js (이 파일)
//
// /config 는 main.py(FastAPI)가 비밀/.env 에 있는 SUPABASE_URL / SUPABASE_ANON_KEY 를
// 읽어서 내려주는 엔드포인트입니다. anon key는 공개용 키(RLS로 보호)라 프론트에 노출돼도 안전합니다.

let supabaseClientPromise = null;

function getSupabaseClient() {
    if (!supabaseClientPromise) {
        supabaseClientPromise = fetch("/config")
            .then((res) => res.json())
            .then(({ supabaseUrl, supabaseAnonKey }) =>
                supabase.createClient(supabaseUrl, supabaseAnonKey)
            );
    }
    return supabaseClientPromise;
}
