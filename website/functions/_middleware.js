const PASSWORD = "betrayal";

export async function onRequest(context) {
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
