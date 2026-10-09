"""Fallback de IA pro parser de tracklist — só entra pras linhas que o regex
heurístico (`core/tracklist_parser.py`) não conseguiu separar em artista/título.

Vários provedores "pluga e usa", todos com API compatível com o formato da
OpenAI (`/chat/completions` com `messages`), então um cliente HTTP genérico
serve pros três — só muda URL base, chave e nome do modelo:

  - GLM (Zhipu/Z.ai)  — tem modelo gratuito (glm-4-flash).
  - Grok (xAI)
  - Gemini (Google)   — usa a camada de compatibilidade OpenAI do Google.

As chaves ficam em <dados do app>/ai_settings.json (mesma pasta do
soulseek.json). Nenhuma chave vem embutida no código — cada pessoa cola a
própria na tela de configurações. Se nenhuma estiver configurada, o parser de
tracklist simplesmente não usa IA nenhuma (cai na limpeza simples).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from .soulseek import _data_dir  # mesma pasta de dados do resto do app

REQUEST_TIMEOUT = 25


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    base_url: str
    default_model: str
    signup_hint: str


# Presets editáveis pela UI (base_url e modelo podem ser sobrescritos em
# AISettings.base_urls / AISettings.models — os provedores mudam nome de
# modelo com frequência, não vale a pena travar isso no código).
PROVIDERS: dict[str, ProviderPreset] = {
    "glm": ProviderPreset(
        id="glm", label="GLM (Zhipu / Z.ai)",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        signup_hint="Chave em open.bigmodel.cn (ou z.ai) — tem modelo gratuito.",
    ),
    "grok": ProviderPreset(
        id="grok", label="Grok (xAI)",
        base_url="https://api.x.ai/v1",
        default_model="grok-4-fast",
        signup_hint="Chave em console.x.ai.",
    ),
    "gemini": ProviderPreset(
        id="gemini", label="Gemini (Google)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        signup_hint="Chave grátis em aistudio.google.com/apikey.",
    ),
}

PROVIDER_ORDER = ("glm", "grok", "gemini")


@dataclass
class AISettings:
    """Preferências de IA: qual provedor usar primeiro e as chaves de cada um."""

    active_provider: str = "glm"
    keys: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)       # override por provedor
    base_urls: dict[str, str] = field(default_factory=dict)    # override por provedor

    @classmethod
    def path(cls):
        return _data_dir() / "ai_settings.json"

    @classmethod
    def load(cls) -> "AISettings":
        cfg = cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except Exception:
            return cfg
        for key in ("active_provider", "keys", "models", "base_urls"):
            if key in raw and raw[key] is not None:
                setattr(cfg, key, raw[key])
        return cfg

    def save(self) -> None:
        data = {
            "active_provider": self.active_provider,
            "keys": self.keys,
            "models": self.models,
            "base_urls": self.base_urls,
        }
        try:
            self.path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def key_for(self, provider_id: str) -> str:
        return (self.keys.get(provider_id) or "").strip()

    def model_for(self, provider_id: str) -> str:
        return self.models.get(provider_id) or PROVIDERS[provider_id].default_model

    def base_url_for(self, provider_id: str) -> str:
        return self.base_urls.get(provider_id) or PROVIDERS[provider_id].base_url

    def configured_providers(self) -> list[str]:
        """Provedores com chave preenchida, começando pelo ativo."""
        ordered = [p for p in (self.active_provider, *PROVIDER_ORDER)]
        seen = set()
        out = []
        for p in ordered:
            if p in PROVIDERS and p not in seen and self.key_for(p):
                seen.add(p)
                out.append(p)
        return out

    def has_any_key(self) -> bool:
        return bool(self.configured_providers())


def _chat_completion(base_url: str, api_key: str, model: str, messages: list[dict],
                     timeout: float = REQUEST_TIMEOUT) -> Optional[str]:
    """POST genérico num endpoint estilo OpenAI. Nunca levanta exceção."""
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "temperature": 0},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_array(text: str):
    fence = _JSON_FENCE_RE.search(text)
    payload = fence.group(1) if fence else text
    start, end = payload.find("["), payload.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(payload[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return None


def _build_prompt(raw_lines: list[str]) -> list[dict]:
    numbered = "\n".join(f"{i + 1}) {line}" for i, line in enumerate(raw_lines))
    system = (
        "Você separa linhas de um tracklist de DJ/rádio em artista e título de "
        "faixa. As linhas podem ter timestamp, numeração, gravadora entre "
        "colchetes e formatação inconsistente — ignore tudo isso e extraia só "
        "o nome do(s) artista(s) e o nome da faixa (mantenha remix/edit se "
        "fizer parte do título). Responda apenas com um array JSON, sem texto "
        'antes ou depois, no formato: [{"i": 1, "artist": "...", "title": "..."}]. '
        "Um item por linha numerada recebida, na mesma numeração. Se não der "
        'pra identificar artista/título numa linha, use "artist": "".'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": numbered},
    ]


def extract_tracks_with_ai(
    raw_lines: list[str],
    settings: AISettings,
    log: Optional[Callable[[str], None]] = None,
) -> list["ParsedTrack"]:  # noqa: F821 - tipo importado só dentro da função (evita ciclo)
    from .tracklist_parser import ParsedTrack

    log = log or (lambda *_: None)
    if not raw_lines:
        return []

    providers = settings.configured_providers()
    if not providers:
        return []

    messages = _build_prompt(raw_lines)

    for provider_id in providers:
        preset = PROVIDERS[provider_id]
        content = _chat_completion(
            settings.base_url_for(provider_id),
            settings.key_for(provider_id),
            settings.model_for(provider_id),
            messages,
        )
        if not content:
            log(f"{preset.label}: sem resposta, tentando próximo provedor.")
            continue

        items = _extract_json_array(content)
        if not isinstance(items, list):
            log(f"{preset.label}: resposta não veio em JSON, tentando próximo provedor.")
            continue

        out: list[ParsedTrack] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("i", 0)) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(raw_lines)):
                continue
            artist = str(item.get("artist") or "").strip()
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            out.append(ParsedTrack(artist=artist, title=title, raw=raw_lines[idx], source="ai"))

        if out:
            log(f"{preset.label}: {len(out)}/{len(raw_lines)} linha(s) resolvida(s) por IA.")
            return out
        log(f"{preset.label}: resposta veio vazia, tentando próximo provedor.")

    return []
