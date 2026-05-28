/**
 * configApi.ts — type-safe client for /api/config/* endpoints.
 *
 * Variant B read/write flow:
 *   Live read  → GET /live/...   (CMD, read-only)
 *   Save       → POST /stored/save/... (CMD read → YAML + git commit)
 *   Apply      → POST /ansible/apply/stream (YAML → nodes via Ansible)
 *   Rollback   → POST /git/rollback + POST /ansible/apply
 */

const BASE = "/api/config";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface CmdCreds {
  user: string;
  pass: string;
  port?: number;
}

export type ConfigData = Record<string, unknown>;

/** A single diff entry produced by flattening the backend diff response. */
export interface DiffEntry {
  key: string;
  kind: "changed" | "added" | "removed";
  stored: string | null;
  live: string | null;
}

/** Raw diff shape from the backend `diff_configs()` function. */
interface BackendDiff {
  added:     Record<string, unknown>;
  removed:   Record<string, unknown>;
  changed:   Record<string, { stored: unknown; live: unknown }>;
  identical: boolean;
}

export interface GitCommit {
  hash:    string;
  short:   string;   // first 7 chars of hash (computed in client)
  author:  string;
  email:   string;
  date:    string;
  message: string;
}

export interface ProfileMeta {
  slug:         string;
  name:         string;
  created_at:   string;
  updated_at:   string;
  notes:        string;
  module_names: string[];   // just names, no content
}

export interface ProfileFull extends ProfileMeta {
  modules: Record<string, Record<string, unknown>>;
}

// ── History ────────────────────────────────────────────────────────────────────

export interface HistoryEntry {
  id: number;
  applied_at: string;         // ISO datetime
  profile_slug: string;
  profile_name: string;
  apply_mode: "cmd" | "ansible";
  playbook_path: string | null;
  target_hosts: string[];
  modules_applied: string[];
  node_versions: Record<string, string> | null;
  status: "ok" | "partial" | "failed";
  errors: string[] | null;
}

// Pull-all SSE event types
export type PullAllEvent =
  | { type: "progress";      ip: string; module: string; status: "ok"; config: Record<string, unknown> }
  | { type: "error";         ip: string; module: string; error: string }
  | { type: "connect_error"; ip: string; error: string }
  | { type: "done";          total_ok: number; total_err: number };

// ── Internal helpers ───────────────────────────────────────────────────────────

async function apiFetch<T>(url: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json() as Promise<T>;
}

/** Build a query-string from a flat params object (skips undefined values). */
function qs(params: Record<string, string | number | boolean | undefined>): string {
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return pairs.length ? `?${pairs.join("&")}` : "";
}

function credParams(c: CmdCreds): Record<string, string | number> {
  return { cmd_user: c.user, cmd_password: c.pass, port: c.port ?? 106 };
}

/**
 * Flatten the backend BackendDiff into a DiffEntry[].
 * Note: "identical" keys are NOT returned by the backend — the diff table
 * will only show changed / added / removed rows.
 */
function flattenDiff(diff: BackendDiff): DiffEntry[] {
  const entries: DiffEntry[] = [];

  for (const [key, val] of Object.entries(diff.added ?? {})) {
    entries.push({ key, kind: "added", stored: null, live: JSON.stringify(val) });
  }
  for (const [key, val] of Object.entries(diff.removed ?? {})) {
    entries.push({ key, kind: "removed", stored: JSON.stringify(val), live: null });
  }
  for (const [key, val] of Object.entries(diff.changed ?? {})) {
    const cv = val as { stored: unknown; live: unknown };
    entries.push({
      key,
      kind:   "changed",
      stored: JSON.stringify(cv.stored),
      live:   JSON.stringify(cv.live),
    });
  }

  return entries;
}

// ── SSE streaming helper (POST → text/event-stream via fetch) ──────────────────

/**
 * Start streaming a POST SSE endpoint.
 * Returns an AbortController; call `.abort()` to cancel.
 *
 * The backend emits lines like:
 *   data: <text>\n\n
 */
export function streamPlaybook(
  endpoint: string,
  body: object,
  onLine: (line: string) => void,
  onDone: (ok: boolean) => void,
): AbortController {
  const ac = new AbortController();

  (async () => {
    try {
      const res = await fetch(BASE + endpoint, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
        signal:  ac.signal,
      });

      if (!res.ok || !res.body) {
        onDone(false);
        return;
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buf     = "";

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // SSE frames are separated by "\n\n"
        const frames = buf.split("\n\n");
        buf = frames.pop()!;

        for (const frame of frames) {
          for (const line of frame.split("\n")) {
            if (line.startsWith("data: ")) {
              onLine(line.slice(6));
            }
          }
        }
      }

      onDone(true);
    } catch (e: unknown) {
      if ((e as { name?: string })?.name !== "AbortError") onDone(false);
    }
  })();

  return ac;
}

// ── API surface ────────────────────────────────────────────────────────────────

export const configApi = {

  // ── Live reads (require CMD credentials) ──────────────────────────────────

  live: {
    async listModules(ip: string, creds: CmdCreds): Promise<string[]> {
      const r = await apiFetch<{ modules: string[] }>(
        `${BASE}/live/nodes/${ip}/modules${qs(credParams(creds))}`,
      );
      return r.modules;
    },

    async readModule(ip: string, module: string, creds: CmdCreds): Promise<ConfigData> {
      const r = await apiFetch<{ config: ConfigData }>(
        `${BASE}/live/nodes/${ip}/modules/${module}${qs(credParams(creds))}`,
      );
      return r.config;
    },

    async listDomains(ip: string, creds: CmdCreds): Promise<string[]> {
      const r = await apiFetch<{ domains: string[] }>(
        `${BASE}/live/domains${qs({ ip, ...credParams(creds) })}`,
      );
      return r.domains;
    },

    async readDomain(ip: string, domain: string, creds: CmdCreds): Promise<ConfigData> {
      const r = await apiFetch<{ config: ConfigData }>(
        `${BASE}/live/domains/${domain}${qs({ ip, ...credParams(creds) })}`,
      );
      return r.config;
    },

    async listObjects(ip: string, domain: string, creds: CmdCreds): Promise<string[]> {
      const r = await apiFetch<{ objects: string[] }>(
        `${BASE}/live/domains/${domain}/objects${qs({ ip, ...credParams(creds) })}`,
      );
      return r.objects;
    },
  },

  // ── Stored reads (no credentials needed) ─────────────────────────────────

  stored: {
    async listNodes(): Promise<string[]> {
      const r = await apiFetch<{ nodes: string[] }>(`${BASE}/stored/nodes`);
      return r.nodes;
    },

    async listModules(ip: string): Promise<string[]> {
      const r = await apiFetch<{ modules: string[] }>(`${BASE}/stored/nodes/${ip}/modules`);
      return r.modules;
    },

    async readModule(ip: string, module: string): Promise<ConfigData> {
      const r = await apiFetch<{ config: ConfigData }>(
        `${BASE}/stored/nodes/${ip}/modules/${module}`,
      );
      return r.config;
    },

    async listDomains(): Promise<string[]> {
      const r = await apiFetch<{ domains: string[] }>(`${BASE}/stored/domains`);
      return r.domains;
    },

    async readDomain(domain: string): Promise<ConfigData> {
      const r = await apiFetch<{ config: ConfigData }>(`${BASE}/stored/domains/${domain}`);
      return r.config;
    },

    async listObjects(domain: string): Promise<string[]> {
      const r = await apiFetch<{ objects: string[] }>(
        `${BASE}/stored/domains/${domain}/objects`,
      );
      return r.objects;
    },

    async readObject(domain: string, uid: string): Promise<ConfigData> {
      const r = await apiFetch<{ config: ConfigData }>(
        `${BASE}/stored/domains/${domain}/objects/${uid}`,
      );
      return r.config;
    },
  },

  // ── Save (live CMD → YAML + git commit) ───────────────────────────────────

  save: {
    async module(ip: string, module: string, creds: CmdCreds): Promise<{ saved: boolean; path: string }> {
      return apiFetch(`${BASE}/stored/save/module`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ip,
          module,
          cmd_user:     creds.user,
          cmd_password: creds.pass,
          port:         creds.port ?? 106,
        }),
      });
    },

    async domain(ip: string, domain: string, creds: CmdCreds): Promise<{ saved: boolean; path: string }> {
      return apiFetch(`${BASE}/stored/save/domain`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ip,
          domain,
          cmd_user:     creds.user,
          cmd_password: creds.pass,
          port:         creds.port ?? 106,
        }),
      });
    },
  },

  // ── Diff (stored vs live) ─────────────────────────────────────────────────

  diff: {
    async module(ip: string, module: string, creds: CmdCreds): Promise<DiffEntry[]> {
      const r = await apiFetch<{ diff: BackendDiff }>(
        `${BASE}/diff/module${qs({ ip, module, ...credParams(creds) })}`,
      );
      return flattenDiff(r.diff);
    },

    async domain(ip: string, domain: string, creds: CmdCreds): Promise<DiffEntry[]> {
      const r = await apiFetch<{ diff: BackendDiff }>(
        `${BASE}/diff/domain${qs({ ip, domain, ...credParams(creds) })}`,
      );
      return flattenDiff(r.diff);
    },
  },

  // ── Git operations ─────────────────────────────────────────────────────────

  git: {
    async log(maxCount = 50): Promise<GitCommit[]> {
      const r = await apiFetch<{ commits: Array<Omit<GitCommit, "short">> }>(
        `${BASE}/git/log${qs({ max_count: maxCount })}`,
      );
      return r.commits.map((c) => ({ ...c, short: c.hash.slice(0, 7) }));
    },

    async diff(commitHash: string): Promise<string> {
      const r = await apiFetch<{ diff: string }>(`${BASE}/git/diff/${commitHash}`);
      return r.diff;
    },

    async rollback(commitHash: string): Promise<void> {
      await apiFetch(`${BASE}/git/rollback`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ commit_hash: commitHash }),
      });
    },

    async tags(): Promise<string[]> {
      const r = await apiFetch<{ tags: string[] }>(`${BASE}/git/tags`);
      return r.tags;
    },
  },

  // ── Profiles ──────────────────────────────────────────────────────────────

  profiles: {
    async list(): Promise<ProfileMeta[]> {
      const r = await apiFetch<{ profiles: ProfileMeta[] }>(`${BASE}/profiles`);
      return r.profiles;
    },
    async get(slug: string): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}`);
      return r.profile;
    },
    async create(name: string, notes = ""): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, notes }),
      });
      return r.profile;
    },
    async update(slug: string, patch: { name?: string; notes?: string }): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      return r.profile;
    },
    async remove(slug: string): Promise<void> {
      await apiFetch(`${BASE}/profiles/${slug}`, { method: "DELETE" });
    },
    async upsertModule(slug: string, module: string, config: Record<string, unknown>): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}/modules/${module}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });
      return r.profile;
    },
    async removeModule(slug: string, module: string): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}/modules/${module}`, {
        method: "DELETE",
      });
      return r.profile;
    },
    async pull(slug: string, ip: string, modules: string[], cmdUser: string, cmdPass: string, port = 106): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}/pull`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip, modules, cmd_user: cmdUser, cmd_password: cmdPass, port }),
      });
      return r.profile;
    },
    async duplicate(slug: string, name: string): Promise<ProfileFull> {
      const r = await apiFetch<{ profile: ProfileFull }>(`${BASE}/profiles/${slug}/duplicate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      return r.profile;
    },
    streamApply(
      slug: string,
      hosts: string[],
      modules: string[] | undefined,
      cmdUser: string,
      cmdPass: string,
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
      mode: "cmd" | "ansible" = "cmd",
    ): AbortController {
      return streamPlaybook(
        `/profiles/${slug}/apply/stream`,
        { hosts, modules, cmd_user: cmdUser, cmd_password: cmdPass, mode },
        onLine,
        onDone,
      );
    },

    /** SSE pull-all: returns AbortController. Raw SSE lines are JSON PullAllEvent objects. */
    streamPullAll(
      slug: string,
      ips: string[],
      cmdUser: string,
      cmdPass: string,
      onEvent: (event: PullAllEvent) => void,
      onDone: () => void,
    ): AbortController {
      const ac = new AbortController();
      (async () => {
        try {
          const res = await fetch(`${BASE}/profiles/${slug}/pull-all/stream`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ ips, cmd_user: cmdUser, cmd_password: cmdPass }),
            signal:  ac.signal,
          });
          if (!res.ok || !res.body) { onDone(); return; }

          const reader  = res.body.getReader();
          const decoder = new TextDecoder();
          let   buf     = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const frames = buf.split("\n\n");
            buf = frames.pop()!;
            for (const frame of frames) {
              for (const line of frame.split("\n")) {
                if (line.startsWith("data: ")) {
                  try { onEvent(JSON.parse(line.slice(6)) as PullAllEvent); } catch { /* */ }
                }
              }
            }
          }
          onDone();
        } catch (e: unknown) {
          if ((e as { name?: string })?.name !== "AbortError") onDone();
        }
      })();
      return ac;
    },
  },

  // ── Apply history ─────────────────────────────────────────────────────────

  history: {
    async list(opts?: { limit?: number; offset?: number; profile_slug?: string; status?: string }): Promise<{ history: HistoryEntry[]; total: number }> {
      const p: Record<string, string | number | undefined> = {
        limit: opts?.limit ?? 50,
        offset: opts?.offset ?? 0,
        profile_slug: opts?.profile_slug,
        status: opts?.status,
      };
      const r = await apiFetch<{ history: HistoryEntry[]; total: number }>(
        `${BASE}/history${qs(p as Record<string, string | number | boolean | undefined>)}`,
      );
      return r;
    },

    async get(id: number): Promise<HistoryEntry> {
      const r = await apiFetch<{ entry: HistoryEntry }>(`${BASE}/history/${id}`);
      return r.entry;
    },

    async remove(id: number): Promise<void> {
      await apiFetch(`${BASE}/history/${id}`, { method: "DELETE" });
    },
  },

  // ── Ansible SSE streaming ──────────────────────────────────────────────────

  ansible: {
    streamDump(
      hosts: string[],
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
    ): AbortController {
      return streamPlaybook("/ansible/dump/stream", { hosts }, onLine, onDone);
    },

    streamApply(
      hosts: string[],
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
    ): AbortController {
      return streamPlaybook("/ansible/apply/stream", { hosts }, onLine, onDone);
    },

    streamDumpV2(
      hosts: string[],
      includeObjects: boolean,
      configTagName: string | undefined,
      cmdUser: string,
      cmdPass: string,
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
    ): AbortController {
      return streamPlaybook(
        "/ansible/dump/stream/v2",
        { hosts, include_objects: includeObjects, config_tag_name: configTagName,
          cmd_user: cmdUser, cmd_password: cmdPass },
        onLine,
        onDone,
      );
    },

    streamApplyV2(
      hosts: string[],
      mode: "full" | "diff",
      includeObjects: boolean,
      cmdUser: string,
      cmdPass: string,
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
    ): AbortController {
      return streamPlaybook(
        "/ansible/apply/stream/v2",
        { hosts, mode, include_objects: includeObjects,
          cmd_user: cmdUser, cmd_password: cmdPass },
        onLine,
        onDone,
      );
    },

    streamRollback(
      hosts: string[],
      tag: string,
      mode: "yaml_only" | "yaml_and_apply",
      cmdUser: string,
      cmdPass: string,
      onLine: (line: string) => void,
      onDone: (ok: boolean) => void,
    ): AbortController {
      return streamPlaybook(
        "/ansible/rollback/stream",
        { hosts, tag, mode, cmd_user: cmdUser, cmd_password: cmdPass },
        onLine,
        onDone,
      );
    },
  },
};
