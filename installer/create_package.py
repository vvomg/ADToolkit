"""
create_package.py — создаёт deploy.zip для загрузки на сервер.

Включает:
  backend/           — FastAPI исходники
  frontend/dist/     — собранный React SPA
  iva-mail-ansible/  — структура ansible-проекта (без .git)
  deploy/            — nginx.conf, systemd unit, setup_server.sh
  requirements.txt   — Python зависимости

Запуск:
  python create_package.py
"""

import os
import sys
import zipfile
from pathlib import Path

# Корень проекта (на уровень выше installer/)
ROOT = Path(__file__).parent.parent.resolve()
OUT  = Path(__file__).parent / "deploy.zip"

# ── Правила включения/исключения ──────────────────────────────────────────────

# Паттерны файлов/папок, которые НЕ включаем
EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".tmp", ".playwright-mcp",
    "installer",  # сам себя не включаем
    # "dist" НЕ исключаем — нам нужен frontend/dist/
}
EXCLUDE_EXT = {
    ".pyc", ".pyo", ".pyd",
    ".log", ".lock",
}

# Что включаем (rel. от ROOT)
INCLUDE_ROOTS = [
    "backend",
    "frontend/dist",
    "iva-mail-ansible",
    "deploy",
]


def should_include(path: Path) -> bool:
    """Вернуть True если файл/папку нужно включить в архив."""
    parts = path.parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return False
    if path.is_file() and path.suffix in EXCLUDE_EXT:
        return False
    return True


def add_tree(zf: zipfile.ZipFile, src: Path, arc_prefix: str = "") -> int:
    """Рекурсивно добавить дерево файлов в zip. Вернуть кол-во файлов."""
    count = 0
    for item in src.rglob("*"):
        if not should_include(item):
            continue
        if item.is_file():
            arc_name = arc_prefix + "/" + str(item.relative_to(ROOT)).replace("\\", "/")
            zf.write(item, arc_name)
            count += 1
    return count


def main() -> None:
    print(f"Корень проекта: {ROOT}")
    print(f"Выходной файл:  {OUT}")
    print()

    total = 0

    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:

        for rel_root in INCLUDE_ROOTS:
            src = ROOT / Path(rel_root)
            if not src.exists():
                print(f"  [ПРОПУЩЕНО] {rel_root} — не найдено")
                continue

            if src.is_file():
                arc = rel_root.replace("\\", "/")
                zf.write(src, arc)
                total += 1
                print(f"  [+] {arc}")
            else:
                n = add_tree(zf, src)
                total += n
                print(f"  [+] {rel_root}/ — {n} файлов")

        # requirements.txt в корень архива
        req = ROOT / "backend" / "requirements.txt"
        if req.exists():
            zf.write(req, "requirements.txt")
            total += 1
            print("  [+] requirements.txt")

        # iva-mail-ansible структура если директория отсутствует
        iva_dir = ROOT / "iva-mail-ansible"
        if not iva_dir.exists():
            print("  [INFO] iva-mail-ansible/ не найден — создаём структуру в архиве")
            # Минимальная структура для config-store и playbooks
            placeholders = [
                "iva-mail-ansible/config-store/.gitkeep",
                "iva-mail-ansible/playbooks/.gitkeep",
                "iva-mail-ansible/inventory/.gitkeep",
            ]
            for p in placeholders:
                zf.writestr(p, "")
            total += len(placeholders)

    size_kb = OUT.stat().st_size // 1024
    print()
    print(f"Готово: {OUT.name}  ({size_kb} КБ, {total} файлов)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERR] {e}", file=sys.stderr)
        sys.exit(1)
