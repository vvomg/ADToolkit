export type DeployPhase =
  | "configuration" | "preflight" | "infra_setup" | "node_startup"
  | "cluster_config" | "license_request" | "waiting_license"
  | "license_install" | "remaining_nodes" | "health_checks"
  | "monitoring_setup" | "reporting" | "success" | "failed";

export interface MockDeployment {
  id: string;
  startedAt: string;
  status: "running" | "success" | "failed" | "waiting_license";
  currentPhase: DeployPhase;
  progress: number;
  duration?: string;
  accounts?: number;
}

export const ALL_PHASES: DeployPhase[] = [
  "configuration", "preflight", "infra_setup", "node_startup",
  "cluster_config", "license_request", "waiting_license", "license_install",
  "remaining_nodes", "health_checks", "monitoring_setup", "reporting", "success",
];

export const mockDeployments: MockDeployment[] = [
  { id: "dep-2026-001", startedAt: "2026-05-25 14:32", status: "running",         currentPhase: "cluster_config",  progress: 45, accounts: 10000 },
  { id: "dep-2026-002", startedAt: "2026-05-25 11:17", status: "failed",          currentPhase: "failed",          progress: 30, duration: "4m 12s",  accounts: 10000 },
  { id: "dep-2026-003", startedAt: "2026-05-25 10:58", status: "success",         currentPhase: "success",         progress: 100, duration: "18m 32s", accounts: 10000 },
  { id: "dep-2026-004", startedAt: "2026-05-24 09:00", status: "success",         currentPhase: "success",         progress: 100, duration: "21m 45s", accounts: 5000  },
  { id: "dep-2026-005", startedAt: "2026-05-23 16:11", status: "waiting_license", currentPhase: "waiting_license", progress: 50, accounts: 1000 },
];

export const mockLogs: string[] = [
  "[14:32:01] [INFO]  Starting CONFIGURATION phase...",
  "[14:32:02] [INFO]  Validating cluster topology: 2 backends, 2 frontends",
  "[14:32:05] [INFO]  Starting PREFLIGHT phase...",
  "[14:32:08] [INFO]  SSH check 10.3.6.206: OK",
  "[14:32:09] [INFO]  SSH check 10.3.6.207: OK",
  "[14:32:10] [INFO]  SSH check 10.3.6.208: OK",
  "[14:32:15] [INFO]  Starting INFRA_SETUP: PostgreSQL + NFS",
  "[14:32:30] [INFO]  PostgreSQL 15 installed on 10.3.6.208",
  "[14:32:45] [INFO]  NFS v3 share configured: /srv/nfs/nfsshared",
  "[14:33:00] [INFO]  Starting NODE_STARTUP on backends (parallel)...",
  "[14:33:15] [INFO]  Backend 10.3.6.206: ivamail --backend started",
  "[14:33:17] [INFO]  Backend 10.3.6.207: ivamail --backend started",
  "[14:33:20] [INFO]  CMD AUTH 10.3.6.206 → OK",
  "[14:33:22] [INFO]  Starting CLUSTER_CONFIG...",
  "[14:33:25] [INFO]  Sending ClusterConfig: backends=2, frontends=2",
];
