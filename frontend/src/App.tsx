import { Routes, Route } from "react-router-dom";
import { Sidebar } from "@/components/Layout/Sidebar";
import { Dashboard }        from "@/pages/Dashboard";
import { Deploy }           from "@/pages/Deploy";
import { JobMonitor }       from "@/pages/JobMonitor";
import { ConfigManagement } from "@/pages/ConfigManagement";
import { History }          from "@/pages/History";

export default function App() {
  return (
    <div className="flex min-h-screen bg-base">
      <Sidebar />
      <main className="flex-1 overflow-y-auto min-h-screen">
        <Routes>
          <Route path="/"        element={<Dashboard />} />
          <Route path="/deploy"  element={<Deploy />} />
          <Route path="/monitor" element={<JobMonitor />} />
          <Route path="/config"  element={<ConfigManagement />} />
          <Route path="/history" element={<History />} />
        </Routes>
      </main>
    </div>
  );
}
