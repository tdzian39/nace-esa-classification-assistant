"""English titles of the NACE divisions, for the lexical pre-filter only.

The codebooks are Czech, and a description of a foreign issuer - pasted from its website, a
prospectus or a register - is usually English, so Czech-English lexical overlap is near zero
(see :mod:`core.classify.text`). Giving every division its English title as one more text to
score against closes the commonest gap without a model: "manufacture of motor vehicles"
now shares words with "builds passenger vehicles".

The titles are the Eurostat NACE wording, which CZ-NACE translates: the NACE Rev. 2 title,
except for the divisions CZ-NACE 2025 (= NACE Rev. 2.1) re-scoped - 46, 47, 52, 60, 63, 90,
95, 96 - which carry the Rev. 2.1 title (90 both). Dropping Rev. 2's "except of motor
vehicles" from 46 and 47 matters: it would pull every carmaker towards trade. The codes are
those of ``CTS_OKEC_NACE2`` (87 divisions, no 45).

Scoring input and nothing else: never shown on the page, never put in a prompt, never
emitted. What MO sees is the Czech label from the codebook.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

NACE_TITLES_EN: Final[Mapping[str, str]] = {
    "01": "Crop and animal production, hunting and related service activities",
    "02": "Forestry and logging",
    "03": "Fishing and aquaculture",
    "05": "Mining of coal and lignite",
    "06": "Extraction of crude petroleum and natural gas",
    "07": "Mining of metal ores",
    "08": "Other mining and quarrying",
    "09": "Mining support service activities",
    "10": "Manufacture of food products",
    "11": "Manufacture of beverages",
    "12": "Manufacture of tobacco products",
    "13": "Manufacture of textiles",
    "14": "Manufacture of wearing apparel",
    "15": "Manufacture of leather and related products",
    "16": (
        "Manufacture of wood and of products of wood and cork, except furniture; "
        "manufacture of articles of straw and plaiting materials"
    ),
    "17": "Manufacture of paper and paper products",
    "18": "Printing and reproduction of recorded media",
    "19": "Manufacture of coke and refined petroleum products",
    "20": "Manufacture of chemicals and chemical products",
    "21": "Manufacture of basic pharmaceutical products and pharmaceutical preparations",
    "22": "Manufacture of rubber and plastic products",
    "23": "Manufacture of other non-metallic mineral products",
    "24": "Manufacture of basic metals",
    "25": "Manufacture of fabricated metal products, except machinery and equipment",
    "26": "Manufacture of computer, electronic and optical products",
    "27": "Manufacture of electrical equipment",
    "28": "Manufacture of machinery and equipment n.e.c.",
    "29": "Manufacture of motor vehicles, trailers and semi-trailers",
    "30": "Manufacture of other transport equipment",
    "31": "Manufacture of furniture",
    "32": "Other manufacturing",
    "33": "Repair and installation of machinery and equipment",
    "35": "Electricity, gas, steam and air conditioning supply",
    "36": "Water collection, treatment and supply",
    "37": "Sewerage",
    "38": "Waste collection, treatment and disposal activities; materials recovery",
    "39": "Remediation activities and other waste management services",
    "41": "Construction of buildings",
    "42": "Civil engineering",
    "43": "Specialised construction activities",
    "46": "Wholesale trade",
    "47": "Retail trade",
    "49": "Land transport and transport via pipelines",
    "50": "Water transport",
    "51": "Air transport",
    "52": "Warehousing, storage and support activities for transportation",
    "53": "Postal and courier activities",
    "55": "Accommodation",
    "56": "Food and beverage service activities",
    "58": "Publishing activities",
    "59": (
        "Motion picture, video and television programme production, sound recording and "
        "music publishing activities"
    ),
    "60": "Programming, broadcasting, news agency and other content distribution activities",
    "61": "Telecommunications",
    "62": "Computer programming, consultancy and related activities",
    "63": (
        "Computing infrastructure, data processing, hosting and other information "
        "service activities"
    ),
    "64": "Financial service activities, except insurance and pension funding",
    "65": "Insurance, reinsurance and pension funding, except compulsory social security",
    "66": "Activities auxiliary to financial services and insurance activities",
    "68": "Real estate activities",
    "69": "Legal and accounting activities",
    "70": "Activities of head offices; management consultancy activities",
    "71": "Architectural and engineering activities; technical testing and analysis",
    "72": "Scientific research and development",
    "73": "Advertising and market research",
    "74": "Other professional, scientific and technical activities",
    "75": "Veterinary activities",
    "77": "Rental and leasing activities",
    "78": "Employment activities",
    "79": "Travel agency, tour operator reservation service and related activities",
    "80": "Security and investigation activities",
    "81": "Services to buildings and landscape activities",
    "82": "Office administrative, office support and other business support activities",
    "84": "Public administration and defence; compulsory social security",
    "85": "Education",
    "86": "Human health activities",
    "87": "Residential care activities",
    "88": "Social work activities without accommodation",
    "90": (
        "Arts creation and performing arts activities; creative, arts and entertainment activities"
    ),
    "91": "Libraries, archives, museums and other cultural activities",
    "92": "Gambling and betting activities",
    "93": "Sports activities and amusement and recreation activities",
    "94": "Activities of membership organisations",
    "95": (
        "Repair and maintenance of computers, personal and household goods, and motor "
        "vehicles and motorcycles"
    ),
    "96": "Personal service activities",
    "97": "Activities of households as employers of domestic personnel",
    "98": (
        "Undifferentiated goods- and services-producing activities of private households "
        "for own use"
    ),
    "99": "Activities of extraterritorial organisations and bodies",
}

__all__ = ["NACE_TITLES_EN"]
