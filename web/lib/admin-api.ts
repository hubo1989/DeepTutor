import { apiFetch, apiUrl } from "@/lib/api";
import type { AccountRole } from "@/lib/account-role";

export type AccountPreset = "standard" | "learner" | "custom";

export interface UserRecord {
  id: string;
  username: string;
  role: AccountRole;
  created_at: string;
  disabled?: boolean;
  /** Avatar marker: "", "icon:<name>:<color>", or "img:<version>". */
  avatar?: string;
  preset?: AccountPreset;
  book_permission?: {
    create: boolean;
    default: "none" | "read";
    books: Record<string, "none" | "read" | "edit">;
  };
}

export interface LearnerProfile {
  age?: number;
  grade_level?: string;
  curriculum?: string;
  language?: string;
  reading_level?: string;
  explanation_style?: string;
}

export async function getLearnerProfile(
  username: string,
): Promise<LearnerProfile | null> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/learner-profile`),
  );
  if (!res.ok) throw new Error("Failed to fetch learner profile");
  const data = (await res.json()) as {
    learner_profile?: LearnerProfile | null;
  };
  return data.learner_profile ?? null;
}

export async function setLearnerProfile(
  username: string,
  profile: LearnerProfile,
): Promise<LearnerProfile | null> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/learner-profile`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to save learner profile");
  }
  const data = (await res.json()) as {
    learner_profile?: LearnerProfile | null;
  };
  return data.learner_profile ?? null;
}

export async function listUsers(): Promise<UserRecord[]> {
  const res = await apiFetch(apiUrl("/api/auth/users"));
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

export async function deleteUser(username: string): Promise<void> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}`),
    {
      method: "DELETE",
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to delete user");
  }
}

export interface UserBatchResult {
  row?: number;
  username: string;
  ok: boolean;
  error?: string | null;
  user?: CreatedUser;
}

export interface UserImportResult {
  ok: boolean;
  created_count: number;
  failed_count: number;
  results: UserBatchResult[];
}

export interface UserBatchDeleteResult {
  ok: boolean;
  deleted_count: number;
  failed_count: number;
  results: Omit<UserBatchResult, "row">[];
}

export async function importUsers(file: File): Promise<UserImportResult> {
  const body = new FormData();
  body.append("file", file);
  const res = await apiFetch(apiUrl("/api/auth/users/import"), {
    method: "POST",
    body,
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Failed to import users",
    );
  }
  return (await res.json()) as UserImportResult;
}

export async function deleteUsers(
  usernames: string[],
): Promise<UserBatchDeleteResult> {
  const res = await apiFetch(apiUrl("/api/auth/users/batch-delete"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ usernames }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Failed to delete users",
    );
  }
  return (await res.json()) as UserBatchDeleteResult;
}

export async function setUserRole(
  username: string,
  role: AccountRole,
): Promise<void> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/role`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to update role");
  }
}

export interface CreatedUser {
  user_id: string;
  username: string;
  role: AccountRole;
  is_admin: boolean;
  preset: AccountPreset;
}

export interface LlmQuota {
  daily_tokens: number;
  monthly_tokens: number;
}

export interface EmbeddingQuota extends LlmQuota {}

export interface MineruQuota {
  daily_pages: number;
  monthly_pages: number;
  max_pages_per_file: number;
}

export interface ResourceQuota {
  llm: LlmQuota;
  embedding: EmbeddingQuota;
  mineru: MineruQuota;
}

/** @deprecated Use ResourceQuota. Kept for callers that only manage LLM limits. */
export type DefaultTokenQuota = LlmQuota;

export async function fetchDefaultQuota(): Promise<ResourceQuota> {
  const res = await apiFetch(apiUrl("/api/v1/multi-user/admin/default-quota"));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.detail ?? "Failed to load default quota");
  }
  const data = (await res.json()) as {
    default_quota: ResourceQuota;
  };
  return data.default_quota;
}

export async function saveDefaultQuota(
  quota: ResourceQuota,
): Promise<ResourceQuota> {
  const res = await apiFetch(apiUrl("/api/v1/multi-user/admin/default-quota"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_quota: quota }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.detail ?? "Failed to save default quota");
  }
  const data = (await res.json()) as {
    default_quota: ResourceQuota;
  };
  return data.default_quota;
}

/** @deprecated Use fetchDefaultQuota. */
export async function fetchDefaultTokenQuota(): Promise<LlmQuota> {
  return (await fetchDefaultQuota()).llm;
}

/** @deprecated Use saveDefaultQuota. */
export async function saveDefaultTokenQuota(
  quota: LlmQuota,
): Promise<LlmQuota> {
  const current = await fetchDefaultQuota();
  return (
    await saveDefaultQuota({
      ...current,
      llm: quota,
    })
  ).llm;
}

export async function createUser(
  username: string,
  password: string,
  preset: AccountPreset = "standard",
): Promise<CreatedUser> {
  const res = await apiFetch(apiUrl("/api/auth/users"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, preset }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && detail.length > 0 && detail[0]?.msg
          ? String(detail[0].msg)
          : "Failed to create user";
    throw new Error(message);
  }
  return (await res.json()) as CreatedUser;
}
