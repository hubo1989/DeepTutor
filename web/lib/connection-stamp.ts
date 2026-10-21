// Connection-layer client-address stamp for the bundled Next.js frontend.
//
// Why this exists: Next.js 16 exposes no peer address to `proxy.ts` or route
// handlers (NextRequest is built from URL + headers + body only), and the
// `x-forwarded-for` header Next does populate is suppressed by any
// client-supplied copy (`base-server.js`: `x-forwarded-for ??=
// socket.remoteAddress`), so no header-level signal identifies a visitor.
// The backend therefore sees every visitor as the proxy's loopback address
// and all visitors share one rate-limit bucket.
//
// Overwrite semantics: we SET `x-deeptutor-connection-ip` on every incoming
// request (never `??=`), taking the value from the raw TCP socket. A client
// that sends its own copy has it replaced before any handler — proxy.ts or a
// route handler — can read it, so the stamped value cannot be forged through
// headers.
//
// Degradation: this hook runs from `instrumentation.ts` `register()`. Requests
// served before registration (server.listen starts before init completes) and
// any process where the patch could not be applied simply carry no stamp;
// proxy.ts then asserts nothing and the backend falls back to its own peer
// address (the pre-existing shared-bucket behavior). Nothing here may throw
// into the request path.

import { randomUUID } from "node:crypto";
import type { IncomingMessage } from "node:http";
import http from "node:http";
import https from "node:https";

/** Server-side header carrying the address of whoever connected to Next. */
export const CONNECTION_IP_HEADER = "x-deeptutor-connection-ip";

/**
 * Per-boot random token proving the connection-ip header was written by this
 * process's socket patch rather than by the connecting client.
 *
 * The emit patch cannot be installed before the server starts listening in
 * every boot ordering, and during that window a client-supplied
 * connection-ip copy would otherwise survive into proxy.ts and be re-asserted
 * to the backend as if stamped. proxy.ts (same process, same module instance)
 * compares the header against this token and only asserts values carrying the
 * correct one; a client cannot guess it and it is never serialized outward.
 * If module duplication ever produced two instances, the values would simply
 * never match and no assertion would be forwarded (safe degradation).
 */
export const CONNECTION_STAMP_TOKEN = randomUUID();

/** Header the emit patch pairs with the stamped address. */
export const CONNECTION_STAMP_TOKEN_HEADER = "x-deeptutor-connection-stamp";

type Emit = (this: unknown, event: string, ...args: unknown[]) => boolean;

interface PatchTarget {
  emit: Emit;
  stamped?: boolean;
}

function stampRequest(req: IncomingMessage | undefined | null): void {
  const address = req?.socket?.remoteAddress;
  if (req && address && req.headers) {
    req.headers[CONNECTION_IP_HEADER] = address;
    req.headers[CONNECTION_STAMP_TOKEN_HEADER] = CONNECTION_STAMP_TOKEN;
  }
}

function patchEmit(proto: PatchTarget, label: string): void {
  if (proto.stamped) return;
  proto.stamped = true;
  try {
    const originalEmit = proto.emit;
    proto.emit = function patchedEmit(event, ...args) {
      if (event === "request" || event === "upgrade") {
        stampRequest(args[0] as IncomingMessage | undefined);
      }
      return originalEmit.call(this, event, ...args);
    };
  } catch (error) {
    // Never let identity stamping break request serving: revert the flag so a
    // later register() can retry, and surface the failure instead of silently
    // running unstamped.
    proto.stamped = false;
    console.warn(
      `[deeptutor] could not install ${label} connection stamp; the backend will fall back to shared-bucket rate limiting.`,
      error,
    );
  }
}

/** Idempotently patch http/https servers to stamp the connecting address. */
export function stampConnectionAddress(): void {
  patchEmit(http.Server.prototype as unknown as PatchTarget, "http.Server");
  patchEmit(https.Server.prototype as unknown as PatchTarget, "https.Server");
}
