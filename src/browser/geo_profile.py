"""
Map proxy geo (country_code + optional city) to browser locale, timezone and
Accept-Language so the browser fingerprint matches the proxy's IP geolocation.

The data is intentionally minimal — just enough to make the browser look
consistent with the most common proxy geos. For countries with many time zones
(US, RU, CA, AU, BR) we default to the most common zone and override by city
when a well-known one is provided.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeoProfile:
    locale: str            # e.g. "es-ES"
    timezone_id: str       # IANA tz name, e.g. "Europe/Madrid"
    accept_language: str   # Full Accept-Language header


# Primary map: country_code -> default profile.
# Covers all UN member states + common dependencies / special territories.
_COUNTRY_MAP: dict[str, GeoProfile] = {
    # Europe
    "AD": GeoProfile("ca-AD", "Europe/Andorra", "ca-AD,ca;q=0.9,es;q=0.8,fr;q=0.7,en;q=0.6"),
    "AL": GeoProfile("sq-AL", "Europe/Tirane", "sq-AL,sq;q=0.9,en;q=0.8"),
    "AM": GeoProfile("hy-AM", "Asia/Yerevan", "hy-AM,hy;q=0.9,ru;q=0.8,en;q=0.7"),
    "AT": GeoProfile("de-AT", "Europe/Vienna", "de-AT,de;q=0.9,en;q=0.8"),
    "AZ": GeoProfile("az-AZ", "Asia/Baku", "az-AZ,az;q=0.9,ru;q=0.8,en;q=0.7"),
    "BA": GeoProfile("bs-BA", "Europe/Sarajevo", "bs-BA,bs;q=0.9,hr;q=0.8,sr;q=0.7,en;q=0.6"),
    "BE": GeoProfile("nl-BE", "Europe/Brussels", "nl-BE,nl;q=0.9,fr;q=0.8,en;q=0.7"),
    "BG": GeoProfile("bg-BG", "Europe/Sofia", "bg-BG,bg;q=0.9,en;q=0.8"),
    "BY": GeoProfile("be-BY", "Europe/Minsk", "be-BY,be;q=0.9,ru;q=0.8,en;q=0.7"),
    "CH": GeoProfile("de-CH", "Europe/Zurich", "de-CH,de;q=0.9,fr;q=0.8,it;q=0.7,en;q=0.6"),
    "CY": GeoProfile("el-CY", "Asia/Nicosia", "el-CY,el;q=0.9,tr;q=0.8,en;q=0.7"),
    "CZ": GeoProfile("cs-CZ", "Europe/Prague", "cs-CZ,cs;q=0.9,en;q=0.8"),
    "DE": GeoProfile("de-DE", "Europe/Berlin", "de-DE,de;q=0.9,en;q=0.8"),
    "DK": GeoProfile("da-DK", "Europe/Copenhagen", "da-DK,da;q=0.9,en;q=0.8"),
    "EE": GeoProfile("et-EE", "Europe/Tallinn", "et-EE,et;q=0.9,ru;q=0.8,en;q=0.7"),
    "ES": GeoProfile("es-ES", "Europe/Madrid", "es-ES,es;q=0.9,en;q=0.8"),
    "FI": GeoProfile("fi-FI", "Europe/Helsinki", "fi-FI,fi;q=0.9,sv;q=0.8,en;q=0.7"),
    "FO": GeoProfile("fo-FO", "Atlantic/Faroe", "fo-FO,fo;q=0.9,da;q=0.8,en;q=0.7"),
    "FR": GeoProfile("fr-FR", "Europe/Paris", "fr-FR,fr;q=0.9,en;q=0.8"),
    "GB": GeoProfile("en-GB", "Europe/London", "en-GB,en;q=0.9"),
    "GE": GeoProfile("ka-GE", "Asia/Tbilisi", "ka-GE,ka;q=0.9,ru;q=0.8,en;q=0.7"),
    "GI": GeoProfile("en-GI", "Europe/Gibraltar", "en-GI,en;q=0.9,es;q=0.8"),
    "GR": GeoProfile("el-GR", "Europe/Athens", "el-GR,el;q=0.9,en;q=0.8"),
    "HR": GeoProfile("hr-HR", "Europe/Zagreb", "hr-HR,hr;q=0.9,en;q=0.8"),
    "HU": GeoProfile("hu-HU", "Europe/Budapest", "hu-HU,hu;q=0.9,en;q=0.8"),
    "IE": GeoProfile("en-IE", "Europe/Dublin", "en-IE,en;q=0.9"),
    "IS": GeoProfile("is-IS", "Atlantic/Reykjavik", "is-IS,is;q=0.9,en;q=0.8"),
    "IT": GeoProfile("it-IT", "Europe/Rome", "it-IT,it;q=0.9,en;q=0.8"),
    "LI": GeoProfile("de-LI", "Europe/Vaduz", "de-LI,de;q=0.9,en;q=0.8"),
    "LT": GeoProfile("lt-LT", "Europe/Vilnius", "lt-LT,lt;q=0.9,en;q=0.8"),
    "LU": GeoProfile("lb-LU", "Europe/Luxembourg", "lb-LU,lb;q=0.9,fr;q=0.8,de;q=0.7,en;q=0.6"),
    "LV": GeoProfile("lv-LV", "Europe/Riga", "lv-LV,lv;q=0.9,ru;q=0.8,en;q=0.7"),
    "MC": GeoProfile("fr-MC", "Europe/Monaco", "fr-MC,fr;q=0.9,en;q=0.8"),
    "MD": GeoProfile("ro-MD", "Europe/Chisinau", "ro-MD,ro;q=0.9,ru;q=0.8,en;q=0.7"),
    "ME": GeoProfile("sr-ME", "Europe/Podgorica", "sr-ME,sr;q=0.9,en;q=0.8"),
    "MK": GeoProfile("mk-MK", "Europe/Skopje", "mk-MK,mk;q=0.9,en;q=0.8"),
    "MT": GeoProfile("mt-MT", "Europe/Malta", "mt-MT,mt;q=0.9,en;q=0.8"),
    "NL": GeoProfile("nl-NL", "Europe/Amsterdam", "nl-NL,nl;q=0.9,en;q=0.8"),
    "NO": GeoProfile("nb-NO", "Europe/Oslo", "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.7"),
    "PL": GeoProfile("pl-PL", "Europe/Warsaw", "pl-PL,pl;q=0.9,en;q=0.8"),
    "PT": GeoProfile("pt-PT", "Europe/Lisbon", "pt-PT,pt;q=0.9,en;q=0.8"),
    "RO": GeoProfile("ro-RO", "Europe/Bucharest", "ro-RO,ro;q=0.9,en;q=0.8"),
    "RS": GeoProfile("sr-RS", "Europe/Belgrade", "sr-RS,sr;q=0.9,en;q=0.8"),
    "SE": GeoProfile("sv-SE", "Europe/Stockholm", "sv-SE,sv;q=0.9,en;q=0.8"),
    "SI": GeoProfile("sl-SI", "Europe/Ljubljana", "sl-SI,sl;q=0.9,en;q=0.8"),
    "SK": GeoProfile("sk-SK", "Europe/Bratislava", "sk-SK,sk;q=0.9,en;q=0.8"),
    "SM": GeoProfile("it-SM", "Europe/San_Marino", "it-SM,it;q=0.9,en;q=0.8"),
    "TR": GeoProfile("tr-TR", "Europe/Istanbul", "tr-TR,tr;q=0.9,en;q=0.8"),
    "UA": GeoProfile("uk-UA", "Europe/Kyiv", "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7"),
    "VA": GeoProfile("it-VA", "Europe/Vatican", "it-VA,it;q=0.9,en;q=0.8"),

    # North America
    "BS": GeoProfile("en-BS", "America/Nassau", "en-BS,en;q=0.9"),
    "BZ": GeoProfile("en-BZ", "America/Belize", "en-BZ,en;q=0.9,es;q=0.8"),
    "CA": GeoProfile("en-CA", "America/Toronto", "en-CA,en;q=0.9,fr-CA;q=0.8,fr;q=0.7"),
    "CR": GeoProfile("es-CR", "America/Costa_Rica", "es-CR,es;q=0.9,en;q=0.8"),
    "CU": GeoProfile("es-CU", "America/Havana", "es-CU,es;q=0.9,en;q=0.8"),
    "DO": GeoProfile("es-DO", "America/Santo_Domingo", "es-DO,es;q=0.9,en;q=0.8"),
    "GT": GeoProfile("es-GT", "America/Guatemala", "es-GT,es;q=0.9,en;q=0.8"),
    "HN": GeoProfile("es-HN", "America/Tegucigalpa", "es-HN,es;q=0.9,en;q=0.8"),
    "HT": GeoProfile("fr-HT", "America/Port-au-Prince", "fr-HT,fr;q=0.9,en;q=0.8"),
    "JM": GeoProfile("en-JM", "America/Jamaica", "en-JM,en;q=0.9"),
    "MX": GeoProfile("es-MX", "America/Mexico_City", "es-MX,es;q=0.9,en;q=0.8"),
    "NI": GeoProfile("es-NI", "America/Managua", "es-NI,es;q=0.9,en;q=0.8"),
    "PA": GeoProfile("es-PA", "America/Panama", "es-PA,es;q=0.9,en;q=0.8"),
    "PR": GeoProfile("es-PR", "America/Puerto_Rico", "es-PR,es;q=0.9,en;q=0.8"),
    "SV": GeoProfile("es-SV", "America/El_Salvador", "es-SV,es;q=0.9,en;q=0.8"),
    "TT": GeoProfile("en-TT", "America/Port_of_Spain", "en-TT,en;q=0.9"),
    "US": GeoProfile("en-US", "America/New_York", "en-US,en;q=0.9"),

    # South America
    "AR": GeoProfile("es-AR", "America/Argentina/Buenos_Aires", "es-AR,es;q=0.9,en;q=0.8"),
    "BO": GeoProfile("es-BO", "America/La_Paz", "es-BO,es;q=0.9,en;q=0.8"),
    "BR": GeoProfile("pt-BR", "America/Sao_Paulo", "pt-BR,pt;q=0.9,en;q=0.8"),
    "CL": GeoProfile("es-CL", "America/Santiago", "es-CL,es;q=0.9,en;q=0.8"),
    "CO": GeoProfile("es-CO", "America/Bogota", "es-CO,es;q=0.9,en;q=0.8"),
    "EC": GeoProfile("es-EC", "America/Guayaquil", "es-EC,es;q=0.9,en;q=0.8"),
    "GY": GeoProfile("en-GY", "America/Guyana", "en-GY,en;q=0.9"),
    "PE": GeoProfile("es-PE", "America/Lima", "es-PE,es;q=0.9,en;q=0.8"),
    "PY": GeoProfile("es-PY", "America/Asuncion", "es-PY,es;q=0.9,en;q=0.8"),
    "SR": GeoProfile("nl-SR", "America/Paramaribo", "nl-SR,nl;q=0.9,en;q=0.8"),
    "UY": GeoProfile("es-UY", "America/Montevideo", "es-UY,es;q=0.9,en;q=0.8"),
    "VE": GeoProfile("es-VE", "America/Caracas", "es-VE,es;q=0.9,en;q=0.8"),

    # Asia
    "AF": GeoProfile("fa-AF", "Asia/Kabul", "fa-AF,fa;q=0.9,ps;q=0.8,en;q=0.7"),
    "BD": GeoProfile("bn-BD", "Asia/Dhaka", "bn-BD,bn;q=0.9,en;q=0.8"),
    "BH": GeoProfile("ar-BH", "Asia/Bahrain", "ar-BH,ar;q=0.9,en;q=0.8"),
    "BN": GeoProfile("ms-BN", "Asia/Brunei", "ms-BN,ms;q=0.9,en;q=0.8"),
    "BT": GeoProfile("dz-BT", "Asia/Thimphu", "dz-BT,dz;q=0.9,en;q=0.8"),
    "CN": GeoProfile("zh-CN", "Asia/Shanghai", "zh-CN,zh;q=0.9,en;q=0.8"),
    "HK": GeoProfile("zh-HK", "Asia/Hong_Kong", "zh-HK,zh;q=0.9,en;q=0.8"),
    "ID": GeoProfile("id-ID", "Asia/Jakarta", "id-ID,id;q=0.9,en;q=0.8"),
    "IN": GeoProfile("en-IN", "Asia/Kolkata", "en-IN,en;q=0.9,hi;q=0.8"),
    "IQ": GeoProfile("ar-IQ", "Asia/Baghdad", "ar-IQ,ar;q=0.9,en;q=0.8"),
    "IR": GeoProfile("fa-IR", "Asia/Tehran", "fa-IR,fa;q=0.9,en;q=0.8"),
    "JO": GeoProfile("ar-JO", "Asia/Amman", "ar-JO,ar;q=0.9,en;q=0.8"),
    "JP": GeoProfile("ja-JP", "Asia/Tokyo", "ja-JP,ja;q=0.9,en;q=0.8"),
    "KG": GeoProfile("ky-KG", "Asia/Bishkek", "ky-KG,ky;q=0.9,ru;q=0.8,en;q=0.7"),
    "KH": GeoProfile("km-KH", "Asia/Phnom_Penh", "km-KH,km;q=0.9,en;q=0.8"),
    "KP": GeoProfile("ko-KP", "Asia/Pyongyang", "ko-KP,ko;q=0.9"),
    "KR": GeoProfile("ko-KR", "Asia/Seoul", "ko-KR,ko;q=0.9,en;q=0.8"),
    "KW": GeoProfile("ar-KW", "Asia/Kuwait", "ar-KW,ar;q=0.9,en;q=0.8"),
    "KZ": GeoProfile("kk-KZ", "Asia/Almaty", "kk-KZ,kk;q=0.9,ru;q=0.8,en;q=0.7"),
    "LA": GeoProfile("lo-LA", "Asia/Vientiane", "lo-LA,lo;q=0.9,en;q=0.8"),
    "LB": GeoProfile("ar-LB", "Asia/Beirut", "ar-LB,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "LK": GeoProfile("si-LK", "Asia/Colombo", "si-LK,si;q=0.9,ta;q=0.8,en;q=0.7"),
    "MM": GeoProfile("my-MM", "Asia/Yangon", "my-MM,my;q=0.9,en;q=0.8"),
    "MN": GeoProfile("mn-MN", "Asia/Ulaanbaatar", "mn-MN,mn;q=0.9,en;q=0.8"),
    "MO": GeoProfile("zh-MO", "Asia/Macau", "zh-MO,zh;q=0.9,pt;q=0.8,en;q=0.7"),
    "MV": GeoProfile("dv-MV", "Indian/Maldives", "dv-MV,dv;q=0.9,en;q=0.8"),
    "MY": GeoProfile("en-MY", "Asia/Kuala_Lumpur", "en-MY,en;q=0.9,ms;q=0.8"),
    "NP": GeoProfile("ne-NP", "Asia/Kathmandu", "ne-NP,ne;q=0.9,en;q=0.8"),
    "OM": GeoProfile("ar-OM", "Asia/Muscat", "ar-OM,ar;q=0.9,en;q=0.8"),
    "PH": GeoProfile("en-PH", "Asia/Manila", "en-PH,en;q=0.9,fil;q=0.8"),
    "PK": GeoProfile("ur-PK", "Asia/Karachi", "ur-PK,ur;q=0.9,en;q=0.8"),
    "PS": GeoProfile("ar-PS", "Asia/Gaza", "ar-PS,ar;q=0.9,en;q=0.8"),
    "QA": GeoProfile("ar-QA", "Asia/Qatar", "ar-QA,ar;q=0.9,en;q=0.8"),
    "SG": GeoProfile("en-SG", "Asia/Singapore", "en-SG,en;q=0.9,zh;q=0.8"),
    "SY": GeoProfile("ar-SY", "Asia/Damascus", "ar-SY,ar;q=0.9,en;q=0.8"),
    "TH": GeoProfile("th-TH", "Asia/Bangkok", "th-TH,th;q=0.9,en;q=0.8"),
    "TJ": GeoProfile("tg-TJ", "Asia/Dushanbe", "tg-TJ,tg;q=0.9,ru;q=0.8,en;q=0.7"),
    "TM": GeoProfile("tk-TM", "Asia/Ashgabat", "tk-TM,tk;q=0.9,ru;q=0.8,en;q=0.7"),
    "TW": GeoProfile("zh-TW", "Asia/Taipei", "zh-TW,zh;q=0.9,en;q=0.8"),
    "UZ": GeoProfile("uz-UZ", "Asia/Tashkent", "uz-UZ,uz;q=0.9,ru;q=0.8,en;q=0.7"),
    "VN": GeoProfile("vi-VN", "Asia/Ho_Chi_Minh", "vi-VN,vi;q=0.9,en;q=0.8"),
    "YE": GeoProfile("ar-YE", "Asia/Aden", "ar-YE,ar;q=0.9,en;q=0.8"),

    # Middle East
    "AE": GeoProfile("ar-AE", "Asia/Dubai", "ar-AE,ar;q=0.9,en;q=0.8"),
    "IL": GeoProfile("he-IL", "Asia/Jerusalem", "he-IL,he;q=0.9,en;q=0.8"),
    "SA": GeoProfile("ar-SA", "Asia/Riyadh", "ar-SA,ar;q=0.9,en;q=0.8"),

    # Russia (multi-timezone; city_overrides covers the rest)
    "RU": GeoProfile("ru-RU", "Europe/Moscow", "ru-RU,ru;q=0.9,en;q=0.8"),

    # Africa
    "AO": GeoProfile("pt-AO", "Africa/Luanda", "pt-AO,pt;q=0.9,en;q=0.8"),
    "BF": GeoProfile("fr-BF", "Africa/Ouagadougou", "fr-BF,fr;q=0.9,en;q=0.8"),
    "BI": GeoProfile("fr-BI", "Africa/Bujumbura", "fr-BI,fr;q=0.9,en;q=0.8"),
    "BJ": GeoProfile("fr-BJ", "Africa/Porto-Novo", "fr-BJ,fr;q=0.9,en;q=0.8"),
    "BW": GeoProfile("en-BW", "Africa/Gaborone", "en-BW,en;q=0.9"),
    "CD": GeoProfile("fr-CD", "Africa/Kinshasa", "fr-CD,fr;q=0.9,en;q=0.8"),
    "CF": GeoProfile("fr-CF", "Africa/Bangui", "fr-CF,fr;q=0.9,en;q=0.8"),
    "CG": GeoProfile("fr-CG", "Africa/Brazzaville", "fr-CG,fr;q=0.9,en;q=0.8"),
    "CI": GeoProfile("fr-CI", "Africa/Abidjan", "fr-CI,fr;q=0.9,en;q=0.8"),
    "CM": GeoProfile("fr-CM", "Africa/Douala", "fr-CM,fr;q=0.9,en;q=0.8"),
    "DJ": GeoProfile("fr-DJ", "Africa/Djibouti", "fr-DJ,fr;q=0.9,ar;q=0.8,en;q=0.7"),
    "DZ": GeoProfile("ar-DZ", "Africa/Algiers", "ar-DZ,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "EG": GeoProfile("ar-EG", "Africa/Cairo", "ar-EG,ar;q=0.9,en;q=0.8"),
    "ET": GeoProfile("am-ET", "Africa/Addis_Ababa", "am-ET,am;q=0.9,en;q=0.8"),
    "GA": GeoProfile("fr-GA", "Africa/Libreville", "fr-GA,fr;q=0.9,en;q=0.8"),
    "GH": GeoProfile("en-GH", "Africa/Accra", "en-GH,en;q=0.9"),
    "GN": GeoProfile("fr-GN", "Africa/Conakry", "fr-GN,fr;q=0.9,en;q=0.8"),
    "KE": GeoProfile("sw-KE", "Africa/Nairobi", "sw-KE,sw;q=0.9,en;q=0.8"),
    "LY": GeoProfile("ar-LY", "Africa/Tripoli", "ar-LY,ar;q=0.9,en;q=0.8"),
    "MA": GeoProfile("ar-MA", "Africa/Casablanca", "ar-MA,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "MG": GeoProfile("fr-MG", "Indian/Antananarivo", "fr-MG,fr;q=0.9,en;q=0.8"),
    "ML": GeoProfile("fr-ML", "Africa/Bamako", "fr-ML,fr;q=0.9,en;q=0.8"),
    "MU": GeoProfile("en-MU", "Indian/Mauritius", "en-MU,en;q=0.9,fr;q=0.8"),
    "MW": GeoProfile("en-MW", "Africa/Blantyre", "en-MW,en;q=0.9"),
    "MZ": GeoProfile("pt-MZ", "Africa/Maputo", "pt-MZ,pt;q=0.9,en;q=0.8"),
    "NA": GeoProfile("en-NA", "Africa/Windhoek", "en-NA,en;q=0.9"),
    "NE": GeoProfile("fr-NE", "Africa/Niamey", "fr-NE,fr;q=0.9,en;q=0.8"),
    "NG": GeoProfile("en-NG", "Africa/Lagos", "en-NG,en;q=0.9"),
    "RW": GeoProfile("rw-RW", "Africa/Kigali", "rw-RW,rw;q=0.9,fr;q=0.8,en;q=0.7"),
    "SD": GeoProfile("ar-SD", "Africa/Khartoum", "ar-SD,ar;q=0.9,en;q=0.8"),
    "SN": GeoProfile("fr-SN", "Africa/Dakar", "fr-SN,fr;q=0.9,en;q=0.8"),
    "SO": GeoProfile("so-SO", "Africa/Mogadishu", "so-SO,so;q=0.9,ar;q=0.8,en;q=0.7"),
    "SS": GeoProfile("en-SS", "Africa/Juba", "en-SS,en;q=0.9"),
    "TG": GeoProfile("fr-TG", "Africa/Lome", "fr-TG,fr;q=0.9,en;q=0.8"),
    "TN": GeoProfile("ar-TN", "Africa/Tunis", "ar-TN,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "TZ": GeoProfile("sw-TZ", "Africa/Dar_es_Salaam", "sw-TZ,sw;q=0.9,en;q=0.8"),
    "UG": GeoProfile("en-UG", "Africa/Kampala", "en-UG,en;q=0.9,sw;q=0.8"),
    "ZA": GeoProfile("en-ZA", "Africa/Johannesburg", "en-ZA,en;q=0.9"),
    "ZM": GeoProfile("en-ZM", "Africa/Lusaka", "en-ZM,en;q=0.9"),
    "ZW": GeoProfile("en-ZW", "Africa/Harare", "en-ZW,en;q=0.9"),

    # Oceania
    "AU": GeoProfile("en-AU", "Australia/Sydney", "en-AU,en;q=0.9"),
    "FJ": GeoProfile("en-FJ", "Pacific/Fiji", "en-FJ,en;q=0.9"),
    "NZ": GeoProfile("en-NZ", "Pacific/Auckland", "en-NZ,en;q=0.9"),
    "PG": GeoProfile("en-PG", "Pacific/Port_Moresby", "en-PG,en;q=0.9"),
    "SB": GeoProfile("en-SB", "Pacific/Guadalcanal", "en-SB,en;q=0.9"),
    "VU": GeoProfile("bi-VU", "Pacific/Efate", "bi-VU,bi;q=0.9,en;q=0.8,fr;q=0.7"),
    "WS": GeoProfile("sm-WS", "Pacific/Apia", "sm-WS,sm;q=0.9,en;q=0.8"),

    # Europe — territories / small states
    "AX": GeoProfile("fi-AX", "Europe/Mariehamn", "sv-AX,sv;q=0.9,fi;q=0.8,en;q=0.7"),
    "GG": GeoProfile("en-GG", "Europe/Guernsey", "en-GG,en;q=0.9"),
    "IM": GeoProfile("en-IM", "Europe/Isle_of_Man", "en-IM,en;q=0.9"),
    "JE": GeoProfile("en-JE", "Europe/Jersey", "en-JE,en;q=0.9"),
    "SJ": GeoProfile("nb-SJ", "Arctic/Longyearbyen", "nb-SJ,nb;q=0.9,en;q=0.8"),
    "XK": GeoProfile("sq-XK", "Europe/Belgrade", "sq-XK,sq;q=0.9,sr;q=0.8,en;q=0.7"),

    # Americas — Caribbean / territories
    "AG": GeoProfile("en-AG", "America/Antigua", "en-AG,en;q=0.9"),
    "AI": GeoProfile("en-AI", "America/Anguilla", "en-AI,en;q=0.9"),
    "AW": GeoProfile("nl-AW", "America/Aruba", "nl-AW,nl;q=0.9,en;q=0.8,es;q=0.7"),
    "BB": GeoProfile("en-BB", "America/Barbados", "en-BB,en;q=0.9"),
    "BL": GeoProfile("fr-BL", "America/St_Barthelemy", "fr-BL,fr;q=0.9,en;q=0.8"),
    "BM": GeoProfile("en-BM", "Atlantic/Bermuda", "en-BM,en;q=0.9"),
    "BQ": GeoProfile("nl-BQ", "America/Kralendijk", "nl-BQ,nl;q=0.9,en;q=0.8"),
    "CW": GeoProfile("nl-CW", "America/Curacao", "nl-CW,nl;q=0.9,en;q=0.8,es;q=0.7"),
    "DM": GeoProfile("en-DM", "America/Dominica", "en-DM,en;q=0.9"),
    "FK": GeoProfile("en-FK", "Atlantic/Stanley", "en-FK,en;q=0.9"),
    "GD": GeoProfile("en-GD", "America/Grenada", "en-GD,en;q=0.9"),
    "GF": GeoProfile("fr-GF", "America/Cayenne", "fr-GF,fr;q=0.9,en;q=0.8"),
    "GL": GeoProfile("kl-GL", "America/Nuuk", "kl-GL,kl;q=0.9,da;q=0.8,en;q=0.7"),
    "GP": GeoProfile("fr-GP", "America/Guadeloupe", "fr-GP,fr;q=0.9,en;q=0.8"),
    "GS": GeoProfile("en-GS", "Atlantic/South_Georgia", "en-GS,en;q=0.9"),
    "GY": GeoProfile("en-GY", "America/Guyana", "en-GY,en;q=0.9"),
    "KN": GeoProfile("en-KN", "America/St_Kitts", "en-KN,en;q=0.9"),
    "KY": GeoProfile("en-KY", "America/Cayman", "en-KY,en;q=0.9"),
    "LC": GeoProfile("en-LC", "America/St_Lucia", "en-LC,en;q=0.9"),
    "MF": GeoProfile("fr-MF", "America/Marigot", "fr-MF,fr;q=0.9,en;q=0.8"),
    "MQ": GeoProfile("fr-MQ", "America/Martinique", "fr-MQ,fr;q=0.9,en;q=0.8"),
    "MS": GeoProfile("en-MS", "America/Montserrat", "en-MS,en;q=0.9"),
    "PM": GeoProfile("fr-PM", "America/Miquelon", "fr-PM,fr;q=0.9,en;q=0.8"),
    "SR": GeoProfile("nl-SR", "America/Paramaribo", "nl-SR,nl;q=0.9,en;q=0.8"),
    "SX": GeoProfile("nl-SX", "America/Lower_Princes", "nl-SX,nl;q=0.9,en;q=0.8"),
    "TC": GeoProfile("en-TC", "America/Grand_Turk", "en-TC,en;q=0.9"),
    "VC": GeoProfile("en-VC", "America/St_Vincent", "en-VC,en;q=0.9"),
    "VG": GeoProfile("en-VG", "America/Tortola", "en-VG,en;q=0.9"),
    "VI": GeoProfile("en-VI", "America/St_Thomas", "en-VI,en;q=0.9"),

    # Asia — small / special
    "TL": GeoProfile("pt-TL", "Asia/Dili", "pt-TL,pt;q=0.9,en;q=0.8"),

    # Africa — additional
    "CV": GeoProfile("pt-CV", "Atlantic/Cape_Verde", "pt-CV,pt;q=0.9,en;q=0.8"),
    "EH": GeoProfile("ar-EH", "Africa/El_Aaiun", "ar-EH,ar;q=0.9,es;q=0.8,fr;q=0.7"),
    "ER": GeoProfile("ti-ER", "Africa/Asmara", "ti-ER,ti;q=0.9,en;q=0.8,ar;q=0.7"),
    "GM": GeoProfile("en-GM", "Africa/Banjul", "en-GM,en;q=0.9"),
    "GQ": GeoProfile("es-GQ", "Africa/Malabo", "es-GQ,es;q=0.9,fr;q=0.8,pt;q=0.7"),
    "GW": GeoProfile("pt-GW", "Africa/Bissau", "pt-GW,pt;q=0.9,en;q=0.8"),
    "IO": GeoProfile("en-IO", "Indian/Chagos", "en-IO,en;q=0.9"),
    "KM": GeoProfile("ar-KM", "Indian/Comoro", "ar-KM,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "LR": GeoProfile("en-LR", "Africa/Monrovia", "en-LR,en;q=0.9"),
    "LS": GeoProfile("en-LS", "Africa/Maseru", "en-LS,en;q=0.9"),
    "MR": GeoProfile("ar-MR", "Africa/Nouakchott", "ar-MR,ar;q=0.9,fr;q=0.8,en;q=0.7"),
    "RE": GeoProfile("fr-RE", "Indian/Reunion", "fr-RE,fr;q=0.9,en;q=0.8"),
    "SC": GeoProfile("en-SC", "Indian/Mahe", "en-SC,en;q=0.9,fr;q=0.8"),
    "SH": GeoProfile("en-SH", "Atlantic/St_Helena", "en-SH,en;q=0.9"),
    "SL": GeoProfile("en-SL", "Africa/Freetown", "en-SL,en;q=0.9"),
    "ST": GeoProfile("pt-ST", "Africa/Sao_Tome", "pt-ST,pt;q=0.9,en;q=0.8"),
    "SZ": GeoProfile("en-SZ", "Africa/Mbabane", "en-SZ,en;q=0.9"),
    "TD": GeoProfile("fr-TD", "Africa/Ndjamena", "fr-TD,fr;q=0.9,ar;q=0.8,en;q=0.7"),
    "YT": GeoProfile("fr-YT", "Indian/Mayotte", "fr-YT,fr;q=0.9,en;q=0.8"),

    # Oceania — territories / small
    "AS": GeoProfile("en-AS", "Pacific/Pago_Pago", "en-AS,en;q=0.9,sm;q=0.8"),
    "CC": GeoProfile("en-CC", "Indian/Cocos", "en-CC,en;q=0.9"),
    "CK": GeoProfile("en-CK", "Pacific/Rarotonga", "en-CK,en;q=0.9"),
    "CX": GeoProfile("en-CX", "Indian/Christmas", "en-CX,en;q=0.9"),
    "FM": GeoProfile("en-FM", "Pacific/Pohnpei", "en-FM,en;q=0.9"),
    "GU": GeoProfile("en-GU", "Pacific/Guam", "en-GU,en;q=0.9"),
    "KI": GeoProfile("en-KI", "Pacific/Tarawa", "en-KI,en;q=0.9"),
    "MH": GeoProfile("en-MH", "Pacific/Majuro", "en-MH,en;q=0.9"),
    "MP": GeoProfile("en-MP", "Pacific/Saipan", "en-MP,en;q=0.9"),
    "NC": GeoProfile("fr-NC", "Pacific/Noumea", "fr-NC,fr;q=0.9,en;q=0.8"),
    "NF": GeoProfile("en-NF", "Pacific/Norfolk", "en-NF,en;q=0.9"),
    "NR": GeoProfile("en-NR", "Pacific/Nauru", "en-NR,en;q=0.9"),
    "NU": GeoProfile("en-NU", "Pacific/Niue", "en-NU,en;q=0.9"),
    "PF": GeoProfile("fr-PF", "Pacific/Tahiti", "fr-PF,fr;q=0.9,en;q=0.8"),
    "PN": GeoProfile("en-PN", "Pacific/Pitcairn", "en-PN,en;q=0.9"),
    "PW": GeoProfile("en-PW", "Pacific/Palau", "en-PW,en;q=0.9"),
    "TK": GeoProfile("en-TK", "Pacific/Fakaofo", "en-TK,en;q=0.9"),
    "TO": GeoProfile("en-TO", "Pacific/Tongatapu", "en-TO,en;q=0.9"),
    "TV": GeoProfile("en-TV", "Pacific/Funafuti", "en-TV,en;q=0.9"),
    "UM": GeoProfile("en-UM", "Pacific/Midway", "en-UM,en;q=0.9"),
    "WF": GeoProfile("fr-WF", "Pacific/Wallis", "fr-WF,fr;q=0.9,en;q=0.8"),

    # Uninhabited / special — keep in English, UTC-ish where appropriate
    "AQ": GeoProfile("en-AQ", "Antarctica/McMurdo", "en-AQ,en;q=0.9"),
    "BV": GeoProfile("nb-BV", "UTC", "nb-BV,nb;q=0.9,en;q=0.8"),
    "HM": GeoProfile("en-HM", "UTC", "en-HM,en;q=0.9"),
    "TF": GeoProfile("fr-TF", "Indian/Kerguelen", "fr-TF,fr;q=0.9,en;q=0.8"),
}


# City overrides for multi-timezone countries. Keys are (country_code, city_lower).
_CITY_TZ: dict[tuple[str, str], str] = {
    # US — east to west
    ("US", "new york"): "America/New_York",
    ("US", "boston"): "America/New_York",
    ("US", "washington"): "America/New_York",
    ("US", "atlanta"): "America/New_York",
    ("US", "miami"): "America/New_York",
    ("US", "chicago"): "America/Chicago",
    ("US", "dallas"): "America/Chicago",
    ("US", "houston"): "America/Chicago",
    ("US", "denver"): "America/Denver",
    ("US", "phoenix"): "America/Phoenix",
    ("US", "salt lake city"): "America/Denver",
    ("US", "los angeles"): "America/Los_Angeles",
    ("US", "san francisco"): "America/Los_Angeles",
    ("US", "san diego"): "America/Los_Angeles",
    ("US", "seattle"): "America/Los_Angeles",
    ("US", "portland"): "America/Los_Angeles",
    ("US", "las vegas"): "America/Los_Angeles",
    ("US", "anchorage"): "America/Anchorage",
    ("US", "honolulu"): "Pacific/Honolulu",
    # Canada
    ("CA", "toronto"): "America/Toronto",
    ("CA", "ottawa"): "America/Toronto",
    ("CA", "montreal"): "America/Montreal",
    ("CA", "winnipeg"): "America/Winnipeg",
    ("CA", "calgary"): "America/Edmonton",
    ("CA", "edmonton"): "America/Edmonton",
    ("CA", "vancouver"): "America/Vancouver",
    # Russia
    ("RU", "moscow"): "Europe/Moscow",
    ("RU", "saint petersburg"): "Europe/Moscow",
    ("RU", "kazan"): "Europe/Moscow",
    ("RU", "yekaterinburg"): "Asia/Yekaterinburg",
    ("RU", "novosibirsk"): "Asia/Novosibirsk",
    ("RU", "krasnoyarsk"): "Asia/Krasnoyarsk",
    ("RU", "irkutsk"): "Asia/Irkutsk",
    ("RU", "vladivostok"): "Asia/Vladivostok",
    # Australia
    ("AU", "sydney"): "Australia/Sydney",
    ("AU", "melbourne"): "Australia/Melbourne",
    ("AU", "brisbane"): "Australia/Brisbane",
    ("AU", "perth"): "Australia/Perth",
    ("AU", "adelaide"): "Australia/Adelaide",
    # Brazil
    ("BR", "sao paulo"): "America/Sao_Paulo",
    ("BR", "rio de janeiro"): "America/Sao_Paulo",
    ("BR", "brasilia"): "America/Sao_Paulo",
    ("BR", "manaus"): "America/Manaus",
    ("BR", "fortaleza"): "America/Fortaleza",
}


def resolve_profile(country_code: str | None, city: str | None = None) -> GeoProfile | None:
    """
    Return a GeoProfile for the given country (+ optional city override),
    or None when the country is unknown.
    """
    if not country_code:
        return None
    cc = country_code.strip().upper()
    base = _COUNTRY_MAP.get(cc)
    if base is None:
        return None

    if city:
        tz_override = _CITY_TZ.get((cc, city.strip().lower()))
        if tz_override and tz_override != base.timezone_id:
            return GeoProfile(
                locale=base.locale,
                timezone_id=tz_override,
                accept_language=base.accept_language,
            )

    return base
