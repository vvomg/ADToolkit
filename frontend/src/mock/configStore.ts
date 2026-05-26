// ─── Types ───────────────────────────────────────────────────────────────────

export type ConfigScope = "modules" | "domains";

export interface ConfigNode {
  ip: string;
  role: string;
}

export interface ModuleConfig {
  [key: string]: string | number | boolean | string[] | null;
}

export interface DomainConfig {
  [key: string]: string | number | boolean | string[] | null;
}

export interface GitCommit {
  hash: string;
  short: string;
  date: string;
  message: string;
  author: string;
}

export interface DiffEntry {
  key: string;
  kind: "identical" | "changed" | "added" | "removed";
  stored: string | null;
  live: string | null;
}

export interface ObjectEntry {
  uid: string;
  name: string;
  type: "account" | "group" | "resource";
}

// ─── Nodes that support CMD (backends + frontends) ───────────────────────────

export const cmdNodes: ConfigNode[] = [
  { ip: "10.3.6.206", role: "Backend 1" },
  { ip: "10.3.6.207", role: "Backend 2" },
  { ip: "10.3.6.102", role: "Frontend 1" },
  { ip: "10.3.6.103", role: "Frontend 2" },
];

// ─── Module configs ───────────────────────────────────────────────────────────

export const mockModuleNames = [
  "Cluster", "CMD", "SMTP", "IMAP", "POP3", "WebAccess", "AntiSpam", "Antivirus",
];

export const mockModuleConfigs: Record<string, ModuleConfig> = {
  Cluster: {
    BackendList:     ["/I [10.3.6.206]", "/I [10.3.6.207]"],
    FrontendList:    ["/I [10.3.6.102]", "/I [10.3.6.103]"],
    OwnAddress:      "/I [10.3.6.206]",
    Password:        "cluster_secret_42",
    SyncInterval:    5000,
    MaxConnections:  256,
    HeartbeatMs:     3000,
    ClusterDomains:  ["example.com"],
    LogLevel:        2,
  },
  CMD: {
    ListenAddress:   "0.0.0.0",
    ListenPort:      106,
    MaxConnections:  64,
    Timeout:         300,
    NoAuthLocalhost: false,
    TLSEnabled:      false,
    LogLevel:        1,
  },
  SMTP: {
    ListenAddress:   "0.0.0.0",
    ListenPort:      25,
    SubmissionPort:  587,
    MaxConnections:  512,
    MaxMessageSize:  52428800,
    RelayAllowed:    false,
    SPFEnabled:      true,
    DKIMEnabled:     true,
    TLSRequired:     false,
    LogLevel:        2,
  },
  IMAP: {
    ListenAddress:   "0.0.0.0",
    ListenPort:      143,
    ImapsPort:       993,
    MaxConnections:  1024,
    IdleTimeout:     1800,
    TLSEnabled:      true,
    LogLevel:        1,
  },
  POP3: {
    ListenAddress:   "0.0.0.0",
    ListenPort:      110,
    Pop3sPort:       995,
    MaxConnections:  256,
    Enabled:         true,
    LogLevel:        1,
  },
  WebAccess: {
    ListenPort:      8080,
    SessionTimeout:  3600,
    MaxUploadsizeMb: 25,
    TLSEnabled:      false,
    LogLevel:        1,
  },
  AntiSpam: {
    Enabled:         true,
    SpamThreshold:   5.0,
    RejectThreshold: 10.0,
    QuarantineEnabled: true,
    RBLEnabled:      true,
    BayesEnabled:    true,
    LogLevel:        2,
  },
  Antivirus: {
    Enabled:         false,
    EngineType:      "clamav",
    ScanIncoming:    true,
    ScanOutgoing:    false,
    LogLevel:        1,
  },
};

// ─── Live data (simulates slightly different values) ──────────────────────────

export const mockLiveModuleConfigs: Record<string, ModuleConfig> = {
  Cluster: {
    ...mockModuleConfigs.Cluster,
    MaxConnections: 512,       // changed
    HeartbeatMs:    5000,      // changed
    NewlyAdded:     "v2.1.0",  // added live-only
  },
  CMD: {
    ...mockModuleConfigs.CMD,
    Timeout: 600,              // changed
  },
  SMTP: {
    ...mockModuleConfigs.SMTP,
    MaxMessageSize: 104857600, // changed
    RelayAllowed:   true,      // changed
  },
};

// ─── Domains ──────────────────────────────────────────────────────────────────

export const mockDomains = ["example.com", "internal.local"];

export const mockDomainConfigs: Record<string, DomainConfig> = {
  "example.com": {
    DisplayName:       "Example Corporation",
    MaxAccounts:       10000,
    QuotaMb:           1024,
    PasswordMinLength: 8,
    PasswordExpireDays: 90,
    IMAPEnabled:       true,
    POP3Enabled:       false,
    SMTPEnabled:       true,
    WebAccessEnabled:  true,
    AutoCreateFolders: true,
    DefaultLanguage:   "ru",
  },
  "internal.local": {
    DisplayName:       "Internal Services",
    MaxAccounts:       50,
    QuotaMb:           5120,
    PasswordMinLength: 12,
    PasswordExpireDays: 0,
    IMAPEnabled:       true,
    POP3Enabled:       false,
    SMTPEnabled:       true,
    WebAccessEnabled:  false,
    DefaultLanguage:   "en",
  },
};

// ─── Objects ─────────────────────────────────────────────────────────────────

export const mockObjects: Record<string, ObjectEntry[]> = {
  "example.com": [
    { uid: "1001", name: "postmaster",       type: "account"  },
    { uid: "1002", name: "admin",            type: "account"  },
    { uid: "1003", name: "shared-mailbox",   type: "resource" },
    { uid: "1004", name: "all-staff",        type: "group"    },
    { uid: "1005", name: "it-department",    type: "group"    },
  ],
  "internal.local": [
    { uid: "2001", name: "svc-monitoring",   type: "account"  },
    { uid: "2002", name: "svc-backup",       type: "account"  },
  ],
};

// ─── Git history ──────────────────────────────────────────────────────────────

export const mockGitLog: GitCommit[] = [
  {
    hash:    "a3f8c1d2e4b5f678901234567890abcdef123456",
    short:   "a3f8c1d",
    date:    "2026-05-25 14:23",
    message: "config: save module Cluster from 10.3.6.206",
    author:  "ADToolKit",
  },
  {
    hash:    "b9e2a4c7d1f3e567890123456789abcdef234567",
    short:   "b9e2a4c",
    date:    "2026-05-25 11:05",
    message: "config: save module SMTP from 10.3.6.206",
    author:  "ADToolKit",
  },
  {
    hash:    "c1d4f7a2b8e9c345678901234567890abcdef345",
    short:   "c1d4f7a",
    date:    "2026-05-24 17:42",
    message: "config: save domain example.com from 10.3.6.206",
    author:  "ADToolKit",
  },
  {
    hash:    "d5e8b3c9a2f1d456789012345678901234567890",
    short:   "d5e8b3c",
    date:    "2026-05-24 09:18",
    message: "config: save module IMAP from 10.3.6.207",
    author:  "ADToolKit",
  },
  {
    hash:    "e7a1c5b4d9f8e567890123456789012345678901",
    short:   "e7a1c5b",
    date:    "2026-05-23 20:00",
    message: "config: initial dump — all backends",
    author:  "ADToolKit",
  },
  {
    hash:    "f2b6d8e3c7a9f678901234567890123456789012",
    short:   "f2b6d8e",
    date:    "2026-05-22 15:30",
    message: "chore: init config-store structure",
    author:  "ADToolKit",
  },
];

export const mockCommitDiffs: Record<string, string> = {
  a3f8c1d: `--- a/config-store/modules/10.3.6.206/Cluster.yaml
+++ b/config-store/modules/10.3.6.206/Cluster.yaml
@@ -3,8 +3,8 @@
 _meta:
   ip: 10.3.6.206
   module: Cluster
-  saved_at: '2026-05-24T10:00:00+00:00'
+  saved_at: '2026-05-25T14:23:00+00:00'
 BackendList:
 - /I [10.3.6.206]
 - /I [10.3.6.207]
-MaxConnections: 256
+MaxConnections: 512
-HeartbeatMs: 3000
+HeartbeatMs: 5000`,

  b9e2a4c: `--- a/config-store/modules/10.3.6.206/SMTP.yaml
+++ b/config-store/modules/10.3.6.206/SMTP.yaml
@@ -5,6 +5,6 @@
 ListenPort: 25
 SubmissionPort: 587
-MaxMessageSize: 52428800
+MaxMessageSize: 104857600
-RelayAllowed: false
+RelayAllowed: true`,

  c1d4f7a: `--- a/config-store/domains/example.com/_domain.yaml
+++ b/config-store/domains/example.com/_domain.yaml
@@ -2,7 +2,7 @@
 DisplayName: Example Corporation
-MaxAccounts: 5000
+MaxAccounts: 10000
 QuotaMb: 1024
 PasswordMinLength: 8`,
};

// ─── Diff results ─────────────────────────────────────────────────────────────

export function computeMockDiff(module: string): DiffEntry[] {
  const stored = mockModuleConfigs[module] ?? {};
  const live   = mockLiveModuleConfigs[module] ?? stored;

  const allKeys = new Set([...Object.keys(stored), ...Object.keys(live)]);
  const entries: DiffEntry[] = [];

  for (const key of allKeys) {
    const s = stored[key] ?? null;
    const l = live[key]   ?? null;
    const sv = s !== null ? JSON.stringify(s) : null;
    const lv = l !== null ? JSON.stringify(l) : null;

    if (s === null) {
      entries.push({ key, kind: "added",    stored: null, live: lv });
    } else if (l === null) {
      entries.push({ key, kind: "removed",  stored: sv,   live: null });
    } else if (sv !== lv) {
      entries.push({ key, kind: "changed",  stored: sv,   live: lv });
    } else {
      entries.push({ key, kind: "identical", stored: sv,  live: lv });
    }
  }

  return entries;
}

// ─── Playbook terminal mock lines ─────────────────────────────────────────────

export function mockDumpLines(): string[] {
  return [
    "PLAY [Построить динамический инвентарий из Survey AWX] ****",
    "",
    "TASK [Добавить бэкенды в инвентарий] ****",
    "ok: [localhost] => (item=10.3.6.206)",
    "ok: [localhost] => (item=10.3.6.207)",
    "",
    "PLAY [Снять конфиг-дамп всех бэкендов IVA Mail через CMD] ****",
    "",
    "TASK [Создать временный каталог дампа] ****",
    "ok: [10.3.6.206 -> localhost]",
    "ok: [10.3.6.207 -> localhost]",
    "",
    "TASK [Конфиг-дамп IVA Mail через CMD (10.3.6.206)] ****",
    "changed: [10.3.6.206 -> localhost]",
    "",
    "TASK [Конфиг-дамп IVA Mail через CMD (10.3.6.207)] ****",
    "changed: [10.3.6.207 -> localhost]",
    "",
    "PLAY [Сохранить снимок конфигурации в git-репозиторий] ****",
    "",
    "TASK [Git add] ****",
    "changed: [10.3.6.108]",
    "",
    "TASK [Git commit снимка конфигурации] ****",
    "changed: [10.3.6.108]",
    "",
    "PLAY RECAP ****",
    "10.3.6.206   : ok=3  changed=1  unreachable=0  failed=0",
    "10.3.6.207   : ok=3  changed=1  unreachable=0  failed=0",
    "10.3.6.108   : ok=2  changed=2  unreachable=0  failed=0",
    "",
    "[EXIT 0] Playbook completed successfully",
  ];
}

export function mockApplyLines(): string[] {
  return [
    "PLAY [Построить динамический инвентарий из Survey AWX] ****",
    "",
    "TASK [Добавить бэкенды в инвентарий] ****",
    "ok: [localhost] => (item=10.3.6.206)",
    "ok: [localhost] => (item=10.3.6.207)",
    "",
    "PLAY [Применить конфигурацию IVA Mail через CMD] ****",
    "",
    "TASK [Проверить что config_input_dir задан] ****",
    "ok: [10.3.6.206 -> localhost]",
    "ok: [10.3.6.207 -> localhost]",
    "",
    "TASK [Применить конфигурацию через CMD (10.3.6.206)] ****",
    "changed: [10.3.6.206 -> localhost]",
    "",
    "TASK [Применить конфигурацию через CMD (10.3.6.207)] ****",
    "changed: [10.3.6.207 -> localhost]",
    "",
    "TASK [Проверить доступность CMD-порта после применения] ****",
    "ok: [10.3.6.206]",
    "ok: [10.3.6.207]",
    "",
    "PLAY RECAP ****",
    "10.3.6.206   : ok=4  changed=1  unreachable=0  failed=0",
    "10.3.6.207   : ok=4  changed=1  unreachable=0  failed=0",
    "",
    "[EXIT 0] Playbook completed successfully",
  ];
}
