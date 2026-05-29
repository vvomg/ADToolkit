import { NavLink } from "react-router-dom";
import { LayoutDashboard, Rocket, Monitor, Settings2, ScrollText, Info, Search } from "lucide-react";
import { useJobStore } from "@/stores/jobStore";

const navItems = [
  { to: "/",        icon: LayoutDashboard, label: "Dashboard"   },
  { to: "/deploy",  icon: Rocket,          label: "Deploy"      },
  { to: "/monitor", icon: Monitor,         label: "Job Monitor", badge: true },
  { to: "/config",  icon: Settings2,       label: "Config Mgmt" },
  { to: "/history", icon: ScrollText,      label: "History"     },
  { to: "/search",  icon: Search,          label: "Search"      },
];

export function Sidebar() {
  const active = useJobStore((s) => s.activeDeployment);

  return (
    <aside className="w-[220px] flex-shrink-0 bg-mantle border-r border-surface0 flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="px-5 py-4 border-b border-surface0">
        <span className="text-blue font-bold text-lg tracking-tight">⬡ ADToolKit</span>
        <p className="text-overlay0 text-xs mt-0.5 font-mono">IVA Mail Cluster</p>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
        {navItems.map(({ to, icon: Icon, label, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-surface0 text-text font-medium"
                  : "text-subtext hover:bg-surface0/50 hover:text-text"
              }`
            }
          >
            <Icon size={16} />
            <span className="flex-1">{label}</span>
            {badge && active?.status === "running" && (
              <span className="bg-green/20 text-green text-[10px] font-mono px-1.5 py-0.5 rounded-full leading-none">
                RUN
              </span>
            )}
            {badge && active?.status === "waiting_license" && (
              <span className="bg-yellow/20 text-yellow text-[10px] font-mono px-1.5 py-0.5 rounded-full leading-none animate-pulse">
                LIC
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* About — прилипает к футеру */}
      <div className="px-2 pb-1 border-t border-surface0/60 pt-1">
        <NavLink
          to="/about"
          className={({ isActive }) =>
            `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
              isActive
                ? "bg-surface0 text-text font-medium"
                : "text-overlay0 hover:bg-surface0/50 hover:text-subtext"
            }`
          }
        >
          <Info size={16} />
          <span>About</span>
        </NavLink>
      </div>

      {/* Footer version */}
      <div className="px-5 py-3 border-t border-surface0 text-overlay0 text-xs font-mono">
        v0.1.0
      </div>
    </aside>
  );
}
