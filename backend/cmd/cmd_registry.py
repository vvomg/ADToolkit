"""
CMD Registry — парсит CMD-methods.md и предоставляет справочник команд.
Загружается один раз при импорте, хранится в памяти.
"""
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CMDMethodDoc:
    name: str           # оригинальное имя: "SystemInfo"
    name_lower: str     # для поиска: "systeminfo"
    section: str        # из ### заголовка: "Общие"
    syntax: str         # полная сигнатура из #### заголовка: 'SystemInfo'
    description: str    # текст описания


def _parse_cmd_methods_md(md_path: Path) -> Dict[str, CMDMethodDoc]:
    """Парсит CMD-methods.md, возвращает dict {name_lower → CMDMethodDoc}."""
    registry: Dict[str, CMDMethodDoc] = {}

    if not md_path.exists():
        return registry

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_section = "Общие"
    current_syntax: Optional[str] = None
    current_name: Optional[str] = None
    desc_lines: List[str] = []

    # Секцию "Аутентификация" и вводный блок пропускаем
    _SKIP_SECTIONS = {"аутентификация", "синтаксис"}

    def _flush():
        """Сохраняем накопленную команду в реестр."""
        nonlocal current_name, current_syntax, desc_lines
        if current_name and current_syntax and current_section.lower() not in _SKIP_SECTIONS:
            doc = CMDMethodDoc(
                name=current_name,
                name_lower=current_name.lower(),
                section=current_section,
                syntax=current_syntax,
                description="\n".join(desc_lines).strip(),
            )
            registry[current_name.lower()] = doc
        current_name = None
        current_syntax = None
        desc_lines = []

    for line in lines:
        # Определяем новую секцию (### заголовок)
        sec_match = re.match(r"^###\s+(.+)", line)
        if sec_match:
            _flush()
            current_section = sec_match.group(1).strip()
            continue

        # Определяем новую команду (#### `...`)
        cmd_match = re.match(r"^####\s+`(.+?)`", line)
        if cmd_match:
            _flush()
            raw_syntax = cmd_match.group(1).strip()
            # Первое слово — имя команды
            first_word = raw_syntax.split()[0] if raw_syntax.split() else raw_syntax
            # Убираем кавычки если есть (на всякий случай)
            first_word = first_word.strip('"\'')
            current_syntax = raw_syntax
            current_name = first_word
            desc_lines = []
            continue

        # Альтернативный заголовок команды без обратных кавычек: #### **...**
        # Пропускаем заголовки уровня #### которые не являются командами (например "#### LOGIN")
        alt_cmd_match = re.match(r"^####\s+(.+)", line)
        if alt_cmd_match:
            # Это может быть пример аутентификации — пропускаем
            _flush()
            continue

        # Строки описания — только если накапливаем команду
        if current_name is not None:
            desc_lines.append(line)

    # Не забыть последнюю команду
    _flush()

    return registry


# Ленивая загрузка — парсим при первом обращении
_registry: Optional[Dict[str, CMDMethodDoc]] = None
_sections: Optional[Dict[str, List[CMDMethodDoc]]] = None  # секция → список команд


def get_registry() -> Dict[str, CMDMethodDoc]:
    global _registry
    if _registry is None:
        md_path = Path(__file__).parent / "CMD-methods.md"
        _registry = _parse_cmd_methods_md(md_path)
    return _registry


def get_sections() -> Dict[str, List[CMDMethodDoc]]:
    global _sections
    if _sections is None:
        reg = get_registry()
        sections: Dict[str, List[CMDMethodDoc]] = {}
        for doc in reg.values():
            sections.setdefault(doc.section, []).append(doc)
        _sections = sections
    return _sections


def lookup(command_name: str) -> Optional[CMDMethodDoc]:
    """Ищет команду по имени (case-insensitive)."""
    return get_registry().get(command_name.lower())


@dataclass
class EnrichedCommand:
    name: str
    syntax: str
    section: str
    description: str
    available: bool      # присутствует в HELP-ответе сервера
    documented: bool     # есть в CMD-methods.md


def enrich_help_output(help_lines: List[str]) -> List[EnrichedCommand]:
    """
    Принимает список строк из HELP-ответа сервера (имена команд),
    обогащает данными из CMD-methods.md.
    Команды из HELP, не найденные в MD → documented=False, section="Прочие".
    """
    reg = get_registry()
    result = []
    for line in help_lines:
        name = line.strip()
        if not name:
            continue
        doc = reg.get(name.lower())
        if doc:
            result.append(EnrichedCommand(
                name=doc.name,
                syntax=doc.syntax,
                section=doc.section,
                description=doc.description,
                available=True,
                documented=True,
            ))
        else:
            result.append(EnrichedCommand(
                name=name,
                syntax=name,
                section="Прочие",
                description="",
                available=True,
                documented=False,
            ))
    return result


def full_reference() -> List[dict]:
    """Весь реестр из MD как список dict (для API)."""
    return [
        {
            "name": d.name,
            "syntax": d.syntax,
            "section": d.section,
            "description": d.description,
            "documented": True,
            "available": None,  # неизвестно без конкретной ноды
        }
        for d in get_registry().values()
    ]
