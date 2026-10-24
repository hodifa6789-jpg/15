"""Validated deployment configuration. Empty allowlist is locked /id-only mode."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

def integer(name,default,minimum,maximum):
    try: value=int(os.getenv(name,str(default)))
    except ValueError: raise RuntimeError(f'{name}: invalid integer') from None
    if not minimum<=value<=maximum: raise RuntimeError(f'{name}: outside supported range')
    return value

def validate_url(base):
    url=urlparse(base)
    if url.scheme!='https' or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise RuntimeError('LLM_BASE_URL must use HTTPS without credentials/query/fragment')
    return base.rstrip('/')

@dataclass(frozen=True)
class Settings:
    token: str
    allowed: frozenset[int]
    model: str
    database: Path
    retention_hours: int
    max_parallel_llm: int

    @classmethod
    def from_env(cls):
        token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
        if not token or ':' not in token or token.startswith('PASTE'):
            raise RuntimeError('TELEGRAM_BOT_TOKEN is required')
        raw=os.getenv('ALLOWED_TELEGRAM_IDS','').strip()
        try:
            allowed=frozenset(int(x.strip()) for x in raw.split(',') if x.strip())
        except ValueError:
            raise RuntimeError('ALLOWED_TELEGRAM_IDS must contain numeric IDs') from None
        if any(i<=0 for i in allowed): raise RuntimeError('Telegram IDs must be positive')
        model=os.getenv('LLM_MODEL','').strip()
        if allowed:
            if os.getenv('LLM_PROVIDER','cloud')!='cloud': raise RuntimeError('Railway requires LLM_PROVIDER=cloud')
            if not model or not os.getenv('LLM_API_KEY','').strip(): raise RuntimeError('LLM_MODEL and LLM_API_KEY are required')
            validate_url(os.getenv('LLM_BASE_URL','https://openrouter.ai/api/v1'))
        root=Path(os.getenv('DATA_DIR','data'))
        if os.getenv('RAILWAY_ENVIRONMENT_ID'):
            mount=os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
            if not mount or Path(mount).resolve()!=root.resolve():
                raise RuntimeError('Attach a Railway volume at DATA_DIR before enabling persistent sessions')
        return cls(token,allowed,model,root/'sessions.sqlite3',
                   integer('DATA_RETENTION_HOURS',24,1,720),integer('MAX_PARALLEL_LLM',3,1,8))
