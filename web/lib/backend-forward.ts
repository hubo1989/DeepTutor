import {
  CONNECTION_IP_HEADER,
  CONNECTION_STAMP_TOKEN,
  CONNECTION_STAMP_TOKEN_HEADER,
} from "./connection-stamp";

export const FRONTEND_HOST_HEADER = "x-deeptutor-frontend-host";

export const CLIENT_IP_ASSERTION_HEADER = "x-deeptutor-client-ip";

const UNTRUSTED_FORWARD_HEADERS = [
  "connection",
  "upgrade",
  "forwarded",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-forwarded-for",
  "x-forwarded-port",
  "x-forwarded-server",
  "x-real-ip",
  "x-client-ip",
  "x-host",
  "x-original-host",
  FRONTEND_HOST_HEADER,
  // Client identity headers: only the value this process stamps from its own
  // TCP socket (web/lib/connection-stamp.ts) may be re-asserted below. A
  // browser-supplied copy is deleted, so a direct Next client can never have
  // the backend rate-limit on an address it chose.
  CLIENT_IP_ASSERTION_HEADER,
  CONNECTION_IP_HEADER,
  CONNECTION_STAMP_TOKEN_HEADER,
];

export function prepareBackendForwardHeaders(
  source: Headers,
  { allowWebSocketUpgrade = false }: { allowWebSocketUpgrade?: boolean } = {},
): Headers {
  // NextRequest.nextUrl.host is the Next server's bind address in standalone
  // and source deployments. The HTTP Host header carries the requested
  // frontend host through Next's proxy, including when TLS ends upstream.
  const frontendHost = source.get("host");
  // The connection stamp is installed beyond header reach (see
  // connection-stamp.ts); copy it before the strip pass removes any
  // client-supplied impostors. Only a value paired with this boot's stamp
  // token counts as stamped: a client cannot guess the token, so a forged
  // connection-ip alone (e.g. sent during the pre-patch boot race) is never
  // asserted to the backend.
  const stampedIp =
    source.get(CONNECTION_STAMP_TOKEN_HEADER) === CONNECTION_STAMP_TOKEN
      ? source.get(CONNECTION_IP_HEADER)
      : null;
  const headers = new Headers(source);
  const connectionTokens = (headers.get("connection") || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  const isWebSocketUpgrade =
    allowWebSocketUpgrade &&
    connectionTokens.includes("upgrade") &&
    headers.get("upgrade")?.trim().toLowerCase() === "websocket";

  for (const name of [...UNTRUSTED_FORWARD_HEADERS, ...connectionTokens]) {
    headers.delete(name);
  }
  // Next applies this header set to the original upgrade request before
  // proxying it. Restore only the WebSocket handshake, not other hop-by-hop
  // headers named by the client in Connection.
  if (isWebSocketUpgrade) {
    headers.set("connection", "Upgrade");
    headers.set("upgrade", "websocket");
  }
  if (frontendHost) headers.set(FRONTEND_HOST_HEADER, frontendHost);
  // Re-assert only the value this process stamped on the incoming socket.
  // stampedIp is null when instrumentation has not registered (dev without
  // the hook, or a request that raced server.listen) or when a client tried
  // to forge the pair, in which case nothing is asserted and the backend
  // falls back to its own peer address.
  if (stampedIp) {
    headers.set(CLIENT_IP_ASSERTION_HEADER, stampedIp);
  }
  return headers;
}
