export interface CredentialProfile {
  name: string;
  sshKeyPath: string;
  sshUser: string;
  cmdUser: string;
  pgUser: string;
}

export const mockProfiles: CredentialProfile[] = [
  { name: "dev-cluster",  sshKeyPath: "/home/user/.ssh/id_rsa", sshUser: "root", cmdUser: "admin", pgUser: "postgres" },
  { name: "prod-cluster", sshKeyPath: "/root/.ssh/id_rsa",      sshUser: "root", cmdUser: "admin", pgUser: "postgres" },
];
