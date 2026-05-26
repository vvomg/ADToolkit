export type NodeStatus = "online" | "degraded" | "offline" | "unknown";

export interface ClusterNode {
  role: string;
  ip: string;
  status: NodeStatus;
  uptime: string;
}

export const mockNodes: ClusterNode[] = [
  { role: "Controller",  ip: "10.3.6.100", status: "online",   uptime: "12d 4h" },
  { role: "HAProxy",     ip: "10.3.6.101", status: "online",   uptime: "12d 4h" },
  { role: "Frontend 1",  ip: "10.3.6.102", status: "online",   uptime: "11d 22h" },
  { role: "Frontend 2",  ip: "10.3.6.103", status: "online",   uptime: "11d 22h" },
  { role: "Backend 1",   ip: "10.3.6.206", status: "online",   uptime: "11d 20h" },
  { role: "Backend 2",   ip: "10.3.6.207", status: "degraded", uptime: "2h 15m" },
  { role: "Storage",     ip: "10.3.6.208", status: "online",   uptime: "12d 4h" },
  { role: "Monitoring",  ip: "10.3.6.108", status: "online",   uptime: "12d 4h" },
];
