"""Generated from webtree-cms-api/resources/data/industries.json.
Run backend/scripts/sync_industries.py; do not edit by hand."""
from typing import Literal

INDUSTRY_LABELS: dict[str, str] = {
    "restaurant": "Restaurant, café & hospitality",
    "agency": "Agency & studio",
    "saas": "SaaS & software",
    "professional-services": "Professional services",
    "ecommerce": "Retail & e-commerce",
    "consultancy": "Consultancy",
    "nonprofit": "Non-profit & charity",
    "childcare": "Childcare & kindergarten",
    "personal": "Personal & portfolio",
    "home-services": "Home services",
    "health-wellness": "Health & wellness",
    "real-estate": "Real estate",
    "other": "Other"
}

IndustryCategoryLiteral = Literal[
    "restaurant",
    "agency",
    "saas",
    "professional-services",
    "ecommerce",
    "consultancy",
    "nonprofit",
    "childcare",
    "personal",
    "home-services",
    "health-wellness",
    "real-estate",
    "other",
]
