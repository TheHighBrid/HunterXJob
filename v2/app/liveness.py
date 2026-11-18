from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Liveness(str, Enum):
    LIVE = "live"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class LivenessResult:
    status: Liveness
    reason: str


_EXPIRED_MARKERS = (
    "job is no longer available",
    "job no longer available",
    "position has been filled",
    "position is closed",
    "job posting has expired",
    "posting has expired",
    "this role is no longer accepting applications",
    "no longer accepting applications",
    "not accepting applications",
    "applications are closed",
    "application period has ended",
    "application period has closed",
    "page not found",
    # French & Canadian Government / Public Sector expired markers
    "n'accepte plus les candidatures",
    "n'accepte plus de candidatures",
    "ne reçoit plus de candidatures",
    "ne reçoit plus les candidatures",
    "cette offre d'emploi n'est plus disponible",
    "offre d'emploi n'est plus disponible",
    "ce poste n'est plus disponible",
    "la période de candidature est terminée",
    "la période de mise en candidature est terminée",
    "le concours est fermé",
    "le concours a pris fin",
    "candidatures closes",
)
_BLOCKED_MARKERS = (
    "captcha",
    "verify you are human",
    "access denied",
    "unusual traffic",
    "cloudflare challenge",
)
_LIVE_MARKERS = (
    "apply for this job",
    "submit application",
    "apply now",
    "start application",
    "begin application",
    # French & Canadian Government live markers
    "postuler",
    "soumettre ma candidature",
    "postulez maintenant",
    "déposer votre candidature",
    "soumettre une demande",
)


def classify_liveness(status_code: int | None, final_url: str, body_text: str) -> LivenessResult:
    text = (body_text or "").lower()
    if status_code in {404, 410}:
        return LivenessResult(Liveness.EXPIRED, f"HTTP {status_code}")
    if status_code in {401, 403, 429}:
        return LivenessResult(Liveness.BLOCKED, f"HTTP {status_code}")
    if any(marker in text for marker in _BLOCKED_MARKERS):
        return LivenessResult(Liveness.BLOCKED, "challenge or access block detected")
    if any(marker in text for marker in _EXPIRED_MARKERS):
        return LivenessResult(Liveness.EXPIRED, "expired-job marker detected")
    if status_code is not None and 200 <= status_code < 400 and any(marker in text for marker in _LIVE_MARKERS):
        return LivenessResult(Liveness.LIVE, "application markers detected")
    if final_url and status_code is not None and 200 <= status_code < 400:
        return LivenessResult(Liveness.REVIEW, "page responded but liveness is ambiguous")
    return LivenessResult(Liveness.REVIEW, "insufficient response evidence")
