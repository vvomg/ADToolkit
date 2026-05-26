"""
PHASE 0.1: License Configuration Models

Pydantic schemas для конфигурации лицензирования.
Администратор вводит эти параметры в UI перед развертыванием.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
import re


class LicenseRequestParams(BaseModel):
    """
    Параметры лицензии вводятся администратором.
    Используются для генерации LicenseRequest через CMD протокол.
    """

    licensed_accounts: int = Field(
        description="Количество лицензированных аккаунтов",
        example=50000,
        ge=1,
        le=1000000
    )

    cluster_backends: int = Field(
        description="Количество бэкенд-узлов в кластере",
        example=2,
        ge=2,
        le=100
    )

    cluster_frontends: int = Field(
        description="Количество фронтенд-узлов",
        example=0,
        ge=0,
        le=100
    )

    licensed_resources: int = Field(
        description="Количество лицензированных ресурсов",
        example=100,
        ge=1,
        le=100000
    )

    licensee_name: str = Field(
        description="Название организации (русский)",
        example="jump.msk stand",
        min_length=3,
        max_length=200
    )

    licensee_name_eng: str = Field(
        description="Название организации (английский)",
        example="jump.msk stand",
        min_length=3,
        max_length=200
    )

    @field_validator('licensee_name', 'licensee_name_eng')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Проверка на допустимые символы."""
        if not re.match(r'^[a-zA-Zа-яА-Я0-9\s\-_\.]+$', v):
            raise ValueError(
                f"Недопустимые символы в названии: '{v}'. "
                "Разрешены: буквы, цифры, пробелы, дефисы, точки, подчёркивания."
            )
        return v.strip()

    def to_cmd_dict(self) -> dict:
        """
        Сериализует параметры в словарь для CMD:LicenseRequest.

        Маппинг snake_case → CamelCase (протокол IVA Mail).
        """
        return {
            "LicensedAccounts":  self.licensed_accounts,
            "LicenseeNameEng":   self.licensee_name_eng,
            "LicenseeName":      self.licensee_name,
            "ClusterBackends":   self.cluster_backends,
            "ClusterFrontends":  self.cluster_frontends,
            "LicensedResources": self.licensed_resources,
        }

    class Config:
        json_schema_extra = {
            "example": {
                "licensed_accounts": 50000,
                "cluster_backends": 2,
                "cluster_frontends": 0,
                "licensed_resources": 100,
                "licensee_name": "ООО Пример",
                "licensee_name_eng": "OOO Primer"
            }
        }


class LicenseRequestParamsForUI(LicenseRequestParams):
    """
    Расширенная версия для UI — включает вспомогательные поля.
    """
    notes: Optional[str] = Field(
        default=None,
        description="Дополнительные заметки для администратора",
        max_length=1000
    )


class LicenseRequestSchema(BaseModel):
    """
    Полная схема запроса лицензии (для генерации CMD команды).
    """
    params: LicenseRequestParams
    generated_at: Optional[str] = None
    deployment_id: Optional[str] = None


class ValidationResult(BaseModel):
    """
    Результат валидации конфигурации.
    """
    valid: bool = Field(description="Прошла ли валидация")
    errors: List[str] = Field(default_factory=list, description="Список ошибок")
    warnings: List[str] = Field(default_factory=list, description="Список предупреждений")

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        """Объединить два результата валидации."""
        return ValidationResult(
            valid=self.valid and other.valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings
        )
