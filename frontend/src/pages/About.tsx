import type { ReactNode } from "react";
import {
  Server, Globe, Terminal,
  Shield, Cpu,
  BarChart2, Boxes,
} from "lucide-react";

// ── Primitives ─────────────────────────────────────────────────────────────────

function SectionTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="text-overlay0">{icon}</span>
      <h2 className="text-xs font-semibold text-subtext uppercase tracking-widest">{children}</h2>
    </div>
  );
}

function Tag({ children, color = "default" }: { children: ReactNode; color?: "blue" | "green" | "mauve" | "peach" | "teal" | "yellow" | "default" }) {
  const cls = {
    blue:    "bg-blue/10 text-blue border-blue/20",
    green:   "bg-green/10 text-green border-green/20",
    mauve:   "bg-mauve/10 text-mauve border-mauve/20",
    peach:   "bg-peach/10 text-peach border-peach/20",
    teal:    "bg-teal/10 text-teal border-teal/20",
    yellow:  "bg-yellow/10 text-yellow border-yellow/20",
    default: "bg-surface1/60 text-subtext border-surface1",
  }[color];
  return (
    <span className={`inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded border ${cls}`}>
      {children}
    </span>
  );
}

function StackCard({
  name, version, desc, color = "default",
}: {
  name: string; version?: string; desc: string; color?: "blue" | "green" | "mauve" | "peach" | "teal" | "yellow" | "default";
}) {
  return (
    <div className="bg-surface0 border border-surface1/60 rounded-xl px-3 py-2.5 flex items-start gap-2.5 hover:border-surface1 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold text-text">{name}</span>
          {version && <Tag color={color}>{version}</Tag>}
        </div>
        <p className="text-[11px] text-overlay0 mt-0.5 leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

function ClusterRow({ ip, role, components, color }: {
  ip: string; role: string; components: string; color: string;
}) {
  const dot = {
    blue:   "bg-blue",
    green:  "bg-green",
    mauve:  "bg-mauve",
    peach:  "bg-peach",
    teal:   "bg-teal",
    yellow: "bg-yellow",
  }[color] ?? "bg-overlay0";

  return (
    <div className="grid grid-cols-[110px_120px_1fr] gap-3 px-3 py-2 text-[11px] font-mono border-b border-surface1/30 last:border-0 hover:bg-surface1/10 transition-colors">
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
        <span className="text-blue">{ip}</span>
      </div>
      <span className="text-mauve">{role}</span>
      <span className="text-subtext truncate">{components}</span>
    </div>
  );
}

function PlaybookStep({ n, name, desc }: { n: string; name: string; desc: string }) {
  return (
    <div className="flex items-start gap-2.5 py-1.5 border-b border-surface1/20 last:border-0">
      <span className="text-[10px] font-mono text-overlay0 w-5 shrink-0 pt-0.5">{n}</span>
      <span className="text-[11px] font-mono text-blue shrink-0">{name}</span>
      <span className="text-[11px] text-subtext">→ {desc}</span>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export function About() {
  return (
    <div className="p-6 space-y-8 max-w-4xl">

      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-text">О проекте</h1>
        <p className="text-sm text-subtext mt-1 leading-relaxed">
          ADToolKit — инструмент автоматизации развёртывания почтовой системы{" "}
          <span className="text-blue font-medium">IVA Mail</span>. Управляет кластером
          через Ansible и предоставляет React SPA для настройки, мониторинга и управления конфигами.
        </p>
      </div>

      {/* Frontend */}
      <section>
        <SectionTitle icon={<Globe size={13} />}>Frontend</SectionTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          <StackCard name="React" version="19" desc="UI-фреймворк, SPA" color="blue" />
          <StackCard name="TypeScript" version="~6.0" desc="Статическая типизация" color="blue" />
          <StackCard name="Vite" version="8" desc="Сборщик, dev-server" color="peach" />
          <StackCard name="Tailwind CSS" version="3.4" desc="Utility-first стили" color="teal" />
          <StackCard name="Framer Motion" version="12" desc="Анимации, transitions" color="mauve" />
          <StackCard name="Zustand" version="5" desc="Глобальный стейт" color="yellow" />
          <StackCard name="React Router" version="7" desc="Клиентская маршрутизация" color="blue" />
          <StackCard name="Lucide React" version="1.16" desc="Иконки" color="default" />
        </div>
      </section>

      {/* Backend */}
      <section>
        <SectionTitle icon={<Server size={13} />}>Backend (Python)</SectionTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          <StackCard name="FastAPI" version="≥0.111" desc="REST API фреймворк" color="green" />
          <StackCard name="Uvicorn" version="≥0.29" desc="ASGI сервер, systemd" color="green" />
          <StackCard name="Pydantic v2" version="≥2.7" desc="Валидация схем" color="blue" />
          <StackCard name="SQLAlchemy" version="≥2.0" desc="ORM" color="yellow" />
          <StackCard name="SQLite" desc="БД: деплои, ноды, кластеры" color="default" />
          <StackCard name="Paramiko" version="≥3.4" desc="SSH клиент (async)" color="mauve" />
          <StackCard name="PyYAML" version="≥6.0" desc="Config-store YAML" color="peach" />
          <StackCard name="Jinja2" version="≥3.1" desc="Шаблоны конфигов" color="peach" />
          <StackCard name="SSE-Starlette" version="≥2.1" desc="Стриминг вывода Ansible" color="teal" />
          <StackCard name="ansible-runner" version="≥2.4" desc="Запуск плейбуков" color="green" />
          <StackCard name="cryptography" version="≥42.0" desc="SSH ключи" color="mauve" />
          <StackCard name="httpx" version="≥0.27" desc="HTTP клиент" color="default" />
        </div>
      </section>

      {/* Собственные модули */}
      <section>
        <SectionTitle icon={<Cpu size={13} />}>Собственные модули</SectionTitle>
        <div className="bg-surface0 border border-surface1/60 rounded-xl overflow-hidden">
          {[
            { name: "cmd_client.py",     desc: "TCP клиент порт 106 — протокол IVA Mail CMD (AUTH, ModuleReadConfig, LicenseRequest, LicenseInstall, ClusterConfig)" },
            { name: "ssh_manager.py",    desc: "Async SSH через Paramiko — выполнение команд на нодах, SFTP загрузка пакетов" },
            { name: "health_checker.py", desc: "Параллельная проверка SSH / CMD / PostgreSQL / NFS на всех нодах кластера" },
            { name: "config_store.py",   desc: "YAML config-store + Git versioning — сохранение, diff, rollback конфигов" },
            { name: "git_service.py",    desc: "Git операции: commit при save, log, rollback к произвольному хешу" },
            { name: "ansible_runner.py", desc: "Запуск плейбуков, SSE стриминг вывода в реальном времени" },
            { name: "orchestrator.py",   desc: "State machine из 13 фаз развёртывания: от валидации до health-check" },
          ].map(({ name, desc }) => (
            <div key={name} className="flex items-start gap-3 px-4 py-2.5 border-b border-surface1/30 last:border-0 hover:bg-surface1/10 transition-colors">
              <span className="text-[11px] font-mono text-blue shrink-0 w-40">{name}</span>
              <span className="text-[11px] text-subtext">{desc}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Ansible */}
      <section>
        <SectionTitle icon={<Terminal size={13} />}>Ansible — Плейбуки</SectionTitle>
        <div className="bg-surface0 border border-surface1/60 rounded-xl px-4 py-3">
          <PlaybookStep n="00" name="bootstrap"            desc="Подготовка всех хостов (Python, pip, базовые пакеты)" />
          <PlaybookStep n="01" name="postgres-nfs"         desc="PostgreSQL + NFS сервер" />
          <PlaybookStep n="02" name="backends-install"     desc="Установка бэкендов параллельно (ivamail_common + db_setup + cluster_config)" />
          <PlaybookStep n="02" name="license-request"      desc="Запрос лицензии run_once на be1, сохранение request.txt" />
          <PlaybookStep n="—"  name="[APPROVAL]"           desc="Ожидание загрузки license.txt и подтверждения" />
          <PlaybookStep n="02" name="license-install"      desc="LicenseInstall + рестарт нод serial: 1 с задержкой" />
          <PlaybookStep n="03" name="frontends"            desc="Установка фронтендов параллельно" />
          <PlaybookStep n="04" name="haproxy"              desc="Балансировщик — Jinja2 → haproxy.cfg (IMAP 143, SMTP 25, HTTP 80/443)" />
          <PlaybookStep n="05" name="monitoring"           desc="Prometheus + Grafana + Graylog + OpenSearch + Node Exporter" />
          <PlaybookStep n="06" name="backup-config"        desc="pg_dump cron + rsync NFS backup + git config-repo" />
          <PlaybookStep n="07" name="config-dump"          desc="Snapshot конфигов с нод → YAML в config-store/" />
          <PlaybookStep n="08" name="config-apply"         desc="Применение YAML конфигов из config-store/ к нодам" />
          <PlaybookStep n="09" name="config-rollback"      desc="Git rollback конфигов к выбранному коммиту" />
          <PlaybookStep n="hc" name="health-check"         desc="Финальный assert всех сервисов кластера" />
        </div>

        <div className="mt-3">
          <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">Ansible Collections</p>
          <div className="flex flex-wrap gap-2">
            <Tag color="green">ansible.posix</Tag>
            <Tag color="green">community.postgresql</Tag>
            <Tag color="green">community.general</Tag>
          </div>
        </div>
      </section>

      {/* Мониторинг */}
      <section>
        <SectionTitle icon={<BarChart2 size={13} />}>Стек мониторинга (10.3.6.108)</SectionTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <StackCard name="Prometheus" version="9090" desc="Сбор метрик с Node Exporter" color="peach" />
          <StackCard name="Grafana" version="3000" desc="Дашборды метрик" color="yellow" />
          <StackCard name="Graylog" version="9000/5141" desc="Централизованные логи" color="teal" />
          <StackCard name="OpenSearch" desc="Backend хранилище для Graylog" color="blue" />
          <StackCard name="Node Exporter" version="9100" desc="Метрики на каждой ноде" color="green" />
        </div>
      </section>

      {/* Инфраструктура сервера */}
      <section>
        <SectionTitle icon={<Shield size={13} />}>Инфраструктура сервера ADToolKit</SectionTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <StackCard name="Nginx" desc="Reverse proxy /api/ + React SPA статика" color="green" />
          <StackCard name="systemd" desc="adtoolkit-backend.service" color="default" />
          <StackCard name="Python venv" desc="Изоляция зависимостей бэкенда" color="blue" />
          <StackCard name="SQLite" desc="6 таблиц: деплои, ноды, кластеры" color="yellow" />
          <StackCard name="Git" desc="Версионирование конфигов в config-store/" color="mauve" />
        </div>
      </section>

      {/* Кластер IVA Mail */}
      <section>
        <SectionTitle icon={<Boxes size={13} />}>Кластер IVA Mail</SectionTitle>
        <div className="bg-surface0 border border-surface1/60 rounded-xl overflow-hidden">
          <div className="grid grid-cols-[110px_120px_1fr] gap-3 px-3 py-1.5 text-[10px] text-overlay0 uppercase tracking-widest border-b border-surface1/40">
            <span>IP</span><span>Роль</span><span>Компоненты</span>
          </div>
          <ClusterRow ip="10.3.6.100" role="controller"       components="ADToolKit, Ansible" color="blue" />
          <ClusterRow ip="10.3.6.101" role="haproxy"          components="HAProxy — IMAP 143, SMTP 25, HTTP 80/443" color="peach" />
          <ClusterRow ip="10.3.6.102" role="ivamail_frontend" components="IVA Mail --frontend" color="green" />
          <ClusterRow ip="10.3.6.103" role="ivamail_frontend" components="IVA Mail --frontend" color="green" />
          <ClusterRow ip="10.3.6.206" role="ivamail_backend"  components="IVA Mail --backend, CMD port 106" color="mauve" />
          <ClusterRow ip="10.3.6.207" role="ivamail_backend"  components="IVA Mail --backend, CMD port 106" color="mauve" />
          <ClusterRow ip="10.3.6.208" role="nfs + postgres"   components="PostgreSQL + NFS v3" color="yellow" />
          <ClusterRow ip="10.3.6.108" role="monitoring"       components="Prometheus + Grafana + Graylog + OpenSearch" color="teal" />
        </div>
      </section>

      {/* Нижний отступ чтобы контент не упирался в футер */}
      <div className="h-4" />
    </div>
  );
}
