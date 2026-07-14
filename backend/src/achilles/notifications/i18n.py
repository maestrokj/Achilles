"""Server-side notification strings, RU/EN — the one string home.

The DB stores the event key + params; the feed, letters and webhook titles all
render here on the reader's language. Frontend locales never duplicate these.
"""

from dataclasses import dataclass

from achilles.email.i18n import Locale


@dataclass(frozen=True, slots=True)
class Rendered:
    title: str
    body: str | None


_STRINGS: dict[str, dict[Locale, tuple[str, str | None]]] = {
    "sync.run_failure_series": {
        Locale.RU: (
            "Синхронизация «{source_name}» падает подряд",
            "Последние {count} прогонов источника завершились ошибкой. Проверьте подключение.",
        ),
        Locale.EN: (
            "Sync of “{source_name}” keeps failing",
            "The last {count} runs of the source ended in an error. Check the connection.",
        ),
    },
    "sync.source_unreachable": {
        Locale.RU: (
            "Источник «{source_name}» недоступен",
            "Проверка соединения не прошла — данные не обновляются.",
        ),
        Locale.EN: (
            "Source “{source_name}” is unreachable",
            "The connection probe failed — the data is not updating.",
        ),
    },
    "sync.run_with_losses": {
        Locale.RU: (
            "Прогон «{source_name}» завершился с потерями",
            "Синхронизация прошла, но {errors} элементов не обработано.",
        ),
        Locale.EN: (
            "Run of “{source_name}” finished with losses",
            "The sync completed, but {errors} items failed to process.",
        ),
    },
    "security.brute_force": {
        Locale.RU: (
            "Перебор пароля: {email}",
            "10 и более неудачных попыток входа за 15 минут.",
        ),
        Locale.EN: (
            "Password brute force: {email}",
            "10 or more failed sign-in attempts within 15 minutes.",
        ),
    },
    "security.webhook_rejected": {
        Locale.RU: (
            "Отклонены вебхуки: «{source_name}»",
            "10 и более входящих вызовов не прошли проверку подписи за 15 минут — "
            "проверьте секрет источника.",
        ),
        Locale.EN: (
            "Webhooks rejected: “{source_name}”",
            "10 or more inbound calls failed the signature check within 15 minutes — "
            "verify the source's secret.",
        ),
    },
    "security.role_changed": {
        Locale.RU: (
            "Смена роли: {user_name} → {new_role}",
            "Роль изменена администратором {actor_name}.",
        ),
        Locale.EN: (
            "Role change: {user_name} → {new_role}",
            "The role was changed by administrator {actor_name}.",
        ),
    },
    "security.api_key_expiring": {
        Locale.RU: (
            "API-ключ «{key_prefix}…» скоро истечёт",
            "Срок действия ключа пользователя {user_name} заканчивается {expires_on}.",
        ),
        Locale.EN: (
            "API key “{key_prefix}…” is expiring soon",
            "The key of {user_name} expires on {expires_on}.",
        ),
    },
    "budget.ai_monthly_exceeded": {
        Locale.RU: (
            "Месячный бюджет AI превышен",
            "Расходы за {month} превысили порог {budget}.",
        ),
        Locale.EN: (
            "Monthly AI budget exceeded",
            "Spending for {month} went past the {budget} threshold.",
        ),
    },
    "system.provider_unavailable": {
        Locale.RU: (
            "Провайдер «{provider_name}» недоступен",
            "Проверка соединения не прошла — модели этого провайдера не отвечают.",
        ),
        Locale.EN: (
            "Provider “{provider_name}” is unavailable",
            "The connection probe failed — this provider's models are not answering.",
        ),
    },
    "system.backup_failed": {
        Locale.RU: ("Резервная копия не создана", "Плановый бэкап завершился ошибкой."),
        Locale.EN: ("Backup failed", "The scheduled backup ended in an error."),
    },
    "system.curation_failed": {
        Locale.RU: ("Прогон доводки упал", "Curation Pass завершился ошибкой."),
        Locale.EN: ("Curation pass failed", "The curation pass ended in an error."),
    },
    "system.channel_test": {  # the admin Test button's fabricated probe
        Locale.RU: ("Achilles — проверка канала уведомлений", None),
        Locale.EN: ("Achilles — notification channel test", None),
    },
    "agent.run_failed": {
        Locale.RU: (
            "Агент «{agent_name}»: прогон провалился",
            "Последний запуск завершился ошибкой — загляните в журнал агента.",
        ),
        Locale.EN: (
            "Agent “{agent_name}”: the run failed",
            "The last run ended in an error — check the agent's journal.",
        ),
    },
    "agent.budget_exhausted": {
        Locale.RU: (
            "Агент «{agent_name}»: недельный бюджет исчерпан",
            "Запуски возобновятся с началом новой недели.",
        ),
        Locale.EN: (
            "Agent “{agent_name}”: the weekly budget is exhausted",
            "Runs resume when the new week starts.",
        ),
    },
    "agent.admin_paused": {
        Locale.RU: (
            "Агент «{agent_name}» приостановлен администратором",
            "Запуски не выполняются, пока администратор не снимет паузу.",
        ),
        Locale.EN: (
            "Agent “{agent_name}” was paused by an administrator",
            "Runs stay off until an administrator lifts the pause.",
        ),
    },
    "agent.model_removed": {
        Locale.RU: (
            "Агент «{agent_name}»: модель снята",
            "Выбранная модель больше не доступна агентам — выберите новую в редакторе.",
        ),
        Locale.EN: (
            "Agent “{agent_name}”: the model was removed",
            "The selected model is no longer available to agents — pick a new one in the editor.",
        ),
    },
    "account.role_changed": {
        Locale.RU: ("Ваша роль изменена: {new_role}", "Изменение внёс администратор."),
        Locale.EN: ("Your role was changed: {new_role}", "An administrator made the change."),
    },
    "account.temp_password": {
        Locale.RU: (
            "Вам выдан временный пароль",
            "При следующем входе его потребуется сменить.",
        ),
        Locale.EN: (
            "You were issued a temporary password",
            "You will be asked to change it on your next sign-in.",
        ),
    },
}


class _Tolerant(dict[str, object]):
    """A missing param renders as {name} instead of crashing the feed."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(key: str, params: dict[str, object] | None, locale: Locale) -> Rendered:
    """Event key + params → the reader-language title/body.

    Unknown keys degrade to the raw key — an old journal row must not break
    the feed after a catalog rename.
    """
    per_locale = _STRINGS.get(key)
    if per_locale is None:
        return Rendered(title=key, body=None)
    title, body = per_locale[locale]
    values = _Tolerant(params or {})
    return Rendered(title=title.format_map(values), body=body.format_map(values) if body else None)
