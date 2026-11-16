from dataclasses import dataclass, field


@dataclass(frozen=True)
class CorrespondenciaConfig:
    url: str
    token: str = field(default='', repr=False)
    timeout: float = 30
