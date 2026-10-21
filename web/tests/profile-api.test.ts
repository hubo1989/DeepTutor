import test from "node:test";
import assert from "node:assert/strict";

import {
  avatarImageUrl,
  deleteOwnAccount,
  getAccountExport,
  getOwnLearnerProfile,
  getProfile,
  removeAvatarImage,
  setAvatarMarker,
  setOwnLearnerProfile,
  uploadAvatarImage,
} from "../lib/profile-api";

interface CapturedRequest {
  url: URL;
  method: string;
  init: RequestInit;
}

const FALLBACK_ORIGIN = "http://workspace.local";

/**
 * Replace globalThis.fetch, record every request, and answer with `respond`.
 * Restores the original fetch even when the exercised call throws.
 */
async function withFetch(
  respond: (request: CapturedRequest) => Response,
  run: (requests: CapturedRequest[]) => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const requests: CapturedRequest[] = [];
  (globalThis as { fetch: typeof fetch }).fetch = async (input, init) => {
    const raw = typeof input === "string" ? input : String(input);
    requests.push({
      url: new URL(raw, FALLBACK_ORIGIN),
      method: init?.method ?? "GET",
      init: init ?? {},
    });
    return respond(requests[requests.length - 1]);
  };
  try {
    await run(requests);
  } finally {
    (globalThis as { fetch: typeof fetch }).fetch = originalFetch;
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("learner profile round-trip hits /api/auth/profile/learner-profile with GET and PUT", async () => {
  await withFetch(
    () => jsonResponse({ learner_profile: { age: 9, grade_level: "G3" } }),
    async (requests) => {
      const fetched = await getOwnLearnerProfile();
      assert.deepEqual(fetched, { age: 9, grade_level: "G3" });
      assert.equal(requests[0].url.pathname, "/api/auth/profile/learner-profile");
      assert.equal(requests[0].method, "GET");

      const saved = await setOwnLearnerProfile({ age: 10 });
      assert.deepEqual(saved, { age: 9, grade_level: "G3" });
      assert.equal(requests[1].url.pathname, "/api/auth/profile/learner-profile");
      assert.equal(requests[1].method, "PUT");
      assert.deepEqual(JSON.parse(String(requests[1].init.body)), { age: 10 });
    },
  );
});

test("setOwnLearnerProfile keeps null when the backend stores nothing", async () => {
  await withFetch(() => jsonResponse({ learner_profile: null }), async () => {
    assert.equal(await setOwnLearnerProfile({ language: "zh" }), null);
  });
});

test("learner profile failures surface as plain errors", async () => {
  await withFetch(() => new Response(null, { status: 500 }), async () => {
    await assert.rejects(getOwnLearnerProfile(), {
      message: "Failed to fetch learner profile",
    });
    await assert.rejects(setOwnLearnerProfile({ age: 9 }), {
      message: "Failed to save learner profile",
    });
  });
});

test("getProfile reads the signed-in user from /api/auth/profile", async () => {
  const user = {
    id: "u1",
    username: "alice",
    role: "user",
    created_at: "2026-01-01T00:00:00Z",
  };
  await withFetch(() => jsonResponse(user), async (requests) => {
    assert.deepEqual(await getProfile(), user);
    assert.equal(requests[0].url.pathname, "/api/auth/profile");
    assert.equal(requests[0].method, "GET");
  });
});

test("getProfile failure throws", async () => {
  await withFetch(() => new Response(null, { status: 403 }), async () => {
    await assert.rejects(getProfile(), { message: "Failed to fetch profile" });
  });
});

test("avatar marker lifecycle pins GET/PUT/DELETE on /api/auth/profile[.../avatar]", async () => {
  await withFetch(
    () => jsonResponse({ avatar: "icon:cloud:rose" }),
    async (requests) => {
      assert.equal(await setAvatarMarker("icon:moon:slate"), "icon:cloud:rose");
      assert.equal(requests[0].url.pathname, "/api/auth/profile");
      assert.equal(requests[0].method, "PUT");
      assert.deepEqual(JSON.parse(String(requests[0].init.body)), {
        avatar: "icon:moon:slate",
      });

      const form = new FormData();
      form.append("file", new Blob(["png-bytes"]), "avatar");
      assert.equal(await uploadAvatarImage(new Blob(["png-bytes"])), "icon:cloud:rose");
      assert.equal(requests[1].url.pathname, "/api/auth/profile/avatar");
      assert.equal(requests[1].method, "PUT");
      assert.ok(requests[1].init.body instanceof FormData);
      const entry = (requests[1].init.body as FormData).get("file");
      assert.ok(entry && typeof entry !== "string");
      assert.equal(entry.name, "avatar");

      await removeAvatarImage();
      assert.equal(requests[2].url.pathname, "/api/auth/profile/avatar");
      assert.equal(requests[2].method, "DELETE");
    },
  );
});

test("avatar marker falls back to the requested value when the response omits it", async () => {
  await withFetch(() => jsonResponse({}), async () => {
    assert.equal(await setAvatarMarker("icon:leaf:pink"), "icon:leaf:pink");
    assert.equal(await uploadAvatarImage(new Blob(["x"])), "");
  });
});

test("avatar write failures surface the backend detail and non-JSON fallback", async () => {
  await withFetch(
    () =>
      new Response(JSON.stringify({ detail: "File too large" }), {
        status: 413,
        headers: { "Content-Type": "application/json" },
      }),
    async () => {
      await assert.rejects(uploadAvatarImage(new Blob(["x"])), {
        message: "File too large",
      });
    },
  );
  await withFetch(() => new Response("proxy error", { status: 502 }), async () => {
    await assert.rejects(removeAvatarImage(), {
      message: "Failed to remove avatar",
    });
    await assert.rejects(setAvatarMarker("icon:leaf:pink"), {
      message: "Failed to update avatar",
    });
  });
});

test("getAccountExport downloads the archive from /api/auth/profile/export without caching", async () => {
  const zip = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])]);
  await withFetch(() => new Response(zip, { status: 200 }), async (requests) => {
    const blob = await getAccountExport();
    assert.ok(blob instanceof Blob);
    assert.equal(requests[0].url.pathname, "/api/auth/profile/export");
    assert.equal(requests[0].method, "GET");
    assert.equal(requests[0].init.cache, "no-store");
  });
});

test("getAccountExport surfaces export errors with the backend detail", async () => {
  await withFetch(
    () => jsonResponse({ detail: "Auth is disabled" }, 400),
    async () => {
      await assert.rejects(getAccountExport(), { message: "Auth is disabled" });
    },
  );
  await withFetch(() => new Response("gateway timeout", { status: 504 }), async () => {
    await assert.rejects(getAccountExport(), {
      message: "Failed to export account data",
    });
  });
});

test("deleteOwnAccount POSTs the password proof to DELETE /api/auth/profile", async () => {
  await withFetch(
    () => jsonResponse({ ok: true, already_deleted: false }),
    async (requests) => {
      await deleteOwnAccount("s3cret");
      assert.equal(requests[0].url.pathname, "/api/auth/profile");
      assert.equal(requests[0].method, "DELETE");
      assert.deepEqual(JSON.parse(String(requests[0].init.body)), {
        password: "s3cret",
      });
    },
  );
});

test("deleteOwnAccount propagates wrong-password and conflict details", async () => {
  await withFetch(() => jsonResponse({ detail: "Incorrect password" }, 401), async () => {
    await assert.rejects(deleteOwnAccount("wrong"), {
      message: "Incorrect password",
    });
  });
  await withFetch(
    () =>
      jsonResponse(
        { detail: "Demote the administrator before deleting the account." },
        409,
      ),
    async () => {
      await assert.rejects(deleteOwnAccount("s3cret"), {
        message: "Demote the administrator before deleting the account.",
      });
    },
  );
  await withFetch(() => new Response("bad gateway", { status: 502 }), async () => {
    await assert.rejects(deleteOwnAccount("s3cret"), {
      message: "Failed to delete account",
    });
  });
});

test("avatarImageUrl encodes the user id and version query", () => {
  const url = new URL(avatarImageUrl("u 1/2", "img:v7"), FALLBACK_ORIGIN);
  // encodeURIComponent escapes both the space and the slash for the path param.
  assert.equal(url.pathname, "/api/auth/avatar/u%201%2F2");
  assert.equal(url.searchParams.get("v"), "v7");
});

test("no profile API call targets the retired /api/v1 auth prefix", async () => {
  const responses = new Map<string, Response>([
    ["GET /api/auth/profile/learner-profile", jsonResponse({ learner_profile: null })],
    ["PUT /api/auth/profile/learner-profile", jsonResponse({ learner_profile: null })],
    ["GET /api/auth/profile", jsonResponse({ id: "u1", username: "a", role: "user", created_at: "" })],
    ["PUT /api/auth/profile", jsonResponse({ avatar: "" })],
    ["PUT /api/auth/profile/avatar", jsonResponse({ avatar: "" })],
    ["DELETE /api/auth/profile/avatar", new Response(null, { status: 200 })],
    ["GET /api/auth/profile/export", new Response(new Blob(["zip"]), { status: 200 })],
    ["DELETE /api/auth/profile", jsonResponse({ ok: true, already_deleted: false })],
  ]);
  const seen: string[] = [];
  await withFetch((request) => {
    const key = `${request.method} ${request.url.pathname}`;
    seen.push(key);
    const response = responses.get(key);
    assert.ok(response, `unexpected request ${key}`);
    return response;
  }, async () => {
    await getOwnLearnerProfile();
    await setOwnLearnerProfile({ age: 9 });
    await getProfile();
    await setAvatarMarker("icon:moon:slate");
    await uploadAvatarImage(new Blob(["x"]));
    await removeAvatarImage();
    await getAccountExport();
    await deleteOwnAccount("s3cret");
  });
  assert.equal(seen.length, responses.size);
  for (const key of seen) {
    assert.match(key, /^(?:GET|PUT|DELETE) \/api\/auth\//);
    assert.doesNotMatch(key, /\/api\/v1\//);
  }
});
