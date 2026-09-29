"""Card design presets — keep in sync with e-business-card-mobile/src/theme/cardDesigns.ts."""

CARD_DESIGN_PRESETS: dict[str, dict[str, str]] = {
    "classic": {
        "background": "#1C2541",
        "accent": "#3A86FF",
        "text": "#FFFFFF",
        "muted": "#B8C4E0",
    },
    "slate": {
        "background": "#2F3E46",
        "accent": "#84A98C",
        "text": "#F4F7F5",
        "muted": "#CAD2C5",
    },
    "gold": {
        "background": "#2A2118",
        "accent": "#D4A574",
        "text": "#FFF8EE",
        "muted": "#C9B8A4",
    },
    "ocean": {
        "background": "#0B3954",
        "accent": "#4CC9F0",
        "text": "#F1FAFF",
        "muted": "#A8DADC",
    },
    "sand": {
        "background": "#D1C6A5",
        "accent": "#6B5B3E",
        "text": "#2A2118",
        "muted": "#4A4236",
    },
    "noir": {
        "background": "#111111",
        "accent": "#6C757D",
        "text": "#F8F9FA",
        "muted": "#ADB5BD",
    },
}

DEFAULT_CARD_DESIGN_ID = "classic"


def design_for_id(design_id: str) -> dict[str, str]:
    return CARD_DESIGN_PRESETS.get(design_id, CARD_DESIGN_PRESETS[DEFAULT_CARD_DESIGN_ID])
