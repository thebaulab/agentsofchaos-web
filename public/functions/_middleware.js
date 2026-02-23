const PASSWORD = "betrayal";
const CANONICAL = "https://agentsofchaos.baulab.info";

export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (url.hostname.endsWith(".pages.dev")) {
    return Response.redirect(CANONICAL + url.pathname + url.search, 301);
  }

  const authHeader = context.request.headers.get("Authorization");

  if (authHeader && authHeader.startsWith("Basic ")) {
    const decoded = atob(authHeader.slice(6));
    const password = decoded.split(":").slice(1).join(":");
    if (password === PASSWORD) {
      return context.next();
    }
  }

  return new Response("Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Agents of Chaos"',
    },
  });
}
