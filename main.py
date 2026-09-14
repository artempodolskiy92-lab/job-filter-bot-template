import asyncio
import os
import re
import traceback
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import Channel

load_dotenv()

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION_STRING = os.environ["TG_SESSION_STRING"]

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")

FOLDER_NAME = os.environ["FOLDER_NAME"]
CONFIGURED_KEYWORDS = [
    keyword.strip().lower()
    for keyword in os.environ["KEYWORDS"].split(",")
    if keyword.strip()
]

# These words describe work format or geography, not a target profession.
# Letting any of them pass Stage 1 causes almost every post in a remote-jobs
# channel to trigger a paid LLM request.
CONTEXT_ONLY_KEYWORDS = {
    "remote",
    "fully remote",
    "удаленно",
    "удалённо",
    "b2b",
    "contractor",
}

# Keep Russian role names in code so the production Railway configuration
# remains backward-compatible with the existing English-heavy KEYWORDS value.
BUILT_IN_ROLE_KEYWORDS = [
    "бизнес-аналитик",
    "бизнес аналитик",
    "системный аналитик",
    "системный бизнес-аналитик",
    "руководитель проекта",
    "руководитель проектов",
    "менеджер проекта",
    "менеджер проектов",
    "проджект-менеджер",
    "проджект менеджер",
    "менеджер по внедрению",
    "руководитель внедрения",
    "консультант по внедрению",
    "руководитель программы",
    "руководитель программ",
    "менеджер программы",
    "менеджер программ",
    "процессный аналитик",
    "аналитик бизнес-процессов",
    "бизнес-процессы",
    "пресейл",
    "пре-сейл",
]

IGNORED_CONTEXT_KEYWORDS = [
    keyword for keyword in CONFIGURED_KEYWORDS if keyword in CONTEXT_ONLY_KEYWORDS
]
ROLE_KEYWORDS = list(
    dict.fromkeys(
        [
            keyword
            for keyword in CONFIGURED_KEYWORDS
            if keyword not in CONTEXT_ONLY_KEYWORDS
        ]
        + BUILT_IN_ROLE_KEYWORDS
    )
)

# Resume posts often contain the same role names as vacancies, so the role
# keyword filter alone cannot distinguish them. Only strong candidate signals
# near the beginning of a post are used here: a vacancy may legitimately say
# "send your CV" near the end and must not be rejected for that.
RESUME_HEADER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"#(?:резюме|resume|opentowork|open_to_work|ищуработу|ищу_работу)\b",
        r"^\s*(?:резюме|resume|curriculum vitae)\s*[:—-]",
        r"\b(?:open\s+to\s+work|looking\s+for\s+(?:a\s+)?job|seeking\s+new\s+opportunities)\b",
        r"\b(?:ищу\s+(?:работу|вакансию|возможности)|в\s+поиске\s+(?:работы|вакансии)|"
        r"открыт[аы]?\s+к\s+(?:предложениям|новым\s+возможностям))\b",
    )
]
CV_HEADER_PATTERN = re.compile(r"#cv\b", re.IGNORECASE)
RESUME_FIRST_PERSON_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:обо\s+мне|мой\s+опыт|мои\s+навыки|мои\s+сильные\s+стороны)\b",
        r"\b(?:меня\s+зовут|я\s+(?:работал|работала|работаю|специализируюсь))\b",
        r"\b(?:my\s+experience|about\s+me|my\s+skills)\b",
    )
]
RESUME_FIELD_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"^\s*(?:желаемая\s+)?должность\s*:",
        r"^\s*формат(?:\s+работы)?\s*:",
        r"^\s*занятость\s*:",
        r"^\s*(?:зарплатные\s+ожидания|ожидания\s+по\s+зарплате|зп)\s*:",
        r"^\s*(?:контакты(?:\s+для\s+связи)?|telegram|tg|email|почта)\s*:",
    )
]

# Vacancy channels sometimes publish a weekly digest whose entries link back
# to full posts in the same channel. Scoring both the digest and its source
# posts creates duplicates and wastes an LLM request.
DIGEST_MARKERS = (
    "дайджест ваканс",
    "подборка ваканс",
    "вакансии за неделю",
    "вакансии недели",
    "job digest",
    "jobs digest",
    "weekly jobs",
    "jobs roundup",
)
TELEGRAM_MESSAGE_LINK_PATTERN = re.compile(
    r"https?://(?:t\.me|telegram\.me)/(?:s/)?([a-zA-Z0-9_]+)/(\d+)"
)
VACANCY_CATALOG_HEADER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bсегодня\s+мы\s+нашли\s+для\s+вас\s+\d[\d\s]*\s+ваканс",
        r"\b(?:today\s+)?we\s+(?:have\s+)?found\s+\d[\d,\s]*\s+"
        r"(?:jobs?|vacanc(?:y|ies)|open\s+roles?)\b",
    )
]
ROLE_COUNT_LINE_PATTERN = re.compile(
    r"(?m)^\s*[^\n]{1,80}?\s+(?:-|—|–|:)\s*\d[\d\s]*\s*$"
)

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
LOW_BALANCE_THRESHOLD = float(os.environ.get("OPENROUTER_LOW_BALANCE_THRESHOLD", "2"))

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "profile.md")
with open(PROFILE_PATH, encoding="utf-8") as f:
    PROFILE = f.read()


def looks_like_resume(text: str) -> bool:
    if not text:
        return False

    # Candidate labels and introductions normally appear at the top. Limiting
    # the inspected area prevents "send CV/resume" in vacancy instructions
    # from becoming a false rejection.
    header = text[:1500]
    header_start = text[:500]
    if any(pattern.search(header_start) for pattern in RESUME_HEADER_PATTERNS):
        return True

    field_count = sum(bool(pattern.search(header)) for pattern in RESUME_FIELD_PATTERNS)
    has_first_person_signal = any(
        pattern.search(header) for pattern in RESUME_FIRST_PERSON_PATTERNS
    )
    if field_count >= 3 and has_first_person_signal:
        return True

    # #CV by itself is not enough: recruiters sometimes use it in vacancy
    # hashtags. It becomes a resume signal only together with candidate-style
    # fields or first-person self-description.
    return bool(CV_HEADER_PATTERN.search(header_start)) and (
        field_count >= 2 or has_first_person_signal
    )


def contains_role_keyword(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in ROLE_KEYWORDS)


def passes_keyword_filter(text: str) -> bool:
    return contains_role_keyword(text) and not looks_like_resume(text)


def get_message_urls(message) -> list[str]:
    urls = []
    for entity, visible_text in message.get_entities_text():
        url = getattr(entity, "url", None)
        if url is None and entity.__class__.__name__ == "MessageEntityUrl":
            url = visible_text
        if url:
            urls.append(url.strip())
    return urls


def looks_like_link_digest(message, channel_username: str | None) -> bool:
    text = message.text or ""
    if not any(marker in text[:500].lower() for marker in DIGEST_MARKERS):
        return False
    if not channel_username:
        return False

    linked_message_ids = set()
    for url in get_message_urls(message):
        match = TELEGRAM_MESSAGE_LINK_PATTERN.fullmatch(url)
        if not match:
            continue
        linked_username, linked_message_id = match.groups()
        if linked_username.lower() == channel_username.lower():
            linked_message_ids.add(linked_message_id)

    # Requiring several same-channel source links avoids treating a normal
    # vacancy that references one related Telegram post as a digest.
    return len(linked_message_ids) >= 2


def looks_like_vacancy_catalog(message) -> bool:
    text = message.text or ""
    has_catalog_header = any(
        pattern.search(text[:500]) for pattern in VACANCY_CATALOG_HEADER_PATTERNS
    )
    if not has_catalog_header:
        return False

    role_count_lines = len(ROLE_COUNT_LINE_PATTERN.findall(text))
    linked_categories = len(set(get_message_urls(message)))
    return role_count_lines >= 5 or linked_categories >= 5


def looks_like_collection_post(message, channel_username: str | None) -> bool:
    return (
        looks_like_link_digest(message, channel_username)
        or looks_like_vacancy_catalog(message)
    )


def build_message_link(entity, message_id: int) -> str:
    if getattr(entity, "username", None):
        return f"https://t.me/{entity.username}/{message_id}"
    internal_id = str(entity.id)
    return f"https://t.me/c/{internal_id}/{message_id}"


def score_with_llm(text: str) -> dict | None:
    prompt = f"""Ты помогаешь фильтровать вакансии под конкретный профиль кандидата.

Профиль кандидата:
{PROFILE}

Текст вакансии:
{text}

Если это резюме, CV, анкета соискателя или пост человека о поиске работы,
обязательно ответь FIT: no, даже если его опыт хорошо совпадает с профилем.

Ответь СТРОГО в формате:
FIT: yes/no
COMMENT: <1-2 предложения почему подходит или не подходит>
"""
    resp = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    fit = False
    comment = ""
    for line in content.splitlines():
        line = line.strip()
        if line.upper().startswith("FIT:"):
            fit = "yes" in line.lower()
        elif line.upper().startswith("COMMENT:"):
            comment = line.split(":", 1)[1].strip()

    if not fit:
        return None
    return {"comment": comment or content.strip()}


def get_openrouter_balance_warning() -> str | None:
    resp = requests.get(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    remaining = data["total_credits"] - data["total_usage"]
    if remaining < LOW_BALANCE_THRESHOLD:
        return (f"⚠️ Баланс OpenRouter заканчивается: осталось ${remaining:.2f} "
                f"(порог предупреждения: ${LOW_BALANCE_THRESHOLD:.2f}). "
                f"Пополни на openrouter.ai/settings/credits, иначе фильтр вакансий перестанет работать.")
    return None


async def get_folder_channels(client: TelegramClient, folder_title: str) -> list[Channel]:
    await client.get_dialogs()  # populate entity cache so peers below resolve

    result = await client(GetDialogFiltersRequest())
    dialog_filters = getattr(result, "filters", result)

    for dialog_filter in dialog_filters:
        title = getattr(dialog_filter, "title", None)
        title_text = getattr(title, "text", title)
        if title_text != folder_title:
            continue

        channels = []
        for peer in getattr(dialog_filter, "include_peers", []):
            try:
                entity = await client.get_entity(peer)
            except Exception:
                continue
            if isinstance(entity, Channel):
                channels.append(entity)
        return channels

    raise ValueError(f"Folder '{folder_title}' not found among your Telegram folders.")


async def main():
    client = TelegramClient(StringSession(TG_SESSION_STRING), TG_API_ID, TG_API_HASH)
    await client.start()

    try:
        since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        matches = []

        print(
            f"Stage 1 uses {len(ROLE_KEYWORDS)} role keywords; "
            f"ignores context-only keywords: {IGNORED_CONTEXT_KEYWORDS}"
        )

        channels = await get_folder_channels(client, FOLDER_NAME)
        print(f"Found {len(channels)} channels in folder '{FOLDER_NAME}': "
              f"{[getattr(c, 'username', c.id) for c in channels]}")

        for entity in channels:
            scanned = 0
            kept = 0
            resumes_rejected = 0
            collections_rejected = 0
            async for message in client.iter_messages(entity, offset_date=None, reverse=False):
                if message.date < since:
                    break
                scanned += 1
                if not message.text:
                    continue
                if looks_like_collection_post(message, getattr(entity, "username", None)):
                    collections_rejected += 1
                    continue
                if not contains_role_keyword(message.text):
                    continue
                if looks_like_resume(message.text):
                    resumes_rejected += 1
                    continue
                kept += 1

                result = score_with_llm(message.text)
                if result is None:
                    continue

                link = build_message_link(entity, message.id)
                matches.append((link, result["comment"]))
            print(f"  {getattr(entity, 'username', entity.id)}: scanned={scanned}, "
                  f"collections rejected={collections_rejected}, "
                  f"resumes rejected={resumes_rejected}, "
                  f"passed keyword filter={kept}")

        balance_warning = get_openrouter_balance_warning()

        if not matches and not balance_warning:
            print("No matching vacancies found in the lookback window.")
            return

        summary_lines = []
        if balance_warning:
            summary_lines.append(balance_warning + "\n")
        if matches:
            summary_lines.append("Вакансии за последние сутки:\n")
            for link, comment in matches:
                summary_lines.append(f"{link}\n{comment}\n")

        await client.send_message("me", "\n".join(summary_lines))
        print(f"Sent message to Saved Messages ({len(matches)} matches, "
              f"balance_warning={'yes' if balance_warning else 'no'}).")

    except Exception:
        error_text = f"🔴 Job filter bot упал с ошибкой:\n\n{traceback.format_exc()[-3000:]}"
        try:
            await client.send_message("me", error_text)
        except Exception:
            pass
        raise

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
