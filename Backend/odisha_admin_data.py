"""
Odisha administrative divisions data.
Districts -> Blocks -> Villages hierarchy for cadastral workflows.
"""

ODISHA_DISTRICTS = {
    "Angul": {
        "blocks": ["Angul", "Athgarh", "Chhendipada", "Gamharha", "Kisinda", "Patnagarh", "Rengali", "Talcher"]
    },
    "Balangir": {
        "blocks": ["Agalpur", "Balangir", "Bhaur", "Denkanal", "Kantabanji", "Sohela", "Turekela"]
    },
    "Balasore": {
        "blocks": ["Balasore", "Bhadrak", "Bhograi", "Jaleswar", "Nilagiri", "Rasgovindapur", "Soro"]
    },
    "Bargarh": {
        "blocks": ["Agalpur", "Attabira", "Bargarh", "Bijepur", "Borsamba", "Padampur", "Ssubarnapur"]
    },
    "Bhadrak": {
        "blocks": ["Bhadrak", "Chandbali", "Dhamnagar", "Paradeep", "Tihidi", "Uchgaon"]
    },
    "Cuttack": {
        "blocks": ["Adaspur", "Athagarh", "Badamba", "Cuttack", "Jagatpur", "Mahisapat", "Niali", "Odisha", "Salipur"]
    },
    "Dhenkanal": {
        "blocks": ["Dhenkanal", "Gandi", "Gondia", "Kamakshyanagar", "Kankadahad", "Odisha", "Parjang", "Rasol"]
    },
    "Gajapati": {
        "blocks": ["Belgaum", "Gajapati", "Gadaba", "Kashinagar", "Mohana", "Odisha", "Paralakhemundi", "Raygada"]
    },
    "Ganjam": {
        "blocks": ["Asika", "Berhampur", "Buguda", "Chatrapur", "Ganjam", "Khallikote", "Kodala", "Odisha", "Polasara", "Pujagarh", "Surada"]
    },
    "Jagatsinghpur": {
        "blocks": ["Balikuda", "Chhatabar", "Erasama", "Jagatsinghpur", "Kujang", "Odisha", "Raghunathpur"]
    },
    "Jajpur": {
        "blocks": ["Bari", "Dasarathpur", "Jajpur", "Jajpur Road", "Korei", "Odisha"]
    },
    "Jharsuguda": {
        "blocks": ["Brajarajnagar", "Jharsuguda", "Odisha", "Kolabira", "Raghunathpur", "Rourkela City"]
    },
    "Kalahandi": {
        "blocks": ["Bhawanipatna", "Jayapatna", "Kalahandi", "Odisha", "Rampur", "Thuamul Rampur"]
    },
    "Kandhamal": {
        "blocks": ["Baliguda", "Baudh", "Daringbadi", "Kandhamal", "Odisha", "Raigarh", "Tumerikela", "Tumudibandh"]
    },
    "Kendrapara": {
        "blocks": ["Aul", "Garadpur", "Kendrapara", "Kendrapara", "Odisha", "Marshaghai", "Rajnagar"]
    },
    "Keonjhar": {
        "blocks": ["Anandpur", "Champua", "Joda", "Keonjhar", "Odisha", "Odisha", "Sadar", "Sundargarh", "Telkoi"]
    },
    "Khordha": {
        "blocks": ["Banapur", "Bolagarh", "Khordha", "Odisha", "Odisha", "Tangi"]
    },
    "Koraput": {
        "blocks": ["Boipariguda", "Jeypore", "Koraput", "Nalco", "Odisha", "Odisha", "Odisha", "Pottangi"]
    },
    "Malkangiri": {
        "blocks": ["Chitrakonda", "Kalimela", "Malkangiri", "Odisha", "Odisha", "Udayagiri"]
    },
    "Mayurbhanj": {
        "blocks": ["Bahabalpur", "Baripada", "Jashipur", "Keshri", "Mayurbhanj", "Odisha", "Raigarh", "Raruan", "Sulipur", "Udala"]
    },
    "Nabarangpur": {
        "blocks": ["Chandahati", "Diabarigad", "Nabarangpur", "Odisha", "Odisha", "Umarkote"]
    },
    "Nayagarh": {
        "blocks": ["Daspur", "Nayagarh", "Odisha", "Odisha", "Odisha"]
    },
    "Nuapada": {
        "blocks": ["Khariar", "Nuapada", "Odisha", "Odisha"]
    },
    "Puri": {
        "blocks": ["Astaranga", "Balangir", "Gop", "Kakatpur", "Pipili", "Puri", "Sadar"]
    },
    "Rayagada": {
        "blocks": ["Gunpur", "Odisha", "Rayagada"]
    },
    "Sambalpur": {
        "blocks": ["Athamallik", "Bargarh", "Odisha", "Rengali", "Sambalpur", "Deogarh"]
    },
    "Sundargarh": {
        "blocks": ["Bisra", "Brajarajnagar", "Jharpokharia", "Kuanrmunda", "Odisha", "Odisha", "Rajgangpur", "Rourkela City", "Subdega", "Sundargarh"]
    }
}

# Sample Gram Panchayats per block
SAMPLE_GPs = {
    "Angul-Angul": ["Angul GP", "Kusupur GP", "Nishinda GP"],
    "Angul-Athgarh": ["Athgarh GP", "Balianta GP"],
    "Bhadrak-Bhadrak": ["Bhadrak GP", "Tihidi GP"],
    "Bhadrak-Chandbali": ["Chandbali GP", "Mangaraj GP"],
}

# Sample villages per block (can be expanded)
SAMPLE_VILLAGES = {
    # Angul district
    "Angul-Angul": ["Kusupur", "Bari", "Nishinda", "Garposh", "Sundarposh", "Kulei", "Baunsuni"],
    "Angul-Athgarh": ["Athgarh", "Balianta", "Garh", "Sapangarh", "Suanala"],
    "Angul-Chhendipada": ["Chhendipada", "Rengali", "Anla"],
    "Angul-Gamharha": ["Gamharha", "Kaliapani"],
    "Angul-Kisinda": ["Kisinda", "Bindusagar"],
    "Angul-Patnagarh": ["Patnagarh", "Dhanisahi"],
    "Angul-Rengali": ["Rengali", "Talcher"],
    "Angul-Talcher": ["Talcher", "Kuanrmunda"],
    # Bhadrak district
    "Bhadrak-Bhadrak": ["Bhadrak", "Tihidi", "Balarampalli", "Kuakhia", "Chandbali"],
    "Bhadrak-Chandbali": ["Chandbali", "Mangaraj", "Dhamra", "Balasore"],
    "Bhadrak-Dhamnagar": ["Dhamnagar", "Fakirpur"],
    "Bhadrak-Paradeep": ["Paradeep", "Jagatsinghpur"],
    "Bhadrak-Tihidi": ["Tihidi", "Balasore"],
    "Bhadrak-Uchgaon": ["Uchgaon", "Bhadrak"],
    # Balasore district
    "Balasore-Balasore": ["Balasore", "Chandinichak", "Mahisapat"],
    "Balasore-Bhadrak": ["Bhadrak", "Tihidi"],
    "Balasore-Bhograi": ["Bhograi", "Haripur"],
    "Balasore-Jaleswar": ["Jaleswar", "Balianta"],
    "Balasore-Nilagiri": ["Nilagiri", "Nilakanta"],
    "Balasore-Rasgovindapur": ["Rasgovindapur", "Govindpur"],
    "Balasore-Soro": ["Soro", "Bheramara"],
}

def get_districts():
    """Return list of Odisha districts."""
    return sorted(list(ODISHA_DISTRICTS.keys()))

def get_blocks(district: str):
    """Return list of blocks in a district."""
    return ODISHA_DISTRICTS.get(district, {}).get("blocks", [])

def get_gps(district: str, block: str):
    """Return list of Gram Panchayats in a block."""
    key = f"{district}-{block}"
    if key in SAMPLE_GPs:
        return SAMPLE_GPs[key]
    # Generate default GPs based on block name
    return [f"{block} GP"]

def get_villages(district: str, block: str):
    """Return list of villages in a block."""
    key = f"{district}-{block}"
    if key in SAMPLE_VILLAGES:
        return SAMPLE_VILLAGES[key]
    # Fallback: return empty list if not explicitly defined
    return []
