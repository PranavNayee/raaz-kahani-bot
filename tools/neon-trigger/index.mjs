// External trigger for the Raaz Kahani bot (Neon Function + scheduled Function Triggers).
// GitHub's own cron can lag for hours (it did for this new repository), so Neon calls this function on the same
// schedules and it asks GitHub to run the matching workflow. Running a workflow twice is harmless: every job is
// idempotent (slot locks, "already sent", once-per-week checks).
// Env: GH_DISPATCH_TOKEN = fine-grained GitHub token, only this repository, permission "Actions: read and write".
const REPO = process.env.GH_REPO || "PranavNayee/raaz-kahani-bot";
const ROUTES = {
  "/daily": "daily.yml", "/post": "post.yml", "/director": "director.yml",
  "/engage": "engage.yml", "/weekly": "weekly.yml", "/longform": "longform.yml",
};

export default {
  async fetch(req) {
    const path = new URL(req.url).pathname;
    // Neon strips client-set X-Neon-* headers, so this header proves the call came from Neon's trigger system.
    if (req.method !== "POST" || !req.headers.get("x-neon-trigger-invocation-id")) {
      return Response.json({ error: "not a trigger call" }, { status: 401 });
    }
    const workflow = ROUTES[path];
    if (!workflow) return Response.json({ error: `unknown path ${path}` }, { status: 404 });
    if (!process.env.GH_DISPATCH_TOKEN) {
      console.log(`dispatch ${workflow}: GH_DISPATCH_TOKEN is not set`);
      return Response.json({ error: "GH_DISPATCH_TOKEN missing" }, { status: 500 });
    }
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.GH_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
        "User-Agent": "raaz-kahani-trigger", "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main" }),
      signal: AbortSignal.timeout(15000),
    });
    const detail = r.status === 204 ? "" : (await r.text()).slice(0, 200);
    console.log(`dispatch ${workflow}: ${r.status} ${detail}`);
    return Response.json({ ok: r.status === 204, workflow, status: r.status }, { status: r.status === 204 ? 200 : 502 });
  },
};
