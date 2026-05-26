import { create } from "zustand";
import { mockProfiles } from "@/mock/credentials";

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

function calcResourceAccounts(accounts: number): number {
  if (accounts <= 100)   return 10;
  if (accounts <= 1000)  return 50;
  if (accounts <= 5000)  return 100;
  if (accounts <= 10000) return 500;
  return Math.ceil(accounts / 10000) * 500;
}

// ---------------------------------------------------------------------------
// Exported types
// ---------------------------------------------------------------------------

export type IpInputMode = "individual" | "range";
export type SshAuthMode = "password" | "key";
export type MonitoringService =
  | "prometheus"
  | "grafana"
  | "loki"
  | "graylog"
  | "alertmanager"
  | "node_exporter";

export interface SshConfig {
  user: string;
  authMode: SshAuthMode;
  password: string;
  keyPath: string;
  port: number;
}

export interface StorageNodeConfig {
  ip: string;
  isPrimary: boolean;
}

export interface HaproxyNodeConfig {
  ip: string;
}

export interface MonitoringNodeConfig {
  ip: string;
  services: MonitoringService[];
}

// ---------------------------------------------------------------------------
// Exported utility: parseIpRange
// ---------------------------------------------------------------------------

/**
 * Parse an IP range string into an array of IP addresses (max 32).
 *
 * Examples:
 *   "10.3.6.206-207"         → ["10.3.6.206", "10.3.6.207"]
 *   "10.3.6.200-10.3.6.203"  → ["10.3.6.200","10.3.6.201","10.3.6.202","10.3.6.203"]
 *   "10.3.6.206"             → ["10.3.6.206"]
 */
export function parseIpRange(range: string): string[] {
  const trimmed = range.trim();
  if (!trimmed.includes("-")) {
    return [trimmed];
  }

  const dashIdx = trimmed.indexOf("-");
  const startStr = trimmed.slice(0, dashIdx);
  const endStr = trimmed.slice(dashIdx + 1);

  const startParts = startStr.split(".");
  if (startParts.length !== 4) return [trimmed];

  let endLastOctet: number;
  let endFirstOctets: string[];

  if (endStr.includes(".")) {
    // Full IP on the right side
    const endParts = endStr.split(".");
    if (endParts.length !== 4) return [trimmed];
    endFirstOctets = endParts.slice(0, 3);
    endLastOctet = parseInt(endParts[3], 10);
  } else {
    // Only last octet on the right side
    endFirstOctets = startParts.slice(0, 3);
    endLastOctet = parseInt(endStr, 10);
  }

  const startLastOctet = parseInt(startParts[3], 10);
  const prefix = startParts.slice(0, 3).join(".");

  if (
    isNaN(startLastOctet) ||
    isNaN(endLastOctet) ||
    endLastOctet < startLastOctet
  ) {
    return [trimmed];
  }

  // Validate prefix consistency (only when full IP provided on right)
  if (
    endStr.includes(".") &&
    endFirstOctets.join(".") !== prefix
  ) {
    return [trimmed];
  }

  const result: string[] = [];
  const limit = Math.min(endLastOctet, startLastOctet + 31);
  for (let octet = startLastOctet; octet <= limit; octet++) {
    result.push(`${prefix}.${octet}`);
  }
  return result;
}

// ---------------------------------------------------------------------------
// State interface
// ---------------------------------------------------------------------------

interface DeployState {
  // Backends
  backendInputMode: IpInputMode;
  backends: string[];
  backendRange: string;

  // Frontends
  frontendInputMode: IpInputMode;
  frontends: string[];
  frontendRange: string;

  // Infrastructure
  storageNodes: StorageNodeConfig[];
  haproxyNodes: HaproxyNodeConfig[];
  monitoringNodes: MonitoringNodeConfig[];

  // SSH
  globalSsh: SshConfig;
  usePerNodeSsh: boolean;
  perNodeSsh: Record<string, Partial<SshConfig>>;

  // Package
  packageType: "controller_file" | "url" | "local" | "server_path";
  packageValue: string;  // controller_file → filepath; url → url; server_path → path

  // License
  licensedAccounts: number;
  resourceAccounts: number;
  licensedBackends: number;
  licensedFrontends: number;
  licenseeRu: string;
  licenseeEn: string;

  // Profile / CMD / PG credentials
  selectedProfile: string;
  cmdUser: string;
  cmdPassword: string;
  pgUser: string;
  pgPassword: string;

  // Getters
  getEffectiveBackends: () => string[];
  getEffectiveFrontends: () => string[];
  getNodeSsh: (ip: string) => SshConfig;

  // Setters — backends
  setBackendInputMode: (mode: IpInputMode) => void;
  setBackends: (ips: string[]) => void;
  setBackendRange: (range: string) => void;

  // Setters — frontends
  setFrontendInputMode: (mode: IpInputMode) => void;
  setFrontends: (ips: string[]) => void;
  setFrontendRange: (range: string) => void;

  // Setters — infrastructure
  setStorageNodes: (nodes: StorageNodeConfig[]) => void;
  setHaproxyNodes: (nodes: HaproxyNodeConfig[]) => void;
  setMonitoringNodes: (nodes: MonitoringNodeConfig[]) => void;

  // Setters — SSH
  setGlobalSsh: (config: Partial<SshConfig>) => void;
  setUsePerNodeSsh: (value: boolean) => void;
  setPerNodeSsh: (ip: string, config: Partial<SshConfig>) => void;

  // Setters — package
  setPackageType: (type: "controller_file" | "url" | "local" | "server_path") => void;
  setPackageValue: (value: string) => void;

  // Setters — license
  setLicensedAccounts: (n: number) => void;
  setLicenseField: (
    key:
      | "resourceAccounts"
      | "licensedBackends"
      | "licensedFrontends"
      | "licenseeRu"
      | "licenseeEn",
    value: number | string,
  ) => void;
  recalcLicense: () => void;

  // Profiles
  selectProfile: (name: string) => void;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useDeployStore = create<DeployState>((set, get) => ({
  // --- Backends ---
  backendInputMode: "individual",
  backends: ["10.3.6.206", "10.3.6.207"],
  backendRange: "10.3.6.206-207",

  // --- Frontends ---
  frontendInputMode: "individual",
  frontends: ["10.3.6.102", "10.3.6.103"],
  frontendRange: "10.3.6.102-103",

  // --- Infrastructure ---
  storageNodes: [{ ip: "10.3.6.208", isPrimary: true }],
  haproxyNodes: [{ ip: "10.3.6.101" }],
  monitoringNodes: [
    { ip: "10.3.6.108", services: ["prometheus", "grafana", "loki", "graylog"] },
  ],

  // --- SSH ---
  globalSsh: {
    user: "user",
    authMode: "password",
    password: "DefaultP4ss",
    keyPath: "",
    port: 22,
  },
  usePerNodeSsh: false,
  perNodeSsh: {},

  // --- Package ---
  packageType: "url",
  packageValue: "",

  // --- License ---
  licensedAccounts: 10000,
  resourceAccounts: 500,
  licensedBackends: 2,
  licensedFrontends: 2,
  licenseeRu: "",
  licenseeEn: "",

  // --- Profile / CMD / PG ---
  selectedProfile: "dev-cluster",
  cmdUser: mockProfiles[0].cmdUser,
  cmdPassword: "",
  pgUser: mockProfiles[0].pgUser,
  pgPassword: "",

  // ---------------------------------------------------------------------------
  // Getters
  // ---------------------------------------------------------------------------

  getEffectiveBackends: () => {
    const { backendInputMode, backends, backendRange } = get();
    return backendInputMode === "range"
      ? parseIpRange(backendRange)
      : backends.filter(Boolean);
  },

  getEffectiveFrontends: () => {
    const { frontendInputMode, frontends, frontendRange } = get();
    return frontendInputMode === "range"
      ? parseIpRange(frontendRange)
      : frontends.filter(Boolean);
  },

  getNodeSsh: (ip: string) => {
    const { globalSsh, usePerNodeSsh, perNodeSsh } = get();
    if (!usePerNodeSsh) return globalSsh;
    return { ...globalSsh, ...(perNodeSsh[ip] ?? {}) };
  },

  // ---------------------------------------------------------------------------
  // Setters — backends
  // ---------------------------------------------------------------------------

  setBackendInputMode: (mode) => set({ backendInputMode: mode }),
  setBackends: (ips) => set({ backends: ips }),
  setBackendRange: (range) => set({ backendRange: range }),

  // ---------------------------------------------------------------------------
  // Setters — frontends
  // ---------------------------------------------------------------------------

  setFrontendInputMode: (mode) => set({ frontendInputMode: mode }),
  setFrontends: (ips) => set({ frontends: ips }),
  setFrontendRange: (range) => set({ frontendRange: range }),

  // ---------------------------------------------------------------------------
  // Setters — infrastructure
  // ---------------------------------------------------------------------------

  setStorageNodes: (nodes) => set({ storageNodes: nodes }),
  setHaproxyNodes: (nodes) => set({ haproxyNodes: nodes }),
  setMonitoringNodes: (nodes) => set({ monitoringNodes: nodes }),

  // ---------------------------------------------------------------------------
  // Setters — SSH
  // ---------------------------------------------------------------------------

  setGlobalSsh: (config) =>
    set((s) => ({ globalSsh: { ...s.globalSsh, ...config } })),

  setUsePerNodeSsh: (value) => set({ usePerNodeSsh: value }),

  setPerNodeSsh: (ip, config) =>
    set((s) => ({
      perNodeSsh: {
        ...s.perNodeSsh,
        [ip]: { ...(s.perNodeSsh[ip] ?? {}), ...config },
      },
    })),

  // ---------------------------------------------------------------------------
  // Setters — package
  // ---------------------------------------------------------------------------

  setPackageType: (type) => set({ packageType: type }),
  setPackageValue: (value) => set({ packageValue: value }),

  // ---------------------------------------------------------------------------
  // Setters — license
  // ---------------------------------------------------------------------------

  setLicensedAccounts: (n) => {
    const s = get();
    const backends = s.getEffectiveBackends().length || Math.ceil(n / 5000);
    const frontends = s.getEffectiveFrontends().length || Math.ceil(n / 5000);
    set({
      licensedAccounts: n,
      resourceAccounts: calcResourceAccounts(n),
      licensedBackends: backends,
      licensedFrontends: frontends,
    });
  },

  setLicenseField: (key, value) =>
    set({ [key]: value } as Pick<DeployState, typeof key>),

  recalcLicense: () => {
    const s = get();
    const backends = s.getEffectiveBackends().length;
    const frontends = s.getEffectiveFrontends().length;
    set({
      resourceAccounts: calcResourceAccounts(s.licensedAccounts),
      licensedBackends: backends,
      licensedFrontends: frontends,
    });
  },

  // ---------------------------------------------------------------------------
  // Profiles
  // ---------------------------------------------------------------------------

  selectProfile: (name) => {
    const profile = mockProfiles.find((p) => p.name === name);
    if (!profile) return;
    set({
      selectedProfile: name,
      cmdUser: profile.cmdUser,
      pgUser: profile.pgUser,
      globalSsh: {
        ...get().globalSsh,
        user: profile.sshUser,
        keyPath: profile.sshKeyPath,
      },
    });
  },
}));
